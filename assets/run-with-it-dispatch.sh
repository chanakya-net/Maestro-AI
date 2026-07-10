#!/usr/bin/env bash

set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd -- "${SCRIPT_PATH%/*}" && pwd -P)"
ORIGINAL_ARGS=("$@")

ASSET_ROOT="${ASSETS_DEST:-}"
ROLE=""
ISSUE=""
CYCLE=""
AGENT_NAME=""
MODEL_NAME=""
CONTEXT_FILE=""
PROMPT_FILE=""
LOG_FILE=""
DONE_FILE=""
RESULT_FILE=""
STATE_FILE="${RUN_WITH_IT_STATE_FILE:-}"
REPO_ROOT_OVERRIDE=""
ISSUE_DIR=""
STATUS_FILE="${RUN_WITH_IT_STATUS_FILE:-}"
EVENTS_LOG="${RUN_WITH_IT_EVENTS_LOG:-}"
TAIL_STATE_FILE=""
POLL_SECONDS="${WORKER_POLL_SECONDS:-20}"
QUIET_SECONDS="${RUN_WITH_IT_WORKER_QUIET_SECONDS:-120}"
STALL_SECONDS="${RUN_WITH_IT_WORKER_STALL_SECONDS:-600}"
TIMEOUT_SECONDS="${RUN_WITH_IT_DISPATCH_TIMEOUT_SECONDS:-0}"
if [ "${RUN_WITH_IT_WORKER_HARD_LIMIT_SECONDS+x}" = "x" ] && [ -n "${RUN_WITH_IT_WORKER_HARD_LIMIT_SECONDS}" ]; then
  HARD_LIMIT_SECONDS="${RUN_WITH_IT_WORKER_HARD_LIMIT_SECONDS}"
  HARD_LIMIT_EXPLICIT=1
else
  HARD_LIMIT_SECONDS=7200
  HARD_LIMIT_EXPLICIT=0
fi
DETACH_BOOTSTRAP_SECONDS="${RUN_WITH_IT_DETACH_BOOTSTRAP_SECONDS:-3}"
AUTO_FAIL_STALLED_ROLES="${RUN_WITH_IT_AUTO_FAIL_STALLED_ROLES:-complexity,impl,modify,plan}"
DRY_RUN=0
VALIDATE_ONLY=0
DETACH=0
DETACHED_CHILD="${RUN_WITH_IT_DETACHED_CHILD:-0}"
DISPATCH_OUT_FILE=""

fail() {
  echo "run-with-it-dispatch.sh: $1" >&2
  exit 2
}

usage() {
  cat <<'EOF'
Usage:
  run-with-it-dispatch.sh --role <role> --issue <n> --agent <agent> --model <model> \
    --context-file <file> --prompt-file <file> --log-file <file> --done-file <file> \
    --result-file <file> [--state-file <file>] [--repo-root <path>] [--issue-dir <path>] [--cycle <n>] [--status-file <file>] [--events-log <file>]

Modes:
  --dry-run        Print the wrapped run-agent.sh invocation.
  --validate-only Validate inputs and emit dispatch-ready status, but do not spawn.
  --detach        Start a durable detached dispatcher child and return after recording its PID.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --asset-root) ASSET_ROOT="${2:-}"; shift 2 ;;
    --role) ROLE="${2:-}"; shift 2 ;;
    --issue) ISSUE="${2:-}"; shift 2 ;;
    --cycle) CYCLE="${2:-}"; shift 2 ;;
    --agent) AGENT_NAME="${2:-}"; shift 2 ;;
    --model) MODEL_NAME="${2:-}"; shift 2 ;;
    --context-file) CONTEXT_FILE="${2:-}"; shift 2 ;;
    --prompt-file) PROMPT_FILE="${2:-}"; shift 2 ;;
    --log-file) LOG_FILE="${2:-}"; shift 2 ;;
    --done-file) DONE_FILE="${2:-}"; shift 2 ;;
    --result-file) RESULT_FILE="${2:-}"; shift 2 ;;
    --state-file) STATE_FILE="${2:-}"; shift 2 ;;
    --repo-root) REPO_ROOT_OVERRIDE="${2:-}"; shift 2 ;;
    --issue-dir) ISSUE_DIR="${2:-}"; shift 2 ;;
    --status-file) STATUS_FILE="${2:-}"; shift 2 ;;
    --events-log) EVENTS_LOG="${2:-}"; shift 2 ;;
    --tail-state-file) TAIL_STATE_FILE="${2:-}"; shift 2 ;;
    --poll-seconds) POLL_SECONDS="${2:-}"; shift 2 ;;
    --quiet-seconds) QUIET_SECONDS="${2:-}"; shift 2 ;;
    --stall-seconds) STALL_SECONDS="${2:-}"; shift 2 ;;
    --timeout-seconds) TIMEOUT_SECONDS="${2:-}"; shift 2 ;;
    --hard-limit-seconds) HARD_LIMIT_SECONDS="${2:-}"; HARD_LIMIT_EXPLICIT=1; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --validate-only) VALIDATE_ONLY=1; shift ;;
    --detach) DETACH=1; shift ;;
    --dispatch-out-file) DISPATCH_OUT_FILE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

