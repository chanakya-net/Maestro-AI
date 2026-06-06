#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE_DIR="${ROOT_DIR}/tests/fixtures/run-with-it-helper-parity"
PYTHON_PY_STATE_HELPER="${ROOT_DIR}/assets/python/run-with-it-state.py"
PYTHON_ROUTER_HELPER="${ROOT_DIR}/assets/python/run-with-it-router.py"
PYTHON_ARTIFACTS_HELPER="${ROOT_DIR}/assets/python/run-with-it-artifacts.py"
PYTHON_GITHUB_HELPER="${ROOT_DIR}/assets/python/run-with-it-github-update.py"
PYTHON_PR_BODY_HELPER="${ROOT_DIR}/assets/python/run-with-it-pr-body.py"
C_SHARP_STATE_HELPER="${ROOT_DIR}/assets/csharp/run-with-it-state.cs"
C_SHARP_ROUTER_HELPER="${ROOT_DIR}/assets/csharp/run-with-it-router.cs"
C_SHARP_ARTIFACTS_HELPER="${ROOT_DIR}/assets/csharp/run-with-it-artifacts.cs"
C_SHARP_GITHUB_HELPER="${ROOT_DIR}/assets/csharp/run-with-it-github-update.cs"
C_SHARP_PR_BODY_HELPER="${ROOT_DIR}/assets/csharp/run-with-it-pr-body.cs"

export PYTHONDONTWRITEBYTECODE=1

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

assert_file_exists() {
  local file="$1"
  local message="$2"
  [[ -f "$file" ]] || fail "$message (missing: $file)"
}

assert_file_absent() {
  local file="$1"
  local message="$2"
  [[ ! -e "$file" ]] || fail "$message (unexpected: $file)"
}

compare_text_files() {
  local expected_file="$1"
  local actual_file="$2"
  local message="$3"

  if ! cmp -s "$expected_file" "$actual_file"; then
    echo "Expected:" >&2
    cat "$expected_file" >&2
    echo "Actual:" >&2
    cat "$actual_file" >&2
    fail "$message"
  fi
}

compare_json_files() {
  local left_file="$1"
  local right_file="$2"
  local message="$3"
  shift 3
  python3 - "$left_file" "$right_file" "$message" "$@" <<'PY'
import json
import sys
from pprint import pformat

left_file, right_file, message = sys.argv[1:4]
ignore_keys = set(sys.argv[4:])


def prune(value):
    if isinstance(value, dict):
        return {key: prune(item) for key, item in value.items() if key not in ignore_keys}
    if isinstance(value, list):
        return [prune(item) for item in value]
    return value


with open(left_file, "r", encoding="utf-8") as handle:
    left = json.load(handle)
with open(right_file, "r", encoding="utf-8") as handle:
    right = json.load(handle)

left = prune(left)
right = prune(right)

if left != right:
    print(message, file=sys.stderr)
    print("Left:", file=sys.stderr)
    print(pformat(left), file=sys.stderr)
    print("Right:", file=sys.stderr)
    print(pformat(right), file=sys.stderr)
    raise SystemExit(1)
PY
}

copy_fixture_suite() {
  local suite="$1"
  local dest="$2"
  mkdir -p "$dest"
  cp -R "${FIXTURE_DIR}/${suite}/." "$dest/"
}

filter_stderr() {
  local raw_file="$1"
  local clean_file="$2"
  if [[ -s "$raw_file" ]]; then
    grep -v ' warning ' "$raw_file" >"$clean_file" || true
  else
    : >"$clean_file"
  fi
  rm -f "$raw_file"
}

capture_python() {
  local cwd="$1"
  local stdout_file="$2"
  local stderr_file="$3"
  local helper="$4"
  shift 4
  local raw_file="${stderr_file}.raw"

  set +e
  (cd "$cwd" && python3 "$helper" "$@") >"$stdout_file" 2>"$raw_file"
  local status=$?
  set -e

  filter_stderr "$raw_file" "$stderr_file"
  printf '%s' "$status"
}

capture_csharp() {
  local cwd="$1"
  local stdout_file="$2"
  local stderr_file="$3"
  local helper="$4"
  shift 4
  local raw_stdout_file="${stdout_file}.raw"
  local raw_stderr_file="${stderr_file}.raw"

  set +e
  (cd "$cwd" && dotnet run "$helper" -- "$@") >"$raw_stdout_file" 2>"$raw_stderr_file"
  local status=$?
  set -e

  filter_stderr "$raw_stdout_file" "$stdout_file"
  filter_stderr "$raw_stderr_file" "$stderr_file"
  printf '%s' "$status"
}

