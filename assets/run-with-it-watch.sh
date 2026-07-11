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
    [--wait-seconds 240] [--poll-seconds 10]
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --events-log) EVENTS_LOG="${2:-}"; shift 2 ;;
    --pool-state-file) POOL_STATE_FILE="${2:-}"; shift 2 ;;
    --cursor-file) CURSOR_FILE="${2:-}"; shift 2 ;;
    --wait-seconds) WAIT_SECONDS="${2:-}"; shift 2 ;;
    --poll-seconds) POLL_SECONDS="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[ -n "$EVENTS_LOG" ] || fail "--events-log is required"
[ -n "$POOL_STATE_FILE" ] || fail "--pool-state-file is required"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "python helper runtime not found: $PYTHON_BIN"

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
  if [ -z "$pid" ] || [ "$pid" = "0" ] || ! kill -0 "$pid" 2>/dev/null; then
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
