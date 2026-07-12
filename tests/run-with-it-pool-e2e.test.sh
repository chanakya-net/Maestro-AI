#!/usr/bin/env bash

# Behavioral end-to-end coverage for the detach / pool-state handoff /
# reattach contract:
#   1. detach → watch → pool-empty
#   2. kill supervisor while a dispatcher runs → relaunch → reattach exactly
#      once, never a duplicate dispatch
#   3. stale/recycled PID rejection during reattach
#   4. duplicate supervisor launch prevention

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POOL_RUNNER="${ROOT_DIR}/assets/run-with-it-pool.sh"
POOL_RUNNER_PS1="${ROOT_DIR}/assets/run-with-it-pool.ps1"
WATCH_RUNNER="${ROOT_DIR}/assets/run-with-it-watch.sh"
WATCH_RUNNER_PS1="${ROOT_DIR}/assets/run-with-it-watch.ps1"
ASSETS_DIR="${ROOT_DIR}/assets"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_contains() {
  local haystack="$1" needle="$2" message="$3"
  [[ "${haystack}" == *"${needle}"* ]] || fail "${message} (missing: ${needle})"
}

WORK_DIR="$(mktemp -d)"
LIVE_PIDS_FILE="${WORK_DIR}/live-pids"
touch "${LIVE_PIDS_FILE}"
cleanup() {
  local pid
  while IFS= read -r pid; do
    [ -n "$pid" ] && kill -KILL "$pid" 2>/dev/null || true
  done < "${LIVE_PIDS_FILE}"
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

track_pid() {
  echo "$1" >> "${LIVE_PIDS_FILE}"
}

# Fake asset root: real helpers and prompts, fake dispatcher. The fake
# dispatcher honours the --detach contract — it starts a detached long-lived
# runner whose command line matches the default dispatch identity pattern
# ("run-agent") and records that PID as dispatcher_pid in the state file.
FAKE_ASSETS="${WORK_DIR}/assets"
mkdir -p "${FAKE_ASSETS}"
for f in run-with-it-state.py run-with-it-github-update.py run-with-it-artifacts.py \
         sub-coordinator-prompt.md merge-recovery-prompt.md; do
  ln -s "${ASSETS_DIR}/${f}" "${FAKE_ASSETS}/${f}"
done

FAKE_RUNNER="${WORK_DIR}/fake-run-agent-e2e.sh"
cat > "${FAKE_RUNNER}" <<'EOF'
#!/usr/bin/env bash
sleep 600
EOF
chmod +x "${FAKE_RUNNER}"

cat > "${FAKE_ASSETS}/run-with-it-dispatch.sh" <<EOF
#!/usr/bin/env bash
state_file=""
while [ "\$#" -gt 0 ]; do
  case "\$1" in
    --state-file) state_file="\$2"; shift 2 ;;
    *) shift ;;
  esac
done
[ -n "\$state_file" ] || exit 2
mkdir -p "\$(dirname "\$state_file")"
pid="\$(python3 -c '
import subprocess, sys
p = subprocess.Popen([sys.argv[1]], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, start_new_session=True)
print(p.pid)
' "${FAKE_RUNNER}")"
printf '{"dispatcher_pid": %s, "state": "running"}\n' "\$pid" > "\$state_file"
echo "\$pid" >> "${LIVE_PIDS_FILE}"
exit 0
EOF
chmod +x "${FAKE_ASSETS}/run-with-it-dispatch.sh"

make_run_dir() {
  local dir="$1" registry="$2" topo="$3" active="$4"
  mkdir -p "${dir}/.run-with-it/main" "${dir}/.run-with-it/status" "${dir}/.run-with-it/contexts"
  cat > "${dir}/.run-with-it/main-state.json" <<JSON
{
  "schema_version": 4,
  "execution_plan": {"topo_order": ${topo}, "parallel_jobs": 1, "concurrency_policy": "strict"},
  "active_pool_issues": ${active},
  "issue_registry": ${registry}
}
JSON
}

