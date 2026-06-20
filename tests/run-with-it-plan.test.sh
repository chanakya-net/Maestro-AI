#!/usr/bin/env bash

# Slice A coverage for the pre-implementation Plan phase:
#  Test 1 — dispatch --role plan sets RUN_WITH_IT_ROLE=plan, forwards --repo-root,
#           and is excluded from implementation/check-in handling.
#  Test 2 — router --role plan strong-bumps the routing band (PLAN_BUMP).
#  Test 4 — plan.json artifact validity (well-formed accepted, malformed rejected).
#  Test 6 — hybrid complexity refinement: a valid plan complexity_level overrides
#           the blind band for impl routing and emits complexity-refined; an
#           invalid/absent plan falls back to the blind band with no override.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROUTER="${ROOT_DIR}/assets/run-with-it-router.py"
REGISTRY="${ROOT_DIR}/assets/agent-registry.json"
ARTIFACTS="${ROOT_DIR}/assets/run-with-it-artifacts.py"
DISPATCH="${ROOT_DIR}/assets/run-with-it-dispatch.sh"
SUBCOORD_PROMPT="${ROOT_DIR}/assets/sub-coordinator-prompt.md"
PLAN_PROMPT="${ROOT_DIR}/assets/plan-prompt.md"

fail() { echo "FAIL: $1" >&2; exit 1; }

assert_contains() {
  [[ "$1" == *"$2"* ]] || fail "$3 (missing: $2)"
}
assert_not_contains() {
  [[ "$1" != *"$2"* ]] || fail "$3 (unexpected: $2)"
}
assert_eq() {
  [[ "$1" == "$2" ]] || fail "$3 (expected '$2', got '$1')"
}