if [ -z "$ASSET_ROOT" ]; then
  if [ -f "$HOME/.ai-skill-collections/assets/run-agent.sh" ]; then
    ASSET_ROOT="$HOME/.ai-skill-collections/assets"
  else
    ASSET_ROOT="$SCRIPT_DIR"
  fi
fi

RUN_AGENT="${ASSET_ROOT}/run-agent.sh"
WORKER_WATCH="${ASSET_ROOT}/worker-watch.sh"
REGISTRY_FILE="${ASSET_ROOT}/agent-registry.json"
ARTIFACT_HELPER="${ASSET_ROOT}/run-with-it-artifacts.py"
PYTHON_BIN="${PYTHON_BIN:-python3}"

[ -n "$ROLE" ] || fail "--role is required"
[ -n "$ISSUE" ] || fail "--issue is required"
[ -n "$AGENT_NAME" ] || fail "--agent is required"
[ -n "$MODEL_NAME" ] || fail "--model is required"
[ -n "$CONTEXT_FILE" ] || fail "--context-file is required"
[ -n "$PROMPT_FILE" ] || fail "--prompt-file is required"
[ -n "$LOG_FILE" ] || fail "--log-file is required"
[ -n "$DONE_FILE" ] || fail "--done-file is required"
[ -n "$RESULT_FILE" ] || fail "--result-file is required"

if [ "$HARD_LIMIT_EXPLICIT" = 0 ]; then
  case "$ROLE" in
    complexity|impl|modify|review) HARD_LIMIT_SECONDS=7200 ;;
    *) HARD_LIMIT_SECONDS=0 ;;
  esac
fi

[ -x "$RUN_AGENT" ] || fail "runner not executable: $RUN_AGENT"
[ -x "$WORKER_WATCH" ] || fail "worker watcher not executable: $WORKER_WATCH"
[ -f "$REGISTRY_FILE" ] || fail "agent registry not found: $REGISTRY_FILE"
[ -f "$ARTIFACT_HELPER" ] || fail "artifact helper not found: $ARTIFACT_HELPER"
[ -f "$CONTEXT_FILE" ] || fail "context file not found: $CONTEXT_FILE"
[ -f "$PROMPT_FILE" ] || fail "prompt file not found: $PROMPT_FILE"
if [ -n "$REPO_ROOT_OVERRIDE" ]; then
  [ -d "$REPO_ROOT_OVERRIDE" ] || fail "repo root not found: $REPO_ROOT_OVERRIDE"
fi
command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "python helper runtime not found: $PYTHON_BIN"

if [ -z "$STATE_FILE" ]; then
  log_name="$(basename "$LOG_FILE")"
  if [[ "$log_name" == *.log ]]; then
    STATE_FILE="$(dirname "$LOG_FILE")/${log_name%.log}.state.json"
  else
    STATE_FILE="${LOG_FILE}.state.json"
  fi
fi

mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$DONE_FILE")" "$(dirname "$RESULT_FILE")" "$(dirname "$STATE_FILE")"
if [ -n "$STATUS_FILE" ]; then mkdir -p "$(dirname "$STATUS_FILE")"; fi
if [ -n "$EVENTS_LOG" ]; then mkdir -p "$(dirname "$EVENTS_LOG")"; fi

if [ -z "$TAIL_STATE_FILE" ]; then
  cycle_part="${CYCLE:-0}"
  TAIL_STATE_FILE="$(pwd -P)/.run-with-it/status/issue-${ISSUE}-${ROLE}-cycle-${cycle_part}.tail.sha"
fi

if [ -z "$ISSUE_DIR" ]; then
  ISSUE_DIR="${RUN_WITH_IT_ISSUE_DIR:-$(pwd -P)/.run-with-it/issues/${ISSUE}}"
fi
mkdir -p "$ISSUE_DIR"

