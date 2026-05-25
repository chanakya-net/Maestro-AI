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
assert_contains 'After asking a targeted question, pause and wait for the user'"'"'s answer before proceeding.' "requires explicit wait for user answer"
assert_contains 'Do not continue investigation finalization or report generation while the question is pending.' "prevents proceeding while clarification is pending"
assert_contains 'If any unresolved unknown is answerable by the user (policy, UX intent, business rule, expected output), you must ask at least one targeted question before finalizing.' "requires mandatory human-answerable clarification"
assert_contains 'Before finalization, run a completion gate:' "requires completion gate before report finalization"
assert_contains 'If a question is unanswered (`pending`), stop and wait; do not write final artifacts yet.' "blocks final artifacts when question remains unanswered"
assert_contains 'If a targeted question is pending, respond with `Awaiting user answer: <question>` and stop.' "requires explicit pending-response behavior"
assert_contains 'Call Path Trace: ordered end-to-end execution path from trigger to symptom.' "requires call path trace in human report output"
assert_contains 'Inform the user the diagnosis package is ready and they can pass `debug_llm_context.md` to an implementation LLM.' "defines handoff guidance"
assert_contains 'Do not proceed beyond report generation, even if a fix is obvious.' "requires hard stop after outputs"

echo "PASS: help-me-debug diagnosis-only contract"