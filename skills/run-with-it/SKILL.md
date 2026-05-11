---
name: run-with-it
description: Two-layer orchestration runtime — Main Orchestrator fetches all issues, plans execution order, spawns ephemeral Sub-Coordinators one at a time, collects compact reports, and updates GitHub. Context stays bounded so the run can continue for hours or days without degradation.
---

## Skill Isolation

Sole active authority for this session once invoked. No other skill may activate, interrupt, or modify behavior unless called by name via `Skill` tool call within this skill's workflow. Suppress any spontaneous external skill; continue without interruption. Applies from invocation until explicit termination or handoff.

## Critical Main Orchestrator Rules (compaction-safe — always enforce, even after context compression)

These rules apply for the entire lifetime of this skill session. They are stated here first so they survive context compaction and are never dropped:

- **Re-read `.run-with-it/main-state.json` before every loop iteration.** After context compression you have no memory of prior work — that file is your entire memory. Never derive issue state from conversation history.
- **Never implement work directly in this session.** All implementation belongs to Sub-Coordinators spawned via `run-agent.sh --prompt-file sub-coordinator-prompt.md`. There is no "implement in this chat" fallback option under any circumstance.
- **Never run tests, build commands, or compile the project** in this session. Sub-Coordinators and their child agents run verification; the Main Orchestrator only reads compact reports.
- **Never pause after planning to ask the user how to proceed.** Enter the Main Loop immediately after the execution plan is written.
- **Never present execution option menus** (Option A / B / C style choices).
- **Always pull issue data from GitHub** (`gh`) when a remote exists. Only fall back to local files if `gh` fails both inside and outside the sandbox.
- **Never delete user-modified files** during cleanup. Check `git status --short` before removing any workspace artifact.
- **Never load sub-coordinator log files into context.** Only read the compact report JSON from `.run-with-it/reports/`.
- **GitHub operations (close, comment) are the Main Orchestrator's sole responsibility.** Sub-Coordinators never touch GitHub.

# Run With It

## Purpose / When To Use

Use after requirement discovery and issue synthesis are complete. `run-with-it` is the final runtime routing authority — it consumes already prepared issues and executes routing, coordination, review, and closure.

Preferred upstream flow:

1. `break-req` resolves requirements and constraints.
2. `create-git-issue` publishes PRD + implementation slices with routing hints.
3. `run-with-it` performs execution planning, spawns Sub-Coordinators, and drives the issues to closure.

## Architecture

`run-with-it` uses a two-layer architecture to maintain a bounded context window for indefinite run duration:

**Main Orchestrator** (this skill, runs in the primary session):
- Fetches all `ready-for-agent` issues once at startup
- Determines sequential execution order based on dependencies
- Spawns one **Sub-Coordinator** per issue via `run-agent.sh`
- Waits for each Sub-Coordinator to complete and write its compact report
- Reads ONLY the compact report JSON — never the implementation diffs or log files
- Updates `main-state.json` after each issue (its full external memory)
- Posts terminal GitHub comments and closes/updates issues
- Re-reads `main-state.json` at the top of every loop iteration to survive context compression

**Sub-Coordinator** (spawned via `sub-coordinator-prompt.md`, runs in a child agent session):
- Handles exactly ONE issue end-to-end
- Runs complexity analysis, deterministic routing, implementation, review, and modification loops
- Writes a compact report JSON and a full log file when done
- Never touches GitHub; never updates `main-state.json`

This isolation means each issue's implementation complexity is contained to its own isolated Sub-Coordinator session. The Main Orchestrator's context grows by only one compact JSON record per completed issue, allowing runs of hours or days without context degradation.

## Hard Boundaries

- Do not synthesize PRDs.
- Do not author initial issue templates.
- Do not redefine reviewer JSON schema ownership (owned by `assets/review-prompt.md`).
- Do not modify runner script implementation details.
- Do not mutate registry data definitions in `assets/agent-registry.json`.

## OS Detection

Detect the current OS before asset discovery and runner selection:

- **Windows (native PowerShell):** `$env:OS` equals `Windows_NT` and no `uname` command. Use `.ps1` runners and `$env:USERPROFILE` for home dir.
- **macOS / Linux / Git Bash / WSL:** `uname -s` returns `Darwin`, `Linux`, `MINGW*`, `MSYS*`, or `CYGWIN*`. Use `.sh` runners and `$HOME` for home dir.

