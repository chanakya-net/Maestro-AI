#!/usr/bin/env bash

# Contract: a (re)dispatched sub-coordinator must never reuse a stale terminal
# report. The dispatch chokepoint quarantines sub-coordinator terminal markers
# (report.json, sub-coordinator.done, sub-coordinator.state.json,
# sub-coordinator.dispatch.out) before launch, while PRESERVING resume state
# (sub-state.json, workers/). Without this, a fresh runner finds the prior
# "blocked" report, no-ops, and the control plane stamps a phantom
# "sub-coordinator recovery dispatcher failed" (root cause of issues #653/#641).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_HELPER="${ROOT_DIR}/assets/run-with-it-state.py"
POOL_SH="${ROOT_DIR}/assets/run-with-it-pool.sh"
POOL_PS1="${ROOT_DIR}/assets/run-with-it-pool.ps1"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_file_exists() {
  [[ -f "$1" ]] || fail "$2 (missing file: $1)"
}

assert_file_absent() {
  [[ ! -e "$1" ]] || fail "$2 (unexpected file: $1)"
}

assert_file_contains() {
  grep -Fq -- "$2" "$1" || fail "$3 (missing: $2)"
}

WORK_DIR="$(mktemp -d)"
cleanup() { rm -rf "${WORK_DIR}"; }
trap cleanup EXIT

ISSUE_DIR="${WORK_DIR}/issues/653"
mkdir -p "${ISSUE_DIR}/workers/review"

# Stale terminal markers (the no-op trigger).
printf '{"outcome":"blocked","blocking_reasons":["sub-coordinator recovery dispatcher failed"]}\n' > "${ISSUE_DIR}/report.json"
printf 'DONE|issue=653|status=success\n' > "${ISSUE_DIR}/sub-coordinator.done"
printf '{"state":"completed","alive":false,"done":true}\n' > "${ISSUE_DIR}/sub-coordinator.state.json"
printf 'STATUS|type=dispatch-complete|issue=653\n' > "${ISSUE_DIR}/sub-coordinator.dispatch.out"

# Resume state that recovery MUST keep.
printf '{"phase":"review","review_history":[{"cycle":1}]}\n' > "${ISSUE_DIR}/sub-state.json"
printf '{"verdict":"revise"}\n' > "${ISSUE_DIR}/workers/review/cycle-1-status.json"

# --- archive run ---
archive_dir="$(python3 "${STATE_HELPER}" quarantine-sub-coord-markers --issue-dir "${ISSUE_DIR}")"
[[ -n "${archive_dir}" ]] || fail "quarantine prints the archive dir when markers were moved"
case "${archive_dir}" in
  *"/recovery/predispatch-"*) ;;
  *) fail "archive dir uses the default predispatch label (got: ${archive_dir})" ;;
esac

# Terminal markers moved out of the live issue dir...
assert_file_absent "${ISSUE_DIR}/report.json" "report.json quarantined from live dir"
assert_file_absent "${ISSUE_DIR}/sub-coordinator.done" "done marker quarantined from live dir"
assert_file_absent "${ISSUE_DIR}/sub-coordinator.state.json" "dispatcher state quarantined from live dir"
assert_file_absent "${ISSUE_DIR}/sub-coordinator.dispatch.out" "dispatch.out quarantined from live dir"

# ...and preserved under the archive dir.
assert_file_exists "${archive_dir}/report.json" "report.json archived for audit"
assert_file_exists "${archive_dir}/sub-coordinator.done" "done marker archived for audit"
assert_file_exists "${archive_dir}/sub-coordinator.state.json" "dispatcher state archived for audit"
assert_file_exists "${archive_dir}/sub-coordinator.dispatch.out" "dispatch.out archived for audit"

# Resume state is untouched so recovery continues instead of restarting.
assert_file_exists "${ISSUE_DIR}/sub-state.json" "sub-state.json preserved for recovery resume"
assert_file_exists "${ISSUE_DIR}/workers/review/cycle-1-status.json" "worker artifacts preserved for recovery resume"

# --- idempotency: a clean dir moves nothing and prints empty ---
second="$(python3 "${STATE_HELPER}" quarantine-sub-coord-markers --issue-dir "${ISSUE_DIR}")"
[[ -z "${second}" ]] || fail "quarantine is idempotent: prints empty when nothing to move (got: ${second})"

# --- custom label ---
printf '{"outcome":"blocked"}\n' > "${ISSUE_DIR}/report.json"
labelled="$(python3 "${STATE_HELPER}" quarantine-sub-coord-markers --issue-dir "${ISSUE_DIR}" --label custom-label)"
[[ "${labelled}" == *"/recovery/custom-label" ]] || fail "quarantine honors an explicit --label (got: ${labelled})"
assert_file_exists "${ISSUE_DIR}/recovery/custom-label/report.json" "custom label archive holds the marker"

# --- wiring: every dispatch chokepoint must call the guard ---
assert_file_contains "${POOL_SH}" "quarantine-sub-coord-markers" "pool.sh invokes the quarantine subcommand"
assert_file_contains "${POOL_SH}" "quarantine_sub_coord_markers \"\$issue\" \"\$issue_dir\"" "pool.sh calls the guard in spawn paths"
# Both spawn functions (normal + recovery) must guard: expect two call sites.
call_sites="$(grep -c 'quarantine_sub_coord_markers "\$issue" "\$issue_dir"' "${POOL_SH}" || true)"
[[ "${call_sites}" -ge 2 ]] || fail "pool.sh guards both spawn_issue and spawn_recovery_issue (found ${call_sites} call sites)"
assert_file_contains "${POOL_PS1}" "quarantine-sub-coord-markers" "pool.ps1 invokes the quarantine subcommand"
assert_file_contains "${POOL_PS1}" "Quarantine-SubCoordMarkers \$issue \$issueDir" "pool.ps1 calls the guard in spawn paths"
ps1_sites="$(grep -c 'Quarantine-SubCoordMarkers \$issue \$issueDir' "${POOL_PS1}" || true)"
[[ "${ps1_sites}" -ge 2 ]] || fail "pool.ps1 guards both Spawn-Issue and Spawn-RecoveryIssue (found ${ps1_sites} call sites)"

echo "PASS: run-with-it requeue marker quarantine"
