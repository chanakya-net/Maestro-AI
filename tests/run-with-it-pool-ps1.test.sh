#!/usr/bin/env bash

set -euo pipefail

unset RUN_WITH_IT_DETACHED_CHILD

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POOL="${ROOT_DIR}/assets/powershell/run-with-it-pool.ps1"
PS_CMD="${PWSH:-}"
if [[ -z "$PS_CMD" ]]; then
  PS_CMD="$(command -v pwsh || command -v powershell.exe || command -v powershell || true)"
fi

if [[ -z "$PS_CMD" ]]; then
  echo "SKIP: PowerShell unavailable for run-with-it-pool.ps1 contract"
  exit 0
fi

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_file_contains() {
  local file="$1"
  local needle="$2"
  local message="$3"
  grep -Fq -- "$needle" "$file" || fail "$message (missing: $needle in $file)"
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"
  [[ "${haystack}" == *"${needle}"* ]] || fail "${message} (missing: ${needle})"
}

assert_json_file() {
  local file="$1"
  local message="$2"
  python3 -m json.tool "$file" >/dev/null || fail "$message (invalid JSON: $file)"
}

assert_file_contains "$POOL" "Analyze-SubCoordFailure" "PowerShell pool includes sub-coordinator failure analysis"
assert_file_contains "$POOL" "sub-coord-recovery-wait" "PowerShell pool can wait for in-flight workers before recovery"
assert_file_contains "$POOL" "sub-coord-recovery-spawn" "PowerShell pool can spawn recovery sub-coordinators"

BASE_DIR="$(mktemp -d)"
WORK_DIR="${BASE_DIR}/with spaces"
mkdir -p "$WORK_DIR"
cleanup() {
  if [[ "${KEEP_RUN_WITH_IT_SMOKE:-0}" == "1" ]]; then
    echo "SMOKE_WORK_DIR=$WORK_DIR" >&2
  else
    rm -rf "$BASE_DIR"
  fi
}
trap cleanup EXIT

SMOKE_ASSET_ROOT="${WORK_DIR}/assets"
SMOKE_PROJECT="${WORK_DIR}/project"
SMOKE_REPO_ROOT="${WORK_DIR}/repo-root"
mkdir -p "$SMOKE_ASSET_ROOT"/{powershell,python,prompts} "$SMOKE_PROJECT/.run-with-it/contexts" "$SMOKE_REPO_ROOT"
cp \
  "${ROOT_DIR}/assets/powershell/run-agent.ps1" \
  "${ROOT_DIR}/assets/powershell/run-with-it-dispatch.ps1" \
  "${ROOT_DIR}/assets/powershell/run-with-it-pool.ps1" \
  "${ROOT_DIR}/assets/powershell/worker-watch.ps1" \
  "$SMOKE_ASSET_ROOT/powershell/"
cp \
  "${ROOT_DIR}/assets/python/run-with-it-state.py" \
  "${ROOT_DIR}/assets/python/run-with-it-github-update.py" \
  "${ROOT_DIR}/assets/python/run-with-it-artifacts.py" \
  "$SMOKE_ASSET_ROOT/python/"
cp \
  "${ROOT_DIR}/assets/prompts/sub-coordinator-prompt.md" \
  "${ROOT_DIR}/assets/prompts/merge-recovery-prompt.md" \
  "$SMOKE_ASSET_ROOT/prompts/"

FAKE_AGENT="${WORK_DIR}/fake-sub-coordinator.ps1"
cat > "$FAKE_AGENT" <<'PS1'
param([string]$RepoRoot, [string]$Prompt)
if ($Prompt -eq "--version") {
  Write-Output "fake-sub-coordinator 1.0"
  exit 0
}

$resultFile = (($Prompt -split "`n") | Where-Object { $_ -like "RUN_WITH_IT_RESULT_FILE=*" } | Select-Object -First 1) -replace "^RUN_WITH_IT_RESULT_FILE=", ""
if (-not $resultFile) {
  $resultFile = Join-Path $env:RUN_WITH_IT_ISSUE_DIR "report.json"
}