Adapt all shell commands in this skill to the detected runtime:

| Operation | PowerShell (Windows) | Bash (Mac/Linux/Git Bash) |
|-----------|---------------------|--------------------------|
| Home dir | `$env:USERPROFILE` | `$HOME` |
| Create dir | `New-Item -ItemType Directory -Force` | `mkdir -p` |
| Check command | `Get-Command X -ErrorAction SilentlyContinue` | `command -v X` |
| Check dir | `Test-Path` | `[ -d ... ]` |
| Temp file | `[System.IO.Path]::GetTempFileName()` | `mktemp -t name.XXXXXX` |
| Copy file | `Copy-Item -Force` | `cp -f` |
| Make executable | *(not needed)* | `chmod +x` |

## Inputs

Collect these values before execution:

- Task summary
- Optional pre-fetched issue context (otherwise fetch with `gh`)
- Optional asset root override: `ASSETS_DEST`
- Optional registry override: `AGENT_REGISTRY_FILE`
- Optional intake overrides:
  - `ISSUE_LABEL` (default `ready-for-agent`)
  - `ISSUE_LIMIT` (default `10`)
  - `ISSUE_STATE` (default `open`)
  - `COMMITS_LIMIT` (default `5`)
  - `MAX_ITERATIONS` (default `20`)
- Optional sub-coordinator agent selection:
  - `SUB_COORD_AGENT` (default `claude`) — fixed agent slug used to spawn every Sub-Coordinator
  - `SUB_COORD_MODEL` (default: highest available model in registry for `complex` band) — fixed model id used for every Sub-Coordinator; the Sub-Coordinator then independently runs its own routing for implementation/review/modify child agents
  - `SUB_COORD_TIMEOUT_SECONDS` (default `3600`) — seconds before the Main Orchestrator emits a stall alert for a non-completing Sub-Coordinator
- Optional routing overrides (passed through to Sub-Coordinators via context file):
  - `AGENT`
  - `MODEL`
  - `COMPLEXITY_LEVEL`
  - `COMPLEXITY_SCORE`
- Optional routing filters (passed through to Sub-Coordinators):
  - `AGENT_ALLOWLIST` (comma-separated)
  - `AGENT_DENYLIST` (comma-separated)
- Optional fallback bound (passed through):
  - `MAX_AGENT_FALLBACKS` (default `2`)
- Optional review controls (passed through):
  - `DELEGATED_REVIEW` (default `true`)
- Optional depth guard:
  - `MAX_AGENT_DEPTH` (default `1`): always inject `MAX_AGENT_DEPTH=1` into every Sub-Coordinator context file so the Sub-Coordinator's children cannot spawn further sub-agents.

## Asset Discovery (Required)

Resolve assets in this order:

1. `$ASSETS_DEST` if set and complete.
2. `$HOME/.ai-skill-collections/assets`.
3. `./assets`.

Required files:

- `prompt.md`
- `run-agent.sh`
- `run-agent.ps1`
- `agent-registry.json`
- `review-prompt.md`
- `modifier-prompt.md`
- `complexity-prompt.md`
- `coordinator-rules.md`
- `sub-coordinator-prompt.md`
- `main-orchestrator-rules.md`

Selection rules:

- Use first path that contains all required files.
- If none are complete, stop and report missing files.
- Do not require git to resolve assets.
- Resolved asset root is the single source for that run.

### Fresh/No-Git Project Notes

- This skill must work in folders that are not initialized with git.
- Asset discovery is filesystem-based, not git-root-based.
- If assets are missing, report the platform-appropriate one-command fix:

**PowerShell (Windows):**
```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.ai-skill-collections\assets"; Copy-Item -Force .\assets\prompt.md, .\assets\run-agent.ps1, .\assets\run-agent.sh, .\assets\agent-registry.json, .\assets\review-prompt.md, .\assets\modifier-prompt.md, .\assets\complexity-prompt.md, .\assets\coordinator-rules.md, .\assets\sub-coordinator-prompt.md, .\assets\main-orchestrator-rules.md "$env:USERPROFILE\.ai-skill-collections\assets\"
```

