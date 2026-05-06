#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_FILE="${ROOT_DIR}/skills/create-git-issue/SKILL.md"

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

[[ -f "$SKILL_FILE" ]] || fail "create-git-issue skill file exists"

assert_contains 'agent_routing:' "issue template includes routing block"
assert_contains 'complexity_hint:' "routing block includes complexity hint"
assert_contains 'complexity_hint: <quite-easy|easy|medium|medium-hard|complex|holy-fuck>' "routing hint uses canonical complexity levels"
assert_contains 'required_capability:' "routing block includes required capability"
assert_contains 'required_capability: <fast|balanced|advanced>' "routing hint uses registry capability bands"
assert_contains 'parallel_safe:' "routing block includes parallel safety"
assert_contains 'cost_preference:' "routing block includes cost preference"
assert_contains 'speed_preference:' "routing block includes speed preference"
assert_contains 'ownership_scope:' "routing block includes ownership scope"
assert_contains 'verification:' "routing block includes verification hints"
assert_contains 'must not assign concrete agent/model names' "documents no concrete agent/model assignment"
assert_contains 'run-with-it remains the final runtime routing authority' "documents run-with-it routing authority"
assert_contains 'If GitHub publishing is unavailable, append each approved slice issue to `issues.md` instead.' "documents local fallback for implementation issues"

echo "PASS: create-git-issue routing metadata documentation contract"
