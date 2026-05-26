#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER_PATH="${ROOT_DIR}/assets/run-agent.sh"
ASSET_ROOT="${ROOT_DIR}/assets"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_file() {
  local file="$1"
  local message="$2"
  [[ -f "${file}" ]] || fail "${message} (missing: ${file})"
}

assert_contains() {
  local file="$1"
  local needle="$2"
  local message="$3"
  if ! grep -Fq -- "${needle}" "${file}"; then
    fail "${message} (missing: ${needle} in ${file})"
  fi
}

assert_json_file() {
  local file="$1"
  local message="$2"
  python3 -m json.tool "${file}" >/dev/null || fail "${message} (invalid JSON: ${file})"
}

WORK_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

PROJECT_DIR="${WORK_DIR}/project"
FAKE_BIN="${WORK_DIR}/bin"
REGISTRY_FILE="${WORK_DIR}/agent-registry.json"
SUB_CONTEXT_FILE="${WORK_DIR}/sub-context.md"
MAIN_LOG="${PROJECT_DIR}/.run-with-it/main/main.log"
STATUS_FILE="${PROJECT_DIR}/.run-with-it/status/current.txt"
EVENTS_LOG="${PROJECT_DIR}/.run-with-it/status/events.log"
ISSUE_DIR="${PROJECT_DIR}/.run-with-it/issues/101"
SUB_REPORT_FILE="${ISSUE_DIR}/report.json"
SUB_LOG_FILE="${ISSUE_DIR}/sub-coordinator.log"
SUB_STATE_FILE="${ISSUE_DIR}/sub-state.json"
SUB_DONE_FILE="${ISSUE_DIR}/sub-coordinator.done"
TRANSITION_PROOF="${ISSUE_DIR}/transition-proof.txt"
IMPL_CLEANUP_MARKER="${ISSUE_DIR}/workers/impl/impl-cleanup-finished.marker"

mkdir -p "${PROJECT_DIR}/.run-with-it/main" \
  "${PROJECT_DIR}/.run-with-it/status" \
  "${ISSUE_DIR}/workers/complexity" \
  "${ISSUE_DIR}/workers/impl" \
  "${ISSUE_DIR}/workers/review" \
  "${ISSUE_DIR}/workers/modify" \
  "${FAKE_BIN}"

cat > "${SUB_CONTEXT_FILE}" <<'EOF'
# Issue 101

Title: Smoke-test run-with-it logging and done sentinels

Acceptance criteria:
- Run complexity, implementation, review, modification, and final review stages.
- Write logs under the issue-scoped Sub-Coordinator folder.
- Write worker logs, results, and done sentinels under that same issue folder.

Environment:
SUB_COORD_ISSUE_NUMBER=101
EOF

cat > "${FAKE_BIN}/fake-complexity" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf 'STATUS|type=heartbeat|issue=%s|role=complexity|phase=scoring|progress=reading issue\n' "${RUN_WITH_IT_ISSUE:-unknown}"
printf 'COMPLEXITY|score=12|level=quite-easy|d1=1|d2=1|d3=1|d4=1|d5=2|d6=1|d7=1|d8=1|d9=2\n'
printf '{"total":12,"level":"quite-easy","scores":{"dependency_complexity":1,"ownership_overlap_risk":1,"architecture_risk":1,"orchestration_burden":1,"verification_risk":2,"ambiguity_of_requirements":1,"integration_surface_breadth":1,"rollback_recovery_risk":1,"blast_radius":2},"rationale":{"dependency_complexity":"Tiny fixture.","ownership_overlap_risk":"Single owner.","architecture_risk":"No architecture touched.","orchestration_burden":"Single smoke flow.","verification_risk":"Harness checks files.","ambiguity_of_requirements":"Explicit smoke issue.","integration_surface_breadth":"No integrations.","rollback_recovery_risk":"Temp dir only.","blast_radius":"Temp dir only."}}\n'
if [[ -n "${RUN_WITH_IT_DONE_FILE:-}" ]]; then
  mkdir -p "$(dirname "${RUN_WITH_IT_DONE_FILE}")"
  printf 'DONE|issue=%s|role=complexity|status=success|source=agent\n' "${RUN_WITH_IT_ISSUE:-unknown}" > "${RUN_WITH_IT_DONE_FILE}"