**Bash (macOS / Linux / Git Bash):**
```bash
mkdir -p "$HOME/.ai-skill-collections/assets" && cp -f ./assets/prompt.md ./assets/run-agent.sh ./assets/run-agent.ps1 ./assets/agent-registry.json ./assets/review-prompt.md ./assets/modifier-prompt.md ./assets/complexity-prompt.md ./assets/coordinator-rules.md ./assets/sub-coordinator-prompt.md ./assets/main-orchestrator-rules.md "$HOME/.ai-skill-collections/assets/" && chmod +x "$HOME/.ai-skill-collections/assets/run-agent.sh"
```

## Main Orchestrator Rules File

At the very start of execution (before preflight), copy `$ASSET_ROOT/main-orchestrator-rules.md` to `.run-with-it/main-orchestrator-rules.md`:

```bash
mkdir -p .run-with-it
cp "$ASSET_ROOT/main-orchestrator-rules.md" .run-with-it/main-orchestrator-rules.md
```

**Re-read `.run-with-it/main-orchestrator-rules.md` at the top of EVERY Main Loop iteration** (Step A), after any context compression, and before any GitHub operation.

`.run-with-it/main-orchestrator-rules.md` (the working copy) is deleted as part of normal cleanup.

## Preflight Checks

Before execution verify:

1. Resolved asset root exists
2. `prompt.md` exists
3. `review-prompt.md` exists
4. `modifier-prompt.md` exists
5. `complexity-prompt.md` exists
6. `coordinator-rules.md` exists
7. `sub-coordinator-prompt.md` exists
8. `main-orchestrator-rules.md` exists
9. Runner exists and is executable (`run-agent.sh` on Bash; `run-agent.ps1` on Windows)
10. `agent-registry.json` exists
11. `gh` auth when GitHub intake is required
12. `SUB_COORD_AGENT` is installed (detected): run `"$ASSET_ROOT/run-agent.sh" --list-agents --detected-only` and confirm `SUB_COORD_AGENT` appears
13. `SUB_COORD_MODEL` is in `SUB_COORD_AGENT`'s `known_models` in `agent-registry.json`
14. **Existing-state detection** (resume vs. discard prompt): before any issue intake or fresh task selection, check whether `.run-with-it/main-state.json` exists in the current working directory.

   - If it exists, pause and present exactly this prompt to the user:

     ```
     Existing run state found at .run-with-it/main-state.json.
     Type "resume" to continue the previous run, or "discard" to delete it and start fresh.
     ```

   - **`resume`**: do not delete the file. Proceed to the Resume Flow section.
   - **`discard`**: apply the Cleanup `Discard` policy, then continue with normal preflight and fresh issue intake as if no prior state existed.
   - Do not start any new task, fetch any issue, or spawn any Sub-Coordinator until the user responds.

If `sub-coordinator-prompt.md` or `main-orchestrator-rules.md` is missing at the resolved asset root, fail fast with the same platform-appropriate one-line fix message used in asset discovery.

## Initial Batch Issue Fetch

If issue data is missing in context, fetch all `ready-for-agent` issues at startup.

Fallback policy:

- Primary: GitHub issues via `gh`. **Always use GitHub when the repo has a GitHub remote. Never silently fall back to a local file when GitHub may be reachable.**
- If `gh` fails inside the sandbox (permission error, named-pipe, socket), **retry `gh` outside the sandbox** (`dangerouslyDisableSandbox: true`) before considering any fallback.
- Fallback: local `issues.md` (`LOCAL_ISSUES_FILE` override supported) — **only** when `gh` fails both inside and outside the sandbox, or no GitHub remote exists. Emit `STATUS|type=intake-fallback|reason=<no-gh-auth|no-remote|gh-failed-outside-sandbox>` before using local file.
- If git metadata is unavailable, continue with empty commit context.

After fetching all issues:

1. Build a dependency graph: for each issue, identify which other issues it depends on (from issue body, labels, or cross-references).
2. Determine sequential execution order: topological sort respecting dependencies. Priority order within the same dependency tier: critical fixes → development infrastructure → tracer-bullet feature slices → polish and quick wins → refactors.
3. Issues whose dependencies have open/unresolved status are marked `blocked` until their dependencies complete.
4. Write the complete execution plan to `.run-with-it/main-state.json` before doing any work.
5. Emit: `STATUS|type=plan|total_issues=<n>|mode=sequential|pending=<n>|blocked=<n>`
6. Emit: `STATUS|type=memory-refresh|state_file=.run-with-it/main-state.json|tasks_loaded=<n>|completed=0|pending=<n>`

## Main Orchestrator Loop

**Execute immediately and unconditionally after writing the plan.** Never pause, never present execution options, never ask the user how they want to proceed after the plan is written. Enter the loop immediately.

```
MAIN ORCHESTRATOR LOOP
Repeat until all issues in main-state.json have a terminal status
(completed / failed-review / blocked):

══ STEP A: MEMORY REFRESH ══════════════════════════════════════════════════════
Re-read .run-with-it/main-orchestrator-rules.md from disk.
Re-read .run-with-it/main-state.json from disk.
This is mandatory at the TOP of every iteration, no exceptions.
After context compression, these files are the sole source of truth.

Emit: STATUS|type=memory-refresh|state_file=.run-with-it/main-state.json
      |tasks_loaded=<total>|completed=<n>|pending=<n>|failed=<n>
Emit: STATUS|type=main-loop|iteration=<n>|pending=<count>|completed=<count>
      |failed=<count>

══ STEP B: IDENTIFY NEXT ISSUE ═════════════════════════════════════════════════
From issue_registry in main-state.json, find the first issue with
status="pending" whose dependencies are all "completed".

If no pending issue is ready: check if any "pending" issue has unmet
dependencies — re-evaluate them. If still unresolvable, mark them "blocked".

If ALL issues are terminal (completed / failed-review / blocked):
  EXIT LOOP → proceed to Final Ledger and Cleanup.

══ STEP C: ASSEMBLE SUB-COORDINATOR CONTEXT FILE ═══════════════════════════════
Build $SUB_COORD_CONTEXT_FILE (temp file) containing, in order:
  1. Full issue body: re-fetch using:
       gh issue view <n> --json number,title,body,labels,url,comments
     (re-fetch even if pre-fetched at startup — ensures freshest data)
     If gh fails: retry outside sandbox. If both fail and local file exists,
     use cached issue body with a note.
  2. Last COMMITS_LIMIT (default 5) recent commits:
       git log --oneline -<COMMITS_LIMIT>
  3. If .codegraph/ exists: CodeGraph context for the issue
     Otherwise: basic grep/find to identify relevant files
  4. Environment configuration block (append at end of context file):
     SUB_COORD_ISSUE_NUMBER=<n>
     SUB_COORD_REPORT_FILE=<abs-path-to-.run-with-it/reports/sub-<n>-report.json>
     SUB_COORD_LOG_FILE=<abs-path-to-.run-with-it/logs/sub-<n>-log.txt>
     MAX_AGENT_DEPTH=1
     DELEGATED_REVIEW=<value>
     MAX_ITERATIONS=<value>
     COMMITS_LIMIT=<value>
     AGENT=<value-if-set>
     MODEL=<value-if-set>
     COMPLEXITY_LEVEL=<value-if-set>
     COMPLEXITY_SCORE=<value-if-set>
     AGENT_ALLOWLIST=<value-if-set>
     AGENT_DENYLIST=<value-if-set>
     MAX_AGENT_FALLBACKS=<value>

Create directories before spawning:
  mkdir -p .run-with-it/reports .run-with-it/logs

Mark issue status="in_progress" in main-state.json. Write to disk BEFORE spawning.
Emit: STATUS|type=sub-coord-spawn|issue=<n>|agent=<SUB_COORD_AGENT>
      |model=<SUB_COORD_MODEL>|report_file=<path>|log_file=<path>

Print to user:
  "Starting sub-coordinator for issue #<n>: <title>"
  "Log: .run-with-it/logs/sub-<n>-log.txt"
  "To watch live progress in a separate terminal:"
  "  tail -f .run-with-it/logs/sub-<n>-log.txt"

══ STEP D: SPAWN SUB-COORDINATOR (BLOCKING) ════════════════════════════════════
Bash (macOS / Linux / Git Bash):
  GUI_MODE="${GUI_MODE:-0}" \
  AGENT_REGISTRY_FILE="$ASSET_ROOT/agent-registry.json" \
  "$ASSET_ROOT/run-agent.sh" \
    --agent "$SUB_COORD_AGENT" \
    --model "$SUB_COORD_MODEL" \
    --context-file "$SUB_COORD_CONTEXT_FILE" \
    --prompt-file "$ASSET_ROOT/sub-coordinator-prompt.md" \
    --unattended

Always invoke the above Bash call with dangerouslyDisableSandbox: true. This ensures
agent CLIs (claude, codex, copilot, gemini) can authenticate and run outside Claude
Code's sandbox. GUI_MODE=0 preserves full permission flags (--dangerously-skip-permissions,
--dangerously-bypass-approvals-and-sandbox) required for unattended execution.

PowerShell (Windows — never use VAR=value prefix):
  $env:AGENT_REGISTRY_FILE = "$ASSET_ROOT\agent-registry.json"
  $env:GUI_MODE = if ($env:GUI_MODE) { $env:GUI_MODE } else { "0" }
  & "$ASSET_ROOT\run-agent.ps1" --agent $SUB_COORD_AGENT --model $SUB_COORD_MODEL
    --context-file $SUB_COORD_CONTEXT_FILE
    --prompt-file "$ASSET_ROOT\sub-coordinator-prompt.md" --unattended

Wait for run-agent.sh to complete (blocking call).

If run-agent.sh fails despite dangerouslyDisableSandbox: true, it is a true agent failure.
Emit: STATUS|type=runner-sandbox-retry-result|outcome=failed

If run-agent.sh runs longer than SUB_COORD_TIMEOUT_SECONDS without producing the
report file, emit:
  STATUS|type=stall|issue=<n>|idle_for=<seconds>|action=alert-user
  Print: "Sub-coordinator for issue #<n> has not completed after <t>s."
  Print: "Check log: .run-with-it/logs/sub-<n>-log.txt"
  Wait for user: type 'continue' to keep waiting or 'skip' to mark as blocked.

══ STEP E: COLLECT REPORT ══════════════════════════════════════════════════════
Check: does .run-with-it/reports/sub-<n>-report.json exist with valid JSON?
  YES: Read the compact report JSON into context (this is the ONLY file you read).
  NO:  Mark issue status="blocked" with reason="report-missing".
       Update main-state.json. Proceed to Step F with blocked status.

Print to user (do NOT read into AI context):
  "=== Sub-coordinator output for issue #<n> ==="
  "Full log: .run-with-it/logs/sub-<n>-log.txt"
  "Report:   .run-with-it/reports/sub-<n>-report.json"
  "(Log is not loaded into context — use tail -f to inspect)"

Parse from report JSON: outcome, summary, files_modified, verification,
review_summary, token_usage, commit_sha, blocking_reasons.

Delete $SUB_COORD_CONTEXT_FILE immediately after reading the report.

══ STEP F: UPDATE STATE + GITHUB ════════════════════════════════════════════════
1. Update issue_registry[<n>].status = report.outcome in main-state.json.
2. Append to completed_summaries:
     { issue, outcome, files_modified_count, lines_added, lines_deleted,
       review_cycles, commit_sha }
3. Append ledger rows derived from report.token_usage to ledger_rows.
4. Write main-state.json to disk ← WRITE BEFORE any GitHub call.
5. Post terminal comment to GitHub issue using Appendix E template,
   populated from report JSON fields.
6. If outcome="completed": gh issue close <n> --comment "<brief closing note>"
   If outcome="failed-review" or "blocked": leave open (terminal comment already posted)
7. Emit: STATUS|type=sub-coord-complete|issue=<n>|outcome=<outcome>
         |report_file=<path>|commit_sha=<sha-or-none>

══ GOTO STEP A ═════════════════════════════════════════════════════════════════
```

