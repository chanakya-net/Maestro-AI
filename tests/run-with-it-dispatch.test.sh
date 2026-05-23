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
WORKTREE_ROOT="${WORK_DIR}/.run-with-it/worktrees/issue-42"

printf '# Context\n' > "${CONTEXT_FILE}"
mkdir -p "$(dirname "${RESULT_FILE}")" "${WORKTREE_ROOT}"
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
  --repo-root "${WORKTREE_ROOT}" \
  --status-file "${STATUS_FILE}" \
  --events-log "${EVENTS_LOG}")"

assert_contains "${dry_output}" "run-agent.sh" "dry-run wraps run-agent"
assert_contains "${dry_output}" "--agent codex" "dry-run forwards agent"
assert_contains "${dry_output}" "--model gpt-5.5" "dry-run forwards model"
assert_contains "${dry_output}" "--context-file ${CONTEXT_FILE}" "dry-run forwards context file"
assert_contains "${dry_output}" "--prompt-file ${PROMPT_FILE}" "dry-run forwards prompt file"
assert_contains "${dry_output}" "RUN_WITH_IT_ROLE=impl" "dry-run sets role"
assert_contains "${dry_output}" "RUN_WITH_IT_ISSUE=42" "dry-run sets issue"
assert_contains "${dry_output}" "REPO_ROOT=${WORKTREE_ROOT}" "dry-run forwards issue worktree repo root"
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
  --repo-root "${WORKTREE_ROOT}" \
  --status-file "${STATUS_FILE}" \
  --events-log "${EVENTS_LOG}")"

assert_contains "${validate_output}" "STATUS|type=dispatch-ready|issue=42|role=impl|cycle=1" "validate-only reports ready"
assert_file_contains "${STATUS_FILE}" "STATUS|type=dispatch-ready|issue=42|role=impl|cycle=1" "validate-only writes status bus"
assert_file_contains "${EVENTS_LOG}" "STATUS|type=dispatch-ready|issue=42|role=impl|cycle=1" "validate-only appends events log"

SMOKE_ASSET_ROOT="${WORK_DIR}/assets"
SMOKE_PROJECT="${WORK_DIR}/project"
SMOKE_REPO_ROOT="${WORK_DIR}/repo-root"
SMOKE_BIN="${WORK_DIR}/bin"
mkdir -p "${SMOKE_ASSET_ROOT}" "${SMOKE_PROJECT}" "${SMOKE_REPO_ROOT}" "${SMOKE_BIN}"
cp "${ROOT_DIR}/assets/run-agent.sh" "${ROOT_DIR}/assets/worker-watch.sh" "${ROOT_DIR}/assets/run-with-it-dispatch.sh" "${SMOKE_ASSET_ROOT}/"
chmod +x "${SMOKE_ASSET_ROOT}/run-agent.sh" "${SMOKE_ASSET_ROOT}/worker-watch.sh" "${SMOKE_ASSET_ROOT}/run-with-it-dispatch.sh"

cat > "${SMOKE_ASSET_ROOT}/agent-registry.json" <<'JSON'
{
  "schema_version": 1,
  "aliases": {},
  "agents": {
    "fake": {
      "display_name": "Fake",
      "detection": { "command": "fake-agent", "args": ["--version"] },
      "invocation": {
        "command": "fake-agent",
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

cat > "${SMOKE_BIN}/fake-agent" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  printf 'fake-agent 1.0\n'
  exit 0
fi
repo_root="$1"
prompt_payload="$2"
result_file="$(printf '%s' "$prompt_payload" | sed -n 's/^RESULT_FILE=//p' | head -n 1)"
printf 'STATUS|type=heartbeat|issue=%s|role=%s|phase=testing|progress=repo-root\n' "${RUN_WITH_IT_ISSUE:-unknown}" "${RUN_WITH_IT_ROLE:-unknown}"
mkdir -p "$repo_root" "$(dirname "$result_file")"
printf 'seen\n' > "$repo_root/marker.txt"
printf '{"outcome":"completed","repo_root_seen":"%s"}\n' "$repo_root" > "$result_file"
SH
chmod +x "${SMOKE_BIN}/fake-agent"

SMOKE_CONTEXT="${SMOKE_PROJECT}/context.md"
SMOKE_PROMPT="${SMOKE_PROJECT}/prompt.md"
SMOKE_RESULT="${SMOKE_PROJECT}/.run-with-it/reports/result.json"
SMOKE_LOG="${SMOKE_PROJECT}/.run-with-it/merge-recovery/issue-42.log"
SMOKE_DONE="${SMOKE_PROJECT}/.run-with-it/done/issue-42-merge-recovery.done"
SMOKE_STATUS="${SMOKE_PROJECT}/.run-with-it/status/current.txt"
SMOKE_EVENTS="${SMOKE_PROJECT}/.run-with-it/status/events.log"
mkdir -p "$(dirname "${SMOKE_RESULT}")"
printf 'RESULT_FILE=%s\n' "${SMOKE_RESULT}" > "${SMOKE_CONTEXT}"
printf '# Prompt\n' > "${SMOKE_PROMPT}"

PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/run-with-it-dispatch.sh" \
  --asset-root "${SMOKE_ASSET_ROOT}" \
  --role merge-recovery \
  --issue 42 \
  --agent fake \
  --model fake-model \
  --context-file "${SMOKE_CONTEXT}" \
  --prompt-file "${SMOKE_PROMPT}" \
  --log-file "${SMOKE_LOG}" \
  --done-file "${SMOKE_DONE}" \
  --result-file "${SMOKE_RESULT}" \
  --repo-root "${SMOKE_REPO_ROOT}" \
  --status-file "${SMOKE_STATUS}" \
  --events-log "${SMOKE_EVENTS}" \
  --poll-seconds 1 >/dev/null

assert_file_contains "${SMOKE_LOG}" "STATUS|type=heartbeat|issue=42|role=merge-recovery|phase=testing|progress=repo-root" "actual dispatch writes child heartbeat to role log"
assert_file_contains "${SMOKE_EVENTS}" "STATUS|type=heartbeat|issue=42|role=merge-recovery|phase=testing|progress=repo-root" "actual dispatch appends child heartbeat to events log"
assert_file_contains "${SMOKE_DONE}" "DONE|issue=42|role=merge-recovery" "actual dispatch writes done sentinel"
assert_file_contains "${SMOKE_RESULT}" "\"repo_root_seen\":\"${SMOKE_REPO_ROOT}\"" "actual dispatch forwards repo root to child agent"
heartbeat_count="$(grep -Fc "STATUS|type=heartbeat|issue=42|role=merge-recovery|phase=testing|progress=repo-root" "${SMOKE_LOG}")"
assert_contains "${heartbeat_count}" "1" "actual dispatch does not duplicate child heartbeat in role log"

echo "PASS: run-with-it dispatcher contract"
