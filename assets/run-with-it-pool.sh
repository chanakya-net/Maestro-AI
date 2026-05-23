#!/usr/bin/env bash

set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd -- "${SCRIPT_PATH%/*}" && pwd -P)"

ASSET_ROOT="${ASSETS_DEST:-}"
STATE_FILE="$(pwd -P)/.run-with-it/main-state.json"
PARALLEL_JOBS=""
SUB_COORD_AGENT="${SUB_COORD_AGENT:-codex}"
SUB_COORD_MODEL="${SUB_COORD_MODEL:-gpt-5.5}"
STATUS_FILE="${RUN_WITH_IT_STATUS_FILE:-$(pwd -P)/.run-with-it/status/current.txt}"
EVENTS_LOG="${RUN_WITH_IT_EVENTS_LOG:-$(pwd -P)/.run-with-it/status/events.log}"
MAIN_LOG="$(pwd -P)/.run-with-it/main/main.log"
POLL_SECONDS="${STATUS_POLL_SECONDS:-10}"
TIMEOUT_SECONDS="${SUB_COORD_TIMEOUT_SECONDS:-3600}"
DRY_RUN=0
VALIDATE_ONLY=0

fail() {
  echo "run-with-it-pool.sh: $1" >&2
  exit 2
}

usage() {
  cat <<'EOF'
Usage:
  run-with-it-pool.sh --state-file .run-with-it/main-state.json \
    --parallel-jobs 4 --agent codex --model gpt-5.5

Modes:
  --dry-run        Print the initial dispatch commands without spawning.
  --validate-only Validate state and emit pool-ready status without spawning.
EOF
}

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
    --dry-run) DRY_RUN=1; shift ;;
    --validate-only) VALIDATE_ONLY=1; shift ;;
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

[ -x "$DISPATCHER" ] || fail "dispatcher not executable: $DISPATCHER"
[ -f "$PROMPT_FILE" ] || fail "sub-coordinator prompt not found: $PROMPT_FILE"
[ -f "$STATE_FILE" ] || fail "state file not found: $STATE_FILE"

if [ -z "$PARALLEL_JOBS" ]; then
  PARALLEL_JOBS="$(python3 - "$STATE_FILE" <<'PY'
import json, sys
state = json.load(open(sys.argv[1]))
print(state.get("execution_plan", {}).get("parallel_jobs", 4))
PY
)"
fi

mkdir -p "$(dirname "$MAIN_LOG")" "$(dirname "$STATUS_FILE")" "$(dirname "$EVENTS_LOG")"

write_status() {
  local line="$1"
  printf '%s\n' "$line"
  printf '%s\n' "$line" >> "$MAIN_LOG"
  printf '%s\n' "$line" > "$STATUS_FILE"
  printf '%s\n' "$line" >> "$EVENTS_LOG"
}

ready_issues() {
  python3 - "$STATE_FILE" "$1" <<'PY'
import json, sys
state = json.load(open(sys.argv[1]))
limit = int(sys.argv[2])
reg = state.get("issue_registry", {})
completed = {int(k) for k, v in reg.items() if v.get("status") == "completed"}
topo = state.get("execution_plan", {}).get("topo_order", [])
ready = []
for issue in topo:
    if len(ready) >= limit:
        break
    info = reg.get(str(issue), {})
    if info.get("status") != "pending":
        continue
    if all(int(dep) in completed for dep in info.get("deps", [])):
        context_file = info.get("context_file") or info.get("sub_coord_context_file")
        if context_file:
            ready.append(str(issue))
print(" ".join(ready))
PY
}

context_file_for() {
  python3 - "$STATE_FILE" "$1" <<'PY'
import json, sys
state = json.load(open(sys.argv[1]))
info = state.get("issue_registry", {}).get(str(sys.argv[2]), {})
print(info.get("context_file") or info.get("sub_coord_context_file") or "")
PY
}

mark_in_progress() {
  python3 - "$STATE_FILE" "$1" "$2" "$3" "$4" "$5" "$6" <<'PY'
import json, sys, time
path, issue, pid, context_file, log_file, done_file, report_file = sys.argv[1:]
state = json.load(open(path))
reg = state.setdefault("issue_registry", {})
entry = reg.setdefault(str(issue), {})
entry["status"] = "in_progress"
entry["context_file"] = context_file
entry["pid"] = int(pid)
entry["started_at"] = int(time.time())
entry["log_file"] = log_file
entry["done_file"] = done_file
entry["report_file"] = report_file
active = [str(x) for x in state.setdefault("active_pool_issues", [])]
if str(issue) not in active:
    active.append(str(issue))
state["active_pool_issues"] = active
json.dump(state, open(path, "w"), indent=2)
PY
}

