# PR #150 Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Address the remaining actionable round-three review findings on PR #150 without changing its successful handoff architecture.

**Architecture:** Extend the existing runner and dispatcher parity paths with defensive timer parsing and a stall-equivalent hard-limit salvage sequence. Add a strict stale-base-only dependency matcher and a one-attempt automatic requeue budget while leaving the broader auto-unblock helper and all core handoff invariants intact.

**Tech Stack:** Bash 3.2-compatible shell, PowerShell, Python 3 standard library, Git, existing shell contract harnesses.

## Global Constraints

- Preserve canonical SHA validation, mandatory verification, typed artifact recovery, remote-tracking bases, cumulative reviewer exclusions, and ownership-aware admission.
- The default hard limit applies only to `impl`, `modify`, `review`, and `complexity`; explicit CLI or environment values remain authoritative for every role.
- Automatic stale-base requeue is capped at one attempt per issue; manual requeue remains available.
- Do not implement the fast-follow findings listed as non-goals in the approved design.
- Keep Bash compatible with macOS Bash 3.2 and PowerShell behavior equivalent where both platforms implement the same flow.

---

### Task 1: Normalize heartbeat and hard-limit configuration

**Files:**
- Modify: `assets/run-agent.sh`
- Modify: `assets/run-agent.ps1`
- Modify: `assets/run-with-it-dispatch.sh`
- Modify: `assets/run-with-it-dispatch.ps1`
- Test: `tests/run-agent-status-bus.test.sh`
- Test: `tests/run-agent-ps1-status-bus.test.sh`
- Test: `tests/run-with-it-dispatch.test.sh`
- Test: `tests/run-with-it-dispatch-ps1.test.sh`

**Interfaces:**
- Consumes: `RUN_WITH_IT_HEARTBEAT_SECONDS`, `RUN_WITH_IT_WORKER_HARD_LIMIT_SECONDS`, `--hard-limit-seconds`, and dispatcher `Role`.
- Produces: non-negative effective integer timer values and role-aware default hard limits.

- [ ] **Step 1: Write failing timer regression tests**

Add a Bash source contract proving zero-like heartbeat values are normalized:

```bash
assert_contains "${runner_source}" 'heartbeat_seconds=$((10#${RUN_WITH_IT_HEARTBEAT_SECONDS}))' "runner normalizes zero-padded heartbeat values"
```

Add a PowerShell runner invocation with `RUN_WITH_IT_HEARTBEAT_SECONDS=invalid`, a one-second fake child, and assertions that the runner exits successfully and writes `agent-complete`. Add dispatcher validation invocations proving malformed `RUN_WITH_IT_WORKER_HARD_LIMIT_SECONDS` does not terminate parameter binding and that a default `sub-coord` dispatch records `"hard_limit_seconds": 0` while an explicit `-HardLimitSeconds 2` records `2`.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
bash tests/run-agent-status-bus.test.sh
bash tests/run-agent-ps1-status-bus.test.sh
bash tests/run-with-it-dispatch.test.sh
bash tests/run-with-it-dispatch-ps1.test.sh
```

Expected: timer assertions fail because Bash accepts `"00"` unchanged, PowerShell casts malformed values under `Stop`, and every role inherits 7200 seconds.

- [ ] **Step 3: Implement minimal timer parsing**

In `run-agent.sh`, normalize after the numeric guard and use the normalized local in `sleep`:

```bash
local heartbeat_seconds
case "${RUN_WITH_IT_HEARTBEAT_SECONDS}" in
  ''|*[!0-9]*) return 0 ;;
