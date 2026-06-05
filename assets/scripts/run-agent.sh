#!/usr/bin/env bash

set -eo pipefail

RUN_AGENT_BOOTSTRAP_PATH="${RUN_AGENT_BOOTSTRAP_PATH:-1}"
if [[ "${RUN_AGENT_BOOTSTRAP_PATH}" != "0" ]]; then
  for _path_dir in \
    /opt/homebrew/bin \
    /usr/local/bin \
    "${HOME:-}/.npm-global/bin" \
    "${HOME:-}/.local/bin" \
    "${HOME:-}/.cargo/bin" \
    "${HOME:-}/.bun/bin" \
    "${HOME:-}/.dotnet/tools"; do
    if [[ -d "${_path_dir}" && ":${PATH}:" != *":${_path_dir}:"* ]]; then
      PATH="${_path_dir}:${PATH}"
    fi
  done
  export PATH
  unset _path_dir
fi

SCRIPT_PATH="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd -- "${SCRIPT_PATH%/*}" && pwd -P)"
REPO_ROOT="${REPO_ROOT:-$(pwd -P)}"
if [[ -d "${REPO_ROOT}/.codegraph" ]] && command -v codegraph >/dev/null 2>&1; then
  (cd "${REPO_ROOT}" && codegraph unlock 2>/dev/null) || true
fi
AGENT_REGISTRY_FILE="${AGENT_REGISTRY_FILE:-${SCRIPT_DIR}/agent-registry.json}"

AGENT="${AGENT:-}"
MODEL="${MODEL:-}"
CONTEXT_PAYLOAD_FILE="${CONTEXT_PAYLOAD_FILE:-}"
PROMPT_FILE="${PROMPT_FILE:-${SCRIPT_DIR}/prompt.md}"
PRINT_PROMPT="${PRINT_PROMPT:-0}"
AGENT_PERMISSION_MODE="${AGENT_PERMISSION_MODE:-}"
AGENT_EXTRA_ARGS="${AGENT_EXTRA_ARGS:-}"
UNATTENDED="${UNATTENDED:-0}"
GUI_MODE="${GUI_MODE:-auto}"
RUN_WITH_IT_STATUS_FILE="${RUN_WITH_IT_STATUS_FILE:-}"
RUN_WITH_IT_EVENTS_LOG="${RUN_WITH_IT_EVENTS_LOG:-}"
RUN_WITH_IT_LOG_FILE="${RUN_WITH_IT_LOG_FILE:-}"
RUN_WITH_IT_DONE_FILE="${RUN_WITH_IT_DONE_FILE:-}"
RUN_WITH_IT_RESULT_FILE="${RUN_WITH_IT_RESULT_FILE:-}"
RUN_WITH_IT_ROLE="${RUN_WITH_IT_ROLE:-agent}"
RUN_WITH_IT_ISSUE="${RUN_WITH_IT_ISSUE:-unknown}"
RUN_WITH_IT_STATE_FILE="${RUN_WITH_IT_STATE_FILE:-}"
RUN_WITH_IT_ISSUE_DIR="${RUN_WITH_IT_ISSUE_DIR:-}"
export REPO_ROOT
export RUN_WITH_IT_STATUS_FILE
export RUN_WITH_IT_EVENTS_LOG
export RUN_WITH_IT_LOG_FILE
export RUN_WITH_IT_DONE_FILE
export RUN_WITH_IT_RESULT_FILE
export RUN_WITH_IT_STATE_FILE
export RUN_WITH_IT_ROLE
export RUN_WITH_IT_ISSUE
export RUN_WITH_IT_ISSUE_DIR

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

  local line
  line="$(printf 'STATUS|type=telemetry|agent=%s|model=%s|input_tokens=unknown|output_tokens=unknown|cache_hit_tokens=unknown|status=%s|source=runner-default' \
    "${telemetry_agent}" \
    "${telemetry_model}" \
    "${status}")"

  write_log_line "${line}"
  printf '%s\n' "${line}" >&2
}

write_log_line() {
  local line="$1"
  local log_dir

  if [[ -z "${RUN_WITH_IT_LOG_FILE}" ]]; then
    return 0
  fi

  log_dir="$(dirname -- "${RUN_WITH_IT_LOG_FILE}")"
  mkdir -p "${log_dir}"
  printf '%s\n' "${line}" >> "${RUN_WITH_IT_LOG_FILE}"
}

write_status_line() {
  local line="$1"
  local status_dir events_dir

  if [[ -n "${RUN_WITH_IT_STATUS_FILE}" ]]; then
    status_dir="$(dirname -- "${RUN_WITH_IT_STATUS_FILE}")"
    mkdir -p "${status_dir}"
    printf '%s\n' "${line}" > "${RUN_WITH_IT_STATUS_FILE}"
  fi

  if [[ -n "${RUN_WITH_IT_EVENTS_LOG}" ]]; then
    events_dir="$(dirname -- "${RUN_WITH_IT_EVENTS_LOG}")"
    mkdir -p "${events_dir}"
    printf '%s\n' "${line}" >> "${RUN_WITH_IT_EVENTS_LOG}"
  fi
}

prepare_done_file() {
  local done_dir

  if [[ -z "${RUN_WITH_IT_DONE_FILE}" ]]; then
    return 0
  fi

  done_dir="$(dirname -- "${RUN_WITH_IT_DONE_FILE}")"
  mkdir -p "${done_dir}"
  rm -f "${RUN_WITH_IT_DONE_FILE}"
}

write_done_file() {
  local status="$1"
  local source="$2"
  local done_dir line

  if [[ -z "${RUN_WITH_IT_DONE_FILE}" ]]; then
    return 0
  fi

  done_dir="$(dirname -- "${RUN_WITH_IT_DONE_FILE}")"
  mkdir -p "${done_dir}"
  line="$(printf 'DONE|issue=%s|role=%s|agent=%s|model=%s|status=%s|source=%s|completed_at=%s' \
    "$(normalize_telemetry_value "${RUN_WITH_IT_ISSUE}")" \
    "$(normalize_telemetry_value "${RUN_WITH_IT_ROLE}")" \
    "$(normalize_telemetry_value "${AGENT}")" \
    "$(normalize_telemetry_value "${MODEL}")" \
    "$(normalize_telemetry_value "${status}")" \
    "$(normalize_telemetry_value "${source}")" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)")"

  printf '%s\n' "${line}" >> "${RUN_WITH_IT_DONE_FILE}"
  write_log_line "${line}"
}

emit_run_status() {
  local type="$1"
  local status="${2:-}"
  local status_field=""

  if [[ -z "${RUN_WITH_IT_STATUS_FILE}" && -z "${RUN_WITH_IT_EVENTS_LOG}" && -z "${RUN_WITH_IT_LOG_FILE}" ]]; then
    return 0
  fi

  if [[ -n "${status}" ]]; then
    status_field="|status=${status}"
  fi

  local line
  line="$(printf 'STATUS|type=%s|issue=%s|role=%s|agent=%s|model=%s%s' \
    "${type}" \
    "$(normalize_telemetry_value "${RUN_WITH_IT_ISSUE}")" \
    "$(normalize_telemetry_value "${RUN_WITH_IT_ROLE}")" \
    "$(normalize_telemetry_value "${AGENT}")" \
    "$(normalize_telemetry_value "${MODEL}")" \
    "${status_field}")"

  write_status_line "${line}"
  write_log_line "${line}"
  printf '%s\n' "${line}" >&2
}

forward_status_stream() {
  local target_fd="$1"
  local line suppress_console

  while IFS= read -r line || [[ -n "${line}" ]]; do
    suppress_console=0
    case "${line}" in
      STATUS\|type=heartbeat\|*)
        suppress_console=1
        ;;
    esac

    if [[ "${suppress_console}" != "1" ]]; then
      printf '%s\n' "${line}" >&"${target_fd}"
    fi
    write_log_line "${line}"
    case "${line}" in
      STATUS\|*|ROUTE\|*|COMPLEXITY\|*)
        write_status_line "${line}"
        ;;
    esac
  done
}

usage() {
  cat <<'EOF'
Usage:
  run-agent.sh --agent <agent> [--model <model>] --context-file <file> --prompt-file <file> [--dry-run] [--unattended]
  run-agent.sh --list-agents [--detected-only]
  run-agent.sh --list-models <agent>

Environment equivalents:
  AGENT, MODEL, CONTEXT_PAYLOAD_FILE, PROMPT_FILE, PRINT_PROMPT, AGENT_PERMISSION_MODE, AGENT_REGISTRY_FILE, UNATTENDED,
  RUN_WITH_IT_STATUS_FILE, RUN_WITH_IT_EVENTS_LOG, RUN_WITH_IT_LOG_FILE, RUN_WITH_IT_DONE_FILE, RUN_WITH_IT_RESULT_FILE,
  RUN_WITH_IT_STATE_FILE, RUN_WITH_IT_ROLE, RUN_WITH_IT_ISSUE, RUN_WITH_IT_ISSUE_DIR, REPO_ROOT
EOF
}

detect_gui_mode() {
  [[ -n "${VSCODE_PID:-}" ]] && return 0
  [[ "${TERM_PROGRAM:-}" == "vscode" ]] && return 0
  [[ -n "${ELECTRON_RUN_AS_NODE:-}" ]] && return 0
  [[ -n "${ANTIGRAVITY_APP:-}" ]] && return 0
  [[ -n "${CURSOR_TRACE_ID:-}" ]] && return 0
  [[ -n "${CLAUDE_CODE_ENTRYPOINT:-}" ]] && return 0
  return 1
}

resolve_gui_mode() {
  case "${GUI_MODE}" in
    auto)
      if detect_gui_mode; then
        GUI_MODE=1
      else
        GUI_MODE=0
      fi
      ;;
    1|true|TRUE|yes|YES|on|ON)
      GUI_MODE=1
      ;;
    0|false|FALSE|no|NO|off|OFF)
      GUI_MODE=0
      ;;
    *)
      fail "GUI_MODE must be auto, 1, or 0"
      ;;
  esac
}

apply_gui_permission_mode() {
  [[ "${GUI_MODE}" == "1" ]] || return 0

  UNATTENDED=1
  case "${AGENT}" in
    codex)
      if [[ -z "${AGENT_PERMISSION_MODE}" || "${AGENT_PERMISSION_MODE}" == "--dangerously-bypass-approvals-and-sandbox" ]]; then
        AGENT_PERMISSION_MODE="--sandbox=workspace-write"
      fi
      ;;
    claude)
      if [[ -z "${AGENT_PERMISSION_MODE}" || "${AGENT_PERMISSION_MODE}" == "--dangerously-skip-permissions" ]]; then
        AGENT_PERMISSION_MODE="--permission-mode=acceptEdits"
      fi
      ;;
    github-copilot)
      if [[ -z "${AGENT_PERMISSION_MODE}" || "${AGENT_PERMISSION_MODE}" == "--allow-all" || "${AGENT_PERMISSION_MODE}" == "--autopilot --yolo" ]]; then
        AGENT_PERMISSION_MODE="--allow-all-tools"
      fi
      ;;
    agy)
      if [[ -z "${AGENT_PERMISSION_MODE}" || "${AGENT_PERMISSION_MODE}" == "--dangerously-skip-permissions" ]]; then
        AGENT_PERMISSION_MODE="--sandbox"
      fi
      ;;
  esac
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
[[ -d "${REPO_ROOT}" ]] || fail "repo root not found: ${REPO_ROOT}"

if [[ -z "${MODEL}" ]]; then
  MODEL="$(json_value default_model "${AGENT}")"
fi

if [[ -z "${AGENT_PERMISSION_MODE}" ]]; then
  AGENT_PERMISSION_MODE="$(json_value default_permission "${AGENT}")"
fi

resolve_gui_mode
apply_gui_permission_mode

if [[ "${AGENT_PERMISSION_MODE}" == "safe" ]]; then
  AGENT_PERMISSION_MODE=""
fi

PAYLOAD_FILE="$(mktemp -t ai-skills-prompt.XXXXXX)"
status_stream_dir=""
cleanup_payload() {
  rm -f "${PAYLOAD_FILE}"
  if [[ -n "${status_stream_dir}" ]]; then
    rm -rf "${status_stream_dir}"
  fi
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
if [[ "${#prompt_payload}" -gt 131072 ]]; then
  echo "warn: prompt exceeds 128KB (${#prompt_payload} bytes); may be truncated in sandboxed contexts" >&2
fi

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
      if [[ -n "${AGENT_PERMISSION_MODE}" ]]; then
        read -r -a permission_parts <<< "${AGENT_PERMISSION_MODE}"
        cmd+=("${permission_parts[@]}")
      fi
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
prepare_done_file
emit_run_status "agent-start"
if [[ -n "${RUN_WITH_IT_STATUS_FILE}" || -n "${RUN_WITH_IT_EVENTS_LOG}" || -n "${RUN_WITH_IT_LOG_FILE}" ]]; then
  status_stream_dir="$(mktemp -d -t run-agent-status.XXXXXX)"
  stdout_fifo="${status_stream_dir}/stdout"
  stderr_fifo="${status_stream_dir}/stderr"
  mkfifo "${stdout_fifo}" "${stderr_fifo}"
  forward_status_stream 1 < "${stdout_fifo}" &
  stdout_forward_pid=$!
  forward_status_stream 2 < "${stderr_fifo}" &
  stderr_forward_pid=$!

  (cd -- "${REPO_ROOT}" && "${cmd[@]}") > "${stdout_fifo}" 2> "${stderr_fifo}"
  command_status=$?
  wait "${stdout_forward_pid}"
  wait "${stderr_forward_pid}"
  rm -rf "${status_stream_dir}"
  status_stream_dir=""
else
  (cd -- "${REPO_ROOT}" && "${cmd[@]}")
  command_status=$?
fi
set -e

if [[ -d "${REPO_ROOT}/.codegraph" ]] && command -v codegraph >/dev/null 2>&1; then
  (cd "${REPO_ROOT}" && codegraph mark-dirty 2>/dev/null) || true
fi

if [[ "${command_status}" == "0" ]]; then
  write_done_file "success" "runner-exit"
  emit_run_status "worker-done" "success"
  emit_run_status "agent-complete" "success"
  emit_telemetry "success"
else
  write_done_file "failed" "runner-exit"
  emit_run_status "worker-done" "failed"
  emit_run_status "agent-complete" "failed"
  emit_telemetry "failed"
fi

exit "${command_status}"