write_status() {
  local line="$1"
  printf '%s\n' "$line"
  printf '%s\n' "$line" >> "$LOG_FILE"
  if [ -n "$STATUS_FILE" ]; then printf '%s\n' "$line" > "$STATUS_FILE"; fi
  if [ -n "$EVENTS_LOG" ]; then printf '%s\n' "$line" >> "$EVENTS_LOG"; fi
}

iso_now() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

json_string() {
  local value="$1"
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$value"
  elif command -v jq >/dev/null 2>&1; then
    jq -Rn --arg value "$value" '$value'
  else
    printf '"%s"' "$(printf '%s' "$value" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  fi
}

json_nullable_string() {
  local value="$1"
  if [ -z "$value" ]; then
    printf 'null'
  else
    json_string "$value"
  fi
}

worktree_status_json() {
  if [ -z "$REPO_ROOT_OVERRIDE" ] || ! is_implementation_role || ! git -C "$REPO_ROOT_OVERRIDE" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    printf '{"dirty": false, "changed_files": []}'
    return 0
  fi

  git -C "$REPO_ROOT_OVERRIDE" status --short --untracked-files=all | "$PYTHON_BIN" -c '
import json
import sys

rows = [line.rstrip("\n") for line in sys.stdin if line.strip()]
files = []
for row in rows[:200]:
    path = row[3:] if len(row) > 3 else row
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    files.append(path)
print(json.dumps({"dirty": bool(rows), "changed_files": files, "changed_files_truncated": len(rows) > len(files)}))
'
}

is_implementation_role() {
  [ "$ROLE" = "impl" ] || [ "$ROLE" = "modify" ]
}

should_auto_fail_stalled_role() {
  case ",${AUTO_FAIL_STALLED_ROLES}," in
    *",${ROLE},"*) return 0 ;;
    *) return 1 ;;
  esac
}

repo_root_for_worker() {
  printf '%s\n' "${REPO_ROOT_OVERRIDE:-${REPO_ROOT:-$(pwd -P)}}"
}

result_artifact_failure_reason() {
  "$PYTHON_BIN" "$ARTIFACT_HELPER" failure-reason \
    --role "$ROLE" \
    --issue "$ISSUE" \
    --result-file "$RESULT_FILE" \
    --done-file "$DONE_FILE" \
    --issue-dir "$ISSUE_DIR" \
    --repo-root "$(repo_root_for_worker)" \
    --pre-spawn-head "${pre_spawn_head:-}"
}

result_artifact_failure_class() {
  "$PYTHON_BIN" "$ARTIFACT_HELPER" failure-class \
    --role "$ROLE" \
    --issue "$ISSUE" \
    --result-file "$RESULT_FILE" \
    --done-file "$DONE_FILE" \
    --log-file "$LOG_FILE" \
    --issue-dir "$ISSUE_DIR" \
    --repo-root "$(repo_root_for_worker)" \
    --pre-spawn-head "${pre_spawn_head:-}"
}

synthesize_result_artifact_if_possible() {
  "$PYTHON_BIN" "$ARTIFACT_HELPER" synthesize \
    --role "$ROLE" \
    --issue "$ISSUE" \
    --result-file "$RESULT_FILE" \
    --done-file "$DONE_FILE" \
    --log-file "$LOG_FILE" \
    --issue-dir "$ISSUE_DIR" \
    --repo-root "$(repo_root_for_worker)" \
    --pre-spawn-head "${pre_spawn_head:-}" >/dev/null 2>&1
}

# Like the above but for the stall path: a stalled runner never wrote a DONE
# sentinel, so --from-stall lets the helper salvage a committed-but-unreported
# HEAD advance or an uncommitted dirty tree (it commits it) before we kill it.
synthesize_stalled_result_if_possible() {
  "$PYTHON_BIN" "$ARTIFACT_HELPER" synthesize \
    --role "$ROLE" \
    --issue "$ISSUE" \
    --result-file "$RESULT_FILE" \
    --done-file "$DONE_FILE" \
    --log-file "$LOG_FILE" \
    --issue-dir "$ISSUE_DIR" \
    --repo-root "$(repo_root_for_worker)" \
    --pre-spawn-head "${pre_spawn_head:-}" \
    --from-stall >/dev/null 2>&1
}

completion_failure_reason() {
  local result_reason

  if [ ! -s "$DONE_FILE" ]; then
    printf 'missing-done-sentinel\n'
    return 0
  fi

  result_reason="$(result_artifact_failure_reason)"
  if [ -n "$result_reason" ]; then
    printf '%s\n' "$result_reason"
  fi
}