require_dotnet_sdk_10() {
  if ! command -v dotnet >/dev/null 2>&1; then
    echo "SKIP: dotnet SDK is not available"
    exit 0
  fi

  local dotnet_version
  dotnet_version="$(dotnet --version)"
  local major_version="${dotnet_version%%.*}"
  if [[ "$major_version" -lt 10 ]]; then
    echo "SKIP: dotnet SDK version is ${dotnet_version}, but 10+ is required"
    exit 0
  fi
}

for helper in \
  "$PYTHON_PY_STATE_HELPER" \
  "$PYTHON_ROUTER_HELPER" \
  "$PYTHON_ARTIFACTS_HELPER" \
  "$PYTHON_GITHUB_HELPER" \
  "$PYTHON_PR_BODY_HELPER" \
  "$C_SHARP_STATE_HELPER" \
  "$C_SHARP_ROUTER_HELPER" \
  "$C_SHARP_ARTIFACTS_HELPER" \
  "$C_SHARP_GITHUB_HELPER" \
  "$C_SHARP_PR_BODY_HELPER"
do
  assert_file_exists "$helper" "helper exists"
done

require_dotnet_sdk_10

WORK_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

###############################################################################
# State helper parity
###############################################################################

STATE_PY_DIR="${WORK_DIR}/state/python"
STATE_CS_DIR="${WORK_DIR}/state/csharp"
mkdir -p "$STATE_PY_DIR" "$STATE_CS_DIR"
copy_fixture_suite state "$STATE_PY_DIR"
copy_fixture_suite state "$STATE_CS_DIR"

ready_py_stdout="${STATE_PY_DIR}/ready-issues.out"
ready_py_stderr="${STATE_PY_DIR}/ready-issues.err"
ready_cs_stdout="${STATE_CS_DIR}/ready-issues.out"
ready_cs_stderr="${STATE_CS_DIR}/ready-issues.err"
ready_py_status="$(capture_python "$STATE_PY_DIR" "$ready_py_stdout" "$ready_py_stderr" "$PYTHON_PY_STATE_HELPER" ready-issues --state-file base-state.json --limit 4)"
ready_cs_status="$(capture_csharp "$STATE_CS_DIR" "$ready_cs_stdout" "$ready_cs_stderr" "$C_SHARP_STATE_HELPER" ready-issues --state-file base-state.json --limit 4)"
assert_eq "$ready_py_status" "$ready_cs_status" "state ready-issues exit code parity"
compare_text_files "$ready_py_stdout" "$ready_cs_stdout" "state ready-issues stdout parity"
compare_text_files "$ready_py_stderr" "$ready_cs_stderr" "state ready-issues stderr parity"

context_py_stdout="${STATE_PY_DIR}/context-file-for.out"
context_py_stderr="${STATE_PY_DIR}/context-file-for.err"
context_cs_stdout="${STATE_CS_DIR}/context-file-for.out"
context_cs_stderr="${STATE_CS_DIR}/context-file-for.err"
context_py_status="$(capture_python "$STATE_PY_DIR" "$context_py_stdout" "$context_py_stderr" "$PYTHON_PY_STATE_HELPER" context-file-for --state-file base-state.json --issue 2)"
context_cs_status="$(capture_csharp "$STATE_CS_DIR" "$context_cs_stdout" "$context_cs_stderr" "$C_SHARP_STATE_HELPER" context-file-for --state-file base-state.json --issue 2)"
assert_eq "$context_py_status" "$context_cs_status" "state context-file-for exit code parity"
compare_text_files "$context_py_stdout" "$context_cs_stdout" "state context-file-for stdout parity"
compare_text_files "$context_py_stderr" "$context_cs_stderr" "state context-file-for stderr parity"

