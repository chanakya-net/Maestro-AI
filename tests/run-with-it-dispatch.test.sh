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

assert_json_file() {
  local file="$1"
  local message="$2"
  python3 -m json.tool "$file" >/dev/null || fail "${message} (invalid JSON: ${file})"
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
ISSUE_DIR="${WORK_DIR}/.run-with-it/issues/42"
LOG_FILE="${ISSUE_DIR}/workers/impl/cycle-1.log"
DONE_FILE="${ISSUE_DIR}/workers/impl/cycle-1.done"
RESULT_FILE="${ISSUE_DIR}/workers/impl/cycle-1-result.json"
STATE_FILE="${ISSUE_DIR}/workers/impl/cycle-1.state.json"
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
  --state-file "${STATE_FILE}" \
  --repo-root "${WORKTREE_ROOT}" \
  --issue-dir "${ISSUE_DIR}" \
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
assert_contains "${dry_output}" "RUN_WITH_IT_RESULT_FILE=${RESULT_FILE}" "dry-run sets result file"
assert_contains "${dry_output}" "RUN_WITH_IT_STATE_FILE=${STATE_FILE}" "dry-run sets watchdog state file"
assert_contains "${dry_output}" "RUN_WITH_IT_ISSUE_DIR=${ISSUE_DIR}" "dry-run sets issue-scoped artifact folder"

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
  --state-file "${STATE_FILE}" \
  --repo-root "${WORKTREE_ROOT}" \
  --issue-dir "${ISSUE_DIR}" \
  --status-file "${STATUS_FILE}" \
  --events-log "${EVENTS_LOG}")"

assert_contains "${validate_output}" "STATUS|type=dispatch-ready|issue=42|role=impl|cycle=1" "validate-only reports ready"
assert_file_contains "${STATUS_FILE}" "STATUS|type=dispatch-ready|issue=42|role=impl|cycle=1" "validate-only writes status bus"
assert_file_contains "${EVENTS_LOG}" "STATUS|type=dispatch-ready|issue=42|role=impl|cycle=1" "validate-only appends events log"
assert_json_file "${STATE_FILE}" "validate-only writes watchdog state JSON"
assert_file_contains "${STATE_FILE}" '"state": "ready"' "validate-only records ready state"
assert_file_contains "${STATE_FILE}" '"log_file":' "watchdog state records role log path"

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
printf 'STATUS|type=heartbeat|issue=%s|role=%s|phase=testing|progress=repo-root\n' "${RUN_WITH_IT_ISSUE:-unknown}" "${RUN_WITH_IT_ROLE:-unknown}"
printf 'fake-agent stdout is captured\n'
printf 'fake-agent stderr is captured\n' >&2
mkdir -p "$repo_root" "$(dirname "$RUN_WITH_IT_RESULT_FILE")"
printf 'seen\n' > "$repo_root/marker.txt"
printf '{"outcome":"completed","repo_root_seen":"%s"}\n' "$repo_root" > "$RUN_WITH_IT_RESULT_FILE"
SH
chmod +x "${SMOKE_BIN}/fake-agent"

SMOKE_CONTEXT="${SMOKE_PROJECT}/context.md"
SMOKE_PROMPT="${SMOKE_PROJECT}/prompt.md"
SMOKE_ISSUE_DIR="${SMOKE_PROJECT}/.run-with-it/issues/42"
SMOKE_RESULT="${SMOKE_ISSUE_DIR}/merge-recovery-report.json"
SMOKE_LOG="${SMOKE_ISSUE_DIR}/merge-recovery.log"
SMOKE_DONE="${SMOKE_ISSUE_DIR}/merge-recovery.done"
SMOKE_STATE="${SMOKE_ISSUE_DIR}/merge-recovery.state.json"
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
  --state-file "${SMOKE_STATE}" \
  --repo-root "${SMOKE_REPO_ROOT}" \
  --issue-dir "${SMOKE_ISSUE_DIR}" \
  --status-file "${SMOKE_STATUS}" \
  --events-log "${SMOKE_EVENTS}" \
  --poll-seconds 1 >/dev/null

