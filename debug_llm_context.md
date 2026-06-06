## Project Overview

Project: AI-Skills, a shell/Python skill runtime for Codex-style orchestration.

Relevant runtime: Bash on macOS/Linux for `run-with-it`; PowerShell parity exists separately.

Relevant entrypoints:

- `skills/run-with-it/SKILL.md`: human/agent workflow for orchestration.
- `assets/run-with-it-pool.sh`: Bash rolling pool supervisor.
- `assets/run-with-it-dispatch.sh`: Bash role dispatcher and worker monitor.
- `assets/run-with-it-state.py`: JSON state helper for pool and dispatch orchestration.

Relevant test commands:

- `bash tests/run-with-it-pool.test.sh`
- `bash tests/run-with-it-pool-actual-flow.test.sh`
- `bash tests/run-with-it-dispatch.test.sh`
- `bash tests/run-with-it-routing.test.sh`

## Architecture Map

The Main Orchestrator prepares `.run-with-it/main-state.json`, then Step D starts `assets/run-with-it-pool.sh`. The pool runner selects ready issues from state, spawns `assets/run-with-it-dispatch.sh` as background sub-coordinator dispatchers, captures each dispatcher PID, and monitors those PIDs until reports are finalized.

The reported failure occurs before this runtime path actually starts. The host shell tool statically validates the submitted inline command and rejects a `kill` invocation whose PID argument is not a numeric literal.

## Internal Dependencies

- `skills/run-with-it/SKILL.md`: change target. Step D currently shows background pool launch and says to monitor `POOL_PID`; it should guide agents away from inline `kill -0 "$POOL_PID"` monitor loops in Codex tool calls.
- `assets/main-orchestrator-rules.md`: change target or invariant. It already says to use the platform pool runner, not a custom script; it could additionally state inline shell wrappers must not use `kill` with variable PIDs.
- `assets/run-with-it-pool.sh`: inspect/protect. Internal `kill -0 "$pid"` checks are valid Bash when run as a script, but should guard numeric PID values before calling `kill` for robustness.
- `assets/run-with-it-dispatch.sh`: inspect/protect. Internal `kill -0` checks should remain inside the script; numeric guards may be added where values can come from helper output.
- `assets/run-with-it-state.py`: validation dependency. `mark_in_progress` casts PID to int, so persisted pool issue PIDs are expected numeric.

## External Dependencies

- Codex desktop shell validator: command submissions containing `kill` must specify a numeric PID in the command text. It rejects variable-PID forms before Bash variable expansion.
- macOS/Linux shell tools: `ps -p "$PID"` can be used as a tool-safe liveness probe in inline wrappers if needed.

## Critical Call Paths

Reported failure path:

```text
Main Orchestrator Step D
-> submits inline shell command titled "Assemble contexts and run run-with-it pool"
-> command includes `set -euo pipefail`, pool env setup, and a PID monitor
-> inline monitor uses `kill -0 "$POOL_PID"` or another variable PID
-> host shell validator sees `kill` without a numeric PID literal
-> command is rejected before Bash executes
```

Normal desired runtime path:

```text
Main Orchestrator
-> invokes `assets/run-with-it-pool.sh`
-> pool runner spawns dispatchers
-> pool runner stores numeric PIDs through `assets/run-with-it-state.py mark-in-progress`
-> pool runner monitors and finalizes issues
```

## Fault Surface Inventory

- `skills/run-with-it/SKILL.md:415`: risk medium, confidence medium. The documented Bash launch pattern uses background `nohup` and `POOL_PID=$!`; subsequent monitoring guidance may cause an agent to synthesize an inline `kill -0 "$POOL_PID"` loop.
- `skills/run-with-it/SKILL.md:448`: risk medium, confidence medium. It says to monitor the single process but does not specify a tool-safe monitor method.
- `assets/main-orchestrator-rules.md:42-44`: risk medium, confidence medium. It tells shell watchers to poll status and worker watcher diagnostics; it can be clarified to avoid inline variable-PID `kill`.
- `assets/run-with-it-pool.sh:344-345`: risk low for the reported failure, confidence high. Internal script liveness check uses variable PID. It is not the immediate pre-execution failure, but numeric guard would improve robustness.
- `assets/run-with-it-dispatch.sh:423`, `:528`, `:534`: risk low for the reported failure, confidence high. Internal script liveness checks use variable PIDs.

## Implementation Approach

