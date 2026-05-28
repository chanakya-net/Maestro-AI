#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_HELPER="${ROOT_DIR}/assets/run-with-it-state.py"
GITHUB_HELPER="${ROOT_DIR}/assets/run-with-it-github-update.py"
PR_BODY_HELPER="${ROOT_DIR}/assets/run-with-it-pr-body.py"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_eq() {
  local actual="$1"
  local expected="$2"
  local message="$3"
  [[ "$actual" == "$expected" ]] || fail "$message (expected: $expected, got: $actual)"
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"
  [[ "$haystack" == *"$needle"* ]] || fail "$message (missing: $needle)"
}

assert_not_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"
  [[ "$haystack" != *"$needle"* ]] || fail "$message (forbidden: $needle)"
}

WORK_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

STATE_FILE="$WORK_DIR/main-state.json"
REPORT_FILE="$WORK_DIR/report.json"
DERIVED_REPORT_FILE="$WORK_DIR/report-derived.json"
RECOVERY_CONTEXT="$WORK_DIR/merge-recovery-context.md"
RECOVERY_REPORT="$WORK_DIR/merge-recovery-report.json"
MERGE_FINAL_REPORT="$WORK_DIR/merge-final-report.json"
FALLBACK_STATE_FILE="$WORK_DIR/fallback-main-state.json"
MISSING_STATE_FILE="$WORK_DIR/missing-main-state.json"
MALFORMED_STATE_FILE="$WORK_DIR/malformed-main-state.json"

cat > "$STATE_FILE" <<JSON
{
  "schema_version": 4,
  "execution_plan": { "parallel_jobs": 2, "topo_order": [1, 2, 3] },
  "issue_registry": {
    "1": { "status": "completed", "deps": [], "context_file": "$WORK_DIR/sub-1.md" },
    "2": { "status": "pending", "deps": [1], "context_file": "$WORK_DIR/sub-2.md" },
    "3": { "status": "pending", "deps": [2], "context_file": "$WORK_DIR/sub-3.md" }
  },
  "active_pool_issues": [],
  "completed_summaries": [],
  "ledger_rows": []
}
JSON

ready="$(python3 "$STATE_HELPER" ready-issues --state-file "$STATE_FILE" --limit 4)"
assert_eq "$ready" "2" "state helper returns only dependency-ready issues"

context="$(python3 "$STATE_HELPER" context-file-for --state-file "$STATE_FILE" --issue 2)"
assert_eq "$context" "$WORK_DIR/sub-2.md" "state helper returns persisted context path"

python3 "$STATE_HELPER" mark-in-progress \
  --state-file "$STATE_FILE" \
  --issue 2 \
  --pid 12345 \
  --context-file "$WORK_DIR/sub-2.md" \
  --log-file "$WORK_DIR/issue-2.log" \
  --done-file "$WORK_DIR/issue-2.done" \
  --report-file "$REPORT_FILE" \
  --issue-dir "$WORK_DIR/issues/2"

cat > "$REPORT_FILE" <<'JSON'
{
  "outcome": "completed",
  "summary": "helper completed",
  "files_modified_count": 1,
  "lines_added": 3,
  "lines_deleted": 1,
  "review_cycles": 1,
  "commit_sha": "abc123",
  "verification": {
    "passed": true,
    "commands_run": ["fake test"],
    "evidence": "fake test passed"
  },
  "review_summary": {
    "cycles_used": 1,
    "final_verdict": "approve",
    "reviewer_model": "fake-reviewer"
  },
  "model_usage": [
    {
      "role": "complexity",
      "cycle": 1,
      "agent": "agy",
      "model": "gemini-3.5-flash-medium",
      "selection_reason": "complexity-scorer"
    },
    {
      "role": "impl",
      "cycle": 1,
      "agent": "codex",
      "model": "gpt-5.3-codex",
      "selection_reason": "under-target"
    },
    {
      "role": "review",
      "cycle": 1,
      "agent": "claude",
      "model": "claude-sonnet-4-6",
      "selection_reason": "independent-review"
    }
  ],
  "token_usage": {
    "impl_input": 10,
    "impl_output": 5,
    "cache_hit_tokens": 2
  }
}
JSON

outcome="$(python3 "$STATE_HELPER" finalize-issue --state-file "$STATE_FILE" --issue 2 --report-file "$REPORT_FILE")"
assert_eq "$outcome" "completed" "state helper reports finalized issue outcome"

python3 - "$STATE_FILE" <<'PY'
import json, sys
state = json.load(open(sys.argv[1]))
issue = state["issue_registry"]["2"]
assert issue["status"] == "completed"
assert state["active_pool_issues"] == []
assert any(row == f"STATUS|type=ledger|task=2|outcome=completed|report={sys.argv[1].replace('main-state.json', 'report.json')}" for row in state["ledger_rows"])
summary = state["completed_summaries"][-1]
assert summary["commit_sha"] == "abc123"
assert summary["summary"] == "helper completed"
assert summary["verification"]["passed"] is True
assert summary["verification"]["evidence"] == "fake test passed"
assert summary["report_file"] == sys.argv[1].replace("main-state.json", "report.json")
model_usage = summary["model_usage"]
assert model_usage[0]["role"] == "complexity"
assert model_usage[0]["agent"] == "agy"
assert model_usage[1]["model"] == "gpt-5.3-codex"
PY

