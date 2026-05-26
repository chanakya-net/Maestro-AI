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
MERGE_RECOVERY_PROMPT_FILE="${ASSET_ROOT}/merge-recovery-prompt.md"

[ -x "$DISPATCHER" ] || fail "dispatcher not executable: $DISPATCHER"
[ -f "$PROMPT_FILE" ] || fail "sub-coordinator prompt not found: $PROMPT_FILE"
[ -f "$MERGE_RECOVERY_PROMPT_FILE" ] || fail "merge recovery prompt not found: $MERGE_RECOVERY_PROMPT_FILE"
[ -f "$STATE_FILE" ] || fail "state file not found: $STATE_FILE"
RUN_ROOT="$(cd "$(dirname "$STATE_FILE")/.." && pwd -P)"

JSON_PARSER=""
if command -v jq >/dev/null 2>&1; then
  JSON_PARSER="jq"
elif command -v python3 >/dev/null 2>&1; then
  JSON_PARSER="python3"
else
  fail "no JSON parser available; install jq or python3"
fi

if [ -z "$PARALLEL_JOBS" ]; then
  if [ "$JSON_PARSER" = "jq" ]; then
    PARALLEL_JOBS="$(jq -r '.execution_plan.parallel_jobs // 4' "$STATE_FILE")"
  else
    PARALLEL_JOBS="$(python3 - "$STATE_FILE" <<'PY'
import json, sys
state = json.load(open(sys.argv[1]))
print(state.get("execution_plan", {}).get("parallel_jobs", 4))
PY
)"
  fi
fi

mkdir -p "$(dirname "$MAIN_LOG")" "$(dirname "$STATUS_FILE")" "$(dirname "$EVENTS_LOG")"

write_status() {
  local line="$1"
  printf '%s\n' "$line"
  printf '%s\n' "$line" >> "$MAIN_LOG"
  printf '%s\n' "$line" > "$STATUS_FILE"
  printf '%s\n' "$line" >> "$EVENTS_LOG"
}

