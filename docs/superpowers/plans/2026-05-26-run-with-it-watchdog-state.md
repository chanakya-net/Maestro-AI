# Run With It Watchdog State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every run-with-it worker externally observable through mandatory log capture and a dispatcher-maintained watchdog state file.

**Architecture:** `run-with-it-dispatch.sh` remains the single launcher for Sub-Coordinators and workers. It captures all worker stdout/stderr through `run-agent.sh`, monitors PID plus role log activity, writes a small per-worker `cycle-<n>.state.json`, and emits quiet/stalled status lines without requiring the worker agent to remember heartbeats.

**Tech Stack:** Bash, JSON state files, existing `run-agent.sh` status bus, shell contract tests.

---

## File Structure

- Modify: `assets/run-with-it-dispatch.sh`
  - Add `--state-file`, `--quiet-seconds`, and `--stall-seconds`.
  - Default state file to the role log path with `.state.json`.
  - Write atomic state JSON on ready, running, quiet, stalled, completed, and failed transitions.
  - Monitor role log size/mtime as the objective activity signal; any stdout or stderr counts as progress.

- Modify: `assets/run-agent.sh`
  - Ensure the stdout/stderr forwarders also capture a final unterminated line fragment.

- Modify: `assets/coordinator-rules.md`
  - Document dispatcher-maintained worker state files.
  - Make worker heartbeats advisory, not authoritative.
  - Require Sub-Coordinators to read only state/result artifacts, not raw worker logs.

- Modify: `assets/sub-coordinator-prompt.md`
  - Add `WORKER_STATE_FILE` paths to every worker launch.
  - Pass `--state-file` into dispatcher invocations.
  - Persist `state_file` in `$RUN_WITH_IT_ISSUE_DIR/sub-state.json`.

- Modify: `skills/run-with-it/SKILL.md`
  - Document the state file input and dispatcher watchdog behavior.

- Modify: `tests/run-with-it-dispatch.test.sh`
  - Test dry-run and validate-only state-file handling.
  - Test completed final state.
  - Test stdout and stderr role-log capture.
  - Test a silent live worker becomes `stalled` before it eventually completes.

- Modify: `tests/run-agent-status-bus.test.sh`
  - Assert normal lines and final unterminated stdout/stderr fragments are mirrored into the role log.

- Modify: `tests/run-with-it-routing.test.sh`
  - Assert the prompt/rules document state files and silence-based watchdog behavior.

## Implementation Tasks

- [x] Write failing dispatcher tests for `--state-file`, role-log capture, and silent-worker stall detection.
- [x] Write failing runner test for final unterminated stdout/stderr capture.
- [x] Implement dispatcher state JSON writing and silence thresholds.
- [x] Update Sub-Coordinator rules and prompt snippets to pass/read worker state files.
- [x] Update run-with-it skill docs and routing contract assertions.
- [x] Run focused tests: `run-with-it-dispatch`, `run-with-it-routing`, `run-agent-status-bus`, `worker-watch`.
- [x] Run full shell test suite.

## State Contract

Each worker writes:

```text
.run-with-it/issues/<issue>/workers/<role>/cycle-<cycle>.log
.run-with-it/issues/<issue>/workers/<role>/cycle-<cycle>.done
.run-with-it/issues/<issue>/workers/<role>/cycle-<cycle>-result.json
.run-with-it/issues/<issue>/workers/<role>/cycle-<cycle>.state.json
```

The dispatcher owns `cycle-<cycle>.state.json`:

```json
{
  "schema_version": 1,
  "issue": "42",
  "role": "impl",
  "cycle": "1",
  "state": "running",
  "dispatcher_pid": 123,
  "runner_pid": 456,
  "agent": "codex",
  "model": "gpt-5.5",
  "alive": true,
  "done": false,
  "result_present": false,
  "log_present": true,
  "log_size_bytes": 1204,
  "seconds_since_last_output": 18,
  "seconds_since_last_heartbeat": 18,
  "started_at": "2026-05-26T00:00:00Z",
  "last_output_at": "2026-05-26T00:00:18Z",
  "last_heartbeat_at": "2026-05-26T00:00:18Z",
  "updated_at": "2026-05-26T00:00:36Z",
  "stall_reason": null,
  "exit_code": null
}
```

State meanings:

- `running`: PID alive and role log changed recently.
- `quiet`: PID alive, no done/result, no role-log output for `WORKER_QUIET_SECONDS`.
- `stalled`: PID alive, no done/result, no role-log output for `WORKER_STALL_SECONDS`.
- `failed`: PID dead without done/result artifacts.
- `completed`: done file and result file are present.

Completion still requires the Sub-Coordinator to validate role-specific artifacts before moving phases.