fi
sleep 1
printf 'STATUS|type=heartbeat|issue=%s|role=complexity|phase=cleanup|progress=cleanup done\n' "${RUN_WITH_IT_ISSUE:-unknown}"
SH

cat > "${FAKE_BIN}/fake-impl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf 'STATUS|type=heartbeat|issue=%s|role=impl|phase=implementing|progress=writing fixture\n' "${RUN_WITH_IT_ISSUE:-unknown}"
printf 'implemented\n' > "${PROJECT_DIR}/smoke-output.txt"
mkdir -p "$(dirname "${WORKER_RESULT_FILE}")"
printf '{"role":"impl","verification":{"status":"passed","evidence":["smoke fixture written"]}}\n' > "${WORKER_RESULT_FILE}"
if [[ -n "${RUN_WITH_IT_DONE_FILE:-}" ]]; then
  mkdir -p "$(dirname "${RUN_WITH_IT_DONE_FILE}")"
  printf 'DONE|issue=%s|role=impl|status=success|source=agent\n' "${RUN_WITH_IT_ISSUE:-unknown}" > "${RUN_WITH_IT_DONE_FILE}"
fi
printf 'STATUS|type=heartbeat|issue=%s|role=impl|phase=testing|progress=verification passed\n' "${RUN_WITH_IT_ISSUE:-unknown}"
sleep 2
touch "${IMPL_CLEANUP_MARKER}"
printf 'fake impl cleanup finished\n'
SH

cat > "${FAKE_BIN}/fake-review" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
verdict="approve"
comment_count="0"
if [[ "${REVIEW_CYCLE:-1}" == "1" ]]; then
  verdict="revise"
  comment_count="1"
fi
printf 'STATUS|type=heartbeat|issue=%s|role=review|phase=review|progress=writing json\n' "${RUN_WITH_IT_ISSUE:-unknown}"
mkdir -p "$(dirname "${REVIEWER_STATUS_FILE}")"
printf '{"verdict":"%s","comment_count":%s,"nitpick_only":false}\n' "${verdict}" "${comment_count}" > "${REVIEWER_STATUS_FILE}"
printf '{"verdict":"%s","summary":"smoke review cycle %s","comments":[],"blocking_reasons":[]}\n' "${verdict}" "${REVIEW_CYCLE:-1}" > "${REVIEWER_INSTRUCTIONS_FILE}"
python3 -m json.tool "${REVIEWER_STATUS_FILE}" >/dev/null
python3 -m json.tool "${REVIEWER_INSTRUCTIONS_FILE}" >/dev/null
if [[ -n "${RUN_WITH_IT_DONE_FILE:-}" ]]; then
  mkdir -p "$(dirname "${RUN_WITH_IT_DONE_FILE}")"
  printf 'DONE|issue=%s|role=review|status=success|source=agent\n' "${RUN_WITH_IT_ISSUE:-unknown}" > "${RUN_WITH_IT_DONE_FILE}"
fi
sleep 1
printf 'fake review cleanup finished\n'
SH

cat > "${FAKE_BIN}/fake-modify" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf 'STATUS|type=heartbeat|issue=%s|role=modify|phase=implementing|progress=applying review\n' "${RUN_WITH_IT_ISSUE:-unknown}"
printf 'modified\n' >> "${PROJECT_DIR}/smoke-output.txt"
mkdir -p "$(dirname "${WORKER_RESULT_FILE}")"
printf '{"role":"modify","verification":{"status":"passed","evidence":["review addressed"]}}\n' > "${WORKER_RESULT_FILE}"
if [[ -n "${RUN_WITH_IT_DONE_FILE:-}" ]]; then
  mkdir -p "$(dirname "${RUN_WITH_IT_DONE_FILE}")"
  printf 'DONE|issue=%s|role=modify|status=success|source=agent\n' "${RUN_WITH_IT_ISSUE:-unknown}" > "${RUN_WITH_IT_DONE_FILE}"
fi
sleep 1
printf 'fake modify cleanup finished\n'
SH

