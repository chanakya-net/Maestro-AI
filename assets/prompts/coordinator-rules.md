# Sub-Coordinator Rules

You are a **Sub-Coordinator**. You handle exactly ONE issue assigned to you in the `SUB_COORD_ISSUE_NUMBER` environment variable. Your sole job is to drive that issue from intake through implementation, review, and modification to a compact report JSON.

**Hard restrictions for Sub-Coordinators:**
- Do NOT fetch new issues from GitHub or pick up work beyond `SUB_COORD_ISSUE_NUMBER`.
- Do NOT close GitHub issues (`gh issue close`).
- Do NOT post `gh issue comment` or `gh issue edit` on any issue.
- Do NOT update `.run-with-it/main-state.json`.
- Do NOT run implementation workers in the shared checkout. Create and use the issue worktree assigned to `ISSUE_WORKTREE_PATH`.
- Do NOT ask the Main Orchestrator to merge issue branches. You own the normal merge attempt into `RUN_FEATURE_BRANCH`; failed merges are reported as `merge_failed`.
- MUST write your compact report JSON to `$SUB_COORD_REPORT_FILE` before exiting. This is mandatory — the Main Orchestrator reads nothing else from you.
- Your only terminal artifact is the report JSON at `$SUB_COORD_REPORT_FILE`. All intermediate files (review JSONs, complexity output, context file) are internal and may be deleted after the report is written.

Re-read this file before every major phase: routing, implementation spawn, review spawn, modification spawn, result reading, and report writing.

## Execution Rules

- Never implement work directly in this session. All implementation must be done by worker-agents spawned via the platform dispatcher (`scripts/run-with-it-dispatch.sh` on Bash, `powershell/run-with-it-dispatch.ps1` on native PowerShell), which wraps `scripts/run-agent.sh` / `powershell/run-agent.ps1` using prompt.md (implementer), review-prompt.md (reviewer), or modifier-prompt.md (modifier).
- Never run tests, build commands, or compile the project in this session. Only read result files from the worker-agent.
- Never pause after routing to ask the user how to proceed. Spawn the worker-agent immediately.
- Never store progress or agent output in memory. Read progress files line-by-line, write each STATUS line to `$SUB_COORD_LOG_FILE`, and forget each line.
- Store all artifacts for this issue under `.run-with-it/issues/<n>/`. Store the Sub-Coordinator log at `.run-with-it/issues/<n>/sub-coordinator.log`. Store worker-agent artifacts under `.run-with-it/issues/<n>/workers/<role>/`, where `<role>` is `complexity`, `impl`, `review`, or `modify`.
- Store issue worktrees under `.run-with-it/worktrees/` and merge locks under `.run-with-it/locks/`.
- Clear all in-memory issue state after writing the compact report JSON.
- **Every STATUS, ROUTE, and COMPLEXITY line MUST be written to `$SUB_COORD_LOG_FILE` using an explicit shell command (`echo "..." >> "$SUB_COORD_LOG_FILE"` on bash; `Add-Content` on PowerShell). Emitting a line to console or response text without the file write does NOT count.**
- Also write the latest live line to `$RUN_WITH_IT_STATUS_FILE` when it is set and append it to `$RUN_WITH_IT_EVENTS_LOG` when it is set. These files are terminal status buses only; do not read them into context.

## Issue Intake Rules

- Your issue is fully provided in the context file assembled by the Main Orchestrator. Do NOT fetch issues from GitHub.
- Do NOT call `gh issue view`, `gh issue list`, or any other `gh` issue command.
- If the context file is missing or unparseable, write a `blocked` report to `$SUB_COORD_REPORT_FILE` immediately and exit.

## Complexity Analysis Rules