launch_pool() {
  local dir="$1" poll="$2"
  "${POOL_RUNNER}" \
    --asset-root "${FAKE_ASSETS}" \
    --state-file "${dir}/.run-with-it/main-state.json" \
    --parallel-jobs 1 --agent codex --model gpt-5.6-sol \
    --status-file "${dir}/.run-with-it/status/current.txt" \
    --events-log "${dir}/.run-with-it/status/events.log" \
    --main-log "${dir}/.run-with-it/main/main.log" \
    --poll-seconds "${poll}" --timeout-seconds 60 \
    --pool-state-file "${dir}/.run-with-it/main/pool.state.json" \
    --detach
}

pool_pid_of() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["pool_pid"])' \
    "$1/.run-with-it/main/pool.state.json"
}

wait_for_event() {
  local events="$1" needle="$2" tries=100
  while [ "$tries" -gt 0 ]; do
    grep -Fq -- "$needle" "$events" 2>/dev/null && return 0
    sleep 0.1
    tries=$((tries - 1))
  done
  return 1
}

# ═══ Scenario 1: detach → watch → pool-empty ═══
RUN1="${WORK_DIR}/run1"
make_run_dir "${RUN1}" '{"1": {"status": "completed", "deps": []}}' '[1]' '[]'
launch_pool "${RUN1}" 1 >/dev/null
track_pid "$(pool_pid_of "${RUN1}")"
watch_output="$(bash "${WATCH_RUNNER}" \
  --events-log "${RUN1}/.run-with-it/status/events.log" \
  --pool-state-file "${RUN1}/.run-with-it/main/pool.state.json" \
  --wait-seconds 30 --poll-seconds 1)"
assert_contains "${watch_output}" "WATCH|result=pool-empty" "detached pool reaches pool-empty and the watch observes it"

# ═══ Scenario 2: kill supervisor mid-dispatch → relaunch → reattach exactly once ═══
RUN2="${WORK_DIR}/run2"
printf '# issue 1 context\n' > "${WORK_DIR}/ctx-1.md"
make_run_dir "${RUN2}" \
  "{\"1\": {\"status\": \"pending\", \"deps\": [], \"parallel_safe\": true, \"ownership_scope\": [\"src\"], \"context_file\": \"${WORK_DIR}/ctx-1.md\"}}" \
  '[1]' '[]'
launch_pool "${RUN2}" 30 >/dev/null
EVENTS2="${RUN2}/.run-with-it/status/events.log"
wait_for_event "${EVENTS2}" "STATUS|type=sub-coord-spawn|issue=1" || fail "pool never dispatched issue 1"
SUPERVISOR_PID="$(pool_pid_of "${RUN2}")"
kill -KILL "${SUPERVISOR_PID}" 2>/dev/null || true
sleep 0.5
kill -0 "${SUPERVISOR_PID}" 2>/dev/null && fail "fixture could not kill the supervisor"

DISPATCHER_PID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["issue_registry"]["1"]["pid"])' "${RUN2}/.run-with-it/main-state.json")"
kill -0 "${DISPATCHER_PID}" 2>/dev/null || fail "detached dispatcher should survive the supervisor kill"

launch_pool "${RUN2}" 30 >/dev/null
track_pid "$(pool_pid_of "${RUN2}")"
wait_for_event "${EVENTS2}" "STATUS|type=pool-reattached|count=1" || fail "relaunched pool never reattached"
grep -Fq -- "STATUS|type=sub-coord-reattach|issue=1|pid=${DISPATCHER_PID}|identity=live" "${EVENTS2}" \
  || fail "relaunched pool did not adopt the live dispatcher by identity"
spawn_count="$(grep -Fc -- "STATUS|type=sub-coord-spawn|issue=1" "${EVENTS2}")"
[ "${spawn_count}" -eq 1 ] || fail "issue 1 was dispatched ${spawn_count} times; reattach must not duplicate work"
kill -KILL "$(pool_pid_of "${RUN2}")" 2>/dev/null || true
kill -KILL "${DISPATCHER_PID}" 2>/dev/null || true

