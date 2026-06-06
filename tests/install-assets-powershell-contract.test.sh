#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER_PATH="${ROOT_DIR}/install.ps1"
PS_CMD="${PWSH:-}"
if [[ -z "$PS_CMD" ]]; then
  PS_CMD="$(command -v pwsh || command -v powershell.exe || command -v powershell || true)"
fi

if [[ -z "$PS_CMD" ]]; then
  echo "SKIP: PowerShell unavailable for install.ps1 asset contract"
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

[[ -f "${INSTALLER_PATH}" ]] || fail "install.ps1 exists"

dry_run_output="$("$PS_CMD" -NoProfile -File "${INSTALLER_PATH}" -DryRun)"

assert_contains "${dry_run_output}" "prompts\\prompt.md" "PowerShell dry-run includes prompt asset in prompts folder"
assert_contains "${dry_run_output}" "prompts\\sub-coordinator-prompt.md" "PowerShell dry-run includes sub-coordinator prompt asset in prompts folder"
assert_contains "${dry_run_output}" "prompts\\merge-recovery-prompt.md" "PowerShell dry-run includes merge recovery prompt asset in prompts folder"
assert_contains "${dry_run_output}" "prompts\\modifier-prompt.md" "PowerShell dry-run includes modifier prompt asset in prompts folder"
assert_contains "${dry_run_output}" "prompts\\coordinator-rules.md" "PowerShell dry-run includes coordinator-rules asset in prompts folder"
assert_contains "${dry_run_output}" "python\\run-with-it-state.py" "PowerShell dry-run includes shared state helper asset in python folder"
assert_contains "${dry_run_output}" "python\\run-with-it-github-update.py" "PowerShell dry-run includes shared GitHub update helper asset in python folder"
assert_contains "${dry_run_output}" "python\\run-with-it-pr-body.py" "PowerShell dry-run includes shared PR body helper asset in python folder"
assert_contains "${dry_run_output}" "python\\run-with-it-router.py" "PowerShell dry-run includes shared router helper asset in python folder"
assert_contains "${dry_run_output}" "python\\run-with-it-artifacts.py" "PowerShell dry-run includes shared artifact helper asset in python folder"
assert_contains "${dry_run_output}" "csharp\\run-with-it-state.cs" "PowerShell dry-run includes shared state C# helper asset in csharp folder"
assert_contains "${dry_run_output}" "csharp\\run-with-it-github-update.cs" "PowerShell dry-run includes shared GitHub update C# helper asset in csharp folder"
assert_contains "${dry_run_output}" "csharp\\run-with-it-pr-body.cs" "PowerShell dry-run includes shared PR body C# helper asset in csharp folder"
assert_contains "${dry_run_output}" "csharp\\run-with-it-router.cs" "PowerShell dry-run includes shared router C# helper asset in csharp folder"
assert_contains "${dry_run_output}" "csharp\\run-with-it-artifacts.cs" "PowerShell dry-run includes shared artifact C# helper asset in csharp folder"
assert_contains "${dry_run_output}" "powershell\\run-agent.ps1" "PowerShell dry-run includes PowerShell runner asset in powershell folder"
assert_contains "${dry_run_output}" "powershell\\run-with-it-dispatch.ps1" "PowerShell dry-run includes PowerShell dispatcher asset in powershell folder"
assert_contains "${dry_run_output}" "powershell\\run-with-it-pool.ps1" "PowerShell dry-run includes PowerShell pool asset in powershell folder"
assert_contains "${dry_run_output}" "powershell\\worker-watch.ps1" "PowerShell dry-run includes PowerShell watcher asset in powershell folder"
assert_contains "${dry_run_output}" "agent-registry.json" "PowerShell dry-run includes registry asset in root assets folder"
assert_not_contains "${dry_run_output}" "run-agent.sh" "PowerShell installer excludes Bash runner asset"
assert_not_contains "${dry_run_output}" "run-with-it-dispatch.sh" "PowerShell installer excludes Bash dispatcher asset"
assert_not_contains "${dry_run_output}" "run-with-it-pool.sh" "PowerShell installer excludes Bash pool asset"
assert_not_contains "${dry_run_output}" "worker-watch.sh" "PowerShell installer excludes Bash watcher asset"
assert_not_contains "${dry_run_output}" "scripts" "PowerShell installer excludes scripts folder"

# Ensure no old flat downloads into ASSETS_DEST directly
assert_not_contains "${dry_run_output}" "-OutFile \$ASSETS_DEST\\prompt.md" "no flat prompt download"
assert_not_contains "${dry_run_output}" "-OutFile \$ASSETS_DEST\\run-agent.ps1" "no flat script download"
assert_not_contains "${dry_run_output}" "-OutFile \$ASSETS_DEST\\run-with-it-state.py" "no flat python download"

echo "PASS: PowerShell installer dry-run asset contract"
