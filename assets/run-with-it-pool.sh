#!/usr/bin/env bash

set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd -- "${SCRIPT_PATH%/*}" && pwd -P)"

ASSET_ROOT="${ASSETS_DEST:-}"
STATE_FILE="$(pwd -P)/.run-with-it/main-state.json"
PARALLEL_JOBS=""
SUB_COORD_AGENT="${SUB_COORD_AGENT:-codex}"
SUB_COORD_MODEL="${SUB_COORD_MODEL:-gpt-5.6-sol}"
STATUS_FILE="${RUN_WITH_IT_STATUS_FILE:-$(pwd -P)/.run-with-it/status/current.txt}"
EVENTS_LOG="${RUN_WITH_IT_EVENTS_LOG:-$(pwd -P)/.run-with-it/status/events.log}"
MAIN_LOG="$(pwd -P)/.run-with-it/main/main.log"
POLL_SECONDS="${STATUS_POLL_SECONDS:-10}"
TIMEOUT_SECONDS="${SUB_COORD_TIMEOUT_SECONDS:-3600}"
MAX_SUB_COORD_RECOVERY_ATTEMPTS="${MAX_SUB_COORD_RECOVERY_ATTEMPTS:-2}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DRY_RUN=0
VALIDATE_ONLY=0
DETACH=0
POOL_DETACHED_CHILD="${RUN_WITH_IT_POOL_DETACHED_CHILD:-0}"
POOL_STATE_FILE=""

fail() {
  echo "run-with-it-pool.sh: $1" >&2
  exit 2
}

usage() {
  cat <<'EOF'
Usage:
  run-with-it-pool.sh --state-file .run-with-it/main-state.json \
    --parallel-jobs 4 --agent codex --model gpt-5.6-sol

Modes:
  --dry-run        Print the initial dispatch commands without spawning.
  --validate-only Validate state and emit pool-ready status without spawning.
  --detach         Start a durable detached pool supervisor and return after
                   recording its PID in the pool state file.
EOF
}

ORIGINAL_ARGS=("$@")

while [ "$#" -gt 0 ]; do
  case "$1" in
    --asset-root) ASSET_ROOT="${2:-}"; shift 2 ;;
    --state-file) STATE_FILE="${2:-}"; shift 2 ;;
    --parallel-jobs) PARALLEL_JOBS="${2:-}"; shift 2 ;;
    --agent) SUB_COORD_AGENT="${2:-}"; shift 2 ;;
    --model) SUB_COORD_MODEL="${2:-}"; shift 2 ;;
    --status-file) STATUS_FILE="${2:-}"; shift 2 ;;
    --events-log) EVENTS_LOG="${2:-}"; shift 2 ;;
    --main-log) MAIN_LOG="${2:-}"; shift 2 ;;
    --poll-seconds) POLL_SECONDS="${2:-}"; shift 2 ;;
    --timeout-seconds) TIMEOUT_SECONDS="${2:-}"; shift 2 ;;
    --pool-state-file) POOL_STATE_FILE="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --validate-only) VALIDATE_ONLY=1; shift ;;
    --detach) DETACH=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

if [ -z "$ASSET_ROOT" ]; then
  if [ -f "$HOME/.ai-skill-collections/assets/run-with-it-dispatch.sh" ]; then
    ASSET_ROOT="$HOME/.ai-skill-collections/assets"
  else
    ASSET_ROOT="$SCRIPT_DIR"
  fi
fi

DISPATCHER="${ASSET_ROOT}/run-with-it-dispatch.sh"
PROMPT_FILE="${ASSET_ROOT}/sub-coordinator-prompt.md"
MERGE_RECOVERY_PROMPT_FILE="${ASSET_ROOT}/merge-recovery-prompt.md"
STATE_HELPER="${ASSET_ROOT}/run-with-it-state.py"
GITHUB_UPDATE_HELPER="${ASSET_ROOT}/run-with-it-github-update.py"