# ═══ Scenario 3: recycled-PID rejection during reattach ═══
RUN3="${WORK_DIR}/run3"
sleep 777 &
RECYCLED_PID=$!
disown "${RECYCLED_PID}" 2>/dev/null || true
track_pid "${RECYCLED_PID}"
make_run_dir "${RUN3}" \
  "{\"2\": {\"status\": \"in_progress\", \"deps\": [], \"pid\": ${RECYCLED_PID}, \"report_file\": \"${RUN3}/.run-with-it/issues/2/report.json\", \"context_file\": \"${WORK_DIR}/ctx-1.md\"}}" \
  '[2]' '[2]'
launch_pool "${RUN3}" 30 >/dev/null
EVENTS3="${RUN3}/.run-with-it/status/events.log"
wait_for_event "${EVENTS3}" "STATUS|type=sub-coord-reattach|issue=2|pid=0|identity=stale" \
  || fail "reattach adopted a recycled PID instead of marking it stale"
kill -0 "${RECYCLED_PID}" 2>/dev/null || fail "reattach must never signal the recycled PID's owner"

# ═══ Scenario 4: duplicate supervisor launch prevention ═══
RUN3_POOL_PID="$(pool_pid_of "${RUN3}")"
track_pid "${RUN3_POOL_PID}"
kill -0 "${RUN3_POOL_PID}" 2>/dev/null || fail "scenario 4 needs the run3 supervisor alive"
dup_status=0
dup_output="$(launch_pool "${RUN3}" 30 2>&1)" || dup_status=$?
[ "${dup_status}" -eq 2 ] || fail "duplicate supervisor launch must exit 2 (got ${dup_status})"
assert_contains "${dup_output}" "STATUS|type=pool-already-running|pid=${RUN3_POOL_PID}" "duplicate launch reports the live supervisor"
kill -KILL "${RUN3_POOL_PID}" 2>/dev/null || true

# ═══ PowerShell: detach → watch → pool-empty (parity where pwsh is available) ═══
if command -v pwsh >/dev/null 2>&1; then
  RUN4="${WORK_DIR}/run4"
  mkdir -p "${RUN4}/.run-with-it/main" "${RUN4}/.run-with-it/status"
  cat > "${RUN4}/.run-with-it/main-state.json" <<'JSON'
{
  "schema_version": 4,
  "execution_plan": {"topo_order": [1], "parallel_jobs": 1, "concurrency_policy": "strict"},
  "active_pool_issues": [],
  "issue_registry": {"1": {"status": "completed", "deps": []}}
}
JSON
  cat > "${FAKE_ASSETS}/run-with-it-dispatch.ps1" <<'EOF'
exit 2
EOF
  pwsh -NoProfile -File "${POOL_RUNNER_PS1}" \
    -AssetRoot "${FAKE_ASSETS}" \
    -StateFile "${RUN4}/.run-with-it/main-state.json" \
    -ParallelJobs 1 -Agent codex -Model gpt-5.6-sol \
    -StatusFile "${RUN4}/.run-with-it/status/current.txt" \
    -EventsLog "${RUN4}/.run-with-it/status/events.log" \
    -MainLog "${RUN4}/.run-with-it/main/main.log" \
    -PollSeconds 1 -TimeoutSeconds 60 \
    -PoolStateFile "${RUN4}/.run-with-it/main/pool.state.json" \
    -Detach >/dev/null
  track_pid "$(pool_pid_of "${RUN4}")"
  # On non-Windows pwsh the process command line is not exposed via CIM, so the
  # identity pattern accepts the pwsh host binary as well.
  ps_watch="$(pwsh -NoProfile -File "${WATCH_RUNNER_PS1}" \
    -EventsLog "${RUN4}/.run-with-it/status/events.log" \
    -PoolStateFile "${RUN4}/.run-with-it/main/pool.state.json" \
    -WaitSeconds 60 -PollSeconds 1 -MatchPattern "run-with-it-pool|pwsh|powershell")"
  assert_contains "${ps_watch}" "WATCH|result=pool-empty" "pwsh detached pool reaches pool-empty through the watch"
else
  echo "SKIP: pwsh not installed; PowerShell e2e checks skipped"
fi

echo "PASS run-with-it-pool-e2e.test.sh"