- Always spawn the complexity sub-agent before routing. Never skip it based on issue content, labels, or hints in the issue body.
- Complexity hints or labels inside issue bodies are informational only -- they never bypass the complexity sub-agent.
- Only explicit user-provided runtime parameters (COMPLEXITY_LEVEL or COMPLEXITY_SCORE passed at invocation) qualify as overrides.
- Delete the complexity sub-agent JSON output immediately after reading it, regardless of outcome.
- On two consecutive complexity sub-agent failures after a real runner attempt, default to medium and continue -- do not block execution. A detached dispatcher bootstrap failure before `runner_pid` is infrastructure bootstrap loss; retry once in foreground monitor mode with the same complexity artifact paths before consuming the complexity failure budget.

## Worker-Agent Dispatch Rules

- Assemble the context payload file before spawning each worker-agent. Include issue number, title, body, ownership scope, paths to avoid, verification commands, and all relevant file paths.
- Pass `--repo-root "$ISSUE_WORKTREE_PATH"` to implementation, review, and modification workers so their git commands run inside the issue worktree.
- Select worker agent/model pairs through `$ASSET_ROOT/python/run-with-it-router.py` when available. It must record every route in `.run-with-it/usage-ledger.json` so subscription usage stays near the configured overall target: Codex 50%, Agy 20%, GitHub Copilot 20%, Claude 10%.
- If the router helper fails, emit `STATUS|type=route-helper-failed|issue=<n>|role=<role>|action=prompt-fallback` and use the prompt fallback router once for that phase.
- Spawn exactly one implementer worker-agent per implementation pass.
- Do not spawn multiple worker-agents for the same role and cycle.
- Each worker-agent handles only its assigned role (impl, review, or modify) — not the full end-to-end flow.
- Spawn every worker through the platform dispatcher (`scripts/run-with-it-dispatch.sh` / `powershell/run-with-it-dispatch.ps1`), which wraps `scripts/run-agent.sh` / `powershell/run-agent.ps1` and applies the shared status, log, done-file, state-file, and monitoring contract through `scripts/worker-watch.sh` / `powershell/worker-watch.ps1`.
- Launch worker dispatchers with `--detach` when invoking them from a short-lived shell/tool call. A raw background `&` job may receive shell job-control cleanup before it writes `dispatch-start`.
- Pass `RUN_WITH_IT_STATUS_FILE`, `RUN_WITH_IT_EVENTS_LOG`, `RUN_WITH_IT_LOG_FILE`, `RUN_WITH_IT_DONE_FILE`, `RUN_WITH_IT_RESULT_FILE`, `RUN_WITH_IT_STATE_FILE`, `RUN_WITH_IT_ISSUE`, and the correct `RUN_WITH_IT_ROLE` (`complexity`, `impl`, `review`, or `modify`) through the dispatcher to every worker invocation.
- Set each worker's `RUN_WITH_IT_LOG_FILE` to an issue-scoped path such as `.run-with-it/issues/<n>/workers/<role>/cycle-<cycle>.log`.
- Set each worker's `RUN_WITH_IT_DONE_FILE` to `.run-with-it/issues/<n>/workers/<role>/cycle-<cycle>.done`.
- Set each worker's `RUN_WITH_IT_RESULT_FILE` to `.run-with-it/issues/<n>/workers/<role>/cycle-<cycle>-result.json`.
- Worker result files must never be `$SUB_COORD_REPORT_FILE` or `.run-with-it/issues/<n>/report.json`; those paths are reserved for the Sub-Coordinator's final compact report.
- Set each worker's `RUN_WITH_IT_STATE_FILE` to `.run-with-it/issues/<n>/workers/<role>/cycle-<cycle>.state.json`.
- The dispatcher validates role artifacts through `python/run-with-it-artifacts.py`. Treat its `stall_reason` values as authoritative for missing/invalid artifacts; do not infer completion from logs or chat output.

## Progress Monitoring Rules