# State helper maps merge_failed reports to merge_recovery before terminal
# GitHub updates are attempted.
[ -x "$DISPATCHER" ] || fail "dispatcher not executable: $DISPATCHER"
[ -f "$PROMPT_FILE" ] || fail "sub-coordinator prompt not found: $PROMPT_FILE"
[ -f "$MERGE_RECOVERY_PROMPT_FILE" ] || fail "merge recovery prompt not found: $MERGE_RECOVERY_PROMPT_FILE"
[ -f "$STATE_HELPER" ] || fail "state helper not found: $STATE_HELPER"
[ -f "$GITHUB_UPDATE_HELPER" ] || fail "GitHub update helper not found: $GITHUB_UPDATE_HELPER"
[ -f "$STATE_FILE" ] || fail "state file not found: $STATE_FILE"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "python helper runtime not found: $PYTHON_BIN"

RUN_ROOT="$(cd "$(dirname "$STATE_FILE")/.." && pwd -P)"
if [ -z "$PARALLEL_JOBS" ]; then
  PARALLEL_JOBS="$("$PYTHON_BIN" "$STATE_HELPER" parallel-jobs --state-file "$STATE_FILE")"
fi
if [ -z "$POOL_STATE_FILE" ]; then
  POOL_STATE_FILE="${RUN_ROOT}/.run-with-it/main/pool.state.json"
fi

mkdir -p "$(dirname "$MAIN_LOG")" "$(dirname "$STATUS_FILE")" "$(dirname "$EVENTS_LOG")" "$(dirname "$POOL_STATE_FILE")"

write_pool_state() {
  local pid="$1"
  "$PYTHON_BIN" - "$POOL_STATE_FILE" "$pid" "$STATE_FILE" <<'PY'
import json
import sys
import time

path, pid, state_file = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, "w", encoding="utf-8") as handle:
    json.dump({"pool_pid": int(pid), "started_at": int(time.time()), "state_file": state_file}, handle)
PY
}

write_status() {
  local line="$1"
  printf '%s\n' "$line"
  printf '%s\n' "$line" >> "$MAIN_LOG"
  printf '%s\n' "$line" > "$STATUS_FILE"
  printf '%s\n' "$line" >> "$EVENTS_LOG"
}

if [ "$DETACH" = 1 ] && [ "$POOL_DETACHED_CHILD" != "1" ] && [ "$VALIDATE_ONLY" != 1 ] && [ "$DRY_RUN" != 1 ]; then
  # nohup alone can remain in the caller's process group; create a new session
  # so short-lived tool-call cleanup cannot kill the pool supervisor.
  detached_pid="$("$PYTHON_BIN" - "$MAIN_LOG" "$SCRIPT_PATH" "${ORIGINAL_ARGS[@]}" <<'PY'
import os
import subprocess
import sys

out_file = sys.argv[1]
command = sys.argv[2:]
env = os.environ.copy()
env["RUN_WITH_IT_POOL_DETACHED_CHILD"] = "1"

with open(os.devnull, "rb") as stdin, open(out_file, "ab") as stdout:
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
  write_pool_state "$detached_pid"
  write_status "STATUS|type=pool-detached|pid=${detached_pid}|pool_state_file=${POOL_STATE_FILE}"
  exit 0
fi

if [ "$VALIDATE_ONLY" != 1 ] && [ "$DRY_RUN" != 1 ]; then
  write_pool_state "$$"
fi

# Compact per-issue stage board for main-coordinator visibility. Emitted only
# when the board changes so the event log stays bounded.
LAST_RUN_BOARD=""
emit_run_board() {
  local board
  board="$("$PYTHON_BIN" "$STATE_HELPER" status-board --oneline --state-file "$STATE_FILE" 2>/dev/null || true)"
  [ -n "$board" ] || return 0
  if [ "$board" != "$LAST_RUN_BOARD" ]; then
    write_status "STATUS|type=run-board|board=${board}"
    LAST_RUN_BOARD="$board"
  fi
}

ready_issues() {
  "$PYTHON_BIN" "$STATE_HELPER" ready-issues --state-file "$STATE_FILE" --limit "$1"
}

ready_missing_context_count() {
  "$PYTHON_BIN" "$STATE_HELPER" ready-missing-context-count --state-file "$STATE_FILE"
}

