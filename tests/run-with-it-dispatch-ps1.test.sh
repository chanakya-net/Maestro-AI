#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DISPATCHER="${ROOT_DIR}/assets/run-with-it-dispatch.ps1"
PS_CMD="${PWSH:-}"
if [[ -z "$PS_CMD" ]]; then
  PS_CMD="$(command -v pwsh || command -v powershell.exe || command -v powershell || true)"
fi

if [[ -z "$PS_CMD" ]]; then
  echo "SKIP: PowerShell unavailable for run-with-it-dispatch.ps1 contract"
  exit 0
fi

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"
  [[ "$haystack" == *"$needle"* ]] || fail "$message (missing: $needle)"
}

assert_file_contains() {
  local file="$1"
  local needle="$2"
  local message="$3"
  grep -Fq -- "$needle" "$file" || fail "$message (missing: $needle in $file)"
}

assert_json_file() {
  local file="$1"
  local message="$2"
  python3 -m json.tool "$file" >/dev/null || fail "$message (invalid JSON: $file)"
}

BASE_DIR="$(mktemp -d)"
WORK_DIR="${BASE_DIR}/with spaces"
mkdir -p "$WORK_DIR"
cleanup() {
  rm -rf "$BASE_DIR"
}
trap cleanup EXIT

SMOKE_ASSET_ROOT="${WORK_DIR}/assets"
SMOKE_PROJECT="${WORK_DIR}/project"
SMOKE_REPO_ROOT="${WORK_DIR}/repo-root"
mkdir -p "$SMOKE_ASSET_ROOT" "$SMOKE_PROJECT" "$SMOKE_REPO_ROOT"
cp "${ROOT_DIR}/assets/run-agent.ps1" "${ROOT_DIR}/assets/run-with-it-dispatch.ps1" "${ROOT_DIR}/assets/worker-watch.ps1" "$SMOKE_ASSET_ROOT/"
cp "${ROOT_DIR}/assets/prompt.md" "$SMOKE_ASSET_ROOT/"

FAKE_AGENT="${WORK_DIR}/fake-agent.ps1"
cat > "$FAKE_AGENT" <<'PS1'
param([string]$RepoRoot, [string]$Prompt)
if ($Prompt -eq "--version") {
  Write-Output "fake-agent 1.0"
  exit 0
}
$resultFile = (($Prompt -split "`n") | Where-Object { $_ -like "RESULT_FILE=*" } | Select-Object -First 1) -replace "^RESULT_FILE=", ""
Write-Output "STATUS|type=heartbeat|issue=$env:RUN_WITH_IT_ISSUE|role=$env:RUN_WITH_IT_ROLE|phase=testing|progress=repo-root"
Write-Output "fake-agent stdout is captured"
[Console]::Error.WriteLine("fake-agent stderr is captured")
New-Item -ItemType Directory -Force -Path $RepoRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $resultFile) | Out-Null
Set-Content -Path (Join-Path $RepoRoot "marker.txt") -Value "seen" -Encoding UTF8
Set-Content -Path $resultFile -Value "{`"outcome`":`"completed`",`"repo_root_seen`":`"$RepoRoot`"}" -Encoding UTF8
PS1

SILENT_AGENT="${WORK_DIR}/silent-agent.ps1"
cat > "$SILENT_AGENT" <<'PS1'
param([string]$RepoRoot, [string]$Prompt)
if ($Prompt -eq "--version") {
  Write-Output "silent-agent 1.0"
  exit 0
}
$resultFile = (($Prompt -split "`n") | Where-Object { $_ -like "RESULT_FILE=*" } | Select-Object -First 1) -replace "^RESULT_FILE=", ""
Start-Sleep -Seconds 4
New-Item -ItemType Directory -Force -Path (Split-Path $resultFile) | Out-Null
Set-Content -Path $resultFile -Value "{`"outcome`":`"completed`",`"silent`":true}" -Encoding UTF8
PS1

