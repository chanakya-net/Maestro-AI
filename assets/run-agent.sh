#!/usr/bin/env bash

set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd -- "${SCRIPT_PATH%/*}" && pwd -P)"
REPO_ROOT="${REPO_ROOT:-$(cd -- "${SCRIPT_DIR}/.." && pwd -P)}"
AGENT_REGISTRY_FILE="${AGENT_REGISTRY_FILE:-${SCRIPT_DIR}/agent-registry.json}"

AGENT="${AGENT:-}"
MODEL="${MODEL:-}"
CONTEXT_PAYLOAD_FILE="${CONTEXT_PAYLOAD_FILE:-}"
PROMPT_FILE="${PROMPT_FILE:-${SCRIPT_DIR}/prompt.md}"
PRINT_PROMPT="${PRINT_PROMPT:-0}"
AGENT_PERMISSION_MODE="${AGENT_PERMISSION_MODE:-}"
AGENT_EXTRA_ARGS="${AGENT_EXTRA_ARGS:-}"
UNATTENDED="${UNATTENDED:-0}"

DRY_RUN=0
LIST_AGENTS=0
DETECTED_ONLY=0
LIST_MODELS_AGENT=""

fail() {
  echo "error: $1" >&2
  exit 1
}

normalize_telemetry_value() {
  local value="${1:-}"

  if [[ -z "${value}" ]]; then
    printf 'unknown\n'
    return
  fi

  value="${value//$'\r'/ }"
  value="${value//$'\n'/ }"
  printf '%s\n' "${value}"
}

emit_telemetry() {
  local status="$1"
  local telemetry_agent telemetry_model

  telemetry_agent="$(normalize_telemetry_value "${AGENT}")"
  telemetry_model="$(normalize_telemetry_value "${MODEL}")"

  printf 'STATUS|type=telemetry|agent=%s|model=%s|input_tokens=unknown|output_tokens=unknown|cache_hit_tokens=unknown|status=%s|source=runner-default\n' \
    "${telemetry_agent}" \
    "${telemetry_model}" \
    "${status}" >&2
}

