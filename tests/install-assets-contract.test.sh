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

assert_contains "${dry_run_output}" "prompt.md" "dry-run includes prompt asset"
assert_contains "${dry_run_output}" "sub-coordinator-prompt.md" "dry-run includes sub-coordinator prompt asset"
assert_contains "${dry_run_output}" "artifact-recovery-prompt.md" "dry-run includes artifact recovery prompt asset"
assert_contains "${dry_run_output}" "merge-recovery-prompt.md" "dry-run includes merge recovery prompt asset"
assert_contains "${dry_run_output}" "modifier-prompt.md" "dry-run includes modifier prompt asset"
assert_contains "${dry_run_output}" "plan-prompt.md" "dry-run includes plan prompt asset"
assert_contains "${dry_run_output}" "coordinator-rules.md" "dry-run includes coordinator-rules asset"
assert_contains "${dry_run_output}" "run-with-it-state.py" "dry-run includes shared state helper asset"
assert_contains "${dry_run_output}" "run-with-it-github-update.py" "dry-run includes shared GitHub update helper asset"
assert_contains "${dry_run_output}" "run-with-it-pr-body.py" "dry-run includes shared PR body helper asset"
assert_contains "${dry_run_output}" "run-with-it-router.py" "dry-run includes shared router helper asset"
assert_contains "${dry_run_output}" "run-with-it-artifacts.py" "dry-run includes shared artifact helper asset"
assert_contains "${dry_run_output}" "run-agent.sh" "dry-run includes unified runner asset"
assert_contains "${dry_run_output}" "run-with-it-dispatch.sh" "dry-run includes shared run-with-it dispatcher asset"
assert_contains "${dry_run_output}" "run-with-it-pool.sh" "dry-run includes shared run-with-it pool asset"
assert_contains "${dry_run_output}" "run-with-it-watch.sh" "dry-run includes bounded status watcher asset"
assert_contains "${dry_run_output}" "run-with-it-stop.sh" "dry-run includes run shutdown helper asset"
assert_contains "${dry_run_output}" "worker-watch.sh" "dry-run includes worker watcher asset"
assert_contains "${dry_run_output}" "agent-registry.json" "dry-run includes registry asset"
assert_not_contains "${dry_run_output}" "run-agent.ps1" "Bash installer excludes PowerShell runner asset"
assert_not_contains "${dry_run_output}" "run-with-it-dispatch.ps1" "Bash installer excludes PowerShell dispatcher asset"
assert_not_contains "${dry_run_output}" "run-with-it-pool.ps1" "Bash installer excludes PowerShell pool asset"
assert_not_contains "${dry_run_output}" "run-with-it-watch.ps1" "Bash installer excludes PowerShell status watcher asset"
assert_not_contains "${dry_run_output}" "run-with-it-stop.ps1" "Bash installer excludes PowerShell shutdown helper asset"
assert_not_contains "${dry_run_output}" "worker-watch.ps1" "Bash installer excludes PowerShell watcher asset"
assert_not_contains "${dry_run_output}" "run-codex.sh" "dry-run excludes legacy codex runner asset"
assert_not_contains "${dry_run_output}" "run-copilot.sh" "dry-run excludes legacy copilot runner asset"

echo "PASS: installer dry-run asset contract"
