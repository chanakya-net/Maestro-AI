Issue summary: `run-with-it` can fail before execution when the orchestrator sends an inline shell command that contains `kill` with a variable PID.

The reported message says the command was not executed. That means the failure happened in the host command validator before Bash ran. The validator treats `kill` specially and requires at least one numeric PID in the submitted command text, so a monitor expression such as `kill -0 "$POOL_PID"` or `kill -0 "$pid"` can be rejected even though it is valid Bash once the variable is populated at runtime.

## Expected vs Actual

Expected: The Main Orchestrator should start the shared `run-with-it-pool.sh` runner, capture the pool PID, and monitor the pool until it exits or emits completion status.

Actual: The shell tool rejects the whole command before execution with:

```text
Command not executed. The 'kill' command must specify at least one numeric PID. Usage: kill <PID> or kill -9 <PID>
```

## Observed symptoms and impact

The failure blocks the pool startup step before any Sub-Coordinator is spawned. Because it happens at tool-validation time, there may be no `.run-with-it/` state transition, no pool log, and no useful Bash error output.

## Architecture and Dependency Summary

`run-with-it` is a shell/Python orchestration runtime. The Main Orchestrator prepares `.run-with-it/main-state.json`, then starts `assets/run-with-it-pool.sh`. The pool runner spawns `assets/run-with-it-dispatch.sh` processes, which in turn run agents through `assets/run-agent.sh`.

Relevant internal dependencies:

- `skills/run-with-it/SKILL.md`: documents the Main Orchestrator Step D pool startup.
- `assets/main-orchestrator-rules.md`: requires use of the shared pool runner instead of a custom rolling-pool script.
- `assets/run-with-it-pool.sh`: contains the legitimate internal process monitor loop.
- `assets/run-with-it-dispatch.sh`: contains legitimate internal worker liveness checks.
- `assets/run-with-it-state.py`: persists pool and issue state.

Relevant external dependency:

- Codex desktop shell command validator: rejects submitted `kill` command segments unless they include a numeric PID in the command text.

## Why this issue is happening

Confirmed cause: an inline shell command submitted to the tool includes a `kill` command whose PID is a shell variable, not a numeric literal. The validator rejects it before Bash can expand the variable.

Likely contributing cause: Step D documentation tells the orchestrator to launch `run-with-it-pool.sh`, capture `POOL_PID=$!`, and monitor that single process. It does not explicitly prohibit using `kill -0 "$POOL_PID"` in the inline monitor. In this environment, that pattern is blocked by tool validation.

Non-cause: the checked-in `assets/run-with-it-pool.sh` and `assets/run-with-it-dispatch.sh` do use `kill -0 "$pid"` internally, but those are inside scripts. The reported failure happened before the submitted command executed, so the immediate failure is the inline command text, not Bash reaching those script lines.

## Call Path Trace

```text
User action
-> asks run-with-it / Main Orchestrator to assemble contexts and run the pool

Shell tool submission
-> inline command starts with `set -euo pipefail`
-> command defines `ASSET_ROOT`, `STATE_FILE`, `RULES_FILE`, `PARALLEL_JOBS`, and likely starts/monitors the pool
-> command text contains a `kill` liveness check with a variable PID

Tool validation
-> validator splits command into shell segments
-> sees `kill -0 "$POOL_PID"` or equivalent
-> PID argument is not a numeric literal
-> rejects command before Bash execution

Final symptom
-> `Command not executed. The 'kill' command must specify at least one numeric PID.`
```

## Contributing factors and conditions that trigger the failure

- The command is submitted inline through the shell tool instead of being delegated entirely to the checked-in pool runner.
- The inline monitor uses `kill -0` with `$POOL_PID`, `$pid`, or an empty/unset PID variable.
- The shell tool applies static safety validation before shell variable expansion.

## Evidence table

| Evidence | Conclusion |
|---|---|
| Reported error starts with `Command not executed` | Failure happened before Bash executed. |
| `assets/run-with-it-pool.sh:344-345` uses `pid="$(pool_get PID "$issue")"` then `kill -0 "$pid"` | The shared runner has internal PID liveness checks. |
| `assets/run-with-it-pool.sh:265-270` captures `$!` immediately after background spawn and persists it | Internal pool PIDs should normally be numeric after successful spawn. |
| `assets/run-with-it-dispatch.sh:423`, `:528`, `:534` use `kill -0` for dispatcher/worker monitoring | PID liveness checks are a known orchestration pattern in this codebase. |
| `skills/run-with-it/SKILL.md:415-429` shows `nohup "$ASSET_ROOT/run-with-it-pool.sh" ... &` followed by `POOL_PID=$!` | Step D encourages pool PID capture, and later text says to monitor that process. |
| `assets/main-orchestrator-rules.md:28` says not to synthesize a rolling-pool shell script | The safe shape is to invoke the platform pool runner, not inline custom process management. |

## Confidence per cause and unresolved unknowns

- High confidence: static shell validation rejected a variable-PID `kill` before execution.
- Medium confidence: the rejected `kill` was in an inline pool monitor around `POOL_PID`, based on the reported command title and Step D documentation.
- Low confidence: an actually empty runtime PID caused the failure. The wording `Command not executed` argues against this.

Unresolved unknowns:

- The exact full inline command was not available. This is not required to identify the failure class, but it would identify the exact line to rewrite.

## Human-readable fix direction

Prefer avoiding `kill` in the inline command submitted to the tool. Let `run-with-it-pool.sh` own long-lived monitoring, or use a tool-safe liveness probe such as `ps -p "$POOL_PID"` in wrapper code. If `kill -0` remains inside checked-in scripts, guard PID values before calling it and keep those calls inside scripts rather than inline tool submissions.

## Question log

No targeted question asked. Repository evidence and the exact validator message were sufficient to identify the failure class.
