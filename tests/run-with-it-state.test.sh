#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_HELPER="${ROOT_DIR}/assets/run-with-it-state.py"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_contains() {
  local haystack="$1" needle="$2" message="$3"
  case "${haystack}" in
    *"${needle}"*) : ;;
    *) fail "${message} (missing: ${needle} in: ${haystack})" ;;
  esac
}

assert_eq() {
  [[ "$1" == "$2" ]] || fail "$3 (expected: $2, got: $1)"
}

WORK_DIR="$(mktemp -d)"
cleanup() { rm -rf "${WORK_DIR}"; }
trap cleanup EXIT

STATE_FILE="${WORK_DIR}/main-state.json"
ISSUE_DIR_700="${WORK_DIR}/issues/700"
mkdir -p "${ISSUE_DIR_700}"
printf '{"outcome":"blocked"}\n' > "${ISSUE_DIR_700}/report.json"
printf 'DONE\n' > "${ISSUE_DIR_700}/sub-coordinator.done"

cat > "${STATE_FILE}" <<JSON
{
  "schema_version": 4,
  "execution_plan": {"topo_order": [600, 701, 700, 633], "parallel_jobs": 4},
  "issue_registry": {
    "600": {"status": "completed", "deps": []},
    "701": {"status": "in_progress", "deps": [], "issue_dir": "${WORK_DIR}/issues/701"},
    "700": {"status": "blocked", "deps": [701], "blocking_reasons": ["blocked-by-issue-701"], "sub_coord_recovery_attempts": 3,
            "issue_dir": "${ISSUE_DIR_700}", "report_file": "${ISSUE_DIR_700}/report.json"},
    "633": {"status": "pending", "deps": [700]}
  }
}
JSON

# --- status-board: compact per-issue stage view ---
board="$(python3 "${STATE_HELPER}" status-board --oneline --state-file "${STATE_FILE}")"
assert_contains "${board}" "#600 done" "board shows completed issue as done"
assert_contains "${board}" "#700 blocked" "board shows blocked issue"
assert_contains "${board}" "#633 blocked:700" "board shows pending issue gated on its unmet dependency"

# --- Fix G: requeue quarantines stale artifacts and resets to pending ---
python3 "${STATE_HELPER}" requeue --issue 700 --reason "force fresh retry" --state-file "${STATE_FILE}" >/dev/null
status_700="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["issue_registry"]["700"]["status"])' "${STATE_FILE}")"
assert_eq "${status_700}" "pending" "requeue resets the issue to pending"
recovery_attempts_700="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["issue_registry"]["700"]["sub_coord_recovery_attempts"])' "${STATE_FILE}")"
assert_eq "${recovery_attempts_700}" "0" "manual requeue resets the sub-coordinator recovery budget"
[[ ! -f "${ISSUE_DIR_700}/report.json" ]] || fail "requeue should quarantine the stale report.json"
ls "${ISSUE_DIR_700}/recovery/"requeue-* >/dev/null 2>&1 || fail "requeue should create a recovery archive dir"

# --- Fix G: completing a dependency auto-unblocks a blocked dependent ---
# Re-block 700 on 701 to test the auto-unblock path on finalize.
python3 - "${STATE_FILE}" "${ISSUE_DIR_700}" <<'PY'
import json, sys
state_file, issue_dir = sys.argv[1], sys.argv[2]
s = json.load(open(state_file))
s["issue_registry"]["700"] = {
    "status": "blocked", "deps": [701],
    "blocking_reasons": ["blocked-by-issue-701"],
    "issue_dir": issue_dir,
}
json.dump(s, open(state_file, "w"), indent=2)
PY
printf '{"outcome":"completed","summary":"done","commit_sha":"abc"}\n' > "${WORK_DIR}/rep701.json"
python3 "${STATE_HELPER}" finalize-issue --issue 701 --report-file "${WORK_DIR}/rep701.json" --state-file "${STATE_FILE}" >/dev/null
status_700b="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["issue_registry"]["700"]["status"])' "${STATE_FILE}")"
assert_eq "${status_700b}" "pending" "completing dependency 701 auto-unblocks dependent 700"
reasons_700="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["issue_registry"]["700"]["blocking_reasons"])' "${STATE_FILE}")"
assert_eq "${reasons_700}" "[]" "auto-unblock clears the dependency blocking reason"