context_file_for() {
  "$PYTHON_BIN" "$STATE_HELPER" context-file-for --state-file "$STATE_FILE" --issue "$1"
}

issue_dir_for() {
  printf '%s/.run-with-it/issues/%s\n' "$RUN_ROOT" "$1"
}

mark_in_progress() {
  "$PYTHON_BIN" "$STATE_HELPER" mark-in-progress \
    --state-file "$STATE_FILE" \
    --issue "$1" \
    --pid "$2" \
    --context-file "$3" \
    --log-file "$4" \
    --done-file "$5" \
    --report-file "$6" \
    --issue-dir "$7" \
    --sub-coord-state-file "${8:-}"
}

finalize_issue() {
  "$PYTHON_BIN" "$STATE_HELPER" finalize-issue \
    --state-file "$STATE_FILE" \
    --issue "$1" \
    --report-file "$2"
}

analyze_sub_coord_failure() {
  "$PYTHON_BIN" "$STATE_HELPER" analyze-sub-coord-failure \
    --state-file "$STATE_FILE" \
    --issue "$1" \
    --report-file "$2" \
    --max-attempts "$MAX_SUB_COORD_RECOVERY_ATTEMPTS"
}

decision_field() {
  local decision_json="$1"
  local key="$2"
  printf '%s' "$decision_json" | "$PYTHON_BIN" -c '
import json
import sys

payload = json.load(sys.stdin)
value = payload.get(sys.argv[1])
if value is None:
    print("")
else:
    print(value)
' "$key"
}

write_sub_coord_recovery_context() {
  "$PYTHON_BIN" "$STATE_HELPER" write-sub-coord-recovery-context \
    --state-file "$STATE_FILE" \
    --issue "$1" \
    --context-file "$2" \
    --attempt "$3" \
    --reason "$4"
}

mark_sub_coord_recovery_started() {
  "$PYTHON_BIN" "$STATE_HELPER" mark-sub-coord-recovery-started \
    --state-file "$STATE_FILE" \
    --issue "$1" \
    --attempt "$2" \
    --reason "$3" \
    --context-file "$4"
}

mark_sub_coord_recovery_dispatch_failed() {
  "$PYTHON_BIN" "$STATE_HELPER" mark-sub-coord-recovery-dispatch-failed \
    --state-file "$STATE_FILE" \
    --issue "$1" \
    --report-file "$2"
}

write_merge_recovery_context() {
  "$PYTHON_BIN" "$STATE_HELPER" write-merge-recovery-context \
    --state-file "$STATE_FILE" \
    --issue "$1" \
    --context-file "$2" \
    --recovery-report-file "$3"
}

finalize_merge_recovery() {
  "$PYTHON_BIN" "$STATE_HELPER" finalize-merge-recovery \
    --state-file "$STATE_FILE" \
    --issue "$1" \
    --report-file "$2"
}

mark_merge_recovery_dispatch_failed() {
  "$PYTHON_BIN" "$STATE_HELPER" mark-merge-recovery-dispatch-failed \
    --state-file "$STATE_FILE" \
    --issue "$1" \
    --report-file "$2"
}

# Archive any stale sub-coordinator terminal markers (report.json, done, dispatcher
# state, dispatch.out) before a (re)dispatch so the fresh runner never reuses a
# poisoned report and no-ops into a phantom "recovery dispatcher failed". Preserves
# sub-state.json / workers/ so recovery dispatches still resume from saved artifacts.
quarantine_sub_coord_markers() {
  local issue="$1" issue_dir="$2" archived
  archived="$("$PYTHON_BIN" "$STATE_HELPER" quarantine-sub-coord-markers --issue-dir "$issue_dir" 2>/dev/null || true)"
  if [ -n "$archived" ]; then
    write_status "STATUS|type=sub-coord-markers-quarantined|issue=${issue}|archive_dir=${archived}"
  fi
}

update_github_issue() {
  local line output
  if ! output="$(
    "$PYTHON_BIN" "$GITHUB_UPDATE_HELPER" update \
      --state-file "$STATE_FILE" \
      --run-root "$RUN_ROOT" \
      --issue "$1" \
      --outcome "$2" \
      --report-file "$3"
)"; then
    fail "GitHub update helper failed for issue $1"
  fi
  while IFS= read -r line; do
    [ -n "$line" ] && write_status "$line"
  done <<< "$output"
}