set_github_update_state() {
  local issue="$1"
  local status="$2"
  local detail="$3"
  if [ "$JSON_PARSER" = "jq" ]; then
    local tmp_file
    tmp_file="$(mktemp -t run-with-it-state.XXXXXX)"
    jq \
      --arg issue "$issue" \
      --arg status "$status" \
      --arg detail "$detail" \
      --arg updated_at "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
      '
        .issue_registry = (.issue_registry // {})
        | .issue_registry[$issue] = ((.issue_registry[$issue] // {}) + {
            github_update_status: $status,
            github_update_detail: $detail,
            github_updated_at: $updated_at
          })
      ' "$STATE_FILE" > "$tmp_file"
    mv "$tmp_file" "$STATE_FILE"
  else
    python3 - "$STATE_FILE" "$issue" "$status" "$detail" <<'PY'
import datetime, json, sys
path, issue, status, detail = sys.argv[1:]
state = json.load(open(path))
entry = state.setdefault("issue_registry", {}).setdefault(str(issue), {})
entry["github_update_status"] = status
entry["github_update_detail"] = detail
entry["github_updated_at"] = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
json.dump(state, open(path, "w"), indent=2)
PY
  fi
}

write_terminal_comment() {
  python3 - "$1" "$2" > "$3" <<'PY'
import json, sys

report_file, fallback_outcome = sys.argv[1:3]
try:
    report = json.load(open(report_file))
except Exception:
    report = {}

outcome = report.get("outcome") or fallback_outcome or "blocked"
summary = report.get("summary") or "No summary provided."
verification = report.get("verification") or {}
if isinstance(verification, dict):
    commands = verification.get("commands_run") or []
    evidence = verification.get("evidence") or ""
    passed = verification.get("passed")
    state = "passed" if passed is True else "failed" if passed is False else "unknown"
    command_text = ", ".join(str(x) for x in commands) if commands else "unknown"
    verification_text = f"State: {state}\nCommands: {command_text}\nEvidence: {evidence or 'unknown'}"
else:
    verification_text = str(verification) if verification else "unknown"

tokens = report.get("token_usage") or {}
def token_sum(kind):
    if not isinstance(tokens, dict):
        return None
    total = 0
    found = False
    for key, value in tokens.items():
        key_l = str(key).lower()
        if kind == "input" and "input" not in key_l:
            continue
        if kind == "output" and "output" not in key_l:
            continue
        if kind == "cache" and "cache" not in key_l:
            continue
        if isinstance(value, (int, float)):
            total += int(value)
            found = True
    return total if found else None

def fmt(value):
    return str(value) if value is not None else "unknown"

review = report.get("review_summary") or {}
cycles = review.get("cycles_used")
final = review.get("final_verdict") or "unknown"
reviewer = review.get("reviewer_model") or "unknown"
if cycles is None:
    review_line = f"Review: unknown, final verdict: {final}, reviewer model: {reviewer}"
elif int(cycles) <= 1 and final == "approve":
    review_line = f"Review: approve (1 cycle), final verdict: {final}, reviewer model: {reviewer}"
else:
    review_line = f"Review: revise ({cycles} cycles), final verdict: {final}, reviewer model: {reviewer}"

blocking = report.get("blocking_reasons") or []

print("## Status")
print(outcome)
print()
print("## Summary")
print(summary)
print()
print("## Verification")
print(verification_text)
print()
print("## Token Usage")
print(f"- Input tokens: {fmt(token_sum('input'))}")
print(f"- Output tokens: {fmt(token_sum('output'))}")
print(f"- Cache hit tokens: {fmt(token_sum('cache'))}")
print()
print("## Notes")
print(review_line)
if report.get("commit_sha"):
    print(f"Commit: {report['commit_sha']}")
merge = report.get("merge") or {}
if isinstance(merge, dict) and merge.get("merge_sha"):
    print(f"Merge: {merge['merge_sha']}")
if blocking:
    print()
    print("## Blocking Reasons")
    for reason in blocking:
        print(f"- {reason}")
PY
}

update_github_issue() {
  local issue="$1"
  local outcome="$2"
  local report_file="$3"
  local close_issue="false"
  local comment_file

  case "$outcome" in
    completed) close_issue="true" ;;
    blocked|failed-review|failed-merge) close_issue="false" ;;
    *) return 0 ;;
  esac

  if [ "${RUN_WITH_IT_GITHUB_UPDATES:-1}" = "0" ]; then
    set_github_update_state "$issue" "skipped" "disabled"
    write_status "STATUS|type=github-update|issue=${issue}|outcome=${outcome}|action=skipped|reason=disabled"
    return 0
  fi
  if ! command -v gh >/dev/null 2>&1; then
    set_github_update_state "$issue" "skipped" "gh-not-found"
    write_status "STATUS|type=github-update|issue=${issue}|outcome=${outcome}|action=skipped|reason=gh-not-found"
    return 0
  fi
  if ! git -C "$RUN_ROOT" remote -v 2>/dev/null | grep -qi 'github.com'; then
    set_github_update_state "$issue" "skipped" "no-github-remote"
    write_status "STATUS|type=github-update|issue=${issue}|outcome=${outcome}|action=skipped|reason=no-github-remote"
    return 0
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    set_github_update_state "$issue" "skipped" "python3-not-found"
    write_status "STATUS|type=github-update|issue=${issue}|outcome=${outcome}|action=skipped|reason=python3-not-found"
    return 0
  fi

  comment_file="$(mktemp -t run-with-it-comment.XXXXXX.md)"
  if ! write_terminal_comment "$report_file" "$outcome" "$comment_file"; then
    rm -f "$comment_file"
    set_github_update_state "$issue" "failed" "comment-render-failed"
    write_status "STATUS|type=github-update|issue=${issue}|outcome=${outcome}|action=failed|reason=comment-render-failed"
    return 0
  fi

  if ! (cd "$RUN_ROOT" && gh issue comment "$issue" --body-file "$comment_file" >/dev/null); then
    rm -f "$comment_file"
    set_github_update_state "$issue" "failed" "comment-failed"
    write_status "STATUS|type=github-update|issue=${issue}|outcome=${outcome}|action=failed|reason=comment-failed"
    return 0
  fi
  rm -f "$comment_file"

  if [ "$close_issue" = "true" ]; then
    if ! (cd "$RUN_ROOT" && gh issue close "$issue" >/dev/null); then
      set_github_update_state "$issue" "failed" "close-failed"
      write_status "STATUS|type=github-update|issue=${issue}|outcome=${outcome}|action=commented|closed=false|reason=close-failed"
      return 0
    fi
  fi

  set_github_update_state "$issue" "updated" "commented;closed=${close_issue}"
  write_status "STATUS|type=github-update|issue=${issue}|outcome=${outcome}|action=commented|closed=${close_issue}"
}