# --- Safe parallel admission uses explicit metadata and active ownership locks ---
ADMISSION_STATE="${WORK_DIR}/admission-state.json"
cat > "${ADMISSION_STATE}" <<'JSON'
{
  "schema_version": 4,
  "execution_plan": {"topo_order": [1, 2, 3, 4, 5], "parallel_jobs": 4},
  "active_pool_issues": [1],
  "issue_registry": {
    "1": {"status": "in_progress", "deps": [], "parallel_safe": true, "ownership_scope": ["src/api"], "context_file": "1.md"},
    "2": {"status": "pending", "deps": [], "parallel_safe": true, "ownership_scope": ["src/api/users"], "context_file": "2.md"},
    "3": {"status": "pending", "deps": [], "parallel_safe": true, "ownership_scope": ["docs"], "context_file": "3.md"},
    "4": {"status": "pending", "deps": [], "parallel_safe": false, "ownership_scope": ["tools"], "context_file": "4.md"},
    "5": {"status": "pending", "deps": [], "parallel_safe": true, "ownership_scope": ["tests"], "context_file": "5.md"}
  }
}
JSON
ready_safe="$(python3 "${STATE_HELPER}" ready-issues --state-file "${ADMISSION_STATE}" --limit 4)"
assert_eq "${ready_safe}" "3 5" "ready selection admits only disjoint explicitly safe issues"

python3 - "${ADMISSION_STATE}" <<'PY'
import json, sys
path = sys.argv[1]
state = json.load(open(path))
state["active_pool_issues"] = [8]
state["execution_plan"]["topo_order"] = [8, 9, 10]
state["issue_registry"] = {
    "8": {"status": "in_progress", "deps": [], "parallel_safe": True, "ownership_scope": ["DOCS"], "context_file": "8.md"},
    "9": {"status": "pending", "deps": [], "parallel_safe": True, "ownership_scope": ["docs/api"], "context_file": "9.md"},
    "10": {"status": "pending", "deps": [], "parallel_safe": True, "ownership_scope": ["src"], "context_file": "10.md"},
}
json.dump(state, open(path, "w"), indent=2)
PY
ready_casefold="$(python3 "${STATE_HELPER}" ready-issues --state-file "${ADMISSION_STATE}" --limit 4)"
assert_eq "${ready_casefold}" "10" "ownership admission is conservative on case-insensitive filesystems"

python3 - "${ADMISSION_STATE}" <<'PY'
import json, sys
path = sys.argv[1]
state = json.load(open(path))
state["active_pool_issues"] = []
state["execution_plan"]["topo_order"] = [6, 7]
state["issue_registry"] = {
    "6": {"status": "pending", "deps": [], "context_file": "6.md"},
    "7": {"status": "pending", "deps": [], "parallel_safe": True, "ownership_scope": ["docs"], "context_file": "7.md"},
}
json.dump(state, open(path, "w"), indent=2)
PY
ready_conservative="$(python3 "${STATE_HELPER}" ready-issues --state-file "${ADMISSION_STATE}" --limit 4)"
assert_eq "${ready_conservative}" "6" "missing concurrency metadata defaults to exclusive"
python3 - "${ADMISSION_STATE}" <<'PY'
import json, sys
state = json.load(open(sys.argv[1]))
assert state["issue_registry"]["6"]["parallel_safe"] is False
assert state["issue_registry"]["6"]["ownership_scope"] == []
assert state["issue_registry"]["7"]["ownership_scope"] == ["docs"]
PY

