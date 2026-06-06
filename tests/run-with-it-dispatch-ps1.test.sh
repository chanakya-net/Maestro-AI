#!/usr/bin/env bash

set -euo pipefail

unset RUN_WITH_IT_DETACHED_CHILD

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DISPATCHER="${ROOT_DIR}/assets/powershell/run-with-it-dispatch.ps1"
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

assert_file_contains "$DISPATCHER" '[switch]$Detach' "PowerShell dispatcher exposes detach switch"
assert_file_contains "$DISPATCHER" 'STATUS|type=dispatch-detached' "PowerShell dispatcher reports detached launch"

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
mkdir -p "$SMOKE_ASSET_ROOT"/{powershell,python,prompts} "$SMOKE_PROJECT" "$SMOKE_REPO_ROOT"
cp "${ROOT_DIR}/assets/powershell/run-agent.ps1" "${ROOT_DIR}/assets/powershell/run-with-it-dispatch.ps1" "${ROOT_DIR}/assets/powershell/worker-watch.ps1" "$SMOKE_ASSET_ROOT/powershell/"
cp "${ROOT_DIR}/assets/python/run-with-it-artifacts.py" "$SMOKE_ASSET_ROOT/python/"
cp "${ROOT_DIR}/assets/prompts/prompt.md" "$SMOKE_ASSET_ROOT/prompts/"
git -C "$SMOKE_REPO_ROOT" init -q
git -C "$SMOKE_REPO_ROOT" config user.email "test@example.com"
git -C "$SMOKE_REPO_ROOT" config user.name "Test User"
printf 'baseline\n' > "$SMOKE_REPO_ROOT/README.md"
git -C "$SMOKE_REPO_ROOT" add README.md
git -C "$SMOKE_REPO_ROOT" commit -m "baseline" >/dev/null

FAKE_AGENT="${WORK_DIR}/fake-agent.ps1"
cat > "$FAKE_AGENT" <<'PS1'
param([string]$RepoRoot, [string]$Prompt)
if ($Prompt -eq "--version") {
  Write-Output "fake-agent 1.0"
  exit 0
}
$resultFile = (($Prompt -split "`n") | Where-Object { $_ -like "RESULT_FILE=*" } | Select-Object -First 1) -replace "^RESULT_FILE=", ""
if (-not $RepoRoot) {
  $RepoRoot = $env:REPO_ROOT
}
Write-Output "STATUS|type=heartbeat|issue=$env:RUN_WITH_IT_ISSUE|role=$env:RUN_WITH_IT_ROLE|phase=testing|progress=repo-root"
Write-Output "fake-agent stdout is captured"
[Console]::Error.WriteLine("fake-agent stderr is captured")
New-Item -ItemType Directory -Force -Path $RepoRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $resultFile) | Out-Null
Set-Content -Path (Join-Path $RepoRoot "marker.txt") -Value "seen" -Encoding UTF8
& git -C $RepoRoot add marker.txt | Out-Null
& git -C $RepoRoot commit -m "impl fake marker" | Out-Null
$commitSha = (& git -C $RepoRoot rev-parse HEAD).Trim()
Set-Content -Path $resultFile -Value "{`"schema_version`":1,`"issue`":`"$env:RUN_WITH_IT_ISSUE`",`"role`":`"$env:RUN_WITH_IT_ROLE`",`"status`":`"success`",`"commit_sha`":`"$commitSha`",`"files_committed`":[`"marker.txt`"],`"verification`":{`"passed`":true,`"commands`":[`"fake`"]},`"repo_root_seen`":`"$RepoRoot`"}" -Encoding UTF8
PS1

