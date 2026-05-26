#!/usr/bin/env bash

set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd -- "${SCRIPT_PATH%/*}" && pwd -P)"

ASSET_ROOT="${ASSETS_DEST:-}"
ROLE=""
ISSUE=""
CYCLE=""
AGENT_NAME=""
MODEL_NAME=""
CONTEXT_FILE=""
PROMPT_FILE=""
LOG_FILE=""
DONE_FILE=""
RESULT_FILE=""
REPO_ROOT_OVERRIDE=""
ISSUE_DIR=""
STATUS_FILE="${RUN_WITH_IT_STATUS_FILE:-}"
EVENTS_LOG="${RUN_WITH_IT_EVENTS_LOG:-}"
TAIL_STATE_FILE=""
POLL_SECONDS="${WORKER_POLL_SECONDS:-20}"
TIMEOUT_SECONDS="${RUN_WITH_IT_DISPATCH_TIMEOUT_SECONDS:-0}"
DRY_RUN=0
VALIDATE_ONLY=0

fail() {
  echo "run-with-it-dispatch.sh: $1" >&2
  exit 2
}

usage() {
  cat <<'EOF'
Usage:
  run-with-it-dispatch.sh --role <role> --issue <n> --agent <agent> --model <model> \
    --context-file <file> --prompt-file <file> --log-file <file> --done-file <file> \
    --result-file <file> [--repo-root <path>] [--issue-dir <path>] [--cycle <n>] [--status-file <file>] [--events-log <file>]

Modes:
  --dry-run        Print the wrapped run-agent.sh invocation.
  --validate-only Validate inputs and emit dispatch-ready status, but do not spawn.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --asset-root) ASSET_ROOT="${2:-}"; shift 2 ;;
    --role) ROLE="${2:-}"; shift 2 ;;
    --issue) ISSUE="${2:-}"; shift 2 ;;
    --cycle) CYCLE="${2:-}"; shift 2 ;;
    --agent) AGENT_NAME="${2:-}"; shift 2 ;;
    --model) MODEL_NAME="${2:-}"; shift 2 ;;
    --context-file) CONTEXT_FILE="${2:-}"; shift 2 ;;
    --prompt-file) PROMPT_FILE="${2:-}"; shift 2 ;;
    --log-file) LOG_FILE="${2:-}"; shift 2 ;;
    --done-file) DONE_FILE="${2:-}"; shift 2 ;;
    --result-file) RESULT_FILE="${2:-}"; shift 2 ;;
    --repo-root) REPO_ROOT_OVERRIDE="${2:-}"; shift 2 ;;
    --issue-dir) ISSUE_DIR="${2:-}"; shift 2 ;;
    --status-file) STATUS_FILE="${2:-}"; shift 2 ;;
    --events-log) EVENTS_LOG="${2:-}"; shift 2 ;;
    --tail-state-file) TAIL_STATE_FILE="${2:-}"; shift 2 ;;
    --poll-seconds) POLL_SECONDS="${2:-}"; shift 2 ;;
    --timeout-seconds) TIMEOUT_SECONDS="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --validate-only) VALIDATE_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

if [ -z "$ASSET_ROOT" ]; then
  if [ -f "$HOME/.ai-skill-collections/assets/run-agent.sh" ]; then
    ASSET_ROOT="$HOME/.ai-skill-collections/assets"
  else
    ASSET_ROOT="$SCRIPT_DIR"
  fi
fi

RUN_AGENT="${ASSET_ROOT}/run-agent.sh"
WORKER_WATCH="${ASSET_ROOT}/worker-watch.sh"
REGISTRY_FILE="${ASSET_ROOT}/agent-registry.json"

[ -n "$ROLE" ] || fail "--role is required"
[ -n "$ISSUE" ] || fail "--issue is required"
[ -n "$AGENT_NAME" ] || fail "--agent is required"
[ -n "$MODEL_NAME" ] || fail "--model is required"
[ -n "$CONTEXT_FILE" ] || fail "--context-file is required"
[ -n "$PROMPT_FILE" ] || fail "--prompt-file is required"
[ -n "$LOG_FILE" ] || fail "--log-file is required"
[ -n "$DONE_FILE" ] || fail "--done-file is required"
[ -n "$RESULT_FILE" ] || fail "--result-file is required"

[ -x "$RUN_AGENT" ] || fail "runner not executable: $RUN_AGENT"
[ -x "$WORKER_WATCH" ] || fail "worker watcher not executable: $WORKER_WATCH"
[ -f "$REGISTRY_FILE" ] || fail "agent registry not found: $REGISTRY_FILE"
[ -f "$CONTEXT_FILE" ] || fail "context file not found: $CONTEXT_FILE"
[ -f "$PROMPT_FILE" ] || fail "prompt file not found: $PROMPT_FILE"
if [ -n "$REPO_ROOT_OVERRIDE" ]; then
  [ -d "$REPO_ROOT_OVERRIDE" ] || fail "repo root not found: $REPO_ROOT_OVERRIDE"
fi

mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$DONE_FILE")" "$(dirname "$RESULT_FILE")"
if [ -n "$STATUS_FILE" ]; then mkdir -p "$(dirname "$STATUS_FILE")"; fi
if [ -n "$EVENTS_LOG" ]; then mkdir -p "$(dirname "$EVENTS_LOG")"; fi

