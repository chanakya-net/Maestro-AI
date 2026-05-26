#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POOL_RUNNER="${ROOT_DIR}/assets/run-with-it-pool.sh"

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

WORK_DIR="$(mktemp -d)"
WORK_DIR_REAL="$(cd "${WORK_DIR}" && pwd -P)"
cleanup() {
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

STATE_FILE="${WORK_DIR}/.run-with-it/main-state.json"
STATUS_FILE="${WORK_DIR}/.run-with-it/status/current.txt"
EVENTS_LOG="${WORK_DIR}/.run-with-it/status/events.log"
MAIN_LOG="${WORK_DIR}/.run-with-it/main/main.log"
mkdir -p "${WORK_DIR}/.run-with-it/contexts" "$(dirname "${STATE_FILE}")"
printf '# issue 101 context\n' > "${WORK_DIR}/.run-with-it/contexts/sub-101.md"
printf '# issue 102 context\n' > "${WORK_DIR}/.run-with-it/contexts/sub-102.md"

cat > "${STATE_FILE}" <<JSON
{
  "schema_version": 2,
  "execution_plan": {
    "parallel_jobs": 2,
    "topo_order": [101, 102]
  },
  "issue_registry": {
    "101": {
      "status": "pending",
      "deps": [],
      "context_file": "${WORK_DIR}/.run-with-it/contexts/sub-101.md"
    },
    "102": {
      "status": "pending",
      "deps": [],
      "context_file": "${WORK_DIR}/.run-with-it/contexts/sub-102.md"
    }
  },
  "active_pool_issues": [],
  "completed_summaries": [],
  "ledger_rows": []
}
JSON

[[ -f "${POOL_RUNNER}" ]] || fail "run-with-it-pool.sh exists"
[[ -x "${POOL_RUNNER}" ]] || fail "run-with-it-pool.sh is executable"
assert_file_contains "${POOL_RUNNER}" "merge_recovery" "pool runner documents merge recovery as non-terminal"
assert_file_contains "${POOL_RUNNER}" "merge_failed" "pool runner maps merge failed reports to merge recovery"

validate_output="$("${POOL_RUNNER}" \
  --validate-only \
  --asset-root "${ROOT_DIR}/assets" \
  --state-file "${STATE_FILE}" \
  --parallel-jobs 2 \
  --agent codex \
  --model gpt-5.5 \
  --status-file "${STATUS_FILE}" \
  --events-log "${EVENTS_LOG}" \
  --main-log "${MAIN_LOG}")"

assert_contains "${validate_output}" "STATUS|type=pool-ready|parallel_jobs=2|ready=2" "validate-only reports pool readiness"
assert_file_contains "${STATUS_FILE}" "STATUS|type=pool-ready|parallel_jobs=2|ready=2" "validate-only writes status bus"
assert_file_contains "${EVENTS_LOG}" "STATUS|type=pool-ready|parallel_jobs=2|ready=2" "validate-only appends events log"

dry_output="$("${POOL_RUNNER}" \
  --dry-run \
  --asset-root "${ROOT_DIR}/assets" \
  --state-file "${STATE_FILE}" \
  --parallel-jobs 2 \
  --agent codex \
  --model gpt-5.5 \
  --status-file "${STATUS_FILE}" \
  --events-log "${EVENTS_LOG}" \
  --main-log "${MAIN_LOG}")"

assert_contains "${dry_output}" "run-with-it-dispatch.sh" "dry-run uses shared dispatcher"
assert_contains "${dry_output}" "--role sub-coord" "dry-run dispatches sub-coordinator role"
assert_contains "${dry_output}" "--issue 101" "dry-run queues first ready issue"
assert_contains "${dry_output}" "--issue 102" "dry-run queues second ready issue"
assert_contains "${dry_output}" "--context-file ${WORK_DIR}/.run-with-it/contexts/sub-101.md" "dry-run forwards persisted context path"
assert_contains "${dry_output}" "--issue-dir ${WORK_DIR_REAL}/.run-with-it/issues/101" "dry-run forwards issue-scoped sub-coordinator folder"
assert_contains "${dry_output}" "--log-file ${WORK_DIR_REAL}/.run-with-it/issues/101/sub-coordinator.log" "dry-run places sub-coordinator log in issue folder"
assert_contains "${dry_output}" "--result-file ${WORK_DIR_REAL}/.run-with-it/issues/101/report.json" "dry-run places compact report in issue folder"

printf '# issue 201 context\n' > "${WORK_DIR}/.run-with-it/contexts/sub-201.md"
printf '# issue 202 context\n' > "${WORK_DIR}/.run-with-it/contexts/sub-202.md"
printf '# issue 203 context\n' > "${WORK_DIR}/.run-with-it/contexts/sub-203.md"
printf '# issue 204 context\n' > "${WORK_DIR}/.run-with-it/contexts/sub-204.md"

cat > "${STATE_FILE}" <<JSON
{
  "schema_version": 4,
  "execution_plan": {
    "parallel_jobs": 4,
    "topo_order": [201, 202, 203, 204]
  },
  "issue_registry": {
    "201": {
      "status": "completed",
      "deps": [],
      "context_file": "${WORK_DIR}/.run-with-it/contexts/sub-201.md"
    },
    "202": {
      "status": "merge_recovery",
      "deps": [],
      "context_file": "${WORK_DIR}/.run-with-it/contexts/sub-202.md"
    },
    "203": {
      "status": "pending",
      "deps": [202],
      "context_file": "${WORK_DIR}/.run-with-it/contexts/sub-203.md"
    },
    "204": {
      "status": "pending",
      "deps": [201],
      "context_file": "${WORK_DIR}/.run-with-it/contexts/sub-204.md"
    }
  },
  "active_pool_issues": [],
  "completed_summaries": [],
  "ledger_rows": []
}
JSON

dependency_output="$("${POOL_RUNNER}" \
  --dry-run \
  --asset-root "${ROOT_DIR}/assets" \
  --state-file "${STATE_FILE}" \
  --parallel-jobs 4 \
  --agent codex \
  --model gpt-5.5 \
  --status-file "${STATUS_FILE}" \
  --events-log "${EVENTS_LOG}" \
  --main-log "${MAIN_LOG}")"

assert_contains "${dependency_output}" "--issue 204" "dry-run queues issue whose dependency is completed"
if [[ "${dependency_output}" == *"--issue 203"* ]]; then
  fail "dry-run must not queue issue whose dependency is in merge_recovery"
fi

echo "PASS: run-with-it pool contract"
