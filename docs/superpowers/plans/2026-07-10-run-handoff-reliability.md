# Run Handoff Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Eliminate stale-base, false artifact failure, unsafe stall salvage, quiet-worker termination, reviewer-reuse, cache bootstrap, and overlapping-schedule failures documented by the run diagnosis.

**Architecture:** Enforce correctness at four boundaries: Git/artifact identity, worker liveness, issue worktree base selection, and scheduler admission. Preserve old state formats with conservative defaults and keep Bash/PowerShell behavior equivalent.

**Tech Stack:** Python 3 standard library, Bash 3.2, PowerShell, Git CLI, JSON contract files, shell integration tests.

## Global Constraints

- Do not track `debug_human_report.md` or `debug_llm_context.md`.
- Normal implementation/modify success must require `verification.passed=true`.
- Unverified recovered work must be preserved as `artifact-recovery-required`, never normal success.
- Missing concurrency metadata defaults to exclusive execution.
- No Bash 4-only syntax and no new third-party dependency.
- Existing state files remain readable and user work is never destructively reset.
- GitHub and merge-recovery ownership contracts remain unchanged.

---

### Task 1: Make implementation artifacts canonical and verification-safe

**Files:**
- Modify: `assets/run-with-it-artifacts.py`
- Test: `tests/run-with-it-artifacts.test.sh`

**Interfaces:**
- Consumes: issue worktree path, `pre_spawn_head`, result JSON, Git CLI.
- Produces: canonical full `commit_sha`; precise validation reasons; typed
  `artifact-recovery-required` synthesis result.

- [x] **Step 1: Write failing artifact regression cases**

Add cases that create a real commit and assert:

```bash
# A unique short SHA is accepted and rewritten to the full HEAD.
short_sha="$(git -C "$repo" rev-parse --short=12 HEAD)"
full_sha="$(git -C "$repo" rev-parse HEAD)"
write_impl_result "$short_sha" true
assert_equals "" "$(validate_impl_result)" "short SHA validates"
assert_json_field "$result" 'payload["commit_sha"] == expected' "$full_sha"

# Passing verification is mandatory.
write_impl_result "$full_sha" false
assert_equals "implementation-verification-failed" \
  "$(validate_impl_result)" "failed verification is rejected"

# Stall synthesis is recovery, not success.
synthesize_from_stall
assert_json_field "$result" \
  'payload["status"] == "artifact_recovery_required"' \
  "salvage requires recovery"
```

Also assert unknown and non-commit object IDs are rejected.

- [x] **Step 2: Run the focused test and confirm the expected failures**

Run: `bash tests/run-with-it-artifacts.test.sh`

Expected: FAIL because short SHAs are compared as raw strings, failed
verification is accepted, and synthesis writes `status=success`.

- [x] **Step 3: Implement canonical commit resolution and recovery status**

Add and use helpers with these contracts:

```python
def resolve_commit(repo_root: str, revision: str) -> str | None:
    """Return one canonical 40-character commit SHA, or None."""

def canonicalize_result_commit(
    args: argparse.Namespace, payload: dict[str, Any]
) -> tuple[str | None, str]:
    """Return canonical commit and empty reason, or None and precise reason."""
```

`implementation_result_reason()` must require
`payload["verification"]["passed"] is True`, compare canonical commits, reject
the baseline commit, and atomically rewrite an accepted abbreviation.
`synthesize_implementation()` must write:

```json
{
  "status": "artifact_recovery_required",
  "verification": {"passed": false},
  "source": "dispatcher-synthesized"
}
```

The validator must return `artifact-recovery-required` for this typed handoff.

- [x] **Step 4: Run focused tests**

Run: `bash tests/run-with-it-artifacts.test.sh`

Expected: PASS.

### Task 2: Make routing bootstrap-safe and reviewer exclusions cumulative

**Files:**
- Modify: `assets/run-with-it-router.py`
- Modify: `assets/sub-coordinator-prompt.md`
- Test: `tests/run-with-it-router.test.sh`
- Test: `tests/run-with-it-routing.test.sh`

**Interfaces:**
- Consumes: zero or more `--exclude-model` values and an optional availability
  JSON path.
- Produces: deterministic excluded-model set and an independent selected route.

- [x] **Step 1: Write failing router and prompt tests**

Add assertions equivalent to:

```bash
"$ROUTER" ... --exclude-model model-a --exclude-model model-b
# Assert neither model appears as the selection.

"$ROUTER" ... --availability-file "$work/missing.json"
# Assert routing succeeds.

printf '{bad json' > "$work/invalid.json"
# Assert routing fails with an availability-file parse error.
```

The prompt contract must show the implementation model and all failed reviewer
models being passed as repeated exclusions before every review dispatch.

- [x] **Step 2: Run focused tests and confirm failure**

Run:

```bash
bash tests/run-with-it-router.test.sh
bash tests/run-with-it-routing.test.sh
```

Expected: FAIL because argparse accepts one exclusion, a missing cache raises,
and the prompt retains only one exclusion.

- [x] **Step 3: Implement set-based exclusions and missing-file semantics**

Use:

```python
parser.add_argument("--exclude-model", action="append", default=[])
exclude_models = {model for value in args.exclude_model for model in split_csv(value)}
```

Thread `set[str]` through candidate filtering. In `availability_from_file()`,
return `empty_availability()` when the path does not exist, but keep existing
JSON/type failures for present files. Update Bash and PowerShell prompt examples
to build repeated exclusion arguments from the implementation model plus the
failed-reviewer set and validate the selected reviewer before launch.

- [x] **Step 4: Run focused tests**

Run the two commands from Step 2. Expected: PASS.

### Task 3: Select and persist the correct issue worktree base

**Files:**
- Modify: `assets/sub-coordinator-prompt.md`
- Test: `tests/run-with-it-pool-actual-flow.test.sh`
- Test: `tests/run-with-it-routing.test.sh`

**Interfaces:**
- Consumes: fetched `origin/$RUN_FEATURE_BRANCH`, local fallback ref, recovery
  state, and issue worktree Git status.
- Produces: `issue_base_sha`, `issue_base_source`, and a worktree whose `HEAD`
  equals the selected base.

- [x] **Step 1: Add a failing diverged-ref integration test**

Create a bare remote, leave the local shared ref one commit behind its
remote-tracking ref, run the documented bootstrap, and assert:

```bash
assert_equals "$remote_tip" "$(git -C "$issue_worktree" rev-parse HEAD)" \
  "issue starts from fetched remote tip"
assert_json_field "$sub_state" 'payload["issue_base_sha"] == expected' "$remote_tip"
assert_json_field "$sub_state" \
  'payload["issue_base_source"] == "remote-tracking"' \
  "base source is durable"
```

Add contract assertions for local fallback and the dirty-resume recovery guard.

- [x] **Step 2: Run focused tests and confirm failure**

Run:

```bash
bash tests/run-with-it-pool-actual-flow.test.sh
bash tests/run-with-it-routing.test.sh
```

Expected: FAIL because bootstrap uses `$RUN_FEATURE_BRANCH` directly and does
not persist the source.

- [x] **Step 3: Update bootstrap and resume contracts**

Document and exercise this Bash 3.2-compatible selection:

```bash
git fetch origin "$RUN_FEATURE_BRANCH" 2>/dev/null || true
ISSUE_BASE_REF="origin/${RUN_FEATURE_BRANCH}"
ISSUE_BASE_SOURCE="remote-tracking"
if ! git rev-parse --verify "${ISSUE_BASE_REF}^{commit}" >/dev/null 2>&1; then
  ISSUE_BASE_REF="$RUN_FEATURE_BRANCH"
  ISSUE_BASE_SOURCE="local-fallback"
fi
ISSUE_BASE_SHA="$(git rev-parse "${ISSUE_BASE_REF}^{commit}")"
git worktree add -B "$ISSUE_BRANCH" "$ISSUE_WORKTREE_PATH" "$ISSUE_BASE_SHA"
test "$(git -C "$ISSUE_WORKTREE_PATH" rev-parse HEAD)" = "$ISSUE_BASE_SHA"
```

Persist both fields atomically. Refresh only when the saved worktree is clean,
`HEAD == issue_base_sha`, and no implementation/modification commit is saved;
otherwise retain it and enter recovery.

- [x] **Step 4: Run focused tests**

Run the commands from Step 2. Expected: PASS.

### Task 4: Enforce safe parallel admission and stale-dependency requeue

**Files:**
- Modify: `assets/run-with-it-state.py`
- Modify: `assets/run-with-it-pool.sh`
- Modify: `assets/run-with-it-pool.ps1`
- Test: `tests/run-with-it-state.test.sh`
- Test: `tests/run-with-it-pool.test.sh`
- Test: `tests/run-with-it-pool-ps1.test.sh`
- Test: `tests/run-with-it-pool-actual-flow.test.sh`