cat > "${FAKE_BIN}/fake-sub" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

issue="${SUB_COORD_ISSUE_NUMBER:-${RUN_WITH_IT_ISSUE:-101}}"
RUN_WITH_IT_ISSUE_DIR="${RUN_WITH_IT_ISSUE_DIR:-${PROJECT_DIR}/.run-with-it/issues/${issue}}"
mkdir -p "${RUN_WITH_IT_ISSUE_DIR}" "$(dirname "${SUB_COORD_LOG_FILE}")" "$(dirname "${SUB_COORD_REPORT_FILE}")"
SUB_STATE_FILE="${RUN_WITH_IT_ISSUE_DIR}/sub-state.json"

write_status() {
  local line="$1"
  printf '%s\n' "${line}" >> "${SUB_COORD_LOG_FILE}"
  if [[ -n "${RUN_WITH_IT_STATUS_FILE:-}" ]]; then
    mkdir -p "$(dirname "${RUN_WITH_IT_STATUS_FILE}")"
    printf '%s\n' "${line}" > "${RUN_WITH_IT_STATUS_FILE}"
  fi
  if [[ -n "${RUN_WITH_IT_EVENTS_LOG:-}" ]]; then
    mkdir -p "$(dirname "${RUN_WITH_IT_EVENTS_LOG}")"
    printf '%s\n' "${line}" >> "${RUN_WITH_IT_EVENTS_LOG}"
  fi
  printf '%s\n' "${line}"
}

write_state() {
  local phase="$1"
  local in_flight_json="${2:-[]}"
  cat > "${SUB_STATE_FILE}" <<JSON
{
  "schema_version": 1,
  "issue_number": ${issue},
  "phase": "${phase}",
  "in_flight_agents": ${in_flight_json},
  "review_history": [],
  "updated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON
  python3 -m json.tool "${SUB_STATE_FILE}" > "${SUB_STATE_FILE}.pretty"
  mv "${SUB_STATE_FILE}.pretty" "${SUB_STATE_FILE}"
  cat "${SUB_STATE_FILE}" >> "${RUN_WITH_IT_ISSUE_DIR}/state-history.log"
}

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))'
}

valid_json() {
  python3 -m json.tool "$1" >/dev/null 2>&1
}

wait_for_done_and_artifact() {
  local pid="$1"
  local role="$2"
  local phase="$3"
  local done_file="$4"
  local artifact_check="$5"
  local log_file="$6"
  local tail_state_file="${PROJECT_DIR}/.run-with-it/status/issue-${issue}-${role}-cycle-tail.sha"
  local started_at
  started_at="$(date +%s)"
  while kill -0 "${pid}" 2>/dev/null; do
    watcher_output="$("${ASSET_ROOT}/worker-watch.sh" --pid "${pid}" --done-file "${done_file}" --log-file "${log_file}" --tail-state-file "${tail_state_file}" --tail-lines 5)"
    if [[ "${watcher_output}" == *"log_tail_changed=true"* ]]; then
      write_status "STATUS|type=worker-log-tail|issue=${issue}|role=${role}|phase=${phase}|summary=changed-log-tail"
    fi
    if [[ -s "${done_file}" ]] && eval "${artifact_check}"; then
      local source
      source="$(sed -n 's/.*source=\([^|]*\).*/\1/p' "${done_file}" | tail -n 1)"
      write_status "STATUS|type=worker-done|issue=${issue}|role=${role}|phase=${phase}|source=${source:-unknown}"
      return 0
    fi
    if [[ "$(( $(date +%s) - started_at ))" -gt 15 ]]; then
      write_status "STATUS|type=worker-timeout|issue=${issue}|role=${role}|phase=${phase}"
      return 1
    fi
    sleep 0.1
  done
  wait "${pid}"
  [[ -s "${done_file}" ]] && eval "${artifact_check}"
}