jobs_py_stdout="${STATE_PY_DIR}/parallel-jobs.out"
jobs_py_stderr="${STATE_PY_DIR}/parallel-jobs.err"
jobs_cs_stdout="${STATE_CS_DIR}/parallel-jobs.out"
jobs_cs_stderr="${STATE_CS_DIR}/parallel-jobs.err"
jobs_py_status="$(capture_python "$STATE_PY_DIR" "$jobs_py_stdout" "$jobs_py_stderr" "$PYTHON_PY_STATE_HELPER" parallel-jobs --state-file base-state.json)"
jobs_cs_status="$(capture_csharp "$STATE_CS_DIR" "$jobs_cs_stdout" "$jobs_cs_stderr" "$C_SHARP_STATE_HELPER" parallel-jobs --state-file base-state.json)"
assert_eq "$jobs_py_status" "$jobs_cs_status" "state parallel-jobs exit code parity"
compare_text_files "$jobs_py_stdout" "$jobs_cs_stdout" "state parallel-jobs stdout parity"
compare_text_files "$jobs_py_stderr" "$jobs_cs_stderr" "state parallel-jobs stderr parity"

mark_py_stdout="${STATE_PY_DIR}/mark-in-progress.out"
mark_py_stderr="${STATE_PY_DIR}/mark-in-progress.err"
mark_cs_stdout="${STATE_CS_DIR}/mark-in-progress.out"
mark_cs_stderr="${STATE_CS_DIR}/mark-in-progress.err"
mark_py_status="$(capture_python "$STATE_PY_DIR" "$mark_py_stdout" "$mark_py_stderr" "$PYTHON_PY_STATE_HELPER" mark-in-progress --state-file base-state.json --issue 2 --pid 12345 --context-file contexts/sub-2.md --log-file logs/issue-2.log --done-file workers/issue-2.done --report-file reports/issue-2-report.json --issue-dir issues/2)"
mark_cs_status="$(capture_csharp "$STATE_CS_DIR" "$mark_cs_stdout" "$mark_cs_stderr" "$C_SHARP_STATE_HELPER" mark-in-progress --state-file base-state.json --issue 2 --pid 12345 --context-file contexts/sub-2.md --log-file logs/issue-2.log --done-file workers/issue-2.done --report-file reports/issue-2-report.json --issue-dir issues/2)"
assert_eq "$mark_py_status" "$mark_cs_status" "state mark-in-progress exit code parity"
compare_text_files "$mark_py_stdout" "$mark_cs_stdout" "state mark-in-progress stdout parity"
compare_text_files "$mark_py_stderr" "$mark_cs_stderr" "state mark-in-progress stderr parity"
compare_json_files "${STATE_PY_DIR}/base-state.json" "${STATE_CS_DIR}/base-state.json" "state mark-in-progress side effects parity" started_at

final_py_stdout="${STATE_PY_DIR}/finalize-completed.out"
final_py_stderr="${STATE_PY_DIR}/finalize-completed.err"
final_cs_stdout="${STATE_CS_DIR}/finalize-completed.out"
final_cs_stderr="${STATE_CS_DIR}/finalize-completed.err"
final_py_status="$(capture_python "$STATE_PY_DIR" "$final_py_stdout" "$final_py_stderr" "$PYTHON_PY_STATE_HELPER" finalize-issue --state-file base-state.json --issue 2 --report-file report-issue-2-completed.json)"
final_cs_status="$(capture_csharp "$STATE_CS_DIR" "$final_cs_stdout" "$final_cs_stderr" "$C_SHARP_STATE_HELPER" finalize-issue --state-file base-state.json --issue 2 --report-file report-issue-2-completed.json)"
assert_eq "$final_py_status" "$final_cs_status" "state finalize-issue completed exit code parity"
compare_text_files "$final_py_stdout" "$final_cs_stdout" "state finalize-issue completed stdout parity"
compare_text_files "$final_py_stderr" "$final_cs_stderr" "state finalize-issue completed stderr parity"
compare_json_files "${STATE_PY_DIR}/base-state.json" "${STATE_CS_DIR}/base-state.json" "state finalize-issue completed side effects parity" started_at

merge_py_dir="${WORK_DIR}/state-merge/python"
merge_cs_dir="${WORK_DIR}/state-merge/csharp"
mkdir -p "$merge_py_dir" "$merge_cs_dir"
copy_fixture_suite state "$merge_py_dir"
copy_fixture_suite state "$merge_cs_dir"

