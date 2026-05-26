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

assert_contains "${dry_run_output}" "prompt.md" "PowerShell dry-run includes prompt asset"
assert_contains "${dry_run_output}" "sub-coordinator-prompt.md" "PowerShell dry-run includes sub-coordinator prompt asset"
assert_contains "${dry_run_output}" "merge-recovery-prompt.md" "PowerShell dry-run includes merge recovery prompt asset"
assert_contains "${dry_run_output}" "modifier-prompt.md" "PowerShell dry-run includes modifier prompt asset"
assert_contains "${dry_run_output}" "coordinator-rules.md" "PowerShell dry-run includes coordinator-rules asset"
assert_contains "${dry_run_output}" "run-with-it-state.py" "PowerShell dry-run includes shared state helper asset"
assert_contains "${dry_run_output}" "run-with-it-github-update.py" "PowerShell dry-run includes shared GitHub update helper asset"
assert_contains "${dry_run_output}" "run-agent.ps1" "PowerShell dry-run includes PowerShell runner asset"
assert_contains "${dry_run_output}" "run-with-it-dispatch.ps1" "PowerShell dry-run includes PowerShell dispatcher asset"
assert_contains "${dry_run_output}" "run-with-it-pool.ps1" "PowerShell dry-run includes PowerShell pool asset"
assert_contains "${dry_run_output}" "worker-watch.ps1" "PowerShell dry-run includes PowerShell watcher asset"
assert_contains "${dry_run_output}" "agent-registry.json" "PowerShell dry-run includes registry asset"
assert_not_contains "${dry_run_output}" "run-agent.sh" "PowerShell installer excludes Bash runner asset"
assert_not_contains "${dry_run_output}" "run-with-it-dispatch.sh" "PowerShell installer excludes Bash dispatcher asset"
assert_not_contains "${dry_run_output}" "run-with-it-pool.sh" "PowerShell installer excludes Bash pool asset"
assert_not_contains "${dry_run_output}" "worker-watch.sh" "PowerShell installer excludes Bash watcher asset"

echo "PASS: PowerShell installer dry-run asset contract"