Preferred fix: update the run-with-it orchestration instructions so Codex tool submissions do not inline `kill` with variable PIDs. Step D should either run `run-with-it-pool.sh` in the foreground as the long-lived shell session, or background it and use `wait "$POOL_PID"` plus status-file polling without `kill`. If liveness polling is needed in inline shell, use `ps -p "$POOL_PID" >/dev/null 2>&1`.

Also add numeric PID guards in Bash scripts before internal `kill -0` calls:

- If PID is empty or contains non-digits, treat the process as not alive and finalize/fail through existing artifact paths.
- Do not call `kill` with an empty or non-numeric PID.

Rejected alternative: requesting broader permission for variable-PID `kill` is not preferred because it depends on host policy and makes future agent-generated inline commands brittle.

## File-by-File Change Guide

`skills/run-with-it/SKILL.md`

- Target: Step D Bash section around lines 413-449.
- Current behavior: documents background pool launch and `POOL_PID=$!`, then says to monitor that process.
- Required behavior: explicitly forbid inline `kill -0 "$POOL_PID"` in Codex shell submissions. Prefer running the shared pool runner directly as the long-lived command, or use `ps -p "$POOL_PID"`/`wait "$POOL_PID"` for wrapper monitoring.
- Tests: update `tests/run-with-it-routing.test.sh` to assert the instruction mentions tool-safe monitoring and does not encourage inline variable-PID `kill`.

`assets/main-orchestrator-rules.md`

- Target: Spawning Rules and Live Status Rules.
- Current behavior: requires the shared pool runner and status polling.
- Required behavior: add a rule that shell command submissions must not include `kill` with variable PIDs; liveness checks belong inside platform scripts or must use tool-safe probes.
- Tests: update `tests/run-with-it-routing.test.sh`.

`assets/run-with-it-pool.sh`

- Target: monitor loop around `pid="$(pool_get PID "$issue")"` and `kill -0 "$pid"`.
- Current behavior: calls `kill -0` on whatever `pool_get` returns.
- Required behavior: only call `kill -0` when PID is non-empty and numeric. If invalid, skip `wait "$pid"` and finalize the issue as a failed/blocked dispatch through existing report handling.
- Tests: add a unit case in `tests/run-with-it-pool.test.sh` or helper-level test that an invalid/empty pool PID does not execute `kill` and produces deterministic handling.

`assets/run-with-it-dispatch.sh`

- Target: detached PID bootstrap and worker PID liveness checks.
- Current behavior: calls `kill -0` on `detached_pid` and `pid`.
- Required behavior: validate PID shape before `kill`; invalid detached PID should emit `dispatch-bootstrap-failed`, invalid worker PID should emit `dispatch-failed`.
- Tests: extend `tests/run-with-it-dispatch.test.sh`.

## What NOT to change

- Do not replace `run-with-it-pool.sh` with custom Main Orchestrator inline rolling-pool logic.
- Do not remove the platform dispatcher contract.
- Do not change `.run-with-it/main-state.json` schema except adding optional diagnostic fields if needed.
- Do not change PowerShell behavior unless adding equivalent documentation or parity tests.
- Do not weaken the rule that Sub-Coordinators are not killed/restarted mid-batch by the Main Orchestrator.

## Constraints

- Must work in Codex desktop shell tool where inline `kill` with variable PID can be rejected before execution.
- Must preserve Bash/macOS/Linux support.
- Must preserve existing status bus files: `.run-with-it/status/current.txt` and `.run-with-it/status/events.log`.
- Must preserve the shared pool runner as the single rolling-pool supervisor.

## Dependencies & Libraries

- Bash scripts: `assets/run-with-it-pool.sh`, `assets/run-with-it-dispatch.sh`, `assets/worker-watch.sh`.
- Python helper: `assets/run-with-it-state.py`.
- No new third-party libraries are needed.

## Test files to update

- `tests/run-with-it-routing.test.sh`: assert instructions avoid inline variable-PID `kill` and recommend tool-safe monitoring.
- `tests/run-with-it-pool.test.sh`: add invalid/empty pool PID guard coverage if script-level changes are made.
- `tests/run-with-it-dispatch.test.sh`: add invalid detached/worker PID guard coverage if script-level changes are made.

## Acceptance Criteria

- A Codex shell command for Step D can be submitted without being rejected by the host validator.
- The submitted inline command does not contain `kill -0 "$POOL_PID"` or any other `kill` invocation with a variable PID.
- `run-with-it-pool.sh` remains the rolling-pool supervisor.
- Existing pool and dispatch tests pass.
- New tests prove documentation and any PID guards prevent empty/non-numeric PID `kill` calls.