spawn_worker() {
  local role="$1"
  local agent="$2"
  local prompt_file="$3"
  local cycle="${4:-1}"
  local worker_dir="${RUN_WITH_IT_ISSUE_DIR}/workers/${role}"
  local context_file="${worker_dir}/cycle-${cycle}-context.md"
  local log_file="${worker_dir}/cycle-${cycle}.log"
  local done_file="${worker_dir}/cycle-${cycle}.done"
  local result_file="${worker_dir}/cycle-${cycle}-result.json"
  printf 'issue=%s role=%s cycle=%s\n' "${issue}" "${role}" "${cycle}" > "${context_file}"
  mkdir -p "${worker_dir}"
  PATH="${FAKE_BIN}:${PATH}" \
    AGENT_REGISTRY_FILE="${AGENT_REGISTRY_FILE}" \
    PROJECT_DIR="${PROJECT_DIR}" \
    WORKER_RESULT_FILE="${result_file}" \
    REVIEW_CYCLE="${cycle}" \
    REVIEWER_STATUS_FILE="${RUN_WITH_IT_ISSUE_DIR}/workers/review/cycle-${cycle}-status.json" \
    REVIEWER_INSTRUCTIONS_FILE="${RUN_WITH_IT_ISSUE_DIR}/workers/review/cycle-${cycle}-instructions.json" \
    RUN_WITH_IT_ISSUE_DIR="${RUN_WITH_IT_ISSUE_DIR}" \
    RUN_WITH_IT_WORKER_DIR="${worker_dir}" \
    RUN_WITH_IT_STATUS_FILE="${RUN_WITH_IT_STATUS_FILE}" \
    RUN_WITH_IT_EVENTS_LOG="${RUN_WITH_IT_EVENTS_LOG}" \
    RUN_WITH_IT_LOG_FILE="${log_file}" \
    RUN_WITH_IT_DONE_FILE="${done_file}" \
    RUN_WITH_IT_ROLE="${role}" \
    RUN_WITH_IT_ISSUE="${issue}" \
    "${RUNNER_PATH}" \
      --agent "${agent}" \
      --model fake-model \
      --context-file "${context_file}" \
      --prompt-file "${prompt_file}" \
      --unattended &
  spawned_pid="$!"
  escaped_log="$(printf '%s' "${log_file}" | json_escape)"
  escaped_done="$(printf '%s' "${done_file}" | json_escape)"
  escaped_result="$(printf '%s' "${result_file}" | json_escape)"
  write_state "${role}" "[{\"role\":\"${role}\",\"cycle\":${cycle},\"pid\":${spawned_pid},\"agent\":\"${agent}\",\"model\":\"fake-model\",\"log_file\":${escaped_log},\"done_file\":${escaped_done},\"result_file\":${escaped_result},\"started_at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}]"
}

write_state "starting" "[]"
write_status "STATUS|type=sub-start|issue=${issue}"

spawn_worker complexity fake-complexity "${ASSET_ROOT}/complexity-prompt.md" 1
complexity_pid="${spawned_pid}"
wait_for_done_and_artifact "${complexity_pid}" complexity scoring "${RUN_WITH_IT_ISSUE_DIR}/workers/complexity/cycle-1.done" "grep -Fq 'COMPLEXITY|score=12' '${RUN_WITH_IT_ISSUE_DIR}/workers/complexity/cycle-1.log'" "${RUN_WITH_IT_ISSUE_DIR}/workers/complexity/cycle-1.log"

spawn_worker impl fake-impl "${ASSET_ROOT}/prompt.md" 1
impl_pid="${spawned_pid}"
wait_for_done_and_artifact "${impl_pid}" impl implementing "${RUN_WITH_IT_ISSUE_DIR}/workers/impl/cycle-1.done" "valid_json '${RUN_WITH_IT_ISSUE_DIR}/workers/impl/cycle-1-result.json'" "${RUN_WITH_IT_ISSUE_DIR}/workers/impl/cycle-1.log"
if [[ ! -f "${IMPL_CLEANUP_MARKER}" ]]; then
  printf 'started_review_before_impl_cleanup=yes\n' > "${TRANSITION_PROOF}"
fi

spawn_worker review fake-review "${ASSET_ROOT}/review-prompt.md" 1
review1_pid="${spawned_pid}"
wait_for_done_and_artifact "${review1_pid}" review review "${RUN_WITH_IT_ISSUE_DIR}/workers/review/cycle-1.done" "valid_json '${RUN_WITH_IT_ISSUE_DIR}/workers/review/cycle-1-status.json' && valid_json '${RUN_WITH_IT_ISSUE_DIR}/workers/review/cycle-1-instructions.json'" "${RUN_WITH_IT_ISSUE_DIR}/workers/review/cycle-1.log"

