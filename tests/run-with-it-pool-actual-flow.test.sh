#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_file() {
  local file="$1"
  local message="$2"
  [[ -f "$file" ]] || fail "$message (missing: $file)"
}

assert_dir() {
  local dir="$1"
  local message="$2"
  [[ -d "$dir" ]] || fail "$message (missing: $dir)"
}

assert_not_file() {
  local file="$1"
  local message="$2"
  [[ ! -f "$file" ]] || fail "$message (unexpected file: $file)"
}

assert_file_contains() {
  local file="$1"
  local needle="$2"
  local message="$3"
  grep -Fq -- "$needle" "$file" || fail "$message (missing: $needle in $file)"
}

assert_json_status() {
  local state_file="$1"
  local issue="$2"
  local expected="$3"
  python3 - "$state_file" "$issue" "$expected" <<'PY'
import json, sys
state = json.load(open(sys.argv[1]))
actual = state["issue_registry"][sys.argv[2]]["status"]
expected = sys.argv[3]
if actual != expected:
    raise SystemExit(f"issue {sys.argv[2]} status expected {expected}, got {actual}")
PY
}

assert_json_github_update() {
  local state_file="$1"
  local issue="$2"
  local expected="$3"
  python3 - "$state_file" "$issue" "$expected" <<'PY'
import json, sys
state = json.load(open(sys.argv[1]))
actual = state["issue_registry"][sys.argv[2]].get("github_update_status")
expected = sys.argv[3]
if actual != expected:
    raise SystemExit(f"issue {sys.argv[2]} github_update_status expected {expected}, got {actual}")
PY
}

make_fixture() {
  local root="$1"
  local asset_root="${root}/assets"
  local project="${root}/project"
  local fake_bin="${root}/bin"
  mkdir -p "$asset_root" "$project" "$fake_bin"
  cp "${ROOT_DIR}/assets/run-agent.sh" \
    "${ROOT_DIR}/assets/worker-watch.sh" \
    "${ROOT_DIR}/assets/run-with-it-dispatch.sh" \
    "${ROOT_DIR}/assets/run-with-it-pool.sh" \
    "${ROOT_DIR}/assets/run-with-it-state.py" \
    "${ROOT_DIR}/assets/run-with-it-github-update.py" \
    "${ROOT_DIR}/assets/run-with-it-artifacts.py" \
    "${ROOT_DIR}/assets/merge-recovery-prompt.md" \
    "$asset_root/"
  chmod +x "$asset_root/run-agent.sh" \
    "$asset_root/worker-watch.sh" \
    "$asset_root/run-with-it-dispatch.sh" \
    "$asset_root/run-with-it-pool.sh" \
    "$asset_root/run-with-it-state.py" \
    "$asset_root/run-with-it-github-update.py" \
    "$asset_root/run-with-it-artifacts.py"

  cat > "$asset_root/agent-registry.json" <<'JSON'
{
  "schema_version": 1,
  "aliases": {},
  "agents": {
    "fake-sub": {
      "display_name": "Fake Sub Coordinator",
      "detection": { "command": "fake-sub-agent", "args": ["--version"] },
      "invocation": {
        "command": "fake-sub-agent",
        "args_template": ["{{repo_root}}", "{{prompt}}"],
        "prompt_argument_template": "{{prompt}}"
      },
      "permission_modes": { "default": "", "available": [""] },
      "model": { "default": "fake-model", "flag_template": "", "known_models": ["fake-model"] },
      "capability_band": "balanced",
      "fallback_order": [],
      "user_model_configuration": {
        "requires_user_model_config": false,
        "config_paths": [],
        "skip_when_unconfigured": false,
        "skip_message": ""
      }
    }
  }
}
JSON
  printf '# Fake Sub-Coordinator Prompt\n' > "$asset_root/sub-coordinator-prompt.md"

  cat > "$fake_bin/fake-sub-agent" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  printf 'fake-sub-agent 1.0\n'
  exit 0
fi
repo_root="$1"
prompt_payload="$2"
report_file="$(printf '%s' "$prompt_payload" | sed -n 's/^REPORT_FILE=//p; s/^MERGE_RECOVERY_REPORT_FILE=//p' | head -n 1)"
outcome="$(printf '%s' "$prompt_payload" | sed -n 's/^OUTCOME=//p' | head -n 1)"
issue="${RUN_WITH_IT_ISSUE:-unknown}"
role="${RUN_WITH_IT_ROLE:-unknown}"
printf 'STATUS|type=heartbeat|issue=%s|role=%s|phase=starting|progress=fake sub start\n' "$issue" "$role"
printf 'STATUS|type=merge-start|issue=%s|branch=run-with-it/smoke/issue-%s|target=run-with-it/smoke\n' "$issue" "$issue"
mkdir -p "$repo_root" "$(dirname "$report_file")"
printf 'issue=%s repo=%s\n' "$issue" "$repo_root" > "$repo_root/issue-$issue.marker"
if [[ "$role" == "merge-recovery" ]]; then
  printf 'STATUS|type=heartbeat|issue=%s|role=merge-recovery|phase=resolving|progress=fake recovery\n' "$issue"
  printf '{"schema_version":1,"issue_number":%s,"outcome":"completed","summary":"fake recovery completed","feature_branch":"run-with-it/smoke","issue_branch":"run-with-it/smoke/issue-%s","merge_sha":"fake-recovery-%s","files_modified":[{"path":"shared.txt","lines_added":1,"lines_deleted":1}],"verification":{"passed":true,"commands_run":["fake verify"],"evidence":"fake recovery passed"},"blocking_reasons":[]}\n' "$issue" "$issue" "$issue" > "$report_file"
elif [[ "$outcome" == "merge_failed" ]]; then
  printf 'STATUS|type=merge-failed|issue=%s|reason=conflict\n' "$issue"
  printf '{"schema_version":1,"issue_number":%s,"outcome":"merge_failed","summary":"fake merge conflict","files_modified_count":1,"lines_added":2,"lines_deleted":0,"review_cycles":1,"commit_sha":"fake-%s","merge":{"status":"failed","failure_reason":"conflict","conflict_files":["shared.txt"]}}\n' "$issue" "$issue" > "$report_file"
else
  printf 'STATUS|type=merge-complete|issue=%s|merge_sha=fake-merge-%s|pushed=true\n' "$issue" "$issue"
  printf '{"schema_version":1,"issue_number":%s,"outcome":"completed","summary":"fake completed","files_modified_count":1,"lines_added":3,"lines_deleted":0,"review_cycles":1,"commit_sha":"fake-%s","merge":{"status":"completed","merge_sha":"fake-merge-%s","pushed":true}}\n' "$issue" "$issue" "$issue" > "$report_file"
fi
printf 'STATUS|type=heartbeat|issue=%s|role=%s|phase=done|progress=report written\n' "$issue" "$role"
SH
  chmod +x "$fake_bin/fake-sub-agent"

  mkdir -p "$project/.run-with-it/contexts" \
    "$project/.run-with-it/issues" \
    "$project/.run-with-it/status" \
    "$project/.run-with-it/main"
}