print_dispatch_command() {
  local issue="$1"
  local context_file="$2"
  local issue_dir="$3"
  local log_file="$4"
  local done_file="$5"
  local report_file="$6"
  local state_file="$7"
  printf '%s --asset-root %s --role sub-coord --issue %s --agent %s --model %s --context-file %s --prompt-file %s --log-file %s --done-file %s --result-file %s --state-file %s --issue-dir %s --status-file %s --events-log %s --poll-seconds %s --timeout-seconds %s --detach\n' \
    "$DISPATCHER" "$ASSET_ROOT" "$issue" "$SUB_COORD_AGENT" "$SUB_COORD_MODEL" \
    "$context_file" "$PROMPT_FILE" "$log_file" "$done_file" "$report_file" "$state_file" "$issue_dir" \
    "$STATUS_FILE" "$EVENTS_LOG" "$POLL_SECONDS" "$TIMEOUT_SECONDS"
}

wait_for_dispatcher_pid() {
  local state_file="$1"
  "$PYTHON_BIN" - "$state_file" <<'PY'
import json
import sys
import time

path = sys.argv[1]
for _ in range(100):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        pid = data.get("dispatcher_pid")
        if pid:
            print(pid)
            raise SystemExit(0)
    except (OSError, json.JSONDecodeError):
        pass
    time.sleep(0.1)
raise SystemExit(1)
PY
}

launch_sub_coord_dispatcher() {
  local issue="$1"
  local context_file="$2"
  local log_file="$3"
  local done_file="$4"
  local report_file="$5"
  local issue_dir="$6"
  local state_file="$7"

  "$DISPATCHER" \
    --asset-root "$ASSET_ROOT" \
    --role sub-coord \
    --issue "$issue" \
    --agent "$SUB_COORD_AGENT" \
    --model "$SUB_COORD_MODEL" \
    --context-file "$context_file" \
    --prompt-file "$PROMPT_FILE" \
    --log-file "$log_file" \
    --done-file "$done_file" \
    --result-file "$report_file" \
    --state-file "$state_file" \
    --issue-dir "$issue_dir" \
    --status-file "$STATUS_FILE" \
    --events-log "$EVENTS_LOG" \
    --poll-seconds "$POLL_SECONDS" \
    --timeout-seconds "$TIMEOUT_SECONDS" \
    --detach >/dev/null
}

POOL_ISSUES=""

pool_add() {
  case " $POOL_ISSUES " in
    *" $1 "*) ;;
    *) POOL_ISSUES="${POOL_ISSUES} $1" ;;
  esac
}

pool_remove() {
  local next="" cur
  for cur in $POOL_ISSUES; do
    [ "$cur" = "$1" ] || next="${next} $cur"
  done
  POOL_ISSUES="$next"
}

pool_set() {
  local key="$1" issue="$2" value="$3" escaped
  escaped="$(printf '%q' "$value")"
  eval "_POOL_${key}_${issue}=${escaped}"
}

pool_get() {
  local key="$1" issue="$2"
  eval "printf '%s' \"\${_POOL_${key}_${issue}-}\""
}

fill_free_slots() {
  local reason="$1"
  local pool_size free_slots queued_issue next_batch
  set -- $POOL_ISSUES
  pool_size=$#
  free_slots=$((PARALLEL_JOBS - pool_size))
  [ "$free_slots" -gt 0 ] || return 0

  next_batch="$(ready_issues "$free_slots")"
  for queued_issue in $next_batch; do
    spawn_issue "$queued_issue"
    write_status "STATUS|type=pool-slot-filled|issue=${queued_issue}|freed_by=${reason}|pool_size=$(set -- $POOL_ISSUES; echo $#)"
  done
}

LAST_WAITING_CONTEXT_COUNT=""