**Interfaces:**
- Consumes: issue metadata, active issue IDs, completed dependencies.
- Produces: a compatible ready set and structured admission deferral reasons.

- [x] **Step 1: Add failing state and pool admission tests**

Create fixtures proving:

```text
active parallel_safe=false + any candidate => candidate deferred
active safe scope [src/api] + candidate [src/api/users] => candidate deferred
active safe scope [src/api] + candidate [docs] => candidate admitted
missing parallel_safe/ownership_scope => exclusive default
blocked result naming an already-completed dependency => pending stale-base requeue
```

Assert Bash and PowerShell pools pass active issue IDs to `ready-issues` and
preserve rolling refill capacity for compatible candidates.

- [x] **Step 2: Run focused tests and confirm failure**

Run:

```bash
bash tests/run-with-it-state.test.sh
bash tests/run-with-it-pool.test.sh
bash tests/run-with-it-pool-ps1.test.sh
bash tests/run-with-it-pool-actual-flow.test.sh
```

Expected: FAIL because ready selection checks dependencies only.

- [x] **Step 3: Implement normalized ownership admission**

Add helpers with exact contracts:

```python
def normalized_ownership_scope(entry: dict[str, Any]) -> tuple[str, ...]:
    """Return sorted slash-normalized scopes without '.', duplicates, or blanks."""

def ownership_scopes_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    """Return true when equal scopes or a directory-prefix relationship exists."""

def issues_can_run_together(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Require explicit parallel_safe=True on both and disjoint nonempty scopes."""
```

Extend `ready-issues` with repeatable `--active-issue`, filter against active
entries and candidates already selected in the same call, and emit deferral
events without changing stdout's issue-number contract. Update both pools to
pass their active issue sets. During finalization, turn a dependency-missing
block into a pending stale-base requeue when all named dependencies are already
complete.

- [x] **Step 4: Run focused tests**

Run the four commands from Step 2. Expected: PASS.

### Task 5: Separate wrapper liveness from model output and type recovery

**Files:**
- Modify: `assets/run-agent.sh`
- Modify: `assets/run-agent.ps1`
- Modify: `assets/run-with-it-dispatch.sh`
- Modify: `assets/run-with-it-dispatch.ps1`
- Modify: `assets/agent-registry.json`
- Test: `tests/run-agent-status-bus.test.sh`
- Test: `tests/run-agent-ps1-status-bus.test.sh`
- Test: `tests/run-with-it-dispatch.test.sh`
- Test: `tests/run-with-it-dispatch-ps1.test.sh`

**Interfaces:**
- Consumes: external agent process lifecycle, heartbeat interval, stall threshold,
  hard timeout, result and Git state.
- Produces: wrapper heartbeats, non-terminal recovery handoff, bounded terminal
  failure when there is no progress.

- [x] **Step 1: Add failing quiet-worker and recovery tests**

Add fake agents that remain silent longer than the stall threshold. Assert a
wrapper heartbeat appears periodically, the dispatcher does not terminate the
worker for quiet stdout, and heartbeat emission stops after child exit. Add a
hard-limit dirty-worker case expecting `artifact-recovery-required`, plus a
hard-limit no-progress case expecting a bounded capability failure. Mirror the
contracts in PowerShell tests.

- [x] **Step 2: Run focused tests and confirm failure**

Run:

```bash
bash tests/run-agent-status-bus.test.sh
bash tests/run-agent-ps1-status-bus.test.sh
bash tests/run-with-it-dispatch.test.sh
bash tests/run-with-it-dispatch-ps1.test.sh
```

Expected: FAIL because runners emit no wrapper heartbeat and dispatchers
auto-fail on output silence.

- [x] **Step 3: Implement wrapper heartbeat lifecycle**

Use `RUN_WITH_IT_HEARTBEAT_SECONDS` with a small positive default. Bash starts a
background loop that calls the existing status emitter while the child PID is
alive and reliably kills/waits for it in cleanup. PowerShell uses a timer/job
with equivalent cleanup. Heartbeat payloads use
`type=wrapper-heartbeat|source=run-agent` so they cannot be confused with model
output.

- [x] **Step 4: Update dispatcher state machine**

