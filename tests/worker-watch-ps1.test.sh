#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WATCHER="${ROOT_DIR}/assets/worker-watch.ps1"
PS_CMD="${PWSH:-}"
if [[ -z "$PS_CMD" ]]; then
  PS_CMD="$(command -v pwsh || command -v powershell.exe || command -v powershell || true)"
fi

if [[ -z "$PS_CMD" ]]; then
  echo "SKIP: PowerShell unavailable for worker-watch.ps1 contract"
  exit 0
fi

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"
  [[ "$haystack" == *"$needle"* ]] || fail "$message (missing: $needle)"
}

WORK_DIR="$(mktemp -d)"
cleanup() {
  if [[ -n "${sleep_pid:-}" ]]; then
    kill "${sleep_pid}" 2>/dev/null || true
  fi
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

LOG_FILE="${WORK_DIR}/worker.log"
DONE_FILE="${WORK_DIR}/worker.done"
TAIL_STATE="${WORK_DIR}/tail.sha"

printf 'STATUS|type=heartbeat|issue=42|role=impl|phase=testing|progress=running tests\n' > "$LOG_FILE"
sleep 30 &
sleep_pid="$!"

alive_output="$("$PS_CMD" -NoProfile -File "$WATCHER" -Pid "$sleep_pid" -DoneFile "$DONE_FILE" -LogFile "$LOG_FILE" -TailStateFile "$TAIL_STATE" -TailLines 5)"
assert_contains "$alive_output" "WORKER|" "watcher emits parseable prefix"
assert_contains "$alive_output" "alive=true" "watcher reports live process"
assert_contains "$alive_output" "done=false" "watcher reports missing done file"
assert_contains "$alive_output" "log_present=true" "watcher reports existing log"
assert_contains "$alive_output" "log_tail_changed=true" "first tail read is changed"

repeat_output="$("$PS_CMD" -NoProfile -File "$WATCHER" -Pid "$sleep_pid" -DoneFile "$DONE_FILE" -LogFile "$LOG_FILE" -TailStateFile "$TAIL_STATE" -TailLines 5)"
assert_contains "$repeat_output" "log_tail_changed=false" "unchanged tail is detected"

printf 'STATUS|type=heartbeat|issue=42|role=impl|phase=testing|progress=more output\n' >> "$LOG_FILE"
changed_output="$("$PS_CMD" -NoProfile -File "$WATCHER" -Pid "$sleep_pid" -DoneFile "$DONE_FILE" -LogFile "$LOG_FILE" -TailStateFile "$TAIL_STATE" -TailLines 5)"
assert_contains "$changed_output" "log_tail_changed=true" "changed tail is detected"

missing_log_output="$("$PS_CMD" -NoProfile -File "$WATCHER" -Pid "$sleep_pid" -DoneFile "$DONE_FILE" -LogFile "${WORK_DIR}/missing.log" -TailStateFile "$TAIL_STATE" -TailLines 5)"
assert_contains "$missing_log_output" "log_present=false" "missing log is reported"

printf 'DONE|issue=42|role=impl|status=success|source=agent\n' > "$DONE_FILE"
done_output="$("$PS_CMD" -NoProfile -File "$WATCHER" -Pid "$sleep_pid" -DoneFile "$DONE_FILE" -LogFile "$LOG_FILE" -TailStateFile "$TAIL_STATE" -TailLines 5)"
assert_contains "$done_output" "done=true" "done file is reported"

kill "$sleep_pid" 2>/dev/null || true
wait "$sleep_pid" 2>/dev/null || true
sleep_pid=""

dead_output="$("$PS_CMD" -NoProfile -File "$WATCHER" -Pid 999999 -DoneFile "$DONE_FILE" -LogFile "$LOG_FILE" -TailStateFile "$TAIL_STATE" -TailLines 5)"
assert_contains "$dead_output" "alive=false" "dead process is reported"
assert_contains "$dead_output" "done=true" "done file remains visible for dead process"

echo "PASS: worker-watch.ps1 helper"
