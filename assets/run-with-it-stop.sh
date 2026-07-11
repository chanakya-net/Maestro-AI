#!/usr/bin/env bash

# Identity-checked shutdown of every detached run-with-it process for one run:
# the pool supervisor, every dispatcher, and every runner — including nested
# worker dispatchers and orphaned runners whose dispatcher already exited.
#
# Detached dispatchers are session/process-group leaders (start_new_session),
# and runners are spawned into the dispatcher's group via `nohup ... &`, so
# killing a single dispatcher PID leaves its runner and provider CLI alive.
# This helper therefore terminates whole Unix process GROUPS: for each PID
# recorded in run state (pool_pid, dispatcher_pid, runner_pid) it verifies the
# process identity, resolves its pgid, signals the group with TERM, waits,
# escalates to KILL, and verifies termination.
#
# Exit codes: 0 — every targeted process is gone (or none were ours/alive);
#             2 — usage/environment error;
#             3 — survivors remain after KILL; callers must refuse destructive
#                 follow-up actions (e.g. discard) and report the PIDs.

set -euo pipefail

RUN_ROOT=""
POOL_STATE_FILE=""
TERM_WAIT_SECONDS=10
MATCH_PATTERN="run-with-it-pool|run-with-it-dispatch|run-agent"
PYTHON_BIN="${PYTHON_BIN:-python3}"

fail() {
  echo "run-with-it-stop.sh: $1" >&2
  exit 2
}

usage() {
  cat <<'EOF'
Usage:
  run-with-it-stop.sh --run-root <dir-containing-.run-with-it> \
    [--pool-state-file <file>] [--term-wait-seconds 10] \
    [--match-pattern <egrep-pattern>]
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --run-root) RUN_ROOT="${2:-}"; shift 2 ;;
    --pool-state-file) POOL_STATE_FILE="${2:-}"; shift 2 ;;
    --term-wait-seconds) TERM_WAIT_SECONDS="${2:-}"; shift 2 ;;
    --match-pattern) MATCH_PATTERN="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[ -n "$RUN_ROOT" ] || fail "--run-root is required"
[ -d "$RUN_ROOT" ] || fail "run root not found: $RUN_ROOT"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "python helper runtime not found: $PYTHON_BIN"
case "$TERM_WAIT_SECONDS" in ''|*[!0-9]*) TERM_WAIT_SECONDS=10 ;; esac

if [ -z "$POOL_STATE_FILE" ]; then
  POOL_STATE_FILE="${RUN_ROOT}/.run-with-it/main/pool.state.json"
fi

# Collect "source pid" pairs from the pool state file and every dispatcher
# state file under the run root (sub-coordinator, recovery, and nested worker
# cycle states), deduplicated.
collect_pids() {
  "$PYTHON_BIN" - "$RUN_ROOT" "$POOL_STATE_FILE" <<'PY'
import glob
import json
import os
import sys

run_root, pool_state_file = sys.argv[1], sys.argv[2]
seen = set()

def emit(source, pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return
    if pid > 1 and pid not in seen:
        seen.add(pid)
        print(f"{source}\t{pid}")

def load(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}

emit("pool", load(pool_state_file).get("pool_pid"))
patterns = [
    os.path.join(run_root, ".run-with-it", "issues", "*", "*.state.json"),
    os.path.join(run_root, ".run-with-it", "issues", "*", "workers", "*", "*.state.json"),
]
for pattern in patterns:
    for path in sorted(glob.glob(pattern)):
        data = load(path)
        emit("dispatcher", data.get("dispatcher_pid"))
        emit("runner", data.get("runner_pid"))
PY
}

pid_command() {
  ps -p "$1" -o command= 2>/dev/null || true
}

pid_pgid() {
  ps -p "$1" -o pgid= 2>/dev/null | tr -d '[:space:]' || true
}

TERMINATED=0
ALREADY_DEAD=0
SKIPPED_NOT_OURS=0
TARGET_PIDS=""
SIGNALED_PGIDS=""

signal_group() {
  local sig="$1" pgid="$2"
  [ -n "$pgid" ] && [ "$pgid" -gt 1 ] 2>/dev/null || return 1
  kill "-${sig}" -- "-${pgid}" 2>/dev/null || return 1
}

while IFS=$'\t' read -r source pid; do
  [ -n "$pid" ] || continue
  if ! kill -0 "$pid" 2>/dev/null; then
    ALREADY_DEAD=$((ALREADY_DEAD + 1))
    echo "STOP|type=target|source=${source}|pid=${pid}|action=already-dead"
    continue
  fi
  command_line="$(pid_command "$pid")"
  if ! printf '%s' "$command_line" | grep -Eq "$MATCH_PATTERN"; then
    SKIPPED_NOT_OURS=$((SKIPPED_NOT_OURS + 1))
    echo "STOP|type=target|source=${source}|pid=${pid}|action=skip-not-ours"
    continue
  fi
  pgid="$(pid_pgid "$pid")"
  TARGET_PIDS="${TARGET_PIDS} ${pid}"
  if signal_group TERM "$pgid"; then
    case " $SIGNALED_PGIDS " in
      *" $pgid "*) ;;
      *) SIGNALED_PGIDS="${SIGNALED_PGIDS} ${pgid}" ;;
    esac
    echo "STOP|type=target|source=${source}|pid=${pid}|pgid=${pgid}|action=term-group"
  else
    kill -TERM "$pid" 2>/dev/null || true
    echo "STOP|type=target|source=${source}|pid=${pid}|pgid=${pgid:-unknown}|action=term-pid"
  fi
  TERMINATED=$((TERMINATED + 1))
done <<EOF
$(collect_pids)
EOF

any_alive() {
  local pid
  for pid in $TARGET_PIDS; do
    if kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  done
  return 1
}

waited=0
while [ "$waited" -lt "$TERM_WAIT_SECONDS" ] && any_alive; do
  sleep 1
  waited=$((waited + 1))
done

if any_alive; then
  for pgid in $SIGNALED_PGIDS; do
    signal_group KILL "$pgid" || true
  done
  for pid in $TARGET_PIDS; do
    kill -KILL "$pid" 2>/dev/null || true
  done
  sleep 1
fi

SURVIVORS=""
for pid in $TARGET_PIDS; do
  if kill -0 "$pid" 2>/dev/null; then
    SURVIVORS="${SURVIVORS} ${pid}"
  fi
done

if [ -n "$SURVIVORS" ]; then
  echo "STOP|result=survivors|terminated=${TERMINATED}|already_dead=${ALREADY_DEAD}|skipped_not_ours=${SKIPPED_NOT_OURS}|survivors=$(echo "$SURVIVORS" | tr -s ' ' ',' | sed 's/^,//')"
  exit 3
fi

echo "STOP|result=clean|terminated=${TERMINATED}|already_dead=${ALREADY_DEAD}|skipped_not_ours=${SKIPPED_NOT_OURS}"