merge_py_stdout="${merge_py_dir}/finalize-merge-recovery.out"
merge_py_stderr="${merge_py_dir}/finalize-merge-recovery.err"
merge_cs_stdout="${merge_cs_dir}/finalize-merge-recovery.out"
merge_cs_stderr="${merge_cs_dir}/finalize-merge-recovery.err"
merge_py_status="$(capture_python "$merge_py_dir" "$merge_py_stdout" "$merge_py_stderr" "$PYTHON_PY_STATE_HELPER" finalize-issue --state-file base-state.json --issue 3 --report-file report-issue-3-merge-failed.json)"
merge_cs_status="$(capture_csharp "$merge_cs_dir" "$merge_cs_stdout" "$merge_cs_stderr" "$C_SHARP_STATE_HELPER" finalize-issue --state-file base-state.json --issue 3 --report-file report-issue-3-merge-failed.json)"
assert_eq "$merge_py_status" "$merge_cs_status" "state finalize-issue merge recovery exit code parity"
compare_text_files "$merge_py_stdout" "$merge_cs_stdout" "state finalize-issue merge recovery stdout parity"
compare_text_files "$merge_py_stderr" "$merge_cs_stderr" "state finalize-issue merge recovery stderr parity"
compare_json_files "${merge_py_dir}/base-state.json" "${merge_cs_dir}/base-state.json" "state finalize-issue merge recovery side effects parity"

state_error_py_stdout="${STATE_PY_DIR}/missing-state.out"
state_error_py_stderr="${STATE_PY_DIR}/missing-state.err"
state_error_cs_stdout="${STATE_CS_DIR}/missing-state.out"
state_error_cs_stderr="${STATE_CS_DIR}/missing-state.err"
state_error_py_status="$(capture_python "$STATE_PY_DIR" "$state_error_py_stdout" "$state_error_py_stderr" "$PYTHON_PY_STATE_HELPER" parallel-jobs --state-file missing-state.json)"
state_error_cs_status="$(capture_csharp "$STATE_CS_DIR" "$state_error_cs_stdout" "$state_error_cs_stderr" "$C_SHARP_STATE_HELPER" parallel-jobs --state-file missing-state.json)"
assert_eq "$state_error_py_status" "1" "state missing-file python exit code"
assert_eq "$state_error_cs_status" "1" "state missing-file csharp exit code"
assert_contains "$(<"$state_error_py_stderr")" "missing-state.json" "state missing-file python stderr mentions filename"
assert_contains "$(<"$state_error_cs_stderr")" "missing-state.json" "state missing-file csharp stderr mentions filename"
assert_contains "$(<"$state_error_py_stderr")" "FileNotFoundError" "state missing-file python stderr mentions exception type"
assert_contains "$(<"$state_error_cs_stderr")" "Could not find file" "state missing-file csharp stderr mentions file lookup failure"

echo "PASS: state helper parity"

###############################################################################
# Router helper parity
###############################################################################

ROUTER_PY_DIR="${WORK_DIR}/router/python"
ROUTER_CS_DIR="${WORK_DIR}/router/csharp"
mkdir -p "$ROUTER_PY_DIR" "$ROUTER_CS_DIR"
copy_fixture_suite router "$ROUTER_PY_DIR"
copy_fixture_suite router "$ROUTER_CS_DIR"

router_py_stdout="${ROUTER_PY_DIR}/record.out"
router_py_stderr="${ROUTER_PY_DIR}/record.err"
router_cs_stdout="${ROUTER_CS_DIR}/record.out"
router_cs_stderr="${ROUTER_CS_DIR}/record.err"
router_py_status="$(capture_python "$ROUTER_PY_DIR" "$router_py_stdout" "$router_py_stderr" "$PYTHON_ROUTER_HELPER" --registry-file registry.json --ledger-file ledger.json --role impl --complexity-level medium --detected-agents codex,agy,claude --allowlist agy --denylist codex,claude --record)"
router_cs_status="$(capture_csharp "$ROUTER_CS_DIR" "$router_cs_stdout" "$router_cs_stderr" "$C_SHARP_ROUTER_HELPER" --registry-file registry.json --ledger-file ledger.json --role impl --complexity-level medium --detected-agents codex,agy,claude --allowlist agy --denylist codex,claude --record)"
assert_eq "$router_py_status" "$router_cs_status" "router record exit code parity"
compare_json_files "$router_py_stdout" "$router_cs_stdout" "router record stdout parity" selected_at
compare_json_files "${ROUTER_PY_DIR}/ledger.json" "${ROUTER_CS_DIR}/ledger.json" "router record ledger parity" selected_at