cat > "${SMOKE_ASSET_ROOT}/agent-registry.json" <<JSON
{
  "schema_version": 1,
  "aliases": {},
  "agents": {
    "fake": {
      "display_name": "Fake",
      "detection": { "command": "${PS_CMD}", "args": ["-NoProfile", "-File", "${FAKE_AGENT}", "unused", "--version"] },
      "invocation": {
        "command": "${PS_CMD}",
        "args_template": ["-NoProfile", "-File", "${FAKE_AGENT}", "{{repo_root}}", "{{prompt}}"],
        "prompt_argument_template": "{{prompt}}"
      },
      "permission_modes": { "default": "", "available": [""] },
      "model": { "default": "fake-model", "flag_template": "", "known_models": ["fake-model"] },
      "capability_band": "balanced",
      "fallback_order": [],
      "user_model_configuration": { "requires_user_model_config": false, "config_paths": [], "skip_when_unconfigured": false, "skip_message": "" }
    },
    "silent": {
      "display_name": "Silent",
      "detection": { "command": "${PS_CMD}", "args": ["-NoProfile", "-File", "${SILENT_AGENT}", "unused", "--version"] },
      "invocation": {
        "command": "${PS_CMD}",
        "args_template": ["-NoProfile", "-File", "${SILENT_AGENT}", "{{repo_root}}", "{{prompt}}"],
        "prompt_argument_template": "{{prompt}}"
      },
      "permission_modes": { "default": "", "available": [""] },
      "model": { "default": "fake-model", "flag_template": "", "known_models": ["fake-model"] },
      "capability_band": "balanced",
      "fallback_order": [],
      "user_model_configuration": { "requires_user_model_config": false, "config_paths": [], "skip_when_unconfigured": false, "skip_message": "" }
    }
  }
}
JSON

CONTEXT_FILE="${SMOKE_PROJECT}/context.md"
PROMPT_FILE="${SMOKE_PROJECT}/prompt.md"
ISSUE_DIR="${SMOKE_PROJECT}/.run-with-it/issues/42"
LOG_FILE="${ISSUE_DIR}/workers/impl/cycle-1.log"
DONE_FILE="${ISSUE_DIR}/workers/impl/cycle-1.done"
RESULT_FILE="${ISSUE_DIR}/workers/impl/cycle-1-result.json"
STATE_FILE="${ISSUE_DIR}/workers/impl/cycle-1.state.json"
STATUS_FILE="${SMOKE_PROJECT}/.run-with-it/status/current.txt"
EVENTS_LOG="${SMOKE_PROJECT}/.run-with-it/status/events.log"
mkdir -p "$(dirname "$RESULT_FILE")"
printf 'RESULT_FILE=%s\n' "$RESULT_FILE" > "$CONTEXT_FILE"
printf '# Prompt\n' > "$PROMPT_FILE"

dry_output="$("$PS_CMD" -NoProfile -File "$DISPATCHER" \
  -DryRun \
  -AssetRoot "$SMOKE_ASSET_ROOT" \
  -Role impl \
  -Issue 42 \
  -Cycle 1 \
  -Agent fake \
  -Model fake-model \
  -ContextFile "$CONTEXT_FILE" \
  -PromptFile "$PROMPT_FILE" \
  -LogFile "$LOG_FILE" \
  -DoneFile "$DONE_FILE" \
  -ResultFile "$RESULT_FILE" \
  -StateFile "$STATE_FILE" \
  -RepoRoot "$SMOKE_REPO_ROOT" \
  -IssueDir "$ISSUE_DIR" \
  -StatusFile "$STATUS_FILE" \
  -EventsLog "$EVENTS_LOG")"
assert_contains "$dry_output" "RUN_WITH_IT_STATE_FILE=${STATE_FILE}" "dry-run sets state file"
assert_contains "$dry_output" "run-agent.ps1" "dry-run wraps run-agent.ps1"

