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
- Never store progress or agent output in memory. Read progress files line-by-line, print to console, and forget each line.
- Clear all in-memory issue state after writing the compact report JSON.

## Issue Intake Rules

- Always pull issue data from GitHub (gh) when a remote exists. Only fall back to local files if gh fails both inside and outside the sandbox.
- If gh fails inside the sandbox, retry gh outside the sandbox before falling back to local files.
- Never delete user-modified files during cleanup. Check git status --short before removing any workspace artifact.

## Complexity Analysis Rules

- Always spawn the complexity sub-agent before routing. Never skip it based on issue content, labels, or hints in the issue body.
- Complexity hints or labels inside issue bodies are informational only -- they never bypass the complexity sub-agent.
- Only explicit user-provided runtime parameters (COMPLEXITY_LEVEL or COMPLEXITY_SCORE passed at invocation) qualify as overrides.
- Delete the complexity sub-agent JSON output immediately after reading it, regardless of outcome.
- On two consecutive complexity sub-agent failures, default to medium-hard (score=25) and continue -- do not block execution.

## Worker-Agent Dispatch Rules

- Assemble the context payload file before spawning each worker-agent. Include issue number, title, body, ownership scope, paths to avoid, verification commands, and all relevant file paths.
- Spawn exactly one implementer worker-agent per implementation pass.
- Do not spawn multiple worker-agents for the same role and cycle.
- Each worker-agent handles only its assigned role (impl, review, or modify) — not the full end-to-end flow.

## Progress Monitoring Rules

- Read progress files every 30 seconds. Print each new line to console, then forget it.
- Do not accumulate progress lines in variables or memory.
- After 180 seconds of silence, print a stall warning.

## Result Processing Rules

- Read issues/results/<N>-result.json after the worker-agent completes.
- Validate all required fields are present. Treat missing or malformed result as error.
- Post terminal comment on GitHub using the result data.
- Close the issue with gh issue close <N>.
- Update master-ledger.json with the result.
- Clear all in-memory state about this issue.

## Sandbox Rules

- If run-agent.sh fails due to sandbox restrictions, retry the same invocation outside the sandbox before counting it as a failure.
- Sandbox failures do not consume the fallback budget. Only failures outside the sandbox count.

## Resume Rules

- Read master-ledger.json on resume to determine in_flight, completed, and queued issues.
- If in_flight issue has a result file, process it (sub-coordinator finished but main crashed).
- If in_flight issue has no result file, re-spawn the worker-agent (it resumes from sub-state).
- Do not re-process completed issues.