json_field() { python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

[[ -f "${PLAN_PROMPT}" ]] || fail "plan-prompt.md asset exists"

############################################
# Test 2 — router strong-bumps the plan band
############################################
plan_easy="$("${ROUTER}" --registry-file "${REGISTRY}" --ledger-file "${WORK_DIR}/l-easy.json" \
  --role plan --complexity-level easy --detected-agents codex,agy,claude)"
assert_eq "$(printf '%s' "${plan_easy}" | json_field complexity_level)" "easy" "plan keeps the blind complexity_level on the route report"
assert_eq "$(printf '%s' "${plan_easy}" | json_field routing_level)" "medium-hard" "plan @ easy bumps routing band up two levels (PLAN_BUMP)"

plan_mh="$("${ROUTER}" --registry-file "${REGISTRY}" --ledger-file "${WORK_DIR}/l-mh.json" \
  --role plan --complexity-level medium-hard --detected-agents codex,agy,claude)"
assert_eq "$(printf '%s' "${plan_mh}" | json_field routing_level)" "holy-fuck" "plan @ medium-hard (gated runtime band) routes at the strongest band"
# A strong reasoning agent is selected (claude-biased per registry), never agy.
assert_not_contains "$(printf '%s' "${plan_mh}" | json_field agent)" "agy" "plan never routes to agy"

echo "PASS: router strong-bumps the plan routing band"

############################################
# Test 4 — plan.json artifact validity
############################################
plan_validity() {  # <result-file> [issue-dir]
  local idir="${2:-${WORK_DIR}}"
  python3 "${ARTIFACTS}" failure-reason --role plan --issue 42 \
    --result-file "$1" --done-file /dev/null --issue-dir "${idir}" --repo-root "${WORK_DIR}" 2>&1
}

# A complete plan needs BOTH plan.json (result file) AND a non-empty plan.md at
# <issue-dir>/plan.md. Create the plan.md the WORK_DIR cases rely on.
printf '# Plan\nExtend the X handler.\n' > "${WORK_DIR}/plan.md"

cat > "${WORK_DIR}/plan-good.json" <<'JSON'
{ "schema_version": 1, "issue": "42", "role": "plan", "status": "success",
  "approach": "Extend the existing X handler with a Y branch.",
  "complexity_level": "complex", "files": [],
  "slices": [{"order":1,"behavior":"happy path","test_target":"x.test::a","files":["x"]}],
  "interfaces": [], "risks": [], "out_of_scope": [] }
JSON
assert_eq "$(plan_validity "${WORK_DIR}/plan-good.json")" "" "well-formed plan.json (with plan.md present) validates"

# P1: a self-reported failure must NOT be accepted as a usable plan.
cat > "${WORK_DIR}/plan-failed.json" <<'JSON'
{ "schema_version": 1, "issue": "42", "role": "plan", "status": "failed",
  "approach": "could not plan", "complexity_level": "complex",
  "slices": [{"order":1,"behavior":"x","test_target":"t","files":["x"]}] }
JSON
assert_eq "$(plan_validity "${WORK_DIR}/plan-failed.json")" "invalid-plan-result-artifact" "plan.json with status!=success is rejected"

# P2: a valid plan.json without a non-empty plan.md is incomplete.
NOPLAN_DIR="${WORK_DIR}/noplan"; mkdir -p "${NOPLAN_DIR}"
assert_eq "$(plan_validity "${WORK_DIR}/plan-good.json" "${NOPLAN_DIR}")" "missing-plan-file-artifact" "valid plan.json without plan.md is rejected"
printf '' > "${NOPLAN_DIR}/plan.md"  # empty plan.md still counts as missing
assert_eq "$(plan_validity "${WORK_DIR}/plan-good.json" "${NOPLAN_DIR}")" "missing-plan-file-artifact" "empty plan.md is rejected"

cat > "${WORK_DIR}/plan-missing.json" <<'JSON'
{ "schema_version": 1, "issue": "42", "role": "plan", "status": "success",
  "approach": "x", "slices": [{"order":1}] }
JSON
assert_eq "$(plan_validity "${WORK_DIR}/plan-missing.json")" "invalid-plan-result-artifact" "plan.json missing complexity_level is rejected"

cat > "${WORK_DIR}/plan-badband.json" <<'JSON'
{ "schema_version": 1, "issue": "42", "role": "plan", "status": "success",
  "approach": "x", "complexity_level": "trivial", "slices": [{"order":1}] }
JSON
assert_eq "$(plan_validity "${WORK_DIR}/plan-badband.json")" "invalid-plan-result-artifact" "plan.json with a non-router band is rejected"

cat > "${WORK_DIR}/plan-emptyslices.json" <<'JSON'
{ "schema_version": 1, "issue": "42", "role": "plan", "status": "success",
  "approach": "x", "complexity_level": "complex", "slices": [] }
JSON
assert_eq "$(plan_validity "${WORK_DIR}/plan-emptyslices.json")" "invalid-plan-result-artifact" "plan.json with empty slices is rejected"

wrong_issue="$(python3 "${ARTIFACTS}" failure-reason --role plan --issue 99 \
  --result-file "${WORK_DIR}/plan-good.json" --done-file /dev/null --issue-dir "${WORK_DIR}" --repo-root "${WORK_DIR}" 2>&1)"
assert_eq "${wrong_issue}" "invalid-plan-result-artifact" "plan.json for a different issue number is rejected"

assert_eq "$(plan_validity "${WORK_DIR}/does-not-exist.json")" "missing-result-artifact" "absent plan.json reports missing artifact"

echo "PASS: plan.json artifact validity accepts well-formed and rejects malformed"

############################################
# Test 1 — dispatch --role plan
############################################
CONTEXT_FILE="${WORK_DIR}/plan-context.md"; printf 'plan context\n' > "${CONTEXT_FILE}"
PLAN_REPO_ROOT="${WORK_DIR}/issue-worktree"; mkdir -p "${PLAN_REPO_ROOT}"
git -C "${PLAN_REPO_ROOT}" init -q
ISSUE_DIR="${WORK_DIR}/issues/42"; mkdir -p "${ISSUE_DIR}/workers/plan"

dry_plan="$("${DISPATCH}" --dry-run \
  --asset-root "${ROOT_DIR}/assets" \
  --role plan \
  --issue 42 \
  --cycle 1 \
  --agent claude \
  --model claude-opus-4-8 \
  --context-file "${CONTEXT_FILE}" \
  --prompt-file "${PLAN_PROMPT}" \
  --log-file "${ISSUE_DIR}/workers/plan/cycle-1.log" \
  --done-file "${ISSUE_DIR}/workers/plan/cycle-1.done" \
  --result-file "${ISSUE_DIR}/workers/plan/cycle-1-result.json" \
  --state-file "${ISSUE_DIR}/workers/plan/cycle-1.state.json" \
  --repo-root "${PLAN_REPO_ROOT}" \
  --issue-dir "${ISSUE_DIR}")"

assert_contains "${dry_plan}" "RUN_WITH_IT_ROLE=plan" "dispatch sets RUN_WITH_IT_ROLE=plan"
assert_contains "${dry_plan}" "REPO_ROOT=${PLAN_REPO_ROOT}" "dispatch forwards --repo-root so the planner can read the worktree"
assert_contains "${dry_plan}" "plan-prompt.md" "dispatch forwards the plan prompt"

# No implementation/check-in handling: is_implementation_role must match impl|modify only.
impl_role_def="$(grep -A2 'is_implementation_role()' "${DISPATCH}")"
assert_contains "${impl_role_def}" '"$ROLE" = "impl"' "is_implementation_role gates on impl"
assert_contains "${impl_role_def}" '"$ROLE" = "modify"' "is_implementation_role gates on modify"
assert_not_contains "${impl_role_def}" '"$ROLE" = "plan"' "plan is NOT an implementation role (no commit/check-in handling)"

echo "PASS: dispatch routes the plan role read-only with repo-root, no check-in handling"

############################################
# Test 6 — hybrid complexity refinement
############################################
# Router half: impl routes straight through on whatever band it is handed (no bump),
# so a refined band of 'complex' makes impl route on 'complex'.
impl_complex="$("${ROUTER}" --registry-file "${REGISTRY}" --ledger-file "${WORK_DIR}/l-impl.json" \
  --role impl --complexity-level complex --detected-agents codex,agy,claude)"
assert_eq "$(printf '%s' "${impl_complex}" | json_field routing_level)" "complex" "impl routes on the band it is handed (no bump)"

# Resolve half: run the ACTUAL band_rank + EFFECTIVE_COMPLEXITY block from the prompt.
RESOLVE_SNIPPET="$(python3 - "${SUBCOORD_PROMPT}" <<'PY'
import re, sys
src = open(sys.argv[1]).read()
start = src.index("### Plan Sub-Agent Delegation")
end = src.index("**Capture the issue baseline SHA", start)
section = src[start:end]
blocks = re.findall(r"```bash\n(.*?)```", section, re.S)
gate, resolve = blocks[0], blocks[1]
band = re.search(r"(band_rank\(\) \{.*?\n\})", gate, re.S).group(1)
print(band + "\n" + resolve)
PY
)"
[[ -n "${RESOLVE_SNIPPET}" ]] || fail "could not extract the EFFECTIVE_COMPLEXITY resolve block from the prompt"

