#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACT_HELPER="${ROOT_DIR}/assets/python/run-with-it-artifacts.py"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_eq() {
  local actual="$1"
  local expected="$2"
  local message="$3"
  [[ "${actual}" == "${expected}" ]] || fail "${message} (expected: ${expected}, got: ${actual})"
}

WORK_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

review_reason() {
  python3 "${ARTIFACT_HELPER}" failure-reason \
    --role review \
    --issue 77 \
    --result-file "${WORK_DIR}/cycle-1-status.json" \
    --done-file "${WORK_DIR}/cycle-1.done"
}

write_review_artifacts() {
  local status_json="$1"
  local instructions_json="$2"
  printf '%s\n' "${status_json}" > "${WORK_DIR}/cycle-1-status.json"
  printf '%s\n' "${instructions_json}" > "${WORK_DIR}/cycle-1-instructions.json"
}

valid_comment='{"id":"R001","file":"assets/prompt.md","line":42,"severity":"warning","category":"security","blocking":true,"fix":"Validate shell arguments before interpolation.","evidence":"The diff passes issue text into a shell command.","expected_change":"Use argv arrays or strict quoting for subprocess calls.","verification":"Run the shell-injection regression test."}'

write_review_artifacts \
  '{"verdict":"revise","comment_count":1,"nitpick_only":false}' \
  '{"verdict":"revise","summary":"Security review found one blocking shell-injection risk.","comments":['"${valid_comment}"'],"blocking_reasons":[]}'
assert_eq "$(review_reason)" "" "structured revise artifact is valid"

missing_category_comment='{"id":"R001","file":"assets/prompt.md","line":42,"severity":"warning","blocking":true,"fix":"Validate shell arguments before interpolation.","evidence":"The diff passes issue text into a shell command.","expected_change":"Use argv arrays or strict quoting for subprocess calls.","verification":"Run the shell-injection regression test."}'
write_review_artifacts \
  '{"verdict":"revise","comment_count":1,"nitpick_only":false}' \
  '{"verdict":"revise","summary":"Missing category should invalidate the actionable comment.","comments":['"${missing_category_comment}"'],"blocking_reasons":[]}'
assert_eq "$(review_reason)" "invalid-review-instructions-artifact" "actionable comments require category metadata"

write_review_artifacts \
  '{"verdict":"revise","comment_count":2,"nitpick_only":false}' \
  '{"verdict":"revise","summary":"Comment count does not match comments.","comments":['"${valid_comment}"'],"blocking_reasons":[]}'
assert_eq "$(review_reason)" "review-comment-count-mismatch" "review status comment_count must equal instruction comments length"

nitpick_comment='{"id":"R002","file":"assets/prompt.md","line":50,"severity":"info","category":"maintainability","blocking":false,"fix":"[nitpick] Rename the local variable for clarity.","evidence":"The name is ambiguous but behavior is unchanged.","expected_change":"Rename the local variable only.","verification":"Run the existing prompt contract test."}'
write_review_artifacts \
  '{"verdict":"approve","comment_count":1,"nitpick_only":false}' \
  '{"verdict":"approve","summary":"Only a nitpick remains.","comments":['"${nitpick_comment}"'],"blocking_reasons":[]}'
assert_eq "$(review_reason)" "review-nitpick-only-mismatch" "review status nitpick_only must match comment details"

approval_warning_comment='{"id":"R003","file":"assets/prompt.md","line":60,"severity":"warning","category":"correctness","blocking":false,"fix":"Handle the missing acceptance criterion.","evidence":"The diff does not implement the required status field.","expected_change":"Add the missing field.","verification":"Run the acceptance test."}'
write_review_artifacts \
  '{"verdict":"approve","comment_count":1,"nitpick_only":false}' \
  '{"verdict":"approve","summary":"Approve cannot carry warning comments.","comments":['"${approval_warning_comment}"'],"blocking_reasons":[]}'
assert_eq "$(review_reason)" "invalid-review-instructions-artifact" "approve artifacts may only contain nitpick comments"

echo "PASS: run-with-it artifact validation"
