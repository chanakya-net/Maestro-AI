#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER_PATH="${ROOT_DIR}/install.sh"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"
  if [[ "${haystack}" != *"${needle}"* ]]; then
    fail "${message} (missing: ${needle})"
  fi
}

assert_not_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"
  if [[ "${haystack}" == *"${needle}"* ]]; then
    fail "${message} (found forbidden: ${needle})"
  fi
}

[[ -f "${INSTALLER_PATH}" ]] || fail "install.sh exists"

dry_run_output="$(bash "${INSTALLER_PATH}" --dry-run)"

assert_contains "${dry_run_output}" "prompts/prompt.md" "dry-run includes prompts/prompt.md"
assert_contains "${dry_run_output}" "prompts/sub-coordinator-prompt.md" "dry-run includes prompts/sub-coordinator-prompt.md"
assert_contains "${dry_run_output}" "prompts/merge-recovery-prompt.md" "dry-run includes prompts/merge-recovery-prompt.md"
assert_contains "${dry_run_output}" "prompts/modifier-prompt.md" "dry-run includes prompts/modifier-prompt.md"
assert_contains "${dry_run_output}" "prompts/coordinator-rules.md" "dry-run includes prompts/coordinator-rules.md"
assert_contains "${dry_run_output}" "python/run-with-it-state.py" "dry-run includes python/run-with-it-state.py"
assert_contains "${dry_run_output}" "python/run-with-it-github-update.py" "dry-run includes python/run-with-it-github-update.py"
assert_contains "${dry_run_output}" "python/run-with-it-pr-body.py" "dry-run includes python/run-with-it-pr-body.py"
assert_contains "${dry_run_output}" "python/run-with-it-router.py" "dry-run includes python/run-with-it-router.py"
assert_contains "${dry_run_output}" "python/run-with-it-artifacts.py" "dry-run includes python/run-with-it-artifacts.py"
assert_contains "${dry_run_output}" "csharp/run-with-it-state.cs" "dry-run includes csharp/run-with-it-state.cs"
assert_contains "${dry_run_output}" "csharp/run-with-it-github-update.cs" "dry-run includes csharp/run-with-it-github-update.cs"
assert_contains "${dry_run_output}" "csharp/run-with-it-pr-body.cs" "dry-run includes csharp/run-with-it-pr-body.cs"
assert_contains "${dry_run_output}" "csharp/run-with-it-router.cs" "dry-run includes csharp/run-with-it-router.cs"
assert_contains "${dry_run_output}" "csharp/run-with-it-artifacts.cs" "dry-run includes csharp/run-with-it-artifacts.cs"
assert_contains "${dry_run_output}" "scripts/run-agent.sh" "dry-run includes scripts/run-agent.sh"
assert_contains "${dry_run_output}" "scripts/run-with-it-dispatch.sh" "dry-run includes scripts/run-with-it-dispatch.sh"
assert_contains "${dry_run_output}" "scripts/run-with-it-pool.sh" "dry-run includes scripts/run-with-it-pool.sh"
assert_contains "${dry_run_output}" "scripts/worker-watch.sh" "dry-run includes scripts/worker-watch.sh"
assert_contains "${dry_run_output}" "agent-registry.json" "dry-run includes agent-registry.json"
assert_not_contains "${dry_run_output}" "run-agent.ps1" "Bash installer excludes PowerShell runner asset"
assert_not_contains "${dry_run_output}" "run-with-it-dispatch.ps1" "Bash installer excludes PowerShell dispatcher asset"
assert_not_contains "${dry_run_output}" "run-with-it-pool.ps1" "Bash installer excludes PowerShell pool asset"
assert_not_contains "${dry_run_output}" "worker-watch.ps1" "Bash installer excludes PowerShell watcher asset"
assert_not_contains "${dry_run_output}" "powershell" "Bash installer excludes powershell directory"
assert_not_contains "${dry_run_output}" "run-codex.sh" "dry-run excludes legacy codex runner asset"
assert_not_contains "${dry_run_output}" "run-copilot.sh" "dry-run excludes legacy copilot runner asset"

# Ensure no old flat downloads into ASSETS_DEST directly
assert_not_contains "${dry_run_output}" "-o \${ASSETS_DEST}/prompt.md" "no flat prompt download"
assert_not_contains "${dry_run_output}" "-o \${ASSETS_DEST}/run-agent.sh" "no flat script download"
assert_not_contains "${dry_run_output}" "-o \${ASSETS_DEST}/run-with-it-state.py" "no flat python download"

echo "PASS: installer dry-run asset contract"
