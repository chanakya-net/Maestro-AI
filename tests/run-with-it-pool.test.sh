#!/usr/bin/env bash

set -euo pipefail

unset RUN_WITH_IT_DETACHED_CHILD

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POOL_RUNNER="${ROOT_DIR}/assets/scripts/run-with-it-pool.sh"
MAIN_RULES="${ROOT_DIR}/assets/prompts/main-orchestrator-rules.md"
SUB_PROMPT="${ROOT_DIR}/assets/prompts/sub-coordinator-prompt.md"

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
assert_file_contains "${POOL_RUNNER}" "analyze-sub-coord-failure" "pool runner analyzes failed sub-coordinators before finalizing"
assert_file_contains "${POOL_RUNNER}" "sub-coord-recovery-wait" "pool runner waits for in-flight worker recovery"
assert_file_contains "${POOL_RUNNER}" "sub-coord-recovery-spawn" "pool runner spawns replacement sub-coordinator"
assert_file_contains "${MAIN_RULES}" "sub-state.json" "main rules permit structured sub-coordinator recovery state"
assert_file_contains "${SUB_PROMPT}" "SUB_COORD_RECOVERY_MODE=1" "sub-coordinator prompt documents recovery mode"

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

FLAT_ASSET_ROOT="${WORK_DIR}/flat-assets"
mkdir -p "${FLAT_ASSET_ROOT}/prompts" "${FLAT_ASSET_ROOT}/python"
cp "${ROOT_DIR}/assets/scripts/run-with-it-dispatch.sh" "${FLAT_ASSET_ROOT}/run-with-it-dispatch.sh"
cp "${ROOT_DIR}/assets/prompts/sub-coordinator-prompt.md" "${FLAT_ASSET_ROOT}/prompts/"
cp "${ROOT_DIR}/assets/prompts/merge-recovery-prompt.md" "${FLAT_ASSET_ROOT}/prompts/"
cp "${ROOT_DIR}/assets/python/run-with-it-state.py" "${FLAT_ASSET_ROOT}/python/"
cp "${ROOT_DIR}/assets/python/run-with-it-github-update.py" "${FLAT_ASSET_ROOT}/python/"
chmod +x "${FLAT_ASSET_ROOT}/run-with-it-dispatch.sh"

flat_dry_output="$("${POOL_RUNNER}" \
  --dry-run \
  --asset-root "${FLAT_ASSET_ROOT}" \
  --state-file "${STATE_FILE}" \
  --parallel-jobs 1 \
  --agent codex \
  --model gpt-5.5 \
  --status-file "${STATUS_FILE}" \
  --events-log "${EVENTS_LOG}" \
  --main-log "${MAIN_LOG}")"

assert_contains "${flat_dry_output}" "${FLAT_ASSET_ROOT}/run-with-it-dispatch.sh --asset-root ${FLAT_ASSET_ROOT}" "flat Python layout resolves root-level helper scripts"

set +e
cs_reject_output="$(
  RUN_WITH_IT_HELPER_RUNTIME=cs "${POOL_RUNNER}" \
    --dry-run \
    --asset-root "${FLAT_ASSET_ROOT}" \
    --state-file "${STATE_FILE}" \
    --parallel-jobs 1 \
    --agent codex \
    --model gpt-5.5 \
    --status-file "${STATUS_FILE}" \
    --events-log "${EVENTS_LOG}" \
    --main-log "${MAIN_LOG}" \
    2>&1
)"
cs_reject_status="$?"
set -e

[[ "${cs_reject_status}" != "0" ]] || fail "flat C# layout must fail for legacy asset root"
assert_contains "${cs_reject_output}" "missing nested asset layout for helper runtime 'cs'" "flat C# layout fails with nested asset error"

echo "PASS: run-with-it pool contract"

RUNTIME_ALIAS_BIN="${WORK_DIR}/pool-runtime-bins"
mkdir -p "${RUNTIME_ALIAS_BIN}"
RUNTIME_PY_CALLS="${RUNTIME_ALIAS_BIN}/python.calls.log"
RUNTIME_DOTNET_CALLS="${RUNTIME_ALIAS_BIN}/dotnet.calls.log"
export RUNTIME_PY_CALLS
export RUNTIME_DOTNET_CALLS

cat > "${RUNTIME_ALIAS_BIN}/fake-python.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${RUNTIME_PY_CALLS}"
if command -v python3 >/dev/null 2>&1; then
  python3 "$@"
elif command -v python >/dev/null 2>&1; then
  python "$@"
else
  printf '%s\n' "python not available" >&2
  exit 1
fi
SH
cat > "${RUNTIME_ALIAS_BIN}/fake-dotnet.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${RUNTIME_DOTNET_CALLS}"
helper="$1"
command="$2"
if [[ "$helper" == *"run-with-it-state.cs" ]]; then
  if [[ "$command" == "ready-issues" ]]; then
    echo "401 402"
    exit 0
  fi
  if [[ "$command" == "parallel-jobs" ]]; then
    echo "2"
    exit 0
  fi
fi
echo ""
exit 0
SH
chmod +x "${RUNTIME_ALIAS_BIN}/fake-python.sh" "${RUNTIME_ALIAS_BIN}/fake-dotnet.sh"