"$PS_CMD" -NoProfile -File "$DISPATCHER" \
  -AssetRoot "$SMOKE_ASSET_ROOT" \
  -Role impl \
  -Issue 42 \
  -Cycle 1 \
  -Agent fake \
  -Model fake-model \
  -ContextFile "$CONTEXT_FILE" \
  -PromptFile "$PROMPT_FILE" \
  -LogFile "$LOG_FILE" \
  -DoneFile "$DONE_FILE" \
  -ResultFile "$RESULT_FILE" \
  -StateFile "$STATE_FILE" \
  -RepoRoot "$SMOKE_REPO_ROOT" \
  -IssueDir "$ISSUE_DIR" \
  -StatusFile "$STATUS_FILE" \
  -EventsLog "$EVENTS_LOG" \
  -PollSeconds 1 >/dev/null

assert_file_contains "$LOG_FILE" "STATUS|type=heartbeat|issue=42|role=impl|phase=testing|progress=repo-root" "dispatch captures heartbeat in role log"
assert_file_contains "$LOG_FILE" "fake-agent stdout is captured" "dispatch captures stdout"
assert_file_contains "$LOG_FILE" "fake-agent stderr is captured" "dispatch captures stderr"
assert_file_contains "$DONE_FILE" "DONE|issue=42|role=impl" "dispatch writes done file"
assert_json_file "$RESULT_FILE" "dispatch result JSON is valid"
assert_json_file "$STATE_FILE" "dispatch state JSON is valid"
assert_file_contains "$STATE_FILE" '"state": "completed"' "dispatch records completed state"

SILENT_ISSUE_DIR="${SMOKE_PROJECT}/.run-with-it/issues/43"
SILENT_CONTEXT="${SMOKE_PROJECT}/silent-context.md"
SILENT_LOG="${SILENT_ISSUE_DIR}/workers/impl/cycle-1.log"
SILENT_DONE="${SILENT_ISSUE_DIR}/workers/impl/cycle-1.done"
SILENT_RESULT="${SILENT_ISSUE_DIR}/workers/impl/cycle-1-result.json"
SILENT_STATE="${SILENT_ISSUE_DIR}/workers/impl/cycle-1.state.json"
mkdir -p "$(dirname "$SILENT_RESULT")"
printf 'RESULT_FILE=%s\n' "$SILENT_RESULT" > "$SILENT_CONTEXT"

"$PS_CMD" -NoProfile -File "$DISPATCHER" \
  -AssetRoot "$SMOKE_ASSET_ROOT" \
  -Role impl \
  -Issue 43 \
  -Cycle 1 \
  -Agent silent \
  -Model fake-model \
  -ContextFile "$SILENT_CONTEXT" \
  -PromptFile "$PROMPT_FILE" \
  -LogFile "$SILENT_LOG" \
  -DoneFile "$SILENT_DONE" \
  -ResultFile "$SILENT_RESULT" \
  -StateFile "$SILENT_STATE" \
  -RepoRoot "$SMOKE_REPO_ROOT" \
  -IssueDir "$SILENT_ISSUE_DIR" \
  -StatusFile "$STATUS_FILE" \
  -EventsLog "$EVENTS_LOG" \
  -PollSeconds 1 \
  -QuietSeconds 1 \
  -StallSeconds 2 >/dev/null &
silent_pid="$!"

saw_stalled=0
for _ in {1..40}; do
  if [[ -f "$SILENT_STATE" ]] && grep -Fq '"state": "stalled"' "$SILENT_STATE"; then
    saw_stalled=1
    break
  fi
  sleep 0.2
done

wait "$silent_pid"
[[ "$saw_stalled" == "1" ]] || fail "silent live worker should be marked stalled before completion"
assert_json_file "$SILENT_STATE" "silent final state JSON is valid"
assert_file_contains "$SILENT_STATE" '"state": "completed"' "silent worker eventually completes"
assert_file_contains "$EVENTS_LOG" "STATUS|type=worker-stalled|issue=43|role=impl|cycle=1|reason=alive-but-silent" "silent worker emits stalled event"

echo "PASS: run-with-it-dispatch.ps1 contract"