if [ -z "$TAIL_STATE_FILE" ]; then
  cycle_part="${CYCLE:-0}"
  TAIL_STATE_FILE="$(pwd -P)/.run-with-it/status/issue-${ISSUE}-${ROLE}-cycle-${cycle_part}.tail.sha"
fi

if [ -z "$ISSUE_DIR" ]; then
  ISSUE_DIR="${RUN_WITH_IT_ISSUE_DIR:-$(pwd -P)/.run-with-it/issues/${ISSUE}}"
fi
mkdir -p "$ISSUE_DIR"

write_status() {
  local line="$1"
  printf '%s\n' "$line"
  printf '%s\n' "$line" >> "$LOG_FILE"
  if [ -n "$STATUS_FILE" ]; then printf '%s\n' "$line" > "$STATUS_FILE"; fi
  if [ -n "$EVENTS_LOG" ]; then printf '%s\n' "$line" >> "$EVENTS_LOG"; fi
}

cycle_field=""
if [ -n "$CYCLE" ]; then
  cycle_field="|cycle=${CYCLE}"
fi

if [ "$DRY_RUN" = 1 ]; then
  printf 'GUI_MODE=0 AGENT_REGISTRY_FILE=%s REPO_ROOT=%s RUN_WITH_IT_ISSUE_DIR=%s RUN_WITH_IT_STATUS_FILE=%s RUN_WITH_IT_EVENTS_LOG=%s RUN_WITH_IT_LOG_FILE=%s RUN_WITH_IT_DONE_FILE=%s RUN_WITH_IT_ROLE=%s RUN_WITH_IT_ISSUE=%s %s --agent %s --model %s --context-file %s --prompt-file %s --unattended\n' \
    "$REGISTRY_FILE" "${REPO_ROOT_OVERRIDE:-${REPO_ROOT:-$(pwd -P)}}" "$ISSUE_DIR" "$STATUS_FILE" "$EVENTS_LOG" "$LOG_FILE" "$DONE_FILE" "$ROLE" "$ISSUE" \
    "$RUN_AGENT" "$AGENT_NAME" "$MODEL_NAME" "$CONTEXT_FILE" "$PROMPT_FILE"
  exit 0
fi

write_status "STATUS|type=dispatch-ready|issue=${ISSUE}|role=${ROLE}${cycle_field}|agent=${AGENT_NAME}|model=${MODEL_NAME}|result_file=${RESULT_FILE}"

if [ "$VALIDATE_ONLY" = 1 ]; then
  exit 0
fi

write_status "STATUS|type=dispatch-start|issue=${ISSUE}|role=${ROLE}${cycle_field}|agent=${AGENT_NAME}|model=${MODEL_NAME}"

GUI_MODE="${GUI_MODE:-0}" \
AGENT_REGISTRY_FILE="$REGISTRY_FILE" \
REPO_ROOT="${REPO_ROOT_OVERRIDE:-${REPO_ROOT:-$(pwd -P)}}" \
RUN_WITH_IT_ISSUE_DIR="$ISSUE_DIR" \
RUN_WITH_IT_STATUS_FILE="$STATUS_FILE" \
RUN_WITH_IT_EVENTS_LOG="$EVENTS_LOG" \
RUN_WITH_IT_LOG_FILE="$LOG_FILE" \
RUN_WITH_IT_DONE_FILE="$DONE_FILE" \
RUN_WITH_IT_ROLE="$ROLE" \
RUN_WITH_IT_ISSUE="$ISSUE" \
nohup "$RUN_AGENT" \
  --agent "$AGENT_NAME" \
  --model "$MODEL_NAME" \
  --context-file "$CONTEXT_FILE" \
  --prompt-file "$PROMPT_FILE" \
  --unattended \
  >/dev/null 2>&1 < /dev/null &

pid="$!"
write_status "STATUS|type=dispatch-pid|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|done_file=${DONE_FILE}|result_file=${RESULT_FILE}"

started_at="$(date +%s)"
while true; do
  sleep "$POLL_SECONDS"
  "$WORKER_WATCH" \
    --pid "$pid" \
    --done-file "$DONE_FILE" \
    --log-file "$LOG_FILE" \
    --tail-state-file "$TAIL_STATE_FILE" \
    --tail-lines "${WORKER_LOG_TAIL_LINES:-5}" >/dev/null || true

  if [ -s "$DONE_FILE" ] && [ -s "$RESULT_FILE" ]; then
    write_status "STATUS|type=dispatch-complete|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|result_file=${RESULT_FILE}"
    exit 0
  fi

  if ! kill -0 "$pid" 2>/dev/null; then
    wait "$pid" 2>/dev/null || true
    if [ -s "$DONE_FILE" ] && [ -s "$RESULT_FILE" ]; then
      write_status "STATUS|type=dispatch-complete|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|result_file=${RESULT_FILE}"
      exit 0
    fi
    write_status "STATUS|type=dispatch-failed|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|reason=missing-done-or-result|done_file=${DONE_FILE}|result_file=${RESULT_FILE}"
    exit 1
  fi

  if [ "$TIMEOUT_SECONDS" != "0" ]; then
    now="$(date +%s)"
    elapsed=$((now - started_at))
    if [ "$elapsed" -ge "$TIMEOUT_SECONDS" ]; then
      write_status "STATUS|type=dispatch-stall|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|elapsed=${elapsed}|action=alert-user"
      TIMEOUT_SECONDS=0
    fi
  fi
done
