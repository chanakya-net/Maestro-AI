#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER_PATH="${ROOT_DIR}/assets/run-agent.ps1"
PS_CMD="${PWSH:-}"
if [[ -z "$PS_CMD" ]]; then
  PS_CMD="$(command -v pwsh || command -v powershell.exe || command -v powershell || true)"
fi

if [[ -z "$PS_CMD" ]]; then
  echo "SKIP: PowerShell unavailable for run-agent.ps1 status bus contract"
  exit 0
fi

fail() {
  echo "FAIL: $1" >&2
  if [[ -n "${STDOUT_FILE:-}" && -f "$STDOUT_FILE" ]]; then
    sed 's/^/STDOUT: /' "$STDOUT_FILE" >&2
  fi
  if [[ -n "${STDERR_FILE:-}" && -f "$STDERR_FILE" ]]; then
    sed 's/^/STDERR: /' "$STDERR_FILE" >&2
  fi
  exit 1
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"
  [[ "$haystack" == *"$needle"* ]] || fail "$message (missing: $needle)"
}

assert_not_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"
  [[ "$haystack" != *"$needle"* ]] || fail "$message (unexpected: $needle)"
}

assert_equals() {
  local expected="$1"
  local actual="$2"
  local message="$3"
  [[ "$expected" == "$actual" ]] || fail "$message (expected: $expected, actual: $actual)"
}

BASE_DIR="$(mktemp -d)"
WORK_DIR="${BASE_DIR}/with spaces"
mkdir -p "$WORK_DIR"
cleanup() {
  rm -rf "$BASE_DIR"
}
trap cleanup EXIT

CONTEXT_FILE="${WORK_DIR}/context.md"
PROMPT_FILE="${WORK_DIR}/prompt.md"
FAKE_AGENT="${WORK_DIR}/fake-agent.ps1"
CUSTOM_REGISTRY="${WORK_DIR}/registry.json"
STATUS_FILE="${WORK_DIR}/status/current.txt"
EVENTS_LOG="${WORK_DIR}/status/events.log"
ROLE_LOG="${WORK_DIR}/impl/issue-42-impl-cycle-1.log"
DONE_FILE="${WORK_DIR}/done/issue-42-impl.done"
STATE_FILE="${WORK_DIR}/impl/issue-42-impl-cycle-1.state.json"
STDOUT_FILE="${WORK_DIR}/stdout.txt"
STDERR_FILE="${WORK_DIR}/stderr.txt"

printf 'Issue context\n' > "$CONTEXT_FILE"
printf 'Do the work\n' > "$PROMPT_FILE"
mkdir -p "$(dirname "$DONE_FILE")"
printf 'stale done file\n' > "$DONE_FILE"

cat > "$FAKE_AGENT" <<'PS1'
param([string]$Model, [string]$Prompt)
if ($env:FAKE_AGENT_SLEEP_SECONDS) { Start-Sleep -Seconds ([int]$env:FAKE_AGENT_SLEEP_SECONDS) }
Write-Output "STATUS|type=heartbeat|issue=42|role=impl|phase=testing|progress=running focused tests"
Write-Output "fake-agent stdout"
[Console]::Error.WriteLine("fake-agent stderr")
[Console]::Out.Write("partial stdout without newline")
[Console]::Error.Write("partial stderr without newline")
PS1