ready_issues() {
  if [ "$JSON_PARSER" = "jq" ]; then
    jq -r --argjson limit "$1" '
      ([.issue_registry // {} | to_entries[] | select(.value.status == "completed") | .key | tonumber]) as $completed
      | [
          (.execution_plan.topo_order // [])[] as $issue
          | (.issue_registry[($issue | tostring)] // {}) as $info
          | select($info.status == "pending")
          | select(all(($info.deps // [])[]; . as $dep | $completed | index($dep | tonumber)))
          | select((($info.context_file // $info.sub_coord_context_file // "") | length) > 0)
          | ($issue | tostring)
        ][0:$limit]
      | join(" ")
    ' "$STATE_FILE"
  else
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
  fi
}

ready_missing_context_count() {
  if [ "$JSON_PARSER" = "jq" ]; then
    jq -r '
      ([.issue_registry // {} | to_entries[] | select(.value.status == "completed") | .key | tonumber]) as $completed
      | [
          (.execution_plan.topo_order // [])[] as $issue
          | (.issue_registry[($issue | tostring)] // {}) as $info
          | select($info.status == "pending")
          | select(all(($info.deps // [])[]; . as $dep | $completed | index($dep | tonumber)))
          | select((($info.context_file // $info.sub_coord_context_file // "") | length) == 0)
        ]
      | length
    ' "$STATE_FILE"
  else
    python3 - "$STATE_FILE" <<'PY'
import json, sys
state = json.load(open(sys.argv[1]))
reg = state.get("issue_registry", {})
completed = {int(k) for k, v in reg.items() if v.get("status") == "completed"}
topo = state.get("execution_plan", {}).get("topo_order", [])
count = 0
for issue in topo:
    info = reg.get(str(issue), {})
    if info.get("status") != "pending":
        continue
    if not all(int(dep) in completed for dep in info.get("deps", [])):
        continue
    context_file = info.get("context_file") or info.get("sub_coord_context_file") or ""
    if not context_file:
        count += 1
print(count)
PY
  fi
}

context_file_for() {
  if [ "$JSON_PARSER" = "jq" ]; then
    jq -r --arg issue "$1" '.issue_registry[$issue].context_file // .issue_registry[$issue].sub_coord_context_file // ""' "$STATE_FILE"
  else
    python3 - "$STATE_FILE" "$1" <<'PY'
import json, sys
state = json.load(open(sys.argv[1]))
info = state.get("issue_registry", {}).get(str(sys.argv[2]), {})
print(info.get("context_file") or info.get("sub_coord_context_file") or "")
PY
  fi
}

issue_dir_for() {
  printf '%s/.run-with-it/issues/%s\n' "$RUN_ROOT" "$1"
}

mark_in_progress() {
  if [ "$JSON_PARSER" = "jq" ]; then
    local tmp_file
    tmp_file="$(mktemp -t run-with-it-state.XXXXXX)"
    jq \
      --arg issue "$1" \
      --argjson pid "$2" \
      --argjson started_at "$(date +%s)" \
      --arg context_file "$3" \
      --arg log_file "$4" \
      --arg done_file "$5" \
      --arg report_file "$6" \
      --arg issue_dir "$7" \
      '
        .issue_registry = (.issue_registry // {})
        | .issue_registry[$issue] = ((.issue_registry[$issue] // {}) + {
            status: "in_progress",
            context_file: $context_file,
            issue_dir: $issue_dir,
            pid: $pid,
            started_at: $started_at,
            log_file: $log_file,
            done_file: $done_file,
            report_file: $report_file
          })
        | .active_pool_issues = (((.active_pool_issues // []) | map(tostring)) + [$issue] | unique)
      ' "$STATE_FILE" > "$tmp_file"
    mv "$tmp_file" "$STATE_FILE"
  else
    python3 - "$STATE_FILE" "$1" "$2" "$3" "$4" "$5" "$6" "$7" <<'PY'
import json, sys, time
path, issue, pid, context_file, log_file, done_file, report_file, issue_dir = sys.argv[1:]
state = json.load(open(path))
reg = state.setdefault("issue_registry", {})
entry = reg.setdefault(str(issue), {})
entry["status"] = "in_progress"
entry["context_file"] = context_file
entry["issue_dir"] = issue_dir
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
  fi
}

finalize_issue() {
  if [ "$JSON_PARSER" = "jq" ]; then
    local report_json tmp_file status
    report_json="$(mktemp -t run-with-it-report.XXXXXX)"
    tmp_file="$(mktemp -t run-with-it-state.XXXXXX)"
    if ! jq empty "$2" >/dev/null 2>&1; then
      printf '{}\n' > "$report_json"
    else
      cp "$2" "$report_json"
    fi
    status="$(jq -r '(.outcome // "blocked") as $outcome | if $outcome == "merge_failed" then "merge_recovery" else $outcome end' "$report_json")"
    jq \
      --arg issue "$1" \
      --arg report_file "$2" \
      --slurpfile report "$report_json" \
      '
        ($report[0] // {}) as $r
        | ($r.outcome // "blocked") as $outcome
        | (if $outcome == "merge_failed" then "merge_recovery" else $outcome end) as $status
        | .issue_registry = (.issue_registry // {})
        | .issue_registry[$issue] = ((.issue_registry[$issue] // {}) + {status: $status})
        | if $outcome == "merge_failed" then
            .issue_registry[$issue].failed_merge_report_file = $report_file
            | .issue_registry[$issue].blocking_reasons = (((.issue_registry[$issue].blocking_reasons // []) + ["merge recovery required"]) | unique)
          else . end
        | .active_pool_issues = ((.active_pool_issues // []) | map(tostring) | map(select(. != $issue)))
        | {
            issue: ($issue | tonumber),
            outcome: $status,
            files_modified_count: ($r.files_modified_count // 0),
            lines_added: ($r.lines_added // 0),
            lines_deleted: ($r.lines_deleted // 0),
            review_cycles: ($r.review_cycles // 0),
            commit_sha: ($r.commit_sha // null)
          } as $summary
        | if $status != "merge_recovery" then
            .completed_summaries = ((.completed_summaries // []) + [$summary])
          else
            .merge_recovery_summaries = ((.merge_recovery_summaries // []) + [$summary])
          end
        | .ledger_rows = ((.ledger_rows // []) + ["STATUS|type=ledger|task=\($issue)|outcome=\($status)|report=\($report_file)"])
      ' "$STATE_FILE" > "$tmp_file"
    mv "$tmp_file" "$STATE_FILE"
    rm -f "$report_json"
    printf '%s\n' "$status"
  else
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
    entry["failed_merge_report_file"] = report_file
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
  fi
}

write_merge_recovery_context() {
  if [ "$JSON_PARSER" = "jq" ]; then
    {
      printf 'You are receiving merge recovery task data only.\n'
      printf 'Resolve only the failed merge for this issue. Do not select new issues, close GitHub issues, create a final PR, or modify main-state.json.\n\n'
      printf 'MERGE_RECOVERY_REPORT_FILE=%s\n' "$3"
      printf 'RUN_WITH_IT_RESULT_FILE=%s\n' "$3"
      printf 'OUTCOME=completed\n\n'
      printf 'MERGE_RECOVERY_CONTEXT_JSON:\n'
      jq --arg issue "$1" '
        (.issue_registry[$issue] // {}) as $entry
        | {
            issue: {
              number: ($issue | tonumber),
              title: ($entry.title // ""),
              deps: ($entry.deps // []),
              issue_branch: ($entry.issue_branch // null),
              worktree_path: ($entry.worktree_path // null)
            },
            run_branch: (.run_branch // {}),
            failed_merge_report_file: ($entry.failed_merge_report_file // $entry.report_file // null),
            failed_merge_summary: {
              blocking_reasons: ($entry.blocking_reasons // []),
              dependency_proof: ($entry.dependency_proof // null)
            },
            completed_summaries: (.completed_summaries // [])
          }
      ' "$STATE_FILE"
    } > "$2"
  else
    python3 - "$STATE_FILE" "$1" "$2" "$3" <<'PY'
import json, sys
state_file, issue, context_file, recovery_report_file = sys.argv[1:]
state = json.load(open(state_file))
entry = state.get("issue_registry", {}).get(str(issue), {})
completed = state.get("completed_summaries", [])
payload = {
    "issue": {
        "number": int(issue),
        "title": entry.get("title", ""),
        "deps": entry.get("deps", []),
        "issue_branch": entry.get("issue_branch"),
        "worktree_path": entry.get("worktree_path"),
    },
    "run_branch": state.get("run_branch", {}),
    "failed_merge_report_file": entry.get("failed_merge_report_file") or entry.get("report_file"),
    "failed_merge_summary": {
        "blocking_reasons": entry.get("blocking_reasons", []),
        "dependency_proof": entry.get("dependency_proof"),
    },
    "completed_summaries": completed,
}
with open(context_file, "w", encoding="utf-8") as handle:
    handle.write("You are receiving merge recovery task data only.\n")
    handle.write("Resolve only the failed merge for this issue. Do not select new issues, close GitHub issues, create a final PR, or modify main-state.json.\n\n")
    handle.write(f"MERGE_RECOVERY_REPORT_FILE={recovery_report_file}\n")
    handle.write(f"RUN_WITH_IT_RESULT_FILE={recovery_report_file}\n")
    handle.write("OUTCOME=completed\n\n")
    handle.write("MERGE_RECOVERY_CONTEXT_JSON:\n")
    json.dump(payload, handle, indent=2)
    handle.write("\n")
PY
  fi
}

finalize_merge_recovery() {
  if [ "$JSON_PARSER" = "jq" ]; then
    local report_json tmp_file status
    report_json="$(mktemp -t run-with-it-recovery.XXXXXX)"
    tmp_file="$(mktemp -t run-with-it-state.XXXXXX)"
    if ! jq empty "$2" >/dev/null 2>&1; then
      printf '{}\n' > "$report_json"
    else
      cp "$2" "$report_json"
    fi
    status="$(jq -r '(.outcome // "blocked") as $outcome | if $outcome == "completed" then "completed" elif ($outcome == "failed-merge" or $outcome == "blocked") then $outcome else "blocked" end' "$report_json")"
    jq \
      --arg issue "$1" \
      --arg report_file "$2" \
      --slurpfile report "$report_json" \
      '
        ($report[0] // {}) as $r
        | ($r.outcome // "blocked") as $outcome
        | (if $outcome == "completed" then "completed" elif ($outcome == "failed-merge" or $outcome == "blocked") then $outcome else "blocked" end) as $status
        | .issue_registry = (.issue_registry // {})
        | .issue_registry[$issue] = ((.issue_registry[$issue] // {}) + {
            status: $status,
            merge_recovery_report_file: $report_file
          })
        | if $status == "completed" then
            .issue_registry[$issue].blocking_reasons = ((.issue_registry[$issue].blocking_reasons // []) | map(select(. != "merge recovery required")))
            | .issue_registry[$issue].commit_sha = ($r.merge_sha // $r.commit_sha // null)
          else
            .issue_registry[$issue].blocking_reasons = (((.issue_registry[$issue].blocking_reasons // []) + ($r.blocking_reasons // [])) | unique)
          end
        | ($r.files_modified // []) as $files
        | {
            issue: ($issue | tonumber),
            outcome: $status,
            files_modified_count: ($r.files_modified_count // ($files | length)),
            lines_added: ($r.lines_added // ([$files[]? | select(type == "object") | (.lines_added // 0)] | add // 0)),
            lines_deleted: ($r.lines_deleted // ([$files[]? | select(type == "object") | (.lines_deleted // 0)] | add // 0)),
            review_cycles: ($r.review_cycles // 0),
            commit_sha: ($r.merge_sha // $r.commit_sha // null)
          } as $summary
        | if $status == "completed" then
            .completed_summaries = ((.completed_summaries // []) + [$summary])
          else
            .merge_recovery_summaries = ((.merge_recovery_summaries // []) + [$summary])
          end
        | .ledger_rows = ((.ledger_rows // []) + ["STATUS|type=ledger|task=\($issue)|outcome=\($status)|report=\($report_file)|role=merge-recovery"])
      ' "$STATE_FILE" > "$tmp_file"
    mv "$tmp_file" "$STATE_FILE"
    rm -f "$report_json"
    printf '%s\n' "$status"
  else
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

status = "completed" if outcome == "completed" else outcome
if status not in {"completed", "failed-merge", "blocked"}:
    status = "blocked"

state = json.load(open(path))
entry = state.setdefault("issue_registry", {}).setdefault(str(issue), {})
entry["status"] = status
entry["merge_recovery_report_file"] = report_file
if status == "completed":
    entry["blocking_reasons"] = [
        reason for reason in entry.get("blocking_reasons", [])
        if reason != "merge recovery required"
    ]
    entry["commit_sha"] = report.get("merge_sha") or report.get("commit_sha")
else:
    entry.setdefault("blocking_reasons", []).extend(report.get("blocking_reasons", []))

files = report.get("files_modified", [])
summary = {
    "issue": int(issue),
    "outcome": status,
    "files_modified_count": report.get("files_modified_count", len(files)),
    "lines_added": report.get("lines_added", sum(item.get("lines_added", 0) for item in files if isinstance(item, dict))),
    "lines_deleted": report.get("lines_deleted", sum(item.get("lines_deleted", 0) for item in files if isinstance(item, dict))),
    "review_cycles": report.get("review_cycles", 0),
    "commit_sha": report.get("merge_sha") or report.get("commit_sha"),
}
if status == "completed":
    state.setdefault("completed_summaries", []).append(summary)
else:
    state.setdefault("merge_recovery_summaries", []).append(summary)
state.setdefault("ledger_rows", []).append(
    f"STATUS|type=ledger|task={issue}|outcome={status}|report={report_file}|role=merge-recovery"
)
json.dump(state, open(path, "w"), indent=2)
print(status)
PY
  fi
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
  local issue_dir="$3"
  local log_file="$4"
  local done_file="$5"
  local report_file="$6"
  printf '%s --asset-root %s --role sub-coord --issue %s --agent %s --model %s --context-file %s --prompt-file %s --log-file %s --done-file %s --result-file %s --issue-dir %s --status-file %s --events-log %s --poll-seconds %s --timeout-seconds %s\n' \
    "$DISPATCHER" "$ASSET_ROOT" "$issue" "$SUB_COORD_AGENT" "$SUB_COORD_MODEL" \
    "$context_file" "$PROMPT_FILE" "$log_file" "$done_file" "$report_file" "$issue_dir" \
    "$STATUS_FILE" "$EVENTS_LOG" "$POLL_SECONDS" "$TIMEOUT_SECONDS"
}

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
      "${issue_dir}/report.json"
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

spawn_issue() {
  local issue="$1"
  local context_file issue_dir log_file done_file report_file pid
  context_file="$(context_file_for "$issue")"
  [ -f "$context_file" ] || fail "context file missing for issue $issue: $context_file"
  issue_dir="$(issue_dir_for "$issue")"
  log_file="${issue_dir}/sub-coordinator.log"
  done_file="${issue_dir}/sub-coordinator.done"
  report_file="${issue_dir}/report.json"
  mkdir -p "$issue_dir"
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
    --issue-dir "$issue_dir" \
    --status-file "$STATUS_FILE" \
    --events-log "$EVENTS_LOG" \
    --poll-seconds "$POLL_SECONDS" \
    --timeout-seconds "$TIMEOUT_SECONDS" \
    >/dev/null 2>&1 < /dev/null &
  pid="$!"
  pool_add "$issue"
  pool_set PID "$issue" "$pid"
  pool_set REPORT "$issue" "$report_file"
  mark_in_progress "$issue" "$pid" "$context_file" "$log_file" "$done_file" "$report_file" "$issue_dir"
  write_status "STATUS|type=sub-coord-spawn|issue=${issue}|pid=${pid}|agent=${SUB_COORD_AGENT}|model=${SUB_COORD_MODEL}|pool_size=$(set -- $POOL_ISSUES; echo $#)|parallel_jobs=${PARALLEL_JOBS}"
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
      if [ "$JSON_PARSER" = "jq" ]; then
        local tmp_file
        tmp_file="$(mktemp -t run-with-it-state.XXXXXX)"
        jq \
          --arg issue "$issue" \
          --arg report_file "$report_file" \
          '
            .issue_registry = (.issue_registry // {})
            | .issue_registry[$issue] = ((.issue_registry[$issue] // {}) + {
                status: "blocked",
                merge_recovery_report_file: $report_file
              })
            | .issue_registry[$issue].blocking_reasons = (((.issue_registry[$issue].blocking_reasons // []) + ["merge recovery dispatcher failed"]) | unique)
          ' "$STATE_FILE" > "$tmp_file"
        mv "$tmp_file" "$STATE_FILE"
      else
        python3 - "$STATE_FILE" "$issue" "$report_file" <<'PY'
import json, sys
path, issue, report_file = sys.argv[1:]
state = json.load(open(path))
entry = state.setdefault("issue_registry", {}).setdefault(str(issue), {})
entry["status"] = "blocked"
entry["merge_recovery_report_file"] = report_file
entry.setdefault("blocking_reasons", []).append("merge recovery dispatcher failed")
json.dump(state, open(path, "w"), indent=2)
PY
      fi
    fi
  fi
  write_status "STATUS|type=merge-recovery|issue=${issue}|report_file=${report_file}|state=${recovery_status}"
  update_github_issue "$issue" "$recovery_status" "$report_file"
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
	    if [ "$outcome" = "merge_recovery" ]; then
	      run_merge_recovery "$issue"
	    else
	      update_github_issue "$issue" "$outcome" "$report_file"
	    fi
	    fill_free_slots "$issue"
  done
  fill_free_slots "tick"
  emit_waiting_context_status
done

write_status "STATUS|type=pool-empty|state_file=${STATE_FILE}"