spawn_worker modify fake-modify "${ASSET_ROOT}/modifier-prompt.md" 1
modify_pid="${spawned_pid}"
wait_for_done_and_artifact "${modify_pid}" modify implementing "${RUN_WITH_IT_ISSUE_DIR}/workers/modify/cycle-1.done" "valid_json '${RUN_WITH_IT_ISSUE_DIR}/workers/modify/cycle-1-result.json'" "${RUN_WITH_IT_ISSUE_DIR}/workers/modify/cycle-1.log"

spawn_worker review fake-review "${ASSET_ROOT}/review-prompt.md" 2
review2_pid="${spawned_pid}"
wait_for_done_and_artifact "${review2_pid}" review review "${RUN_WITH_IT_ISSUE_DIR}/workers/review/cycle-2.done" "valid_json '${RUN_WITH_IT_ISSUE_DIR}/workers/review/cycle-2-status.json' && valid_json '${RUN_WITH_IT_ISSUE_DIR}/workers/review/cycle-2-instructions.json'" "${RUN_WITH_IT_ISSUE_DIR}/workers/review/cycle-2.log"

wait "${complexity_pid}" "${impl_pid}" "${review1_pid}" "${modify_pid}" "${review2_pid}"
write_state "report-written" "[]"

cat > "${SUB_COORD_REPORT_FILE}" <<JSON
{
  "schema_version": 1,
  "issue": ${issue},
  "outcome": "completed",
  "summary": "Smoke run completed complexity, impl, review, modify, and final review.",
  "files_modified": [{"path": "smoke-output.txt", "added": 2, "deleted": 0}],
  "verification": {"status": "passed", "evidence": ["nested runner smoke harness"]},
  "review_summary": {"final_verdict": "approve", "cycles_used": 2},
  "token_usage": {},
  "commit_sha": "none",
  "blocking_reasons": []
}
JSON

write_status "STATUS|type=sub-report-written|issue=${issue}|report_file=${SUB_COORD_REPORT_FILE}"
SH

chmod +x "${FAKE_BIN}/fake-complexity" \
  "${FAKE_BIN}/fake-impl" \
  "${FAKE_BIN}/fake-review" \
  "${FAKE_BIN}/fake-modify" \
  "${FAKE_BIN}/fake-sub"