emit_waiting_context_status() {
  local waiting_count
  waiting_count="$(ready_missing_context_count)"
  if [ "$waiting_count" != "0" ] && [ "$waiting_count" != "$LAST_WAITING_CONTEXT_COUNT" ]; then
    write_status "STATUS|type=pool-waiting-context|count=${waiting_count}|state_file=${STATE_FILE}"
  fi
  LAST_WAITING_CONTEXT_COUNT="$waiting_count"
}

# Surface concurrency-gate deferrals so a serialized pool is visible in the
# status bus instead of silently degrading to one issue at a time.
LAST_ADMISSION_DEFERRALS=""

emit_admission_deferrals() {
  local deferrals count
  deferrals="$("$PYTHON_BIN" "$STATE_HELPER" admission-deferrals --state-file "$STATE_FILE" 2>/dev/null || true)"
  if [ -n "$deferrals" ] && [ "$deferrals" != "$LAST_ADMISSION_DEFERRALS" ]; then
    count="$(printf '%s' "$deferrals" | awk -F',' '{print NF}')"
    write_status "STATUS|type=pool-admission-deferred|count=${count}|deferrals=${deferrals}"
  fi
  LAST_ADMISSION_DEFERRALS="$deferrals"
}

# Re-adopt in-flight issues from a previous pool supervisor (e.g. one killed by
# a bounded tool-call timeout). Live dispatchers keep running unsupervised; the
# monitor loop below re-attaches to them, and dead ones flow through the normal
# exit analysis on the first tick instead of producing a false pool-empty.
reattach_active_pool() {
  local line issue pid report_file reattached=0
  while IFS=$'\t' read -r issue pid report_file; do
    [ -n "$issue" ] || continue
    pool_add "$issue"
    pool_set PID "$issue" "${pid:-0}"
    pool_set REPORT "$issue" "${report_file:-$(issue_dir_for "$issue")/report.json}"
    reattached=$((reattached + 1))
    write_status "STATUS|type=sub-coord-reattach|issue=${issue}|pid=${pid:-0}|report_file=${report_file}"
  done <<EOF
$("$PYTHON_BIN" "$STATE_HELPER" active-pool-entries --state-file "$STATE_FILE")
EOF
  [ "$reattached" -eq 0 ] || write_status "STATUS|type=pool-reattached|count=${reattached}|state_file=${STATE_FILE}"
}

spawn_issue() {
  local issue="$1"
  local context_file issue_dir log_file done_file report_file state_file pid
  context_file="$(context_file_for "$issue")"
  [ -f "$context_file" ] || fail "context file missing for issue $issue: $context_file"
  issue_dir="$(issue_dir_for "$issue")"
  log_file="${issue_dir}/sub-coordinator.log"
  done_file="${issue_dir}/sub-coordinator.done"
  report_file="${issue_dir}/report.json"
  state_file="${issue_dir}/sub-coordinator.state.json"
  mkdir -p "$issue_dir"
  quarantine_sub_coord_markers "$issue" "$issue_dir"
  if ! launch_sub_coord_dispatcher "$issue" "$context_file" "$log_file" "$done_file" "$report_file" "$issue_dir" "$state_file"; then
    write_status "STATUS|type=sub-coord-dispatch-bootstrap-failed|issue=${issue}|state_file=${state_file}|report_file=${report_file}"
    return 1
  fi
  if ! pid="$(wait_for_dispatcher_pid "$state_file")"; then
    write_status "STATUS|type=sub-coord-dispatch-bootstrap-failed|issue=${issue}|reason=missing-dispatcher-pid|state_file=${state_file}|report_file=${report_file}"
    return 1
  fi
  pool_add "$issue"
  pool_set PID "$issue" "$pid"
  pool_set REPORT "$issue" "$report_file"
  mark_in_progress "$issue" "$pid" "$context_file" "$log_file" "$done_file" "$report_file" "$issue_dir" "$state_file"
  write_status "STATUS|type=sub-coord-spawn|issue=${issue}|pid=${pid}|agent=${SUB_COORD_AGENT}|model=${SUB_COORD_MODEL}|state_file=${state_file}|pool_size=$(set -- $POOL_ISSUES; echo $#)|parallel_jobs=${PARALLEL_JOBS}"
}

