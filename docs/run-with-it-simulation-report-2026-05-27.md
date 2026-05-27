# run-with-it simulation report - 2026-05-27

## Scope

Simulation ID: `sim-20260527195554`

Simulation worktree: `/Users/chanakya/projects/AI-Skills-sim-20260527195554`

Source runtime used: `/Users/chanakya/projects/AI-Skills/assets` via `ASSET_ROOT`/`ASSETS_DEST`, so the run used the modified checkout files rather than only the installed copies.

GitHub issues created:

- `#109` `[run-with-it simulation sim-20260527195554] Add alpha fixture` - https://github.com/chanakya-net/AI-Skills/issues/109
- `#110` `[run-with-it simulation sim-20260527195554] Add bravo fixture` - https://github.com/chanakya-net/AI-Skills/issues/110
- `#111` `[run-with-it simulation sim-20260527195554] Add fixture index` - https://github.com/chanakya-net/AI-Skills/issues/111, dependent on `#109`

Feature branch: `run-with-it/sim-20260527195554`

## Initial failure

The first pool run started `#109` and `#110` with `parallel_jobs=2`. Issue `#110` completed and closed, but issue `#109` was finalized as blocked.

Primary evidence:

- Main log: `/Users/chanakya/projects/AI-Skills-sim-20260527195554/.run-with-it/main/main.log`
- Preserved failed issue snapshot: `/Users/chanakya/projects/AI-Skills-sim-20260527195554/.run-with-it/issues/109-stalled-before-fix`
- Complexity worker state: `/Users/chanakya/projects/AI-Skills-sim-20260527195554/.run-with-it/issues/109-stalled-before-fix/workers/complexity/cycle-1.state.json`
- Stalled sub-coordinator log: `/Users/chanakya/projects/AI-Skills-sim-20260527195554/.run-with-it/issues/109-stalled-before-fix/sub-coordinator.log`

Observed state for the stalled complexity worker:

```json
{
  "issue": "109",
  "role": "complexity",
  "cycle": "1",
  "state": "stalled",
  "runner_pid": 14075,
  "alive": true,
  "done": false,
  "result_present": false,
  "stall_reason": "alive-but-silent"
}
```

Important log lines:

- `STATUS|type=worker-quiet|issue=109|role=complexity|cycle=1|reason=alive-but-quiet|silence_seconds=122`
- `STATUS|type=worker-stalled|issue=109|role=complexity|cycle=1|reason=alive-but-silent|silence_seconds=302`
- `ERROR codex_core::tools::router: error=write_stdin failed: stdin is closed for this session; rerun exec_command with tty=true to keep stdin open`
- `STATUS|type=worker-stalled|issue=109|role=sub-coord|reason=alive-but-silent|silence_seconds=301`

Concrete cause: this was not the earlier bootstrap failure where a background worker died before a runner PID was recorded. The worker had a real PID and stayed alive, but it produced no done sentinel and no result artifact. The dispatcher marked the worker as stalled but did not fail the safe role, so the sub-coordinator kept waiting until it also became stalled. The `write_stdin failed` error is a symptom of Codex trying to write to an already closed session while the orchestration was wedged around the silent worker.

## Fix applied

The Bash dispatcher now treats selected stalled roles as terminal infrastructure failures instead of leaving the sub-coordinator to wait indefinitely.

Changed behavior:

- `RUN_WITH_IT_AUTO_FAIL_STALLED_ROLES` defaults to `complexity`.
- When a listed role reaches `state=stalled`, the dispatcher emits `STATUS|type=worker-stall-timeout`.
- The runner process tree is terminated recursively.
- The worker state is rewritten to `failed` with `exit_code=124` and `stall_reason=alive-but-silent`.
- The dispatcher emits `STATUS|type=dispatch-failed`, allowing the sub-coordinator to apply fallback logic.

Related modified files exercised in this run/check:

- `assets/run-with-it-dispatch.sh`
- `assets/run-with-it-artifacts.py`
- `assets/run-with-it-state.py`
- `assets/coordinator-rules.md`
- `assets/sub-coordinator-prompt.md`
- `skills/run-with-it/SKILL.md`
- `tests/run-with-it-dispatch.test.sh`
- `tests/run-with-it-dispatch-ps1.test.sh`
- `tests/run-with-it-routing.test.sh`
- `tests/run-with-it-helpers.test.sh`

Additional reporting fix: compact issue reports may include detailed `files_modified` entries without top-level `files_modified_count`, `lines_added`, or `lines_deleted`. `assets/run-with-it-state.py` now derives those aggregate fields from `files_modified` so future `completed_summaries` do not record false zeroes.

## Rerun result

After applying the stall-timeout fix and resetting only issues `#109` and `#111`, the rerun completed:

- `#109` completed and closed.
- `#111` spawned only after `#109` completed, proving the dependency gate worked.
- `#111` completed and closed.
- The pool emitted `STATUS|type=pool-empty`.

Rerun main log: `/Users/chanakya/projects/AI-Skills-sim-20260527195554/.run-with-it/main/main-rerun.log`

Final issue states:

```json
{
  "active_pool_issues": [],
  "statuses": {
    "109": "completed",
    "110": "completed",
    "111": "completed"
  },
  "github_update_status": {
    "109": "updated",
    "110": "updated",
    "111": "updated"
  }
}
```

Generated simulation files on the feature branch:

- `docs/run-with-it-simulation/alpha.md`
- `docs/run-with-it-simulation/bravo.md`
- `docs/run-with-it-simulation/index.md`

Verification run after the fix:

```text
bash tests/run-with-it-helpers.test.sh
for t in tests/run-with-it-*.test.sh tests/run-agent*.test.sh; do bash "$t"; done
```

Both checks passed.
