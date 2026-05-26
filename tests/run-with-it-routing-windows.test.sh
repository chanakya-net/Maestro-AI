#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_FILE="${ROOT_DIR}/skills/run-with-it/SKILL.md"
SUB_COORDINATOR_PROMPT_FILE="${ROOT_DIR}/assets/sub-coordinator-prompt.md"
COORDINATOR_RULES_FILE="${ROOT_DIR}/assets/coordinator-rules.md"
ORCHESTRATOR_RULES_FILE="${ROOT_DIR}/assets/main-orchestrator-rules.md"
README_FILE="${ROOT_DIR}/README.md"
INSTALL_SH="${ROOT_DIR}/install.sh"
INSTALL_PS1="${ROOT_DIR}/install.ps1"

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

assert_file "${ROOT_DIR}/assets/worker-watch.ps1" "PowerShell worker watcher asset exists"
assert_file "${ROOT_DIR}/assets/run-with-it-dispatch.ps1" "PowerShell dispatcher asset exists"
assert_file "${ROOT_DIR}/assets/run-with-it-pool.ps1" "PowerShell pool asset exists"

assert_contains_file "$SKILL_FILE" "run-with-it-dispatch.ps1" "skill documents PowerShell dispatcher"
assert_contains_file "$SKILL_FILE" "run-with-it-pool.ps1" "skill documents PowerShell pool"
assert_contains_file "$SKILL_FILE" "worker-watch.ps1" "skill documents PowerShell watcher"
assert_not_contains_file "$SKILL_FILE" "native PowerShell can install assets and run \`run-agent.ps1\`, but \`run-with-it\` orchestration requires Bash-only" "skill no longer blocks native PowerShell orchestration"

assert_contains_file "$SUB_COORDINATOR_PROMPT_FILE" "run-with-it-dispatch.ps1" "sub-coordinator prompt uses PowerShell dispatcher"
assert_contains_file "$SUB_COORDINATOR_PROMPT_FILE" "-StateFile \$WORKER_STATE_FILE" "PowerShell worker launch passes state file"
assert_contains_file "$SUB_COORDINATOR_PROMPT_FILE" ".run-with-it\\issues" "PowerShell examples use issue-scoped artifact folder"
assert_not_contains_file "$SUB_COORDINATOR_PROMPT_FILE" ".run-with-it\\done\\issue-" "PowerShell examples do not use legacy done folder"
assert_not_contains_file "$SUB_COORDINATOR_PROMPT_FILE" ".run-with-it\\impl\\issue-" "PowerShell examples do not use legacy impl folder"
assert_not_contains_file "$SUB_COORDINATOR_PROMPT_FILE" ".run-with-it\\complexity\\issue-" "PowerShell examples do not use legacy complexity folder"

assert_contains_file "$COORDINATOR_RULES_FILE" "run-with-it-dispatch.ps1" "coordinator rules document PowerShell dispatcher"
assert_contains_file "$COORDINATOR_RULES_FILE" "worker-watch.ps1" "coordinator rules document PowerShell watcher"
assert_contains_file "$ORCHESTRATOR_RULES_FILE" "run-with-it-pool.ps1" "orchestrator rules document PowerShell pool"

for file in "$INSTALL_SH" "$INSTALL_PS1" "$README_FILE"; do
  assert_contains_file "$file" "run-with-it-dispatch.ps1" "$(basename "$file") includes PowerShell dispatcher asset"
  assert_contains_file "$file" "run-with-it-pool.ps1" "$(basename "$file") includes PowerShell pool asset"
  assert_contains_file "$file" "worker-watch.ps1" "$(basename "$file") includes PowerShell watcher asset"
done

echo "PASS: run-with-it Windows routing documentation contract"
