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
assert_contains 'Complexity Scoring (8 dimensions, each 1-5)' "documents score heading"
assert_contains '9. blast radius' "documents blast-radius scoring dimension"
assert_contains 'Total score range: `8-45.' "documents score range"
assert_contains '`8-12` => `quite-easy`' "documents quite-easy mapping"
assert_contains '`13-17` => `easy`' "documents easy mapping"
assert_contains '`18-22` => `medium`' "documents medium mapping"
assert_contains '`23-27` => `medium-hard`' "documents medium-hard mapping"
assert_contains '`28-32` => `complex`' "documents complex mapping"
assert_contains '`33-45` => `holy-fuck`' "documents holy-fuck mapping"
assert_contains 'Hard Minimum Overrides' "documents hard minimum overrides"
assert_contains 'Model-First Selection' "documents model-first routing"
assert_contains 'score_to_weight' "documents score-to-weight table reference"
assert_contains 'lowest `complexity_weight`' "documents cost-efficient model selection strategy"
assert_contains 'automatic_routing = "last_resort_only"' "documents last-resort-only provider routing policy"
assert_contains 'Google/Gemini is last-resort-only for automatic routing.' "documents google/gemini last-resort automatic routing"
assert_contains 'Do not include Google/Gemini models in the normal candidate pool for any complexity band.' "documents google/gemini normal pool exclusion"
assert_contains 'Forced Gemini via `AGENT`, Gemini `MODEL`, or an `AGENT_ALLOWLIST` that only leaves Gemini is an explicit user constraint' "documents explicit gemini override exception"
assert_contains 'Do not spend `MAX_AGENT_FALLBACKS` on Google/Gemini last-resort attempts.' "documents separate fallback budget behavior"
assert_contains 'separate last-resort phase' "documents separate last-resort phase"
assert_contains 'separate last-resort Google/Gemini attempt' "documents separate diagnostics for gemini fallback"
assert_contains 'interchangeable' "documents codex/copilot interchangeable group"
assert_contains 'random' "documents random selection between interchangeable agents"
assert_contains 'Override Precedence (highest first)' "documents override precedence"
assert_contains 'AGENT_ALLOWLIST' "documents allowlist"
assert_contains 'AGENT_DENYLIST' "documents denylist"
assert_contains 'MAX_AGENT_FALLBACKS' "documents bounded fallback"
assert_contains 'ROUTE|agent=<agent>|model=<model>|complexity_level=<level>|complexity_score=<score>|target_weight=<min>-<max>|model_weight=<n>|price_tier=<tier>|fallback_budget=<n>|allowlist=<value>|denylist=<value>' "documents parseable route line with model-first fields"
assert_contains 'GUI_MODE="${GUI_MODE:-1}"' "documents GUI-safe runner mode for GUI-hosted agents"
assert_contains '"$ASSET_ROOT/run-agent.sh" --agent "$AGENT" --model "$MODEL" --unattended' "documents unified runner CLI invocation"
assert_contains 'Canonical Coordinator Contract (Required)' "preserves coordinator contract"
assert_contains 'You are the coordinator.' "documents coordinator role"
assert_contains 'Continue selecting and completing ready tasks until no ready work remains for the run.' "documents queue continuation"
assert_contains 'STATUS|type=spawn|batch=<batch-id>|agent=<agent-name>|issue=#<n>|phase=assigned|scope=<owned-paths>|eta=<rough-eta>' "documents spawn status line"
assert_contains 'At the end of every run, include a final task execution ledger.' "documents required final execution ledger"
assert_contains 'Line changes: `+<added>/-<deleted> (<total> total)`' "documents final ledger line change format"
assert_contains 'Input tokens: child-agent prompt/input token count when available' "documents input token ledger column"
assert_contains 'Output tokens: child-agent completion/output token count when available' "documents output token ledger column"
assert_contains 'Cache hit tokens: child-agent cache-hit token count when available' "documents cache-hit token ledger column"
assert_contains 'Telemetry source: telemetry origin, such as `runner-default`, provider-native, or coordinator-estimated' "documents telemetry source ledger column"
assert_contains 'Selection reasoning: one short sentence explaining why that agent/model was selected' "documents final ledger selection reasoning"
assert_contains 'Calculate line changes per task from the accepted diff for that task.' "documents per-task line change calculation"
assert_contains 'Preserve all existing parseable ledger fields and append token telemetry fields in this order for backward compatibility: `input_tokens`, `output_tokens`, `cache_hit_tokens`, `telemetry_source`.' "documents backward-compatible ledger extension"
assert_contains 'Normalize child-agent token telemetry from the selected runner' "documents child telemetry normalization"
assert_contains 'unknown' "documents unknown token fallback"
assert_contains 'Child-agent totals' "documents child-agent token summary section"
assert_contains 'Coordinator totals' "documents coordinator token summary section"
assert_contains 'Run totals' "documents run token summary section"
assert_contains 'STATUS|type=ledger|task=<task-id>|agent=<agent-name>|model=<model-id>|added=<n>|deleted=<n>|total=<n>|reason=<short-selection-reason>|input_tokens=<n-or-unknown>|output_tokens=<n-or-unknown>|cache_hit_tokens=<n-or-unknown>|telemetry_source=<source>' "documents parseable final ledger row with token telemetry"
assert_contains 'STATUS|type=summary|scope=child-agents|input_tokens=<n-or-unknown>|output_tokens=<n-or-unknown>|cache_hit_tokens=<n-or-unknown>' "documents child-agent aggregate token totals"
assert_contains 'STATUS|type=summary|scope=coordinator|input_tokens=<n-or-unknown>|output_tokens=<n-or-unknown>|cache_hit_tokens=<n-or-unknown>' "documents coordinator token totals"
assert_contains 'STATUS|type=summary|scope=run|input_tokens=<n-or-unknown>|output_tokens=<n-or-unknown>|cache_hit_tokens=<n-or-unknown>' "documents combined run token totals"
assert_contains 'Commit per issue by default.' "documents per-issue closure loop"
assert_contains '### Terminal Issue Comments' "documents terminal issue comment section"
assert_contains 'Post issue comments only for terminal outcomes: `completed`, `blocked`, or `failed-review`.' "documents terminal-only issue comments"
assert_contains 'Do not post this final template for non-terminal progress updates.' "documents non-terminal comment exclusion"
assert_contains 'Use the same markdown template for every terminal outcome, with this fixed section order:' "documents fixed terminal comment section order"
assert_contains '1. `## Status`' "documents Status section first"
assert_contains '2. `## Summary`' "documents Summary section second"
assert_contains '3. `## Verification`' "documents Verification section third"
assert_contains '4. `## Token Usage`' "documents Token Usage section fourth"
assert_contains '5. `## Notes`' "documents Notes section fifth"
assert_contains '## Status' "documents terminal comment status heading"
assert_contains '## Summary' "documents terminal comment summary heading"
assert_contains '## Verification' "documents terminal comment verification heading"
assert_contains '## Token Usage' "documents terminal comment token usage heading"
assert_contains '## Notes' "documents terminal comment notes heading"
assert_contains '<completed|blocked|failed-review>' "documents terminal status values"
assert_contains 'Token Usage` must report task-specific telemetry only.' "documents task-specific token usage requirement"
assert_contains 'render that value explicitly as `unknown`' "documents unknown token rendering in issue comments"
assert_contains '`Verification` must summarize the checks run for that issue and whether they passed, failed, or were blocked.' "documents issue verification comment requirement"

echo "PASS: run-with-it routing control plane documentation contract"
