#!/usr/bin/env bash

# Bounded foreground watch over the run-with-it status bus. The Main
# Coordinator calls this repeatedly instead of blocking on the pool runner:
# each call prints status lines appended to the events log since the previous
# call (cursor persisted on disk), then exits well before any tool-call
# timeout. The final line is always a WATCH|result=... marker:
#   WATCH|result=pool-empty  — pool finished; Step D is complete (exit 0)
#   WATCH|result=running     — pool alive, watch window elapsed; call again (exit 0)
#   WATCH|result=pool-dead   — pool supervisor gone without pool-empty; relaunch
#                              the pool runner, it re-attaches (exit 3)

set -euo pipefail

EVENTS_LOG=""
POOL_STATE_FILE=""
CURSOR_FILE=""
WAIT_SECONDS="${POOL_WATCH_SECONDS:-240}"
POLL_SECONDS="${STATUS_POLL_SECONDS:-10}"
MATCH_PATTERN="${RUN_WITH_IT_POOL_PATTERN:-run-with-it-pool}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

fail() {
  echo "run-with-it-watch.sh: $1" >&2
  exit 2
}

usage() {
  cat <<'EOF'
Usage:
  run-with-it-watch.sh --events-log .run-with-it/status/events.log \
    --pool-state-file .run-with-it/main/pool.state.json \
    [--cursor-file .run-with-it/status/watch-cursor] \
    [--wait-seconds 240] [--poll-seconds 10] \
    [--match-pattern run-with-it-pool]
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --events-log) EVENTS_LOG="${2:-}"; shift 2 ;;
    --pool-state-file) POOL_STATE_FILE="${2:-}"; shift 2 ;;
    --cursor-file) CURSOR_FILE="${2:-}"; shift 2 ;;
    --wait-seconds) WAIT_SECONDS="${2:-}"; shift 2 ;;
    --poll-seconds) POLL_SECONDS="${2:-}"; shift 2 ;;
    --match-pattern) MATCH_PATTERN="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[ -n "$EVENTS_LOG" ] || fail "--events-log is required"
[ -n "$POOL_STATE_FILE" ] || fail "--pool-state-file is required"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "python helper runtime not found: $PYTHON_BIN"

# A poll interval of zero (e.g. an unset env var binding as 0) would never
# advance elapsed time and hang the bounded watch; fall back to the default.
case "$POLL_SECONDS" in ''|*[!0-9]*) POLL_SECONDS=10 ;; esac
[ "$POLL_SECONDS" -ge 1 ] || POLL_SECONDS=10
case "$WAIT_SECONDS" in ''|*[!0-9]*) WAIT_SECONDS=240 ;; esac

if [ -z "$CURSOR_FILE" ]; then
  CURSOR_FILE="$(dirname "$EVENTS_LOG")/watch-cursor"
fi
mkdir -p "$(dirname "$CURSOR_FILE")"

read_cursor() {
  if [ -f "$CURSOR_FILE" ]; then
    cat "$CURSOR_FILE"
  else
    echo 0
  fi
}

pool_pid() {
  [ -f "$POOL_STATE_FILE" ] || { echo 0; return; }
  "$PYTHON_BIN" - "$POOL_STATE_FILE" <<'PY' 2>/dev/null || echo 0
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    data = json.load(handle)
print(int(data.get("pool_pid") or 0))
PY
}

pool_pid_start() {
  [ -f "$POOL_STATE_FILE" ] || { echo ""; return; }
  "$PYTHON_BIN" - "$POOL_STATE_FILE" <<'PY' 2>/dev/null || echo ""
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    data = json.load(handle)
print(data.get("pool_pid_start") or "")
PY
}

# PID existence alone is not identity: verify the command line and, when the
# lease recorded one, the process start time, so a recycled PID belonging to an
# unrelated process reads as pool-dead instead of being watched forever.
pool_alive() {
  local pid="$1" recorded_start actual_start
  [ -n "$pid" ] && [ "$pid" != "0" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  ps -p "$pid" -o command= 2>/dev/null | grep -Eq "$MATCH_PATTERN" || return 1
  recorded_start="$(pool_pid_start)"
  if [ -n "$recorded_start" ]; then
    actual_start="$(ps -p "$pid" -o lstart= 2>/dev/null | sed 's/^ *//;s/ *$//')"
    [ "$actual_start" = "$recorded_start" ] || return 1
  fi
  return 0
}

# Print events-log lines added since the cursor, advance the cursor, and
# report via exit code whether pool-empty was observed (0 yes, 1 no).
drain_new_lines() {
  local cursor total saw_empty=1 line
  cursor="$(read_cursor)"
  [ -f "$EVENTS_LOG" ] || return 1
  total="$(wc -l < "$EVENTS_LOG" | tr -d '[:space:]')"
  if [ "$total" -gt "$cursor" ]; then
    while IFS= read -r line; do
      printf '%s\n' "$line"
      case "$line" in
        *"type=pool-empty"*) saw_empty=0 ;;
      esac
    done < <(tail -n +"$((cursor + 1))" "$EVENTS_LOG")
    printf '%s\n' "$total" > "$CURSOR_FILE"
  fi
  return "$saw_empty"
}

elapsed=0
while :; do
  if drain_new_lines; then
    echo "WATCH|result=pool-empty|events_log=${EVENTS_LOG}"
    exit 0
  fi
  pid="$(pool_pid)"
  if ! pool_alive "$pid"; then
    # Drain once more: the pool may have written pool-empty and exited
    # between the drain above and the liveness check.
    if drain_new_lines; then
      echo "WATCH|result=pool-empty|events_log=${EVENTS_LOG}"
      exit 0
    fi
    echo "WATCH|result=pool-dead|pid=${pid}|pool_state_file=${POOL_STATE_FILE}"
    exit 3
  fi
  if [ "$elapsed" -ge "$WAIT_SECONDS" ]; then
    echo "WATCH|result=running|pid=${pid}|elapsed=${elapsed}"
    exit 0
  fi
  sleep "$POLL_SECONDS"
  elapsed=$((elapsed + POLL_SECONDS))
done
