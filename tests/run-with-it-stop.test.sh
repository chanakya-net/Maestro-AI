#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STOP_RUNNER="${ROOT_DIR}/assets/run-with-it-stop.sh"
STOP_RUNNER_PS1="${ROOT_DIR}/assets/run-with-it-stop.ps1"
SKILL_FILE="${ROOT_DIR}/skills/run-with-it/SKILL.md"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"
  [[ "${haystack}" == *"${needle}"* ]] || fail "${message} (missing: ${needle})"
}

assert_file_contains() {
  local file="$1"
  local needle="$2"
  local message="$3"
  grep -Fq -- "$needle" "$file" || fail "${message} (missing: ${needle})"
}

WORK_DIR="$(mktemp -d)"
NOT_OURS_PID=""
cleanup() {
  # Best-effort teardown of any fixture process that outlived the test.
  for pid_file in "${WORK_DIR}"/pids/*; do
    [ -f "$pid_file" ] || continue
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    [ -n "$pid" ] && kill -KILL "$pid" 2>/dev/null || true
  done
  [ -n "${NOT_OURS_PID}" ] && kill -KILL "${NOT_OURS_PID}" 2>/dev/null || true
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

mkdir -p "${WORK_DIR}/pids" \
  "${WORK_DIR}/run/.run-with-it/main" \
  "${WORK_DIR}/run/.run-with-it/issues/7" \
  "${WORK_DIR}/run/.run-with-it/issues/8" \
  "${WORK_DIR}/run/.run-with-it/issues/9/workers/impl"

# Fake detached dispatcher: a session leader that spawns a "runner" child into
# its own process group, mirroring `nohup run-agent ... &` inside a detached
# dispatcher. With --orphan it exits after spawning, leaving the child behind.
FAKE_DISPATCHER="${WORK_DIR}/fake-run-agent-dispatcher.sh"
cat > "${FAKE_DISPATCHER}" <<'EOF'
#!/usr/bin/env bash
child_pid_file="$1"
mode="${2:-attached}"
sleep 600 &
echo "$!" > "$child_pid_file"
[ "$mode" = "orphan" ] && exit 0
wait
EOF
chmod +x "${FAKE_DISPATCHER}"

spawn_detached() {
  python3 - "$@" <<'PY'
import subprocess
import sys

process = subprocess.Popen(
    sys.argv[1:],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
    start_new_session=True,
)
print(process.pid)
PY
}

wait_for_file() {
  local path="$1" tries=50
  while [ "$tries" -gt 0 ]; do
    [ -s "$path" ] && return 0
    sleep 0.1
    tries=$((tries - 1))
  done
  return 1
}

MATCH_PATTERN="fake-run-agent-dispatcher|sleep 600"

# --- Live dispatcher tree: leader + in-group runner child ---
DISPATCHER_PID="$(spawn_detached "${FAKE_DISPATCHER}" "${WORK_DIR}/pids/runner-7")"
echo "${DISPATCHER_PID}" > "${WORK_DIR}/pids/dispatcher-7"
wait_for_file "${WORK_DIR}/pids/runner-7" || fail "fixture dispatcher never spawned its runner"
RUNNER_PID="$(cat "${WORK_DIR}/pids/runner-7")"
printf '{"dispatcher_pid": %s, "state": "running"}\n' "${DISPATCHER_PID}" \
  > "${WORK_DIR}/run/.run-with-it/issues/7/sub-coordinator.state.json"

# --- Orphaned runner: dispatcher exited, runner recorded in a nested worker state ---
ORPHAN_PARENT_PID="$(spawn_detached "${FAKE_DISPATCHER}" "${WORK_DIR}/pids/runner-9" orphan)"
wait_for_file "${WORK_DIR}/pids/runner-9" || fail "fixture orphan parent never spawned its runner"
ORPHAN_RUNNER_PID="$(cat "${WORK_DIR}/pids/runner-9")"
printf '{"dispatcher_pid": %s, "runner_pid": %s, "state": "running"}\n' "${ORPHAN_PARENT_PID}" "${ORPHAN_RUNNER_PID}" \
  > "${WORK_DIR}/run/.run-with-it/issues/9/workers/impl/cycle-1.state.json"

# --- Not ours: live PID recorded in state whose command line fails the identity check ---
sleep 654 &
NOT_OURS_PID=$!
printf '{"dispatcher_pid": %s, "state": "running"}\n' "${NOT_OURS_PID}" \
  > "${WORK_DIR}/run/.run-with-it/issues/8/sub-coordinator.state.json"

# --- Pool supervisor entry pointing at a dead PID (stale lease) ---
printf '{"pool_pid": 99999999, "started_at": 0, "state_file": "unused"}\n' \
  > "${WORK_DIR}/run/.run-with-it/main/pool.state.json"

stop_output="$(bash "${STOP_RUNNER}" \
  --run-root "${WORK_DIR}/run" \
  --term-wait-seconds 5 \
  --match-pattern "${MATCH_PATTERN}")"
stop_status=$?

assert_contains "${stop_output}" "STOP|result=clean" "stop reports clean shutdown"
[ "${stop_status}" -eq 0 ] || fail "clean stop must exit 0 (got ${stop_status})"
assert_contains "${stop_output}" "action=term-group" "stop signals whole process groups"
assert_contains "${stop_output}" "action=skip-not-ours" "stop refuses to signal PIDs that fail the identity check"
assert_contains "${stop_output}" "source=pool" "stop inspects the pool supervisor lease"
assert_contains "${stop_output}" "source=runner" "stop targets recorded runner PIDs"

kill -0 "${DISPATCHER_PID}" 2>/dev/null && fail "dispatcher leader survived stop"
kill -0 "${RUNNER_PID}" 2>/dev/null && fail "in-group runner survived stop (dispatcher-only kill regression)"
kill -0 "${ORPHAN_RUNNER_PID}" 2>/dev/null && fail "orphaned runner survived stop"
kill -0 "${NOT_OURS_PID}" 2>/dev/null || fail "not-ours process was killed despite failing the identity check"

# --- Idempotent re-run: everything already dead except the not-ours PID ---
second_output="$(bash "${STOP_RUNNER}" \
  --run-root "${WORK_DIR}/run" \
  --term-wait-seconds 2 \
  --match-pattern "${MATCH_PATTERN}")"
assert_contains "${second_output}" "STOP|result=clean" "re-run stop is idempotent"
assert_contains "${second_output}" "already_dead=" "re-run stop reports already-dead targets"

kill "${NOT_OURS_PID}" 2>/dev/null || true
wait "${NOT_OURS_PID}" 2>/dev/null || true
NOT_OURS_PID=""

# --- Static contract: discard flow uses the stop helpers and refuses on survivors ---
assert_file_contains "${SKILL_FILE}" "run-with-it-stop.sh" "discard flow invokes the platform stop helper"
assert_file_contains "${SKILL_FILE}" "Refuse discard" "discard refuses when termination cannot be established"
assert_file_contains "${STOP_RUNNER_PS1}" "ParentProcessId" "PowerShell stop helper expands full process trees"
assert_file_contains "${STOP_RUNNER_PS1}" "STOP|result=survivors" "PowerShell stop helper reports survivors"
assert_file_contains "${STOP_RUNNER}" "runner_pid" "stop helper targets recorded runner PIDs"

echo "PASS run-with-it-stop.test.sh"