SILENT_AGENT="${WORK_DIR}/silent-agent.ps1"
cat > "$SILENT_AGENT" <<'PS1'
param([string]$RepoRoot, [string]$Prompt)
if ($Prompt -eq "--version") {
  Write-Output "silent-agent 1.0"
  exit 0
}
$resultFile = (($Prompt -split "`n") | Where-Object { $_ -like "RESULT_FILE=*" } | Select-Object -First 1) -replace "^RESULT_FILE=", ""
if (-not $RepoRoot) {
  $RepoRoot = $env:REPO_ROOT
}
Start-Sleep -Seconds 4
New-Item -ItemType Directory -Force -Path (Split-Path $resultFile) | Out-Null
Set-Content -Path (Join-Path $RepoRoot "silent.txt") -Value "silent" -Encoding UTF8
& git -C $RepoRoot add silent.txt | Out-Null
& git -C $RepoRoot commit -m "impl silent marker" | Out-Null
$commitSha = (& git -C $RepoRoot rev-parse HEAD).Trim()
Set-Content -Path $resultFile -Value "{`"schema_version`":1,`"issue`":`"$env:RUN_WITH_IT_ISSUE`",`"role`":`"$env:RUN_WITH_IT_ROLE`",`"status`":`"success`",`"commit_sha`":`"$commitSha`",`"files_committed`":[`"silent.txt`"],`"verification`":{`"passed`":true,`"commands`":[`"fake`"]},`"silent`":true}" -Encoding UTF8
PS1

REVIEW_INSTRUCTIONS_ONLY_AGENT="${WORK_DIR}/review-instructions-only-agent.ps1"
cat > "$REVIEW_INSTRUCTIONS_ONLY_AGENT" <<'PS1'
param([string]$RepoRoot, [string]$Prompt)
if ($Prompt -eq "--version") {
  Write-Output "review-instructions-only-agent 1.0"
  exit 0
}
$statusFile = $env:RUN_WITH_IT_RESULT_FILE
$instructionsFile = $statusFile -replace "-status\.json$", "-instructions.json"
New-Item -ItemType Directory -Force -Path (Split-Path $instructionsFile) | Out-Null
Set-Content -Path $instructionsFile -Value "{`"verdict`":`"approve`",`"summary`":`"review approved from instructions`",`"comments`":[],`"blocking_reasons`":[]}" -Encoding UTF8
New-Item -ItemType Directory -Force -Path (Split-Path $env:RUN_WITH_IT_DONE_FILE) | Out-Null
Set-Content -Path $env:RUN_WITH_IT_DONE_FILE -Value "DONE|issue=$env:RUN_WITH_IT_ISSUE|role=review|status=success|source=agent" -Encoding UTF8
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
    },
    "review-instructions-only": {
      "display_name": "Review Instructions Only",
      "detection": { "command": "${PS_CMD}", "args": ["-NoProfile", "-File", "${REVIEW_INSTRUCTIONS_ONLY_AGENT}", "unused", "--version"] },
      "invocation": {
        "command": "${PS_CMD}",
        "args_template": ["-NoProfile", "-File", "${REVIEW_INSTRUCTIONS_ONLY_AGENT}", "{{repo_root}}", "{{prompt}}"],
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
assert_contains "$dry_output" "RUN_WITH_IT_RESULT_FILE=${RESULT_FILE}" "dry-run sets result file"
assert_contains "$dry_output" "run-agent.ps1" "dry-run wraps run-agent.ps1"

FLAT_ASSET_ROOT="${WORK_DIR}/flat-assets"
mkdir -p "${FLAT_ASSET_ROOT}/prompts" "${FLAT_ASSET_ROOT}/python"
cp "${ROOT_DIR}/assets/powershell/run-with-it-dispatch.ps1" "${FLAT_ASSET_ROOT}/run-with-it-dispatch.ps1"
cp "${ROOT_DIR}/assets/powershell/run-agent.ps1" "${FLAT_ASSET_ROOT}/run-agent.ps1"
cp "${ROOT_DIR}/assets/powershell/worker-watch.ps1" "${FLAT_ASSET_ROOT}/worker-watch.ps1"
cp "${ROOT_DIR}/assets/powershell/run-with-it-pool.ps1" "${FLAT_ASSET_ROOT}/run-with-it-pool.ps1"
cp "${ROOT_DIR}/assets/python/run-with-it-artifacts.py" "${FLAT_ASSET_ROOT}/python/"
cp "${ROOT_DIR}/assets/agent-registry.json" "${FLAT_ASSET_ROOT}/agent-registry.json"
cp "${ROOT_DIR}/assets/prompts/prompt.md" "${FLAT_ASSET_ROOT}/prompts/prompt.md"
chmod +x "${FLAT_ASSET_ROOT}/run-with-it-dispatch.ps1" "${FLAT_ASSET_ROOT}/run-agent.ps1" \
  "${FLAT_ASSET_ROOT}/worker-watch.ps1" "${FLAT_ASSET_ROOT}/run-with-it-pool.ps1"

flat_output="$("${PS_CMD}" -NoProfile -File "$DISPATCHER" \
  -DryRun \
  -AssetRoot "$FLAT_ASSET_ROOT" \
  -Role impl \
  -Issue 88 \
  -Cycle 1 \
  -Agent fake \
  -Model fake-model \
  -ContextFile "$CONTEXT_FILE" \
  -PromptFile "${FLAT_ASSET_ROOT}/prompts/prompt.md" \
  -LogFile "$LOG_FILE" \
  -DoneFile "$DONE_FILE" \
  -ResultFile "$RESULT_FILE" \
  -StateFile "$STATE_FILE" \
  -RepoRoot "$SMOKE_REPO_ROOT" \
  -IssueDir "$ISSUE_DIR" \
  -StatusFile "$STATUS_FILE" \
  -EventsLog "$EVENTS_LOG" \
  -HelperRuntime py)"
assert_contains "$flat_output" "${FLAT_ASSET_ROOT}/run-agent.ps1 --agent fake" "flat Python layout resolves root-level run-agent helper"

set +e
cs_output="$("${PS_CMD}" -NoProfile -File "$DISPATCHER" \
  -DryRun \
  -AssetRoot "$FLAT_ASSET_ROOT" \
  -Role impl \
  -Issue 89 \
  -Cycle 1 \
  -Agent fake \
  -Model fake-model \
  -ContextFile "$CONTEXT_FILE" \
  -PromptFile "${FLAT_ASSET_ROOT}/prompts/prompt.md" \
  -LogFile "$LOG_FILE" \
  -DoneFile "$DONE_FILE" \
  -ResultFile "$RESULT_FILE" \
  -StateFile "$STATE_FILE" \
  -RepoRoot "$SMOKE_REPO_ROOT" \
  -IssueDir "$ISSUE_DIR" \
  -StatusFile "$STATUS_FILE" \
  -EventsLog "$EVENTS_LOG" \
  -HelperRuntime cs 2>&1)"
cs_status="$?"
set -e

[[ "$cs_status" != "0" ]] || fail "flat C# layout must fail when helper runtime is cs"
assert_contains "$cs_output" "missing nested asset layout for helper runtime 'cs' at" "flat C# layout emits nested layout failure"

NESTED_DISPATCH_ASSET_ROOT="${WORK_DIR}/nested-dispatch-assets-ps1"
mkdir -p "${NESTED_DISPATCH_ASSET_ROOT}/prompts" "${NESTED_DISPATCH_ASSET_ROOT}/powershell" "${NESTED_DISPATCH_ASSET_ROOT}/python"
cp "${ROOT_DIR}/assets/powershell/run-with-it-dispatch.ps1" "${NESTED_DISPATCH_ASSET_ROOT}/run-with-it-dispatch.ps1"
cp "${ROOT_DIR}/assets/powershell/run-agent.ps1" "${NESTED_DISPATCH_ASSET_ROOT}/powershell/run-agent.ps1"
cp "${ROOT_DIR}/assets/powershell/worker-watch.ps1" "${NESTED_DISPATCH_ASSET_ROOT}/powershell/worker-watch.ps1"
cp "${ROOT_DIR}/assets/powershell/run-with-it-pool.ps1" "${NESTED_DISPATCH_ASSET_ROOT}/powershell/run-with-it-pool.ps1"
cp "${ROOT_DIR}/assets/python/run-with-it-artifacts.py" "${NESTED_DISPATCH_ASSET_ROOT}/python/"
cp "${ROOT_DIR}/assets/agent-registry.json" "${NESTED_DISPATCH_ASSET_ROOT}/agent-registry.json"
cp "${ROOT_DIR}/assets/prompts/prompt.md" "${NESTED_DISPATCH_ASSET_ROOT}/prompts/prompt.md"
printf 'explicit powershell dispatch prompt\n' > "${NESTED_DISPATCH_ASSET_ROOT}/prompts/explicit-prompt.md"
chmod +x "${NESTED_DISPATCH_ASSET_ROOT}/run-with-it-dispatch.ps1" "${NESTED_DISPATCH_ASSET_ROOT}/powershell/run-agent.ps1" \
  "${NESTED_DISPATCH_ASSET_ROOT}/powershell/worker-watch.ps1" "${NESTED_DISPATCH_ASSET_ROOT}/powershell/run-with-it-pool.ps1"

nested_dispatch_prompt_output="$("${PS_CMD}" -NoProfile -File "$DISPATCHER" \
  -DryRun \
  -AssetRoot "$NESTED_DISPATCH_ASSET_ROOT" \
  -Role impl \
  -Issue 101 \
  -Cycle 1 \
  -Agent fake \
  -Model fake-model \
  -ContextFile "$CONTEXT_FILE" \
  -PromptFile "${NESTED_DISPATCH_ASSET_ROOT}/prompts/explicit-prompt.md" \
  -LogFile "$LOG_FILE" \
  -DoneFile "$DONE_FILE" \
  -ResultFile "$RESULT_FILE" \
  -StateFile "$STATE_FILE" \
  -RepoRoot "$SMOKE_REPO_ROOT" \
  -IssueDir "$ISSUE_DIR" \
  -StatusFile "$STATUS_FILE" \
  -EventsLog "$EVENTS_LOG" \
  -PollSeconds 1)"
assert_contains "$nested_dispatch_prompt_output" "--prompt-file ${NESTED_DISPATCH_ASSET_ROOT}/prompts/explicit-prompt.md" "dispatch preserves explicit prompt-file in nested PowerShell layout"

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

DETACH_ISSUE_DIR="${SMOKE_PROJECT}/.run-with-it/issues/45"
DETACH_CONTEXT="${SMOKE_PROJECT}/detach-context.md"
DETACH_LOG="${DETACH_ISSUE_DIR}/workers/impl/cycle-1.log"
DETACH_DONE="${DETACH_ISSUE_DIR}/workers/impl/cycle-1.done"
DETACH_RESULT="${DETACH_ISSUE_DIR}/workers/impl/cycle-1-result.json"
DETACH_STATE="${DETACH_ISSUE_DIR}/workers/impl/cycle-1.state.json"
DETACH_OUT="${DETACH_ISSUE_DIR}/workers/impl/cycle-1.dispatch.out"
mkdir -p "$(dirname "$DETACH_RESULT")"
printf 'RESULT_FILE=%s\n' "$DETACH_RESULT" > "$DETACH_CONTEXT"

"$PS_CMD" -NoProfile -File "$DISPATCHER" \
  -Detach \
  -AssetRoot "$SMOKE_ASSET_ROOT" \
  -Role impl \
  -Issue 45 \
  -Cycle 1 \
  -Agent fake \
  -Model fake-model \
  -ContextFile "$DETACH_CONTEXT" \
  -PromptFile "$PROMPT_FILE" \
  -LogFile "$DETACH_LOG" \
  -DoneFile "$DETACH_DONE" \
  -ResultFile "$DETACH_RESULT" \
  -StateFile "$DETACH_STATE" \
  -RepoRoot "$SMOKE_REPO_ROOT" \
  -IssueDir "$DETACH_ISSUE_DIR" \
  -StatusFile "$STATUS_FILE" \
  -EventsLog "$EVENTS_LOG" \
  -DispatchOutFile "$DETACH_OUT" \
  -PollSeconds 1 >/dev/null

for _ in {1..40}; do
  if [[ -f "$DETACH_LOG" ]] && grep -Fq "STATUS|type=dispatch-complete|issue=45|role=impl" "$DETACH_LOG"; then
    break
  fi
  sleep 0.25
done

assert_file_contains "$DETACH_LOG" "STATUS|type=dispatch-detached|issue=45|role=impl|cycle=1" "detached PowerShell dispatcher logs parent handoff"
assert_file_contains "$DETACH_LOG" "STATUS|type=dispatch-start|issue=45|role=impl|cycle=1" "detached PowerShell dispatcher starts child monitor"
assert_file_contains "$DETACH_LOG" "STATUS|type=dispatch-pid|issue=45|role=impl|cycle=1" "detached PowerShell dispatcher captures runner pid"
assert_json_file "$DETACH_STATE" "detached PowerShell dispatcher writes state JSON"
assert_json_file "$DETACH_RESULT" "detached PowerShell dispatcher writes result JSON"

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

REVIEW_ISSUE_DIR="${SMOKE_PROJECT}/.run-with-it/issues/44"
REVIEW_LOG="${REVIEW_ISSUE_DIR}/workers/review/cycle-2.log"
REVIEW_DONE="${REVIEW_ISSUE_DIR}/workers/review/cycle-2.done"
REVIEW_RESULT="${REVIEW_ISSUE_DIR}/workers/review/cycle-2-status.json"
REVIEW_STATE="${REVIEW_ISSUE_DIR}/workers/review/cycle-2.state.json"

"$PS_CMD" -NoProfile -File "$DISPATCHER" \
  -AssetRoot "$SMOKE_ASSET_ROOT" \
  -Role review \
  -Issue 44 \
  -Cycle 2 \
  -Agent review-instructions-only \
  -Model fake-model \
  -ContextFile "$CONTEXT_FILE" \
  -PromptFile "$PROMPT_FILE" \
  -LogFile "$REVIEW_LOG" \
  -DoneFile "$REVIEW_DONE" \
  -ResultFile "$REVIEW_RESULT" \
  -StateFile "$REVIEW_STATE" \
  -RepoRoot "$SMOKE_REPO_ROOT" \
  -IssueDir "$REVIEW_ISSUE_DIR" \
  -StatusFile "$STATUS_FILE" \
  -EventsLog "$EVENTS_LOG" \
  -PollSeconds 1 >/dev/null

assert_json_file "$REVIEW_RESULT" "PowerShell dispatcher synthesizes missing review status from instructions"
assert_file_contains "$REVIEW_RESULT" '"source": "dispatcher-synthesized"' "PowerShell synthesized review status is auditable"
assert_file_contains "$REVIEW_STATE" '"state": "completed"' "PowerShell review instructions-only worker completes"

echo "PASS: run-with-it-dispatch.ps1 contract"
