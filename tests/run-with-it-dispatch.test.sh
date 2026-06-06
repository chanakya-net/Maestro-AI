#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DISPATCHER="${ROOT_DIR}/assets/scripts/run-with-it-dispatch.sh"

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

assert_eq() {
  local actual="$1"
  local expected="$2"
  local message="$3"
  [[ "${actual}" == "${expected}" ]] || fail "${message} (expected: ${expected}, got: ${actual})"
}

assert_file_contains() {
  local file="$1"
  local needle="$2"
  local message="$3"
  grep -Fq -- "$needle" "$file" || fail "${message} (missing: ${needle})"
}

assert_not_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"
  [[ "${haystack}" != *"${needle}"* ]] || fail "${message} (unexpected: ${needle})"
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

unset RUN_WITH_IT_DETACHED_CHILD

WORK_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

CONTEXT_FILE="${WORK_DIR}/context.md"
PROMPT_FILE="${ROOT_DIR}/assets/prompts/prompt.md"
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
assert_file_contains "${DISPATCHER}" "start_new_session=True" "detached dispatcher starts in a new session"

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

FLAT_ASSET_ROOT="${WORK_DIR}/flat-assets"
mkdir -p "${FLAT_ASSET_ROOT}/prompts" "${FLAT_ASSET_ROOT}/python"
cp "${ROOT_DIR}/assets/scripts/run-with-it-dispatch.sh" "${FLAT_ASSET_ROOT}/run-with-it-dispatch.sh"
cp "${ROOT_DIR}/assets/scripts/run-agent.sh" "${FLAT_ASSET_ROOT}/run-agent.sh"
cp "${ROOT_DIR}/assets/scripts/worker-watch.sh" "${FLAT_ASSET_ROOT}/worker-watch.sh"
cp "${ROOT_DIR}/assets/python/run-with-it-artifacts.py" "${FLAT_ASSET_ROOT}/python/"
cp "${ROOT_DIR}/assets/prompts/prompt.md" "${FLAT_ASSET_ROOT}/prompts/prompt.md"
cat > "${FLAT_ASSET_ROOT}/agent-registry.json" <<'JSON'
{
  "schema_version": 1,
  "aliases": {},
  "agents": {
    "fake": {
      "display_name": "Fake",
      "detection": { "command": "fake-agent", "args": ["--version"] },
      "invocation": { "command": "fake-agent", "args_template": ["{{repo_root}}", "{{prompt}}"], "prompt_argument_template": "{{prompt}}" },
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
chmod +x "${FLAT_ASSET_ROOT}/run-with-it-dispatch.sh" "${FLAT_ASSET_ROOT}/run-agent.sh" "${FLAT_ASSET_ROOT}/worker-watch.sh"

FLAT_PROJECT="${WORK_DIR}/flat-project"
FLAT_CONTEXT="${FLAT_PROJECT}/context.md"
FLAT_PROMPT="${FLAT_PROJECT}/prompts/prompt.md"
FLAT_LOG="${FLAT_PROJECT}/flat.log"
FLAT_DONE="${FLAT_PROJECT}/flat.done"
FLAT_RESULT="${FLAT_PROJECT}/flat-result.json"
FLAT_STATE="${FLAT_PROJECT}/flat-state.json"
mkdir -p "${FLAT_PROJECT}/prompts"
printf 'flat context\n' > "${FLAT_CONTEXT}"
printf '# flat prompt\n' > "${FLAT_PROMPT}"
mkdir -p "${WORK_DIR}/flat-worktree"

flat_dry_output="$(RUN_WITH_IT_HELPER_RUNTIME=py "${DISPATCHER}" \
  --dry-run \
  --asset-root "${FLAT_ASSET_ROOT}" \
  --role impl \
  --issue 99 \
  --agent fake \
  --model fake-model \
  --context-file "${FLAT_CONTEXT}" \
  --prompt-file "${FLAT_PROMPT}" \
  --log-file "${FLAT_LOG}" \
  --done-file "${FLAT_DONE}" \
  --result-file "${FLAT_RESULT}" \
  --state-file "${FLAT_STATE}" \
  --repo-root "${WORK_DIR}/flat-worktree" \
  --issue-dir "${FLAT_PROJECT}" \
  --status-file "${FLAT_PROJECT}/status.txt" \
  --events-log "${FLAT_PROJECT}/events.log" \
  --poll-seconds 1)"
assert_contains "${flat_dry_output}" "${FLAT_ASSET_ROOT}/run-agent.sh --agent fake" "flat python layout resolves root-level run-agent helper"

set +e
cs_fail_output_file="$(mktemp -d)/cs-fail-output.log"
if RUN_WITH_IT_HELPER_RUNTIME=cs "${DISPATCHER}" \
  --dry-run \
  --asset-root "${FLAT_ASSET_ROOT}" \
  --role impl \
  --issue 100 \
  --agent fake \
  --model fake-model \
  --context-file "${FLAT_CONTEXT}" \
  --prompt-file "${FLAT_PROMPT}" \
  --log-file "${FLAT_LOG}" \
  --done-file "${FLAT_DONE}" \
  --result-file "${FLAT_RESULT}" \
  --state-file "${FLAT_STATE}" \
  --repo-root "${WORK_DIR}/flat-worktree" \
  --issue-dir "${FLAT_PROJECT}" \
  --status-file "${FLAT_PROJECT}/status.txt" \
  --events-log "${FLAT_PROJECT}/events.log" \
  --poll-seconds 1 \
  >>"$cs_fail_output_file" 2>&1; then
  cs_fail_status=0
else
  cs_fail_status=$?
fi
cs_fail_output="$(cat "$cs_fail_output_file")"
rm -f "$cs_fail_output_file"
set -e

[[ "${cs_fail_status}" != "0" ]] || fail "flat C# layout fails when helper runtime is cs"
assert_contains "${cs_fail_output}" "missing nested asset layout for helper runtime 'cs'" "flat C# layout is rejected with nested asset error"

SMOKE_ASSET_ROOT="${WORK_DIR}/assets"
SMOKE_PROJECT="${WORK_DIR}/project"
SMOKE_REPO_ROOT="${WORK_DIR}/repo-root"
SMOKE_BIN="${WORK_DIR}/bin"
mkdir -p "${SMOKE_ASSET_ROOT}/scripts" "${SMOKE_ASSET_ROOT}/python" "${SMOKE_PROJECT}" "${SMOKE_REPO_ROOT}" "${SMOKE_BIN}"
cp "${ROOT_DIR}/assets/scripts/run-agent.sh" "${ROOT_DIR}/assets/scripts/worker-watch.sh" "${ROOT_DIR}/assets/scripts/run-with-it-dispatch.sh" "${SMOKE_ASSET_ROOT}/scripts/"
cp "${ROOT_DIR}/assets/python/run-with-it-artifacts.py" "${SMOKE_ASSET_ROOT}/python/"
chmod +x "${SMOKE_ASSET_ROOT}/scripts/run-agent.sh" "${SMOKE_ASSET_ROOT}/scripts/worker-watch.sh" "${SMOKE_ASSET_ROOT}/scripts/run-with-it-dispatch.sh" "${SMOKE_ASSET_ROOT}/python/run-with-it-artifacts.py"

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
agent_role="${RUN_WITH_IT_ROLE:-unknown}"
agent_issue="${RUN_WITH_IT_ISSUE:-unknown}"

printf 'STATUS|type=heartbeat|issue=%s|role=%s|phase=testing|progress=repo-root\n' "${RUN_WITH_IT_ISSUE:-unknown}" "${RUN_WITH_IT_ROLE:-unknown}"
printf 'fake-agent stdout is captured\n'
printf 'fake-agent stderr is captured\n' >&2
mkdir -p "$repo_root" "$(dirname "$RUN_WITH_IT_RESULT_FILE")"
printf 'seen\n' > "$repo_root/marker.txt"

if [[ "$agent_role" == "impl" || "$agent_role" == "modify" ]]; then
  git -C "$repo_root" init -q
  git -C "$repo_root" config user.email "test@example.com"
  git -C "$repo_root" config user.name "Test User"
  printf 'marker-%s\n' "$(date -u +%s%N)" > "$repo_root/marker.txt"
  git -C "$repo_root" add "$repo_root/marker.txt"
  git -C "$repo_root" commit -m "impl fake marker" >/dev/null
  commit_sha="$(git -C "$repo_root" rev-parse HEAD)"
  printf '{"schema_version":1,"issue":"%s","role":"%s","status":"success","commit_sha":"%s","files_committed":["marker.txt"],"verification":{"passed":true,"commands":["fake"]},"repo_root_seen":"%s"}\n' \
    "$agent_issue" "$agent_role" "$commit_sha" "$repo_root" > "$RUN_WITH_IT_RESULT_FILE"
else
  printf '{"outcome":"completed","repo_root_seen":"%s"}\n' "$repo_root" > "$RUN_WITH_IT_RESULT_FILE"
fi
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

PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/scripts/run-with-it-dispatch.sh" \
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

RECOVERY_PARENT="${WORK_DIR}/recovery-parent"
RECOVERY_ISSUE="${WORK_DIR}/recovery-issue"
mkdir -p "${RECOVERY_PARENT}"
git -C "${RECOVERY_PARENT}" init -q
git -C "${RECOVERY_PARENT}" config user.name "Run With It Test"
git -C "${RECOVERY_PARENT}" config user.email "run-with-it@example.test"
printf 'base\n' > "${RECOVERY_PARENT}/item.txt"
git -C "${RECOVERY_PARENT}" add item.txt
git -C "${RECOVERY_PARENT}" commit -q -m "base"
RECOVERY_BASE_SHA="$(git -C "${RECOVERY_PARENT}" rev-parse HEAD)"
git -C "${RECOVERY_PARENT}" branch issue-42
git -C "${RECOVERY_PARENT}" worktree add -q "${RECOVERY_ISSUE}" issue-42
git -C "${RECOVERY_PARENT}" checkout -q -b wrong-worktree
printf 'base\nwrong worktree change\n' > "${RECOVERY_PARENT}/item.txt"
git -C "${RECOVERY_PARENT}" add item.txt
git -C "${RECOVERY_PARENT}" commit -q -m "modifier committed in wrong worktree"
RECOVERY_WRONG_SHA="$(git -C "${RECOVERY_PARENT}" rev-parse HEAD)"

RECOVERY_RESULT="${WORK_DIR}/recovery-result.json"
RECOVERY_DONE="${WORK_DIR}/recovery.done"
cat > "${RECOVERY_RESULT}" <<JSON
{
  "schema_version": 1,
  "issue": "42",
  "role": "modify",
  "status": "success",
  "commit_sha": "${RECOVERY_WRONG_SHA}",
  "files_committed": ["item.txt"],
  "verification": {
    "passed": true,
    "commands": ["fake verification"]
  }
}
JSON
printf 'DONE|issue=42|role=modify|status=success\n' > "${RECOVERY_DONE}"

recovery_reason="$(python3 "${ROOT_DIR}/assets/python/run-with-it-artifacts.py" failure-reason \
  --role modify \
  --issue 42 \
  --result-file "${RECOVERY_RESULT}" \
  --done-file "${RECOVERY_DONE}" \
  --repo-root "${RECOVERY_ISSUE}" \
  --pre-spawn-head "${RECOVERY_BASE_SHA}")"
assert_eq "${recovery_reason}" "" "artifact helper recovers wrong-worktree modifier commit"
RECOVERY_ISSUE_HEAD="$(git -C "${RECOVERY_ISSUE}" rev-parse HEAD)"
[[ "${RECOVERY_ISSUE_HEAD}" != "${RECOVERY_BASE_SHA}" ]] || fail "recovery advances issue worktree HEAD"
assert_file_contains "${RECOVERY_ISSUE}/item.txt" "wrong worktree change" "recovery cherry-picks reported commit into issue worktree"
assert_file_contains "${RECOVERY_RESULT}" "\"commit_sha\": \"${RECOVERY_ISSUE_HEAD}\"" "recovery rewrites result commit to recovered issue-worktree commit"
assert_file_contains "${RECOVERY_RESULT}" "\"recovered_from_commit\": \"${RECOVERY_WRONG_SHA}\"" "recovery records original wrong-worktree commit"
assert_file_contains "${RECOVERY_RESULT}" '"recovery": "cherry-picked commit from outside issue worktree into issue worktree"' "recovery records recovery note"

DETACH_PROJECT="${WORK_DIR}/detach-project"
DETACH_CONTEXT="${DETACH_PROJECT}/context.md"
DETACH_PROMPT="${DETACH_PROJECT}/prompt.md"
DETACH_LOG="${DETACH_PROJECT}/workers/impl/cycle-1.log"
DETACH_DONE="${DETACH_PROJECT}/workers/impl/cycle-1.done"
DETACH_RESULT="${DETACH_PROJECT}/workers/impl/cycle-1-result.json"
DETACH_STATE="${DETACH_PROJECT}/workers/impl/cycle-1.state.json"
mkdir -p "${DETACH_PROJECT}/workers/impl"
printf 'detached context\n' > "${DETACH_CONTEXT}"
printf 'detached prompt\n' > "${DETACH_PROMPT}"

(
  cd "${DETACH_PROJECT}"
  PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/scripts/run-with-it-dispatch.sh" \
    --asset-root "${SMOKE_ASSET_ROOT}" \
    --role impl \
    --issue 46 \
    --cycle 1 \
    --agent fake \
    --model fake-model \
    --context-file "${DETACH_CONTEXT}" \
    --prompt-file "${DETACH_PROMPT}" \
    --log-file "${DETACH_LOG}" \
    --done-file "${DETACH_DONE}" \
    --result-file "${DETACH_RESULT}" \
    --state-file "${DETACH_STATE}" \
    --repo-root "${SMOKE_REPO_ROOT}" \
    --issue-dir "${DETACH_PROJECT}" \
    --poll-seconds 1 \
    --detach >"${DETACH_PROJECT}/dispatch.out" 2>&1
)

for _ in 1 2 3 4 5; do
  if grep -Fq "STATUS|type=dispatch-complete|issue=46|role=impl" "${DETACH_LOG}" 2>/dev/null; then
    break
  fi
  sleep 1
done

assert_file_contains "${DETACH_LOG}" "STATUS|type=dispatch-detached|issue=46|role=impl|cycle=1" "detached dispatcher records detached pid"
assert_file_contains "${DETACH_LOG}" "STATUS|type=dispatch-start|issue=46|role=impl|cycle=1" "detached dispatcher starts after parent shell exits"
assert_file_contains "${DETACH_LOG}" "STATUS|type=dispatch-pid|issue=46|role=impl|cycle=1" "detached dispatcher captures runner pid"
assert_json_file "${DETACH_STATE}" "detached dispatcher writes final state JSON"
assert_json_file "${DETACH_RESULT}" "detached worker writes result JSON"

PRESTART_DIR="${WORK_DIR}/prestart-project"
PRESTART_LOG="${PRESTART_DIR}/cycle-1.log"
PRESTART_DONE="${PRESTART_DIR}/cycle-1.done"
PRESTART_RESULT="${PRESTART_DIR}/cycle-1-result.json"
PRESTART_STATE="${PRESTART_DIR}/cycle-1.state.json"
PRESTART_CONTEXT="${PRESTART_DIR}/context.md"
PRESTART_PROMPT="${PRESTART_DIR}/prompt.md"
mkdir -p "${PRESTART_DIR}"
printf 'context\n' > "${PRESTART_CONTEXT}"
printf 'prompt\n' > "${PRESTART_PROMPT}"

set +e
RUN_WITH_IT_TEST_FAIL_READY_STATE=1 PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/scripts/run-with-it-dispatch.sh" \
  --asset-root "${SMOKE_ASSET_ROOT}" \
  --role complexity \
  --issue 47 \
  --cycle 1 \
  --agent fake \
  --model fake-model \
  --context-file "${PRESTART_CONTEXT}" \
  --prompt-file "${PRESTART_PROMPT}" \
  --log-file "${PRESTART_LOG}" \
  --done-file "${PRESTART_DONE}" \
  --result-file "${PRESTART_RESULT}" \
  --state-file "${PRESTART_STATE}" \
  --issue-dir "${PRESTART_DIR}" \
  --poll-seconds 1 >"${PRESTART_DIR}/dispatch.out" 2>&1
prestart_status="$?"
set -e

[[ "${prestart_status}" != "0" ]] || fail "pre-start state failure must not exit success"
assert_file_contains "${PRESTART_LOG}" "STATUS|type=dispatch-ready|issue=47|role=complexity|cycle=1" "pre-start failure reaches ready"
assert_file_contains "${PRESTART_LOG}" "STATUS|type=dispatch-pre-start-failed|issue=47|role=complexity|cycle=1" "pre-start failure is classified"
assert_not_contains "$(cat "${PRESTART_LOG}")" "STATUS|type=dispatch-start|issue=47|role=complexity|cycle=1" "pre-start failure never starts runner"

START_FAIL_DIR="${WORK_DIR}/start-fail-project"
START_FAIL_LOG="${START_FAIL_DIR}/cycle-1.log"
START_FAIL_DONE="${START_FAIL_DIR}/cycle-1.done"
START_FAIL_RESULT="${START_FAIL_DIR}/cycle-1-result.json"
START_FAIL_STATE="${START_FAIL_DIR}/cycle-1.state.json"
START_FAIL_CONTEXT="${START_FAIL_DIR}/context.md"
START_FAIL_PROMPT="${START_FAIL_DIR}/prompt.md"
START_FAIL_OUTPUT="${START_FAIL_DIR}/dispatch.out"
mkdir -p "${START_FAIL_DIR}"
printf 'context\n' > "${START_FAIL_CONTEXT}"
printf 'prompt\n' > "${START_FAIL_PROMPT}"

set +e
RUN_WITH_IT_TEST_FAIL_STARTING_STATE=1 PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/scripts/run-with-it-dispatch.sh" \
  --asset-root "${SMOKE_ASSET_ROOT}" \
  --role complexity \
  --issue 52 \
  --cycle 1 \
  --agent fake \
  --model fake-model \
  --context-file "${START_FAIL_CONTEXT}" \
  --prompt-file "${START_FAIL_PROMPT}" \
  --log-file "${START_FAIL_LOG}" \
  --done-file "${START_FAIL_DONE}" \
  --result-file "${START_FAIL_RESULT}" \
  --state-file "${START_FAIL_STATE}" \
  --issue-dir "${START_FAIL_DIR}" \
  --poll-seconds 1 >"${START_FAIL_OUTPUT}" 2>&1
start_fail_status="$?"
set -e

[[ "${start_fail_status}" != "0" ]] || fail "start-state bootstrap failure must not exit success"
assert_file_contains "${START_FAIL_LOG}" "STATUS|type=dispatch-start|issue=52|role=complexity|cycle=1" "start-state failure reaches dispatch start"
assert_file_contains "${START_FAIL_LOG}" "STATUS|type=dispatch-bootstrap-failed|issue=52|role=complexity|cycle=1" "start-state failure is classified as bootstrap failure"
assert_not_contains "$(cat "${START_FAIL_LOG}")" "STATUS|type=dispatch-pid|issue=52|role=complexity|cycle=1" "start-state failure never reports runner pid"

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

PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/scripts/run-with-it-dispatch.sh" \
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

cat > "${SMOKE_BIN}/noarg-commit-agent" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  printf 'noarg-commit-agent 1.0\n'
  exit 0
fi
git config user.email "test@example.com"
git config user.name "Test User"
printf 'plain git used cwd\n' > noarg-commit.txt
git add noarg-commit.txt
git commit -m "impl noarg cwd" >/dev/null
commit_sha="$(git rev-parse HEAD)"
mkdir -p "$(dirname "$RUN_WITH_IT_RESULT_FILE")"
printf '{"schema_version":1,"issue":"%s","role":"impl","status":"success","commit_sha":"%s","files_committed":["noarg-commit.txt"],"verification":{"passed":true,"commands":["plain git cwd smoke"]}}\n' "${RUN_WITH_IT_ISSUE:-unknown}" "$commit_sha" > "$RUN_WITH_IT_RESULT_FILE"
SH
chmod +x "${SMOKE_BIN}/noarg-commit-agent"

python3 - "${SMOKE_ASSET_ROOT}/agent-registry.json" <<'PY'
import json, sys
path = sys.argv[1]
with open(path) as handle:
    registry = json.load(handle)
registry["agents"]["noarg-commit"] = {
    "display_name": "No Arg Commit",
    "detection": {"command": "noarg-commit-agent", "args": ["--version"]},
    "invocation": {
        "command": "noarg-commit-agent",
        "args_template": ["{{prompt}}"],
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

NOARG_REPO="${WORK_DIR}/noarg-repo"
NOARG_ISSUE_DIR="${SMOKE_PROJECT}/.run-with-it/issues/48"
NOARG_CONTEXT="${SMOKE_PROJECT}/noarg-context.md"
NOARG_RESULT="${NOARG_ISSUE_DIR}/workers/impl/cycle-1-result.json"
NOARG_LOG="${NOARG_ISSUE_DIR}/workers/impl/cycle-1.log"
NOARG_DONE="${NOARG_ISSUE_DIR}/workers/impl/cycle-1.done"
NOARG_STATE="${NOARG_ISSUE_DIR}/workers/impl/cycle-1.state.json"
mkdir -p "${NOARG_REPO}"
git -C "${NOARG_REPO}" init -q
git -C "${NOARG_REPO}" config user.email "test@example.com"
git -C "${NOARG_REPO}" config user.name "Test User"
printf 'baseline\n' > "${NOARG_REPO}/README.md"
git -C "${NOARG_REPO}" add README.md
git -C "${NOARG_REPO}" commit -m "baseline" >/dev/null
printf '# no repo arg\n' > "${NOARG_CONTEXT}"

PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/scripts/run-with-it-dispatch.sh" \
  --asset-root "${SMOKE_ASSET_ROOT}" \
  --role impl \
  --issue 48 \
  --cycle 1 \
  --agent noarg-commit \
  --model fake-model \
  --context-file "${NOARG_CONTEXT}" \
  --prompt-file "${SMOKE_PROMPT}" \
  --log-file "${NOARG_LOG}" \
  --done-file "${NOARG_DONE}" \
  --result-file "${NOARG_RESULT}" \
  --state-file "${NOARG_STATE}" \
  --repo-root "${NOARG_REPO}" \
  --issue-dir "${NOARG_ISSUE_DIR}" \
  --status-file "${SMOKE_STATUS}" \
  --events-log "${SMOKE_EVENTS}" \
  --poll-seconds 1 >/dev/null

assert_json_file "${NOARG_RESULT}" "no-arg implementation result is valid"
assert_file_contains "${NOARG_RESULT}" '"noarg-commit.txt"' "no-arg implementation records committed file"
assert_file_contains "${NOARG_STATE}" '"state": "completed"' "no-arg implementation completes through dispatcher"
[[ -f "${NOARG_REPO}/noarg-commit.txt" ]] || fail "no-arg implementation committed in supplied repo root"

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
PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/scripts/run-with-it-dispatch.sh" \
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

cat > "${SMOKE_BIN}/commit-without-result-agent" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  printf 'commit-without-result-agent 1.0\n'
  exit 0
fi
repo_root="$1"
git -C "$repo_root" config user.email "test@example.com"
git -C "$repo_root" config user.name "Test User"
printf 'committed without result\n' > "$repo_root/recovered.txt"
git -C "$repo_root" add recovered.txt
git -C "$repo_root" commit -m "impl without result" >/dev/null
SH
chmod +x "${SMOKE_BIN}/commit-without-result-agent"

python3 - "${SMOKE_ASSET_ROOT}/agent-registry.json" <<'PY'
import json, sys
path = sys.argv[1]
with open(path) as handle:
    registry = json.load(handle)
registry["agents"]["commit-without-result"] = {
    "display_name": "Commit Without Result",
    "detection": {"command": "commit-without-result-agent", "args": ["--version"]},
    "invocation": {
        "command": "commit-without-result-agent",
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

RECOVER_ISSUE_DIR="${SMOKE_PROJECT}/.run-with-it/issues/45"
RECOVER_CONTEXT="${SMOKE_PROJECT}/recover-context.md"
RECOVER_RESULT="${RECOVER_ISSUE_DIR}/workers/impl/cycle-1-result.json"
RECOVER_LOG="${RECOVER_ISSUE_DIR}/workers/impl/cycle-1.log"
RECOVER_DONE="${RECOVER_ISSUE_DIR}/workers/impl/cycle-1.done"
RECOVER_STATE="${RECOVER_ISSUE_DIR}/workers/impl/cycle-1.state.json"
printf '# recover missing result\n' > "${RECOVER_CONTEXT}"

PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/scripts/run-with-it-dispatch.sh" \
  --asset-root "${SMOKE_ASSET_ROOT}" \
  --role impl \
  --issue 45 \
  --cycle 1 \
  --agent commit-without-result \
  --model fake-model \
  --context-file "${RECOVER_CONTEXT}" \
  --prompt-file "${SMOKE_PROMPT}" \
  --log-file "${RECOVER_LOG}" \
  --done-file "${RECOVER_DONE}" \
  --result-file "${RECOVER_RESULT}" \
  --state-file "${RECOVER_STATE}" \
  --repo-root "${SMOKE_REPO_ROOT}" \
  --issue-dir "${RECOVER_ISSUE_DIR}" \
  --status-file "${SMOKE_STATUS}" \
  --events-log "${SMOKE_EVENTS}" \
  --poll-seconds 1 >/dev/null

assert_json_file "${RECOVER_RESULT}" "missing implementation result is synthesized from committed work"
assert_file_contains "${RECOVER_RESULT}" '"issue": "45"' "synthesized result records issue"
assert_file_contains "${RECOVER_RESULT}" '"role": "impl"' "synthesized result records role"
assert_file_contains "${RECOVER_RESULT}" '"status": "success"' "synthesized result validates as successful handoff"
assert_file_contains "${RECOVER_RESULT}" '"recovered.txt"' "synthesized result records committed file"
assert_file_contains "${RECOVER_RESULT}" '"source": "dispatcher-synthesized"' "synthesized result is auditable"
assert_file_contains "${RECOVER_STATE}" '"state": "completed"' "recoverable missing result completes"
assert_file_contains "${SMOKE_EVENTS}" "STATUS|type=result-artifact-synthesized|issue=45|role=impl|cycle=1" "recovery emits synthesis status"

cat > "${SMOKE_BIN}/wrong-path-modifier-agent" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  printf 'wrong-path-modifier-agent 1.0\n'
  exit 0
fi
repo_root="$1"
git -C "$repo_root" config user.email "test@example.com"
git -C "$repo_root" config user.name "Test User"
printf 'change\n' > "$repo_root/wrong-path.txt"
git -C "$repo_root" add wrong-path.txt
git -C "$repo_root" commit -q -m "wrong path change"
commit_sha="$(git -C "$repo_root" rev-parse HEAD)"
wrong_file="${RUN_WITH_IT_ISSUE_DIR}/report.json"
mkdir -p "$(dirname "$wrong_file")" "$(dirname "$RUN_WITH_IT_DONE_FILE")"
printf '{"schema_version":1,"issue":"%s","role":"modify","status":"success","commit_sha":"%s","files_committed":["wrong-path.txt"],"verification":{"passed":true,"commands":["fake pass"]}}\n' "${RUN_WITH_IT_ISSUE}" "$commit_sha" > "$wrong_file"
printf 'DONE|issue=%s|role=modify|status=success|source=agent\n' "${RUN_WITH_IT_ISSUE}" > "$RUN_WITH_IT_DONE_FILE"
SH
chmod +x "${SMOKE_BIN}/wrong-path-modifier-agent"

python3 - "${SMOKE_ASSET_ROOT}/agent-registry.json" <<'PY'
import json, sys
path = sys.argv[1]
with open(path) as handle:
    registry = json.load(handle)
registry["agents"]["wrong-path-modifier"] = {
    "display_name": "Wrong Path Modifier",
    "detection": {"command": "wrong-path-modifier-agent", "args": ["--version"]},
    "invocation": {
        "command": "wrong-path-modifier-agent",
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

WRONG_PATH_REPO="${WORK_DIR}/wrong-path-repo"
WRONG_PATH_ISSUE_DIR="${SMOKE_PROJECT}/.run-with-it/issues/50"
WRONG_PATH_CONTEXT="${SMOKE_PROJECT}/wrong-path-context.md"
WRONG_PATH_RESULT="${WRONG_PATH_ISSUE_DIR}/workers/modify/cycle-1-result.json"
WRONG_PATH_LOG="${WRONG_PATH_ISSUE_DIR}/workers/modify/cycle-1.log"
WRONG_PATH_DONE="${WRONG_PATH_ISSUE_DIR}/workers/modify/cycle-1.done"
WRONG_PATH_STATE="${WRONG_PATH_ISSUE_DIR}/workers/modify/cycle-1.state.json"
WRONG_PATH_OUTPUT="${WORK_DIR}/wrong-path-dispatch.out"
mkdir -p "${WRONG_PATH_REPO}"
git -C "${WRONG_PATH_REPO}" init -q
git -C "${WRONG_PATH_REPO}" config user.email "test@example.com"
git -C "${WRONG_PATH_REPO}" config user.name "Test User"
printf 'baseline\n' > "${WRONG_PATH_REPO}/README.md"
git -C "${WRONG_PATH_REPO}" add README.md
git -C "${WRONG_PATH_REPO}" commit -m "baseline" >/dev/null
printf '# wrong path\n' > "${WRONG_PATH_CONTEXT}"

set +e
PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/scripts/run-with-it-dispatch.sh" \
  --asset-root "${SMOKE_ASSET_ROOT}" \
  --role modify \
  --issue 50 \
  --cycle 1 \
  --agent wrong-path-modifier \
  --model fake-model \
  --context-file "${WRONG_PATH_CONTEXT}" \
  --prompt-file "${SMOKE_PROMPT}" \
  --log-file "${WRONG_PATH_LOG}" \
  --done-file "${WRONG_PATH_DONE}" \
  --result-file "${WRONG_PATH_RESULT}" \
  --state-file "${WRONG_PATH_STATE}" \
  --repo-root "${WRONG_PATH_REPO}" \
  --issue-dir "${WRONG_PATH_ISSUE_DIR}" \
  --status-file "${SMOKE_STATUS}" \
  --events-log "${SMOKE_EVENTS}" \
  --poll-seconds 1 >"${WRONG_PATH_OUTPUT}" 2>&1
wrong_path_status="$?"
set -e
[[ "${wrong_path_status}" != "0" ]] || fail "wrong-path worker result must not complete"
assert_file_contains "${WRONG_PATH_OUTPUT}" "reason=missing-result-artifact" "wrong report path does not count as worker result"
assert_not_contains "$(cat "${WRONG_PATH_RESULT}" 2>/dev/null || true)" '"commands":["fake pass"]' "dispatcher does not trust wrong-path report as worker result"
report_path_reason="$(python3 "${SMOKE_ASSET_ROOT}/python/run-with-it-artifacts.py" failure-reason \
  --role modify \
  --issue 50 \
  --result-file "${WRONG_PATH_ISSUE_DIR}/report.json" \
  --done-file "${WRONG_PATH_DONE}" \
  --issue-dir "${WRONG_PATH_ISSUE_DIR}" \
  --repo-root "${WRONG_PATH_REPO}" \
  --pre-spawn-head "")"
[[ "${report_path_reason}" == "worker-result-path-is-sub-coordinator-report" ]] || fail "artifact helper rejects report.json as worker result path"

cat > "${SMOKE_BIN}/review-instructions-only-agent" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  printf 'review-instructions-only-agent 1.0\n'
  exit 0
fi
instructions_file="${RUN_WITH_IT_RESULT_FILE%-status.json}-instructions.json"
mkdir -p "$(dirname "$instructions_file")" "$(dirname "$RUN_WITH_IT_DONE_FILE")"
printf '{"verdict":"approve","summary":"review approved from instructions","comments":[],"blocking_reasons":[]}\n' > "$instructions_file"
printf 'DONE|issue=%s|role=review|status=success|source=agent\n' "${RUN_WITH_IT_ISSUE:-unknown}" > "$RUN_WITH_IT_DONE_FILE"
SH
chmod +x "${SMOKE_BIN}/review-instructions-only-agent"

cat > "${SMOKE_BIN}/review-approve-status-only-agent" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  printf 'review-approve-status-only-agent 1.0\n'
  exit 0
fi
mkdir -p "$(dirname "$RUN_WITH_IT_RESULT_FILE")" "$(dirname "$RUN_WITH_IT_DONE_FILE")"
printf '{"verdict":"approve","comment_count":0,"nitpick_only":false}\n' > "$RUN_WITH_IT_RESULT_FILE"
printf 'DONE|issue=%s|role=review|status=success|source=agent\n' "${RUN_WITH_IT_ISSUE:-unknown}" > "$RUN_WITH_IT_DONE_FILE"
SH
chmod +x "${SMOKE_BIN}/review-approve-status-only-agent"

cat > "${SMOKE_BIN}/review-revise-status-only-agent" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  printf 'review-revise-status-only-agent 1.0\n'
  exit 0
fi
mkdir -p "$(dirname "$RUN_WITH_IT_RESULT_FILE")" "$(dirname "$RUN_WITH_IT_DONE_FILE")"
printf '{"verdict":"revise","comment_count":1,"nitpick_only":false}\n' > "$RUN_WITH_IT_RESULT_FILE"
printf 'DONE|issue=%s|role=review|status=success|source=agent\n' "${RUN_WITH_IT_ISSUE:-unknown}" > "$RUN_WITH_IT_DONE_FILE"
SH
chmod +x "${SMOKE_BIN}/review-revise-status-only-agent"

cat > "${SMOKE_BIN}/review-canonical-retry-agent" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  printf 'review-canonical-retry-agent 1.0\n'
  exit 0
fi
canonical_status="$(printf '%s' "$RUN_WITH_IT_RESULT_FILE" | sed -E 's/-attempt-[0-9]+-status\.json$/-status.json/')"
canonical_instructions="${canonical_status%-status.json}-instructions.json"
canonical_done="$(printf '%s' "$RUN_WITH_IT_DONE_FILE" | sed -E 's/-attempt-[0-9]+\.done$/.done/')"
mkdir -p "$(dirname "$canonical_status")" "$(dirname "$canonical_done")"
printf '{"verdict":"approve","comment_count":0,"nitpick_only":false}\n' > "$canonical_status"
printf '{"verdict":"approve","summary":"canonical retry review approved","comments":[],"blocking_reasons":[]}\n' > "$canonical_instructions"
printf 'DONE|issue=%s|role=review|status=success|source=agent\n' "${RUN_WITH_IT_ISSUE:-unknown}" > "$canonical_done"
SH
chmod +x "${SMOKE_BIN}/review-canonical-retry-agent"

cat > "${SMOKE_BIN}/invalid-complexity-agent" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  printf 'invalid-complexity-agent 1.0\n'
  exit 0
fi
mkdir -p "$(dirname "$RUN_WITH_IT_RESULT_FILE")" "$(dirname "$RUN_WITH_IT_DONE_FILE")"
printf '{"bad":true}\n' > "$RUN_WITH_IT_RESULT_FILE"
printf 'DONE|issue=%s|role=complexity|status=success|source=agent\n' "${RUN_WITH_IT_ISSUE:-unknown}" > "$RUN_WITH_IT_DONE_FILE"
SH
chmod +x "${SMOKE_BIN}/invalid-complexity-agent"

cat > "${SMOKE_BIN}/complexity-canonical-retry-agent" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  printf 'complexity-canonical-retry-agent 1.0\n'
  exit 0
fi
canonical_result="$(printf '%s' "$RUN_WITH_IT_RESULT_FILE" | sed -E 's/-attempt-[0-9]+-result\.json$/-result.json/')"
mkdir -p "$(dirname "$canonical_result")"
cat > "$canonical_result" <<'JSON'
{
  "total": 18,
  "level": "medium",
  "scores": {
    "dependency_complexity": 2,
    "ownership_overlap_risk": 2,
    "architecture_risk": 2,
    "orchestration_burden": 2,
    "verification_risk": 3,
    "ambiguity_of_requirements": 2,
    "integration_surface_breadth": 2,
    "rollback_recovery_risk": 1,
    "blast_radius": 2
  },
  "rationale": {
    "dependency_complexity": "A few stable dependencies are involved.",
    "ownership_overlap_risk": "Ownership is mostly local.",
    "architecture_risk": "The architecture surface is modest.",
    "orchestration_burden": "A small amount of coordination is needed.",
    "verification_risk": "Some integration verification may be needed.",
    "ambiguity_of_requirements": "The requirements are mostly clear.",
    "integration_surface_breadth": "A couple of integration points are touched.",
    "rollback_recovery_risk": "Rollback is straightforward.",
    "blast_radius": "The affected surface is moderate."
  }
}
JSON
SH
chmod +x "${SMOKE_BIN}/complexity-canonical-retry-agent"

cat > "${SMOKE_BIN}/stdout-complexity-agent" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  printf 'stdout-complexity-agent 1.0\n'
  exit 0
fi
mkdir -p "$(dirname "$RUN_WITH_IT_DONE_FILE")"
cat <<'JSON'
COMPLEXITY|score=14|level=easy|d1=2|d2=1|d3=2|d4=1|d5=2|d6=1|d7=2|d8=1|d9=2
{
  "total": 14,
  "level": "easy",
  "scores": {
    "dependency_complexity": 2,
    "ownership_overlap_risk": 1,
    "architecture_risk": 2,
    "orchestration_burden": 1,
    "verification_risk": 2,
    "ambiguity_of_requirements": 1,
    "integration_surface_breadth": 2,
    "rollback_recovery_risk": 1,
    "blast_radius": 2
  },
  "rationale": {
    "dependency_complexity": "Uses a few stable local dependencies.",
    "ownership_overlap_risk": "Single owner surface.",
    "architecture_risk": "Small architectural surface.",
    "orchestration_burden": "No orchestration required.",
    "verification_risk": "Unit tests can cover the behavior.",
    "ambiguity_of_requirements": "Requirements are clear.",
    "integration_surface_breadth": "One stable integration point.",
    "rollback_recovery_risk": "Instant revert.",
    "blast_radius": "Small user-visible surface."
  }
}
JSON
printf 'DONE|issue=%s|role=complexity|status=success|source=agent\n' "${RUN_WITH_IT_ISSUE:-unknown}" > "$RUN_WITH_IT_DONE_FILE"
SH
chmod +x "${SMOKE_BIN}/stdout-complexity-agent"

cat > "${SMOKE_BIN}/hanging-complexity-agent" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  printf 'hanging-complexity-agent 1.0\n'
  exit 0
fi
trap 'exit 143' TERM INT
while true; do
  sleep 60
done
SH
chmod +x "${SMOKE_BIN}/hanging-complexity-agent"

python3 - "${SMOKE_ASSET_ROOT}/agent-registry.json" <<'PY'
import json, sys
path = sys.argv[1]
with open(path) as handle:
    registry = json.load(handle)
for name in (
    "review-instructions-only",
    "review-approve-status-only",
    "review-revise-status-only",
    "review-canonical-retry",
    "invalid-complexity",
    "complexity-canonical-retry",
    "stdout-complexity",
    "hanging-complexity",
):
    registry["agents"][name] = {
        "display_name": name,
        "detection": {"command": f"{name}-agent", "args": ["--version"]},
        "invocation": {
            "command": f"{name}-agent",
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

REVIEW_SYNTH_STATUS_DIR="${SMOKE_PROJECT}/.run-with-it/issues/46"
REVIEW_SYNTH_STATUS_RESULT="${REVIEW_SYNTH_STATUS_DIR}/workers/review/cycle-2-status.json"
REVIEW_SYNTH_STATUS_LOG="${REVIEW_SYNTH_STATUS_DIR}/workers/review/cycle-2.log"
REVIEW_SYNTH_STATUS_DONE="${REVIEW_SYNTH_STATUS_DIR}/workers/review/cycle-2.done"
REVIEW_SYNTH_STATUS_STATE="${REVIEW_SYNTH_STATUS_DIR}/workers/review/cycle-2.state.json"
PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/scripts/run-with-it-dispatch.sh" \
  --asset-root "${SMOKE_ASSET_ROOT}" \
  --role review \
  --issue 46 \
  --cycle 2 \
  --agent review-instructions-only \
  --model fake-model \
  --context-file "${SMOKE_CONTEXT}" \
  --prompt-file "${SMOKE_PROMPT}" \
  --log-file "${REVIEW_SYNTH_STATUS_LOG}" \
  --done-file "${REVIEW_SYNTH_STATUS_DONE}" \
  --result-file "${REVIEW_SYNTH_STATUS_RESULT}" \
  --state-file "${REVIEW_SYNTH_STATUS_STATE}" \
  --repo-root "${SMOKE_REPO_ROOT}" \
  --issue-dir "${REVIEW_SYNTH_STATUS_DIR}" \
  --status-file "${SMOKE_STATUS}" \
  --events-log "${SMOKE_EVENTS}" \
  --poll-seconds 1 >/dev/null
assert_json_file "${REVIEW_SYNTH_STATUS_RESULT}" "review status is synthesized from valid instructions"
assert_file_contains "${REVIEW_SYNTH_STATUS_RESULT}" '"source": "dispatcher-synthesized"' "synthesized review status is auditable"
assert_file_contains "${REVIEW_SYNTH_STATUS_STATE}" '"state": "completed"' "review instructions-only worker completes after status synthesis"

REVIEW_SYNTH_INSTRUCTIONS_DIR="${SMOKE_PROJECT}/.run-with-it/issues/47"
REVIEW_SYNTH_INSTRUCTIONS_RESULT="${REVIEW_SYNTH_INSTRUCTIONS_DIR}/workers/review/cycle-2-status.json"
REVIEW_SYNTH_INSTRUCTIONS_FILE="${REVIEW_SYNTH_INSTRUCTIONS_DIR}/workers/review/cycle-2-instructions.json"
REVIEW_SYNTH_INSTRUCTIONS_LOG="${REVIEW_SYNTH_INSTRUCTIONS_DIR}/workers/review/cycle-2.log"
REVIEW_SYNTH_INSTRUCTIONS_DONE="${REVIEW_SYNTH_INSTRUCTIONS_DIR}/workers/review/cycle-2.done"
REVIEW_SYNTH_INSTRUCTIONS_STATE="${REVIEW_SYNTH_INSTRUCTIONS_DIR}/workers/review/cycle-2.state.json"
PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/scripts/run-with-it-dispatch.sh" \
  --asset-root "${SMOKE_ASSET_ROOT}" \
  --role review \
  --issue 47 \
  --cycle 2 \
  --agent review-approve-status-only \
  --model fake-model \
  --context-file "${SMOKE_CONTEXT}" \
  --prompt-file "${SMOKE_PROMPT}" \
  --log-file "${REVIEW_SYNTH_INSTRUCTIONS_LOG}" \
  --done-file "${REVIEW_SYNTH_INSTRUCTIONS_DONE}" \
  --result-file "${REVIEW_SYNTH_INSTRUCTIONS_RESULT}" \
  --state-file "${REVIEW_SYNTH_INSTRUCTIONS_STATE}" \
  --repo-root "${SMOKE_REPO_ROOT}" \
  --issue-dir "${REVIEW_SYNTH_INSTRUCTIONS_DIR}" \
  --status-file "${SMOKE_STATUS}" \
  --events-log "${SMOKE_EVENTS}" \
  --poll-seconds 1 >/dev/null
assert_json_file "${REVIEW_SYNTH_INSTRUCTIONS_FILE}" "approve review instructions are synthesized from valid status"
assert_file_contains "${REVIEW_SYNTH_INSTRUCTIONS_FILE}" '"source": "dispatcher-synthesized"' "synthesized review instructions are auditable"
assert_file_contains "${REVIEW_SYNTH_INSTRUCTIONS_STATE}" '"state": "completed"' "approve status-only worker completes after instructions synthesis"

REVIEW_MISSING_INSTRUCTIONS_DIR="${SMOKE_PROJECT}/.run-with-it/issues/48"
REVIEW_MISSING_INSTRUCTIONS_RESULT="${REVIEW_MISSING_INSTRUCTIONS_DIR}/workers/review/cycle-2-status.json"
REVIEW_MISSING_INSTRUCTIONS_LOG="${REVIEW_MISSING_INSTRUCTIONS_DIR}/workers/review/cycle-2.log"
REVIEW_MISSING_INSTRUCTIONS_DONE="${REVIEW_MISSING_INSTRUCTIONS_DIR}/workers/review/cycle-2.done"
REVIEW_MISSING_INSTRUCTIONS_STATE="${REVIEW_MISSING_INSTRUCTIONS_DIR}/workers/review/cycle-2.state.json"
REVIEW_MISSING_INSTRUCTIONS_OUTPUT="${WORK_DIR}/review-missing-instructions.out"
set +e
PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/scripts/run-with-it-dispatch.sh" \
  --asset-root "${SMOKE_ASSET_ROOT}" \
  --role review \
  --issue 48 \
  --cycle 2 \
  --agent review-revise-status-only \
  --model fake-model \
  --context-file "${SMOKE_CONTEXT}" \
  --prompt-file "${SMOKE_PROMPT}" \
  --log-file "${REVIEW_MISSING_INSTRUCTIONS_LOG}" \
  --done-file "${REVIEW_MISSING_INSTRUCTIONS_DONE}" \
  --result-file "${REVIEW_MISSING_INSTRUCTIONS_RESULT}" \
  --state-file "${REVIEW_MISSING_INSTRUCTIONS_STATE}" \
  --repo-root "${SMOKE_REPO_ROOT}" \
  --issue-dir "${REVIEW_MISSING_INSTRUCTIONS_DIR}" \
  --status-file "${SMOKE_STATUS}" \
  --events-log "${SMOKE_EVENTS}" \
  --poll-seconds 1 >"${REVIEW_MISSING_INSTRUCTIONS_OUTPUT}" 2>&1
review_missing_instructions_status="$?"
set -e
[[ "${review_missing_instructions_status}" != "0" ]] || fail "revise review without instructions must not complete"
assert_file_contains "${REVIEW_MISSING_INSTRUCTIONS_OUTPUT}" "reason=missing-review-instructions-artifact" "revise review without instructions fails with precise reason"
assert_file_contains "${REVIEW_MISSING_INSTRUCTIONS_STATE}" '"stall_reason": "missing-review-instructions-artifact"' "review state records missing instructions reason"

REVIEW_CANONICAL_RETRY_DIR="${SMOKE_PROJECT}/.run-with-it/issues/51"
REVIEW_CANONICAL_RETRY_RESULT="${REVIEW_CANONICAL_RETRY_DIR}/workers/review/cycle-1-attempt-2-status.json"
REVIEW_CANONICAL_RETRY_INSTRUCTIONS="${REVIEW_CANONICAL_RETRY_DIR}/workers/review/cycle-1-attempt-2-instructions.json"
REVIEW_CANONICAL_RETRY_LOG="${REVIEW_CANONICAL_RETRY_DIR}/workers/review/cycle-1-attempt-2.log"
REVIEW_CANONICAL_RETRY_DONE="${REVIEW_CANONICAL_RETRY_DIR}/workers/review/cycle-1-attempt-2.done"
REVIEW_CANONICAL_RETRY_STATE="${REVIEW_CANONICAL_RETRY_DIR}/workers/review/cycle-1-attempt-2.state.json"
PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/scripts/run-with-it-dispatch.sh" \
  --asset-root "${SMOKE_ASSET_ROOT}" \
  --role review \
  --issue 51 \
  --cycle 1 \
  --agent review-canonical-retry \
  --model fake-model \
  --context-file "${SMOKE_CONTEXT}" \
  --prompt-file "${SMOKE_PROMPT}" \
  --log-file "${REVIEW_CANONICAL_RETRY_LOG}" \
  --done-file "${REVIEW_CANONICAL_RETRY_DONE}" \
  --result-file "${REVIEW_CANONICAL_RETRY_RESULT}" \
  --state-file "${REVIEW_CANONICAL_RETRY_STATE}" \
  --repo-root "${SMOKE_REPO_ROOT}" \
  --issue-dir "${REVIEW_CANONICAL_RETRY_DIR}" \
  --status-file "${SMOKE_STATUS}" \
  --events-log "${SMOKE_EVENTS}" \
  --poll-seconds 1 >/dev/null
assert_json_file "${REVIEW_CANONICAL_RETRY_RESULT}" "attempt-specific review status is repaired from canonical retry output"
assert_json_file "${REVIEW_CANONICAL_RETRY_INSTRUCTIONS}" "attempt-specific review instructions are repaired from canonical retry output"
assert_file_contains "${REVIEW_CANONICAL_RETRY_RESULT}" '"source": "dispatcher-copied-from-canonical-retry"' "canonical retry status repair is auditable"
assert_file_contains "${REVIEW_CANONICAL_RETRY_STATE}" '"state": "completed"' "canonical retry output completes attempt-specific dispatcher monitoring"

COMPLEXITY_CANONICAL_RETRY_DIR="${SMOKE_PROJECT}/.run-with-it/issues/52"
COMPLEXITY_CANONICAL_RETRY_RESULT="${COMPLEXITY_CANONICAL_RETRY_DIR}/workers/complexity/cycle-1-attempt-2-result.json"
COMPLEXITY_CANONICAL_RETRY_LOG="${COMPLEXITY_CANONICAL_RETRY_DIR}/workers/complexity/cycle-1-attempt-2.log"
COMPLEXITY_CANONICAL_RETRY_DONE="${COMPLEXITY_CANONICAL_RETRY_DIR}/workers/complexity/cycle-1-attempt-2.done"
COMPLEXITY_CANONICAL_RETRY_STATE="${COMPLEXITY_CANONICAL_RETRY_DIR}/workers/complexity/cycle-1-attempt-2.state.json"
PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/scripts/run-with-it-dispatch.sh" \
  --asset-root "${SMOKE_ASSET_ROOT}" \
  --role complexity \
  --issue 52 \
  --cycle 1 \
  --agent complexity-canonical-retry \
  --model fake-model \
  --context-file "${SMOKE_CONTEXT}" \
  --prompt-file "${SMOKE_PROMPT}" \
  --log-file "${COMPLEXITY_CANONICAL_RETRY_LOG}" \
  --done-file "${COMPLEXITY_CANONICAL_RETRY_DONE}" \
  --result-file "${COMPLEXITY_CANONICAL_RETRY_RESULT}" \
  --state-file "${COMPLEXITY_CANONICAL_RETRY_STATE}" \
  --repo-root "${SMOKE_REPO_ROOT}" \
  --issue-dir "${COMPLEXITY_CANONICAL_RETRY_DIR}" \
  --status-file "${SMOKE_STATUS}" \
  --events-log "${SMOKE_EVENTS}" \
  --poll-seconds 1 >/dev/null
assert_json_file "${COMPLEXITY_CANONICAL_RETRY_RESULT}" "attempt-specific complexity result is repaired from canonical retry output"
assert_file_contains "${COMPLEXITY_CANONICAL_RETRY_RESULT}" '"source": "dispatcher-copied-from-canonical-retry"' "canonical complexity retry repair is auditable"
assert_file_contains "${COMPLEXITY_CANONICAL_RETRY_STATE}" '"state": "completed"' "canonical complexity retry output completes attempt-specific dispatcher monitoring"

COMPLEXITY_STDOUT_DIR="${SMOKE_PROJECT}/.run-with-it/issues/50"
COMPLEXITY_STDOUT_RESULT="${COMPLEXITY_STDOUT_DIR}/workers/complexity/cycle-1-result.json"
COMPLEXITY_STDOUT_LOG="${COMPLEXITY_STDOUT_DIR}/workers/complexity/cycle-1.log"
COMPLEXITY_STDOUT_DONE="${COMPLEXITY_STDOUT_DIR}/workers/complexity/cycle-1.done"
COMPLEXITY_STDOUT_STATE="${COMPLEXITY_STDOUT_DIR}/workers/complexity/cycle-1.state.json"
PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/scripts/run-with-it-dispatch.sh" \
  --asset-root "${SMOKE_ASSET_ROOT}" \
  --role complexity \
  --issue 50 \
  --cycle 1 \
  --agent stdout-complexity \
  --model fake-model \
  --context-file "${SMOKE_CONTEXT}" \
  --prompt-file "${SMOKE_PROMPT}" \
  --log-file "${COMPLEXITY_STDOUT_LOG}" \
  --done-file "${COMPLEXITY_STDOUT_DONE}" \
  --result-file "${COMPLEXITY_STDOUT_RESULT}" \
  --state-file "${COMPLEXITY_STDOUT_STATE}" \
  --repo-root "${SMOKE_REPO_ROOT}" \
  --issue-dir "${COMPLEXITY_STDOUT_DIR}" \
  --status-file "${SMOKE_STATUS}" \
  --events-log "${SMOKE_EVENTS}" \
  --poll-seconds 1 >/dev/null
assert_json_file "${COMPLEXITY_STDOUT_RESULT}" "valid complexity stdout JSON is synthesized into result artifact"
assert_file_contains "${COMPLEXITY_STDOUT_RESULT}" '"level": "easy"' "synthesized complexity artifact preserves level"
assert_file_contains "${COMPLEXITY_STDOUT_RESULT}" '"source": "dispatcher-synthesized-from-log"' "synthesized complexity artifact is auditable"
assert_file_contains "${COMPLEXITY_STDOUT_STATE}" '"state": "completed"' "complexity stdout-only worker completes after result synthesis"

COMPLEXITY_HANG_DIR="${SMOKE_PROJECT}/.run-with-it/issues/53"
COMPLEXITY_HANG_RESULT="${COMPLEXITY_HANG_DIR}/workers/complexity/cycle-1-result.json"
COMPLEXITY_HANG_LOG="${COMPLEXITY_HANG_DIR}/workers/complexity/cycle-1.log"
COMPLEXITY_HANG_DONE="${COMPLEXITY_HANG_DIR}/workers/complexity/cycle-1.done"
COMPLEXITY_HANG_STATE="${COMPLEXITY_HANG_DIR}/workers/complexity/cycle-1.state.json"
COMPLEXITY_HANG_OUTPUT="${WORK_DIR}/complexity-hang.out"
set +e
PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/scripts/run-with-it-dispatch.sh" \
  --asset-root "${SMOKE_ASSET_ROOT}" \
  --role complexity \
  --issue 53 \
  --cycle 1 \
  --agent hanging-complexity \
  --model fake-model \
  --context-file "${SMOKE_CONTEXT}" \
  --prompt-file "${SMOKE_PROMPT}" \
  --log-file "${COMPLEXITY_HANG_LOG}" \
  --done-file "${COMPLEXITY_HANG_DONE}" \
  --result-file "${COMPLEXITY_HANG_RESULT}" \
  --state-file "${COMPLEXITY_HANG_STATE}" \
  --repo-root "${SMOKE_REPO_ROOT}" \
  --issue-dir "${COMPLEXITY_HANG_DIR}" \
  --status-file "${SMOKE_STATUS}" \
  --events-log "${SMOKE_EVENTS}" \
  --poll-seconds 1 \
  --quiet-seconds 1 \
  --stall-seconds 1 >"${COMPLEXITY_HANG_OUTPUT}" 2>&1
complexity_hang_status="$?"
set -e
[[ "${complexity_hang_status}" != "0" ]] || fail "stalled complexity worker must fail instead of hanging forever"
assert_file_contains "${COMPLEXITY_HANG_OUTPUT}" "STATUS|type=worker-stall-timeout|issue=53|role=complexity|cycle=1" "stalled complexity worker is terminated"
assert_file_contains "${COMPLEXITY_HANG_OUTPUT}" "STATUS|type=dispatch-failed|issue=53|role=complexity|cycle=1" "stalled complexity worker exits dispatcher as failed"
assert_file_contains "${COMPLEXITY_HANG_STATE}" '"state": "failed"' "stalled complexity worker records failed state"
assert_file_contains "${COMPLEXITY_HANG_STATE}" '"stall_reason": "alive-but-silent"' "stalled complexity worker records precise reason"

COMPLEXITY_INVALID_DIR="${SMOKE_PROJECT}/.run-with-it/issues/49"
COMPLEXITY_INVALID_RESULT="${COMPLEXITY_INVALID_DIR}/workers/complexity/cycle-1-result.json"
COMPLEXITY_INVALID_LOG="${COMPLEXITY_INVALID_DIR}/workers/complexity/cycle-1.log"
COMPLEXITY_INVALID_DONE="${COMPLEXITY_INVALID_DIR}/workers/complexity/cycle-1.done"
COMPLEXITY_INVALID_STATE="${COMPLEXITY_INVALID_DIR}/workers/complexity/cycle-1.state.json"
COMPLEXITY_INVALID_OUTPUT="${WORK_DIR}/complexity-invalid.out"
set +e
PATH="${SMOKE_BIN}:${PATH}" "${SMOKE_ASSET_ROOT}/scripts/run-with-it-dispatch.sh" \
  --asset-root "${SMOKE_ASSET_ROOT}" \
  --role complexity \
  --issue 49 \
  --cycle 1 \
  --agent invalid-complexity \
  --model fake-model \
  --context-file "${SMOKE_CONTEXT}" \
  --prompt-file "${SMOKE_PROMPT}" \
  --log-file "${COMPLEXITY_INVALID_LOG}" \
  --done-file "${COMPLEXITY_INVALID_DONE}" \
  --result-file "${COMPLEXITY_INVALID_RESULT}" \
  --state-file "${COMPLEXITY_INVALID_STATE}" \
  --repo-root "${SMOKE_REPO_ROOT}" \
  --issue-dir "${COMPLEXITY_INVALID_DIR}" \
  --status-file "${SMOKE_STATUS}" \
  --events-log "${SMOKE_EVENTS}" \
  --poll-seconds 1 >"${COMPLEXITY_INVALID_OUTPUT}" 2>&1
complexity_invalid_status="$?"
set -e
[[ "${complexity_invalid_status}" != "0" ]] || fail "invalid complexity JSON must not complete"
assert_file_contains "${COMPLEXITY_INVALID_OUTPUT}" "reason=invalid-complexity-result-artifact" "invalid complexity output has precise failure reason"
assert_file_contains "${COMPLEXITY_INVALID_STATE}" '"stall_reason": "invalid-complexity-result-artifact"' "complexity state records invalid artifact reason"

echo "PASS: run-with-it dispatcher contract"