cat > "$CUSTOM_REGISTRY" <<JSON
{
  "schema_version": 1,
  "aliases": {},
  "agents": {
    "fake": {
      "display_name": "Fake Agent",
      "detection": { "command": "${PS_CMD}", "args": ["-NoProfile", "-File", "${FAKE_AGENT}"] },
      "invocation": {
        "command": "${PS_CMD}",
        "args_template": ["-NoProfile", "-File", "${FAKE_AGENT}", "{{prompt}}"],
        "prompt_argument_template": "{{prompt}}"
      },
      "permission_modes": { "default": "", "available": [""] },
      "model": { "default": "fake-default", "flag_template": "--model {{model}}", "known_models": ["fake-default"] },
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

AGENT_REGISTRY_FILE="$CUSTOM_REGISTRY" \
  AGENT=fake \
  CONTEXT_PAYLOAD_FILE="$CONTEXT_FILE" \
  PROMPT_FILE="$PROMPT_FILE" \
  RUN_WITH_IT_STATUS_FILE="$STATUS_FILE" \
  RUN_WITH_IT_EVENTS_LOG="$EVENTS_LOG" \
  RUN_WITH_IT_LOG_FILE="$ROLE_LOG" \
  RUN_WITH_IT_DONE_FILE="$DONE_FILE" \
  RUN_WITH_IT_STATE_FILE="$STATE_FILE" \
  RUN_WITH_IT_ROLE=impl \
  RUN_WITH_IT_ISSUE=42 \
  UNATTENDED=1 \
  "$PS_CMD" -NoProfile -File "$RUNNER_PATH" >"$STDOUT_FILE" 2>"$STDERR_FILE"

stdout_output="$(<"$STDOUT_FILE")"
stderr_output="$(<"$STDERR_FILE")"
status_current="$(tr -d '\r' < "$STATUS_FILE")"
status_events="$(tr -d '\r' < "$EVENTS_LOG")"
role_log="$(tr -d '\r' < "$ROLE_LOG")"
done_signal="$(tr -d '\r' < "$DONE_FILE")"

assert_not_contains "$stdout_output" "STATUS|type=heartbeat|issue=42|role=impl|phase=testing|progress=running focused tests" "runner suppresses heartbeat stdout"
assert_contains "$stdout_output" "fake-agent stdout" "runner preserves normal stdout"
assert_contains "$stderr_output" "fake-agent stderr" "runner preserves normal stderr"
assert_contains "$stderr_output" "STATUS|type=agent-complete|issue=42|role=impl|agent=fake|model=fake-default|status=success" "runner prints agent-complete status"
assert_contains "$status_events" "STATUS|type=agent-start|issue=42|role=impl|agent=fake|model=fake-default" "runner writes agent-start to event log"
assert_contains "$status_events" "STATUS|type=heartbeat|issue=42|role=impl|phase=testing|progress=running focused tests" "runner forwards heartbeat to event log"
assert_equals "STATUS|type=agent-complete|issue=42|role=impl|agent=fake|model=fake-default|status=success" "$status_current" "runner writes latest status"
assert_contains "$role_log" "STATUS|type=agent-start|issue=42|role=impl|agent=fake|model=fake-default" "runner writes agent-start to role log"
assert_contains "$role_log" "STATUS|type=heartbeat|issue=42|role=impl|phase=testing|progress=running focused tests" "runner mirrors heartbeat to role log"
assert_contains "$role_log" "fake-agent stdout" "runner captures stdout"
assert_contains "$role_log" "fake-agent stderr" "runner captures stderr"
assert_contains "$role_log" "partial stdout without newline" "runner captures unterminated stdout"
assert_contains "$role_log" "partial stderr without newline" "runner captures unterminated stderr"
assert_contains "$done_signal" "DONE|issue=42|role=impl|agent=fake|model=fake-default|status=success|source=runner-exit" "runner writes done sentinel"
if [[ "$done_signal" == *"stale done file"* ]]; then
  fail "runner must remove stale done sentinel before starting"
fi

QUIET_EVENTS_LOG="${WORK_DIR}/quiet/events.log"
QUIET_ROLE_LOG="${WORK_DIR}/quiet/role.log"
QUIET_DONE_FILE="${WORK_DIR}/quiet/done"
AGENT_REGISTRY_FILE="$CUSTOM_REGISTRY" \
  AGENT=fake \
  CONTEXT_PAYLOAD_FILE="$CONTEXT_FILE" \
  PROMPT_FILE="$PROMPT_FILE" \
  RUN_WITH_IT_EVENTS_LOG="$QUIET_EVENTS_LOG" \
  RUN_WITH_IT_LOG_FILE="$QUIET_ROLE_LOG" \
  RUN_WITH_IT_DONE_FILE="$QUIET_DONE_FILE" \
  RUN_WITH_IT_ROLE=impl \
  RUN_WITH_IT_ISSUE=42 \
  RUN_WITH_IT_HEARTBEAT_SECONDS=1 \
  FAKE_AGENT_SLEEP_SECONDS=3 \
  UNATTENDED=1 \
  "$PS_CMD" -NoProfile -File "$RUNNER_PATH" >/dev/null 2>/dev/null
wrapper_heartbeat_count="$(grep -Fc 'STATUS|type=wrapper-heartbeat|' "$QUIET_EVENTS_LOG" || true)"
[[ "$wrapper_heartbeat_count" -ge 2 ]] || fail "PowerShell quiet child should receive periodic wrapper heartbeats"
quiet_last_event="$(tail -n 1 "$QUIET_EVENTS_LOG" | tr -d '\r')"
assert_contains "$quiet_last_event" 'STATUS|type=agent-complete|' "PowerShell wrapper heartbeat stops before terminal event"

INVALID_HEARTBEAT_EVENTS_LOG="${WORK_DIR}/invalid-heartbeat/events.log"
INVALID_HEARTBEAT_ROLE_LOG="${WORK_DIR}/invalid-heartbeat/role.log"
INVALID_HEARTBEAT_DONE_FILE="${WORK_DIR}/invalid-heartbeat/done"
set +e
AGENT_REGISTRY_FILE="$CUSTOM_REGISTRY" \
  AGENT=fake \
  CONTEXT_PAYLOAD_FILE="$CONTEXT_FILE" \
  PROMPT_FILE="$PROMPT_FILE" \
  RUN_WITH_IT_EVENTS_LOG="$INVALID_HEARTBEAT_EVENTS_LOG" \
  RUN_WITH_IT_LOG_FILE="$INVALID_HEARTBEAT_ROLE_LOG" \
  RUN_WITH_IT_DONE_FILE="$INVALID_HEARTBEAT_DONE_FILE" \
  RUN_WITH_IT_ROLE=impl \
  RUN_WITH_IT_ISSUE=42 \
  RUN_WITH_IT_HEARTBEAT_SECONDS=invalid \
  FAKE_AGENT_SLEEP_SECONDS=1 \
  UNATTENDED=1 \
  "$PS_CMD" -NoProfile -File "$RUNNER_PATH" >/dev/null 2>/dev/null
invalid_heartbeat_status="$?"
set -e
assert_equals "0" "$invalid_heartbeat_status" "PowerShell malformed heartbeat value falls back instead of terminating"
invalid_heartbeat_last_event="$(tail -n 1 "$INVALID_HEARTBEAT_EVENTS_LOG" | tr -d '\r')"
assert_contains "$invalid_heartbeat_last_event" 'STATUS|type=agent-complete|' "PowerShell malformed heartbeat fallback still completes the agent"

for model in gpt-5.6-luna gpt-5.6-terra gpt-5.6-sol; do
  output="$(REPO_ROOT="${ROOT_DIR}" \
    "$PS_CMD" -NoProfile -File "$RUNNER_PATH" \
    --agent codex \
    --model "$model" \
    --context-file "$CONTEXT_FILE" \
    --prompt-file "$PROMPT_FILE" \
    --dry-run \
    --unattended)"
  assert_contains "$output" "'--model' '$model'" "PowerShell runner uses canonical $model ID"
  assert_contains "$output" "'-c' 'model_reasoning_effort=high'" "PowerShell runner applies high reasoning to $model"
done

codex_xhigh_output="$(REPO_ROOT="${ROOT_DIR}" \
  "$PS_CMD" -NoProfile -File "$RUNNER_PATH" \
  --agent codex \
  --model gpt-5.6-sol \
  --effort xhigh \
  --context-file "$CONTEXT_FILE" \
  --prompt-file "$PROMPT_FILE" \
  --dry-run \
  --unattended)"
assert_contains "$codex_xhigh_output" "'model_reasoning_effort=xhigh'" "PowerShell Codex runner renders xhigh effort"

claude_xhigh_output="$(REPO_ROOT="${ROOT_DIR}" \
  "$PS_CMD" -NoProfile -File "$RUNNER_PATH" \
  --agent claude \
  --model claude-opus-4-8 \
  --effort xhigh \
  --context-file "$CONTEXT_FILE" \
  --prompt-file "$PROMPT_FILE" \
  --dry-run \
  --unattended)"
assert_contains "$claude_xhigh_output" "'--effort' 'xhigh'" "PowerShell Claude runner renders xhigh effort"

precedence_output="$(AGENT_EXTRA_ARGS='-c model_reasoning_effort=medium' \
  REPO_ROOT="${ROOT_DIR}" \
  "$PS_CMD" -NoProfile -File "$RUNNER_PATH" \
  --agent codex \
  --model gpt-5.6-sol \
  --context-file "$CONTEXT_FILE" \
  --prompt-file "$PROMPT_FILE" \
  --dry-run \
  --unattended)"
case "$precedence_output" in
  *"model_reasoning_effort=medium"*"model_reasoning_effort=high"*) ;;
  *) fail "PowerShell registry high reasoning must follow caller extra arguments" ;;
esac

echo "PASS: run-agent.ps1 status bus contract"