completion_ready() {
  local reason

  if [ ! -s "$DONE_FILE" ]; then
    return 1
  fi

  reason="$(result_artifact_failure_reason)"
  [ -z "$reason" ]
}

file_mtime_epoch() {
  local file="$1"
  if [ ! -e "$file" ]; then
    printf '0'
  elif stat -c %Y "$file" >/dev/null 2>&1; then
    stat -c %Y "$file"
  else
    stat -f %m "$file"
  fi
}

file_size_bytes() {
  local file="$1"
  if [ -s "$file" ]; then
    wc -c < "$file" | tr -d ' '
  else
    printf '0'
  fi
}

log_signature() {
  if [ -e "$LOG_FILE" ]; then
    printf '%s:%s' "$(file_size_bytes "$LOG_FILE")" "$(file_mtime_epoch "$LOG_FILE")"
  else
    printf 'missing'
  fi
}

latest_heartbeat_line() {
  if [ -s "$LOG_FILE" ]; then
    grep -E '(^|[^A-Z])STATUS\|type=(wrapper-)?heartbeat\|' "$LOG_FILE" 2>/dev/null | tail -n 1 || true
  fi
}

write_worker_state() {
  local state="$1"
  local alive="$2"
  local exit_code="${3:-}"
  local stall_reason="${4:-}"
  local failure_class="${5:-}"
  local now_epoch now_iso done_present result_present log_present log_size log_mtime
  local seconds_since_output seconds_since_heartbeat runner_pid_json exit_code_json stall_reason_json failure_class_json tmp_file

  now_epoch="$(date +%s)"
  now_iso="$(iso_now)"
  done_present=false
  result_present=false
  log_present=false
  if [ -s "$DONE_FILE" ]; then done_present=true; fi
  if [ -s "$RESULT_FILE" ]; then result_present=true; fi
  if [ -s "$LOG_FILE" ]; then log_present=true; fi

  log_size="$(file_size_bytes "$LOG_FILE")"
  log_mtime="$(file_mtime_epoch "$LOG_FILE")"
  seconds_since_output=$((now_epoch - last_output_epoch))
  if [ "${last_heartbeat_epoch:-0}" -gt 0 ]; then
    seconds_since_heartbeat=$((now_epoch - last_heartbeat_epoch))
  else
    seconds_since_heartbeat=null
  fi

  runner_pid_json="null"
  if [ -n "${pid:-}" ]; then runner_pid_json="$pid"; fi
  exit_code_json="null"
  if [ -n "$exit_code" ]; then exit_code_json="$exit_code"; fi
  stall_reason_json="null"
  if [ -n "$stall_reason" ]; then stall_reason_json="$(json_string "$stall_reason")"; fi
  failure_class_json="null"
  if [ -n "$failure_class" ]; then failure_class_json="$(json_string "$failure_class")"; fi
  worktree_status="$(worktree_status_json)"

  tmp_file="${STATE_FILE}.tmp.$$"
  if [ "${RUN_WITH_IT_TEST_FAIL_READY_STATE:-0}" = "1" ] && [ "$state" = "ready" ]; then
    : > "$tmp_file"
    return 98
  fi
  if [ "${RUN_WITH_IT_TEST_FAIL_STARTING_STATE:-0}" = "1" ] && [ "$state" = "starting" ]; then
    : > "$tmp_file"
    return 97
  fi
  cat > "$tmp_file" <<JSON
{
  "schema_version": 1,
  "issue": $(json_string "$ISSUE"),
  "role": $(json_string "$ROLE"),
  "cycle": $(json_nullable_string "$CYCLE"),
  "state": $(json_string "$state"),
  "dispatcher_pid": $$,
  "runner_pid": ${runner_pid_json},
  "agent": $(json_string "$AGENT_NAME"),
  "model": $(json_string "$MODEL_NAME"),
  "alive": ${alive},
  "done": ${done_present},
  "result_present": ${result_present},
  "log_present": ${log_present},
  "log_file": $(json_string "$LOG_FILE"),
  "done_file": $(json_string "$DONE_FILE"),
  "result_file": $(json_string "$RESULT_FILE"),
  "state_file": $(json_string "$STATE_FILE"),
  "log_size_bytes": ${log_size},
  "log_mtime_epoch": ${log_mtime},
  "quiet_seconds": ${QUIET_SECONDS},
  "stall_seconds": ${STALL_SECONDS},
  "hard_limit_seconds": ${HARD_LIMIT_SECONDS},
  "seconds_since_last_output": ${seconds_since_output},
  "seconds_since_last_heartbeat": ${seconds_since_heartbeat},
  "started_at": $(json_string "$started_iso"),
  "last_output_at": $(json_string "$last_output_at"),
  "last_heartbeat_at": $(json_nullable_string "${last_heartbeat_at:-}"),
  "updated_at": $(json_string "$now_iso"),
  "stall_reason": ${stall_reason_json},
  "failure_class": ${failure_class_json},
  "exit_code": ${exit_code_json},
  "worktree": ${worktree_status}
}
JSON
  mv "$tmp_file" "$STATE_FILE"
}