usage() {
  cat <<'EOF'
Usage:
  run-agent.sh --agent <agent> [--model <model>] --context-file <file> --prompt-file <file> [--dry-run] [--unattended]
  run-agent.sh --list-agents [--detected-only]
  run-agent.sh --list-models <agent>

Environment equivalents:
  AGENT, MODEL, CONTEXT_PAYLOAD_FILE, PROMPT_FILE, PRINT_PROMPT, AGENT_PERMISSION_MODE, AGENT_REGISTRY_FILE, UNATTENDED
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent)
      [[ $# -ge 2 ]] || fail "--agent requires a value"
      AGENT="$2"
      shift 2
      ;;
    --model)
      [[ $# -ge 2 ]] || fail "--model requires a value"
      MODEL="$2"
      shift 2
      ;;
    --context-file|--context-payload-file)
      [[ $# -ge 2 ]] || fail "$1 requires a value"
      CONTEXT_PAYLOAD_FILE="$2"
      shift 2
      ;;
    --prompt-file)
      [[ $# -ge 2 ]] || fail "--prompt-file requires a value"
      PROMPT_FILE="$2"
      shift 2
      ;;
    --permission-mode)
      [[ $# -ge 2 ]] || fail "--permission-mode requires a value"
      AGENT_PERMISSION_MODE="$2"
      shift 2
      ;;
    --extra-arg)
      [[ $# -ge 2 ]] || fail "--extra-arg requires a value"
      AGENT_EXTRA_ARGS="${AGENT_EXTRA_ARGS:+${AGENT_EXTRA_ARGS} }$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --unattended)
      UNATTENDED=1
      shift
      ;;
    --list-agents)
      LIST_AGENTS=1
      shift
      ;;
    --detected-only)
      DETECTED_ONLY=1
      shift
      ;;
    --list-models)
      [[ $# -ge 2 ]] || fail "--list-models requires an agent"
      LIST_MODELS_AGENT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      fail "unknown argument: $1"
      ;;
    *)
      if [[ -z "${CONTEXT_PAYLOAD_FILE}" ]]; then
        CONTEXT_PAYLOAD_FILE="$1"
      elif [[ -z "${PROMPT_FILE}" || "${PROMPT_FILE}" == "${SCRIPT_DIR}/prompt.md" ]]; then
        PROMPT_FILE="$1"
      else
        fail "unexpected positional argument: $1"
      fi
      shift
      ;;
  esac
done

[[ -f "${AGENT_REGISTRY_FILE}" ]] || fail "agent registry file not found: ${AGENT_REGISTRY_FILE}"

JSON_PARSER=""
if command -v jq >/dev/null 2>&1; then
  JSON_PARSER="jq"
elif command -v python3 >/dev/null 2>&1; then
  JSON_PARSER="python3"
else
  fail "no JSON parser available; install jq or python3"
fi

json_jq() {
  jq -r "$@" "${AGENT_REGISTRY_FILE}"
}

json_py() {
  local action="$1"
  local arg="${2:-}"
  python3 - "${AGENT_REGISTRY_FILE}" "${action}" "${arg}" <<'PY'
import json
import os
import sys

path, action, arg = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, "r", encoding="utf-8") as handle:
    registry = json.load(handle)

agents = registry.get("agents", {})
aliases = registry.get("aliases", {})

def agent_id(value):
    return aliases.get(value, value)

def agent(value):
    return agents.get(agent_id(value), {})

if action == "normalize":
    print(agent_id(arg))
elif action == "agents":
    for key in agents:
        print(key)
elif action == "exists":
    sys.exit(0 if agent_id(arg) in agents else 1)
elif action == "display":
    print(agent(arg).get("display_name", ""))
elif action == "detect_command":
    print(agent(arg).get("detection", {}).get("command", ""))
elif action == "detect_args":
    for item in agent(arg).get("detection", {}).get("args", []):
        print(item)
elif action == "invoke_command":
    print(agent(arg).get("invocation", {}).get("command", ""))
elif action == "args_template":
    for item in agent(arg).get("invocation", {}).get("args_template", []):
        print(item)
elif action == "default_permission":
    print(agent(arg).get("permission_modes", {}).get("default", ""))
elif action == "default_model":
    print(agent(arg).get("model", {}).get("default", ""))
elif action == "model_flag_template":
    print(agent(arg).get("model", {}).get("flag_template", ""))
elif action == "known_models":
    for item in agent(arg).get("model", {}).get("known_models", []):
        print(item)
elif action == "requires_config":
    print("true" if agent(arg).get("user_model_configuration", {}).get("requires_user_model_config") else "false")
elif action == "skip_unconfigured":
    print("true" if agent(arg).get("user_model_configuration", {}).get("skip_when_unconfigured") else "false")
elif action == "skip_message":
    print(agent(arg).get("user_model_configuration", {}).get("skip_message", ""))
elif action == "config_paths":
    for item in agent(arg).get("user_model_configuration", {}).get("config_paths", []):
        print(os.path.expandvars(os.path.expanduser(item)))
else:
    raise SystemExit(f"unknown action: {action}")
PY
}

json_value() {
  local action="$1"
  local arg="${2:-}"

  if [[ "${JSON_PARSER}" == "jq" ]]; then
    case "${action}" in
      normalize) json_jq --arg a "${arg}" '.aliases[$a] // $a' ;;
      agents) json_jq '.agents | keys[]' ;;
      exists) jq -e --arg a "${arg}" '(.aliases[$a] // $a) as $id | has("agents") and (.agents[$id] != null)' "${AGENT_REGISTRY_FILE}" >/dev/null ;;
      display) json_jq --arg a "${arg}" '(.aliases[$a] // $a) as $id | .agents[$id].display_name // ""' ;;
      detect_command) json_jq --arg a "${arg}" '(.aliases[$a] // $a) as $id | .agents[$id].detection.command // ""' ;;
      detect_args) json_jq --arg a "${arg}" '(.aliases[$a] // $a) as $id | .agents[$id].detection.args[]?' ;;
      invoke_command) json_jq --arg a "${arg}" '(.aliases[$a] // $a) as $id | .agents[$id].invocation.command // ""' ;;
      args_template) json_jq --arg a "${arg}" '(.aliases[$a] // $a) as $id | .agents[$id].invocation.args_template[]?' ;;
      default_permission) json_jq --arg a "${arg}" '(.aliases[$a] // $a) as $id | .agents[$id].permission_modes.default // ""' ;;
      default_model) json_jq --arg a "${arg}" '(.aliases[$a] // $a) as $id | .agents[$id].model.default // ""' ;;
      model_flag_template) json_jq --arg a "${arg}" '(.aliases[$a] // $a) as $id | .agents[$id].model.flag_template // ""' ;;
      known_models) json_jq --arg a "${arg}" '(.aliases[$a] // $a) as $id | .agents[$id].model.known_models[]?' ;;
      requires_config) json_jq --arg a "${arg}" '(.aliases[$a] // $a) as $id | if (.agents[$id].user_model_configuration.requires_user_model_config // false) then "true" else "false" end' ;;
      skip_unconfigured) json_jq --arg a "${arg}" '(.aliases[$a] // $a) as $id | if (.agents[$id].user_model_configuration.skip_when_unconfigured // false) then "true" else "false" end' ;;
      skip_message) json_jq --arg a "${arg}" '(.aliases[$a] // $a) as $id | .agents[$id].user_model_configuration.skip_message // ""' ;;
      config_paths) json_jq --arg a "${arg}" '(.aliases[$a] // $a) as $id | .agents[$id].user_model_configuration.config_paths[]?' ;;
      *) fail "unknown JSON action: ${action}" ;;
    esac
  else
    json_py "${action}" "${arg}"
  fi
}