RESOLVE_LOG="${WORK_DIR}/resolve.log"   # parent scope: survives the command-substitution subshell
RESOLVE_PLAN_MD="${WORK_DIR}/resolve-plan.md"; printf '# plan\nbody\n' > "${RESOLVE_PLAN_MD}"
run_resolve() {  # <plan_result_file> <blind_level> [plan_md_path] ; prints EFFECTIVE=..., log to $RESOLVE_LOG
  : > "${RESOLVE_LOG}"
  local plan_md="${3:-${RESOLVE_PLAN_MD}}"
  { printf '%s\n' "${RESOLVE_SNIPPET}"; printf 'printf "EFFECTIVE=%%s\\n" "$EFFECTIVE_COMPLEXITY"\n'; } > "${WORK_DIR}/resolve.sh"
  PLAN_RAN=1 PLAN_RESULT_FILE="$1" BLIND_COMPLEXITY_LEVEL="$2" EFFECTIVE_COMPLEXITY="$2" \
    PLAN_FILE="${plan_md}" \
    SUB_COORD_ISSUE_NUMBER=42 PYTHON_BIN=python3 SUB_COORD_LOG_FILE="${RESOLVE_LOG}" \
    bash "${WORK_DIR}/resolve.sh"
}

# Valid plan band 'complex' over blind 'medium' -> override + complexity-refined line.
out="$(run_resolve "${WORK_DIR}/plan-good.json" medium)"
assert_contains "${out}" "EFFECTIVE=complex" "valid plan complexity_level overrides the blind band"
assert_contains "$(cat "${RESOLVE_LOG}")" "type=complexity-refined|issue=42|from=medium|to=complex" "override emits an auditable complexity-refined STATUS line"

# Invalid plan (bad band) -> fall back to blind, no override line.
out="$(run_resolve "${WORK_DIR}/plan-badband.json" medium)"
assert_contains "${out}" "EFFECTIVE=medium" "invalid plan complexity_level falls back to the blind band"
assert_not_contains "$(cat "${RESOLVE_LOG}")" "complexity-refined" "no refinement line when the plan band is invalid"

# Valid plan that re-scores to the SAME band as blind -> no override line.
out="$(run_resolve "${WORK_DIR}/plan-good.json" complex)"
assert_contains "${out}" "EFFECTIVE=complex" "same-band re-score keeps the band"
assert_not_contains "$(cat "${RESOLVE_LOG}")" "complexity-refined" "no refinement line when the band does not change"

