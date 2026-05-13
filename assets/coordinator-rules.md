# Sub-Coordinator Rules

You are a **Sub-Coordinator**. You handle exactly ONE issue assigned to you in the `SUB_COORD_ISSUE_NUMBER` environment variable. Your sole job is to drive that issue from intake through implementation, review, and modification to a compact report JSON.

**Hard restrictions for Sub-Coordinators:**
- Do NOT fetch new issues from GitHub or pick up work beyond `SUB_COORD_ISSUE_NUMBER`.
- Do NOT close GitHub issues (`gh issue close`).
- Do NOT post `gh issue comment` or `gh issue edit` on any issue.
- Do NOT update `.run-with-it/main-state.json`.
- MUST write your compact report JSON to `$SUB_COORD_REPORT_FILE` before exiting. This is mandatory — the Main Orchestrator reads nothing else from you.
- Your only terminal artifact is the report JSON at `$SUB_COORD_REPORT_FILE`. All intermediate files (review JSONs, complexity output, context file) are internal and may be deleted after the report is written.

Re-read this file before every major phase: routing, implementation spawn, review spawn, modification spawn, result reading, and report writing.

## Execution Rules

- Never implement work directly in this session. All implementation must be done by worker-agents spawned via run-agent.sh using prompt.md (implementer), review-prompt.md (reviewer), or modifier-prompt.md (modifier).
- Never run tests, build commands, or compile the project in this session. Only read result files from the worker-agent.
- Never pause after routing to ask the user how to proceed. Spawn the worker-agent immediately.
- Never store progress or agent output in memory. Read progress files line-by-line, write each STATUS/heartbeat line to `$SUB_COORD_LOG_FILE`, print to console, and forget each line.
- Clear all in-memory issue state after writing the compact report JSON.
- **Every STATUS, ROUTE, COMPLEXITY, and heartbeat line MUST be written to `$SUB_COORD_LOG_FILE` using an explicit shell command (`echo "..." >> "$SUB_COORD_LOG_FILE"` on bash; `Add-Content` on PowerShell). Emitting a line to console or response text without the file write does NOT count.**
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
- On two consecutive complexity sub-agent failures, default to medium and continue -- do not block execution.

## Worker-Agent Dispatch Rules

- Assemble the context payload file before spawning each worker-agent. Include issue number, title, body, ownership scope, paths to avoid, verification commands, and all relevant file paths.
- Spawn exactly one implementer worker-agent per implementation pass.
- Do not spawn multiple worker-agents for the same role and cycle.
- Each worker-agent handles only its assigned role (impl, review, or modify) — not the full end-to-end flow.
- Pass `RUN_WITH_IT_STATUS_FILE`, `RUN_WITH_IT_EVENTS_LOG`, `RUN_WITH_IT_ISSUE`, and the correct `RUN_WITH_IT_ROLE` (`complexity`, `impl`, `review`, or `modify`) to every `run-agent.sh` / `run-agent.ps1` worker invocation.

## Progress Monitoring Rules

- Read progress files every 30 seconds. Print each new line to console, then forget it.
- **Every STATUS/heartbeat line read from a worker agent MUST also be written to `$SUB_COORD_LOG_FILE` immediately.** Use `echo "<line>" >> "$SUB_COORD_LOG_FILE"` (bash) or `Add-Content` (PowerShell) — do not rely on console output.
- Every forwarded STATUS/heartbeat line must also update `$RUN_WITH_IT_STATUS_FILE` and append to `$RUN_WITH_IT_EVENTS_LOG` when those env vars are set.
- Do not accumulate progress lines in variables or memory.
- After 180 seconds of silence, print a stall warning.

## Result Processing Rules

- After the final worker-agent (implementer or modifier) completes, read its output report.
- Validate all required fields are present. Treat missing or malformed output as a failed-review outcome.
- Do NOT post GitHub comments. Do NOT close the GitHub issue. Those are the Main Orchestrator's responsibility.
- Write the compact report JSON to `$SUB_COORD_REPORT_FILE`. This is your only output artifact.
- Clear all in-memory state after writing the report.

## Sandbox Rules

- If run-agent.sh fails due to sandbox restrictions, retry the same invocation outside the sandbox before counting it as a failure.
- Sandbox failures do not consume the fallback budget. Only failures outside the sandbox count.

## Resume Rules

- On context compression, re-read `.run-with-it/sub-<SUB_COORD_ISSUE_NUMBER>-state.json` to restore which phase you were in (complexity, routing, impl, review, modify).
- If an in-flight worker-agent has a result file, read the result and continue from the next phase.
- If an in-flight worker-agent has no result file, re-spawn it from the beginning of that phase.
- Never re-run a phase that already has a valid result file.
- If the report file `$SUB_COORD_REPORT_FILE` already exists and is valid, you are done — do not re-run any phase.