python3 "$STATE_HELPER" mark-in-progress \
  --state-file "$STATE_FILE" \
  --issue 3 \
  --pid 12346 \
  --context-file "$WORK_DIR/sub-3.md" \
  --log-file "$WORK_DIR/issue-3.log" \
  --done-file "$WORK_DIR/issue-3.done" \
  --report-file "$DERIVED_REPORT_FILE" \
  --issue-dir "$WORK_DIR/issues/3"

cat > "$DERIVED_REPORT_FILE" <<'JSON'
{
  "outcome": "completed",
  "summary": "helper completed with compact file stats",
  "files_modified": [
    { "path": "alpha.md", "lines_added": 5, "lines_deleted": 0 },
    { "path": "bravo.md", "lines_added": 2, "lines_deleted": 1 }
  ],
  "review_cycles": 0,
  "commit_sha": "def456"
}
JSON

derived_outcome="$(python3 "$STATE_HELPER" finalize-issue --state-file "$STATE_FILE" --issue 3 --report-file "$DERIVED_REPORT_FILE")"
assert_eq "$derived_outcome" "completed" "state helper reports finalized derived issue outcome"

python3 - "$STATE_FILE" <<'PY'
import json, sys
state = json.load(open(sys.argv[1]))
summary = state["completed_summaries"][-1]
assert summary["issue"] == 3
assert summary["files_modified_count"] == 2
assert summary["lines_added"] == 7
assert summary["lines_deleted"] == 1
assert summary["commit_sha"] == "def456"
PY

comment="$(python3 "$GITHUB_HELPER" render-comment --outcome completed --report-file "$REPORT_FILE")"
assert_contains "$comment" "## Status" "GitHub helper renders status section"
assert_contains "$comment" "helper completed" "GitHub helper includes report summary"
assert_contains "$comment" "Input tokens: 10" "GitHub helper sums input tokens"
assert_contains "$comment" "Output tokens: 5" "GitHub helper sums output tokens"
assert_contains "$comment" "Cache hit tokens: 2" "GitHub helper reports cache tokens"

pr_body="$(python3 "$PR_BODY_HELPER" render --state-file "$STATE_FILE")"
assert_contains "$pr_body" "## Summary" "PR body includes summary section"
assert_contains "$pr_body" "## Closed Issues" "PR body includes closed issues section"
assert_contains "$pr_body" "## Models Used" "PR body includes models section"
assert_contains "$pr_body" "## Verification" "PR body includes verification section"
assert_contains "$pr_body" "- #2" "PR body links completed issue 2"
assert_contains "$pr_body" "- #3" "PR body links completed issue 3"
assert_not_contains "$pr_body" "Closes #" "PR body avoids auto-closing keyword Closes"
assert_not_contains "$pr_body" "Fixes #" "PR body avoids auto-closing keyword Fixes"
assert_not_contains "$pr_body" "Resolves #" "PR body avoids auto-closing keyword Resolves"
assert_contains "$pr_body" "| #2 | complexity | 1 | agy | gemini-3.5-flash-medium | complexity-scorer |" "PR body includes complexity model row"
assert_contains "$pr_body" "| #2 | impl | 1 | codex | gpt-5.3-codex | under-target |" "PR body includes implementation model row"
assert_contains "$pr_body" "| #2 | review | 1 | claude | claude-sonnet-4-6 | independent-review |" "PR body includes review model row"
assert_contains "$pr_body" "| #3 | unknown | - | unknown | unknown | missing-model-usage |" "PR body falls back when model usage is absent"

cat > "$FALLBACK_STATE_FILE" <<JSON
{
  "schema_version": 4,
  "execution_plan": { "parallel_jobs": 1, "topo_order": [4] },
  "issue_registry": {
    "4": { "status": "completed", "report_file": "$WORK_DIR/missing-report.json" }
  },
  "active_pool_issues": [],
  "completed_summaries": [
    {
      "issue": 4,
      "outcome": "completed",
      "summary": "summary fallback completed",
      "verification": {
        "passed": true,
        "evidence": "Fixes #123 and Resolves owner/repo#789"
      },
      "report_file": "$WORK_DIR/invalid-report.json",
      "model_usage": [
        {
          "role": "impl",
          "cycle": 2,
          "agent": "codex",
          "model": "gpt-5.3-codex",
          "selection_reason": "Closes #456"
        }
      ],
      "files_modified_count": 1,
      "lines_added": 2,
      "lines_deleted": 0,
      "review_cycles": 1,
      "commit_sha": "fed987"
    }
  ],
  "ledger_rows": []
}
JSON
printf '{invalid json' > "$WORK_DIR/invalid-report.json"

