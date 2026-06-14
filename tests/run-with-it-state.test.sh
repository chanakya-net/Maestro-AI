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
    "700": {"status": "blocked", "deps": [701], "blocking_reasons": ["blocked-by-issue-701"],
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

echo "PASS: run-with-it state requeue/auto-unblock/status-board"