finalize_issue() {
  python3 - "$STATE_FILE" "$1" "$2" <<'PY'
import json, os, sys
path, issue, report_file = sys.argv[1:]
outcome = "blocked"
report = {}
if os.path.exists(report_file):
    try:
        report = json.load(open(report_file))
        outcome = report.get("outcome", "blocked")
    except Exception:
        outcome = "blocked"
status = "merge_recovery" if outcome == "merge_failed" else outcome
state = json.load(open(path))
entry = state.setdefault("issue_registry", {}).setdefault(str(issue), {})
entry["status"] = status
if outcome == "merge_failed":
    entry["merge_recovery_report_file"] = report_file
    entry.setdefault("blocking_reasons", []).append("merge recovery required")
state["active_pool_issues"] = [x for x in state.get("active_pool_issues", []) if str(x) != str(issue)]
summary = {
    "issue": int(issue),
    "outcome": status,
    "files_modified_count": report.get("files_modified_count", 0),
    "lines_added": report.get("lines_added", 0),
    "lines_deleted": report.get("lines_deleted", 0),
    "review_cycles": report.get("review_cycles", 0),
    "commit_sha": report.get("commit_sha"),
}
if status != "merge_recovery":
    state.setdefault("completed_summaries", []).append(summary)
else:
    state.setdefault("merge_recovery_summaries", []).append(summary)
ledger = f"STATUS|type=ledger|task={issue}|outcome={status}|report={report_file}"
state.setdefault("ledger_rows", []).append(ledger)
json.dump(state, open(path, "w"), indent=2)
print(status)
PY
}

READY_INITIAL="$(ready_issues "$PARALLEL_JOBS")"
ready_count=0
for _issue in $READY_INITIAL; do ready_count=$((ready_count + 1)); done
write_status "STATUS|type=pool-ready|parallel_jobs=${PARALLEL_JOBS}|ready=${ready_count}|state_file=${STATE_FILE}"

if [ "$VALIDATE_ONLY" = 1 ]; then
  exit 0
fi

print_dispatch_command() {
  local issue="$1"
  local context_file="$2"
  local log_file="$3"
  local done_file="$4"
  local report_file="$5"
  printf '%s --asset-root %s --role sub-coord --issue %s --agent %s --model %s --context-file %s --prompt-file %s --log-file %s --done-file %s --result-file %s --status-file %s --events-log %s --poll-seconds %s --timeout-seconds %s\n' \
    "$DISPATCHER" "$ASSET_ROOT" "$issue" "$SUB_COORD_AGENT" "$SUB_COORD_MODEL" \
    "$context_file" "$PROMPT_FILE" "$log_file" "$done_file" "$report_file" \
    "$STATUS_FILE" "$EVENTS_LOG" "$POLL_SECONDS" "$TIMEOUT_SECONDS"
}

if [ "$DRY_RUN" = 1 ]; then
  for issue in $READY_INITIAL; do
    context_file="$(context_file_for "$issue")"
    print_dispatch_command \
      "$issue" \
      "$context_file" \
      "$(pwd -P)/.run-with-it/sub/sub-${issue}.log" \
      "$(pwd -P)/.run-with-it/done/issue-${issue}-sub-coord.done" \
      "$(pwd -P)/.run-with-it/reports/sub-${issue}-report.json"
  done
  exit 0
fi

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

spawn_issue() {
  local issue="$1"
  local context_file log_file done_file report_file pid
  context_file="$(context_file_for "$issue")"
  [ -f "$context_file" ] || fail "context file missing for issue $issue: $context_file"
  log_file="$(pwd -P)/.run-with-it/sub/sub-${issue}.log"
  done_file="$(pwd -P)/.run-with-it/done/issue-${issue}-sub-coord.done"
  report_file="$(pwd -P)/.run-with-it/reports/sub-${issue}-report.json"
  mkdir -p "$(dirname "$log_file")" "$(dirname "$done_file")" "$(dirname "$report_file")"
  nohup "$DISPATCHER" \
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
    --status-file "$STATUS_FILE" \
    --events-log "$EVENTS_LOG" \
    --poll-seconds "$POLL_SECONDS" \
    --timeout-seconds "$TIMEOUT_SECONDS" \
    >/dev/null 2>&1 < /dev/null &
  pid="$!"
  pool_add "$issue"
  pool_set PID "$issue" "$pid"
  pool_set REPORT "$issue" "$report_file"
  mark_in_progress "$issue" "$pid" "$context_file" "$log_file" "$done_file" "$report_file"
  write_status "STATUS|type=sub-coord-spawn|issue=${issue}|pid=${pid}|agent=${SUB_COORD_AGENT}|model=${SUB_COORD_MODEL}|pool_size=$(set -- $POOL_ISSUES; echo $#)|parallel_jobs=${PARALLEL_JOBS}"
}

for issue in $READY_INITIAL; do
  spawn_issue "$issue"
done

while [ -n "$POOL_ISSUES" ]; do
  sleep "$POLL_SECONDS"
  CURRENT_POOL="$POOL_ISSUES"
  for issue in $CURRENT_POOL; do
    pid="$(pool_get PID "$issue")"
    if kill -0 "$pid" 2>/dev/null; then
      continue
    fi
    wait "$pid" 2>/dev/null || true
    report_file="$(pool_get REPORT "$issue")"
    outcome="$(finalize_issue "$issue" "$report_file")"
    pool_remove "$issue"
    write_status "STATUS|type=sub-coord-complete|issue=${issue}|outcome=${outcome}|report_file=${report_file}"
    next_issue="$(ready_issues 1)"
    if [ -n "$next_issue" ]; then
      spawn_issue "$next_issue"
      write_status "STATUS|type=pool-slot-filled|issue=${next_issue}|freed_by=${issue}|pool_size=$(set -- $POOL_ISSUES; echo $#)"
    fi
  done
done

write_status "STATUS|type=pool-empty|state_file=${STATE_FILE}"
