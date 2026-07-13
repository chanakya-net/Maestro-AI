#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_FILE="${ROOT_DIR}/skills/run-with-it/SKILL.md"
SUB_COORDINATOR_PROMPT_FILE="${ROOT_DIR}/assets/sub-coordinator-prompt.md"
IMPLEMENTER_PROMPT_FILE="${ROOT_DIR}/assets/prompt.md"
MODIFIER_PROMPT_FILE="${ROOT_DIR}/assets/modifier-prompt.md"
COORDINATOR_RULES_FILE="${ROOT_DIR}/assets/coordinator-rules.md"
ORCHESTRATOR_RULES_FILE="${ROOT_DIR}/assets/main-orchestrator-rules.md"
README_FILE="${ROOT_DIR}/README.md"
INSTALL_SH="${ROOT_DIR}/install.sh"
INSTALL_PS1="${ROOT_DIR}/install.ps1"
PS_CMD="${PWSH:-}"
if [[ -z "$PS_CMD" ]]; then
  PS_CMD="$(command -v pwsh || command -v powershell.exe || command -v powershell || true)"
fi

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_file() {
  local file="$1"
  local message="$2"
  [[ -f "$file" ]] || fail "$message (missing: $file)"
}

assert_contains_file() {
  local file="$1"
  local needle="$2"
  local message="$3"
  grep -Fq -- "$needle" "$file" || fail "$message (missing: $needle in $file)"
}

assert_not_contains_file() {
  local file="$1"
  local needle="$2"
  local message="$3"
  if grep -Fq -- "$needle" "$file"; then
    fail "$message (unexpected: $needle in $file)"
  fi
}

assert_prompt_preserves_empty_pre_spawn_head() {
  local prompt_file="$1"
  local role="$2"
  local command_line
  local temp_dir
  local script_file

  command_line="$(grep -F "& python3 \$env:RUN_WITH_IT_ARTIFACT_HELPER write-json --role ${role}" "$prompt_file" | head -1)"
  [[ -n "$command_line" ]] || fail "missing PowerShell ${role} artifact command in $prompt_file"

  temp_dir="$(mktemp -d)"
  script_file="${temp_dir}/artifact-command.ps1"
  cat > "$script_file" <<'PS1'
function global:python3 {
    $received = @($args)
    if ($received.Count -lt 2 -or $received[-2] -ne "--pre-spawn-head" -or $received[-1] -ne "") {
        throw "--pre-spawn-head did not preserve its empty value: $($received -join '|')"
    }
    $global:LASTEXITCODE = 0
}
Remove-Item Env:ISSUE_BASE_SHA -ErrorAction SilentlyContinue
Remove-Item Env:REVIEW_HEAD_SHA -ErrorAction SilentlyContinue
$env:RUN_WITH_IT_ARTIFACT_HELPER = "artifact-helper.py"
$env:RUN_WITH_IT_ISSUE = "42"
$env:RUN_WITH_IT_RESULT_FILE = "result.json"
$payloadFile = "payload.json"
$checkinRepoRoot = "repo"
PS1
  printf '%s\n' "$command_line" >> "$script_file"
  if ! "$PS_CMD" -NoProfile -File "$script_file"; then
    rm -rf "$temp_dir"
    fail "PowerShell ${role} artifact command drops an unset pre-spawn-head value"
  fi
  rm -rf "$temp_dir"
}

assert_file "${ROOT_DIR}/assets/worker-watch.ps1" "PowerShell worker watcher asset exists"
assert_file "${ROOT_DIR}/assets/run-with-it-dispatch.ps1" "PowerShell dispatcher asset exists"
assert_file "${ROOT_DIR}/assets/run-with-it-pool.ps1" "PowerShell pool asset exists"

assert_contains_file "$SKILL_FILE" "run-with-it-dispatch.ps1" "skill documents PowerShell dispatcher"
assert_contains_file "$SKILL_FILE" "run-with-it-pool.ps1" "skill documents PowerShell pool"
assert_contains_file "$SKILL_FILE" "worker-watch.ps1" "skill documents PowerShell watcher"
assert_contains_file "$SKILL_FILE" "run-agent.ps1\") --list-agents --detected-only" "skill uses PowerShell runner for native Windows preflight"
assert_not_contains_file "$SKILL_FILE" "native PowerShell can install assets and run \`run-agent.ps1\`, but \`run-with-it\` orchestration requires Bash-only" "skill no longer blocks native PowerShell orchestration"