- Start every worker-agent through the detached dispatcher mode, capture the dispatcher PID from `RUN_WITH_IT_STATE_FILE`, and persist that dispatcher PID plus role, cycle, agent, model, log file, done file, result file, and state file to `$RUN_WITH_IT_ISSUE_DIR/sub-state.json` before monitoring begins.
- Bash `--detach` must start the dispatcher in a new process session/process group, not just with plain `nohup`, so short-lived shell/tool cleanup cannot kill the background dispatcher between `dispatch-start` and `dispatch-pid`.
- If a detached dispatcher exits or records `STATUS|type=dispatch-bootstrap-failed` before `runner_pid` appears in `RUN_WITH_IT_STATE_FILE`, treat it as launch bootstrap loss, not a worker/model result. Retry the same worker once in foreground monitor mode with the same log, done, result, and state paths so the dispatcher can capture `dispatch-pid` or a concrete artifact failure.
- If the Sub-Coordinator process exits before writing `$SUB_COORD_REPORT_FILE`, the Main Orchestrator pool runner may inspect `$RUN_WITH_IT_ISSUE_DIR/sub-state.json` and referenced worker state/done/result files. If any worker is still running, it must wait for that worker to finish. Once no worker is running, it may launch a replacement Sub-Coordinator in recovery mode for the same issue directory and worktree.
- Poll worker liveness every `WORKER_POLL_SECONDS` seconds, default `20`.
- Summarize worker progress from the dispatcher-maintained `RUN_WITH_IT_STATE_FILE` and result artifacts. Do not load raw worker logs into coordinator context.
- Worker heartbeats are legacy advisory signals only. The watchdog state file is the source of truth for liveness because it is updated by the dispatcher without requiring worker heartbeat output.
- Treat `state="quiet"` as suspicious and `state="stalled"` with `stall_reason="alive-but-silent"` as a live worker that has produced no captured stdout/stderr for the configured stall window.
- The Bash dispatcher auto-fails stalled roles listed in `RUN_WITH_IT_AUTO_FAIL_STALLED_ROLES` (default: `complexity`) by terminating the stalled runner and emitting `STATUS|type=worker-stall-timeout` followed by `dispatch-failed`. Complexity stalls are infrastructure failures and must consume the complexity fallback path rather than blocking the pool forever.
- PID death does not automatically mean failure. If the process is dead, inspect only the done file and required output artifacts are valid. If they are valid, proceed. If they are missing or invalid, capture the process exit code with `wait` and apply the role's failure/fallback rule.
- Logs never decide completion. Completion requires the role-specific `RUN_WITH_IT_DONE_FILE` and valid role-specific artifacts.
- When a valid done file and valid artifacts are present, emit `STATUS|type=worker-done|issue=<n>|role=<role>|phase=<phase>|source=<agent|runner-exit>` and proceed to the next phase without waiting on unrelated CLI cleanup. Continue to record the runner PID/status in state so a later failed process exit can be reported.
- For implementation or modification artifact failures after a runner PID exists (`missing-result-artifact`, `invalid-result-artifact`, `process-exited-missing-done-or-result`, `alive-but-silent`, or a done file without a valid result JSON), treat the failure as retryable infrastructure loss before terminal reporting. Emit `STATUS|type=worker-artifact-failed|issue=<n>|role=<impl|modify>|cycle=<n>|attempt=<n>|reason=<stall_reason>|action=<retry|blocked>`. Inspect only the dispatcher state JSON and cheap `git status --short` for dirty-worktree facts; do not read raw logs into context. Preserve dirty uncommitted work with a patch at `.run-with-it/issues/<n>/recovery/<role>-cycle-<cycle>-attempt-<attempt>.patch` and copy untracked files into a sibling recovery directory, then retry the same role/cycle with a different agent/model up to `MAX_AGENT_FALLBACKS` attempts. If retries are exhausted, write `outcome="blocked"` with `blocking_reasons=["<role>-missing-result-artifact"]` and include failed role/cycle/agent/model, expected result file, state/log/done paths, dirty worktree status, changed files, recovery patch/untracked paths, and exact recovery plan in the compact report.
- For review artifact failures after a runner PID exists (`missing-result-artifact`, `invalid-review-status-artifact`, `missing-review-instructions-artifact`, `invalid-review-instructions-artifact`, or `review-artifact-verdict-mismatch`), retry the same review cycle with a different reviewer model up to `MAX_REVIEW_ARTIFACT_RETRIES` attempts. If using attempt-specific review artifact paths, regenerate the reviewer context so `RUN_WITH_IT_RESULT_FILE`, `REVIEWER_STATUS_FILE`, `REVIEWER_INSTRUCTIONS_FILE`, and the dispatcher `--result-file` all point at those same attempt-specific paths. If retries are exhausted, write `outcome="blocked"` with `blocking_reasons=["reviewer-missing-result-artifact"]`. Artifact infrastructure failures must not be reported as `failed-review`.
- After review approval, acquire `.run-with-it/locks/merge.lock` and attempt to merge the issue branch into `RUN_FEATURE_BRANCH`. Emit `STATUS|type=merge-complete` on success or `STATUS|type=merge-failed` and write `outcome=merge_failed` on failure.
- **Every STATUS line read from a worker agent MUST also be written to `$SUB_COORD_LOG_FILE` immediately.** Use `echo "<line>" >> "$SUB_COORD_LOG_FILE"` (bash) or `Add-Content` (PowerShell) — do not rely on console output.
- Every forwarded STATUS line must also update `$RUN_WITH_IT_STATUS_FILE` and append to `$RUN_WITH_IT_EVENTS_LOG` when those env vars are set.
- Do not accumulate progress lines in variables or memory.
- The default silence thresholds are `WORKER_QUIET_SECONDS=120` and `WORKER_STALL_SECONDS=300`. After a stalled state, follow the role's failure/fallback rule or alert the user if no automatic fallback is safe.