## `run-agent.sh` — Full Syntax Reference

```
run-agent.sh --agent <agent> [--model <model>] --context-file <file> [--prompt-file <file>]
             [--permission-mode <mode>] [--extra-arg <arg>] [--unattended] [--dry-run]
run-agent.sh --list-agents [--detected-only]
run-agent.sh --list-models <agent>
```

| Flag | Env var equivalent | Required | Description |
|------|--------------------|----------|-------------|
| `--agent <agent>` | `AGENT` | Yes | Agent slug (e.g. `codex`, `github-copilot`, `claude`, `gemini`) |
| `--model <model>` | `MODEL` | Yes (always pass explicitly) | Model id to use |
| `--context-file <file>` | `CONTEXT_PAYLOAD_FILE` | Yes | Path to the assembled context payload file |
| `--prompt-file <file>` | `PROMPT_FILE` | No (defaults to `<script-dir>/prompt.md`) | Path to the prompt file |
| `--permission-mode <mode>` | `AGENT_PERMISSION_MODE` | No | Override agent permission mode |
| `--extra-arg <arg>` | `AGENT_EXTRA_ARGS` | No | Repeatable; appended to agent invocation |
| `--unattended` | `UNATTENDED=1` | Yes (always pass) | Required when any permission mode is set |
| `--dry-run` | — | No | Print the resolved command without executing |
| `--list-agents` | — | — | List all agents and detection status; add `--detected-only` to filter |
| `--list-models <agent>` | — | — | List known models for an agent |

