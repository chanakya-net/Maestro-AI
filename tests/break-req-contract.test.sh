#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_FILE="${ROOT_DIR}/skills/break-req/SKILL.md"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_contains() {
  local needle="$1"
  local message="$2"

  if ! grep -Fq "$needle" "$SKILL_FILE"; then
    fail "${message} (missing: ${needle})"
  fi
}

[[ -f "$SKILL_FILE" ]] || fail "break-req skill file exists"

assert_contains 'This skill is requirements-only.' "documents requirements-only boundary"
assert_contains 'Never implement code, edit product files, create issues, publish to GitHub, run `create-git-issue`, run `run-with-it`, spawn implementation agents, or invoke external coding agents.' "forbids implementation and downstream workflow execution"
assert_contains 'The only file this skill may create or update is `technical_requirements.md`.' "limits writable output"
assert_contains 'Inform the user that requirements are ready and they can now run the `create-git-issue` skill.' "hands off to create-git-issue"
assert_contains 'Do not proceed beyond `technical_requirements.md`, even if the next step is obvious.' "requires hard stop after requirements"

echo "PASS: break-req requirements-only contract"
