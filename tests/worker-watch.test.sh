#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WATCHER="${ROOT_DIR}/assets/shell/worker-watch.sh"

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

WORK_DIR="$(mktemp -d)"
cleanup() {
  if [[ -n "${sleep_pid:-}" ]]; then
    kill "${sleep_pid}" 2>/dev/null || true
  fi
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

LOG_FILE="${WORK_DIR}/worker.log"
DONE_FILE="${WORK_DIR}/worker.done"
TAIL_STATE="${WORK_DIR}/tail.sha"

printf 'STATUS|type=heartbeat|issue=42|role=impl|phase=testing|progress=running tests\n' > "${LOG_FILE}"

sleep 30 &
sleep_pid="$!"

alive_output="$("${WATCHER}" --pid "${sleep_pid}" --done-file "${DONE_FILE}" --log-file "${LOG_FILE}" --tail-state-file "${TAIL_STATE}" --tail-lines 5)"
assert_contains "${alive_output}" 'WORKER|' 'watcher emits parseable prefix'
assert_contains "${alive_output}" 'alive=true' 'watcher reports live process'
assert_contains "${alive_output}" 'done=false' 'watcher reports missing done file'
assert_contains "${alive_output}" 'log_present=true' 'watcher reports existing log'
assert_contains "${alive_output}" 'log_tail_changed=true' 'first log read is changed'

repeat_output="$("${WATCHER}" --pid "${sleep_pid}" --done-file "${DONE_FILE}" --log-file "${LOG_FILE}" --tail-state-file "${TAIL_STATE}" --tail-lines 5)"
assert_contains "${repeat_output}" 'log_tail_changed=false' 'unchanged tail is detected'

printf 'STATUS|type=heartbeat|issue=42|role=impl|phase=testing|progress=more output\n' >> "${LOG_FILE}"
changed_output="$("${WATCHER}" --pid "${sleep_pid}" --done-file "${DONE_FILE}" --log-file "${LOG_FILE}" --tail-state-file "${TAIL_STATE}" --tail-lines 5)"
assert_contains "${changed_output}" 'log_tail_changed=true' 'changed tail is detected'

missing_log_output="$("${WATCHER}" --pid "${sleep_pid}" --done-file "${DONE_FILE}" --log-file "${WORK_DIR}/missing.log" --tail-state-file "${TAIL_STATE}" --tail-lines 5)"
assert_contains "${missing_log_output}" 'log_present=false' 'watcher reports missing log'

printf 'DONE|issue=42|role=impl|status=success|source=agent\n' > "${DONE_FILE}"
done_output="$("${WATCHER}" --pid "${sleep_pid}" --done-file "${DONE_FILE}" --log-file "${LOG_FILE}" --tail-state-file "${TAIL_STATE}" --tail-lines 5)"
assert_contains "${done_output}" 'done=true' 'watcher reports done file'

kill "${sleep_pid}" 2>/dev/null || true
wait "${sleep_pid}" 2>/dev/null || true
sleep_pid=""

dead_output="$("${WATCHER}" --pid 999999 --done-file "${DONE_FILE}" --log-file "${LOG_FILE}" --tail-state-file "${TAIL_STATE}" --tail-lines 5)"
assert_contains "${dead_output}" 'alive=false' 'watcher reports dead process'
assert_contains "${dead_output}" 'done=true' 'watcher still reports done file for dead process'

echo "PASS: worker-watch helper"
