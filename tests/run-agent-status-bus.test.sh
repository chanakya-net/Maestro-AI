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

assert_not_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"

  if [[ "${haystack}" == *"${needle}"* ]]; then
    fail "${message} (unexpected: ${needle})"
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
if [[ -n "${FAKE_AGENT_PID_FILE:-}" ]]; then
  printf '%s\n' "$$" > "${FAKE_AGENT_PID_FILE}"
fi
if [[ -n "${FAKE_AGENT_SLEEP_SECONDS:-}" ]]; then
  sleep "${FAKE_AGENT_SLEEP_SECONDS}"
fi
printf 'STATUS|type=heartbeat|issue=42|role=impl|phase=testing|progress=running focused tests\n'
printf 'fake-agent done\n'
printf 'partial stdout without newline'
printf 'partial stderr without newline' >&2
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

assert_not_contains "${stdout_output}" "STATUS|type=heartbeat|issue=42|role=impl|phase=testing|progress=running focused tests" "runner suppresses heartbeat stdout"
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
assert_contains "${role_log}" "partial stdout without newline" "runner captures unterminated stdout in role log"
assert_contains "${role_log}" "partial stderr without newline" "runner captures unterminated stderr in role log"
assert_contains "${role_log}" "STATUS|type=agent-complete|issue=42|role=impl|agent=fake|model=fake-default|status=success" "runner writes agent-complete to role log"
assert_contains "${done_signal}" "DONE|issue=42|role=impl|agent=fake|model=fake-default|status=success|source=runner-exit" "runner writes done sentinel on successful exit"
if [[ "${done_signal}" == *"stale done file"* ]]; then
  fail "runner must remove stale done sentinel before starting"
fi

runner_source="$(<"${RUNNER_PATH}")"
assert_contains "${runner_source}" 'wait "${stdout_forward_pid}"' "runner waits for stdout forwarder by explicit pid"
assert_contains "${runner_source}" 'wait "${stderr_forward_pid}"' "runner waits for stderr forwarder by explicit pid"
assert_contains "${runner_source}" 'heartbeat_seconds=$((10#${RUN_WITH_IT_HEARTBEAT_SECONDS}))' "runner normalizes zero-padded heartbeat values"
if [[ "${runner_source}" == *'wait  # drain forward_status_stream subshells before writing final status'* ]]; then
  fail "runner must not use bare wait in status bus path"
fi

# A quiet non-streaming child receives wrapper-owned liveness heartbeats that
# stop before the terminal event is written.
QUIET_EVENTS_LOG="${WORK_DIR}/quiet/events.log"
QUIET_ROLE_LOG="${WORK_DIR}/quiet/role.log"
QUIET_DONE_FILE="${WORK_DIR}/quiet/done"
FAKE_AGENT_SLEEP_SECONDS=3 \
  RUN_WITH_IT_HEARTBEAT_SECONDS=1 \
  PATH="${FAKE_BIN}:${PATH}" \
  AGENT_REGISTRY_FILE="${CUSTOM_REGISTRY}" \
  AGENT=fake \
  CONTEXT_PAYLOAD_FILE="${CONTEXT_FILE}" \
  PROMPT_FILE="${PROMPT_FILE}" \
  RUN_WITH_IT_EVENTS_LOG="${QUIET_EVENTS_LOG}" \
  RUN_WITH_IT_LOG_FILE="${QUIET_ROLE_LOG}" \
  RUN_WITH_IT_DONE_FILE="${QUIET_DONE_FILE}" \
  RUN_WITH_IT_ROLE=impl \
  RUN_WITH_IT_ISSUE=42 \
  UNATTENDED=1 \
  "${RUNNER_PATH}" >/dev/null 2>/dev/null
wrapper_heartbeat_count="$(grep -Fc 'STATUS|type=wrapper-heartbeat|' "${QUIET_EVENTS_LOG}" || true)"
[[ "${wrapper_heartbeat_count}" -ge 2 ]] || fail "quiet child should receive periodic wrapper heartbeats"
quiet_last_event="$(tail -n 1 "${QUIET_EVENTS_LOG}")"
assert_contains "${quiet_last_event}" 'STATUS|type=agent-complete|' "wrapper heartbeat stops before terminal event"

# SIGKILL bypasses the runner EXIT trap. The heartbeat child must notice that
# its parent disappeared instead of appending false liveness forever.
ORPHAN_EVENTS_LOG="${WORK_DIR}/orphan/events.log"
ORPHAN_ROLE_LOG="${WORK_DIR}/orphan/role.log"
ORPHAN_DONE_FILE="${WORK_DIR}/orphan/done"
ORPHAN_AGENT_PID_FILE="${WORK_DIR}/orphan/agent.pid"
FAKE_AGENT_SLEEP_SECONDS=10 \
  FAKE_AGENT_PID_FILE="${ORPHAN_AGENT_PID_FILE}" \
  RUN_WITH_IT_HEARTBEAT_SECONDS=1 \
  PATH="${FAKE_BIN}:${PATH}" \
  AGENT_REGISTRY_FILE="${CUSTOM_REGISTRY}" \
  AGENT=fake \
  CONTEXT_PAYLOAD_FILE="${CONTEXT_FILE}" \
  PROMPT_FILE="${PROMPT_FILE}" \
  RUN_WITH_IT_EVENTS_LOG="${ORPHAN_EVENTS_LOG}" \
  RUN_WITH_IT_LOG_FILE="${ORPHAN_ROLE_LOG}" \
  RUN_WITH_IT_DONE_FILE="${ORPHAN_DONE_FILE}" \
  RUN_WITH_IT_ROLE=impl \
  RUN_WITH_IT_ISSUE=42 \
  UNATTENDED=1 \
  "${RUNNER_PATH}" >/dev/null 2>/dev/null &
orphan_runner_pid="$!"
for _ in {1..20}; do
  if [[ -f "${ORPHAN_EVENTS_LOG}" ]] && grep -Fq 'STATUS|type=wrapper-heartbeat|' "${ORPHAN_EVENTS_LOG}"; then
    break
  fi
  sleep 0.25
done
grep -Fq 'STATUS|type=wrapper-heartbeat|' "${ORPHAN_EVENTS_LOG}" || fail "orphan test runner did not emit its initial heartbeat"
orphan_children="$(pgrep -P "${orphan_runner_pid}" || true)"
kill -9 "${orphan_runner_pid}" 2>/dev/null || true
wait "${orphan_runner_pid}" 2>/dev/null || true
sleep 2
orphan_count_before="$(grep -Fc 'STATUS|type=wrapper-heartbeat|' "${ORPHAN_EVENTS_LOG}" || true)"
sleep 2
orphan_count_after="$(grep -Fc 'STATUS|type=wrapper-heartbeat|' "${ORPHAN_EVENTS_LOG}" || true)"
for child in ${orphan_children}; do
  kill "${child}" 2>/dev/null || true
done
if [[ -f "${ORPHAN_AGENT_PID_FILE}" ]]; then
  kill "$(<"${ORPHAN_AGENT_PID_FILE}")" 2>/dev/null || true
fi
assert_equals "${orphan_count_before}" "${orphan_count_after}" "wrapper heartbeat stops when its parent is SIGKILLed"

echo "PASS: run-agent status bus contract"