write_context() {
  local project="$1"
  local issue="$2"
  local outcome="$3"
  mkdir -p "$project/.run-with-it/issues/${issue}"
  cat > "$project/.run-with-it/contexts/sub-${issue}.md" <<EOF
REPORT_FILE=$project/.run-with-it/issues/${issue}/report.json
OUTCOME=$outcome
EOF
}

run_pool() {
  local root="$1"
  local parallel_jobs="$2"
  local asset_root="${root}/assets"
  local project="${root}/project"
  local fake_bin="${root}/bin"
  (
    cd "$project"
    RUN_WITH_IT_GITHUB_UPDATES=0 PATH="$fake_bin:$PATH" "$asset_root/run-with-it-pool.sh" \
      --asset-root "$asset_root" \
      --state-file "$project/.run-with-it/main-state.json" \
      --parallel-jobs "$parallel_jobs" \
      --agent fake-sub \
      --model fake-model \
      --status-file "$project/.run-with-it/status/current.txt" \
      --events-log "$project/.run-with-it/status/events.log" \
      --main-log "$project/.run-with-it/main/main.log" \
      --poll-seconds 1 \
      --timeout-seconds 30 >/dev/null
  )
}

assert_spawned_issue_artifacts() {
  local project="$1"
  local issue="$2"
  local expected_merge_line="$3"
  local issue_dir="$project/.run-with-it/issues/${issue}"
  local log_file="$issue_dir/sub-coordinator.log"
  local report_file="$issue_dir/report.json"
  local done_file="$issue_dir/sub-coordinator.done"
  assert_dir "$issue_dir" "issue artifact folder exists for issue ${issue}"
  assert_file "$log_file" "sub log exists for issue ${issue}"
  assert_file "$report_file" "report exists for issue ${issue}"
  assert_file "$done_file" "done sentinel exists for issue ${issue}"
  assert_file_contains "$log_file" "STATUS|type=dispatch-ready|issue=${issue}|role=sub-coord" "sub log records dispatch-ready for issue ${issue}"
  assert_file_contains "$log_file" "STATUS|type=heartbeat|issue=${issue}|role=sub-coord|phase=starting" "sub log records heartbeat for issue ${issue}"
  assert_file_contains "$log_file" "$expected_merge_line" "sub log records merge result for issue ${issue}"
  assert_file_contains "$done_file" "DONE|issue=${issue}|role=sub-coord" "done sentinel records issue ${issue}"
  local dispatch_ready_count
  dispatch_ready_count="$(grep -Fc "STATUS|type=dispatch-ready|issue=${issue}|role=sub-coord" "$log_file")"
  [[ "$dispatch_ready_count" == "1" ]] || fail "dispatch-ready duplicated for issue ${issue}: $dispatch_ready_count"
}