router_error_py_stdout="${ROUTER_PY_DIR}/malformed-registry.out"
router_error_py_stderr="${ROUTER_PY_DIR}/malformed-registry.err"
router_error_cs_stdout="${ROUTER_CS_DIR}/malformed-registry.out"
router_error_cs_stderr="${ROUTER_CS_DIR}/malformed-registry.err"
printf '{invalid json' >"${ROUTER_PY_DIR}/malformed-registry.json"
printf '{invalid json' >"${ROUTER_CS_DIR}/malformed-registry.json"
router_error_py_status="$(capture_python "$ROUTER_PY_DIR" "$router_error_py_stdout" "$router_error_py_stderr" "$PYTHON_ROUTER_HELPER" --registry-file malformed-registry.json --ledger-file ledger.json --role impl --complexity-level medium --detected-agents codex,agy,claude)"
router_error_cs_status="$(capture_csharp "$ROUTER_CS_DIR" "$router_error_cs_stdout" "$router_error_cs_stderr" "$C_SHARP_ROUTER_HELPER" --registry-file malformed-registry.json --ledger-file ledger.json --role impl --complexity-level medium --detected-agents codex,agy,claude)"
assert_eq "$router_error_py_status" "$router_error_cs_status" "router malformed-registry exit code parity"
assert_eq "$router_error_py_status" "2" "router malformed-registry python exit code"
assert_eq "$router_error_cs_status" "2" "router malformed-registry csharp exit code"
assert_contains "$(<"$router_error_py_stderr")" "invalid JSON in malformed-registry.json" "router malformed-registry python stderr mentions invalid JSON"
assert_contains "$(<"$router_error_cs_stderr")" "invalid JSON in malformed-registry.json" "router malformed-registry csharp stderr mentions invalid JSON"

echo "PASS: router helper parity"

###############################################################################
# Artifact helper parity
###############################################################################

ARTIFACTS_PY_DIR="${WORK_DIR}/artifacts/python"
ARTIFACTS_CS_DIR="${WORK_DIR}/artifacts/csharp"
mkdir -p "$ARTIFACTS_PY_DIR" "$ARTIFACTS_CS_DIR"
copy_fixture_suite artifacts "$ARTIFACTS_PY_DIR"
copy_fixture_suite artifacts "$ARTIFACTS_CS_DIR"

artifact_valid_py_stdout="${ARTIFACTS_PY_DIR}/failure-valid.out"
artifact_valid_py_stderr="${ARTIFACTS_PY_DIR}/failure-valid.err"
artifact_valid_cs_stdout="${ARTIFACTS_CS_DIR}/failure-valid.out"
artifact_valid_cs_stderr="${ARTIFACTS_CS_DIR}/failure-valid.err"
artifact_valid_py_status="$(capture_python "$ARTIFACTS_PY_DIR" "$artifact_valid_py_stdout" "$artifact_valid_py_stderr" "$PYTHON_ARTIFACTS_HELPER" failure-reason --role complexity --issue 7 --result-file valid-complexity-result.json)"
artifact_valid_cs_status="$(capture_csharp "$ARTIFACTS_CS_DIR" "$artifact_valid_cs_stdout" "$artifact_valid_cs_stderr" "$C_SHARP_ARTIFACTS_HELPER" failure-reason --role complexity --issue 7 --result-file valid-complexity-result.json)"
assert_eq "$artifact_valid_py_status" "$artifact_valid_cs_status" "artifact valid failure-reason exit code parity"
compare_text_files "$artifact_valid_py_stdout" "$artifact_valid_cs_stdout" "artifact valid failure-reason stdout parity"
compare_text_files "$artifact_valid_py_stderr" "$artifact_valid_cs_stderr" "artifact valid failure-reason stderr parity"

printf '{invalid json' >"${ARTIFACTS_PY_DIR}/malformed-result.json"
printf '{invalid json' >"${ARTIFACTS_CS_DIR}/malformed-result.json"
artifact_malformed_py_stdout="${ARTIFACTS_PY_DIR}/failure-malformed.out"
artifact_malformed_py_stderr="${ARTIFACTS_PY_DIR}/failure-malformed.err"
artifact_malformed_cs_stdout="${ARTIFACTS_CS_DIR}/failure-malformed.out"
artifact_malformed_cs_stderr="${ARTIFACTS_CS_DIR}/failure-malformed.err"
artifact_malformed_py_status="$(capture_python "$ARTIFACTS_PY_DIR" "$artifact_malformed_py_stdout" "$artifact_malformed_py_stderr" "$PYTHON_ARTIFACTS_HELPER" failure-reason --role complexity --issue 7 --result-file malformed-result.json)"
artifact_malformed_cs_status="$(capture_csharp "$ARTIFACTS_CS_DIR" "$artifact_malformed_cs_stdout" "$artifact_malformed_cs_stderr" "$C_SHARP_ARTIFACTS_HELPER" failure-reason --role complexity --issue 7 --result-file malformed-result.json)"
assert_eq "$artifact_malformed_py_status" "$artifact_malformed_cs_status" "artifact malformed failure-reason exit code parity"
compare_text_files "$artifact_malformed_py_stdout" "$artifact_malformed_cs_stdout" "artifact malformed failure-reason stdout parity"
compare_text_files "$artifact_malformed_py_stderr" "$artifact_malformed_cs_stderr" "artifact malformed failure-reason stderr parity"