Additional env vars (no flag equivalents):

| Env var | Default | Description |
|---------|---------|-------------|
| `AGENT_REGISTRY_FILE` | `<script-dir>/agent-registry.json` | Path to agent registry |
| `GUI_MODE` | `auto` | `auto` detects GUI env vars; `1` forces GUI-safe noninteractive mode; `0` forces CLI/CI mode |
| `REPO_ROOT` | `$(pwd)` | Working directory passed to agent |
| `PRINT_PROMPT` | `0` | Set to `1` to print assembled prompt without running |

`GUI_MODE` behavior:
- `auto` — detects `VSCODE_PID`, `TERM_PROGRAM=vscode`, `ELECTRON_RUN_AS_NODE`, `ANTIGRAVITY_APP`, `CURSOR_TRACE_ID`, `CLAUDE_CODE_ENTRYPOINT`; sets `GUI_MODE=1` if any match.
- `1` — forces `UNATTENDED=1` and downgrades dangerous full-bypass permission flags to safer per-agent equivalents.
- `0` — preserves CLI/CI behavior with no permission downgrades.

## Cleanup

Cleanup runs only after a successful completion, failed run, interrupted run, or explicit `discard` command. Cleanup must not fire on `resume`.

### Successful Run Completion

On successful run completion:

- Delete all `$SUB_COORD_CONTEXT_FILE` temp files (should already be deleted after each issue, but clean up any stragglers).
- Delete `.run-with-it/main-state.json`, `.run-with-it/main-orchestrator-rules.md`, `.run-with-it/coordinator-rules.md`.
- Delete all files under `.run-with-it/reports/`, `.run-with-it/logs/`, `.run-with-it/reviews/`.
- Remove `.run-with-it/` directory if empty.
- For each of `technical_requirements.md`, `prd.md`, and `issues.md` present in the workspace root: run `git status --short <file>`. Delete the file **only if** it is untracked (`??`) or clean (not listed). If the file has user modifications (any other status), skip deletion and emit `STATUS|type=cleanup|action=skipped-dirty-file|file=<file>` — never delete user-modified workspace files.
- Ensure `.gitignore` contains entries for `.run-with-it/`, `technical_requirements.md`, `prd.md`, and `issues.md` using an idempotent append.
- If `.git/` exists, stage only the deleted files and `.gitignore`; commit with message `chore: remove skill-generated artifacts post-run`.
- Emit `STATUS|type=cleanup|action=completed|files_removed=<n>`.

### Failed or Interrupted Run

On failed or interrupted run:

- Keep all `.run-with-it/` files.
- Print the paths of preserved files (state, reports, logs, reviews).
- Offer `discard` command to force-delete and restart.

### Discard

On `discard`:

- Delete all preserved files including `.run-with-it/main-state.json`, all reports, logs, reviews, `technical_requirements.md`, `prd.md`, and `issues.md`.
- Update `.gitignore` and commit with message `chore: remove skill-generated artifacts (discarded run)`.
- Emit `STATUS|type=cleanup|action=discarded|files_removed=<n>`.
- Proceed as a fresh run.