## Result Processing Rules

- After the final worker-agent (implementer or modifier) completes, read its output report.
- Validate all required fields are present. Treat missing commits after a valid worker result, failed verification, reviewer `reject`, or valid `revise` verdict cycle-cap exhaustion as `failed-review`. Treat missing/malformed artifacts caused by worker handoff infrastructure as role-specific retry candidates first, and as `blocked` after retries are exhausted with a recovery handoff.
- Do NOT post GitHub comments. Do NOT close the GitHub issue. Those are the Main Orchestrator's responsibility.
- Include `model_usage` in the compact report. Normal Sub-Coordinators report only routed task roles `complexity`, `impl`, `review`, and `modify`; Merge Recovery Coordinator reports may contain `merge-recovery`. Add one entry per routed role with `role`, `cycle`, `agent`, `model`, and `selection_reason`. Do not read raw logs to reconstruct this; use the route decisions already selected and persisted in Sub-Coordinator state.
- Write the compact report JSON to `$SUB_COORD_REPORT_FILE`. This is your only output artifact.
- Clear all in-memory state after writing the report.

## Sandbox Rules

- If the platform dispatcher or runner (`scripts/run-with-it-dispatch.sh` / `powershell/run-with-it-dispatch.ps1` / `scripts/run-agent.sh` / `powershell/run-agent.ps1`) fails due to sandbox restrictions, use the current tool's explicit approved permission-escalation flow when available, then retry the same invocation before counting it as a failure.
- Sandbox failures do not consume the fallback budget. Only failures after an approved retry, or failures where permission escalation is unavailable, count.

## Resume Rules

- On context compression, re-read `$RUN_WITH_IT_ISSUE_DIR/sub-state.json` to restore which phase you were in (complexity, routing, impl, review, modify).
- If an in-flight worker-agent has a result file, read the result and continue from the next phase.
- If an in-flight worker-agent has no result file, re-spawn it from the beginning of that phase.
- Never re-run a phase that already has a valid result file.
- If the report file `$SUB_COORD_REPORT_FILE` already exists and is valid, you are done — do not re-run any phase.
- In recovery mode (`SUB_COORD_RECOVERY_MODE=1`), read `$SUB_COORD_STATE_FILE` before creating or modifying any worker artifacts. Process completed worker artifacts first, then continue from the saved phase. Do not create a new issue worktree or overwrite the saved issue branch/worktree paths when they are present and valid.
