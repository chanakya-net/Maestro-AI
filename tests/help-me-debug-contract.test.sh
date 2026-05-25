#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_FILE="${ROOT_DIR}/skills/help-me-debug/SKILL.md"

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

[[ -f "$SKILL_FILE" ]] || fail "help-me-debug skill file exists"

assert_contains 'This skill is diagnosis-only.' "documents diagnosis-only boundary"
assert_contains 'Never implement code, edit product files, create issues, publish to GitHub, run `create-git-issue`, run `run-with-it`, spawn implementation agents, or invoke external coding agents.' "forbids implementation and downstream workflow execution"
assert_contains 'The only files this skill may create or update are `debug_human_report.md` and `debug_llm_context.md`.' "limits writable outputs"
assert_contains 'Walk this diagnosis decision tree in order:' "requires explicit diagnosis decision tree"
assert_contains 'If gaps remain, ask exactly one targeted question at a time.' "requires single-question clarification loop"
assert_contains 'If any unresolved unknown is answerable by the user (policy, UX intent, business rule, expected output), you must ask at least one targeted question before finalizing.' "requires mandatory human-answerable clarification"
assert_contains 'Before finalization, run a completion gate:' "requires completion gate before report finalization"
assert_contains 'Inform the user the diagnosis package is ready and they can pass `debug_llm_context.md` to an implementation LLM.' "defines handoff guidance"
assert_contains 'Do not proceed beyond report generation, even if a fix is obvious.' "requires hard stop after outputs"

echo "PASS: help-me-debug diagnosis-only contract"