assert_file_contains "${SMOKE_LOG}" "STATUS|type=heartbeat|issue=42|role=merge-recovery|phase=testing|progress=repo-root" "actual dispatch writes child heartbeat to role log"
assert_file_contains "${SMOKE_LOG}" "fake-agent stdout is captured" "actual dispatch captures child stdout in role log"
assert_file_contains "${SMOKE_LOG}" "fake-agent stderr is captured" "actual dispatch captures child stderr in role log"
assert_file_contains "${SMOKE_EVENTS}" "STATUS|type=heartbeat|issue=42|role=merge-recovery|phase=testing|progress=repo-root" "actual dispatch appends child heartbeat to events log"
assert_file_contains "${SMOKE_DONE}" "DONE|issue=42|role=merge-recovery" "actual dispatch writes done sentinel"
assert_file_contains "${SMOKE_RESULT}" "\"repo_root_seen\":\"${SMOKE_REPO_ROOT}\"" "actual dispatch forwards repo root to child agent"
assert_json_file "${SMOKE_STATE}" "actual dispatch writes final watchdog state JSON"
assert_file_contains "${SMOKE_STATE}" '"state": "completed"' "actual dispatch records completed state"
assert_file_contains "${SMOKE_STATE}" '"result_present": true' "actual dispatch records result artifact presence"
heartbeat_count="$(grep -Fc "STATUS|type=heartbeat|issue=42|role=merge-recovery|phase=testing|progress=repo-root" "${SMOKE_LOG}")"
assert_contains "${heartbeat_count}" "1" "actual dispatch does not duplicate child heartbeat in role log"

cat > "${SMOKE_BIN}/silent-agent" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  printf 'silent-agent 1.0\n'
  exit 0
fi
prompt_payload="$2"
sleep 4
git -C "$1" config user.email "test@example.com"
git -C "$1" config user.name "Test User"
printf 'silent\n' > "$1/silent.txt"
git -C "$1" add silent.txt
git -C "$1" commit -m "impl test" >/dev/null
commit_sha="$(git -C "$1" rev-parse HEAD)"
mkdir -p "$(dirname "$RUN_WITH_IT_RESULT_FILE")"
printf '{"schema_version":1,"issue":"%s","role":"%s","status":"success","commit_sha":"%s","files_committed":["silent.txt"],"verification":{"passed":true,"commands":["fake"]}}\n' "${RUN_WITH_IT_ISSUE:-unknown}" "${RUN_WITH_IT_ROLE:-unknown}" "$commit_sha" > "$RUN_WITH_IT_RESULT_FILE"
SH
chmod +x "${SMOKE_BIN}/silent-agent"

python3 - "${SMOKE_ASSET_ROOT}/agent-registry.json" <<'PY'
import json, sys
path = sys.argv[1]
with open(path) as handle:
    registry = json.load(handle)
registry["agents"]["silent"] = {
    "display_name": "Silent",
    "detection": {"command": "silent-agent", "args": ["--version"]},
    "invocation": {
        "command": "silent-agent",
        "args_template": ["{{repo_root}}", "{{prompt}}"],
        "prompt_argument_template": "{{prompt}}",
    },
    "permission_modes": {"default": "", "available": [""]},
    "model": {"default": "fake-model", "flag_template": "", "known_models": ["fake-model"]},
    "capability_band": "balanced",
    "fallback_order": [],
    "user_model_configuration": {
        "requires_user_model_config": False,
        "config_paths": [],
        "skip_when_unconfigured": False,
        "skip_message": "",
    },
}
with open(path, "w") as handle:
    json.dump(registry, handle, indent=2)
PY

SILENT_ISSUE_DIR="${SMOKE_PROJECT}/.run-with-it/issues/43"
SILENT_CONTEXT="${SMOKE_PROJECT}/silent-context.md"
SILENT_RESULT="${SILENT_ISSUE_DIR}/workers/impl/cycle-1-result.json"
SILENT_LOG="${SILENT_ISSUE_DIR}/workers/impl/cycle-1.log"
SILENT_DONE="${SILENT_ISSUE_DIR}/workers/impl/cycle-1.done"
SILENT_STATE="${SILENT_ISSUE_DIR}/workers/impl/cycle-1.state.json"
mkdir -p "$(dirname "${SILENT_RESULT}")"
printf 'RESULT_FILE=%s\n' "${SILENT_RESULT}" > "${SILENT_CONTEXT}"
git -C "${SMOKE_REPO_ROOT}" init -q
git -C "${SMOKE_REPO_ROOT}" config user.email "test@example.com"
git -C "${SMOKE_REPO_ROOT}" config user.name "Test User"
printf 'baseline\n' > "${SMOKE_REPO_ROOT}/README.md"
git -C "${SMOKE_REPO_ROOT}" add README.md
git -C "${SMOKE_REPO_ROOT}" commit -m "baseline" >/dev/null

PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/run-with-it-dispatch.sh" \
  --asset-root "${SMOKE_ASSET_ROOT}" \
  --role impl \
  --issue 43 \
  --cycle 1 \
  --agent silent \
  --model fake-model \
  --context-file "${SILENT_CONTEXT}" \
  --prompt-file "${SMOKE_PROMPT}" \
  --log-file "${SILENT_LOG}" \
  --done-file "${SILENT_DONE}" \
  --result-file "${SILENT_RESULT}" \
  --state-file "${SILENT_STATE}" \
  --repo-root "${SMOKE_REPO_ROOT}" \
  --issue-dir "${SILENT_ISSUE_DIR}" \
  --status-file "${SMOKE_STATUS}" \
  --events-log "${SMOKE_EVENTS}" \
  --poll-seconds 1 \
  --quiet-seconds 1 \
  --stall-seconds 2 >/dev/null &