## Appendix A: Main State Schema

The Main Orchestrator persists `.run-with-it/main-state.json` (schema_version 2). This is the Main Orchestrator's entire persistent memory.

```json
{
  "schema_version": 2,
  "run_id": "<uuid generated at run start>",
  "started_at": "<iso8601>",
  "execution_plan": {
    "batches": [
      { "batch_id": 1, "issues": [36, 37, 38], "mode": "sequential" }
    ]
  },
  "issue_registry": {
    "36": {
      "status": "completed | in_progress | pending | failed-review | blocked",
      "title": "issue title",
      "report_file": ".run-with-it/reports/sub-36-report.json",
      "log_file": ".run-with-it/logs/sub-36-log.txt",
      "commit_sha": "abc1234"
    }
  },
  "current_batch_id": 1,
  "current_issue_index": 0,
  "completed_summaries": [
    {
      "issue": 36,
      "outcome": "completed",
      "files_modified_count": 3,
      "lines_added": 42,
      "lines_deleted": 7,
      "review_cycles": 1,
      "commit_sha": "abc1234"
    }
  ],
  "ledger_rows": [
    "STATUS|type=ledger|task=36|... (verbatim line as emitted)"
  ]
}
```

Key invariants:
- `issue_registry` has one entry per issue
- `completed_summaries` accumulates one compact record per finished issue — this is what the main orchestrator reads back after compression
- `ledger_rows` stores verbatim STATUS lines for the final ledger printout
- `current_batch_id` + `current_issue_index` tell a resumed orchestrator exactly where to continue

### `.gitignore` Auto-Append for `.run-with-it/` (Required)

On the first write of any file under `.run-with-it/`:

- If `.git/` exists in the current working directory, ensure `.run-with-it/` is in `.gitignore`:
  - Create `.gitignore` if it does not exist
  - Append `.run-with-it/` on its own line only if not already present (idempotent)
  - Preserve existing `.gitignore` contents unchanged
- If `.git/` is absent, skip `.gitignore` creation silently.

## Appendix B: Status and Ledger Contract

### Status Messages

Emit parseable one-line status messages:

- plan: `STATUS|type=plan|total_issues=<n>|mode=sequential|pending=<n>|blocked=<n>`
- memory refresh: `STATUS|type=memory-refresh|state_file=.run-with-it/main-state.json|tasks_loaded=<n>|completed=<n>|pending=<n>|failed=<n>`
- main loop: `STATUS|type=main-loop|iteration=<n>|pending=<count>|completed=<count>|failed=<count>`
- sub-coordinator spawn: `STATUS|type=sub-coord-spawn|issue=<n>|agent=<name>|model=<model>|report_file=<path>|log_file=<path>`
- sub-coordinator complete: `STATUS|type=sub-coord-complete|issue=<n>|outcome=<completed|failed-review|blocked>|report_file=<path>|commit_sha=<sha-or-none>`
- stall: `STATUS|type=stall|issue=<n>|idle_for=<seconds>|action=alert-user`
- intake fallback: `STATUS|type=intake-fallback|reason=<no-gh-auth|no-remote|gh-failed-outside-sandbox>`
- runner sandbox retry: `STATUS|type=runner-sandbox-retry|agent=<agent>|model=<model>|reason=<error-summary>`
- runner sandbox retry result: `STATUS|type=runner-sandbox-retry-result|outcome=<success|failed>`
- cleanup completed: `STATUS|type=cleanup|action=completed|files_removed=<n>`
- cleanup discarded: `STATUS|type=cleanup|action=discarded|files_removed=<n>`
- final ledger row: `STATUS|type=ledger|task=<task-id>|role=impl|cycle=0|agent=<agent-name>|model=<model-id>|added=<n>|deleted=<n>|total=<n>|reason=sub-coordinator|input_tokens=<n-or-unknown>|output_tokens=<n-or-unknown>|cache_hit_tokens=<n-or-unknown>|telemetry_source=sub-coordinator-report`

### Final Ledger

After the loop exits and before cleanup, print the aggregated final ledger by reading `ledger_rows` from `main-state.json`. This means the ledger survives compression and captures all issues including those completed before the context was compressed.

