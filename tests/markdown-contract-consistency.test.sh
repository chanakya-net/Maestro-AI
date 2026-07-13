#!/usr/bin/env bash
# Documentation contract test: asserts the skill/asset Markdown matches the
# behavior enforced by the runtime scripts and validators, and that the
# compaction-safe twin files stay synchronized on key tokens.
# Twins: skills/run-with-it/SKILL.md <-> assets/main-orchestrator-rules.md
#        assets/sub-coordinator-prompt.md <-> assets/coordinator-rules.md
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
FAILURES=0

fail() { printf 'FAIL: %s\n' "$1" >&2; FAILURES=$((FAILURES + 1)); }

assert_contains() {  # file token message
  grep -Fq -- "$2" "$ROOT_DIR/$1" || fail "$1: $3"
}

assert_not_contains() {  # file token message
  if grep -Fq -- "$2" "$ROOT_DIR/$1"; then fail "$1: $3"; fi
}

assert_twins_contain() {  # fileA fileB token message
  assert_contains "$1" "$3" "$4 (missing in $1)"
  assert_contains "$2" "$3" "$4 (missing in $2)"
}

# --- Verified no-op contract (run-with-it-artifacts.py accepts no_op:true) ---

assert_contains "assets/sub-coordinator-prompt.md" '"no_op": true' \
  "sub-coordinator must document no-op artifact acceptance"
assert_contains "assets/coordinator-rules.md" 'verified no-op' \
  "coordinator-rules must carve out the verified no-op exception"
assert_contains "assets/prompt.md" 'or the verified no-op result artifact' \
  "implementer done-file gates must allow the verified no-op"
assert_contains "assets/modifier-prompt.md" 'or the verified no-op result artifact' \
  "modifier done-file gates must allow the verified no-op"

# --- Iteration limit (no script reads MAX_ITERATIONS; cap is prompt-enforced) ---

assert_contains "skills/run-with-it/SKILL.md" 'hardcoded to 8 cycles' \
  "MAX_ITERATIONS row must state the 8-cycle cap is hardcoded and the variable inactive"

# --- Complexity fallback band (medium-hard, score=25) ---

assert_not_contains "assets/coordinator-rules.md" 'default to medium and continue' \
  "complexity fallback must be medium-hard, not medium"
assert_contains "assets/coordinator-rules.md" 'medium-hard' \
  "coordinator-rules must state the medium-hard fallback"
assert_contains "assets/sub-coordinator-prompt.md" 'fallback=medium-hard' \
  "sub-coordinator STATUS contract keeps fallback=medium-hard"

# --- Stall threshold (snippets pass 300 explicitly; 600 is the env fallback) ---

assert_contains "assets/coordinator-rules.md" 'WORKER_STALL_SECONDS=300' \
  "coordinator-rules must document the effective 300s snippet value"

# --- Terminal sets: orchestrator issue statuses include failed-merge... ---

assert_twins_contain "skills/run-with-it/SKILL.md" "assets/main-orchestrator-rules.md" \
  'completed / failed-review / failed-merge / blocked' \
  "orchestrator terminal enumerations must include failed-merge"
assert_not_contains "skills/run-with-it/SKILL.md" 'completed / failed-review / blocked' \
  "stale three-status orchestrator enumeration must be gone"
assert_not_contains "assets/main-orchestrator-rules.md" 'completed / failed-review / blocked' \
  "stale three-status orchestrator enumeration must be gone"
assert_not_contains "assets/main-orchestrator-rules.md" 'completed/failed-review/blocked' \
  "stale compact three-status enumeration must be gone"

# --- ...while sub-coordinator REPORT outcomes use merge_failed, never failed-merge ---

assert_contains "assets/sub-coordinator-prompt.md" 'completed | failed-review | merge_failed | blocked' \
  "Appendix E outcome enum must include merge_failed"
assert_not_contains "assets/sub-coordinator-prompt.md" 'failed-merge' \
  "failed-merge is an orchestrator issue status and must not leak into sub-coordinator outcomes"

# --- Auto-fail stalled roles (script default: complexity,impl,modify,plan) ---

assert_contains "assets/sub-coordinator-prompt.md" 'complexity,impl,modify,plan' \
  "auto-fail role default must match run-with-it-dispatch.sh"
assert_twins_contain "skills/run-with-it/SKILL.md" "assets/coordinator-rules.md" \
  'complexity,impl,modify,plan' "auto-fail role default must match in twins"
assert_not_contains "assets/sub-coordinator-prompt.md" '(Bash default: `complexity`)' \
  "stale single-role auto-fail default must be gone"

# --- Worker-watch ownership (pool runner/dispatcher own it, not the orchestrator) ---

assert_contains "assets/main-orchestrator-rules.md" 'never runs worker-watch itself' \
  "Main Orchestrator must not be told to run worker-watch directly"

# --- Plan template must satisfy valid_plan_payload (non-empty slices) ---

assert_not_contains "assets/plan-prompt.md" '"slices": [],' \
  "Bash plan example must not produce an invalid empty slices array"
assert_not_contains "assets/plan-prompt.md" 'slices = @()' \
  "PowerShell plan example must not produce an invalid empty slices array"

# --- Review schema includes every required coverage row ---

