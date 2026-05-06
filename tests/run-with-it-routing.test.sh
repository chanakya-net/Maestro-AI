#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_FILE="${ROOT_DIR}/skills/run-with-it/SKILL.md"

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

assert_not_contains() {
  local needle="$1"
  local message="$2"
  if grep -Fq "$needle" "$SKILL_FILE"; then
    fail "${message} (found forbidden: ${needle})"
  fi
}

assert_not_present_in_active_files() {
  local needle="$1"
  local message="$2"
  local output

  output="$(rg -n --glob '*.md' --glob '*.sh' "${needle}" "${ROOT_DIR}/README.md" "${ROOT_DIR}/docs" "${ROOT_DIR}/skills" "${ROOT_DIR}/assets" "${ROOT_DIR}/install.sh" || true)"
  if [[ -n "${output}" ]]; then
    fail "${message} (found forbidden references: ${output})"
  fi
}

[[ -f "$SKILL_FILE" ]] || fail "run-with-it skill file exists"

assert_not_contains 'run-codex.sh' "legacy codex runner references removed"
assert_not_contains 'run-copilot.sh' "legacy copilot runner references removed"
assert_not_present_in_active_files 'run-codex\.sh' "legacy codex runner references removed from active docs/scripts"
assert_not_present_in_active_files 'run-copilot\.sh' "legacy copilot runner references removed from active docs/scripts"
assert_not_present_in_active_files 'run-claude\.sh' "legacy claude runner references removed from active docs/scripts"
assert_not_present_in_active_files 'run-gemini\.sh' "legacy gemini runner references removed from active docs/scripts"
assert_not_present_in_active_files 'run-opencode\.sh' "legacy opencode runner references removed from active docs/scripts"

assert_contains 'prompt.md' "asset discovery includes prompt.md"
assert_contains 'run-agent.sh' "asset discovery includes run-agent.sh"
assert_contains 'agent-registry.json' "asset discovery includes agent-registry.json"

assert_contains 'can coordinate multiple safe parallel agents' "description documents multi-agent capability"
assert_contains 'MAX_PARALLEL_AGENTS' "documents multi-agent concurrency bound"
assert_contains 'ALLOW_PARALLEL_AGENTS' "documents multi-agent enable switch"
assert_contains 'Multi-Agent Capability' "documents explicit multi-agent section"
assert_contains '`run-with-it` may run a single issue or coordinate a batch of multiple agents.' "documents single-or-batch behavior"
assert_contains 'Complexity Scoring (8 dimensions, each 1-5)' "documents 8-dimension scoring"
assert_contains 'Total score range: `8-40`.' "documents score range"
assert_contains '`8-12` => `quite-easy`' "documents quite-easy mapping"
assert_contains '`13-17` => `easy`' "documents easy mapping"
assert_contains '`18-22` => `medium`' "documents medium mapping"
assert_contains '`23-27` => `medium-hard`' "documents medium-hard mapping"
assert_contains '`28-32` => `complex`' "documents complex mapping"
assert_contains '`33-40` => `holy-fuck`' "documents holy-fuck mapping"
assert_contains 'Hard Minimum Overrides' "documents hard minimum overrides"
assert_contains 'Model-First Selection' "documents model-first routing"
assert_contains 'score_to_weight' "documents score-to-weight table reference"
assert_contains 'lowest `complexity_weight`' "documents cost-efficient model selection strategy"
assert_contains 'interchangeable' "documents codex/copilot interchangeable group"
assert_contains 'random' "documents random selection between interchangeable agents"
assert_contains 'Override Precedence (highest first)' "documents override precedence"
assert_contains 'AGENT_ALLOWLIST' "documents allowlist"
assert_contains 'AGENT_DENYLIST' "documents denylist"
assert_contains 'MAX_AGENT_FALLBACKS' "documents bounded fallback"
assert_contains 'ROUTE|agent=<agent>|model=<model>|complexity_level=<level>|complexity_score=<score>|target_weight=<min>-<max>|model_weight=<n>|price_tier=<tier>|fallback_budget=<n>|allowlist=<value>|denylist=<value>' "documents parseable route line with model-first fields"
assert_contains '"$ASSET_ROOT/run-agent.sh" --agent "$AGENT" --model "$MODEL" --unattended' "documents unified runner CLI invocation"
assert_contains 'Canonical Coordinator Contract (Required)' "preserves coordinator contract"
assert_contains 'You are the coordinator.' "documents coordinator role"
assert_contains 'Continue selecting and completing ready tasks until no ready work remains for the run.' "documents queue continuation"
assert_contains 'STATUS|type=spawn|batch=<batch-id>|agent=<agent-name>|issue=#<n>|phase=assigned|scope=<owned-paths>|eta=<rough-eta>' "documents spawn status line"
assert_contains 'Commit per issue by default.' "documents per-issue closure loop"

echo "PASS: run-with-it routing control plane documentation contract"
