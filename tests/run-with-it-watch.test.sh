#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WATCH_RUNNER="${ROOT_DIR}/assets/run-with-it-watch.sh"
POOL_RUNNER="${ROOT_DIR}/assets/run-with-it-pool.sh"
POOL_RUNNER_PS1="${ROOT_DIR}/assets/run-with-it-pool.ps1"
WATCH_RUNNER_PS1="${ROOT_DIR}/assets/run-with-it-watch.ps1"

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

assert_not_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"
  [[ "${haystack}" != *"${needle}"* ]] || fail "${message} (found forbidden: ${needle})"
}

assert_file_contains() {
  local file="$1"
  local needle="$2"
  local message="$3"
  grep -Fq -- "$needle" "$file" || fail "${message} (missing: ${needle})"
}

WORK_DIR="$(mktemp -d)"
SLEEPER_PID=""
cleanup() {
  [ -z "${SLEEPER_PID}" ] || kill "${SLEEPER_PID}" 2>/dev/null || true
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

EVENTS_LOG="${WORK_DIR}/status/events.log"
POOL_STATE_FILE="${WORK_DIR}/main/pool.state.json"
CURSOR_FILE="${WORK_DIR}/status/watch-cursor"
mkdir -p "${WORK_DIR}/status" "${WORK_DIR}/main"

# Stand-in for a live pool supervisor.
sleep 300 &
SLEEPER_PID=$!
printf '{"pool_pid": %s, "started_at": 0, "state_file": "unused"}\n' "${SLEEPER_PID}" > "${POOL_STATE_FILE}"

printf 'STATUS|type=pool-ready|parallel_jobs=2|ready=2\n' >> "${EVENTS_LOG}"
printf 'STATUS|type=sub-coord-spawn|issue=1|pid=111\n' >> "${EVENTS_LOG}"

# --- A running pool drains new lines and reports result=running ---
first_output="$(bash "${WATCH_RUNNER}" \
  --events-log "${EVENTS_LOG}" \
  --pool-state-file "${POOL_STATE_FILE}" \
  --cursor-file "${CURSOR_FILE}" \
  --wait-seconds 0 --poll-seconds 1)"
assert_contains "${first_output}" "STATUS|type=pool-ready" "watch drains status lines since start"
assert_contains "${first_output}" "STATUS|type=sub-coord-spawn|issue=1" "watch drains every appended line"
assert_contains "${first_output}" "WATCH|result=running" "watch reports running while the pool is alive"

# --- The cursor suppresses already-printed lines on the next call ---
printf 'STATUS|type=run-board|board=#1 impl(cyc1)\n' >> "${EVENTS_LOG}"
second_output="$(bash "${WATCH_RUNNER}" \
  --events-log "${EVENTS_LOG}" \
  --pool-state-file "${POOL_STATE_FILE}" \
  --cursor-file "${CURSOR_FILE}" \
  --wait-seconds 0 --poll-seconds 1)"
assert_not_contains "${second_output}" "STATUS|type=pool-ready" "watch does not reprint drained lines"
assert_contains "${second_output}" "STATUS|type=run-board" "watch prints newly appended lines"

# --- pool-empty terminates the watch with the completion marker ---
printf 'STATUS|type=pool-empty|state_file=unused\n' >> "${EVENTS_LOG}"
third_output="$(bash "${WATCH_RUNNER}" \
  --events-log "${EVENTS_LOG}" \
  --pool-state-file "${POOL_STATE_FILE}" \
  --cursor-file "${CURSOR_FILE}" \
  --wait-seconds 30 --poll-seconds 1)"
assert_contains "${third_output}" "WATCH|result=pool-empty" "watch reports pool-empty and stops"

# --- A dead pool without pool-empty exits 3 with the pool-dead marker ---
kill "${SLEEPER_PID}" 2>/dev/null || true
wait "${SLEEPER_PID}" 2>/dev/null || true
SLEEPER_PID=""
printf 'STATUS|type=sub-coord-spawn|issue=2|pid=222\n' >> "${EVENTS_LOG}"
dead_status=0
dead_output="$(bash "${WATCH_RUNNER}" \
  --events-log "${EVENTS_LOG}" \
  --pool-state-file "${POOL_STATE_FILE}" \
  --cursor-file "${CURSOR_FILE}" \
  --wait-seconds 30 --poll-seconds 1)" || dead_status=$?
assert_contains "${dead_output}" "WATCH|result=pool-dead" "watch reports a dead pool supervisor"
[ "${dead_status}" -eq 3 ] || fail "pool-dead must exit 3 (got ${dead_status})"
assert_contains "${dead_output}" "STATUS|type=sub-coord-spawn|issue=2" "watch still drains lines before reporting pool-dead"

# --- PowerShell watcher: functional parity (regression: status lines in the
# success stream must not make `if (Drain-NewLines)` truthy and fake pool-empty) ---
if command -v pwsh >/dev/null 2>&1; then
  PS_EVENTS_LOG="${WORK_DIR}/status/ps-events.log"
  PS_POOL_STATE_FILE="${WORK_DIR}/main/ps-pool.state.json"
  PS_CURSOR_FILE="${WORK_DIR}/status/ps-watch-cursor"

  sleep 300 &
  SLEEPER_PID=$!
  printf '{"pool_pid": %s, "started_at": 0, "state_file": "unused"}\n' "${SLEEPER_PID}" > "${PS_POOL_STATE_FILE}"
  printf 'STATUS|type=pool-detached|pid=%s\n' "${SLEEPER_PID}" >> "${PS_EVENTS_LOG}"
  printf 'STATUS|type=run-board|board=#1 impl(cyc1)\n' >> "${PS_EVENTS_LOG}"

  ps_first="$(pwsh -NoProfile -File "${WATCH_RUNNER_PS1}" \
    -EventsLog "${PS_EVENTS_LOG}" \
    -PoolStateFile "${PS_POOL_STATE_FILE}" \
    -CursorFile "${PS_CURSOR_FILE}" \
    -WaitSeconds 0 -PollSeconds 1)"
  assert_contains "${ps_first}" "STATUS|type=run-board" "pwsh watch prints drained status lines"
  assert_contains "${ps_first}" "WATCH|result=running" "pwsh watch reports running while the pool is alive"
  assert_not_contains "${ps_first}" "WATCH|result=pool-empty" "pwsh watch must not fake pool-empty from ordinary status output"

  printf 'STATUS|type=pool-empty|state_file=unused\n' >> "${PS_EVENTS_LOG}"
  ps_second="$(pwsh -NoProfile -File "${WATCH_RUNNER_PS1}" \
    -EventsLog "${PS_EVENTS_LOG}" \
    -PoolStateFile "${PS_POOL_STATE_FILE}" \
    -CursorFile "${PS_CURSOR_FILE}" \
    -WaitSeconds 30 -PollSeconds 1)"
  assert_not_contains "${ps_second}" "STATUS|type=run-board" "pwsh watch does not reprint drained lines"
  assert_contains "${ps_second}" "WATCH|result=pool-empty" "pwsh watch reports pool-empty"

  kill "${SLEEPER_PID}" 2>/dev/null || true
  wait "${SLEEPER_PID}" 2>/dev/null || true
  SLEEPER_PID=""
  printf 'STATUS|type=sub-coord-spawn|issue=9|pid=999\n' >> "${PS_EVENTS_LOG}"
  ps_dead_status=0
  ps_dead="$(pwsh -NoProfile -File "${WATCH_RUNNER_PS1}" \
    -EventsLog "${PS_EVENTS_LOG}" \
    -PoolStateFile "${PS_POOL_STATE_FILE}" \
    -CursorFile "${PS_CURSOR_FILE}" \
    -WaitSeconds 30 -PollSeconds 1)" || ps_dead_status=$?
  assert_contains "${ps_dead}" "WATCH|result=pool-dead" "pwsh watch reports a dead pool supervisor"
  [ "${ps_dead_status}" -eq 3 ] || fail "pwsh pool-dead must exit 3 (got ${ps_dead_status})"
else
  echo "SKIP: pwsh not installed; PowerShell watcher functional checks skipped"
fi

# --- Static contract: pool runner supports detach, re-attach, and deferral visibility ---
assert_file_contains "${POOL_RUNNER}" "--detach" "pool runner supports detached supervisor mode"
assert_file_contains "${POOL_RUNNER}" "active-pool-entries" "pool runner re-attaches to in-flight issues on restart"
assert_file_contains "${POOL_RUNNER}" "sub-coord-reattach" "pool runner reports re-attached issues"
assert_file_contains "${POOL_RUNNER}" "pool-admission-deferred" "pool runner surfaces concurrency-gate deferrals"
assert_file_contains "${POOL_RUNNER}" "pool.state.json" "pool runner records its supervisor pid"
assert_file_contains "${POOL_RUNNER_PS1}" "Detach" "PowerShell pool runner supports detached supervisor mode"
assert_file_contains "${POOL_RUNNER_PS1}" "active-pool-entries" "PowerShell pool runner re-attaches to in-flight issues"
assert_file_contains "${POOL_RUNNER_PS1}" "pool-admission-deferred" "PowerShell pool runner surfaces deferrals"
assert_file_contains "${WATCH_RUNNER_PS1}" "WATCH|result=pool-empty" "PowerShell watch runner reports pool-empty"
assert_file_contains "${WATCH_RUNNER_PS1}" "WATCH|result=pool-dead" "PowerShell watch runner reports pool-dead"

echo "PASS run-with-it-watch.test.sh"