assert_contains "assets/review-prompt.md" 'plan_conformance | maintainability' \
  "review schema area enum must include plan_conformance"

# --- Complexity prompt acceptance checks describe the file truthfully ---

assert_not_contains "assets/complexity-prompt.md" 'Contains CodeGraph tool instructions' \
  "acceptance check must not claim CodeGraph instructions exist"

# --- Merge-recovery bootstrap copy-paste leftovers ---

assert_not_contains "assets/merge-recovery-prompt.md" 'tdd-implementation' \
  "merge recovery never bootstraps tdd-implementation"
assert_not_contains "assets/merge-recovery-prompt.md" 'both activations' \
  "merge recovery bootstraps a single skill"

# --- Modifier verification scoped to change-caused failures (implementer policy untouched) ---

assert_contains "assets/modifier-prompt.md" 'caused by the reviewed change before reporting completion' \
  "modifier verification must be scoped to change-caused failures"
assert_contains "assets/prompt.md" 'If tests outside your assigned scope are failing, fix them' \
  "implementer out-of-scope policy must remain unchanged"

# --- Reviewer band bump owned by the router; manual table scoped to fallback ---

assert_contains "assets/sub-coordinator-prompt.md" 'REVIEW_BUMP' \
  "route-helper inputs must name the router's internal review bump"
assert_contains "assets/sub-coordinator-prompt.md" 'prompt fallback router only' \
  "manual reviewer band table must be scoped to the fallback router"

# --- Review-skip keys documented in the compact report schema ---

assert_contains "assets/sub-coordinator-prompt.md" '"review_skipped": false' \
  "Appendix E schema must include the review_skipped key"
assert_contains "skills/run-with-it/SKILL.md" 'Review: skipped' \
  "Appendix D must define the terminal-comment line for skipped review"

# --- Skill isolation permits the governing-prompt bootstrap ---

assert_contains "skills/save-tokens/SKILL.md" 'governing prompt' \
  "save-tokens isolation must allow the worker-prompt bootstrap"
assert_contains "skills/tdd-implementation/SKILL.md" 'governing prompt' \
  "tdd-implementation isolation must allow the worker-prompt bootstrap"

# --- No-Git support claims scoped to what actually works without git ---

assert_contains "skills/run-with-it/SKILL.md" 'asset discovery and local-issue intake' \
  "no-git support claim must be scoped; branches/worktrees/merges require git"

# --- Stale references and typos ---

assert_not_contains "skills/run-with-it/SKILL.md" 'Preflight Check 14' \
  "stale preflight cross-reference must be corrected"
assert_not_contains "skills/create-git-issue/SKILL.md" 'outsise' "typo: outsise"
assert_not_contains "skills/create-git-issue/SKILL.md" 'requirment in detils' "typo: requirment in detils"

# --- Review skip still merges back to the shared feature branch ---

assert_twins_contain "assets/sub-coordinator-prompt.md" "assets/coordinator-rules.md" \
  'or a Step 0 review skip' \
  "merge trigger must cover the review-skip path"

# --- Modifier no-op: dedicated variant, correct pre-spawn head ---

assert_contains "assets/modifier-prompt.md" 'Verified no-op variant' \
  "modifier must have a no-op payload variant (git show NONE breaks the builder)"
assert_contains "assets/modifier-prompt.md" '--pre-spawn-head "${REVIEW_HEAD_SHA:-}"' \
  "modifier validation must use the modify-cycle pre-spawn head"
assert_not_contains "assets/modifier-prompt.md" '--pre-spawn-head "${ISSUE_BASE_SHA:-}"' \
  "modifier must not validate no-ops against the issue baseline"

# --- Verification exception uses a validator-supported representation ---

assert_contains "assets/modifier-prompt.md" 'applies only to failures outside those required commands' \
  "pre-existing-failure path must not conflict with the passed=true validator requirement"

# --- Stall env fallback scoped by platform (Bash 600, PowerShell 300) ---

assert_contains "assets/coordinator-rules.md" '600 on Bash, 300 on PowerShell' \
  "stall env fallback must be scoped by platform"

# --- failed-merge in the remaining enumerations (Resume Flow, final summary) ---

assert_contains "skills/run-with-it/SKILL.md" 'Completed / failed-review / failed-merge / blocked counts' \
  "final summary counts must include failed-merge"
assert_contains "skills/run-with-it/SKILL.md" '`"failed-merge"`, or `"blocked"`' \
  "Resume Flow terminal-skip enumeration must include failed-merge"

# --- Task 7A: baseline confirm anchored to the issue worktree ---

assert_contains "assets/sub-coordinator-prompt.md" 'ISSUE_BASE_SHA:-$(git -C "$ISSUE_WORKTREE_PATH" rev-parse HEAD)' \
  "baseline confirm must read the issue worktree, not ambient HEAD"

# --- Task 7B: review-skip gate rows must be mutually exclusive ---

assert_not_contains "assets/sub-coordinator-prompt.md" 'or `files_changed` 2–4' \
  "overlapping gray-zone file-count range must be gone"

# --- Result ---

if [ "$FAILURES" -gt 0 ]; then
  printf '%d markdown contract failure(s)\n' "$FAILURES" >&2
  exit 1
fi
printf 'markdown contract consistency: OK\n'