cat > "${REGISTRY_FILE}" <<JSON
{
  "schema_version": 1,
  "aliases": {},
  "agents": {
    "fake-sub": {
      "display_name": "Fake Sub Coordinator",
      "detection": {"command": "fake-sub", "args": ["--version"]},
      "invocation": {"command": "fake-sub", "args_template": ["{{prompt}}"], "prompt_argument_template": "{{prompt}}"},
      "permission_modes": {"default": "", "available": [""]},
      "model": {"default": "fake-model", "flag_template": "--model {{model}}", "known_models": ["fake-model"]},
      "capability_band": "balanced",
      "fallback_order": [],
      "user_model_configuration": {"requires_user_model_config": false, "config_paths": [], "skip_when_unconfigured": false, "skip_message": ""}
    },
    "fake-complexity": {
      "display_name": "Fake Complexity",
      "detection": {"command": "fake-complexity", "args": ["--version"]},
      "invocation": {"command": "fake-complexity", "args_template": ["{{prompt}}"], "prompt_argument_template": "{{prompt}}"},
      "permission_modes": {"default": "", "available": [""]},
      "model": {"default": "fake-model", "flag_template": "--model {{model}}", "known_models": ["fake-model"]},
      "capability_band": "balanced",
      "fallback_order": [],
      "user_model_configuration": {"requires_user_model_config": false, "config_paths": [], "skip_when_unconfigured": false, "skip_message": ""}
    },
    "fake-impl": {
      "display_name": "Fake Implementer",
      "detection": {"command": "fake-impl", "args": ["--version"]},
      "invocation": {"command": "fake-impl", "args_template": ["{{prompt}}"], "prompt_argument_template": "{{prompt}}"},
      "permission_modes": {"default": "", "available": [""]},
      "model": {"default": "fake-model", "flag_template": "--model {{model}}", "known_models": ["fake-model"]},
      "capability_band": "balanced",
      "fallback_order": [],
      "user_model_configuration": {"requires_user_model_config": false, "config_paths": [], "skip_when_unconfigured": false, "skip_message": ""}
    },
    "fake-review": {
      "display_name": "Fake Reviewer",
      "detection": {"command": "fake-review", "args": ["--version"]},
      "invocation": {"command": "fake-review", "args_template": ["{{prompt}}"], "prompt_argument_template": "{{prompt}}"},
      "permission_modes": {"default": "", "available": [""]},
      "model": {"default": "fake-model", "flag_template": "--model {{model}}", "known_models": ["fake-model"]},
      "capability_band": "balanced",
      "fallback_order": [],
      "user_model_configuration": {"requires_user_model_config": false, "config_paths": [], "skip_when_unconfigured": false, "skip_message": ""}
    },
    "fake-modify": {
      "display_name": "Fake Modifier",
      "detection": {"command": "fake-modify", "args": ["--version"]},
      "invocation": {"command": "fake-modify", "args_template": ["{{prompt}}"], "prompt_argument_template": "{{prompt}}"},
      "permission_modes": {"default": "", "available": [""]},
      "model": {"default": "fake-model", "flag_template": "--model {{model}}", "known_models": ["fake-model"]},
      "capability_band": "balanced",
      "fallback_order": [],
      "user_model_configuration": {"requires_user_model_config": false, "config_paths": [], "skip_when_unconfigured": false, "skip_message": ""}
    }
  }
}
JSON

printf 'STATUS|type=main-start|issue=101\n' >> "${MAIN_LOG}"
PATH="${FAKE_BIN}:${PATH}" \
  AGENT_REGISTRY_FILE="${REGISTRY_FILE}" \
  PROJECT_DIR="${PROJECT_DIR}" \
  RUNNER_PATH="${RUNNER_PATH}" \
  ASSET_ROOT="${ASSET_ROOT}" \
  FAKE_BIN="${FAKE_BIN}" \
  SUB_COORD_ISSUE_NUMBER=101 \
  SUB_COORD_REPORT_FILE="${SUB_REPORT_FILE}" \
  SUB_COORD_LOG_FILE="${SUB_LOG_FILE}" \
  TRANSITION_PROOF="${TRANSITION_PROOF}" \
  IMPL_CLEANUP_MARKER="${IMPL_CLEANUP_MARKER}" \
  RUN_WITH_IT_STATUS_FILE="${STATUS_FILE}" \
  RUN_WITH_IT_EVENTS_LOG="${EVENTS_LOG}" \
  RUN_WITH_IT_ISSUE_DIR="${ISSUE_DIR}" \
  RUN_WITH_IT_LOG_FILE="${SUB_LOG_FILE}" \
  RUN_WITH_IT_DONE_FILE="${SUB_DONE_FILE}" \
  RUN_WITH_IT_ROLE=sub-coord \
  RUN_WITH_IT_ISSUE=101 \
  "${RUNNER_PATH}" \
    --agent fake-sub \
    --model fake-model \
    --context-file "${SUB_CONTEXT_FILE}" \
    --prompt-file "${ASSET_ROOT}/sub-coordinator-prompt.md" \
    --unattended
printf 'STATUS|type=sub-coord-complete|issue=101|outcome=completed|report_file=%s\n' "${SUB_REPORT_FILE}" >> "${MAIN_LOG}"

assert_json_file "${SUB_REPORT_FILE}" "sub-coordinator report is valid JSON"
assert_contains "${SUB_REPORT_FILE}" '"outcome": "completed"' "sub-coordinator report completed"
assert_file "${SUB_STATE_FILE}" "sub-coordinator state file exists"
assert_json_file "${SUB_STATE_FILE}" "sub-coordinator state file is valid JSON"
assert_contains "${SUB_STATE_FILE}" '"schema_version": 1' "state file has schema version"
assert_contains "${SUB_STATE_FILE}" '"in_flight_agents"' "state file tracks in-flight agents"
assert_contains "${SUB_STATE_FILE}" '"phase": "report-written"' "state file records final phase"
assert_contains "${SUB_LOG_FILE}" 'STATUS|type=worker-log-tail|issue=101' "sub log records worker log-tail progress summaries"

