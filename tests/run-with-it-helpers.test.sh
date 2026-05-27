#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_HELPER="${ROOT_DIR}/assets/run-with-it-state.py"
GITHUB_HELPER="${ROOT_DIR}/assets/run-with-it-github-update.py"

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
assert state["completed_summaries"][-1]["commit_sha"] == "abc123"
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

update_output="$(RUN_WITH_IT_GITHUB_UPDATES=0 python3 "$GITHUB_HELPER" update --state-file "$STATE_FILE" --run-root "$WORK_DIR" --issue 2 --outcome completed --report-file "$REPORT_FILE")"
assert_contains "$update_output" "STATUS|type=github-update|issue=2|outcome=completed|action=skipped|reason=disabled" "GitHub helper emits disabled update status"

python3 "$STATE_HELPER" write-merge-recovery-context \
  --state-file "$STATE_FILE" \
  --issue 2 \
  --context-file "$RECOVERY_CONTEXT" \
  --recovery-report-file "$RECOVERY_REPORT"
grep -Fq "MERGE_RECOVERY_CONTEXT_JSON" "$RECOVERY_CONTEXT" || fail "recovery context contains JSON payload"

echo "PASS: run-with-it Python helper contracts"