WORK_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

SUCCESS_ROOT="$WORK_DIR/success"
make_fixture "$SUCCESS_ROOT"
SUCCESS_PROJECT="$SUCCESS_ROOT/project"
write_context "$SUCCESS_PROJECT" 10 completed
write_context "$SUCCESS_PROJECT" 11 completed
write_context "$SUCCESS_PROJECT" 12 completed
cat > "$SUCCESS_PROJECT/.run-with-it/main-state.json" <<JSON
{
  "schema_version": 4,
  "execution_plan": { "parallel_jobs": 2, "topo_order": [10, 11, 12] },
  "issue_registry": {
    "10": { "status": "pending", "deps": [], "context_file": "$SUCCESS_PROJECT/.run-with-it/contexts/sub-10.md" },
    "11": { "status": "pending", "deps": [], "context_file": "$SUCCESS_PROJECT/.run-with-it/contexts/sub-11.md" },
    "12": { "status": "pending", "deps": [10], "context_file": "$SUCCESS_PROJECT/.run-with-it/contexts/sub-12.md" }
  },
  "active_pool_issues": [],
  "completed_summaries": [],
  "ledger_rows": []
}
JSON
run_pool "$SUCCESS_ROOT" 2
assert_json_status "$SUCCESS_PROJECT/.run-with-it/main-state.json" 10 completed
assert_json_status "$SUCCESS_PROJECT/.run-with-it/main-state.json" 11 completed
assert_json_status "$SUCCESS_PROJECT/.run-with-it/main-state.json" 12 completed
assert_spawned_issue_artifacts "$SUCCESS_PROJECT" 10 "STATUS|type=merge-complete|issue=10"
assert_spawned_issue_artifacts "$SUCCESS_PROJECT" 11 "STATUS|type=merge-complete|issue=11"
assert_spawned_issue_artifacts "$SUCCESS_PROJECT" 12 "STATUS|type=merge-complete|issue=12"
assert_file_contains "$SUCCESS_PROJECT/.run-with-it/status/events.log" "STATUS|type=sub-coord-complete|issue=10|outcome=completed" "events log records issue 10 completion"
assert_file_contains "$SUCCESS_PROJECT/.run-with-it/status/events.log" "STATUS|type=github-update|issue=10|outcome=completed|action=skipped|reason=disabled" "events log records immediate issue 10 GitHub update attempt"
assert_file_contains "$SUCCESS_PROJECT/.run-with-it/status/events.log" "STATUS|type=sub-coord-complete|issue=11|outcome=completed" "events log records issue 11 completion"
assert_file_contains "$SUCCESS_PROJECT/.run-with-it/status/events.log" "STATUS|type=github-update|issue=11|outcome=completed|action=skipped|reason=disabled" "events log records immediate issue 11 GitHub update attempt"
assert_file_contains "$SUCCESS_PROJECT/.run-with-it/status/events.log" "STATUS|type=sub-coord-complete|issue=12|outcome=completed" "events log records issue 12 completion"
assert_json_github_update "$SUCCESS_PROJECT/.run-with-it/main-state.json" 10 skipped
assert_file_contains "$SUCCESS_PROJECT/.run-with-it/main/main.log" "STATUS|type=pool-empty" "main log records pool empty"

