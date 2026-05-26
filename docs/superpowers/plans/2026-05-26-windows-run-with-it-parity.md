# Windows Run With It Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Windows behavior explicit and reliable: native PowerShell must either fail early with a clear Git Bash/WSL requirement or run `run-with-it` with PowerShell dispatcher, pool, watcher, and watchdog parity.

**Architecture:** Preserve the existing Bash implementation for macOS, Linux, Git Bash, and WSL. Add native PowerShell equivalents for the orchestration helpers and update `run-agent.ps1` so worker output is captured live enough for watchdog state updates. The skill chooses the helper family from `OS_FAMILY`: Bash for Unix-like shells, PowerShell for native Windows.

**Tech Stack:** Bash, PowerShell 5+/PowerShell 7, JSON state files, existing shell contract tests, Windows-focused PowerShell tests when `pwsh` or Windows PowerShell is available.

---

## Verification Summary

Confirmed gaps:

- `skills/run-with-it/SKILL.md` explicitly says native PowerShell must stop because `run-with-it` requires Bash-only pool/dispatcher helpers.
- `assets/sub-coordinator-prompt.md` says native Windows should use `.ps1` runners, but the current worker-monitoring contract is Bash-only and uses `run-with-it-dispatch.sh`.
- Existing PowerShell snippets in `assets/sub-coordinator-prompt.md` call `run-agent.ps1` directly, use old scattered paths such as `.run-with-it\complexity` and `.run-with-it\done`, and do not pass `RUN_WITH_IT_STATE_FILE`.
- There is no `run-with-it-dispatch.ps1`, `run-with-it-pool.ps1`, or `worker-watch.ps1`.
- `assets/run-agent.ps1` does not read/document `RUN_WITH_IT_STATE_FILE`.
- `assets/run-agent.ps1` captures stdout/stderr into temporary files and forwards them only after the child command exits, which means a native Windows watchdog cannot observe live output activity.
- Existing tests exercise Bash contracts only; there is no PowerShell parity test suite.

## File Structure

- Create: `assets/worker-watch.ps1`
  - PowerShell equivalent of `worker-watch.sh`.
  - Inputs: `-Pid`, `-DoneFile`, `-LogFile`, `-TailStateFile`, `-TailLines`.
  - Output: `WORKER|pid=...|alive=...|done=...|log_present=...|log_tail_changed=...|tail_hash=...`.

- Create: `assets/run-with-it-dispatch.ps1`
  - PowerShell equivalent of `run-with-it-dispatch.sh`.
  - Inputs mirror Bash flags: `-Role`, `-Issue`, `-Cycle`, `-Agent`, `-Model`, `-ContextFile`, `-PromptFile`, `-LogFile`, `-DoneFile`, `-ResultFile`, `-StateFile`, `-RepoRoot`, `-IssueDir`, `-StatusFile`, `-EventsLog`, `-PollSeconds`, `-QuietSeconds`, `-StallSeconds`, `-TimeoutSeconds`, `-DryRun`, `-ValidateOnly`.
  - Launches `run-agent.ps1`, captures process id, updates `cycle-<n>.state.json`, and emits `worker-quiet`, `worker-stalled`, `dispatch-complete`, and `dispatch-failed`.

- Create: `assets/run-with-it-pool.ps1`
  - PowerShell equivalent of `run-with-it-pool.sh`.
  - Maintains the rolling Sub-Coordinator pool from `.run-with-it/main-state.json`.
  - Uses `run-with-it-dispatch.ps1`.
  - Keeps merge recovery behavior and issue-scoped artifact paths.

- Modify: `assets/run-agent.ps1`
  - Add `RUN_WITH_IT_STATE_FILE` to documented environment variables.
  - Ensure role log capture includes stdout and stderr for every worker.
  - Prefer live forwarding so watchdog log activity works during long-running workers. If true streaming is not feasible on Windows PowerShell 5, use a background process with redirected stdout/stderr to temporary stream files and a forwarder loop that tails/appends new content.

