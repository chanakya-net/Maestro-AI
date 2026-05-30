# Sub-Coordinator Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover a failed Sub-Coordinator by analyzing saved issue state, waiting for any live worker to finish, then spawning a replacement Sub-Coordinator from the saved phase instead of blocking dependent work.

**Architecture:** Add deterministic recovery analysis to `run-with-it-state.py`, then wire the Bash and PowerShell pool supervisors to use that analysis before finalizing a failed Sub-Coordinator. Recovery uses structured artifacts only: `sub-state.json`, worker state files, done sentinels, and result files.

**Tech Stack:** Bash, PowerShell, Python 3 JSON helpers, existing run-with-it shell test harness.

---

### Task 1: Recovery Analysis Helper

**Files:**
- Modify: `/Users/chanakya/projects/AI-Skills/tests/run-with-it-helpers.test.sh`
- Modify: `/Users/chanakya/projects/AI-Skills/assets/run-with-it-state.py`

- [ ] **Step 1: Write failing helper tests**

Add tests that create an issue with missing `report.json`, a `sub-state.json` containing an in-flight modify worker, and worker state transitions for `running` and `completed`.

Expected commands:

```bash
tests/run-with-it-helpers.test.sh
```

Expected red result before implementation: `run-with-it-state.py: invalid choice: 'analyze-sub-coord-failure'`.

- [ ] **Step 2: Implement `analyze-sub-coord-failure`**

Add a command that returns compact JSON:

```json
{
  "action": "wait_worker | spawn_recovery | finalize | block",
  "reason": "string",
  "phase": "modify",
  "worker_role": "modify",
  "worker_state": "running",
  "worker_state_file": "/path",
  "worker_result_file": "/path"
}
```

Rules:

- valid report file with terminal `outcome` -> `finalize`
- no `sub-state.json` -> `block`
- in-flight worker state `running`, `quiet`, or `stalled` with no valid result -> `wait_worker`
- in-flight worker state `completed` or valid result/done artifact -> `spawn_recovery`
- in-flight worker state `failed` -> `spawn_recovery`

- [ ] **Step 3: Verify helper tests pass**

Run:

```bash
tests/run-with-it-helpers.test.sh
```

Expected: `PASS: run-with-it helpers contract`.

### Task 2: Recovery Context and State Tracking

**Files:**
- Modify: `/Users/chanakya/projects/AI-Skills/tests/run-with-it-helpers.test.sh`
- Modify: `/Users/chanakya/projects/AI-Skills/assets/run-with-it-state.py`

- [ ] **Step 1: Write failing tests for recovery context**

Add a test for `write-sub-coord-recovery-context` that asserts the output includes:

- original issue context path
- `SUB_COORD_RECOVERY_MODE=1`
- `SUB_COORD_RECOVERY_ATTEMPT=<n>`
- `SUB_COORD_RECOVERY_REASON=<reason>`
- `SUB_COORD_STATE_FILE=<issue-dir>/sub-state.json`
- instruction to rehydrate from structured state and not restart completed phases

- [ ] **Step 2: Implement recovery context command**

Add `write-sub-coord-recovery-context` to `run-with-it-state.py`. It should append a small recovery preamble plus the original context file contents into a recovery context path.

- [ ] **Step 3: Add recovery attempt mutation**

Add `mark-sub-coord-recovery-dispatch-failed` to increment a bounded recovery failure reason if a replacement coordinator also exits without a report.

- [ ] **Step 4: Verify helper tests pass**

Run:

```bash
tests/run-with-it-helpers.test.sh
```

Expected: pass.

### Task 3: Bash Pool Recovery Flow

**Files:**
- Modify: `/Users/chanakya/projects/AI-Skills/tests/run-with-it-pool-actual-flow.test.sh`
- Modify: `/Users/chanakya/projects/AI-Skills/assets/run-with-it-pool.sh`

- [ ] **Step 1: Write failing pool smoke test**

Add a fake Sub-Coordinator that fails on first launch after writing `sub-state.json`, while a fake modify worker state later appears completed with a result. Assert the pool emits:

```text
STATUS|type=sub-coord-recovery-wait|issue=<n>
STATUS|type=sub-coord-recovery-spawn|issue=<n>|attempt=1
STATUS|type=sub-coord-complete|issue=<n>|outcome=completed
```

Expected red result: no recovery status lines and issue becomes blocked.

- [ ] **Step 2: Implement Bash recovery loop**

In `run-with-it-pool.sh`, replace immediate `finalize_issue` with:

1. call `analyze-sub-coord-failure`
2. if `wait_worker`, keep issue in pool and do not free a slot
3. if `spawn_recovery`, spawn replacement Sub-Coordinator using recovery context and fresh recovery log/done/state files
4. if `finalize`, finalize normally
5. if `block`, finalize as blocked

- [ ] **Step 3: Verify Bash pool tests pass**

Run:

```bash
tests/run-with-it-pool.test.sh
tests/run-with-it-pool-actual-flow.test.sh
```

Expected: pass.

### Task 4: PowerShell Pool Parity

**Files:**
- Modify: `/Users/chanakya/projects/AI-Skills/tests/run-with-it-pool-ps1.test.sh`
- Modify: `/Users/chanakya/projects/AI-Skills/assets/run-with-it-pool.ps1`

- [ ] **Step 1: Add PowerShell parity assertions**

Assert the PowerShell pool script contains the recovery status events and calls the new state helper commands.

- [ ] **Step 2: Implement PowerShell recovery flow**

Mirror Bash behavior in `run-with-it-pool.ps1`.

- [ ] **Step 3: Verify PowerShell tests pass when PowerShell is available**

Run:

```bash
tests/run-with-it-pool-ps1.test.sh
```

Expected: pass or skip when PowerShell is unavailable.

### Task 5: Prompt and Rule Contracts

**Files:**
- Modify: `/Users/chanakya/projects/AI-Skills/assets/sub-coordinator-prompt.md`
- Modify: `/Users/chanakya/projects/AI-Skills/assets/coordinator-rules.md`
- Modify: `/Users/chanakya/projects/AI-Skills/assets/main-orchestrator-rules.md`

- [ ] **Step 1: Update recovery-mode instructions**

Strengthen rules so a replacement Sub-Coordinator must read `sub-state.json`, process completed worker artifacts first, and never rerun completed phases.

- [ ] **Step 2: Update Main Orchestrator wording**

Replace the “rerun fresh” instruction for interrupted pool members with recovery-from-structured-state where possible.

- [ ] **Step 3: Verify contract tests**

Run:

```bash
tests/run-with-it-pool.test.sh
tests/run-with-it-helpers.test.sh
```

Expected: pass.

### Task 6: Final Verification

**Files:**
- Source assets under `/Users/chanakya/projects/AI-Skills/assets`

- [ ] **Step 1: Run targeted tests**

Run:

```bash
tests/run-with-it-helpers.test.sh
tests/run-with-it-pool.test.sh
tests/run-with-it-pool-actual-flow.test.sh
tests/run-with-it-pool-ps1.test.sh
```

- [ ] **Step 2: Run syntax checks**

Run:

```bash
python3 -m py_compile assets/run-with-it-state.py assets/run-with-it-github-update.py assets/run-with-it-artifacts.py
bash -n assets/run-with-it-pool.sh assets/run-with-it-dispatch.sh assets/run-agent.sh
```

- [ ] **Step 3: Verify git diff**

Run:

```bash
git diff --stat
git diff --check
```

Expected: no whitespace errors and only planned files changed.