refresh_log_activity() {
  local now_epoch="$1"
  local signature latest_heartbeat

  signature="$(log_signature)"
  if [ "$signature" != "$last_log_signature" ]; then
    last_log_signature="$signature"
    last_output_epoch="$now_epoch"
    last_output_at="$(iso_now)"
    latest_heartbeat="$(latest_heartbeat_line)"
    if [ -n "$latest_heartbeat" ] && [ "$latest_heartbeat" != "${last_heartbeat_line:-}" ]; then
      last_heartbeat_line="$latest_heartbeat"
      last_heartbeat_epoch="$now_epoch"
      last_heartbeat_at="$last_output_at"
    fi
  fi
}

terminate_runner_tree() {
  local target="${1:-${pid:-}}" child
  if [ -z "$target" ]; then
    return 0
  fi
  if command -v pgrep >/dev/null 2>&1; then
    for child in $(pgrep -P "$target" 2>/dev/null || true); do
      terminate_runner_tree "$child"
    done
  fi
  kill -TERM "$target" 2>/dev/null || true
  sleep 1
  if kill -0 "$target" 2>/dev/null; then
    kill -KILL "$target" 2>/dev/null || true
  fi
}

cycle_field=""
if [ -n "$CYCLE" ]; then
  cycle_field="|cycle=${CYCLE}"
fi

if [ "$DRY_RUN" = 1 ]; then
  printf 'GUI_MODE=0 AGENT_REGISTRY_FILE=%s REPO_ROOT=%s RUN_WITH_IT_ISSUE_DIR=%s RUN_WITH_IT_STATUS_FILE=%s RUN_WITH_IT_EVENTS_LOG=%s RUN_WITH_IT_LOG_FILE=%s RUN_WITH_IT_DONE_FILE=%s RUN_WITH_IT_RESULT_FILE=%s RUN_WITH_IT_STATE_FILE=%s RUN_WITH_IT_ARTIFACT_HELPER=%s RUN_WITH_IT_ROLE=%s RUN_WITH_IT_ISSUE=%s %s --agent %s --model %s --context-file %s --prompt-file %s --unattended\n' \
    "$REGISTRY_FILE" "$(repo_root_for_worker)" "$ISSUE_DIR" "$STATUS_FILE" "$EVENTS_LOG" "$LOG_FILE" "$DONE_FILE" "$RESULT_FILE" "$STATE_FILE" "$ARTIFACT_HELPER" "$ROLE" "$ISSUE" \
    "$RUN_AGENT" "$AGENT_NAME" "$MODEL_NAME" "$CONTEXT_FILE" "$PROMPT_FILE"
  exit 0
fi

if [ "$DETACH" = 1 ] && [ "$DETACHED_CHILD" != "1" ] && [ "$VALIDATE_ONLY" != "1" ]; then
  if [ -z "$DISPATCH_OUT_FILE" ]; then
    if [[ "$LOG_FILE" == *.log ]]; then
      DISPATCH_OUT_FILE="${LOG_FILE%.log}.dispatch.out"
    else
      DISPATCH_OUT_FILE="${LOG_FILE}.dispatch.out"
    fi
  fi
  mkdir -p "$(dirname "$DISPATCH_OUT_FILE")"
  # nohup alone can remain in the caller's process group; create a new session
  # so short-lived tool-call cleanup cannot kill the dispatcher before runner PID.
  detached_pid="$("$PYTHON_BIN" - "$DISPATCH_OUT_FILE" "$0" "${ORIGINAL_ARGS[@]}" <<'PY'
import os
import subprocess
import sys

out_file = sys.argv[1]
command = sys.argv[2:]
env = os.environ.copy()
env["RUN_WITH_IT_DETACHED_CHILD"] = "1"

with open(os.devnull, "rb") as stdin, open(out_file, "wb") as stdout:
    process = subprocess.Popen(
        command,
        stdin=stdin,
        stdout=stdout,
        stderr=subprocess.STDOUT,
        env=env,
        close_fds=True,
        start_new_session=True,
    )

