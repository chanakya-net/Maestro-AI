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

assert_file_contains "$DISPATCHER" '[switch]$Detach' "PowerShell dispatcher exposes detach switch"
assert_file_contains "$DISPATCHER" 'STATUS|type=dispatch-detached' "PowerShell dispatcher reports detached launch"
assert_file_contains "$DISPATCHER" 'complexity,impl,modify' "PowerShell dispatcher auto-fails stalled implementation and modification workers by default"
ps_hard_limit_completion_checks="$(python3 - "$DISPATCHER" <<'PY'
import sys

text = open(sys.argv[1], encoding="utf-8").read()
start = text.index('if ($HardLimitSeconds -ne 0')
end = text.index('if (($state -eq "stalled")', start)
print(text[start:end].count('if (Test-CompletionReady)'))
PY
)"
[[ "$ps_hard_limit_completion_checks" == "2" ]] || fail "PowerShell hard-limit path must check completion before and after synthesis"

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
cp "${ROOT_DIR}/assets/run-agent.ps1" "${ROOT_DIR}/assets/run-with-it-dispatch.ps1" "${ROOT_DIR}/assets/worker-watch.ps1" "${ROOT_DIR}/assets/run-with-it-artifacts.py" "$SMOKE_ASSET_ROOT/"
cp "${ROOT_DIR}/assets/prompt.md" "$SMOKE_ASSET_ROOT/"
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
Set-Content -Path (Join-Path $RepoRoot "marker.txt") -Value "seen-$env:RUN_WITH_IT_ISSUE" -Encoding UTF8
& git -C $RepoRoot add marker.txt | Out-Null
& git -C $RepoRoot commit -m "impl fake marker" | Out-Null
$commitSha = (& git -C $RepoRoot rev-parse HEAD).Trim()
Set-Content -Path $resultFile -Value "{`"schema_version`":1,`"issue`":`"$env:RUN_WITH_IT_ISSUE`",`"role`":`"$env:RUN_WITH_IT_ROLE`",`"status`":`"success`",`"commit_sha`":`"$commitSha`",`"files_committed`":[`"marker.txt`"],`"verification`":{`"passed`":true,`"commands`":[`"fake`"]},`"repo_root_seen`":`"$RepoRoot`",`"agent_env`":`"$env:AGENT`",`"model_env`":`"$env:MODEL`",`"forced_agent_env`":`"$env:FORCED_AGENT`",`"forced_model_env`":`"$env:FORCED_MODEL`",`"legacy_marker_env`":`"$env:RUN_WITH_IT_EXPLICIT_LEGACY_OVERRIDES`"}" -Encoding UTF8
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

STALL_COMPLEXITY_AGENT="${WORK_DIR}/stall-complexity-agent.ps1"
cat > "$STALL_COMPLEXITY_AGENT" <<'PS1'
param([string]$RepoRoot, [string]$Prompt)
if ($Prompt -eq "--version") {
  Write-Output "stall-complexity-agent 1.0"
  exit 0
}
$scores = @{
  dependency_complexity = 1
  ownership_overlap_risk = 1
  architecture_risk = 1
  orchestration_burden = 1
  verification_risk = 1
  ambiguity_of_requirements = 1
  integration_surface_breadth = 1
  rollback_recovery_risk = 1
  blast_radius = 1
}
$rationale = @{}
foreach ($key in $scores.Keys) { $rationale[$key] = "fixture" }
@{ total = 9; level = "quite-easy"; scores = $scores; rationale = $rationale } |
  ConvertTo-Json -Depth 5 |
  Write-Output
New-Item -ItemType Directory -Force -Path (Split-Path $env:RUN_WITH_IT_DONE_FILE) | Out-Null
Set-Content -Path $env:RUN_WITH_IT_DONE_FILE -Value "DONE|issue=$env:RUN_WITH_IT_ISSUE|role=complexity|status=success|source=agent" -Encoding UTF8
Start-Sleep -Seconds 4
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
    },
    "stall-complexity": {
      "display_name": "Stall Complexity",
      "detection": { "command": "${PS_CMD}", "args": ["-NoProfile", "-File", "${STALL_COMPLEXITY_AGENT}", "unused", "--version"] },
      "invocation": {
        "command": "${PS_CMD}",
        "args_template": ["-NoProfile", "-File", "${STALL_COMPLEXITY_AGENT}", "{{repo_root}}", "{{prompt}}"],
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
  -Effort xhigh \
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
assert_contains "$dry_output" "RUN_WITH_IT_ARTIFACT_HELPER=${SMOKE_ASSET_ROOT}/run-with-it-artifacts.py" "dry-run exposes artifact helper to workers"
assert_contains "$dry_output" "run-agent.ps1" "dry-run wraps run-agent.ps1"
assert_contains "$dry_output" "--effort xhigh" "PowerShell dry-run forwards model effort"

"$PS_CMD" -NoProfile -File "$DISPATCHER" \
  -ValidateOnly \
  -AssetRoot "$SMOKE_ASSET_ROOT" \
  -Role impl \
  -Issue 42 \
  -Cycle 1 \
  -Agent fake \
  -Model fake-model \
  -Effort xhigh \
  -ContextFile "$CONTEXT_FILE" \
  -PromptFile "$PROMPT_FILE" \
  -LogFile "$LOG_FILE" \
  -DoneFile "$DONE_FILE" \
  -ResultFile "$RESULT_FILE" \
  -StateFile "$STATE_FILE" \
  -RepoRoot "$SMOKE_REPO_ROOT" \
  -IssueDir "$ISSUE_DIR" \
  -StatusFile "$STATUS_FILE" \
  -EventsLog "$EVENTS_LOG" >/dev/null
assert_file_contains "$STATE_FILE" '"effort": "xhigh"' "PowerShell validate-only records model effort"

PS_DEFAULT_LIMIT_STATE="${WORK_DIR}/default-sub-coord.state.json"
"$PS_CMD" -NoProfile -File "$DISPATCHER" \
  -ValidateOnly \
  -AssetRoot "$SMOKE_ASSET_ROOT" \
  -Role sub-coord \
  -Issue 420 \
  -Agent fake \
  -Model fake-model \
  -ContextFile "$CONTEXT_FILE" \
  -PromptFile "$PROMPT_FILE" \
  -LogFile "${WORK_DIR}/default-sub-coord.log" \
  -DoneFile "${WORK_DIR}/default-sub-coord.done" \
  -ResultFile "${WORK_DIR}/default-sub-coord-result.json" \
  -StateFile "$PS_DEFAULT_LIMIT_STATE" >/dev/null
assert_file_contains "$PS_DEFAULT_LIMIT_STATE" '"hard_limit_seconds": 0' "PowerShell sub-coordinator defaults to no hard limit"

PS_EXPLICIT_LIMIT_STATE="${WORK_DIR}/explicit-sub-coord.state.json"
"$PS_CMD" -NoProfile -File "$DISPATCHER" \
  -ValidateOnly \
  -AssetRoot "$SMOKE_ASSET_ROOT" \
  -Role sub-coord \
  -Issue 421 \
  -Agent fake \
  -Model fake-model \
  -ContextFile "$CONTEXT_FILE" \
  -PromptFile "$PROMPT_FILE" \
  -LogFile "${WORK_DIR}/explicit-sub-coord.log" \
  -DoneFile "${WORK_DIR}/explicit-sub-coord.done" \
  -ResultFile "${WORK_DIR}/explicit-sub-coord-result.json" \
  -StateFile "$PS_EXPLICIT_LIMIT_STATE" \
  -HardLimitSeconds 2 >/dev/null
assert_file_contains "$PS_EXPLICIT_LIMIT_STATE" '"hard_limit_seconds": 2' "PowerShell explicit sub-coordinator hard limit remains authoritative"

PS_INVALID_LIMIT_STATE="${WORK_DIR}/invalid-limit.state.json"
set +e
RUN_WITH_IT_WORKER_HARD_LIMIT_SECONDS=invalid "$PS_CMD" -NoProfile -File "$DISPATCHER" \
  -ValidateOnly \
  -AssetRoot "$SMOKE_ASSET_ROOT" \
  -Role impl \
  -Issue 422 \
  -Agent fake \
  -Model fake-model \
  -ContextFile "$CONTEXT_FILE" \
  -PromptFile "$PROMPT_FILE" \
  -LogFile "${WORK_DIR}/invalid-limit.log" \
  -DoneFile "${WORK_DIR}/invalid-limit.done" \
  -ResultFile "${WORK_DIR}/invalid-limit-result.json" \
  -StateFile "$PS_INVALID_LIMIT_STATE" >/dev/null 2>&1
invalid_limit_status="$?"
set -e
[[ "$invalid_limit_status" == "0" ]] || fail "PowerShell malformed hard limit must fall back instead of terminating"
assert_file_contains "$PS_INVALID_LIMIT_STATE" '"hard_limit_seconds": 7200' "PowerShell malformed hard limit uses documented default"

AGENT=ambient-agent MODEL=ambient-model RUN_WITH_IT_EXPLICIT_LEGACY_OVERRIDES=AGENT,MODEL \
  RUN_WITH_IT_HEARTBEAT_SECONDS=1 "$PS_CMD" -NoProfile -File "$DISPATCHER" \
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
assert_file_contains "$RESULT_FILE" '"agent_env":""' "PowerShell dispatch scrubs ambient AGENT before child launch"
assert_file_contains "$RESULT_FILE" '"model_env":""' "PowerShell dispatch scrubs ambient MODEL before child launch"
assert_file_contains "$RESULT_FILE" '"forced_agent_env":""' "PowerShell ambient marker cannot promote AGENT to a forced override"
assert_file_contains "$RESULT_FILE" '"forced_model_env":""' "PowerShell ambient marker cannot promote MODEL to a forced override"
assert_file_contains "$RESULT_FILE" '"legacy_marker_env":""' "PowerShell dispatch scrubs the ambient legacy marker before child launch"
assert_json_file "$STATE_FILE" "dispatch state JSON is valid"
assert_file_contains "$STATE_FILE" '"state": "completed"' "dispatch records completed state"

LEGACY_LOG_FILE="${ISSUE_DIR}/workers/impl/cycle-2.log"
LEGACY_DONE_FILE="${ISSUE_DIR}/workers/impl/cycle-2.done"
LEGACY_RESULT_FILE="${ISSUE_DIR}/workers/impl/cycle-2-result.json"
LEGACY_STATE_FILE="${ISSUE_DIR}/workers/impl/cycle-2.state.json"
printf 'RESULT_FILE=%s\n' "$LEGACY_RESULT_FILE" > "$CONTEXT_FILE"
AGENT=legacy-agent MODEL=legacy-model RUN_WITH_IT_EXPLICIT_LEGACY_OVERRIDES=AGENT,MODEL \
  FORCED_AGENT=canonical-agent FORCED_MODEL=canonical-model \
  "$PS_CMD" -NoProfile -File "$DISPATCHER" \
  -AssetRoot "$SMOKE_ASSET_ROOT" \
  -Role impl \
  -Issue 43 \
  -Cycle 2 \
  -Agent fake \
  -Model fake-model \
  -ContextFile "$CONTEXT_FILE" \
  -PromptFile "$PROMPT_FILE" \
  -LogFile "$LEGACY_LOG_FILE" \
  -DoneFile "$LEGACY_DONE_FILE" \
  -ResultFile "$LEGACY_RESULT_FILE" \
  -StateFile "$LEGACY_STATE_FILE" \
  -RepoRoot "$SMOKE_REPO_ROOT" \
  -IssueDir "$ISSUE_DIR" \
  -StatusFile "$STATUS_FILE" \
  -EventsLog "$EVENTS_LOG" \
  -PollSeconds 1 >/dev/null

assert_file_contains "$LEGACY_RESULT_FILE" '"agent_env":""' "PowerShell legacy AGENT is scrubbed when canonical overrides propagate"
assert_file_contains "$LEGACY_RESULT_FILE" '"model_env":""' "PowerShell legacy MODEL is scrubbed when canonical overrides propagate"
assert_file_contains "$LEGACY_RESULT_FILE" '"forced_agent_env":"canonical-agent"' "PowerShell canonical FORCED_AGENT propagates unchanged and takes precedence"
assert_file_contains "$LEGACY_RESULT_FILE" '"forced_model_env":"canonical-model"' "PowerShell canonical FORCED_MODEL propagates unchanged and takes precedence"
assert_file_contains "$LEGACY_RESULT_FILE" '"legacy_marker_env":""' "PowerShell legacy marker is scrubbed when canonical overrides propagate"
printf 'RESULT_FILE=%s\n' "$RESULT_FILE" > "$CONTEXT_FILE"

DETACH_ISSUE_DIR="${SMOKE_PROJECT}/.run-with-it/issues/45"
DETACH_CONTEXT="${SMOKE_PROJECT}/detach-context.md"
DETACH_LOG="${DETACH_ISSUE_DIR}/workers/impl/cycle-1.log"
DETACH_DONE="${DETACH_ISSUE_DIR}/workers/impl/cycle-1.done"
DETACH_RESULT="${DETACH_ISSUE_DIR}/workers/impl/cycle-1-result.json"
DETACH_STATE="${DETACH_ISSUE_DIR}/workers/impl/cycle-1.state.json"
DETACH_OUT="${DETACH_ISSUE_DIR}/workers/impl/cycle-1.dispatch.out"
mkdir -p "$(dirname "$DETACH_RESULT")"
printf 'RESULT_FILE=%s\n' "$DETACH_RESULT" > "$DETACH_CONTEXT"

AGENT=detached-ambient-agent MODEL=detached-ambient-model RUN_WITH_IT_EXPLICIT_LEGACY_OVERRIDES=AGENT,MODEL \
  FORCED_AGENT=detached-canonical-agent FORCED_MODEL=detached-canonical-model \
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
assert_file_contains "$DETACH_RESULT" '"agent_env":""' "detached PowerShell dispatcher scrubs ambient AGENT"
assert_file_contains "$DETACH_RESULT" '"model_env":""' "detached PowerShell dispatcher scrubs ambient MODEL"
assert_file_contains "$DETACH_RESULT" '"forced_agent_env":"detached-canonical-agent"' "detached PowerShell child receives canonical FORCED_AGENT unchanged"
assert_file_contains "$DETACH_RESULT" '"forced_model_env":"detached-canonical-model"' "detached PowerShell child receives canonical FORCED_MODEL unchanged"
assert_file_contains "$DETACH_RESULT" '"legacy_marker_env":""' "detached PowerShell dispatcher scrubs the legacy marker"

SILENT_ISSUE_DIR="${SMOKE_PROJECT}/.run-with-it/issues/43"
SILENT_CONTEXT="${SMOKE_PROJECT}/silent-context.md"
SILENT_LOG="${SILENT_ISSUE_DIR}/workers/impl/cycle-1.log"
SILENT_DONE="${SILENT_ISSUE_DIR}/workers/impl/cycle-1.done"
SILENT_RESULT="${SILENT_ISSUE_DIR}/workers/impl/cycle-1-result.json"
SILENT_STATE="${SILENT_ISSUE_DIR}/workers/impl/cycle-1.state.json"
mkdir -p "$(dirname "$SILENT_RESULT")"
printf 'RESULT_FILE=%s\n' "$SILENT_RESULT" > "$SILENT_CONTEXT"

set +e
RUN_WITH_IT_HEARTBEAT_SECONDS=1 "$PS_CMD" -NoProfile -File "$DISPATCHER" \
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

wait "$silent_pid"
silent_status="$?"
set -e
[[ "$silent_status" == "0" ]] || fail "heartbeat-alive quiet PowerShell impl worker must complete"
assert_json_file "$SILENT_STATE" "silent final state JSON is valid"
assert_file_contains "$SILENT_STATE" '"state": "completed"' "heartbeat-alive quiet PowerShell worker completes"
assert_file_contains "$EVENTS_LOG" "STATUS|type=wrapper-heartbeat|issue=43|role=impl" "PowerShell runner emits wrapper heartbeat while model output is quiet"
assert_file_contains "$EVENTS_LOG" "STATUS|type=dispatch-complete|issue=43|role=impl|cycle=1" "quiet PowerShell worker exits dispatcher successfully"

HARD_LOG="${SILENT_ISSUE_DIR}/workers/impl/hard.log"
HARD_DONE="${SILENT_ISSUE_DIR}/workers/impl/hard.done"
HARD_RESULT="${SILENT_ISSUE_DIR}/workers/impl/hard-result.json"
HARD_STATE="${SILENT_ISSUE_DIR}/workers/impl/hard.state.json"
set +e
RUN_WITH_IT_HEARTBEAT_SECONDS=1 "$PS_CMD" -NoProfile -File "$DISPATCHER" \
  -AssetRoot "$SMOKE_ASSET_ROOT" -Role impl -Issue 431 -Cycle 1 \
  -Agent silent -Model fake-model -ContextFile "$SILENT_CONTEXT" -PromptFile "$PROMPT_FILE" \
  -LogFile "$HARD_LOG" -DoneFile "$HARD_DONE" -ResultFile "$HARD_RESULT" -StateFile "$HARD_STATE" \
  -RepoRoot "$SMOKE_REPO_ROOT" -IssueDir "$SILENT_ISSUE_DIR" -StatusFile "$STATUS_FILE" -EventsLog "$EVENTS_LOG" \
  -PollSeconds 1 -QuietSeconds 1 -StallSeconds 10 -HardLimitSeconds 2 >/dev/null
hard_status="$?"
set -e
[[ "$hard_status" == "124" ]] || fail "PowerShell hard limit must bound a heartbeat-alive worker with no progress"
assert_file_contains "$HARD_STATE" '"stall_reason": "hard-limit-exceeded"' "PowerShell hard limit records precise reason"
assert_file_contains "$EVENTS_LOG" "STATUS|type=worker-hard-limit|issue=431|role=impl|cycle=1" "PowerShell hard limit emits structured status"

PS_HARD_COMPLEXITY_DIR="${SMOKE_PROJECT}/.run-with-it/issues/481"
PS_HARD_COMPLEXITY_LOG="${PS_HARD_COMPLEXITY_DIR}/workers/complexity/cycle-1.log"
PS_HARD_COMPLEXITY_DONE="${PS_HARD_COMPLEXITY_DIR}/workers/complexity/cycle-1.done"
PS_HARD_COMPLEXITY_RESULT="${PS_HARD_COMPLEXITY_DIR}/workers/complexity/cycle-1-result.json"
PS_HARD_COMPLEXITY_STATE="${PS_HARD_COMPLEXITY_DIR}/workers/complexity/cycle-1.state.json"
set +e
RUN_WITH_IT_HEARTBEAT_SECONDS=1 "$PS_CMD" -NoProfile -File "$DISPATCHER" \
  -AssetRoot "$SMOKE_ASSET_ROOT" -Role complexity -Issue 481 -Cycle 1 \
  -Agent stall-complexity -Model fake-model -ContextFile "$CONTEXT_FILE" -PromptFile "$PROMPT_FILE" \
  -LogFile "$PS_HARD_COMPLEXITY_LOG" -DoneFile "$PS_HARD_COMPLEXITY_DONE" -ResultFile "$PS_HARD_COMPLEXITY_RESULT" -StateFile "$PS_HARD_COMPLEXITY_STATE" \
  -RepoRoot "$SMOKE_REPO_ROOT" -IssueDir "$PS_HARD_COMPLEXITY_DIR" -StatusFile "$STATUS_FILE" -EventsLog "$EVENTS_LOG" \
  -PollSeconds 1 -QuietSeconds 1 -StallSeconds 10 -HardLimitSeconds 2 >/dev/null
ps_hard_complexity_status="$?"
set -e
[[ "$ps_hard_complexity_status" == "0" ]] || fail "PowerShell hard-limit accepts synthesized complexity artifact"
assert_json_file "$PS_HARD_COMPLEXITY_RESULT" "PowerShell hard-limit complexity synthesis writes valid JSON"
assert_file_contains "$PS_HARD_COMPLEXITY_STATE" '"state": "completed"' "PowerShell hard-limit complexity synthesis records completion"
assert_file_contains "$EVENTS_LOG" "STATUS|type=worker-hard-limit|issue=481|role=complexity|cycle=1" "PowerShell hard-limit complexity synthesis records salvage decision"

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

# A stalled complexity worker may have emitted valid JSON and the done sentinel
# before hanging. Synthesis turns that log output into a complete result artifact;
# PowerShell must accept it exactly as the Bash dispatcher does.
STALL_COMPLEXITY_ISSUE_DIR="${SMOKE_PROJECT}/.run-with-it/issues/48"
STALL_COMPLEXITY_LOG="${STALL_COMPLEXITY_ISSUE_DIR}/workers/complexity/cycle-1.log"
STALL_COMPLEXITY_DONE="${STALL_COMPLEXITY_ISSUE_DIR}/workers/complexity/cycle-1.done"
STALL_COMPLEXITY_RESULT="${STALL_COMPLEXITY_ISSUE_DIR}/workers/complexity/cycle-1-result.json"
STALL_COMPLEXITY_STATE="${STALL_COMPLEXITY_ISSUE_DIR}/workers/complexity/cycle-1.state.json"
set +e
RUN_WITH_IT_HEARTBEAT_SECONDS=0 "$PS_CMD" -NoProfile -File "$DISPATCHER" \
  -AssetRoot "$SMOKE_ASSET_ROOT" -Role complexity -Issue 48 -Cycle 1 \
  -Agent stall-complexity -Model fake-model -ContextFile "$CONTEXT_FILE" -PromptFile "$PROMPT_FILE" \
  -LogFile "$STALL_COMPLEXITY_LOG" -DoneFile "$STALL_COMPLEXITY_DONE" -ResultFile "$STALL_COMPLEXITY_RESULT" -StateFile "$STALL_COMPLEXITY_STATE" \
  -RepoRoot "$SMOKE_REPO_ROOT" -IssueDir "$STALL_COMPLEXITY_ISSUE_DIR" -StatusFile "$STATUS_FILE" -EventsLog "$EVENTS_LOG" \
  -PollSeconds 1 -QuietSeconds 1 -StallSeconds 2 >/dev/null
stall_complexity_status="$?"
set -e
[[ "$stall_complexity_status" == "0" ]] || fail "PowerShell dispatcher must complete a valid complexity artifact synthesized at stall"
assert_json_file "$STALL_COMPLEXITY_RESULT" "PowerShell stall synthesis writes valid complexity JSON"
assert_file_contains "$STALL_COMPLEXITY_STATE" '"state": "completed"' "PowerShell synthesized complexity stall records completion"
assert_file_contains "$EVENTS_LOG" "STATUS|type=worker-stall-timeout|issue=48|role=complexity|cycle=1" "PowerShell synthesized complexity stall emits timeout decision"
assert_file_contains "$EVENTS_LOG" "action=salvage-and-terminate" "PowerShell synthesized complexity stall records successful salvage"

# --- #2: committed work is synthesized even when the worker exits NONZERO (parity with Bash) ---
COMMIT_FAIL_AGENT="${WORK_DIR}/commit-then-fail-agent.ps1"
cat > "$COMMIT_FAIL_AGENT" <<'PS1'
param([string]$RepoRoot, [string]$Prompt)
if ($Prompt -eq "--version") {
  Write-Output "commit-then-fail-agent 1.0"
  exit 0
}
if (-not $RepoRoot) { $RepoRoot = $env:REPO_ROOT }
& git -C $RepoRoot config user.email "test@example.com" | Out-Null
& git -C $RepoRoot config user.name "Test User" | Out-Null
Set-Content -Path (Join-Path $RepoRoot "crashed.txt") -Value "committed then crashed" -Encoding UTF8
& git -C $RepoRoot add crashed.txt | Out-Null
& git -C $RepoRoot commit -m "impl committed before crash" | Out-Null
[Console]::Error.WriteLine("transient backend hiccup, dropping connection")
exit 7
PS1

# --- #1: agent-unavailable (auth) with no committed work → infrastructure failure class ---
UNAVAIL_AGENT="${WORK_DIR}/unavailable-agent.ps1"
cat > "$UNAVAIL_AGENT" <<'PS1'
param([string]$RepoRoot, [string]$Prompt)
if ($Prompt -eq "--version") {
  Write-Output "unavailable-agent 1.0"
  exit 0
}
if ($env:HARD_LIMIT_HANG_SECONDS) {
  Write-Output "STATUS|type=agent-unavailable|issue=$env:RUN_WITH_IT_ISSUE|role=$env:RUN_WITH_IT_ROLE|agent=unavailable|model=fake-model|reason=auth|action=exclude-route"
  Start-Sleep -Seconds ([int]$env:HARD_LIMIT_HANG_SECONDS)
  exit 1
}
[Console]::Error.WriteLine("API error: 401 authentication failed for this account")
exit 1
PS1

python3 - "${SMOKE_ASSET_ROOT}/agent-registry.json" "${PS_CMD}" "${COMMIT_FAIL_AGENT}" "${UNAVAIL_AGENT}" <<'PY'
import json, sys
path, ps_cmd, commit_fail, unavail = sys.argv[1:5]
with open(path) as handle:
    registry = json.load(handle)
def entry(script, name):
    return {
        "display_name": name,
        "detection": {"command": ps_cmd, "args": ["-NoProfile", "-File", script, "unused", "--version"]},
        "invocation": {
            "command": ps_cmd,
            "args_template": ["-NoProfile", "-File", script, "{{repo_root}}", "{{prompt}}"],
            "prompt_argument_template": "{{prompt}}",
        },
        "permission_modes": {"default": "", "available": [""]},
        "model": {"default": "fake-model", "flag_template": "", "known_models": ["fake-model"]},
        "capability_band": "balanced",
        "fallback_order": [],
        "user_model_configuration": {
            "requires_user_model_config": False,
            "config_paths": [],
            "skip_when_unconfigured": False,
            "skip_message": "",
        },
    }
registry["agents"]["commit-then-fail"] = entry(commit_fail, "Commit Then Fail")
registry["agents"]["unavailable"] = entry(unavail, "Unavailable")
with open(path, "w") as handle:
    json.dump(registry, handle, indent=2)
PY

PSFAIL_ISSUE_DIR="${SMOKE_PROJECT}/.run-with-it/issues/46"
PSFAIL_CONTEXT="${SMOKE_PROJECT}/psfail-context.md"
PSFAIL_LOG="${PSFAIL_ISSUE_DIR}/workers/impl/cycle-1.log"
PSFAIL_DONE="${PSFAIL_ISSUE_DIR}/workers/impl/cycle-1.done"
PSFAIL_RESULT="${PSFAIL_ISSUE_DIR}/workers/impl/cycle-1-result.json"
PSFAIL_STATE="${PSFAIL_ISSUE_DIR}/workers/impl/cycle-1.state.json"
mkdir -p "$(dirname "$PSFAIL_RESULT")"
printf 'RESULT_FILE=%s\n' "$PSFAIL_RESULT" > "$PSFAIL_CONTEXT"

set +e
"$PS_CMD" -NoProfile -File "$DISPATCHER" \
  -AssetRoot "$SMOKE_ASSET_ROOT" \
  -Role impl \
  -Issue 46 \
  -Cycle 1 \
  -Agent commit-then-fail \
  -Model fake-model \
  -ContextFile "$PSFAIL_CONTEXT" \
  -PromptFile "$PROMPT_FILE" \
  -LogFile "$PSFAIL_LOG" \
  -DoneFile "$PSFAIL_DONE" \
  -ResultFile "$PSFAIL_RESULT" \
  -StateFile "$PSFAIL_STATE" \
  -RepoRoot "$SMOKE_REPO_ROOT" \
  -IssueDir "$PSFAIL_ISSUE_DIR" \
  -StatusFile "$STATUS_FILE" \
  -EventsLog "$EVENTS_LOG" \
  -PollSeconds 1 >/dev/null
psfail_status="$?"
set -e

[[ "$psfail_status" == "75" ]] || fail "PowerShell committed crash must request artifact recovery"
assert_json_file "$PSFAIL_RESULT" "PowerShell synthesizes committed work even when the worker exits nonzero"
assert_file_contains "$PSFAIL_RESULT" '"crashed.txt"' "PowerShell nonzero-exit synthesis records the committed file"
assert_file_contains "$PSFAIL_RESULT" '"source": "dispatcher-synthesized"' "PowerShell nonzero-exit synthesis is auditable"
assert_file_contains "$PSFAIL_STATE" '"state": "artifact-recovery-required"' "PowerShell nonzero-exit committed work enters typed recovery"
assert_file_contains "$EVENTS_LOG" "STATUS|type=result-artifact-synthesized|issue=46|role=impl|cycle=1" "PowerShell nonzero-exit synthesis emits status"
assert_file_contains "$EVENTS_LOG" "STATUS|type=dispatch-recovery-required|issue=46|role=impl|cycle=1" "PowerShell nonzero-exit synthesis requests recovery"

UNAVAIL_ISSUE_DIR="${SMOKE_PROJECT}/.run-with-it/issues/47"
UNAVAIL_CONTEXT="${SMOKE_PROJECT}/unavail-context.md"
UNAVAIL_LOG="${UNAVAIL_ISSUE_DIR}/workers/impl/cycle-1.log"
UNAVAIL_DONE="${UNAVAIL_ISSUE_DIR}/workers/impl/cycle-1.done"
UNAVAIL_RESULT="${UNAVAIL_ISSUE_DIR}/workers/impl/cycle-1-result.json"
UNAVAIL_STATE="${UNAVAIL_ISSUE_DIR}/workers/impl/cycle-1.state.json"
mkdir -p "$(dirname "$UNAVAIL_RESULT")"
printf 'RESULT_FILE=%s\n' "$UNAVAIL_RESULT" > "$UNAVAIL_CONTEXT"

set +e
"$PS_CMD" -NoProfile -File "$DISPATCHER" \
  -AssetRoot "$SMOKE_ASSET_ROOT" \
  -Role impl \
  -Issue 47 \
  -Cycle 1 \
  -Agent unavailable \
  -Model fake-model \
  -ContextFile "$UNAVAIL_CONTEXT" \
  -PromptFile "$PROMPT_FILE" \
  -LogFile "$UNAVAIL_LOG" \
  -DoneFile "$UNAVAIL_DONE" \
  -ResultFile "$UNAVAIL_RESULT" \
  -StateFile "$UNAVAIL_STATE" \
  -RepoRoot "$SMOKE_REPO_ROOT" \
  -IssueDir "$UNAVAIL_ISSUE_DIR" \
  -StatusFile "$STATUS_FILE" \
  -EventsLog "$EVENTS_LOG" \
  -PollSeconds 1 >/dev/null
unavail_status="$?"
set -e

[[ "$unavail_status" != "0" ]] || fail "agent-unavailable PowerShell failure should not report success"
assert_file_contains "$UNAVAIL_LOG" "STATUS|type=agent-unavailable" "PowerShell runner records agent-unavailable from auth error"
assert_file_contains "$EVENTS_LOG" "|reason=missing-result-artifact|failure_class=infrastructure|" "PowerShell dispatch-failed classifies availability loss as infrastructure"
assert_file_contains "$UNAVAIL_STATE" '"failure_class": "infrastructure"' "PowerShell state JSON records infrastructure failure class"

PS_HARD_UNAVAIL_DIR="${SMOKE_PROJECT}/.run-with-it/issues/471"
PS_HARD_UNAVAIL_LOG="${PS_HARD_UNAVAIL_DIR}/workers/plan/cycle-1.log"
PS_HARD_UNAVAIL_DONE="${PS_HARD_UNAVAIL_DIR}/workers/plan/cycle-1.done"
PS_HARD_UNAVAIL_RESULT="${PS_HARD_UNAVAIL_DIR}/workers/plan/cycle-1-result.json"
PS_HARD_UNAVAIL_STATE="${PS_HARD_UNAVAIL_DIR}/workers/plan/cycle-1.state.json"
set +e
HARD_LIMIT_HANG_SECONDS=4 RUN_WITH_IT_HEARTBEAT_SECONDS=1 "$PS_CMD" -NoProfile -File "$DISPATCHER" \
  -AssetRoot "$SMOKE_ASSET_ROOT" -Role plan -Issue 471 -Cycle 1 \
  -Agent unavailable -Model fake-model -ContextFile "$UNAVAIL_CONTEXT" -PromptFile "$PROMPT_FILE" \
  -LogFile "$PS_HARD_UNAVAIL_LOG" -DoneFile "$PS_HARD_UNAVAIL_DONE" -ResultFile "$PS_HARD_UNAVAIL_RESULT" -StateFile "$PS_HARD_UNAVAIL_STATE" \
  -RepoRoot "$SMOKE_REPO_ROOT" -IssueDir "$PS_HARD_UNAVAIL_DIR" -StatusFile "$STATUS_FILE" -EventsLog "$EVENTS_LOG" \
  -PollSeconds 1 -QuietSeconds 1 -StallSeconds 10 -HardLimitSeconds 2 >/dev/null
ps_hard_unavail_status="$?"
set -e
[[ "$ps_hard_unavail_status" == "124" ]] || fail "PowerShell hard-limit unavailable worker exits 124"
assert_file_contains "$PS_HARD_UNAVAIL_STATE" '"failure_class": "infrastructure"' "PowerShell hard-limit failure uses artifact classifier"

echo "PASS: run-with-it-dispatch.ps1 contract"