MIXED_ROOT="$WORK_DIR/mixed"
make_fixture "$MIXED_ROOT"
MIXED_PROJECT="$MIXED_ROOT/project"
MIXED_PROJECT_REAL="$(cd "$MIXED_PROJECT" && pwd -P)"
write_context "$MIXED_PROJECT" 1 merge_failed
write_context "$MIXED_PROJECT" 2 completed
write_context "$MIXED_PROJECT" 3 completed
write_context "$MIXED_PROJECT" 4 completed
cat > "$MIXED_PROJECT/.run-with-it/main-state.json" <<JSON
{
  "schema_version": 4,
  "execution_plan": { "parallel_jobs": 2, "topo_order": [1, 2, 3, 4] },
  "issue_registry": {
    "1": { "status": "pending", "deps": [], "context_file": "$MIXED_PROJECT/.run-with-it/contexts/sub-1.md" },
    "2": { "status": "pending", "deps": [1], "context_file": "$MIXED_PROJECT/.run-with-it/contexts/sub-2.md" },
    "3": { "status": "pending", "deps": [], "context_file": "$MIXED_PROJECT/.run-with-it/contexts/sub-3.md" },
    "4": { "status": "pending", "deps": [], "context_file": "$MIXED_PROJECT/.run-with-it/contexts/sub-4.md" }
  },
  "active_pool_issues": [],
  "completed_summaries": [],
  "ledger_rows": []
}
JSON
run_pool "$MIXED_ROOT" 2
assert_json_status "$MIXED_PROJECT/.run-with-it/main-state.json" 1 completed
assert_json_status "$MIXED_PROJECT/.run-with-it/main-state.json" 2 completed
assert_json_status "$MIXED_PROJECT/.run-with-it/main-state.json" 3 completed
assert_json_status "$MIXED_PROJECT/.run-with-it/main-state.json" 4 completed
assert_spawned_issue_artifacts "$MIXED_PROJECT" 1 "STATUS|type=merge-failed|issue=1"
assert_file "$MIXED_PROJECT/.run-with-it/issues/1/merge-recovery.log" "merge recovery log exists for issue 1"
assert_file "$MIXED_PROJECT/.run-with-it/issues/1/merge-recovery-report.json" "merge recovery report exists for issue 1"
assert_file "$MIXED_PROJECT/.run-with-it/issues/1/merge-recovery.done" "merge recovery done sentinel exists for issue 1"
assert_file_contains "$MIXED_PROJECT/.run-with-it/issues/1/merge-recovery.log" "STATUS|type=heartbeat|issue=1|role=merge-recovery|phase=resolving|progress=fake recovery" "merge recovery coordinator runs for issue 1"
assert_spawned_issue_artifacts "$MIXED_PROJECT" 3 "STATUS|type=merge-complete|issue=3"
assert_spawned_issue_artifacts "$MIXED_PROJECT" 4 "STATUS|type=merge-complete|issue=4"
assert_spawned_issue_artifacts "$MIXED_PROJECT" 2 "STATUS|type=merge-complete|issue=2"
assert_file_contains "$MIXED_PROJECT/.run-with-it/status/events.log" "STATUS|type=merge-failed|issue=1|reason=conflict" "events log records issue 1 merge failure"
assert_file_contains "$MIXED_PROJECT/.run-with-it/status/events.log" "STATUS|type=sub-coord-complete|issue=1|outcome=merge_recovery" "events log records issue 1 merge recovery transition"
assert_file_contains "$MIXED_PROJECT/.run-with-it/status/events.log" "STATUS|type=merge-recovery|issue=1|report_file=$MIXED_PROJECT_REAL/.run-with-it/issues/1/merge-recovery-report.json|state=completed" "events log records issue 1 recovery completion"
assert_file_contains "$MIXED_PROJECT/.run-with-it/status/events.log" "STATUS|type=sub-coord-complete|issue=2|outcome=completed" "events log records dependent issue 2 completion after recovery"
assert_file_contains "$MIXED_PROJECT/.run-with-it/status/events.log" "STATUS|type=sub-coord-complete|issue=3|outcome=completed" "events log records unrelated issue 3 completion"
assert_file_contains "$MIXED_PROJECT/.run-with-it/status/events.log" "STATUS|type=sub-coord-complete|issue=4|outcome=completed" "events log records unrelated issue 4 completion"
assert_file_contains "$MIXED_PROJECT/.run-with-it/main/main.log" "STATUS|type=pool-slot-filled" "main log records rolling pool slot refill"

python3 - "$SUCCESS_PROJECT/.run-with-it/main-state.json" "$MIXED_PROJECT/.run-with-it/main-state.json" <<'PY'
import json, sys
success = json.load(open(sys.argv[1]))
mixed = json.load(open(sys.argv[2]))
if len(success.get("completed_summaries", [])) != 3:
    raise SystemExit("success scenario should have three completed summaries")
if len(mixed.get("completed_summaries", [])) != 4:
    raise SystemExit("mixed scenario should have four completed summaries")
if len(mixed.get("merge_recovery_summaries", [])) != 1:
    raise SystemExit("mixed scenario should have one merge recovery summary")
if mixed.get("active_pool_issues") != []:
    raise SystemExit("mixed scenario should end with empty active pool")
PY

echo "PASS: run-with-it actual pool success and merge-recovery flow"