print(process.pid)
PY
)"
  write_status "STATUS|type=dispatch-detached|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${detached_pid}|out_file=${DISPATCH_OUT_FILE}"
  bootstrap_checks=0
  case "$DETACH_BOOTSTRAP_SECONDS" in
    ''|*[!0-9]*) bootstrap_checks=30 ;;
    *) bootstrap_checks=$((DETACH_BOOTSTRAP_SECONDS * 10)) ;;
  esac
  while [ "$bootstrap_checks" -gt 0 ]; do
    if "$PYTHON_BIN" - "$STATE_FILE" <<'PY' >/dev/null 2>&1
import json
import sys

try:
    with open(sys.argv[1], "r", encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    raise SystemExit(1)
if data.get("runner_pid") or data.get("state") in {"completed", "failed", "artifact-recovery-required"}:
    raise SystemExit(0)
raise SystemExit(1)
PY
    then
      exit 0
    fi
    if ! kill -0 "$detached_pid" 2>/dev/null; then
      # See note below on the dead-runner wait: avoid tripping the ERR trap on
      # bash 3.2 while still capturing the real exit code.
      detached_status=0
      wait "$detached_pid" 2>/dev/null || detached_status="$?"
      if ! "$PYTHON_BIN" - "$STATE_FILE" <<'PY' >/dev/null 2>&1
import json
import sys

try:
    with open(sys.argv[1], "r", encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    raise SystemExit(1)
if data.get("runner_pid") or data.get("state") in {"completed", "failed", "artifact-recovery-required"}:
    raise SystemExit(0)
raise SystemExit(1)
PY
      then
        write_status "STATUS|type=dispatch-bootstrap-failed|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${detached_pid}|reason=dispatcher-exited-before-runner-pid|exit_code=${detached_status}|state_file=${STATE_FILE}"
        exit 1
      fi
      exit 0
    fi
    bootstrap_checks=$((bootstrap_checks - 1))
    sleep 0.1
  done
  exit 0
fi

pre_spawn_head=""
if is_implementation_role && git -C "$(repo_root_for_worker)" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  pre_spawn_head="$(git -C "$(repo_root_for_worker)" rev-parse HEAD 2>/dev/null || true)"
fi

dispatch_phase="pre-ready"
on_dispatch_error() {
  local exit_code="$?"
  trap - ERR
  case "${dispatch_phase:-}" in
    ready-state)
      write_status "STATUS|type=dispatch-pre-start-failed|issue=${ISSUE}|role=${ROLE}${cycle_field}|reason=state-write-failed|exit_code=${exit_code}|state_file=${STATE_FILE}"
      ;;
    starting)
      write_status "STATUS|type=dispatch-bootstrap-failed|issue=${ISSUE}|role=${ROLE}${cycle_field}|reason=state-write-failed-before-runner-pid|exit_code=${exit_code}|state_file=${STATE_FILE}"
      ;;
  esac
  exit "$exit_code"
}
trap on_dispatch_error ERR

started_at="$(date +%s)"
started_iso="$(iso_now)"
last_output_epoch="$started_at"
last_output_at="$started_iso"
last_heartbeat_epoch=0
last_heartbeat_at=""
last_heartbeat_line=""
last_log_signature="$(log_signature)"
last_state="ready"

write_status "STATUS|type=dispatch-ready|issue=${ISSUE}|role=${ROLE}${cycle_field}|agent=${AGENT_NAME}|model=${MODEL_NAME}|result_file=${RESULT_FILE}"
last_log_signature="$(log_signature)"
dispatch_phase="ready-state"
write_worker_state "ready" "false"

if [ "$VALIDATE_ONLY" = 1 ]; then
  dispatch_phase="validate-complete"
  exit 0
fi

dispatch_phase="starting"
write_status "STATUS|type=dispatch-start|issue=${ISSUE}|role=${ROLE}${cycle_field}|agent=${AGENT_NAME}|model=${MODEL_NAME}"
last_log_signature="$(log_signature)"
last_state="starting"
write_worker_state "starting" "false"
dispatch_phase="running"

