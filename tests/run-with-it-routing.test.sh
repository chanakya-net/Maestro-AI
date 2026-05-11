#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_FILE="${ROOT_DIR}/skills/run-with-it/SKILL.md"
ORCHESTRATOR_RULES_FILE="${ROOT_DIR}/assets/main-orchestrator-rules.md"
COORDINATOR_RULES_FILE="${ROOT_DIR}/assets/coordinator-rules.md"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_contains() {
  local needle="$1"
  local message="$2"
  if ! grep -Fq -- "$needle" "$SKILL_FILE"; then
    fail "${message} (missing: ${needle})"
  fi
}

assert_not_contains() {
  local needle="$1"
  local message="$2"
  if grep -Fq -- "$needle" "$SKILL_FILE"; then
    fail "${message} (found forbidden: ${needle})"
  fi
}

assert_file_contains() {
  local file="$1"
  local needle="$2"
  local message="$3"
  if ! grep -Fq -- "$needle" "$file"; then
    fail "${message} (missing: ${needle})"
  fi
}

assert_not_present_in_active_files() {
  local needle="$1"
  local message="$2"
  local output
  local search_paths=("${ROOT_DIR}/README.md" "${ROOT_DIR}/skills" "${ROOT_DIR}/assets" "${ROOT_DIR}/install.sh")
  if [[ -d "${ROOT_DIR}/docs" ]]; then
    search_paths+=("${ROOT_DIR}/docs")
  fi
  output="$(rg -n --glob '*.md' --glob '*.sh' "${needle}" "${search_paths[@]}" || true)"
  if [[ -n "${output}" ]]; then
    fail "${message} (found forbidden references: ${output})"
  fi
}

[[ -f "$SKILL_FILE" ]] || fail "run-with-it skill file exists"
[[ -f "$ORCHESTRATOR_RULES_FILE" ]] || fail "main-orchestrator-rules file exists"
[[ -f "$COORDINATOR_RULES_FILE" ]] || fail "coordinator-rules file exists"

assert_not_contains 'run-codex.sh' "legacy codex runner references removed"
assert_not_contains 'run-copilot.sh' "legacy copilot runner references removed"
assert_not_present_in_active_files 'run-codex\.sh' "legacy codex runner references removed from active docs/scripts"
assert_not_present_in_active_files 'run-copilot\.sh' "legacy copilot runner references removed from active docs/scripts"
assert_not_present_in_active_files 'run-claude\.sh' "legacy claude runner references removed from active docs/scripts"
assert_not_present_in_active_files 'run-gemini\.sh' "legacy gemini runner references removed from active docs/scripts"
assert_not_present_in_active_files 'run-opencode\.sh' "legacy opencode runner references removed from active docs/scripts"

# Asset discovery
assert_contains 'sub-coordinator-prompt.md' "asset discovery includes sub-coordinator-prompt.md"
assert_contains 'prompt.md' "asset discovery includes prompt.md"
assert_contains 'modifier-prompt.md' "asset discovery includes modifier-prompt.md"
assert_contains 'run-agent.sh' "asset discovery includes run-agent.sh"
assert_contains 'agent-registry.json' "asset discovery includes agent-registry.json"
assert_contains 'main-orchestrator-rules.md' "asset discovery includes main-orchestrator-rules.md"

# Architecture
assert_contains 'Main Orchestrator' "documents main orchestrator section"
assert_contains 'Sub-Coordinator' "documents sub-coordinator architecture"
assert_contains 'Two-layer' "documents two-layer architecture"
assert_contains 'bounded context window' "documents bounded context window"
assert_contains 'main-state.json' "documents main-state.json state file"
assert_contains 'Never load sub-coordinator log files' "documents no-load policy"

# Complexity mapping
assert_contains 'quite-easy' "documents quite-easy mapping"
assert_contains 'easy' "documents easy mapping"
assert_contains 'medium' "documents medium mapping"
assert_contains 'medium-hard' "documents medium-hard mapping"
assert_contains 'complex' "documents complex mapping"
assert_contains 'holy-fuck' "documents holy-fuck mapping"
assert_contains 'Hard Minimum Overrides' "documents hard minimum overrides"

# Routing rules
assert_contains 'complexity_weight ASC, price_tier ASC' "documents cost-efficient model selection strategy"
assert_contains 'automatic_routing = "easy_only"' "documents easy-only Gemini routing policy"
assert_contains 'Google/Gemini is not a last-resort provider.' "documents Gemini is not last-resort"
assert_contains 'interchangeable' "documents codex/copilot interchangeable group"
assert_contains 'random' "documents random selection between interchangeable agents"
assert_contains 'Override Precedence (highest first)' "documents override precedence"
assert_contains 'AGENT_ALLOWLIST' "documents allowlist"
assert_contains 'AGENT_DENYLIST' "documents denylist"
assert_contains 'MAX_AGENT_FALLBACKS' "documents bounded fallback"

# Sub-coordinator dispatch
assert_contains 'Spawning Sub-Coordinators' "documents sub-coordinator spawning section"
assert_contains 'sub-coordinator-prompt.md' "documents sub-coordinator prompt usage"
assert_contains 'Main Orchestrator Loop' "documents main loop"
assert_contains 'Read Report' "documents report reading step"
assert_contains 'Post terminal comment' "documents terminal comment posting"
assert_contains 'gh issue close' "documents issue closing"

# Parallel (if documented)
assert_contains 'SUB_COORD_AGENT' "documents sub-coordinator agent override"
assert_contains 'SUB_COORD_MODEL' "documents sub-coordinator model override"

# State file schemas
assert_contains 'main-state.json' "documents main-state schema"
assert_contains 'Sub-Coordinator Context File' "documents sub-coordinator context schema"
assert_contains '"status": "completed' "documents status field in state schema"

# Resume
assert_contains 'Resume Flow' "documents resume flow"
assert_contains 'main-state.json' "documents resume state check"
assert_contains 'Re-spawn' "documents sub-coordinator re-spawn on resume"

# Status messages (where documented)
assert_contains 'STATUS|type=spawn|role=sub' "documents sub spawn status line"
assert_contains 'STATUS|type=report' "documents report status line"

# File layout
assert_contains '.run-with-it/' "documents .run-with-it directory"
assert_contains 'main-state.json' "documents main-state in file layout"
assert_contains 'reports/' "documents reports directory"
assert_contains 'logs/' "documents logs directory"
assert_contains 'reviews/' "documents reviews directory"

# Critical rules
assert_contains 'Never implement work directly in this session' "documents no-impl rule"
assert_contains 'Never run tests' "documents no-test rule"

# Cleanup
assert_contains 'Cleanup' "documents cleanup section"

# Orchestrator rules file
assert_file_contains "$ORCHESTRATOR_RULES_FILE" 'Main Orchestrator' "orchestrator rules reference orchestrator"
assert_file_contains "$ORCHESTRATOR_RULES_FILE" 'Never implement work directly' "orchestrator rules preserve no-impl"
assert_file_contains "$ORCHESTRATOR_RULES_FILE" 'main-state.json' "orchestrator rules reference main-state"
assert_file_contains "$ORCHESTRATOR_RULES_FILE" 'compact report' "orchestrator rules reference report files"
assert_file_contains "$ORCHESTRATOR_RULES_FILE" 'Re-read' "orchestrator rules enforce re-read"

echo "PASS: run-with-it main orchestrator documentation contract"