RUNTIME_ASSET_ROOT="${WORK_DIR}/runtime-alias-assets"
mkdir -p "${RUNTIME_ASSET_ROOT}/prompts" "${RUNTIME_ASSET_ROOT}/scripts" "${RUNTIME_ASSET_ROOT}/python" "${RUNTIME_ASSET_ROOT}/powershell" "${RUNTIME_ASSET_ROOT}/csharp"
cp "${ROOT_DIR}/assets/scripts/run-with-it-dispatch.sh" "${RUNTIME_ASSET_ROOT}/scripts/"
cp "${ROOT_DIR}/assets/prompts/sub-coordinator-prompt.md" "${RUNTIME_ASSET_ROOT}/prompts/"
cp "${ROOT_DIR}/assets/prompts/merge-recovery-prompt.md" "${RUNTIME_ASSET_ROOT}/prompts/"
cp "${ROOT_DIR}/assets/python/run-with-it-state.py" "${RUNTIME_ASSET_ROOT}/python/"
cp "${ROOT_DIR}/assets/python/run-with-it-github-update.py" "${RUNTIME_ASSET_ROOT}/python/"
chmod +x "${RUNTIME_ASSET_ROOT}/scripts/run-with-it-dispatch.sh"
cat > "${RUNTIME_ASSET_ROOT}/csharp/run-with-it-state.cs" <<'CS'
Write-Output "run-with-it-state.cs"
CS
cat > "${RUNTIME_ASSET_ROOT}/csharp/run-with-it-github-update.cs" <<'CS'
Write-Output "run-with-it-github-update.cs"
CS

for runtime in py python python3; do
  >"${RUNTIME_PY_CALLS}"
  PYTHON_BIN="${RUNTIME_ALIAS_BIN}/fake-python.sh" \
  RUN_WITH_IT_HELPER_RUNTIME="$runtime" \
  "${POOL_RUNNER}" \
    --validate-only \
    --asset-root "${ROOT_DIR}/assets" \
    --state-file "${STATE_FILE}" \
    --parallel-jobs 2 \
    --agent codex \
    --model gpt-5.5 \
    --status-file "${WORK_DIR}/validate-status-${runtime}.txt" \
    --events-log "${WORK_DIR}/validate-events-${runtime}.txt" \
    --main-log "${WORK_DIR}/validate-main-${runtime}.log" \
    2>/dev/null
  assert_contains "$(cat "${RUNTIME_PY_CALLS}")" "run-with-it-state.py" "helper runtime ${runtime} resolves Python helper path"
done

> "${RUNTIME_PY_CALLS}"
PYTHON_BIN="${RUNTIME_ALIAS_BIN}/fake-python.sh" \
  "${POOL_RUNNER}" \
    --validate-only \
    --asset-root "${ROOT_DIR}/assets" \
    --state-file "${STATE_FILE}" \
    --parallel-jobs 2 \
    --agent codex \
    --model gpt-5.5 \
    --status-file "${WORK_DIR}/validate-status-default.txt" \
    --events-log "${WORK_DIR}/validate-events-default.txt" \
    --main-log "${WORK_DIR}/validate-main-default.log" \
    2>/dev/null
assert_contains "$(cat "${RUNTIME_PY_CALLS}")" "run-with-it-state.py" "default runtime resolves Python helper path"

set +e
invalid_runtime_output_file="$(mktemp -d)/pool-invalid-runtime.log"
RUN_WITH_IT_HELPER_RUNTIME=invalid-runtime \
  "${POOL_RUNNER}" \
  --validate-only \
  --asset-root "${ROOT_DIR}/assets" \
  --state-file "${STATE_FILE}" \
  --parallel-jobs 2 \
  --agent codex \
  --model gpt-5.5 \
  --status-file "${WORK_DIR}/invalid-runtime-status.txt" \
  --events-log "${WORK_DIR}/invalid-runtime-events.log" \
  --main-log "${WORK_DIR}/invalid-runtime-main.log" \
  >"${invalid_runtime_output_file}" 2>&1
invalid_runtime_status=$?
set -e
[[ "${invalid_runtime_status}" != "0" ]] || fail "invalid helper runtime fails immediately"
assert_contains "$(cat "${invalid_runtime_output_file}")" "unsupported helper runtime: invalid-runtime" "invalid helper runtime is rejected"

for runtime in cs csharp c#; do
  >"${RUNTIME_DOTNET_CALLS}"
  DOTNET_BIN="${RUNTIME_ALIAS_BIN}/fake-dotnet.sh" \
  RUN_WITH_IT_HELPER_RUNTIME="$runtime" \
    "${POOL_RUNNER}" \
    --validate-only \
    --asset-root "${RUNTIME_ASSET_ROOT}" \
    --state-file "${STATE_FILE}" \
    --parallel-jobs 2 \
    --agent codex \
    --model gpt-5.5 \
    --status-file "${WORK_DIR}/validate-status-${runtime}-cs.txt" \
    --events-log "${WORK_DIR}/validate-events-${runtime}-cs.txt" \
    --main-log "${WORK_DIR}/validate-main-${runtime}-cs.log"
  assert_contains "$(cat "${RUNTIME_DOTNET_CALLS}")" "run-with-it-state.cs" "helper runtime ${runtime} resolves C# helper path"
done
assert_contains "$(cat "${RUNTIME_DOTNET_CALLS}")" "run-with-it-state.cs" "DOTNET_BIN receives C# helper path in helper invocation"

echo "PASS: run-with-it pool runtime selector contract"