expand_config_path() {
  local path="$1"
  path="${path/#\~/${HOME:-}}"
  path="${path//\$HOME/${HOME:-}}"
  if [[ "${path}" == ./* ]]; then
    path="${REPO_ROOT}/${path#./}"
  fi
  printf '%s\n' "${path}"
}

agent_configured() {
  local agent="$1"
  [[ "$(json_value requires_config "${agent}")" == "true" ]] || return 0

  local config_path
  while IFS= read -r config_path; do
    config_path="$(expand_config_path "${config_path}")"
    [[ -f "${config_path}" ]] && return 0
  done < <(json_value config_paths "${agent}")

  return 1
}

agent_detection_reason() {
  local agent="$1"
  local command_name
  command_name="$(json_value detect_command "${agent}")"

  if [[ -z "${command_name}" ]]; then
    echo "missing detection command"
    return 1
  fi

  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "missing command: ${command_name}"
    return 1
  fi

  if ! agent_configured "${agent}"; then
    local message
    message="$(json_value skip_message "${agent}")"
    echo "${message:-missing user model configuration}"
    return 1
  fi

  echo "detected"
  return 0
}

list_agents() {
  local agent display reason status
  while IFS= read -r agent; do
    display="$(json_value display "${agent}")"
    if reason="$(agent_detection_reason "${agent}")"; then
      status="detected"
    else
      status="missing"
    fi

    if [[ "${DETECTED_ONLY}" == "1" && "${status}" != "detected" ]]; then
      continue
    fi

    printf '%s\t%s\t%s\t%s\n' "${agent}" "${display}" "${status}" "${reason}"
  done < <(json_value agents)
}

list_models() {
  local agent="$1"
  agent="$(json_value normalize "${agent}")"
  json_value exists "${agent}" || fail "unknown agent: ${agent}"

  local models
  models="$(json_value known_models "${agent}")"
  if [[ -z "${models}" ]]; then
    echo "No configured models for ${agent}."
    return 0
  fi

  printf '%s\n' "${models}"
}

if [[ "${LIST_AGENTS}" == "1" ]]; then
  list_agents
  exit 0
fi

if [[ -n "${LIST_MODELS_AGENT}" ]]; then
  list_models "${LIST_MODELS_AGENT}"
  exit 0
fi

[[ -n "${AGENT}" ]] || fail "agent is required. Pass --agent or set AGENT."
AGENT="$(json_value normalize "${AGENT}")"
json_value exists "${AGENT}" || fail "unknown agent: ${AGENT}"

[[ -n "${CONTEXT_PAYLOAD_FILE}" ]] || fail "context payload file is required. Pass --context-file or set CONTEXT_PAYLOAD_FILE."
[[ -f "${CONTEXT_PAYLOAD_FILE}" ]] || fail "context payload file not found: ${CONTEXT_PAYLOAD_FILE}"
[[ -f "${PROMPT_FILE}" ]] || fail "prompt file not found: ${PROMPT_FILE}"

if [[ -z "${MODEL}" ]]; then
  MODEL="$(json_value default_model "${AGENT}")"
fi

if [[ -z "${AGENT_PERMISSION_MODE}" ]]; then
  AGENT_PERMISSION_MODE="$(json_value default_permission "${AGENT}")"
fi

if [[ "${AGENT_PERMISSION_MODE}" == "safe" ]]; then
  AGENT_PERMISSION_MODE=""
fi

PAYLOAD_FILE="$(mktemp -t ai-skills-prompt.XXXXXX)"
cleanup_payload() {
  rm -f "${PAYLOAD_FILE}"
}
trap cleanup_payload EXIT

{
  cat "${CONTEXT_PAYLOAD_FILE}"
  printf '\nInstructions:\n\n'
  cat "${PROMPT_FILE}"
  printf '\n'
} > "${PAYLOAD_FILE}"

if [[ "${PRINT_PROMPT}" == "1" ]]; then
  cat "${PAYLOAD_FILE}"
  exit 0
fi

if [[ -n "${AGENT_PERMISSION_MODE}" && "${UNATTENDED}" != "1" ]]; then
  fail "unattended permission mode requires --unattended or UNATTENDED=1"
fi

prompt_payload="$(cat "${PAYLOAD_FILE}")"
invoke_command="$(json_value invoke_command "${AGENT}")"
[[ -n "${invoke_command}" ]] || fail "agent has no invocation command: ${AGENT}"

model_flag=""
if [[ -n "${MODEL}" ]]; then
  model_flag_template="$(json_value model_flag_template "${AGENT}")"
  model_flag="${model_flag_template//\{\{model\}\}/${MODEL}}"
fi

cmd=("${invoke_command}")
while IFS= read -r template_arg; do
  case "${template_arg}" in
    "{{prompt}}")
      cmd+=("${prompt_payload}")
      ;;
    "{{repo_root}}")
      cmd+=("${REPO_ROOT}")
      ;;
    "{{permission_mode}}")
      [[ -n "${AGENT_PERMISSION_MODE}" ]] && cmd+=("${AGENT_PERMISSION_MODE}")
      ;;
    "{{model_flag}}")
      if [[ -n "${model_flag}" ]]; then
        read -r -a model_parts <<< "${model_flag}"
        cmd+=("${model_parts[@]}")
      fi
      ;;
    "{{extra_args}}")
      if [[ -n "${AGENT_EXTRA_ARGS}" ]]; then
        read -r -a extra_parts <<< "${AGENT_EXTRA_ARGS}"
        cmd+=("${extra_parts[@]}")
      fi
      ;;
    *)
      rendered="${template_arg//\{\{repo_root\}\}/${REPO_ROOT}}"
      rendered="${rendered//\{\{permission_mode\}\}/${AGENT_PERMISSION_MODE}}"
      rendered="${rendered//\{\{model_flag\}\}/${model_flag}}"
      rendered="${rendered//\{\{extra_args\}\}/${AGENT_EXTRA_ARGS}}"
      rendered="${rendered//\{\{prompt\}\}/${prompt_payload}}"
      [[ -n "${rendered}" ]] && cmd+=("${rendered}")
      ;;
  esac
done < <(json_value args_template "${AGENT}")

print_command() {
  local part
  for part in "${cmd[@]}"; do
    printf '%q ' "${part}"
  done
  printf '\n'
}

if [[ "${DRY_RUN}" == "1" ]]; then
  print_command
  exit 0
fi

set +e
"${cmd[@]}"
command_status=$?
set -e

if [[ "${command_status}" == "0" ]]; then
  emit_telemetry "success"
else
  emit_telemetry "failed"
fi

exit "${command_status}"