Also print a final summary of all `completed_summaries` entries showing:
- Total issues processed
- Completed / failed-review / blocked counts
- Total lines added/deleted across all issues
- Aggregate token usage (sum `token_usage` fields from all report JSONs for issues that have completed)

## Appendix C: Resume and State Contract

### Resume Flow

On startup, if `.run-with-it/main-state.json` exists, prompt the user (per Preflight Check 14). On `resume`:

1. Re-read `.run-with-it/main-state.json`.
2. Identify all issues with `status="in_progress"` — these had a Sub-Coordinator that was interrupted mid-run. Reset them to `status="pending"` (Sub-Coordinators are ephemeral; re-run fresh).
3. Identify all issues with `status="pending"` — these haven't started yet.
4. Identify all issues with `status="completed"`, `"failed-review"`, or `"blocked"` — skip these entirely.
5. Re-enter Main Loop at Step A.
6. Emit: `STATUS|type=resume|tasks_restored=<n>|completed=<n>|re_queued_in_progress=<m>`

If `.run-with-it/main-state.json` is missing or unparseable when the user types `resume`, emit:

```
STATUS|type=resume-error|reason=<missing|parse-error>|action=user-required
```

Then stop and ask the user whether to proceed as a fresh run.

### Compression Survival

When the Main Orchestrator's context is compressed and the session resumes (conversation history is cleared):

1. Re-read `.run-with-it/main-state.json` (Step A of the loop).
2. The `completed_summaries` array gives the full bounded history.
3. Issues with `status="completed"` are done — never re-run them.
4. Issues with `status="in_progress"` are reset to `"pending"` — re-run their Sub-Coordinators fresh.
5. Continue the Main Loop as if resuming from a clean slate.

Never derive issue state from conversation history after compression. The state file is always authoritative.

## Appendix D: Terminal Issue Comment Contract

### Terminal Issue Comments

Post issue comments only for terminal outcomes: `completed`, `blocked`, or `failed-review`.
Populate all fields from the Sub-Coordinator's compact report JSON.

Use the same markdown template for every terminal outcome, with this fixed section order:

1. `## Status`
2. `## Summary`
3. `## Verification`
4. `## Token Usage`
5. `## Notes`

Terminal comment template:

```md
## Status
<completed|blocked|failed-review>

## Summary
<task outcome summary — from report.summary>

## Verification
<task-specific verification results — from report.verification.evidence>

## Token Usage
- Input tokens: <n|unknown>
- Output tokens: <n|unknown>
- Cache hit tokens: <n|unknown>

## Notes
Review: <approve|revise (N cycles)>, final verdict: <approve|reject>, reviewer model: <model-id>
<follow-ups, blockers, or additional reviewer notes — omit line if none>

## Blocking Reasons
<omit this section entirely when verdict is not reject; when verdict=reject, list each entry from report.blocking_reasons as a separate bullet>
```

Comment requirements:

- `Token Usage` must report task-specific telemetry only (from `report.token_usage`).
- If any token value is unavailable, render that value explicitly as `unknown`.
- `Verification` must summarize the checks run and whether they passed, failed, or were blocked (from `report.verification`).
- `Notes` must include exactly one review summary line when `DELEGATED_REVIEW=true`. Format: `Review: <verdict-path>, final verdict: <approve|reject>, reviewer model: <model-id>`. For a straight approval write `approve (1 cycle)`; for a revise-then-approve write `revise (N cycles)`.
- `Blocking Reasons` section must be included **only** when `report.blocking_reasons` is non-empty. Render each entry as a separate markdown bullet. Omit the section entirely otherwise.

## Guardrails

- **Never pause after planning to ask the user how to proceed.** Enter the Main Loop immediately.
- **Never load sub-coordinator log files into AI context.** Print their path; never cat or read_file them.
- **Never implement work directly in this session.** Implementation belongs to Sub-Coordinators via the runner.
- **Never present execution option menus.**
- **Never run tests, build commands, or compile the project** in this session.
- **Never derive issue state from conversation history.** Always read from `main-state.json`.
- Preserve local fallback behavior when GitHub or git is unavailable.
- Keep changes minimal and focused to orchestration/control-plane behavior.