New-Item -ItemType Directory -Force -Path $RepoRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $resultFile) | Out-Null
Write-Output "STATUS|type=heartbeat|issue=$env:RUN_WITH_IT_ISSUE|role=$env:RUN_WITH_IT_ROLE|phase=pool-smoke|progress=writing-report"
Write-Output "fake sub coordinator stdout for issue $env:RUN_WITH_IT_ISSUE"
Set-Content -Path $resultFile -Value "{`"outcome`":`"completed`",`"files_modified_count`":1,`"lines_added`":1,`"lines_deleted`":0,`"review_cycles`":0,`"commit_sha`":`"fake-$env:RUN_WITH_IT_ISSUE`"}" -Encoding UTF8
PS1

cat > "${SMOKE_ASSET_ROOT}/agent-registry.json" <<JSON
{
  "schema_version": 1,
  "aliases": {},
  "agents": {
    "fake": {
      "display_name": "Fake Sub Coordinator",
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
    }
  }
}
JSON

for issue in 101 102; do
  issue_dir="${SMOKE_PROJECT}/.run-with-it/issues/${issue}"
  context_file="${SMOKE_PROJECT}/.run-with-it/contexts/issue-${issue}.md"
  mkdir -p "$issue_dir"
  printf 'RUN_WITH_IT_RESULT_FILE=%s\nISSUE_WORKTREE_PATH=%s\n' \
    "${issue_dir}/report.json" \
    "$SMOKE_REPO_ROOT" > "$context_file"
done

STATE_FILE="${SMOKE_PROJECT}/.run-with-it/main-state.json"
STATUS_FILE="${SMOKE_PROJECT}/.run-with-it/status/current.txt"
EVENTS_LOG="${SMOKE_PROJECT}/.run-with-it/status/events.log"
MAIN_LOG="${SMOKE_PROJECT}/.run-with-it/main/main.log"
cat > "$STATE_FILE" <<JSON
{
  "execution_plan": {
    "parallel_jobs": 2,
    "topo_order": [101, 102]
  },
  "issue_registry": {
    "101": {
      "status": "pending",
      "deps": [],
      "title": "First smoke issue",
      "context_file": "${SMOKE_PROJECT}/.run-with-it/contexts/issue-101.md"
    },
    "102": {
      "status": "pending",
      "deps": [],
      "title": "Second smoke issue",
      "context_file": "${SMOKE_PROJECT}/.run-with-it/contexts/issue-102.md"
    }
  },
  "active_pool_issues": [],
  "completed_summaries": [],
  "merge_recovery_summaries": [],
  "ledger_rows": []
}
JSON

"$PS_CMD" -NoProfile -File "$POOL" \
  -AssetRoot "$SMOKE_ASSET_ROOT" \
  -StateFile "$STATE_FILE" \
  -ParallelJobs 2 \
  -Agent fake \
  -Model fake-model \
  -StatusFile "$STATUS_FILE" \
  -EventsLog "$EVENTS_LOG" \
  -MainLog "$MAIN_LOG" \
  -PollSeconds 1 \
  -TimeoutSeconds 30 >/dev/null

for issue in 101 102; do
  issue_dir="${SMOKE_PROJECT}/.run-with-it/issues/${issue}"
  assert_json_file "${issue_dir}/report.json" "pool result JSON is valid for issue ${issue}"
  assert_file_contains "${issue_dir}/sub-coordinator.log" "fake sub coordinator stdout for issue ${issue}" "pool captures sub-coordinator log for issue ${issue}"
  assert_file_contains "${issue_dir}/sub-coordinator.done" "DONE|issue=${issue}|role=sub-coord" "pool writes sub-coordinator done sentinel for issue ${issue}"
done

assert_file_contains "$EVENTS_LOG" "STATUS|type=pool-empty|state_file=$STATE_FILE" "pool emits pool-empty event"
assert_file_contains "$MAIN_LOG" "STATUS|type=sub-coord-spawn|issue=101" "pool logs first spawn"
assert_file_contains "$MAIN_LOG" "STATUS|type=sub-coord-spawn|issue=102" "pool logs second spawn"

