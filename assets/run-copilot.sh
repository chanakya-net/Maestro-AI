#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$PWD}"
CONTEXT_PAYLOAD_FILE="${1:-${CONTEXT_PAYLOAD_FILE:-}}"
PROMPT_FILE="${2:-${PROMPT_FILE:-${SCRIPT_DIR}/prompt.md}}"
COPY_PROMPT="${COPY_PROMPT:-1}"
PRINT_PROMPT="${PRINT_PROMPT:-0}"
COPILOT_PERMISSION_MODE="${COPILOT_PERMISSION_MODE:---allow-all-tools}"
COPILOT_EXTRA_ARGS="${COPILOT_EXTRA_ARGS:-}"
MAX_ITERATIONS="${MAX_ITERATIONS:-20}"
STOP_MARKER="${STOP_MARKER:-<promise>NO MORE TASKS</promise>}"
AGENT_NAME_PREFIX="${AGENT_NAME_PREFIX:-copilot-agent}"
HEARTBEAT_INTERVAL_SECONDS="${HEARTBEAT_INTERVAL_SECONDS:-15}"
COLOR_OUTPUT="${COLOR_OUTPUT:-auto}"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_command copilot

if [[ -z "${CONTEXT_PAYLOAD_FILE}" ]]; then
  echo "Context payload file is required. Pass as arg1 or set CONTEXT_PAYLOAD_FILE." >&2
  exit 1
fi

if [[ ! -f "${CONTEXT_PAYLOAD_FILE}" ]]; then
  echo "Context payload file not found: ${CONTEXT_PAYLOAD_FILE}" >&2
  exit 1
fi

if [[ ! -f "${PROMPT_FILE}" ]]; then
  echo "Prompt file not found: ${PROMPT_FILE}" >&2
  exit 1
fi

PAYLOAD_FILE="$(mktemp -t intelibill-prompt.XXXXXX)"
OUTPUT_FILE="$(mktemp -t intelibill-output.XXXXXX)"

cleanup() {
  rm -f "${PAYLOAD_FILE}" "${OUTPUT_FILE}"
}

trap cleanup EXIT

build_payload() {
  {
    cat "${CONTEXT_PAYLOAD_FILE}"
    printf '\nInstructions:\n\n'
    cat "${PROMPT_FILE}"
    printf '\n'
  } > "${PAYLOAD_FILE}"
}

is_tty() {
  [[ -t 1 ]]
}

should_color() {
  case "${COLOR_OUTPUT}" in
    always) return 0 ;;
    never) return 1 ;;
    auto) is_tty ;;
    *) is_tty ;;
  esac
}

colorize_stream() {
  if ! should_color; then
    cat
    return
  fi

  awk '
    BEGIN {
      reset = "\033[0m"
      palette[1] = "\033[38;5;39m"
      palette[2] = "\033[38;5;46m"
      palette[3] = "\033[38;5;220m"
      palette[4] = "\033[38;5;198m"
      palette[5] = "\033[38;5;51m"
      palette[6] = "\033[38;5;208m"
      palette[7] = "\033[38;5;141m"
      palette[8] = "\033[38;5;82m"
      palette_count = 8
      next_palette = 1
      runner = "\033[1;38;5;250m"
    }

    function color_for_agent(agent,   c) {
      if (!(agent in agent_colors)) {
        agent_colors[agent] = palette[next_palette]
        next_palette++
        if (next_palette > palette_count) {
          next_palette = 1
        }
      }
      c = agent_colors[agent]
      return c
    }

    {
      line = $0

      if (line ~ /^STATUS\|/) {
        if (match(line, /\|agent=[^|]+/)) {
          agent = substr(line, RSTART + 7, RLENGTH - 7)
          c = color_for_agent(agent)
          print c line reset
        } else {
          print runner line reset
        }
      } else if (line ~ /^\[[0-9]{2}:[0-9]{2}:[0-9]{2}\]/ || line ~ /^== /) {
        print runner line reset
      } else {
        print line
      }

      fflush()
    }
  '
}

run_with_status() {
  local run_name="$1"
  shift

  : > "${OUTPUT_FILE}"

  "$@" > >(tee -a "${OUTPUT_FILE}" | colorize_stream) 2> >(tee -a "${OUTPUT_FILE}" | colorize_stream >&2) &
  local cmd_pid=$!
  local started_at
  started_at="$(date +%s)"

  echo "[$(date '+%H:%M:%S')] ${run_name}: started (pid=${cmd_pid})" >&2

  while kill -0 "${cmd_pid}" >/dev/null 2>&1; do
    sleep "${HEARTBEAT_INTERVAL_SECONDS}"

    if kill -0 "${cmd_pid}" >/dev/null 2>&1; then
      local now elapsed output_lines
      now="$(date +%s)"
      elapsed="$((now - started_at))"
      output_lines="$(wc -l < "${OUTPUT_FILE}" | tr -d ' ')"
      echo "[$(date '+%H:%M:%S')] ${run_name}: running (elapsed=${elapsed}s, output_lines=${output_lines})" >&2
    fi
  done

  wait "${cmd_pid}"
  local exit_code=$?
  local finished_at elapsed_total
  finished_at="$(date +%s)"
  elapsed_total="$((finished_at - started_at))"
  echo "[$(date '+%H:%M:%S')] ${run_name}: finished (exit=${exit_code}, elapsed=${elapsed_total}s)" >&2

  return "${exit_code}"
}

cd "${REPO_ROOT}"

for ((iteration = 1; iteration <= MAX_ITERATIONS; iteration++)); do
  run_name="${AGENT_NAME_PREFIX}-iter-${iteration}"
  echo "== Copilot iteration ${iteration}/${MAX_ITERATIONS} [${run_name}] ==" >&2
  build_payload

  if [[ "${COPY_PROMPT}" == "1" ]] && command -v pbcopy >/dev/null 2>&1; then
    pbcopy < "${PAYLOAD_FILE}"
  fi

  if [[ "${PRINT_PROMPT}" == "1" ]]; then
    cat "${PAYLOAD_FILE}"
    exit 0
  fi

  run_with_status "${run_name}" copilot ${COPILOT_PERMISSION_MODE} ${COPILOT_EXTRA_ARGS} -p "$(cat "${PAYLOAD_FILE}")"

  if grep -Fq "${STOP_MARKER}" "${OUTPUT_FILE}"; then
    exit 0
  fi
done

echo "Reached MAX_ITERATIONS=${MAX_ITERATIONS} without seeing ${STOP_MARKER}." >&2
exit 1
