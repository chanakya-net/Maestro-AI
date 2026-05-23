#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DISPATCHER="${ROOT_DIR}/assets/run-with-it-dispatch.sh"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"
  [[ "${haystack}" == *"${needle}"* ]] || fail "${message} (missing: ${needle})"
}

assert_file_contains() {
  local file="$1"
  local needle="$2"
  local message="$3"
  grep -Fq -- "$needle" "$file" || fail "${message} (missing: ${needle})"
}

assert_executable() {
  local file="$1"
  [[ -x "$file" ]] || fail "$file is executable"
}

WORK_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

CONTEXT_FILE="${WORK_DIR}/context.md"
PROMPT_FILE="${ROOT_DIR}/assets/prompt.md"
LOG_FILE="${WORK_DIR}/.run-with-it/impl/issue-42-impl-cycle-1.log"
DONE_FILE="${WORK_DIR}/.run-with-it/done/issue-42-impl-cycle-1.done"
RESULT_FILE="${WORK_DIR}/.run-with-it/impl/issue-42-impl-cycle-1-result.json"
STATUS_FILE="${WORK_DIR}/.run-with-it/status/current.txt"
EVENTS_LOG="${WORK_DIR}/.run-with-it/status/events.log"

printf '# Context\n' > "${CONTEXT_FILE}"
mkdir -p "$(dirname "${RESULT_FILE}")"
printf '{"outcome":"completed"}\n' > "${RESULT_FILE}"

[[ -f "${DISPATCHER}" ]] || fail "run-with-it-dispatch.sh exists"
assert_executable "${DISPATCHER}"

dry_output="$("${DISPATCHER}" \
  --dry-run \
  --asset-root "${ROOT_DIR}/assets" \
  --role impl \
  --issue 42 \
  --cycle 1 \
  --agent codex \
  --model gpt-5.5 \
  --context-file "${CONTEXT_FILE}" \
  --prompt-file "${PROMPT_FILE}" \
  --log-file "${LOG_FILE}" \
  --done-file "${DONE_FILE}" \
  --result-file "${RESULT_FILE}" \
  --status-file "${STATUS_FILE}" \
  --events-log "${EVENTS_LOG}")"

assert_contains "${dry_output}" "run-agent.sh" "dry-run wraps run-agent"
assert_contains "${dry_output}" "--agent codex" "dry-run forwards agent"
assert_contains "${dry_output}" "--model gpt-5.5" "dry-run forwards model"
assert_contains "${dry_output}" "--context-file ${CONTEXT_FILE}" "dry-run forwards context file"
assert_contains "${dry_output}" "--prompt-file ${PROMPT_FILE}" "dry-run forwards prompt file"
assert_contains "${dry_output}" "RUN_WITH_IT_ROLE=impl" "dry-run sets role"
assert_contains "${dry_output}" "RUN_WITH_IT_ISSUE=42" "dry-run sets issue"
assert_contains "${dry_output}" "RUN_WITH_IT_LOG_FILE=${LOG_FILE}" "dry-run sets role log"
assert_contains "${dry_output}" "RUN_WITH_IT_DONE_FILE=${DONE_FILE}" "dry-run sets done file"

validate_output="$("${DISPATCHER}" \
  --validate-only \
  --asset-root "${ROOT_DIR}/assets" \
  --role impl \
  --issue 42 \
  --cycle 1 \
  --agent codex \
  --model gpt-5.5 \
  --context-file "${CONTEXT_FILE}" \
  --prompt-file "${PROMPT_FILE}" \
  --log-file "${LOG_FILE}" \
  --done-file "${DONE_FILE}" \
  --result-file "${RESULT_FILE}" \
  --status-file "${STATUS_FILE}" \
  --events-log "${EVENTS_LOG}")"

assert_contains "${validate_output}" "STATUS|type=dispatch-ready|issue=42|role=impl|cycle=1" "validate-only reports ready"
assert_file_contains "${STATUS_FILE}" "STATUS|type=dispatch-ready|issue=42|role=impl|cycle=1" "validate-only writes status bus"
assert_file_contains "${EVENTS_LOG}" "STATUS|type=dispatch-ready|issue=42|role=impl|cycle=1" "validate-only appends events log"

echo "PASS: run-with-it dispatcher contract"