- Modify: `assets/sub-coordinator-prompt.md`
  - Replace stale native PowerShell snippets with dispatcher-based snippets using `run-with-it-dispatch.ps1`.
  - Use issue-scoped paths under `.run-with-it\issues\<n>\workers\<role>\`.
  - Pass `-StateFile`, `-QuietSeconds`, and `-StallSeconds`.
  - Persist `state_file` in `sub-state.json`.

- Modify: `assets/coordinator-rules.md`
  - State that Bash uses `.sh` dispatcher/watcher and native Windows uses `.ps1` dispatcher/watcher.
  - Keep the invariant that the Sub-Coordinator reads state/result artifacts, not raw logs.

- Modify: `assets/main-orchestrator-rules.md`
  - State that the pool runner is platform-specific: `run-with-it-pool.sh` for Unix-like shells and `run-with-it-pool.ps1` for native Windows.

- Modify: `skills/run-with-it/SKILL.md`
  - Remove the hard native-PowerShell stop once parity exists.
  - Update asset discovery to require PowerShell orchestration assets for native Windows.
  - Document platform helper selection.

- Modify: `install.sh`, `install.ps1`, and `README.md`
  - Include `run-with-it-dispatch.ps1`, `run-with-it-pool.ps1`, and `worker-watch.ps1`.
  - Clarify the supported Windows modes: native PowerShell and Git Bash/WSL.

- Add tests:
  - `tests/worker-watch-ps1.test.sh`
  - `tests/run-agent-ps1-status-bus.test.sh`
  - `tests/run-with-it-dispatch-ps1.test.sh`
  - `tests/run-with-it-routing-windows.test.sh`

PowerShell tests should skip with a clear `SKIP:` line when neither `pwsh` nor `powershell.exe` is available on the current machine.

## Implementation Tasks

### Task 1: Add Static Windows Contract Tests

- [ ] Add `tests/run-with-it-routing-windows.test.sh`.
- [ ] Assert native Windows docs do not contain contradictory direct `run-agent.ps1` worker snippets.
- [ ] Assert required assets include `run-with-it-dispatch.ps1`, `run-with-it-pool.ps1`, and `worker-watch.ps1`.
- [ ] Assert PowerShell snippets include `RUN_WITH_IT_STATE_FILE` and issue-scoped worker paths.
- [ ] Run `bash tests/run-with-it-routing-windows.test.sh`.
- [ ] Expected first run: fail because the `.ps1` orchestration assets do not exist and docs still contain stale snippets.

### Task 2: Implement `worker-watch.ps1`

- [ ] Create `assets/worker-watch.ps1`.
- [ ] Match `worker-watch.sh` output exactly enough for dispatcher parsing.
- [ ] Add `tests/worker-watch-ps1.test.sh`.
- [ ] Test alive process, missing done file, done file, missing log, changed log tail, unchanged log tail, and dead process.
- [ ] Run `bash tests/worker-watch-ps1.test.sh`.
- [ ] Expected: pass on Windows/PowerShell-capable machines; skip elsewhere.

### Task 3: Fix `run-agent.ps1` Log Capture Parity

- [ ] Add `RUN_WITH_IT_STATE_FILE` documentation and environment binding.
- [ ] Add `tests/run-agent-ps1-status-bus.test.sh`.
- [ ] Test stale done sentinel cleanup, stdout capture, stderr capture, heartbeat forwarding, role log writes, status bus writes, events log writes, done sentinel writes, and final output fragment capture.
- [ ] Update `run-agent.ps1` so output is mirrored to the role log reliably.
- [ ] Prefer live streaming; at minimum prove the dispatcher can observe log growth while the worker is running.
- [ ] Run `bash tests/run-agent-ps1-status-bus.test.sh`.

### Task 4: Implement `run-with-it-dispatch.ps1`

- [ ] Create `assets/run-with-it-dispatch.ps1`.
- [ ] Port the Bash dispatcher behavior: validation, dry-run, validate-only, issue-dir setup, status/event writes, done/result checks, JSON state writes, quiet/stalled detection, timeout status, and failed/completed transitions.
- [ ] Add `tests/run-with-it-dispatch-ps1.test.sh`.
- [ ] Include a silent-worker fixture that sleeps long enough to become `stalled`, then writes a valid result.
- [ ] Assert final state is `completed` and events log includes `STATUS|type=worker-stalled|...|reason=alive-but-silent`.

### Task 5: Implement `run-with-it-pool.ps1`

- [ ] Create `assets/run-with-it-pool.ps1`.
- [ ] Port the rolling-pool behavior from `run-with-it-pool.sh`.
- [ ] Preserve `main-state.json` schema and issue-scoped paths.
- [ ] Use `run-with-it-dispatch.ps1` for Sub-Coordinators and merge recovery.
- [ ] Add a PowerShell-capable pool smoke test or extend existing pool tests with a Windows-mode fixture.

### Task 6: Update Prompts And Skill Docs

- [ ] Update `assets/sub-coordinator-prompt.md` PowerShell blocks to use `run-with-it-dispatch.ps1`.
- [ ] Remove legacy `.run-with-it\complexity`, `.run-with-it\impl`, and `.run-with-it\done` examples.
- [ ] Update `assets/coordinator-rules.md`, `assets/main-orchestrator-rules.md`, and `skills/run-with-it/SKILL.md`.
- [ ] Keep Git Bash/WSL behavior unchanged.
- [ ] Run `bash tests/run-with-it-routing.test.sh` and `bash tests/run-with-it-routing-windows.test.sh`.

### Task 7: Install And README Parity

- [ ] Update `install.sh` and `install.ps1` asset lists.
- [ ] Update manual repair snippets in `README.md`.
- [ ] Update README asset table to list `.ps1` dispatcher/pool/watch assets.
- [ ] Run `bash tests/install-assets-contract.test.sh`.

### Task 8: Full Verification

- [ ] Run all Bash tests:
  ```bash
  for test_file in tests/*.sh; do echo "==> $test_file"; bash "$test_file"; done
  ```
- [ ] On a Windows host or CI job, run the PowerShell-capable tests without skips.
- [ ] Run `git diff --check`.
- [ ] Confirm no native Windows docs still instruct users to run the Bash-only dispatcher.

## Acceptance Criteria

- Native Windows no longer has contradictory instructions.
- A native Windows Sub-Coordinator launches workers through `run-with-it-dispatch.ps1`, not direct `run-agent.ps1`.
- Worker artifacts live under `.run-with-it\issues\<n>\workers\<role>\`.
- Every worker has `.log`, `.done`, result JSON, and `.state.json`.
- The PowerShell watchdog detects crashed workers and live silent workers.
- Git Bash/WSL/macOS/Linux behavior remains unchanged.
- PowerShell tests pass on a Windows-capable runner; non-Windows machines skip PowerShell execution tests clearly.