assert_file "${MAIN_LOG}" "main log exists"
assert_file "${SUB_LOG_FILE}" "sub-coordinator log exists"
assert_file "${STATUS_FILE}" "current status file exists"
assert_file "${EVENTS_LOG}" "events log exists"
assert_file "${SUB_DONE_FILE}" "sub-coordinator done file exists"
assert_contains "${MAIN_LOG}" 'STATUS|type=sub-coord-complete|issue=101|outcome=completed' "main log records completion"
assert_contains "${SUB_LOG_FILE}" 'STATUS|type=worker-done|issue=101|role=complexity' "sub log records complexity completion"
assert_contains "${SUB_LOG_FILE}" 'STATUS|type=worker-done|issue=101|role=impl' "sub log records implementation completion"
assert_contains "${SUB_LOG_FILE}" 'STATUS|type=worker-done|issue=101|role=review' "sub log records review completion"
assert_contains "${SUB_LOG_FILE}" 'STATUS|type=worker-done|issue=101|role=modify' "sub log records modifier completion"
assert_contains "${EVENTS_LOG}" 'STATUS|type=worker-done' "events log records worker done statuses"

state_history_file="${ISSUE_DIR}/state-history.log"
assert_file "${state_history_file}" "state history exists"
assert_contains "${state_history_file}" '"phase": "starting"' "state history records bootstrap before worker spawn"
assert_contains "${state_history_file}" '"role": "impl"' "state history recorded implementation worker"
assert_contains "${state_history_file}" '"done_file"' "state history records done file paths"
assert_contains "${state_history_file}" '"log_file"' "state history records log file paths"
assert_contains "${state_history_file}" '"result_file"' "state history records result file paths"
assert_contains "${state_history_file}" '"pid"' "state history records worker pid"

assert_file "${ISSUE_DIR}/workers/complexity/cycle-1.log" "complexity role log exists in issue folder"
assert_file "${ISSUE_DIR}/workers/impl/cycle-1.log" "implementation role log exists in issue folder"
assert_file "${ISSUE_DIR}/workers/review/cycle-1.log" "review cycle 1 role log exists in issue folder"
assert_file "${ISSUE_DIR}/workers/review/cycle-2.log" "review cycle 2 role log exists in issue folder"
assert_file "${ISSUE_DIR}/workers/modify/cycle-1.log" "modifier role log exists in issue folder"

assert_file "${ISSUE_DIR}/workers/complexity/cycle-1.done" "complexity done file exists in issue folder"
assert_file "${ISSUE_DIR}/workers/impl/cycle-1.done" "implementation done file exists in issue folder"
assert_file "${ISSUE_DIR}/workers/review/cycle-1.done" "review cycle 1 done file exists in issue folder"
assert_file "${ISSUE_DIR}/workers/review/cycle-2.done" "review cycle 2 done file exists in issue folder"
assert_file "${ISSUE_DIR}/workers/modify/cycle-1.done" "modifier done file exists in issue folder"

assert_json_file "${ISSUE_DIR}/workers/review/cycle-1-status.json" "review cycle 1 status JSON valid"
assert_json_file "${ISSUE_DIR}/workers/review/cycle-1-instructions.json" "review cycle 1 instructions JSON valid"
assert_json_file "${ISSUE_DIR}/workers/review/cycle-2-status.json" "review cycle 2 status JSON valid"
assert_json_file "${ISSUE_DIR}/workers/review/cycle-2-instructions.json" "review cycle 2 instructions JSON valid"

assert_contains "${TRANSITION_PROOF}" 'started_review_before_impl_cleanup=yes' "sub-coordinator started next phase from done sentinel before worker cleanup finished"
assert_contains "${PROJECT_DIR}/smoke-output.txt" 'modified' "small issue work fixture was modified"

echo "PASS: run-with-it nested logging and done-sentinel smoke harness"