artifact_missing_done_py_stdout="${ARTIFACTS_PY_DIR}/synthesize-missing-done.out"
artifact_missing_done_py_stderr="${ARTIFACTS_PY_DIR}/synthesize-missing-done.err"
artifact_missing_done_cs_stdout="${ARTIFACTS_CS_DIR}/synthesize-missing-done.out"
artifact_missing_done_cs_stderr="${ARTIFACTS_CS_DIR}/synthesize-missing-done.err"
rm -f "${ARTIFACTS_PY_DIR}/missing-done-result.json" "${ARTIFACTS_CS_DIR}/missing-done-result.json"
artifact_missing_done_py_status="$(capture_python "$ARTIFACTS_PY_DIR" "$artifact_missing_done_py_stdout" "$artifact_missing_done_py_stderr" "$PYTHON_ARTIFACTS_HELPER" synthesize --role complexity --issue 7 --result-file missing-done-result.json --done-file missing.done --log-file logs/complexity.log)"
artifact_missing_done_cs_status="$(capture_csharp "$ARTIFACTS_CS_DIR" "$artifact_missing_done_cs_stdout" "$artifact_missing_done_cs_stderr" "$C_SHARP_ARTIFACTS_HELPER" synthesize --role complexity --issue 7 --result-file missing-done-result.json --done-file missing.done --log-file logs/complexity.log)"
assert_eq "$artifact_missing_done_py_status" "$artifact_missing_done_cs_status" "artifact missing-done synthesize exit code parity"
assert_eq "$artifact_missing_done_py_status" "1" "artifact missing-done synthesize exit code"
assert_eq "$(cat "$artifact_missing_done_py_stdout")" "" "artifact missing-done synthesize python stdout"
assert_eq "$(cat "$artifact_missing_done_cs_stdout")" "" "artifact missing-done synthesize csharp stdout"
assert_eq "$(cat "$artifact_missing_done_py_stderr")" "" "artifact missing-done synthesize python stderr"
assert_eq "$(cat "$artifact_missing_done_cs_stderr")" "" "artifact missing-done synthesize csharp stderr"
assert_file_absent "${ARTIFACTS_PY_DIR}/missing-done-result.json" "artifact missing-done python output file absent"
assert_file_absent "${ARTIFACTS_CS_DIR}/missing-done-result.json" "artifact missing-done csharp output file absent"

cp "${ARTIFACTS_PY_DIR}/valid-complexity-result.json" "${ARTIFACTS_PY_DIR}/cycle-1-result.json"
cp "${ARTIFACTS_CS_DIR}/valid-complexity-result.json" "${ARTIFACTS_CS_DIR}/cycle-1-result.json"
artifact_retry_py_stdout="${ARTIFACTS_PY_DIR}/synthesize-retry.out"
artifact_retry_py_stderr="${ARTIFACTS_PY_DIR}/synthesize-retry.err"
artifact_retry_cs_stdout="${ARTIFACTS_CS_DIR}/synthesize-retry.out"
artifact_retry_cs_stderr="${ARTIFACTS_CS_DIR}/synthesize-retry.err"
artifact_retry_py_status="$(capture_python "$ARTIFACTS_PY_DIR" "$artifact_retry_py_stdout" "$artifact_retry_py_stderr" "$PYTHON_ARTIFACTS_HELPER" synthesize --role complexity --issue 7 --result-file cycle-1-attempt-1-result.json --done-file retry.done --log-file logs/complexity.log)"
artifact_retry_cs_status="$(capture_csharp "$ARTIFACTS_CS_DIR" "$artifact_retry_cs_stdout" "$artifact_retry_cs_stderr" "$C_SHARP_ARTIFACTS_HELPER" synthesize --role complexity --issue 7 --result-file cycle-1-attempt-1-result.json --done-file retry.done --log-file logs/complexity.log)"
assert_eq "$artifact_retry_py_status" "$artifact_retry_cs_status" "artifact canonical retry synthesize exit code parity"
assert_eq "$artifact_retry_py_status" "0" "artifact canonical retry synthesize exit code"
compare_json_files "${ARTIFACTS_PY_DIR}/cycle-1-attempt-1-result.json" "${ARTIFACTS_CS_DIR}/cycle-1-attempt-1-result.json" "artifact canonical retry result parity"
compare_text_files "$artifact_retry_py_stdout" "$artifact_retry_cs_stdout" "artifact canonical retry stdout parity"
compare_text_files "$artifact_retry_py_stderr" "$artifact_retry_cs_stderr" "artifact canonical retry stderr parity"

