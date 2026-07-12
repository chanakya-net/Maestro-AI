#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POOL_RUNNER="${ROOT_DIR}/assets/run-with-it-pool.sh"
MAIN_RULES="${ROOT_DIR}/assets/main-orchestrator-rules.md"
SUB_PROMPT="${ROOT_DIR}/assets/sub-coordinator-prompt.md"
COORDINATOR_RULES="${ROOT_DIR}/assets/coordinator-rules.md"
RUN_WITH_IT_SKILL="${ROOT_DIR}/skills/run-with-it/SKILL.md"
README="${ROOT_DIR}/README.md"

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

assert_file_not_contains() {
  local file="$1"
  local needle="$2"
  local message="$3"
  if grep -Fq -- "$needle" "$file"; then
    fail "${message} (found forbidden: ${needle})"
  fi
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
      "parallel_safe": true,
      "ownership_scope": ["src/issue-101"],
      "context_file": "${WORK_DIR}/.run-with-it/contexts/sub-101.md"
    },
    "102": {
      "status": "pending",
      "deps": [],
      "parallel_safe": true,
      "ownership_scope": ["src/issue-102"],
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
assert_file_contains "${POOL_RUNNER}" 'SUB_COORD_MODEL="${SUB_COORD_MODEL:-gpt-5.6-sol}"' "pool runner directly locks the default Sub-Coordinator model"
assert_file_contains "${POOL_RUNNER}" "merge_recovery" "pool runner documents merge recovery as non-terminal"
assert_file_contains "${POOL_RUNNER}" "merge_failed" "pool runner maps merge failed reports to merge recovery"
assert_file_contains "${POOL_RUNNER}" "analyze-sub-coord-failure" "pool runner analyzes failed sub-coordinators before finalizing"
assert_file_contains "${POOL_RUNNER}" "sub-coord-recovery-wait" "pool runner waits for in-flight worker recovery"
assert_file_contains "${POOL_RUNNER}" "sub-coord-recovery-spawn" "pool runner spawns replacement sub-coordinator"
assert_file_contains "${POOL_RUNNER}" "type=pool-heartbeat" "pool runner emits a periodic liveness heartbeat"
assert_file_contains "${POOL_RUNNER}" "type=pool-slot-fill-failed" "pool runner defers failed spawns instead of dying"
assert_file_contains "${POOL_RUNNER}" "type=pool-slot-fill-abandoned" "pool runner finalizes an issue after bounded spawn failures"
assert_file_contains "${POOL_RUNNER}" 'MAX_SPAWN_BOOTSTRAP_ATTEMPTS' "pool runner bounds spawn bootstrap retries"
assert_file_contains "${POOL_RUNNER}" 'if spawn_issue "$queued_issue"; then' "fill_free_slots guards spawn_issue so set -e cannot kill the supervisor"
assert_file_contains "${ROOT_DIR}/assets/run-with-it-pool.ps1" "type=pool-heartbeat" "ps1 pool runner emits a periodic liveness heartbeat"
assert_file_contains "${ROOT_DIR}/assets/run-with-it-pool.ps1" "type=pool-slot-fill-abandoned" "ps1 pool runner finalizes an issue after bounded spawn failures"
assert_file_contains "${RUN_WITH_IT_SKILL}" 'POOL_HEARTBEAT_SECONDS' "skill documents the pool heartbeat cadence"
assert_file_contains "${RUN_WITH_IT_SKILL}" 'MAX_SPAWN_BOOTSTRAP_ATTEMPTS' "skill documents bounded spawn bootstrap retries"
assert_file_contains "${RUN_WITH_IT_SKILL}" "Collect NEWLY_QUEUED = ALL issues with status=\"pending\" that do not yet have a" "skill Step B assembles contexts for all pending issues, not a slot-sized batch"
assert_file_contains "${RUN_WITH_IT_SKILL}" "Stay attached until every issue is terminal" "skill requires the Main Orchestrator to stay attached until the run is terminal"
assert_file_contains "${MAIN_RULES}" "type=pool-heartbeat" "main rules document the pool heartbeat"
assert_file_contains "${MAIN_RULES}" "Assemble context files for ALL pending issues up front" "main rules require contexts for all pending issues"
assert_file_contains "${MAIN_RULES}" "Stay attached until every issue is terminal" "main rules require staying attached until the run is terminal"
assert_file_contains "${MAIN_RULES}" "sub-state.json" "main rules permit structured sub-coordinator recovery state"
assert_file_contains "${SUB_PROMPT}" "SUB_COORD_RECOVERY_MODE=1" "sub-coordinator prompt documents recovery mode"
assert_file_contains "${SUB_PROMPT}" "artifact-recovery-prompt.md" "sub-coordinator prompt documents artifact recovery worker prompt"
assert_file_contains "${SUB_PROMPT}" "STATUS|type=artifact-recovery-result" "sub-coordinator prompt documents artifact recovery result status"
assert_file_contains "${COORDINATOR_RULES}" "hard-limit-exceeded" "coordinator rules classify hard-limit handoff failures"
assert_file_contains "${SUB_PROMPT}" "hard-limit-exceeded" "sub-coordinator retries hard-limit handoff failures"
assert_file_contains "${RUN_WITH_IT_SKILL}" '| `SUB_COORD_MODEL` | `gpt-5.6-sol` | Model for every Sub-Coordinator (Sub-Coordinators route their own children independently) |' "skill documents the complete Sub-Coordinator-only Sol default"
assert_file_contains "${README}" '| `SUB_COORD_MODEL` | `gpt-5.6-sol` | Model used to run Sub-Coordinators |' "README documents the complete Sub-Coordinator-only Sol default"
assert_file_not_contains "${RUN_WITH_IT_SKILL}" 'gpt-5.6-sol` | Model for child workers' "skill does not document Sol as a child-worker override"
assert_file_not_contains "${README}" 'gpt-5.6-sol` | Model for child workers' "README does not document Sol as a child-worker override"

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
assert_contains "${dry_output}" "--state-file ${WORK_DIR_REAL}/.run-with-it/issues/101/sub-coordinator.state.json" "dry-run passes sub-coordinator dispatcher state file"
assert_contains "${dry_output}" "--detach" "dry-run launches sub-coordinator dispatcher in detached mode"
assert_contains "${dry_output}" "--model gpt-5.6-sol" "pool defaults Sub-Coordinators to Sol"

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
assert_contains "${dependency_output}" "--model gpt-5.5" "explicit GPT-5.5 Sub-Coordinator override remains valid"
if [[ "${dependency_output}" == *"--issue 203"* ]]; then
  fail "dry-run must not queue issue whose dependency is in merge_recovery"
fi

# An issue whose recorded context file is missing on disk is waiting-context:
# it must be held back from dispatch, not crash the pool at spawn time.
printf '# issue 301 context\n' > "${WORK_DIR}/.run-with-it/contexts/sub-301.md"
cat > "${STATE_FILE}" <<JSON
{
  "schema_version": 4,
  "execution_plan": {
    "parallel_jobs": 2,
    "topo_order": [301, 302]
  },
  "issue_registry": {
    "301": {
      "status": "pending",
      "deps": [],
      "context_file": "${WORK_DIR}/.run-with-it/contexts/sub-301.md"
    },
    "302": {
      "status": "pending",
      "deps": [],
      "context_file": "${WORK_DIR}/.run-with-it/contexts/sub-302-missing.md"
    }
  },
  "active_pool_issues": [],
  "completed_summaries": [],
  "ledger_rows": []
}
JSON

missing_context_output="$("${POOL_RUNNER}" \
  --dry-run \
  --asset-root "${ROOT_DIR}/assets" \
  --state-file "${STATE_FILE}" \
  --parallel-jobs 2 \
  --agent codex \
  --status-file "${STATUS_FILE}" \
  --events-log "${EVENTS_LOG}" \
  --main-log "${MAIN_LOG}")"

assert_contains "${missing_context_output}" "--issue 301" "dry-run queues the issue whose context file exists"
if [[ "${missing_context_output}" == *"--issue 302"* ]]; then
  fail "dry-run must not queue an issue whose recorded context file is missing on disk"
fi

echo "PASS: run-with-it pool contract"
