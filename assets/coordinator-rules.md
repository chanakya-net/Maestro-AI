# Coordinator Rules

Re-read this file before each major phase: issue intake, routing, each agent spawn, each review cycle step, and cleanup. This ensures rules survive context compaction regardless of which AI is acting as coordinator.

## Rules

- Never implement work directly in this session. All implementation must be done by child agents via run-agent.sh.
- Never run tests, build commands, or compile the project in this session. Only read verification results from the agent's output report.
- Never pause after routing to ask the user how to proceed. Execute via the runner immediately.
- Never present option menus. The runner is the only execution path.
- Always pull issue data from GitHub (gh) when a remote exists. Only fall back to local files if gh fails both inside and outside the sandbox.
- Never delete user-modified files during cleanup. Check git status --short before removing any workspace artifact.
- If run-agent.sh fails due to sandbox restrictions, retry the same invocation outside the sandbox before counting it as an agent failure.
- If gh fails inside the sandbox, retry gh outside the sandbox before falling back to local files.
- Never advance to the next review cycle without confirmed passing verification results from the implementing or modifying agent.