# A blocked handoff that cites a dependency already completed is stale-base
# evidence and must be requeued instead of becoming durably blocked.
STALE_STATE="${WORK_DIR}/stale-state.json"
cat > "${STALE_STATE}" <<'JSON'
{
  "schema_version": 4,
  "execution_plan": {"topo_order": [10, 11], "parallel_jobs": 2},
  "active_pool_issues": [11],
  "issue_registry": {
    "10": {"status": "completed", "deps": []},
    "11": {"status": "in_progress", "deps": [10], "context_file": "11.md", "sub_coord_recovery_attempts": 3}
  }
}
JSON
printf '%s\n' '{"outcome":"blocked","summary":"dependency missing","blocking_reasons":["dependency issue 10 missing from worktree"]}' > "${WORK_DIR}/stale-report.json"
stale_outcome="$(python3 "${STATE_HELPER}" finalize-issue --issue 11 --report-file "${WORK_DIR}/stale-report.json" --state-file "${STALE_STATE}")"
assert_eq "${stale_outcome}" "pending" "completed dependency missing report becomes stale-base requeue"
stale_status="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["issue_registry"]["11"]["status"])' "${STALE_STATE}")"
assert_eq "${stale_status}" "pending" "stale-base issue is eligible for a fresh handoff"
stale_recovery_attempts="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["issue_registry"]["11"]["sub_coord_recovery_attempts"])' "${STALE_STATE}")"
assert_eq "${stale_recovery_attempts}" "0" "automatic stale-base requeue resets the recovery budget"

python3 - "${STALE_STATE}" <<'PY'
import json, sys

path = sys.argv[1]
state = json.load(open(path))
state["issue_registry"]["11"]["status"] = "in_progress"
state["active_pool_issues"] = [11]
json.dump(state, open(path, "w"), indent=2)
PY
second_stale_outcome="$(python3 "${STATE_HELPER}" finalize-issue --issue 11 --report-file "${WORK_DIR}/stale-report.json" --state-file "${STALE_STATE}")"
assert_eq "${second_stale_outcome}" "blocked" "stale-base automatic requeue is capped at one attempt"

SUBSTRING_STATE="${WORK_DIR}/substring-state.json"
cat > "${SUBSTRING_STATE}" <<'JSON'
{
  "schema_version": 4,
  "execution_plan": {"topo_order": [61, 62], "parallel_jobs": 1},
  "active_pool_issues": [62],
  "issue_registry": {
    "61": {"status": "completed", "deps": []},
    "62": {"status": "in_progress", "deps": [61], "context_file": "62.md"}
  }
}
JSON
printf '%s\n' '{"outcome":"blocked","blocking_reasons":["dependency #618 missing from worktree"]}' > "${WORK_DIR}/substring-report.json"
substring_outcome="$(python3 "${STATE_HELPER}" finalize-issue --issue 62 --report-file "${WORK_DIR}/substring-report.json" --state-file "${SUBSTRING_STATE}")"
assert_eq "${substring_outcome}" "blocked" "dependency 61 does not match issue #618"

GENERIC_BLOCK_STATE="${WORK_DIR}/generic-block-state.json"
cat > "${GENERIC_BLOCK_STATE}" <<'JSON'
{
  "schema_version": 4,
  "execution_plan": {"topo_order": [70, 71], "parallel_jobs": 1},
  "active_pool_issues": [71],
  "issue_registry": {
    "70": {"status": "completed", "deps": []},
    "71": {"status": "in_progress", "deps": [70], "context_file": "71.md"}
  }
}
JSON
printf '%s\n' '{"outcome":"blocked","blocking_reasons":["blocked by missing STRIPE_API_KEY"]}' > "${WORK_DIR}/generic-block-report.json"
generic_block_outcome="$(python3 "${STATE_HELPER}" finalize-issue --issue 71 --report-file "${WORK_DIR}/generic-block-report.json" --state-file "${GENERIC_BLOCK_STATE}")"
assert_eq "${generic_block_outcome}" "blocked" "generic blocked-by text is not stale dependency evidence"

echo "PASS: run-with-it state requeue/auto-unblock/status-board"