esac
heartbeat_seconds=$((10#${RUN_WITH_IT_HEARTBEAT_SECONDS}))
[[ "${heartbeat_seconds}" -gt 0 ]] || return 0
```

In `run-agent.ps1`, replace the direct cast with `TryParse` and default 30:

```powershell
$heartbeatSeconds = 30
$parsedHeartbeatSeconds = 0
if ($env:RUN_WITH_IT_HEARTBEAT_SECONDS -and
    [int]::TryParse($env:RUN_WITH_IT_HEARTBEAT_SECONDS, [ref]$parsedHeartbeatSeconds) -and
    $parsedHeartbeatSeconds -ge 0) {
    $heartbeatSeconds = $parsedHeartbeatSeconds
}
```

In both dispatchers, track whether the hard limit came from CLI/environment, parse malformed PowerShell values through `TryParse`, and apply this default predicate only when no explicit value exists:

```text
role in {impl, modify, review, complexity} -> 7200
all other roles                         -> 0
```

Use `HARD_LIMIT_EXPLICIT=1` in Bash and `$PSBoundParameters.ContainsKey("HardLimitSeconds") -or $null -ne $env:RUN_WITH_IT_WORKER_HARD_LIMIT_SECONDS` in PowerShell so explicit zero and explicit limits remain authoritative.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the four commands from Step 2. Expected: all pass, including malformed PowerShell environment values and role-aware defaults.

- [ ] **Step 5: Commit timer handling**

```bash
git add assets/run-agent.sh assets/run-agent.ps1 assets/run-with-it-dispatch.sh assets/run-with-it-dispatch.ps1 tests/run-agent-status-bus.test.sh tests/run-agent-ps1-status-bus.test.sh tests/run-with-it-dispatch.test.sh tests/run-with-it-dispatch-ps1.test.sh
git commit -m "fix(run-with-it): validate worker timers"
```

### Task 2: Make hard-limit termination salvage-safe

**Files:**
- Modify: `assets/run-with-it-dispatch.sh`
- Modify: `assets/run-with-it-dispatch.ps1`
- Test: `tests/run-with-it-dispatch.test.sh`
- Test: `tests/run-with-it-dispatch-ps1.test.sh`

**Interfaces:**
- Consumes: existing `completion_ready`/`Test-CompletionReady`, synthesis functions, artifact failure reason, and artifact failure classifier.
- Produces: exit 0 for valid completion/synthesis, exit 75 for typed recovery, or classified exit 124 for a true hard-limit failure.

- [ ] **Step 1: Write failing hard-limit regression tests**

Extend both dispatcher harnesses with agents that remain alive after writing:

```text
case A: valid done sentinel plus valid result at the hard-limit boundary
case B: valid complexity output that the synthesizer converts into artifacts
case C: auth/quota-shaped log text with no valid artifact
```

Assert case A and B exit 0 with `dispatch-complete`; assert case C exits 124 with `stall_reason=hard-limit-exceeded` and `failure_class=infrastructure` from the existing classifier. Retain the current no-progress case asserting exit 124.

- [ ] **Step 2: Run dispatcher tests and verify RED**

```bash
bash tests/run-with-it-dispatch.test.sh
bash tests/run-with-it-dispatch-ps1.test.sh
```

Expected: completion-window and complexity synthesis cases exit 124, and classified failure is hardcoded as capability.

- [ ] **Step 3: Mirror the stall salvage sequence in Bash**

Implement this order inside the hard-limit branch:

```bash
if completion_ready; then
  write_worker_state "completed" "true"
  write_status "STATUS|type=dispatch-complete|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|result_file=${RESULT_FILE}"
  exit 0
fi
if synthesize_stalled_result_if_possible; then
  artifact_reason="$(result_artifact_failure_reason)"
  if [ "$artifact_reason" = "artifact-recovery-required" ]; then
    write_status "STATUS|type=worker-hard-limit|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|elapsed=${elapsed}|action=preserve-for-recovery"
    write_worker_state "artifact-recovery-required" "false" "75" "$artifact_reason" "capability"
    write_status "STATUS|type=dispatch-recovery-required|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|reason=${artifact_reason}|result_file=${RESULT_FILE}"
    set +e
    terminate_runner_tree "$pid" >/dev/null 2>&1
    set -e
    exit 75
  fi
fi
if completion_ready; then
  write_status "STATUS|type=worker-hard-limit|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|elapsed=${elapsed}|action=salvage-and-terminate"
  write_worker_state "completed" "false" "0" "salvaged-at-hard-limit"
  write_status "STATUS|type=dispatch-complete|issue=${ISSUE}|role=${ROLE}${cycle_field}|pid=${pid}|result_file=${RESULT_FILE}"
  terminate_runner_tree "$pid" >/dev/null 2>&1
  exit 0
fi
failure_class="$(result_artifact_failure_class)"
```

Write `failure_class` into the final state and status instead of the hardcoded value.

- [ ] **Step 4: Apply the PowerShell ordering**

Insert the same completion checks around synthesis and classify the terminal failure:

```powershell
if (Test-CompletionReady) {
    Write-WorkerState "completed" $true 0
    Write-Status "STATUS|type=dispatch-complete|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|result_file=$ResultFile"
    exit 0
}
if (Try-SynthesizeResultArtifact -FromStall) {
    $artifactReason = Get-ResultArtifactFailureReason
    if ($artifactReason -eq "artifact-recovery-required") {
        Write-Status "STATUS|type=worker-hard-limit|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|elapsed=$elapsed|action=preserve-for-recovery"
        try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
        Write-WorkerState "artifact-recovery-required" $false 75 $artifactReason "capability"
        Write-Status "STATUS|type=dispatch-recovery-required|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|reason=$artifactReason|result_file=$ResultFile"
        exit 75
    }
}
if (Test-CompletionReady) {
    Write-Status "STATUS|type=worker-hard-limit|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|elapsed=$elapsed|action=salvage-and-terminate"
    try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
    Write-WorkerState "completed" $false 0 "salvaged-at-hard-limit"
    Write-Status "STATUS|type=dispatch-complete|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|result_file=$ResultFile"
    exit 0
}
$failureClass = Get-ResultArtifactFailureClass
```

- [ ] **Step 5: Run dispatcher tests and verify GREEN**

Run the commands from Step 2. Expected: both platform suites pass every existing and new hard-limit assertion.

- [ ] **Step 6: Commit hard-limit parity**

```bash
git add assets/run-with-it-dispatch.sh assets/run-with-it-dispatch.ps1 tests/run-with-it-dispatch.test.sh tests/run-with-it-dispatch-ps1.test.sh
git commit -m "fix(run-with-it): salvage at hard limit"
```

### Task 3: Define coordinator handling for hard-limit failures

**Files:**
- Modify: `assets/coordinator-rules.md`
- Modify: `assets/sub-coordinator-prompt.md`
- Test: `tests/run-with-it-pool.test.sh`
- Test: `tests/run-with-it-pool-ps1.test.sh`

**Interfaces:**
- Consumes: dispatcher `stall_reason=hard-limit-exceeded`.
- Produces: retryable implementation/modification infrastructure handling and review artifact retry handling without `failed-review` misclassification.

- [ ] **Step 1: Add failing contract assertions**

```bash
assert_file_contains "${ROOT_DIR}/assets/coordinator-rules.md" "hard-limit-exceeded" "coordinator retries hard-limit handoff failures"
assert_file_contains "${ROOT_DIR}/assets/sub-coordinator-prompt.md" "hard-limit-exceeded" "sub-coordinator classifies hard-limit handoff failures"
```

Add equivalent assertions to the PowerShell pool contract.

- [ ] **Step 2: Run pool contracts and verify RED**

```bash
bash tests/run-with-it-pool.test.sh
bash tests/run-with-it-pool-ps1.test.sh
```

Expected: both fail because neither contract names the new terminal reason.

- [ ] **Step 3: Update the coordinator contracts**

Add `hard-limit-exceeded` to the implementation/modification artifact failure list and the review artifact guardrail list. State explicitly:

```text
A hard-limit-exceeded dispatcher result is infrastructure/artifact loss, not a product review verdict, and must never be reported as failed-review.
```

Keep all existing retry budgets and typed recovery routing unchanged.

- [ ] **Step 4: Run pool contracts and verify GREEN**

Run the commands from Step 2. Expected: both pass.

- [ ] **Step 5: Commit coordinator contracts**

```bash
git add assets/coordinator-rules.md assets/sub-coordinator-prompt.md tests/run-with-it-pool.test.sh tests/run-with-it-pool-ps1.test.sh
git commit -m "fix(run-with-it): route hard-limit failures"
```

### Task 4: Bound strict stale-base requeues

**Files:**
- Modify: `assets/run-with-it-state.py`
- Test: `tests/run-with-it-state.test.sh`

**Interfaces:**
- Consumes: issue dependency IDs, blocking reasons, `stale_base_requeue_attempts`, and `sub_coord_recovery_attempts`.
- Produces: one strict automatic stale-base requeue or a durable blocked outcome.

- [ ] **Step 1: Write failing state regression tests**

Add independent fixtures asserting:

```text
dependency 61 + reason containing #618                   -> blocked
completed deps + "blocked by missing STRIPE_API_KEY"    -> blocked
completed dep 10 + "dependency issue 10 missing"        -> first finalize returns pending
same issue and reason after one automatic requeue        -> second finalize returns blocked
manual requeue with sub_coord_recovery_attempts=3        -> pending with attempts reset to 0
automatic stale-base requeue with recovery attempts=3    -> pending with attempts reset to 0
```

- [ ] **Step 2: Run the state suite and verify RED**

```bash
bash tests/run-with-it-state.test.sh
```

Expected: substring and generic phrase fixtures incorrectly return pending, repeated finalization requeues again, and recovery attempts remain exhausted.

- [ ] **Step 3: Add a strict stale-base matcher**

Import `re` and add a dedicated helper without changing the broader auto-unblock predicate:

```python
def is_stale_base_dependency_reason(reason: Any, deps: list[Any]) -> bool:
    if not isinstance(reason, str):
        return False
    for dep in deps:
        dep_id = re.escape(str(dep))
        if re.search(rf"#{dep_id}(?!\d)", reason):
            return True
        if re.search(rf"\bissue(?:-| ){dep_id}\b", reason, re.IGNORECASE):
            return True
    return False
```

Use this helper only in `finalize_issue` stale-base detection.

- [ ] **Step 4: Add the automatic requeue budget and recovery reset**

Gate the stale-base branch with:

```python
stale_base_requeue_attempts = int(entry.get("stale_base_requeue_attempts", 0) or 0)
```

Requeue only when the count is below one, then set:

```python
entry["stale_base_requeue_attempts"] = stale_base_requeue_attempts + 1
entry["sub_coord_recovery_attempts"] = 0
```

In `requeue_issue`, also set `entry["sub_coord_recovery_attempts"] = 0`. Do not cap manual requeues.

- [ ] **Step 5: Run state tests and verify GREEN**

```bash
bash tests/run-with-it-state.test.sh
python3 -m py_compile assets/run-with-it-state.py
```

Expected: all state regressions pass and the helper compiles.

- [ ] **Step 6: Commit bounded requeue behavior**

```bash
git add assets/run-with-it-state.py tests/run-with-it-state.test.sh
git commit -m "fix(run-with-it): bound stale-base requeues"
```

### Task 5: Verify the complete PR branch

**Files:**
- Verify: all files changed by Tasks 1-4

**Interfaces:**
- Consumes: the complete PR branch after focused red-green cycles.
- Produces: fresh full-suite, syntax, compile, and diff evidence.

- [ ] **Step 1: Run every shell suite**

```bash
passed=0
failed=0
for test_file in tests/*.test.sh; do
  if bash "$test_file"; then
    passed=$((passed + 1))
  else
    failed=$((failed + 1))
  fi
done
printf 'SUMMARY passed=%s failed=%s\n' "$passed" "$failed"
test "$failed" -eq 0
```

Expected: `failed=0`.

- [ ] **Step 2: Run syntax and compile checks**

```bash
bash -n assets/run-agent.sh assets/run-with-it-dispatch.sh assets/run-with-it-pool.sh
python3 -m py_compile assets/run-with-it-state.py assets/run-with-it-artifacts.py assets/run-with-it-router.py
if command -v pwsh >/dev/null 2>&1; then
  pwsh -NoProfile -Command '$errors = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("assets/run-agent.ps1", [ref]$null, [ref]$errors); if ($errors.Count) { $errors; exit 1 }; [void][System.Management.Automation.Language.Parser]::ParseFile("assets/run-with-it-dispatch.ps1", [ref]$null, [ref]$errors); if ($errors.Count) { $errors; exit 1 }'
fi
git diff --check origin/codex/fix-run-handoff...HEAD
```

Expected: every command exits 0 with no syntax or whitespace errors.

- [ ] **Step 3: Audit preserved core invariants**

```bash
git diff --stat origin/codex/fix-run-handoff...HEAD
git diff origin/codex/fix-run-handoff...HEAD -- assets/run-with-it-artifacts.py assets/run-with-it-router.py assets/sub-coordinator-prompt.md assets/run-with-it-state.py
```

Confirm no changes weaken verification, canonical SHA handling, typed recovery, remote base selection, cumulative exclusions, or ownership admission.

- [ ] **Step 4: Review final branch status**

```bash
git status --short --branch
git log --oneline origin/codex/fix-run-handoff..HEAD
```

Expected: clean worktree and only the approved design, plan, and focused review-fix commits ahead of the PR remote.