python3 - "$STATE_FILE" <<'PY'
import json
import sys

path = sys.argv[1]
state = json.load(open(path))
registry = state["issue_registry"]
assert registry["101"]["status"] == "completed"
assert registry["102"]["status"] == "completed"
assert state["active_pool_issues"] == []
assert len(state["completed_summaries"]) == 2
assert any("task=101|outcome=completed" in row for row in state["ledger_rows"])
assert any("task=102|outcome=completed" in row for row in state["ledger_rows"])
PY

FLAT_ASSET_ROOT="${WORK_DIR}/flat-assets"
mkdir -p "${FLAT_ASSET_ROOT}/prompts" "${FLAT_ASSET_ROOT}/python"
cp "${ROOT_DIR}/assets/powershell/run-with-it-dispatch.ps1" "${FLAT_ASSET_ROOT}/run-with-it-dispatch.ps1"
cp "${ROOT_DIR}/assets/powershell/run-with-it-pool.ps1" "${FLAT_ASSET_ROOT}/run-with-it-pool.ps1"
cp "${ROOT_DIR}/assets/powershell/worker-watch.ps1" "${FLAT_ASSET_ROOT}/worker-watch.ps1"
cp "${ROOT_DIR}/assets/powershell/run-agent.ps1" "${FLAT_ASSET_ROOT}/run-agent.ps1"
cp "${ROOT_DIR}/assets/prompts/sub-coordinator-prompt.md" "${FLAT_ASSET_ROOT}/prompts/"
cp "${ROOT_DIR}/assets/prompts/merge-recovery-prompt.md" "${FLAT_ASSET_ROOT}/prompts/"
cp "${ROOT_DIR}/assets/python/run-with-it-state.py" "${FLAT_ASSET_ROOT}/python/"
cp "${ROOT_DIR}/assets/python/run-with-it-github-update.py" "${FLAT_ASSET_ROOT}/python/"
chmod +x "${FLAT_ASSET_ROOT}/run-with-it-dispatch.ps1" "${FLAT_ASSET_ROOT}/run-with-it-pool.ps1" "${FLAT_ASSET_ROOT}/run-agent.ps1" "${FLAT_ASSET_ROOT}/worker-watch.ps1"

flat_pool_output="$("$PS_CMD" -NoProfile -File "$POOL" \
  -AssetRoot "$FLAT_ASSET_ROOT" \
  -StateFile "$STATE_FILE" \
  -ParallelJobs 2 \
  -Agent fake \
  -Model fake-model \
  -StatusFile "$STATUS_FILE" \
  -EventsLog "$EVENTS_LOG" \
  -MainLog "$MAIN_LOG" \
  -PollSeconds 1 \
  -ValidateOnly \
  2>&1)"
flat_pool_status="$?"
assert_contains "${flat_pool_output}" "STATUS|type=pool-ready" "flat python layout still runs with legacy helper root"

set +e
cs_pool_output="$("$PS_CMD" -NoProfile -File "$POOL" \
  -AssetRoot "$FLAT_ASSET_ROOT" \
  -StateFile "$STATE_FILE" \
  -ParallelJobs 2 \
  -Agent fake \
  -Model fake-model \
  -StatusFile "$STATUS_FILE" \
  -EventsLog "$EVENTS_LOG" \
  -MainLog "$MAIN_LOG" \
  -PollSeconds 1 \
  -ValidateOnly \
  -HelperRuntime cs \
  2>&1)"
cs_pool_status="$?"
set -e

[[ "$cs_pool_status" != "0" ]] || fail "flat C# layout must fail when cs helper runtime is requested"
assert_contains "${cs_pool_output}" "missing nested asset layout for helper runtime 'cs'" "flat C# layout emits nested layout failure"

echo "PASS: run-with-it-pool.ps1 contract"
