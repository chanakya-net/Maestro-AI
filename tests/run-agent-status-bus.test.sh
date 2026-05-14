#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER_PATH="${ROOT_DIR}/assets/run-agent.sh"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"

  if [[ "${haystack}" != *"${needle}"* ]]; then
    fail "${message} (missing: ${needle})"
  fi
}

assert_equals() {
  local expected="$1"
  local actual="$2"
  local message="$3"

  if [[ "${expected}" != "${actual}" ]]; then
    fail "${message} (expected: ${expected}, actual: ${actual})"
  fi
}

WORK_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

CONTEXT_FILE="${WORK_DIR}/context.md"
PROMPT_FILE="${WORK_DIR}/prompt.md"
FAKE_BIN="${WORK_DIR}/bin"
CUSTOM_REGISTRY="${WORK_DIR}/registry.json"
STATUS_FILE="${WORK_DIR}/status/current.txt"
EVENTS_LOG="${WORK_DIR}/status/events.log"
ROLE_LOG="${WORK_DIR}/impl/issue-42-impl-cycle-1.log"
DONE_FILE="${WORK_DIR}/done/issue-42-impl.done"
STDOUT_FILE="${WORK_DIR}/stdout.txt"
STDERR_FILE="${WORK_DIR}/stderr.txt"

mkdir -p "${FAKE_BIN}"
printf 'Issue context\n' > "${CONTEXT_FILE}"
printf 'Do the work\n' > "${PROMPT_FILE}"
mkdir -p "$(dirname "${DONE_FILE}")"
printf 'stale done file\n' > "${DONE_FILE}"
cat > "${FAKE_BIN}/fake-agent" <<'SH'
#!/usr/bin/env bash
printf 'STATUS|type=heartbeat|issue=42|role=impl|phase=testing|progress=running focused tests\n'
printf 'fake-agent done\n'
SH
chmod +x "${FAKE_BIN}/fake-agent"

cat > "${CUSTOM_REGISTRY}" <<JSON
{
  "schema_version": 1,
  "aliases": {},
  "agents": {
    "fake": {
      "display_name": "Fake Agent",
      "detection": {
        "command": "fake-agent",
        "args": ["--version"]
      },
      "invocation": {
        "command": "fake-agent",
        "args_template": ["{{prompt}}"],
        "prompt_argument_template": "{{prompt}}"
      },
      "permission_modes": {
        "default": "",
        "available": [""]
      },
      "model": {
        "default": "fake-default",
        "flag_template": "--model {{model}}",
        "known_models": ["fake-default"]
      },
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

PATH="${FAKE_BIN}:${PATH}" \
  AGENT_REGISTRY_FILE="${CUSTOM_REGISTRY}" \
  AGENT=fake \
  CONTEXT_PAYLOAD_FILE="${CONTEXT_FILE}" \
  PROMPT_FILE="${PROMPT_FILE}" \
  RUN_WITH_IT_STATUS_FILE="${STATUS_FILE}" \
  RUN_WITH_IT_EVENTS_LOG="${EVENTS_LOG}" \
  RUN_WITH_IT_LOG_FILE="${ROLE_LOG}" \
  RUN_WITH_IT_DONE_FILE="${DONE_FILE}" \
  RUN_WITH_IT_ROLE=impl \
  RUN_WITH_IT_ISSUE=42 \
  UNATTENDED=1 \
  "${RUNNER_PATH}" >"${STDOUT_FILE}" 2>"${STDERR_FILE}"

stdout_output="$(<"${STDOUT_FILE}")"
stderr_output="$(<"${STDERR_FILE}")"
status_current="$(<"${STATUS_FILE}")"
status_events="$(<"${EVENTS_LOG}")"
role_log="$(<"${ROLE_LOG}")"
done_signal="$(<"${DONE_FILE}")"

assert_contains "${stdout_output}" "STATUS|type=heartbeat|issue=42|role=impl|phase=testing|progress=running focused tests" "runner preserves heartbeat stdout"
assert_contains "${stdout_output}" "fake-agent done" "runner preserves normal stdout"
assert_contains "${stderr_output}" "STATUS|type=agent-start|issue=42|role=impl|agent=fake|model=fake-default" "runner prints agent-start status"
assert_contains "${stderr_output}" "STATUS|type=agent-complete|issue=42|role=impl|agent=fake|model=fake-default|status=success" "runner prints agent-complete status"
assert_contains "${status_events}" "STATUS|type=agent-start|issue=42|role=impl|agent=fake|model=fake-default" "runner writes agent-start to event log"
assert_contains "${status_events}" "STATUS|type=heartbeat|issue=42|role=impl|phase=testing|progress=running focused tests" "runner forwards heartbeat to event log"
assert_contains "${status_events}" "STATUS|type=agent-complete|issue=42|role=impl|agent=fake|model=fake-default|status=success" "runner writes agent-complete to event log"
assert_equals "STATUS|type=agent-complete|issue=42|role=impl|agent=fake|model=fake-default|status=success" "${status_current}" "runner writes latest status to current status file"
assert_contains "${role_log}" "STATUS|type=agent-start|issue=42|role=impl|agent=fake|model=fake-default" "runner writes agent-start to role log"
assert_contains "${role_log}" "STATUS|type=heartbeat|issue=42|role=impl|phase=testing|progress=running focused tests" "runner mirrors agent stdout to role log"
assert_contains "${role_log}" "fake-agent done" "runner mirrors normal agent output to role log"
assert_contains "${role_log}" "STATUS|type=agent-complete|issue=42|role=impl|agent=fake|model=fake-default|status=success" "runner writes agent-complete to role log"
assert_contains "${done_signal}" "DONE|issue=42|role=impl|agent=fake|model=fake-default|status=success|source=runner-exit" "runner writes done sentinel on successful exit"
if [[ "${done_signal}" == *"stale done file"* ]]; then
  fail "runner must remove stale done sentinel before starting"
fi

runner_source="$(<"${RUNNER_PATH}")"
assert_contains "${runner_source}" 'wait "${stdout_forward_pid}"' "runner waits for stdout forwarder by explicit pid"
assert_contains "${runner_source}" 'wait "${stderr_forward_pid}"' "runner waits for stderr forwarder by explicit pid"
if [[ "${runner_source}" == *'wait  # drain forward_status_stream subshells before writing final status'* ]]; then
  fail "runner must not use bare wait in status bus path"
fi

echo "PASS: run-agent status bus contract"
