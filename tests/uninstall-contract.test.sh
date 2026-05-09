#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNINSTALLER_PATH="${ROOT_DIR}/uninstall.sh"

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

[[ -f "${UNINSTALLER_PATH}" ]] || fail "uninstall.sh exists"

help_output="$(bash "${UNINSTALLER_PATH}" --help)"
assert_contains "${help_output}" "--only <target>" "help documents target filtering"
assert_contains "${help_output}" "assets, skills, claude, gemini, codex, copilot, antigravity" "help lists supported targets"

dry_run_output="$(bash "${UNINSTALLER_PATH}" --dry-run --only assets)"
assert_contains "${dry_run_output}" "rm -rf" "dry-run shows asset deletion command"
assert_contains "${dry_run_output}" ".ai-skill-collections" "dry-run removes the full default asset root"
assert_contains "${dry_run_output}" "Would remove (dry-run): assets" "dry-run summary includes assets"
assert_not_contains "${dry_run_output}" "npx skills remove" "asset-only dry-run skips npx-managed removal"

skills_root="$(mktemp -d)"
skills_dry_run_output="$(
  HOME="${skills_root}" bash "${UNINSTALLER_PATH}" --dry-run --only skills
)"
assert_not_contains "${skills_dry_run_output}" "Would remove (dry-run): skills" "skills dry-run skips absent skill directories"

mkdir -p "${skills_root}/.agents/skills/save-tokens"
skills_dry_run_output="$(
  HOME="${skills_root}" bash "${UNINSTALLER_PATH}" --dry-run --only skills
)"
assert_contains "${skills_dry_run_output}" "rm -rf ${skills_root}/.agents/skills/save-tokens" "skills dry-run removes installed skill directories"
assert_contains "${skills_dry_run_output}" "Would remove (dry-run): skills" "skills dry-run summary includes skills"

echo "PASS: uninstaller contract"