spawn_recovery_issue() {
  local issue="$1"
  local decision_json="$2"
  local issue_dir context_file log_file done_file report_file state_file pid attempt reason
  issue_dir="$(issue_dir_for "$issue")"
  report_file="${issue_dir}/report.json"
  attempt="$(decision_field "$decision_json" recovery_attempt)"
  reason="$(decision_field "$decision_json" reason)"
  context_file="${issue_dir}/sub-coordinator-recovery-${attempt}-context.md"
  log_file="${issue_dir}/sub-coordinator-recovery-${attempt}.log"
  done_file="${issue_dir}/sub-coordinator-recovery-${attempt}.done"
  state_file="${issue_dir}/sub-coordinator-recovery-${attempt}.state.json"
  mkdir -p "$issue_dir"
  quarantine_sub_coord_markers "$issue" "$issue_dir"
  write_sub_coord_recovery_context "$issue" "$context_file" "$attempt" "$reason"
  mark_sub_coord_recovery_started "$issue" "$attempt" "$reason" "$context_file"
  if ! launch_sub_coord_dispatcher "$issue" "$context_file" "$log_file" "$done_file" "$report_file" "$issue_dir" "$state_file"; then
    write_status "STATUS|type=sub-coord-recovery-dispatch-bootstrap-failed|issue=${issue}|attempt=${attempt}|state_file=${state_file}|report_file=${report_file}"
    mark_sub_coord_recovery_dispatch_failed "$issue" "$report_file"
    finalize_pool_issue "$issue" "$report_file"
    return 0
  fi
  if ! pid="$(wait_for_dispatcher_pid "$state_file")"; then
    write_status "STATUS|type=sub-coord-recovery-dispatch-bootstrap-failed|issue=${issue}|attempt=${attempt}|reason=missing-dispatcher-pid|state_file=${state_file}|report_file=${report_file}"
    mark_sub_coord_recovery_dispatch_failed "$issue" "$report_file"
    finalize_pool_issue "$issue" "$report_file"
    return 0
  fi
  pool_set PID "$issue" "$pid"
  pool_set REPORT "$issue" "$report_file"
  mark_in_progress "$issue" "$pid" "$context_file" "$log_file" "$done_file" "$report_file" "$issue_dir" "$state_file"
  write_status "STATUS|type=sub-coord-recovery-spawn|issue=${issue}|attempt=${attempt}|pid=${pid}|agent=${SUB_COORD_AGENT}|model=${SUB_COORD_MODEL}|state_file=${state_file}|reason=${reason}"
}

finalize_pool_issue() {
  local issue="$1"
  local report_file="$2"
  local outcome
  outcome="$(finalize_issue "$issue" "$report_file")"
  pool_remove "$issue"
  write_status "STATUS|type=sub-coord-complete|issue=${issue}|outcome=${outcome}|report_file=${report_file}"
  if [ "$outcome" = "merge_recovery" ]; then
    run_merge_recovery "$issue"
  else
    update_github_issue "$issue" "$outcome" "$report_file"
  fi
  fill_free_slots "$issue"
}

handle_sub_coord_exit() {
  local issue="$1"
  local report_file="$2"
  local decision_json action reason worker_role worker_state worker_state_file
  decision_json="$(analyze_sub_coord_failure "$issue" "$report_file")"
  action="$(decision_field "$decision_json" action)"
  reason="$(decision_field "$decision_json" reason)"
  case "$action" in
    wait_worker)
      worker_role="$(decision_field "$decision_json" worker_role)"
      worker_state="$(decision_field "$decision_json" worker_state)"
      worker_state_file="$(decision_field "$decision_json" worker_state_file)"
      write_status "STATUS|type=sub-coord-recovery-wait|issue=${issue}|role=${worker_role}|worker_state=${worker_state}|state_file=${worker_state_file}|reason=${reason}"
      return 0
      ;;
    spawn_recovery)
      spawn_recovery_issue "$issue" "$decision_json"
      return 0
      ;;
    finalize)
      finalize_pool_issue "$issue" "$report_file"
      return 0
      ;;
    block|*)
      if [ "$action" != "block" ]; then
        write_status "STATUS|type=sub-coord-recovery-analysis-failed|issue=${issue}|action=${action:-unknown}|reason=${reason:-unknown}"
      fi
      mark_sub_coord_recovery_dispatch_failed "$issue" "$report_file"
      finalize_pool_issue "$issue" "$report_file"
      return 0
      ;;
  esac
}