Track wrapper heartbeat age separately from output silence. Quiet/stalled
stdout may emit status but must not terminate a heartbeat-alive child. Use a
hard elapsed limit for termination. When preserved Git progress synthesizes a
typed recovery artifact, write `state=artifact-recovery-required`, terminate
the child safely, and return a distinct non-success code/reason without writing
a success sentinel. No-progress hard-limit remains terminal failure. Mirror in
PowerShell.

- [x] **Step 5: Run focused tests**

Run the commands from Step 2. Expected: PASS.

### Task 6: Provide atomic artifact writing and lifecycle-aware verification guidance

**Files:**
- Modify: `assets/run-with-it-artifacts.py`
- Modify: `assets/prompt.md`
- Modify: `assets/modifier-prompt.md`
- Modify: `assets/review-prompt.md`
- Modify: `assets/sub-coordinator-prompt.md`
- Test: `tests/run-with-it-artifacts.test.sh`
- Test: `tests/run-agent.test.sh`
- Test: `tests/run-with-it-routing.test.sh`

**Interfaces:**
- Consumes: a schema-valid JSON payload and target artifact path; verification
  command plus repository lifecycle context.
- Produces: atomic artifact file; explicit applicable, inapplicable, or failed
  verification evidence.

- [x] **Step 1: Add failing writer and prompt contract tests**

Test a new helper command:

```bash
python3 assets/run-with-it-artifacts.py write-json \
  --result-file "$result" --payload-file "$payload"
```

Assert schema validation happens before atomic replacement and an invalid
payload leaves any existing target untouched. Prompt tests must forbid generated
Python string quoting and require preflight classification of verification as
`applicable`, `not_applicable` with evidence, or `failed`.

- [x] **Step 2: Run focused tests and confirm failure**

Run:

```bash
bash tests/run-with-it-artifacts.test.sh
bash tests/run-agent.test.sh
bash tests/run-with-it-routing.test.sh
```

Expected: FAIL because no general atomic writer command or lifecycle preflight
contract exists.

- [x] **Step 3: Implement the writer and prompt contract**

Add a `write-json` subcommand that loads the payload file, validates it for the
requested role/schema, and calls `write_json_atomic()`. Document exact command
templates in role prompts. Verification guidance must never silently skip a
required check: it records a greenfield baseline absence as `not_applicable`
with the inspected ref/path evidence and continues only when the issue contract
allows bootstrap; real command failures remain failures.

- [x] **Step 4: Run focused tests**

Run the commands from Step 2. Expected: PASS.

### Task 7: Document contracts and run the complete regression gate

**Files:**
- Modify: `README.md`
- Modify: `skills/run-with-it/SKILL.md`
- Modify: `assets/coordinator-rules.md`
- Test: all `tests/*.test.sh`

**Interfaces:**
- Consumes: completed runtime behavior.
- Produces: user-facing configuration/status documentation and verified PR scope.

- [x] **Step 1: Update documentation**

Document the remote-base invariant, `issue_base_sha/source`, safe ownership
admission, repeated model exclusions, missing-cache semantics, wrapper heartbeat
interval/hard limit, typed artifact recovery, and lifecycle-aware verification.

- [x] **Step 2: Run focused documentation contracts**

Run:

```bash
bash tests/run-with-it-routing.test.sh
bash tests/run-with-it-routing-windows.test.sh
bash tests/install-assets-contract.test.sh
bash tests/install-assets-powershell-contract.test.sh
```

Expected: PASS.

- [x] **Step 3: Run the full suite**

Run:

```bash
for test_file in tests/*.test.sh; do
  bash "$test_file" || exit 1
done
```

Expected: every suite exits zero.

- [x] **Step 4: Verify PR scope**

Run:

```bash
git diff --check
git status --short
git diff --name-only origin/main...HEAD
git ls-files --error-unmatch debug_human_report.md debug_llm_context.md
```

Expected: `git diff --check` passes; intended source, test, and plan/spec files
are present; the final `git ls-files` command fails because both diagnosis files
remain untracked and excluded.

## Plan self-review

- Spec coverage: all 18 acceptance criteria map to Tasks 1-7.
- Placeholder scan: no deferred implementation placeholders remain.
- Type consistency: router exclusions are `set[str]`; ownership scopes are
  normalized `tuple[str, ...]`; artifact recovery uses the stable external
  reason `artifact-recovery-required` and JSON status
  `artifact_recovery_required`.
