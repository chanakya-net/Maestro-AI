#!/usr/bin/env bash

set -euo pipefail

PID=""
DONE_FILE=""
LOG_FILE=""
TAIL_STATE_FILE=""
TAIL_LINES="5"

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --pid)
      PID="${2:-}"
      shift 2
      ;;
    --done-file)
      DONE_FILE="${2:-}"
      shift 2
      ;;
    --log-file)
      LOG_FILE="${2:-}"
      shift 2
      ;;
    --tail-state-file)
      TAIL_STATE_FILE="${2:-}"
      shift 2
      ;;
    --tail-lines)
      TAIL_LINES="${2:-5}"
      shift 2
      ;;
    *)
      echo "worker-watch.sh: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "${PID}" || -z "${DONE_FILE}" ]]; then
  echo "worker-watch.sh: --pid and --done-file are required" >&2
  exit 2
fi

alive="false"
if kill -0 "${PID}" 2>/dev/null; then
  alive="true"
fi

done_present="false"
if [[ -s "${DONE_FILE}" ]]; then
  done_present="true"
fi

log_present="false"
log_tail_changed="false"
tail_hash=""

if [[ -n "${LOG_FILE}" && -s "${LOG_FILE}" ]]; then
  log_present="true"
  tail_text="$(tail -n "${TAIL_LINES}" "${LOG_FILE}")"
  if command -v shasum >/dev/null 2>&1; then
    tail_hash="$(printf '%s' "${tail_text}" | shasum -a 256 | awk '{print $1}')"
  else
    tail_hash="$(printf '%s' "${tail_text}" | cksum | awk '{print $1 "-" $2}')"
  fi

  previous_hash=""
  if [[ -n "${TAIL_STATE_FILE}" && -f "${TAIL_STATE_FILE}" ]]; then
    previous_hash="$(cat "${TAIL_STATE_FILE}")"
  fi

  if [[ "${tail_hash}" != "${previous_hash}" ]]; then
    log_tail_changed="true"
    if [[ -n "${TAIL_STATE_FILE}" ]]; then
      mkdir -p "$(dirname "${TAIL_STATE_FILE}")"
      printf '%s\n' "${tail_hash}" > "${TAIL_STATE_FILE}"
    fi
  fi
fi

printf 'WORKER|pid=%s|alive=%s|done=%s|log_present=%s|log_tail_changed=%s|tail_hash=%s\n' \
  "${PID}" "${alive}" "${done_present}" "${log_present}" "${log_tail_changed}" "${tail_hash:-none}"