run_merge_recovery() {
  local issue="$1"
  local issue_dir context_file log_file done_file report_file recovery_status
  issue_dir="$(issue_dir_for "$issue")"
  context_file="${issue_dir}/merge-recovery-context.md"
  log_file="${issue_dir}/merge-recovery.log"
  done_file="${issue_dir}/merge-recovery.done"
  report_file="${issue_dir}/merge-recovery-report.json"
  mkdir -p "$issue_dir"
  write_merge_recovery_context "$issue" "$context_file" "$report_file"
  write_status "STATUS|type=merge-recovery|issue=${issue}|report_file=${report_file}|state=started"
  if "$DISPATCHER" \
    --asset-root "$ASSET_ROOT" \
    --role merge-recovery \
    --issue "$issue" \
    --agent "$SUB_COORD_AGENT" \
    --model "$SUB_COORD_MODEL" \
    --context-file "$context_file" \
    --prompt-file "$MERGE_RECOVERY_PROMPT_FILE" \
    --log-file "$log_file" \
    --done-file "$done_file" \
    --result-file "$report_file" \
    --issue-dir "$issue_dir" \
    --status-file "$STATUS_FILE" \
    --events-log "$EVENTS_LOG" \
    --poll-seconds "$POLL_SECONDS" \
    --timeout-seconds "$TIMEOUT_SECONDS" >/dev/null; then
    recovery_status="$(finalize_merge_recovery "$issue" "$report_file")"
  else
    recovery_status="$(finalize_merge_recovery "$issue" "$report_file")"
    if [ "$recovery_status" = "completed" ]; then
      recovery_status="blocked"
      mark_merge_recovery_dispatch_failed "$issue" "$report_file"
    fi
  fi
  write_status "STATUS|type=merge-recovery|issue=${issue}|report_file=${report_file}|state=${recovery_status}"
  update_github_issue "$issue" "$recovery_status" "$report_file"
}

READY_INITIAL="$(ready_issues "$PARALLEL_JOBS")"
ready_count=0
for _issue in $READY_INITIAL; do ready_count=$((ready_count + 1)); done
write_status "STATUS|type=pool-ready|parallel_jobs=${PARALLEL_JOBS}|ready=${ready_count}|state_file=${STATE_FILE}"

if [ "$VALIDATE_ONLY" = 1 ]; then
  exit 0
fi

if [ "$DRY_RUN" = 1 ]; then
  for issue in $READY_INITIAL; do
    context_file="$(context_file_for "$issue")"
    issue_dir="$(issue_dir_for "$issue")"
    print_dispatch_command \
      "$issue" \
      "$context_file" \
      "$issue_dir" \
      "${issue_dir}/sub-coordinator.log" \
      "${issue_dir}/sub-coordinator.done" \
      "${issue_dir}/report.json" \
      "${issue_dir}/sub-coordinator.state.json"
  done
  exit 0
fi

reattach_active_pool
fill_free_slots "startup"
emit_admission_deferrals
emit_run_board

while [ -n "$POOL_ISSUES" ]; do
  sleep "$POLL_SECONDS"
  CURRENT_POOL="$POOL_ISSUES"
  for issue in $CURRENT_POOL; do
    pid="$(pool_get PID "$issue")"
    if [ -n "$pid" ] && [ "$pid" != "0" ] && kill -0 "$pid" 2>/dev/null; then
      continue
    fi
    wait "$pid" 2>/dev/null || true
    report_file="$(pool_get REPORT "$issue")"
    handle_sub_coord_exit "$issue" "$report_file"
  done
  fill_free_slots "tick"
  emit_waiting_context_status
  emit_admission_deferrals
  emit_run_board
done

write_status "STATUS|type=pool-empty|state_file=${STATE_FILE}"
