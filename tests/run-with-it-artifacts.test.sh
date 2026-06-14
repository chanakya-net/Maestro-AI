#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACT_HELPER="${ROOT_DIR}/assets/run-with-it-artifacts.py"

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

failure_class() {
  python3 "${ARTIFACT_HELPER}" failure-class \
    --role impl \
    --issue 567 \
    --result-file "${WORK_DIR}/cycle-1-result.json" \
    --done-file "${WORK_DIR}/cycle-1.done" \
    --log-file "${WORK_DIR}/cycle-1.log"
}

# Missing log → capability (no infrastructure signal observed)
rm -f "${WORK_DIR}/cycle-1.log"
assert_eq "$(failure_class)" "capability" "missing log defaults to capability"

# Ordinary worker output with no availability marker → capability
printf 'STATUS|type=dispatch-start|issue=567|role=impl\nSTATUS|type=worker-done|issue=567|role=impl|status=failed\n' > "${WORK_DIR}/cycle-1.log"
assert_eq "$(failure_class)" "capability" "ordinary failure is a capability failure"

# Runner emitted agent-unavailable (auth/quota) → infrastructure
printf 'STATUS|type=dispatch-start|issue=567|role=impl\nSTATUS|type=agent-unavailable|issue=567|role=impl|agent=claude|model=claude-sonnet-4-6|reason=auth|action=exclude-route\n' > "${WORK_DIR}/cycle-1.log"
assert_eq "$(failure_class)" "infrastructure" "agent-unavailable is an infrastructure failure"

# A STALE bootstrap marker must NOT classify a later real failure as infrastructure.
# The foreground bootstrap retry reuses the same log; if that retry then fails for a
# real capability reason (no current agent-unavailable), the inherited bootstrap
# marker must not exempt it from the fallback budget.
printf 'STATUS|type=dispatch-bootstrap-failed|issue=567|role=impl|reason=dispatcher-exited-before-runner-pid\nSTATUS|type=agent-start|issue=567|role=impl|agent=codex|model=gpt-5.4-mini\nSTATUS|type=worker-done|issue=567|role=impl|status=failed\n' > "${WORK_DIR}/cycle-1.log"
assert_eq "$(failure_class)" "capability" "stale bootstrap marker does not mask a real capability failure"

# But a current agent-unavailable on the reused log is still infrastructure.
printf 'STATUS|type=dispatch-bootstrap-failed|issue=567|role=impl|reason=dispatcher-exited-before-runner-pid\nSTATUS|type=agent-start|issue=567|role=impl|agent=claude|model=claude-sonnet-4-6\nSTATUS|type=agent-unavailable|issue=567|role=impl|agent=claude|model=claude-sonnet-4-6|reason=quota|action=exclude-route\n' > "${WORK_DIR}/cycle-1.log"
assert_eq "$(failure_class)" "infrastructure" "current agent-unavailable still classifies as infrastructure"

# --- Fix C: dispatcher must not silently auto-approve dropped review comments ---
synth_review() {
  python3 "${ARTIFACT_HELPER}" synthesize \
    --role review \
    --issue 77 \
    --result-file "${WORK_DIR}/synth-status.json" \
    --done-file "${WORK_DIR}/synth.done"
}
printf 'DONE\n' > "${WORK_DIR}/synth.done"

# approve + comment_count=0 + missing instructions → safe to synthesize an empty approve.
printf '%s\n' '{"verdict":"approve","comment_count":0,"nitpick_only":false}' > "${WORK_DIR}/synth-status.json"
rm -f "${WORK_DIR}/synth-instructions.json"
set +e; synth_review; synth_rc=$?; set -e
assert_eq "${synth_rc}" "0" "synthesize approves a zero-comment status with missing instructions"
[[ -f "${WORK_DIR}/synth-instructions.json" ]] || fail "expected synthesized approve instructions for zero-comment status"

# approve + comment_count>0 + missing instructions → must NOT auto-approve (retry instead).
printf '%s\n' '{"verdict":"approve","comment_count":2,"nitpick_only":true}' > "${WORK_DIR}/synth-status.json"
rm -f "${WORK_DIR}/synth-instructions.json"
set +e; synth_review; synth_rc=$?; set -e
assert_eq "${synth_rc}" "1" "synthesize refuses to auto-approve when comment_count>0 with missing instructions"
[[ ! -f "${WORK_DIR}/synth-instructions.json" ]] || fail "must not fabricate approve instructions when comments were reported"

# --- Fix E: a verified no-op implementation is accepted as success ---
impl_reason() {
  python3 "${ARTIFACT_HELPER}" failure-reason \
    --role impl \
    --issue 88 \
    --result-file "${WORK_DIR}/impl-result.json"
}
printf '%s\n' '{"schema_version":1,"issue":"88","role":"impl","status":"success","no_op":true,"commit_sha":"NONE","files_committed":[],"verification":{"passed":true,"commands":["bun test"]}}' > "${WORK_DIR}/impl-result.json"
assert_eq "$(impl_reason)" "" "verified no-op with passing verification is accepted as success"

printf '%s\n' '{"schema_version":1,"issue":"88","role":"impl","status":"success","no_op":true,"commit_sha":"NONE","files_committed":[],"verification":{"passed":false}}' > "${WORK_DIR}/impl-result.json"
assert_eq "$(impl_reason)" "verified-no-op-requires-passing-verification" "no-op requires passing verification"

# --- Fix B: stall salvage commits a dirty tree only with --from-stall ---
SALVAGE_REPO="${WORK_DIR}/salvage-repo"
mkdir -p "${SALVAGE_REPO}"
git -C "${SALVAGE_REPO}" init -q
git -C "${SALVAGE_REPO}" config user.email t@t.t
git -C "${SALVAGE_REPO}" config user.name t
printf 'base\n' > "${SALVAGE_REPO}/base.txt"
git -C "${SALVAGE_REPO}" add -A
git -C "${SALVAGE_REPO}" commit -q -m base
SALVAGE_BASE="$(git -C "${SALVAGE_REPO}" rev-parse HEAD)"
printf 'work in progress\n' > "${SALVAGE_REPO}/feature.txt"   # uncommitted dirty work

salvage() {
  local from_stall="$1"
  rm -f "${WORK_DIR}/salvage-result.json"
  python3 "${ARTIFACT_HELPER}" synthesize \
    --role impl --issue 99 \
    --result-file "${WORK_DIR}/salvage-result.json" \
    --done-file "${WORK_DIR}/salvage.done" \
    --repo-root "${SALVAGE_REPO}" \
    --pre-spawn-head "${SALVAGE_BASE}" ${from_stall}
}

# Without --from-stall (clean-exit path, no done sentinel) → no salvage.
rm -f "${WORK_DIR}/salvage.done"
set +e; salvage ""; salvage_rc=$?; set -e
assert_eq "${salvage_rc}" "1" "clean-exit synthesize does not salvage a dirty tree without a done sentinel"

# With --from-stall → commit the dirty tree and synthesize a success result.
set +e; salvage "--from-stall"; salvage_rc=$?; set -e
assert_eq "${salvage_rc}" "0" "stall salvage commits the dirty tree and writes a result"
git -C "${SALVAGE_REPO}" diff --quiet && git -C "${SALVAGE_REPO}" diff --cached --quiet || fail "stall salvage should leave a clean tree after committing"
salvaged_head="$(git -C "${SALVAGE_REPO}" rev-parse HEAD)"
[[ "${salvaged_head}" != "${SALVAGE_BASE}" ]] || fail "stall salvage should advance HEAD with a new commit"

echo "PASS: run-with-it artifact validation"
