#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_FILE="${ROOT_DIR}/skills/run-with-it/SKILL.md"
ORCHESTRATOR_RULES_FILE="${ROOT_DIR}/assets/main-orchestrator-rules.md"
COORDINATOR_RULES_FILE="${ROOT_DIR}/assets/coordinator-rules.md"
IMPLEMENTER_PROMPT_FILE="${ROOT_DIR}/assets/prompt.md"
REVIEW_PROMPT_FILE="${ROOT_DIR}/assets/review-prompt.md"
MODIFIER_PROMPT_FILE="${ROOT_DIR}/assets/modifier-prompt.md"
COMPLEXITY_PROMPT_FILE="${ROOT_DIR}/assets/complexity-prompt.md"

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
[[ -f "$IMPLEMENTER_PROMPT_FILE" ]] || fail "implementer prompt file exists"
[[ -f "$REVIEW_PROMPT_FILE" ]] || fail "review prompt file exists"
[[ -f "$MODIFIER_PROMPT_FILE" ]] || fail "modifier prompt file exists"
[[ -f "$COMPLEXITY_PROMPT_FILE" ]] || fail "complexity prompt file exists"

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

# Complexity (delegated to sub-coordinator; main references Sub-Coordinator Context File)
assert_contains 'passed through to Sub-Coordinators via context file' "documents sub-coordinator context structure"

# Routing overrides (passed through to sub-coordinator)
assert_contains 'AGENT_ALLOWLIST' "documents allowlist"
assert_contains 'AGENT_DENYLIST' "documents denylist"
assert_contains 'MAX_AGENT_FALLBACKS' "documents bounded fallback"

# Sub-coordinator dispatch
assert_contains 'sub-coordinator-prompt.md' "documents sub-coordinator prompt usage"
assert_contains 'Main Orchestrator Loop' "documents main loop"
assert_contains 'spawns' "documents sub-coordinator spawning"
assert_contains 'gh issue close' "documents issue closing"
assert_contains 'terminal comment' "documents terminal comment posting"

# Parallel (if documented)
assert_contains 'SUB_COORD_AGENT' "documents sub-coordinator agent override"
assert_contains 'SUB_COORD_MODEL' "documents sub-coordinator model override"

# State file schemas
assert_contains 'main-state.json' "documents main-state schema"
assert_contains '"status"' "documents status field in state schema"

# Resume
assert_contains 'Resume Flow' "documents resume flow"
assert_contains 'main-state.json' "documents resume state check"
assert_contains 'Sub-Coordinators are ephemeral' "documents sub-coordinator re-spawn on resume"

# Status messages (where documented)
assert_contains 'STATUS|type=sub-coord-spawn' "documents sub spawn status line"
assert_contains 'STATUS|type=sub-coord-complete' "documents report status line"
assert_contains 'RUN_WITH_IT_STATUS_FILE' "documents current status file"
assert_contains 'RUN_WITH_IT_EVENTS_LOG' "documents status event log"
assert_contains 'RUN_WITH_IT_LOG_FILE' "documents role-specific log file"
assert_contains 'RUN_WITH_IT_DONE_FILE' "documents worker done sentinel file"
assert_contains 'STATUS|type=agent-start' "documents live agent start status line"
assert_contains 'STATUS|type=agent-complete' "documents live agent complete status line"
assert_contains 'STATUS|type=worker-done' "documents worker done status line"
assert_contains 'STATUS|type=heartbeat|issue=<n>|role=' "documents live heartbeat status line"
assert_file_contains "$ORCHESTRATOR_RULES_FILE" '.run-with-it/status/current.txt' "orchestrator rules document current status file"
assert_file_contains "$ORCHESTRATOR_RULES_FILE" 'poll `current.txt`' "orchestrator rules document shell-only status polling"
assert_file_contains "$ORCHESTRATOR_RULES_FILE" '.run-with-it/main/main.log' "orchestrator rules document main log"
assert_file_contains "$ORCHESTRATOR_RULES_FILE" '.run-with-it/sub/sub-<n>.log' "orchestrator rules document sub log"
assert_file_contains "$ORCHESTRATOR_RULES_FILE" 'tail -n 2' "orchestrator rules limit sub log reads"
assert_file_contains "$COORDINATOR_RULES_FILE" '.run-with-it/<role>/' "coordinator rules document role log directories"
assert_file_contains "$COORDINATOR_RULES_FILE" 'tail -n ${WORKER_LOG_TAIL_LINES:-5}' "coordinator rules limit worker log reads"
assert_file_contains "$COORDINATOR_RULES_FILE" 'RUN_WITH_IT_DONE_FILE' "coordinator rules pass worker done file"
assert_file_contains "$COORDINATOR_RULES_FILE" '.run-with-it/done/' "coordinator rules document done directory"
assert_file_contains "$COORDINATOR_RULES_FILE" 'required output artifacts are valid' "coordinator rules gate phase transition on artifacts"
assert_file_contains "$IMPLEMENTER_PROMPT_FILE" 'RUN_WITH_IT_DONE_FILE' "implementer prompt documents done file"
assert_file_contains "$IMPLEMENTER_PROMPT_FILE" 'DONE|issue=' "implementer prompt documents done sentinel line"
assert_file_contains "$REVIEW_PROMPT_FILE" 'RUN_WITH_IT_DONE_FILE' "review prompt documents done file"
assert_file_contains "$REVIEW_PROMPT_FILE" 'after both JSON files are valid' "review prompt gates done file after artifacts"
assert_file_contains "$MODIFIER_PROMPT_FILE" 'RUN_WITH_IT_DONE_FILE' "modifier prompt documents done file"
assert_file_contains "$MODIFIER_PROMPT_FILE" 'DONE|issue=' "modifier prompt documents done sentinel line"
assert_file_contains "$COMPLEXITY_PROMPT_FILE" 'RUN_WITH_IT_DONE_FILE is runner-owned' "complexity prompt documents runner-owned done sentinel"

# File layout
assert_contains '.run-with-it/' "documents .run-with-it directory"
assert_contains 'main-state.json' "documents main-state in file layout"
assert_contains 'reports/' "documents reports directory"
assert_contains 'done/' "documents done sentinel directory"
assert_contains 'main/' "documents main log directory"
assert_contains 'sub/' "documents sub log directory"
assert_contains 'complexity/' "documents complexity log directory"
assert_contains 'impl/' "documents implementation log directory"
assert_contains 'review/' "documents review log directory"
assert_contains 'modify/' "documents modify log directory"
assert_contains 'reviews/' "documents reviews directory"
assert_not_present_in_active_files '\.run-with-it/logs' "legacy run-with-it logs directory removed from active docs/scripts"

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