echo "PASS: artifact helper parity"

###############################################################################
# GitHub update helper parity
###############################################################################

GITHUB_PY_DIR="${WORK_DIR}/github/python"
GITHUB_CS_DIR="${WORK_DIR}/github/csharp"
mkdir -p "$GITHUB_PY_DIR" "$GITHUB_CS_DIR"
copy_fixture_suite github-update "$GITHUB_PY_DIR"
copy_fixture_suite github-update "$GITHUB_CS_DIR"

github_render_py_stdout="${GITHUB_PY_DIR}/render-comment.out"
github_render_py_stderr="${GITHUB_PY_DIR}/render-comment.err"
github_render_cs_stdout="${GITHUB_CS_DIR}/render-comment.out"
github_render_cs_stderr="${GITHUB_CS_DIR}/render-comment.err"
github_render_py_status="$(capture_python "$GITHUB_PY_DIR" "$github_render_py_stdout" "$github_render_py_stderr" "$PYTHON_GITHUB_HELPER" render-comment --outcome blocked --report-file report-blocked.json)"
github_render_cs_status="$(capture_csharp "$GITHUB_CS_DIR" "$github_render_cs_stdout" "$github_render_cs_stderr" "$C_SHARP_GITHUB_HELPER" render-comment --outcome blocked --report-file report-blocked.json)"
assert_eq "$github_render_py_status" "$github_render_cs_status" "github render-comment exit code parity"
compare_text_files "$github_render_py_stdout" "$github_render_cs_stdout" "github render-comment stdout parity"
compare_text_files "$github_render_py_stderr" "$github_render_cs_stderr" "github render-comment stderr parity"

github_update_py_stdout="${GITHUB_PY_DIR}/update.out"
github_update_py_stderr="${GITHUB_PY_DIR}/update.err"
github_update_cs_stdout="${GITHUB_CS_DIR}/update.out"
github_update_cs_stderr="${GITHUB_CS_DIR}/update.err"
github_update_py_status="$(RUN_WITH_IT_GITHUB_UPDATES=0 capture_python "$GITHUB_PY_DIR" "$github_update_py_stdout" "$github_update_py_stderr" "$PYTHON_GITHUB_HELPER" update --state-file state.json --run-root . --issue 42 --outcome blocked --report-file report-blocked.json)"
github_update_cs_status="$(RUN_WITH_IT_GITHUB_UPDATES=0 capture_csharp "$GITHUB_CS_DIR" "$github_update_cs_stdout" "$github_update_cs_stderr" "$C_SHARP_GITHUB_HELPER" update --state-file state.json --run-root . --issue 42 --outcome blocked --report-file report-blocked.json)"
assert_eq "$github_update_py_status" "$github_update_cs_status" "github update disabled exit code parity"
compare_text_files "$github_update_py_stdout" "$github_update_cs_stdout" "github update disabled stdout parity"
compare_text_files "$github_update_py_stderr" "$github_update_cs_stderr" "github update disabled stderr parity"
compare_json_files "${GITHUB_PY_DIR}/state.json" "${GITHUB_CS_DIR}/state.json" "github update disabled state parity" github_updated_at