GUI_MODE="${GUI_MODE:-0}" \
AGENT_REGISTRY_FILE="$REGISTRY_FILE" \
REPO_ROOT="$(repo_root_for_worker)" \
RUN_WITH_IT_ISSUE_DIR="$ISSUE_DIR" \
RUN_WITH_IT_STATUS_FILE="$STATUS_FILE" \
RUN_WITH_IT_EVENTS_LOG="$EVENTS_LOG" \
RUN_WITH_IT_LOG_FILE="$LOG_FILE" \
RUN_WITH_IT_DONE_FILE="$DONE_FILE" \
RUN_WITH_IT_RESULT_FILE="$RESULT_FILE" \
RUN_WITH_IT_STATE_FILE="$STATE_FILE" \
RUN_WITH_IT_ARTIFACT_HELPER="$ARTIFACT_HELPER" \
RUN_WITH_IT_ROLE="$ROLE" \
RUN_WITH_IT_ISSUE="$ISSUE" \
nohup "$RUN_AGENT" \
  --agent "$AGENT_NAME" \
  --model "$MODEL_NAME" \
  --context-file "$CONTEXT_FILE" \
  --prompt-file "$PROMPT_FILE" \
  --unattended \
  >/dev/null 2>&1 < /dev/null &

pid="$!"
write_status "STATUS|type=dispatch-pid|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|done_file=${DONE_FILE}|result_file=${RESULT_FILE}"
last_log_signature="$(log_signature)"
last_state="running"
write_worker_state "running" "true"