# Valid band but plan.md absent -> no refinement (guarded by [ -s "$PLAN_FILE" ]).
out="$(run_resolve "${WORK_DIR}/plan-good.json" medium "${WORK_DIR}/no-such-plan.md")"
assert_contains "${out}" "EFFECTIVE=medium" "missing plan.md blocks refinement even with a valid band"
assert_not_contains "$(cat "${RESOLVE_LOG}")" "complexity-refined" "no refinement when plan.md is absent (downstream would get no plan)"

echo "PASS: hybrid refinement overrides on a valid re-score and falls back otherwise"

############################################
# Test 3 — gate skips the plan below threshold
############################################
# Run the ACTUAL gate block from the prompt (band_rank + gate + dispatch). With a
# blind band below the threshold the skip branch is taken, so the dispatch in the
# else branch never executes.
GATE_SNIPPET="$(python3 - "${SUBCOORD_PROMPT}" <<'PY'
import re, sys
src = open(sys.argv[1]).read()
start = src.index("### Plan Sub-Agent Delegation")
end = src.index("**Capture the issue baseline SHA", start)
blocks = re.findall(r"```bash\n(.*?)```", src[start:end], re.S)
print(blocks[0])  # gate block
PY
)"
[[ -n "${GATE_SNIPPET}" ]] || fail "could not extract the gate block from the prompt"

run_gate() {  # <blind_level> <min> <enabled> ; writes log to $GATE_LOG, issue dir $GATE_ISSUE_DIR
  GATE_LOG="${WORK_DIR}/gate.log"; : > "${GATE_LOG}"
  GATE_ISSUE_DIR="${WORK_DIR}/gate-issue"; rm -rf "${GATE_ISSUE_DIR}"; mkdir -p "${GATE_ISSUE_DIR}"
  COMPLEXITY_LEVEL="$1" RUN_WITH_IT_PLAN_MIN_COMPLEXITY="$2" RUN_WITH_IT_PLAN_ENABLED="$3" \
    RUN_WITH_IT_ISSUE_DIR="${GATE_ISSUE_DIR}" SUB_COORD_ISSUE_NUMBER=42 SUB_COORD_LOG_FILE="${GATE_LOG}" \
    bash -c "${GATE_SNIPPET}"
}

run_gate easy medium-hard 1
assert_contains "$(cat "${GATE_LOG}")" "type=plan-skipped|issue=42|reason=below-threshold|blind=easy|min=medium-hard" "below-threshold blind band skips the plan phase"
[[ ! -d "${GATE_ISSUE_DIR}/workers/plan" ]] || fail "skipped plan must not create workers/plan/"

run_gate complex medium-hard 0
assert_contains "$(cat "${GATE_LOG}")" "type=plan-skipped|issue=42|reason=disabled" "RUN_WITH_IT_PLAN_ENABLED=0 disables the plan phase"
[[ ! -d "${GATE_ISSUE_DIR}/workers/plan" ]] || fail "disabled plan must not create workers/plan/"

echo "PASS: gate skips the plan below threshold and when disabled (no plan artifacts)"

############################################
# Test 5 — recovery does not re-spawn a valid plan
############################################
# Behavior is prompt-driven; assert the recovery contract plus that a valid
# plan.json is recognized as a complete artifact (so recovery skips re-spawn).
recovery_rule="$(grep -nE "Never rerun complexity, plan, implementation" "${SUBCOORD_PROMPT}")"
assert_contains "${recovery_rule}" "plan" "recovery never-rerun rule lists the plan phase"
assert_contains "$(grep -A40 '### Worker Done Files' "${SUBCOORD_PROMPT}")" "no \`commit_sha\` — the plan never commits" "Worker Done Files defines plan completeness without commit_sha"
# A valid plan.json is recognized as complete (reuse the validity check recovery relies on).
assert_eq "$(plan_validity "${WORK_DIR}/plan-good.json")" "" "recovery recognizes a valid plan.json as a complete artifact"

echo "PASS: recovery treats a valid plan.json as complete and never re-spawns the plan phase"

############################################
# Step 8 wiring — plan auto-fails when stalled, stays read-only
############################################
assert_contains "$(grep AUTO_FAIL_STALLED_ROLES "${ROOT_DIR}/assets/run-with-it-dispatch.sh")" "complexity,impl,modify,plan" "dispatch.sh auto-fails a stalled planner"
assert_contains "$(grep AutoFailStalledRoles "${ROOT_DIR}/assets/run-with-it-dispatch.ps1")" "complexity,impl,modify,plan" "dispatch.ps1 auto-fails a stalled planner"

echo "PASS: a stalled plan worker auto-fails like complexity (both shells)"

echo "ALL PLAN-PHASE TESTS PASSED"