fallback_pr_body="$(python3 "$PR_BODY_HELPER" render --state-file "$FALLBACK_STATE_FILE")"
assert_contains "$fallback_pr_body" "- #4" "PR body links completed fallback issue"
assert_contains "$fallback_pr_body" "| #4 | impl | 2 | codex | gpt-5.3-codex | Closes \#456 |" "PR body uses sanitized summary model usage when report files are unavailable"
assert_contains "$fallback_pr_body" "| #4 | passed | Fixes \#123 and Resolves owner/repo\#789 |" "PR body uses sanitized summary verification when report files are unavailable"
assert_not_contains "$fallback_pr_body" "Closes #" "PR body sanitizes summary fallback closing keyword Closes"
assert_not_contains "$fallback_pr_body" "Fixes #" "PR body sanitizes summary fallback closing keyword Fixes"
assert_not_contains "$fallback_pr_body" "Resolves owner/repo#" "PR body sanitizes owner/repo closing references"

if python3 "$PR_BODY_HELPER" render --state-file "$MISSING_STATE_FILE" >"$WORK_DIR/missing-state.out" 2>"$WORK_DIR/missing-state.err"; then
  fail "PR body renderer fails for missing required state file"
fi
missing_state_error="$(cat "$WORK_DIR/missing-state.err")"
assert_contains "$missing_state_error" "error: failed to load state file" "PR body renderer reports missing required state file"

printf '{invalid json' > "$MALFORMED_STATE_FILE"
if python3 "$PR_BODY_HELPER" render --state-file "$MALFORMED_STATE_FILE" >"$WORK_DIR/malformed-state.out" 2>"$WORK_DIR/malformed-state.err"; then
  fail "PR body renderer fails for malformed required state file"
fi
malformed_state_error="$(cat "$WORK_DIR/malformed-state.err")"
assert_contains "$malformed_state_error" "error: failed to load state file" "PR body renderer reports malformed required state file"

update_output="$(RUN_WITH_IT_GITHUB_UPDATES=0 python3 "$GITHUB_HELPER" update --state-file "$STATE_FILE" --run-root "$WORK_DIR" --issue 2 --outcome completed --report-file "$REPORT_FILE")"
assert_contains "$update_output" "STATUS|type=github-update|issue=2|outcome=completed|action=skipped|reason=disabled" "GitHub helper emits disabled update status"

python3 "$STATE_HELPER" write-merge-recovery-context \
  --state-file "$STATE_FILE" \
  --issue 2 \
  --context-file "$RECOVERY_CONTEXT" \
  --recovery-report-file "$RECOVERY_REPORT"
grep -Fq "MERGE_RECOVERY_CONTEXT_JSON" "$RECOVERY_CONTEXT" || fail "recovery context contains JSON payload"

cat > "$MERGE_FINAL_REPORT" <<'JSON'
{
  "outcome": "completed",
  "summary": "merge recovery completed",
  "files_modified_count": 1,
  "lines_added": 4,
  "lines_deleted": 2,
  "review_cycles": 0,
  "merge_sha": "999aaa",
  "verification": {
    "passed": true,
    "evidence": "merge recovery verified"
  },
  "model_usage": [
    {
      "role": "merge-recovery",
      "cycle": 1,
      "agent": "codex",
      "model": "gpt-5.3-codex",
      "selection_reason": "merge-conflict"
    }
  ]
}
JSON
merge_outcome="$(python3 "$STATE_HELPER" finalize-merge-recovery --state-file "$STATE_FILE" --issue 2 --report-file "$MERGE_FINAL_REPORT")"
assert_eq "$merge_outcome" "completed" "state helper reports finalized merge recovery outcome"

python3 - "$STATE_FILE" <<'PY'
import json, sys
state = json.load(open(sys.argv[1]))
summary = state["completed_summaries"][-1]
assert summary["issue"] == 2
assert summary["outcome"] == "completed"
assert summary["summary"] == "merge recovery completed"
assert summary["verification"]["passed"] is True
assert summary["verification"]["evidence"] == "merge recovery verified"
assert summary["report_file"] == sys.argv[1].replace("main-state.json", "merge-final-report.json")
assert summary["model_usage"][0]["role"] == "merge-recovery"
assert summary["model_usage"][0]["model"] == "gpt-5.3-codex"
assert summary["commit_sha"] == "999aaa"
PY

recovery_pr_body="$(python3 "$PR_BODY_HELPER" render --state-file "$STATE_FILE")"
assert_contains "$recovery_pr_body" "| #2 | merge-recovery | 1 | codex | gpt-5.3-codex | merge-conflict |" "PR body uses recovery model data instead of stale registry report"
assert_contains "$recovery_pr_body" "| #2 | passed | merge recovery verified |" "PR body uses recovery verification instead of stale registry report"
assert_not_contains "$recovery_pr_body" "fake test passed" "PR body avoids stale registry report verification after merge recovery"

echo "PASS: run-with-it Python helper contracts"