while true; do
  sleep "$POLL_SECONDS"
  now="$(date +%s)"
  refresh_log_activity "$now"

  "$WORKER_WATCH" \
    --pid "$pid" \
    --done-file "$DONE_FILE" \
    --log-file "$LOG_FILE" \
    --tail-state-file "$TAIL_STATE_FILE" \
    --tail-lines "${WORKER_LOG_TAIL_LINES:-5}" >/dev/null || true

  if completion_ready; then
    alive=false
    if kill -0 "$pid" 2>/dev/null; then alive=true; fi
    write_worker_state "completed" "$alive"
    write_status "STATUS|type=dispatch-complete|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|result_file=${RESULT_FILE}"
    exit 0
  fi

  if ! kill -0 "$pid" 2>/dev/null; then
    # Capture the runner exit code without tripping the ERR trap. On bash 3.2
    # (macOS) the ERR trap fires on `wait` returning non-zero even under
    # `set +e`; the `|| exit_code=$?` form makes the command succeed so the
    # trap never runs, while still recording the real exit code.
    exit_code=0
    wait "$pid" 2>/dev/null || exit_code="$?"
    # Synthesis is gated by git ground-truth inside the helper (HEAD must have
    # advanced past pre_spawn_head with committed files), so it is safe to
    # attempt regardless of exit code: a worker that committed real work and
    # then crashed (e.g. a provider auth/quota failure mid-run) is salvaged
    # instead of burning a fallback attempt.
    if synthesize_result_artifact_if_possible; then
      write_status "STATUS|type=result-artifact-synthesized|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|exit_code=${exit_code}|result_file=${RESULT_FILE}"
      last_log_signature="$(log_signature)"
    fi
    artifact_reason="$(result_artifact_failure_reason)"
    if [ "$artifact_reason" = "artifact-recovery-required" ]; then
      write_worker_state "artifact-recovery-required" "false" "$exit_code" "$artifact_reason" "capability"
      write_status "STATUS|type=dispatch-recovery-required|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|reason=${artifact_reason}|result_file=${RESULT_FILE}"
      exit 75
    fi
    if completion_ready; then
      write_worker_state "completed" "false" "$exit_code"
      write_status "STATUS|type=dispatch-complete|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|result_file=${RESULT_FILE}"
      exit 0
    fi
    failure_reason="$(completion_failure_reason)"
    if [ -z "$failure_reason" ]; then
      failure_reason="process-exited-missing-done-or-result"
    fi
    failure_class="$(result_artifact_failure_class)"
    write_worker_state "failed" "false" "$exit_code" "$failure_reason" "$failure_class"
    write_status "STATUS|type=dispatch-failed|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|reason=${failure_reason}|failure_class=${failure_class}|done_file=${DONE_FILE}|result_file=${RESULT_FILE}"
    exit 1
  fi

  silence_seconds=$((now - last_output_epoch))
  state="running"
  stall_reason=""
  if [ "$silence_seconds" -ge "$STALL_SECONDS" ]; then
    state="stalled"
    stall_reason="alive-but-silent"
  elif [ "$silence_seconds" -ge "$QUIET_SECONDS" ]; then
    state="quiet"
    stall_reason="alive-but-quiet"
  fi

  write_worker_state "$state" "true" "" "$stall_reason"
  if [ "$state" != "$last_state" ]; then
    if [ "$state" = "quiet" ]; then
      write_status "STATUS|type=worker-quiet|issue=${ISSUE}|role=${ROLE}${cycle_field}|reason=alive-but-quiet|silence_seconds=${silence_seconds}|state_file=${STATE_FILE}"
      last_log_signature="$(log_signature)"
    elif [ "$state" = "stalled" ]; then
      write_status "STATUS|type=worker-stalled|issue=${ISSUE}|role=${ROLE}${cycle_field}|reason=alive-but-silent|silence_seconds=${silence_seconds}|state_file=${STATE_FILE}"
      last_log_signature="$(log_signature)"
    fi
    last_state="$state"
  fi

  elapsed=$((now - started_at))
  if [ "$HARD_LIMIT_SECONDS" != "0" ] && [ "$elapsed" -ge "$HARD_LIMIT_SECONDS" ]; then
    if synthesize_stalled_result_if_possible; then
      artifact_reason="$(result_artifact_failure_reason)"
      if [ "$artifact_reason" = "artifact-recovery-required" ]; then
        write_status "STATUS|type=worker-hard-limit|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|elapsed=${elapsed}|action=preserve-for-recovery"
        write_worker_state "artifact-recovery-required" "false" "75" "$artifact_reason" "capability"
        write_status "STATUS|type=dispatch-recovery-required|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|reason=${artifact_reason}|result_file=${RESULT_FILE}"
        set +e
        terminate_runner_tree "$pid" >/dev/null 2>&1
        set -e
        exit 75
      fi
    fi
    write_status "STATUS|type=worker-hard-limit|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|elapsed=${elapsed}|action=terminate-runner"
    write_worker_state "failed" "false" "124" "hard-limit-exceeded" "capability"
    write_status "STATUS|type=dispatch-failed|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|reason=hard-limit-exceeded|failure_class=capability|done_file=${DONE_FILE}|result_file=${RESULT_FILE}"
    set +e
    terminate_runner_tree "$pid" >/dev/null 2>&1
    set -e
    exit 124
  fi

  if [ "$state" = "stalled" ] && should_auto_fail_stalled_role; then
    # Before killing a silent-but-alive runner, salvage any work it left behind:
    # a committed-but-unreported HEAD advance, or an uncommitted dirty tree
    # (the helper commits it). Only when there is genuinely no git progress do we
    # fail the worker (alive-but-silent stalls: issues 601/602/616/617/618).
    if synthesize_stalled_result_if_possible; then
      artifact_reason="$(result_artifact_failure_reason)"
      if [ "$artifact_reason" = "artifact-recovery-required" ]; then
        write_status "STATUS|type=worker-stall-timeout|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|reason=alive-but-silent|action=preserve-for-recovery"
        write_status "STATUS|type=result-artifact-synthesized|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|reason=stall-salvage|result_file=${RESULT_FILE}"
        write_worker_state "artifact-recovery-required" "false" "75" "$artifact_reason" "capability"
        write_status "STATUS|type=dispatch-recovery-required|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|reason=${artifact_reason}|result_file=${RESULT_FILE}"
        set +e
        terminate_runner_tree "$pid" >/dev/null 2>&1
        set -e
        exit 75
      fi
    fi
    if completion_ready; then
      write_status "STATUS|type=worker-stall-timeout|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|reason=alive-but-silent|action=salvage-and-terminate"
      write_worker_state "completed" "false" "0" "salvaged-from-stall"
      write_status "STATUS|type=dispatch-complete|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|result_file=${RESULT_FILE}"
      set +e
      terminate_runner_tree "$pid" >/dev/null 2>&1
      set -e
      exit 0
    fi
    write_status "STATUS|type=worker-stall-timeout|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|reason=alive-but-silent|action=terminate-runner"
    failure_class="$(result_artifact_failure_class)"
    write_worker_state "failed" "false" "124" "alive-but-silent" "$failure_class"
    write_status "STATUS|type=dispatch-failed|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|reason=alive-but-silent|failure_class=${failure_class}|done_file=${DONE_FILE}|result_file=${RESULT_FILE}"
    set +e
    terminate_runner_tree "$pid" >/dev/null 2>&1
    set -e
    exit 1
  fi

  if [ "$TIMEOUT_SECONDS" != "0" ]; then
    if [ "$elapsed" -ge "$TIMEOUT_SECONDS" ]; then
      write_status "STATUS|type=dispatch-stall|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|elapsed=${elapsed}|action=alert-user"
      last_log_signature="$(log_signature)"
      TIMEOUT_SECONDS=0
    fi
  fi
done