silent_dispatch_pid="$!"

saw_stalled=0
for _ in {1..40}; do
  if [[ -f "${SILENT_STATE}" ]] && grep -Fq '"state": "stalled"' "${SILENT_STATE}"; then
    saw_stalled=1
    break
  fi
  sleep 0.2
done

wait "${silent_dispatch_pid}"
[[ "${saw_stalled}" == "1" ]] || fail "silent live worker should be marked stalled before completion"
assert_json_file "${SILENT_STATE}" "silent worker final watchdog state JSON is valid"
assert_file_contains "${SILENT_STATE}" '"state": "completed"' "silent worker eventually completes"
assert_file_contains "${SMOKE_EVENTS}" "STATUS|type=worker-stalled|issue=43|role=impl|cycle=1|reason=alive-but-silent" "silent worker emits stalled status"

cat > "${SMOKE_BIN}/done-only-agent" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  printf 'done-only-agent 1.0\n'
  exit 0
fi
mkdir -p "$(dirname "${RUN_WITH_IT_DONE_FILE}")"
printf 'DONE|issue=%s|role=%s|status=success|source=agent\n' "${RUN_WITH_IT_ISSUE:-unknown}" "${RUN_WITH_IT_ROLE:-unknown}" > "${RUN_WITH_IT_DONE_FILE}"
SH
chmod +x "${SMOKE_BIN}/done-only-agent"

python3 - "${SMOKE_ASSET_ROOT}/agent-registry.json" <<'PY'
import json, sys
path = sys.argv[1]
with open(path) as handle:
    registry = json.load(handle)
registry["agents"]["done-only"] = {
    "display_name": "Done Only",
    "detection": {"command": "done-only-agent", "args": ["--version"]},
    "invocation": {
        "command": "done-only-agent",
        "args_template": ["{{repo_root}}", "{{prompt}}"],
        "prompt_argument_template": "{{prompt}}",
    },
    "permission_modes": {"default": "", "available": [""]},
    "model": {"default": "fake-model", "flag_template": "", "known_models": ["fake-model"]},
    "capability_band": "balanced",
    "fallback_order": [],
    "user_model_configuration": {
        "requires_user_model_config": False,
        "config_paths": [],
        "skip_when_unconfigured": False,
        "skip_message": "",
    },
}
with open(path, "w") as handle:
    json.dump(registry, handle, indent=2)
PY

DONE_ONLY_ISSUE_DIR="${SMOKE_PROJECT}/.run-with-it/issues/44"
DONE_ONLY_CONTEXT="${SMOKE_PROJECT}/done-only-context.md"
DONE_ONLY_RESULT="${DONE_ONLY_ISSUE_DIR}/workers/impl/cycle-1-result.json"
DONE_ONLY_LOG="${DONE_ONLY_ISSUE_DIR}/workers/impl/cycle-1.log"
DONE_ONLY_DONE="${DONE_ONLY_ISSUE_DIR}/workers/impl/cycle-1.done"
DONE_ONLY_STATE="${DONE_ONLY_ISSUE_DIR}/workers/impl/cycle-1.state.json"
DONE_ONLY_OUTPUT="${WORK_DIR}/done-only-dispatch.out"
printf '# done only\n' > "${DONE_ONLY_CONTEXT}"

set +e
PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/run-with-it-dispatch.sh" \
  --asset-root "${SMOKE_ASSET_ROOT}" \
  --role impl \
  --issue 44 \
  --cycle 1 \
  --agent done-only \
  --model fake-model \
  --context-file "${DONE_ONLY_CONTEXT}" \
  --prompt-file "${SMOKE_PROMPT}" \
  --log-file "${DONE_ONLY_LOG}" \
  --done-file "${DONE_ONLY_DONE}" \
  --result-file "${DONE_ONLY_RESULT}" \
  --state-file "${DONE_ONLY_STATE}" \
  --repo-root "${SMOKE_REPO_ROOT}" \
  --issue-dir "${DONE_ONLY_ISSUE_DIR}" \
  --status-file "${SMOKE_STATUS}" \
  --events-log "${SMOKE_EVENTS}" \
  --poll-seconds 1 >"${DONE_ONLY_OUTPUT}" 2>&1
done_only_status="$?"
set -e

[[ "${done_only_status}" != "0" ]] || fail "impl worker with done sentinel but no result artifact should fail"
assert_file_contains "${DONE_ONLY_OUTPUT}" "reason=missing-result-artifact" "done-only failure reports missing result artifact"
assert_file_contains "${DONE_ONLY_STATE}" '"stall_reason": "missing-result-artifact"' "done-only state records precise missing result reason"

echo "PASS: run-with-it dispatcher contract"