github_error_py_stdout="${GITHUB_PY_DIR}/missing-state.out"
github_error_py_stderr="${GITHUB_PY_DIR}/missing-state.err"
github_error_cs_stdout="${GITHUB_CS_DIR}/missing-state.out"
github_error_cs_stderr="${GITHUB_CS_DIR}/missing-state.err"
github_error_py_status="$(RUN_WITH_IT_GITHUB_UPDATES=0 capture_python "$GITHUB_PY_DIR" "$github_error_py_stdout" "$github_error_py_stderr" "$PYTHON_GITHUB_HELPER" update --state-file missing-state.json --run-root . --issue 42 --outcome blocked --report-file report-blocked.json)"
github_error_cs_status="$(RUN_WITH_IT_GITHUB_UPDATES=0 capture_csharp "$GITHUB_CS_DIR" "$github_error_cs_stdout" "$github_error_cs_stderr" "$C_SHARP_GITHUB_HELPER" update --state-file missing-state.json --run-root . --issue 42 --outcome blocked --report-file report-blocked.json)"
assert_eq "$github_error_py_status" "$github_error_cs_status" "github missing-state exit code parity"
assert_eq "$github_error_py_status" "1" "github missing-state python exit code"
assert_eq "$github_error_cs_status" "1" "github missing-state csharp exit code"
assert_contains "$(<"$github_error_py_stderr")" "missing-state.json" "github missing-state python stderr mentions filename"
assert_contains "$(<"$github_error_cs_stderr")" "missing-state.json" "github missing-state csharp stderr mentions filename"

echo "PASS: GitHub update helper parity"

###############################################################################
# PR body helper parity
###############################################################################

PR_PY_DIR="${WORK_DIR}/pr-body/python"
PR_CS_DIR="${WORK_DIR}/pr-body/csharp"
mkdir -p "$PR_PY_DIR" "$PR_CS_DIR"
copy_fixture_suite pr-body "$PR_PY_DIR"
copy_fixture_suite pr-body "$PR_CS_DIR"

pr_body_py_stdout="${PR_PY_DIR}/render.out"
pr_body_py_stderr="${PR_PY_DIR}/render.err"
pr_body_cs_stdout="${PR_CS_DIR}/render.out"
pr_body_cs_stderr="${PR_CS_DIR}/render.err"
pr_body_py_status="$(capture_python "$PR_PY_DIR" "$pr_body_py_stdout" "$pr_body_py_stderr" "$PYTHON_PR_BODY_HELPER" render --state-file state.json)"
pr_body_cs_status="$(capture_csharp "$PR_CS_DIR" "$pr_body_cs_stdout" "$pr_body_cs_stderr" "$C_SHARP_PR_BODY_HELPER" render --state-file state.json)"
assert_eq "$pr_body_py_status" "$pr_body_cs_status" "pr body render exit code parity"
compare_text_files "$pr_body_py_stdout" "$pr_body_cs_stdout" "pr body render stdout parity"
compare_text_files "$pr_body_py_stderr" "$pr_body_cs_stderr" "pr body render stderr parity"

printf '{invalid json' >"${PR_PY_DIR}/malformed-state.json"
printf '{invalid json' >"${PR_CS_DIR}/malformed-state.json"
pr_body_error_py_stdout="${PR_PY_DIR}/malformed.out"
pr_body_error_py_stderr="${PR_PY_DIR}/malformed.err"
pr_body_error_cs_stdout="${PR_CS_DIR}/malformed.out"
pr_body_error_cs_stderr="${PR_CS_DIR}/malformed.err"
pr_body_error_py_status="$(capture_python "$PR_PY_DIR" "$pr_body_error_py_stdout" "$pr_body_error_py_stderr" "$PYTHON_PR_BODY_HELPER" render --state-file malformed-state.json)"
pr_body_error_cs_status="$(capture_csharp "$PR_CS_DIR" "$pr_body_error_cs_stdout" "$pr_body_error_cs_stderr" "$C_SHARP_PR_BODY_HELPER" render --state-file malformed-state.json)"
assert_eq "$pr_body_error_py_status" "$pr_body_error_cs_status" "pr body malformed state exit code parity"
assert_eq "$pr_body_error_py_status" "1" "pr body malformed state python exit code"
assert_eq "$pr_body_error_cs_status" "1" "pr body malformed state csharp exit code"
assert_contains "$(<"$pr_body_error_py_stderr")" "error: failed to load state file malformed-state.json" "pr body malformed python stderr mentions failure"
assert_contains "$(<"$pr_body_error_cs_stderr")" "error: failed to load state file malformed-state.json" "pr body malformed csharp stderr mentions failure"

echo "PASS: PR body helper parity"
echo "PASS: run-with-it helper parity suite"