assert_contains_file "$SUB_COORDINATOR_PROMPT_FILE" "run-with-it-dispatch.ps1" "sub-coordinator prompt uses PowerShell dispatcher"
assert_contains_file "$SUB_COORDINATOR_PROMPT_FILE" "-Detach" "PowerShell worker launch uses detached dispatcher"
assert_contains_file "$SUB_COORDINATOR_PROMPT_FILE" "-StateFile \$WORKER_STATE_FILE" "PowerShell worker launch passes state file"
assert_contains_file "$SUB_COORDINATOR_PROMPT_FILE" '-Effort $EFFORT' "PowerShell worker launch passes routed effort"
assert_contains_file "$SUB_COORDINATOR_PROMPT_FILE" ".run-with-it\\issues" "PowerShell examples use issue-scoped artifact folder"
assert_contains_file "$SUB_COORDINATOR_PROMPT_FILE" '$ISSUE_BASE_REF = "origin/$env:RUN_FEATURE_BRANCH"' "PowerShell worktree bootstrap prefers remote shared tip"
assert_contains_file "$SUB_COORDINATOR_PROMPT_FILE" 'issue_base_source = $ISSUE_BASE_SOURCE' "PowerShell worktree bootstrap persists base source"
assert_contains_file "$SUB_COORDINATOR_PROMPT_FILE" 'git -C $ISSUE_WORKTREE_PATH status --porcelain' "PowerShell resume protects dirty work"
assert_not_contains_file "$SUB_COORDINATOR_PROMPT_FILE" ".run-with-it\\done\\issue-" "PowerShell examples do not use legacy done folder"
assert_not_contains_file "$SUB_COORDINATOR_PROMPT_FILE" ".run-with-it\\impl\\issue-" "PowerShell examples do not use legacy impl folder"
assert_not_contains_file "$SUB_COORDINATOR_PROMPT_FILE" ".run-with-it\\complexity\\issue-" "PowerShell examples do not use legacy complexity folder"

assert_contains_file "$IMPLEMENTER_PROMPT_FILE" '--pre-spawn-head "$env:ISSUE_BASE_SHA"' "PowerShell implementer preserves an empty pre-spawn-head argument"
assert_contains_file "$MODIFIER_PROMPT_FILE" '--pre-spawn-head "$env:REVIEW_HEAD_SHA"' "PowerShell modifier passes the modify-cycle pre-spawn head"
if [[ -n "$PS_CMD" ]]; then
  assert_prompt_preserves_empty_pre_spawn_head "$IMPLEMENTER_PROMPT_FILE" "impl"
  assert_prompt_preserves_empty_pre_spawn_head "$MODIFIER_PROMPT_FILE" "modify"
fi

assert_contains_file "$COORDINATOR_RULES_FILE" "run-with-it-dispatch.ps1" "coordinator rules document PowerShell dispatcher"
assert_contains_file "$COORDINATOR_RULES_FILE" "worker-watch.ps1" "coordinator rules document PowerShell watcher"
assert_contains_file "$ORCHESTRATOR_RULES_FILE" "run-with-it-pool.ps1" "orchestrator rules document PowerShell pool"

assert_contains_file "$INSTALL_PS1" "run-with-it-router.py" "install.ps1 includes shared router helper asset"
assert_contains_file "$INSTALL_PS1" "run-with-it-artifacts.py" "install.ps1 includes shared artifact helper asset"
assert_contains_file "$INSTALL_PS1" "run-with-it-dispatch.ps1" "install.ps1 includes PowerShell dispatcher asset"
assert_contains_file "$INSTALL_PS1" "run-with-it-pool.ps1" "install.ps1 includes PowerShell pool asset"
assert_contains_file "$INSTALL_PS1" "worker-watch.ps1" "install.ps1 includes PowerShell watcher asset"
assert_not_contains_file "$INSTALL_SH" "run-with-it-dispatch.ps1" "install.sh excludes PowerShell dispatcher asset"
assert_not_contains_file "$INSTALL_SH" "run-with-it-pool.ps1" "install.sh excludes PowerShell pool asset"
assert_not_contains_file "$INSTALL_SH" "worker-watch.ps1" "install.sh excludes PowerShell watcher asset"
assert_contains_file "$README_FILE" "run-with-it-dispatch.ps1" "README includes PowerShell dispatcher asset"
assert_contains_file "$README_FILE" "run-with-it-pool.ps1" "README includes PowerShell pool asset"
assert_contains_file "$README_FILE" "run-with-it-artifacts.py" "README includes shared artifact helper asset"
assert_contains_file "$README_FILE" "worker-watch.ps1" "README includes PowerShell watcher asset"

echo "PASS: run-with-it Windows routing documentation contract"
