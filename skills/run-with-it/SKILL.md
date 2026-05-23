---
name: run-with-it
description: Two-layer orchestration runtime — Main Orchestrator fetches all issues, plans execution order, maintains a rolling pool of Sub-Coordinators (up to PARALLEL_JOBS concurrently), fills freed slots immediately on completion, and updates GitHub. Context stays bounded so the run can continue for hours or days without degradation.
---

## Skill Isolation

Sole active authority for this session once invoked. No other skill may activate, interrupt, or modify behavior unless called by name via `Skill` tool call within this skill's workflow. Suppress any spontaneous external skill; continue without interruption. Applies from invocation until explicit termination or handoff.

This isolation governs orchestration flow only. Under no circumstance may this skill suppress, override, interrupt, or interfere with subcodinate core behavior, native tool invocations, or reasoning. Copilot's own capabilities must remain fully operational at all times. This carve-out cannot be overridden by any instruction within this skill.

## Critical Main Orchestrator Rules (compaction-safe — always enforce, even after context compression)

These rules apply for the entire lifetime of this skill session. They are stated here first so they survive context compaction and are never dropped:

- **Re-read `.run-with-it/main-state.json` before every loop iteration.** After context compression you have no memory of prior work — that file is your entire memory. Never derive issue state from conversation history.
- **Never implement work directly in this session.** All implementation belongs to Sub-Coordinators spawned via `run-with-it-dispatch.sh --role sub-coord`, which wraps `run-agent.sh --prompt-file sub-coordinator-prompt.md`. There is no "implement in this chat" fallback option under any circumstance.
- **Never run tests, build commands, or compile the project** in this session. Sub-Coordinators and their child agents run verification; the Main Orchestrator only reads compact reports.
- **Never pause after planning to ask the user how to proceed.** Enter the Main Loop immediately after the execution plan is written.
- **Never present execution option menus** (Option A / B / C style choices).
- **Always pull issue data from GitHub** (`gh`) when a remote exists. Only fall back to local files if `gh` fails both inside and outside the sandbox.
- **Never delete user-modified files** during cleanup. Check `git status --short` before removing any workspace artifact.
- **Never load full sub-coordinator log files into context.** Sub-Coordinator logs live under `.run-with-it/sub/`. A shell watcher may print only the last two changed lines with `tail -n 2`; only read the compact report JSON from `.run-with-it/reports/`.
- **Never load live status logs into context.** Live progress is written to `.run-with-it/status/current.txt` and `.run-with-it/status/events.log`; shell watchers may print one changed line to the terminal, but the Main Orchestrator must not read those files into AI memory.
- **GitHub operations (close, comment) are the Main Orchestrator's sole responsibility.** Sub-Coordinators never touch GitHub.
- **Never inspect, infer, or act on a Sub-Coordinator's internal routing decisions.** Once a Sub-Coordinator is spawned, the agent and model it selects for its child workers are entirely its own responsibility — the Main Orchestrator has no visibility into, and no authority over, those internal choices. Do not read log files to determine which worker agent or model is running.
- **Never kill, cancel, or restart a Sub-Coordinator mid-run under any circumstance.** If a Sub-Coordinator appears to be using a different agent or model than expected, that is correct behavior — it is applying its own complexity-based routing. Do not intervene. The only valid responses to a running Sub-Coordinator are: (a) wait for it to complete and write its compact report, or (b) alert the user after `SUB_COORD_TIMEOUT_SECONDS` and wait for a 'continue' or 'skip' instruction.
- **Never inject AGENT or MODEL overrides into a Sub-Coordinator that has already been spawned.** Routing overrides (`AGENT`, `MODEL`, `COMPLEXITY_LEVEL`, `COMPLEXITY_SCORE`) may only be set before spawning, as part of the context file assembled in Step C. After `run-with-it-dispatch.sh` calls `run-agent.sh`, those values are locked and the Main Orchestrator must not attempt to change them.
- **Spawn every Sub-Coordinator as a background process, capture `SUB_COORD_PID=$!`, and persist it in `main-state.json` before monitoring.** Persist `issue`, `pid`, `started_at`, `context_file`, `log_file`, `done_file`, and `report_file` together so the rolling pool can recover cleanly after context compression.
- **Use `assets/worker-watch.sh` for Sub-Coordinator liveness checks during pool monitoring.** Pass each Sub-Coordinator's `pid`, `done_file`, and `log_file`; treat PID liveness as diagnostic only. Completion requires the done sentinel and compact report artifacts.
- **All judgments about implementation quality, routing correctness, and worker behavior come exclusively from the compact report JSON.** The Main Orchestrator has no other source of truth about what happened inside a Sub-Coordinator session.

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
- Creates one shared run feature branch (`run-with-it/<run-id>`) from the original base branch, pushes it when a GitHub remote exists, and uses it as the final PR head branch
- Determines execution order with a dependency graph and topological sort based primarily on each issue's `## Blocked by` section; cycles or unresolved external blockers are marked blocked before execution
- Maintains a rolling pool of up to `PARALLEL_JOBS` active **Sub-Coordinators** via `run-with-it-dispatch.sh` — freed slots fill immediately when any job completes rather than waiting for whole batches
- As each Sub-Coordinator completes, reads its compact report and immediately spawns the next ready issue into the freed slot
- Writes its own status log to `.run-with-it/main/main.log`
- Reads ONLY the compact report JSON — never the implementation diffs or log files
- Updates `main-state.json` after each issue (its full external memory)
- Posts terminal GitHub comments and closes/updates issues
- Spawns a Merge Recovery Coordinator when a Sub-Coordinator reports `merge_failed`; Main Orchestrator never merges issue branches itself
- Creates one final PR from the shared run feature branch after all issues are terminal
- Re-reads `main-state.json` at the top of every loop iteration to survive context compression

**Sub-Coordinator** (spawned via `sub-coordinator-prompt.md`, runs in a child agent session):
- Handles exactly ONE issue end-to-end
- Creates an issue branch and issue worktree from the shared run feature branch
- Runs complexity analysis, deterministic routing, implementation, review, and modification loops
- Runs child workers with `REPO_ROOT` pointing at the issue worktree while keeping logs/reports under the root `.run-with-it/`
- Attempts the normal merge back into the shared feature branch under `.run-with-it/locks/merge.lock`
- Writes a compact report JSON and a full log file under `.run-with-it/sub/` when done
- Spawns worker agents whose logs are written under `.run-with-it/complexity/`, `.run-with-it/impl/`, `.run-with-it/review/`, and `.run-with-it/modify/`
- Never touches GitHub; never updates `main-state.json`

**Merge Recovery Coordinator** (spawned via `merge-recovery-prompt.md`, runs only after `merge_failed`):
- Handles one failed issue-branch merge
- Reads the shared feature branch holistically because it contains prior Sub-Coordinator work
- Resolves conflicts or merge-induced verification failures under the same merge lock
- Pushes the shared feature branch on success and writes a compact recovery report
- Never closes issues, creates the final PR, or updates `main-state.json`

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
  - `ISSUE_LIMIT` (default `1000` — fetches all matching issues; set lower to cap)
  - `ISSUE_STATE` (default `open`)
  - `COMMITS_LIMIT` (default `5`)
  - `MAX_ITERATIONS` (default `20`)
- Optional sub-coordinator agent selection:
  - `SUB_COORD_AGENT` (default `codex`) — fixed agent slug used to spawn every Sub-Coordinator
  - `SUB_COORD_MODEL` (default `gpt-5.5`) — fixed model id used for every Sub-Coordinator; the Sub-Coordinator then independently runs its own routing for implementation/review/modify child agents
  - `SUB_COORD_TIMEOUT_SECONDS` (default `3600`) — seconds before the Main Orchestrator emits a stall alert for a non-completing Sub-Coordinator
- Optional live status controls:
  - `STATUS_POLL_SECONDS` (default `10`) — shell polling cadence for printing the latest changed status line while a Sub-Coordinator runs
  - `LOG_TAIL_POLL_SECONDS` (default `120`) — shell polling cadence for printing the last two changed sub-coordinator log lines
  - `RUN_WITH_IT_STATUS_FILE` (default `.run-with-it/status/current.txt`) — single-line current status bus, overwritten on every update
  - `RUN_WITH_IT_EVENTS_LOG` (default `.run-with-it/status/events.log`) — append-only status event log for terminal inspection only; never load it into AI context
  - `RUN_WITH_IT_LOG_FILE` — role-specific runner log file; Sub-Coordinators use `.run-with-it/sub/sub-<n>.log`, workers use `.run-with-it/<role>/...`
  - `RUN_WITH_IT_DONE_FILE` — role-specific completion sentinel; workers use `.run-with-it/done/issue-<n>-<role>-cycle-<cycle>.done`
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
- Optional parallel execution:
  - `PARALLEL_JOBS` (default `4`) — size of the rolling Sub-Coordinator pool. The pool stays filled up to this limit: as each Sub-Coordinator completes, the next ready issue is spawned immediately into the freed slot. Set to `1` for sequential execution.

## Asset Discovery (Required)

Resolve assets in this order:

1. `$ASSETS_DEST` if set and complete.
2. `$HOME/.ai-skill-collections/assets`.
3. `./assets`.

Required files:

- `prompt.md`
- `run-agent.sh`
- `run-agent.ps1`
- `run-with-it-dispatch.sh`
- `run-with-it-pool.sh`
- `agent-registry.json`
- `review-prompt.md`
- `modifier-prompt.md`
- `complexity-prompt.md`
- `coordinator-rules.md`
- `worker-watch.sh`
- `sub-coordinator-prompt.md`
- `main-orchestrator-rules.md`
- `merge-recovery-prompt.md`

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
New-Item -ItemType Directory -Force "$env:USERPROFILE\.ai-skill-collections\assets"; Copy-Item -Force .\assets\prompt.md, .\assets\run-agent.ps1, .\assets\run-agent.sh, .\assets\run-with-it-dispatch.sh, .\assets\run-with-it-pool.sh, .\assets\worker-watch.sh, .\assets\agent-registry.json, .\assets\review-prompt.md, .\assets\modifier-prompt.md, .\assets\complexity-prompt.md, .\assets\coordinator-rules.md, .\assets\sub-coordinator-prompt.md, .\assets\main-orchestrator-rules.md, .\assets\merge-recovery-prompt.md "$env:USERPROFILE\.ai-skill-collections\assets\"
```

**Bash (macOS / Linux / Git Bash):**
```bash
mkdir -p "$HOME/.ai-skill-collections/assets" && cp -f ./assets/prompt.md ./assets/run-agent.sh ./assets/run-agent.ps1 ./assets/run-with-it-dispatch.sh ./assets/run-with-it-pool.sh ./assets/worker-watch.sh ./assets/agent-registry.json ./assets/review-prompt.md ./assets/modifier-prompt.md ./assets/complexity-prompt.md ./assets/coordinator-rules.md ./assets/sub-coordinator-prompt.md ./assets/main-orchestrator-rules.md ./assets/merge-recovery-prompt.md "$HOME/.ai-skill-collections/assets/" && chmod +x "$HOME/.ai-skill-collections/assets/run-agent.sh" "$HOME/.ai-skill-collections/assets/run-with-it-dispatch.sh" "$HOME/.ai-skill-collections/assets/run-with-it-pool.sh" "$HOME/.ai-skill-collections/assets/worker-watch.sh"
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
7. `worker-watch.sh` exists
8. `sub-coordinator-prompt.md` exists
9. `main-orchestrator-rules.md` exists
10. `merge-recovery-prompt.md` exists
11. Runner exists and is executable (`run-agent.sh` on Bash; `run-agent.ps1` on Windows)
12. Shared dispatcher exists and is executable (`run-with-it-dispatch.sh` on Bash)
13. Shared rolling pool runner exists and is executable (`run-with-it-pool.sh` on Bash)
14. `agent-registry.json` exists
15. `gh` auth when GitHub intake is required
16. `SUB_COORD_AGENT` is installed (detected): run `"$ASSET_ROOT/run-agent.sh" --list-agents --detected-only` and confirm `SUB_COORD_AGENT` appears
17. `SUB_COORD_MODEL` is in `SUB_COORD_AGENT`'s `known_models` in `agent-registry.json`
18. **Existing-state detection** (resume vs. discard prompt): before any issue intake or fresh task selection, check whether `.run-with-it/main-state.json` exists in the current working directory.

   - If it exists, pause and present exactly this prompt to the user:

     ```
     Existing run state found at .run-with-it/main-state.json.
     Type "resume" to continue the previous run, or "discard" to delete it and start fresh.
     ```

   - **`resume`**: do not delete the file. Proceed to the Resume Flow section.
   - **`discard`**: apply the Cleanup `Discard` policy, then continue with normal preflight and fresh issue intake as if no prior state existed.
   - Do not start any new task, fetch any issue, or spawn any Sub-Coordinator until the user responds.

If `sub-coordinator-prompt.md`, `main-orchestrator-rules.md`, or `merge-recovery-prompt.md` is missing at the resolved asset root, fail fast with the same platform-appropriate one-line fix message used in asset discovery.

## Initial Batch Issue Fetch

If issue data is missing in context, fetch all `ready-for-agent` issues at startup.

Use `ISSUE_LIMIT` (default `1000`) as the `--limit` argument — this fetches all matching issues by default. Do not cap the result unless the user explicitly sets `ISSUE_LIMIT` to a lower value.

```bash
gh issue list --state "${ISSUE_STATE:-open}" --label "${ISSUE_LABEL:-ready-for-agent}" --limit "${ISSUE_LIMIT:-1000}" --json number,title,labels,body,url
```

Fallback policy:

- Primary: GitHub issues via `gh`. **Always use GitHub when the repo has a GitHub remote. Never silently fall back to a local file when GitHub may be reachable.**
- If `gh` fails inside the sandbox (permission error, named-pipe, socket), **retry `gh` outside the sandbox** (`dangerouslyDisableSandbox: true`) before considering any fallback.
- Fallback: local `issues.md` (`LOCAL_ISSUES_FILE` override supported) — **only** when `gh` fails both inside and outside the sandbox, or no GitHub remote exists. Emit `STATUS|type=intake-fallback|reason=<no-gh-auth|no-remote|gh-failed-outside-sandbox>` before using local file.
- If git metadata is unavailable, continue with empty commit context.

Before fetching work begins, create the shared run feature branch:

1. Capture original base branch and SHA.
2. Create `run-with-it/<run-id>` from that base.
3. Push the branch when a GitHub remote exists.
4. Record `run_branch.base_branch`, `run_branch.base_sha`, `run_branch.feature_branch`, `run_branch.feature_branch_start_sha`, `run_branch.remote`, and `run_branch.pushed`.

After fetching all issues:

1. Build a dependency graph: for each issue, identify which other issues it depends on from `## Blocked by`, issue body, labels, or cross-references. `## Blocked by` is the primary source of truth. Normalize `#123`, full GitHub issue URLs, and plain issue numbers. Treat `None - can start immediately` as no dependencies.
2. Detect cycles and unresolved dependencies; mark affected issues `blocked` with `dependency_proof` and `blocking_reasons`.
3. Determine execution order: topological sort respecting dependencies. Priority order within the same dependency tier: critical fixes → development infrastructure → tracer-bullet feature slices → polish and quick wins → refactors. When `PARALLEL_JOBS > 1`, issues fill a rolling pool (up to `PARALLEL_JOBS` active at a time) — freed slots are filled immediately rather than waiting for a full batch to complete.
4. Issues whose dependencies have open/unresolved status, `merge_recovery`, `failed-merge`, or `blocked` are not ready until the dependency becomes `completed`.
5. Write the complete execution plan to `.run-with-it/main-state.json` before doing any work. Record `parallel_jobs`, `execution_mode` (`sequential` when `PARALLEL_JOBS=1`, `rolling-pool` otherwise), `topo_order`, `dependency_tiers`, and each issue's `dependency_proof`.
6. Emit: `STATUS|type=plan|total_issues=<n>|mode=<sequential|rolling-pool>|parallel_jobs=<PARALLEL_JOBS>|pending=<n>|blocked=<n>`
7. Emit: `STATUS|type=memory-refresh|state_file=.run-with-it/main-state.json|tasks_loaded=<n>|completed=0|pending=<n>`

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

══ STEP B: FILL ROLLING POOL ════════════════════════════════════════════════════
Compute ACTIVE_POOL = all issues in issue_registry with status="in_progress"
(cross-check against active_pool_issues in state for consistency).
POOL_SLOTS_FREE = PARALLEL_JOBS - len(ACTIVE_POOL).

Collect READY_ISSUES = all issues with status="pending" whose dependencies are
all "completed", ordered by priority:
  critical fixes → dev infra → tracer-bullet slices → polish → refactors.
Issues in READY_ISSUES must have no unmet dependencies on each other.

If POOL_SLOTS_FREE > 0 and READY_ISSUES is non-empty:
  Select NEWLY_QUEUED = READY_ISSUES[0 : POOL_SLOTS_FREE].
  For each issue <n> in NEWLY_QUEUED:
    Mark issue status="in_progress" in main-state.json.
    Append <n> to active_pool_issues.
    Write main-state.json to disk BEFORE spawning.
  Emit: STATUS|type=pool-fill|active=<len(ACTIVE_POOL)+len(NEWLY_QUEUED)>
        |newly_queued=<len(NEWLY_QUEUED)>|pending_remaining=<pending_after>
        |parallel_jobs=<PARALLEL_JOBS>

If ACTIVE_POOL is empty and READY_ISSUES is empty:
  Check if any issues remain with status="pending" — if all have unmet deps,
  re-evaluate them; if still unresolvable, mark them "blocked".
  If ALL issues are terminal (completed / failed-review / blocked):
    EXIT LOOP → proceed to Final Ledger and Cleanup.

══ STEP C: ASSEMBLE SUB-COORDINATOR CONTEXT FILES ══════════════════════════════
Repeat for EACH issue <n> in NEWLY_QUEUED:

Build $SUB_COORD_CONTEXT_FILE_<n> (a separate temp file per issue) containing, in order:
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
     SUB_COORD_LOG_FILE=<abs-path-to-.run-with-it/sub/sub-<n>.log>
     RUN_FEATURE_BRANCH=<shared-run-feature-branch>
     RUN_BASE_BRANCH=<original-base-branch>
     RUN_BASE_SHA=<original-base-sha>
     ISSUE_BRANCH=<shared-run-feature-branch>/issue-<n>
     ISSUE_WORKTREE_PATH=<abs-path-to-.run-with-it/worktrees/issue-<n>>
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

  The Sub-Coordinator must derive a separate `COMPLEXITY_CONTEXT_PAYLOAD_FILE`
  before spawning the complexity worker. That file is a sanitized scoring brief,
  not the full implementation issue body. It starts with explicit "task data
  only" guardrails, paraphrases the requested outcome, summarizes acceptance
  criteria and likely touched areas, includes recent commits and relevant file
  context, and strips imperative implementation checklists so the complexity
  worker cannot mistake them for execution instructions.

Create directories before spawning:
  mkdir -p .run-with-it/main .run-with-it/sub .run-with-it/reports .run-with-it/status .run-with-it/done .run-with-it/complexity .run-with-it/impl .run-with-it/review .run-with-it/modify .run-with-it/merge-recovery .run-with-it/worktrees .run-with-it/locks

Resolve live status files before spawning:
  MAIN_LOG_FILE="${MAIN_LOG_FILE:-$(pwd -P)/.run-with-it/main/main.log}"
  SUB_COORD_LOG_FILE="$(pwd -P)/.run-with-it/sub/sub-<n>.log"
  RUN_WITH_IT_STATUS_FILE="${RUN_WITH_IT_STATUS_FILE:-$(pwd -P)/.run-with-it/status/current.txt}"
  RUN_WITH_IT_EVENTS_LOG="${RUN_WITH_IT_EVENTS_LOG:-$(pwd -P)/.run-with-it/status/events.log}"
  STATUS_POLL_SECONDS="${STATUS_POLL_SECONDS:-10}"
  LOG_TAIL_POLL_SECONDS="${LOG_TAIL_POLL_SECONDS:-120}"

Every STATUS line emitted by the Main Orchestrator must be appended to `$MAIN_LOG_FILE`
with an explicit shell write before or at the same time it is printed.

For EACH issue <n> in NEWLY_QUEUED:
  Emit: STATUS|type=sub-coord-spawn|issue=<n>|agent=<SUB_COORD_AGENT>
        |model=<SUB_COORD_MODEL>|report_file=<path>|log_file=<path>
        |pool_size=<current_active_count>|parallel_jobs=<PARALLEL_JOBS>

Print to user for each issue:
  "Starting sub-coordinator for issue #<n>: <title>"
  "Log: .run-with-it/sub/sub-<n>.log"
  "To watch live progress in a separate terminal:"
  "  tail -f .run-with-it/sub/sub-<n>.log"

══ STEP D: SPAWN NEWLY QUEUED + ROLLING POOL MONITOR ════════════════════════════

Execution-mode requirement (critical):
  Run Step D as ONE long-lived shell session that performs both spawn and monitor.
  Do not split spawn and monitor into separate shell invocations. Do not write a
  bespoke rolling-pool script in the Main Orchestrator session. Use the shared
  `run-with-it-pool.sh` executable; it maintains the pool and calls
  `run-with-it-dispatch.sh --role sub-coord` for each active issue.

  Required handoff before Step D:
  - Each ready issue in `main-state.json` must have `issue_registry[<n>].context_file`
    (or `sub_coord_context_file`) pointing at the context file assembled in Step C.

  Bash (macOS / Linux / Git Bash):

    nohup "$ASSET_ROOT/run-with-it-pool.sh" \
      --asset-root "$ASSET_ROOT" \
      --state-file "$(pwd -P)/.run-with-it/main-state.json" \
      --parallel-jobs "$PARALLEL_JOBS" \
      --agent "$SUB_COORD_AGENT" \
      --model "$SUB_COORD_MODEL" \
      --status-file "$RUN_WITH_IT_STATUS_FILE" \
      --events-log "$RUN_WITH_IT_EVENTS_LOG" \
      --main-log "$MAIN_LOG_FILE" \
      --poll-seconds "$STATUS_POLL_SECONDS" \
      --timeout-seconds "$SUB_COORD_TIMEOUT_SECONDS" \
      >>"$MAIN_LOG_FILE" 2>&1 < /dev/null &

    POOL_PID=$!

  Persist `POOL_PID` in `main-state.json`, then monitor that single process until
  it emits `STATUS|type=pool-empty`.

Legacy implementation sketch below is retained only as behavioral reference. Prefer
`run-with-it-pool.sh` whenever it exists at the resolved asset root.

Bash (macOS / Linux / Git Bash):

  # Bash 3-compatible pool tracking (no associative arrays)
  # Keep an issue list plus per-issue dynamic vars: _POOL_PID_<issue>, etc.
  POOL_ISSUES=""

  _pool_add_issue() {
    local issue="$1"
    case " $POOL_ISSUES " in
      *" $issue "*) ;;
      *) POOL_ISSUES="${POOL_ISSUES} ${issue}" ;;
    esac
  }

  _pool_remove_issue() {
    local issue="$1"
    local next=""
    local cur
    for cur in $POOL_ISSUES; do
      if [ "$cur" != "$issue" ]; then
        next="${next} ${cur}"
      fi
    done
    POOL_ISSUES="$next"
  }

  _pool_set() {
    local key="$1"
    local issue="$2"
    local value="$3"
    local escaped
    escaped=$(printf '%q' "$value")
    eval "_POOL_${key}_${issue}=${escaped}"
  }

  _pool_get() {
    local key="$1"
    local issue="$2"
    eval "printf '%s' \"\${_POOL_${key}_${issue}-}\""
  }

  _pool_unset_issue() {
    local issue="$1"
    unset "_POOL_PID_${issue}" "_POOL_STARTED_AT_${issue}" \
      "_POOL_LAST_LOG_TAIL_AT_${issue}" "_POOL_LAST_LOG_TAIL_${issue}"
  }

  # --- Spawn each newly queued issue into the pool ---
  for issue_n in $NEWLY_QUEUED; do
    eval "SUB_COORD_CTX_FILE=\"\${SUB_COORD_CONTEXT_FILE_${issue_n}}\""
    SUB_COORD_LOG="$(pwd -P)/.run-with-it/sub/sub-${issue_n}.log"
    SUB_COORD_DONE="$(pwd -P)/.run-with-it/done/issue-${issue_n}-sub-coord.done"
    nohup "$ASSET_ROOT/run-with-it-dispatch.sh" \
      --asset-root "$ASSET_ROOT" \
      --role sub-coord \
      --issue "$issue_n" \
      --agent "$SUB_COORD_AGENT" \
      --model "$SUB_COORD_MODEL" \
      --context-file "$SUB_COORD_CTX_FILE" \
      --prompt-file "$ASSET_ROOT/sub-coordinator-prompt.md" \
      --log-file "$SUB_COORD_LOG" \
      --done-file "$SUB_COORD_DONE" \
      --result-file "$(pwd -P)/.run-with-it/reports/sub-${issue_n}-report.json" \
      --status-file "$RUN_WITH_IT_STATUS_FILE" \
      --events-log "$RUN_WITH_IT_EVENTS_LOG" \
      --poll-seconds "$STATUS_POLL_SECONDS" \
      --timeout-seconds "$SUB_COORD_TIMEOUT_SECONDS" \
      >>"$SUB_COORD_LOG" 2>&1 < /dev/null &
      _pool_add_issue "$issue_n"
      _pool_set "PID" "$issue_n" "$!"
      SUB_COORD_PID="$(_pool_get "PID" "$issue_n")"
      disown "$SUB_COORD_PID" 2>/dev/null || true
      _pool_set "STARTED_AT" "$issue_n" "$(date +%s)"
      _pool_set "LAST_LOG_TAIL_AT" "$issue_n" "0"
      _pool_set "LAST_LOG_TAIL" "$issue_n" ""

    # Persist PID + monitoring artifacts in main-state.json immediately.
    # Required fields: issue, pid, started_at, context_file, log_file, done_file, report_file.
    # This write must happen before entering the monitor loop for this issue.
  done

  # --- Rolling pool monitor: poll until pool is empty ---
  LAST_PRINTED_STATUS=""
  while [ -n "$POOL_ISSUES" ]; do
    sleep "$STATUS_POLL_SECONDS"

    CURRENT_POOL_ISSUES="$POOL_ISSUES"
    for issue_n in $CURRENT_POOL_ISSUES; do
      pid="$(_pool_get "PID" "$issue_n")"

      if ! kill -0 "$pid" 2>/dev/null; then
        # Sub-coordinator finished — collect exit code and process immediately
        wait "$pid" 2>/dev/null
        _pool_remove_issue "$issue_n"
        _pool_unset_issue "$issue_n"

        # ── Collect report (Step E inline) ───────────────────────────────────
        sub_report="$(pwd -P)/.run-with-it/reports/sub-${issue_n}-report.json"
        printf '=== Sub-coordinator output for issue #%s ===\n' "$issue_n"
        printf 'Full log: .run-with-it/sub/sub-%s.log\n' "$issue_n"
        printf 'Report:   %s\n' "$sub_report"
        if [ -f "$sub_report" ]; then
          # Read compact report JSON into AI context — ONLY file read per issue
          _REPORT_OUTCOME="$(python3 -c "import json; d=json.load(open('$sub_report')); print(d.get('outcome','blocked'))" 2>/dev/null || echo 'blocked')"
        else
          _REPORT_OUTCOME="blocked"
          printf 'WARNING: report missing for issue #%s — marking blocked\n' "$issue_n"
        fi

        # ── Update state + GitHub (Step F inline) ────────────────────────────
        # 1. Update issue_registry[issue_n].status = _REPORT_OUTCOME in main-state.json
        # 2. Remove issue_n from active_pool_issues
        # 3. Append to completed_summaries and ledger_rows
        # 4. Write main-state.json to disk BEFORE any GitHub call
        # 5. Post terminal GitHub comment (Appendix D template) from report JSON
        if [ "$_REPORT_OUTCOME" = "completed" ]; then
          gh issue close "$issue_n" --comment "Completed by run-with-it."
        fi
        # Delete context temp file
        eval "_DONE_CTX_FILE=\"\${SUB_COORD_CONTEXT_FILE_${issue_n}}\""
        rm -f "$_DONE_CTX_FILE" 2>/dev/null || true
        printf 'STATUS|type=sub-coord-complete|issue=%s|outcome=%s|report_file=%s\n' \
          "$issue_n" "$_REPORT_OUTCOME" "$sub_report"

        # ── Fill freed slot immediately ───────────────────────────────────────
        # Re-evaluate ready issues: deps may have resolved now that issue_n completed.
        # Select highest-priority pending issue with all deps "completed".
        # If found: assemble context file → spawn → add to POOL_ISSUES.
        # Emit: STATUS|type=pool-slot-filled|issue=<next>|freed_by=<issue_n>
        # If none: slot stays empty; pool shrinks toward zero.
        continue  # Jump to next pool member check; new spawn is tracked in POOL_ISSUES
      fi

      # Still running — heartbeat and stall detection
      NOW=$(date +%s)
      started_at="$(_pool_get "STARTED_AT" "$issue_n")"
      elapsed=$((NOW - started_at))
      sub_report="$(pwd -P)/.run-with-it/reports/sub-${issue_n}-report.json"
      sub_log="$(pwd -P)/.run-with-it/sub/sub-${issue_n}.log"
      sub_done="$(pwd -P)/.run-with-it/done/issue-${issue_n}-sub-coord.done"
      sub_tail_state="$(pwd -P)/.run-with-it/status/sub-${issue_n}.tail.sha"

      # Use worker-watch to report liveness/log-tail changes for sub-coordinators.
      # Liveness is diagnostic only; completion still requires done sentinel + report artifacts.
      "$ASSET_ROOT/worker-watch.sh" \
        --pid "$pid" \
        --done-file "$sub_done" \
        --log-file "$sub_log" \
        --tail-state-file "$sub_tail_state" \
        --tail-lines 2 >/dev/null || true

      if [ "$elapsed" -ge "$SUB_COORD_TIMEOUT_SECONDS" ] && [ ! -f "$sub_report" ]; then
        printf 'STATUS|type=stall|issue=%s|idle_for=%s|action=alert-user\n' \
          "$issue_n" "$elapsed"
        printf 'Sub-coordinator for issue #%s has not completed after %ss.\n' \
          "$issue_n" "$elapsed"
        printf 'Check log: .run-with-it/sub/sub-%s.log\n' "$issue_n"
      fi
      last_tail_at="$(_pool_get "LAST_LOG_TAIL_AT" "$issue_n")"
      [ -n "$last_tail_at" ] || last_tail_at=0
      if [ "$((NOW - last_tail_at))" -ge "$LOG_TAIL_POLL_SECONDS" ] \
         && [ -s "$sub_log" ]; then
        CURRENT_LOG_TAIL="$(tail -n 2 "$sub_log")"
        last_tail="$(_pool_get "LAST_LOG_TAIL" "$issue_n")"
        if [ "$CURRENT_LOG_TAIL" != "$last_tail" ]; then
          printf '[issue #%s] %s\n' "$issue_n" "$CURRENT_LOG_TAIL"
          _pool_set "LAST_LOG_TAIL" "$issue_n" "$CURRENT_LOG_TAIL"
        fi
        _pool_set "LAST_LOG_TAIL_AT" "$issue_n" "$NOW"
      fi
    done

    # Print changed status line (shared bus — last writer wins)
    if [ -s "$RUN_WITH_IT_STATUS_FILE" ]; then
      CURRENT_STATUS="$(tail -n 1 "$RUN_WITH_IT_STATUS_FILE")"
      if [ "$CURRENT_STATUS" != "$LAST_PRINTED_STATUS" ]; then
        printf '%s\n' "$CURRENT_STATUS"
        LAST_PRINTED_STATUS="$CURRENT_STATUS"
      fi
    fi
  done
  printf 'STATUS|type=pool-empty|pending_remaining=<n>\n'

Always invoke the above Bash call with dangerouslyDisableSandbox: true. This ensures
agent CLIs (claude, codex, copilot, gemini) can authenticate and run outside Claude
Code's sandbox. GUI_MODE=0 preserves full permission flags (--dangerously-skip-permissions,
--dangerously-bypass-approvals-and-sandbox) required for unattended execution.

For tool runtimes that provide sync/async terminal modes:
  - Use async mode for Step D and keep monitoring via the returned terminal/session id.
  - Do not run spawn in one shell call and liveness monitor in another unrelated call.
  - If a shell lifecycle is interrupted, recover from persisted state instead of assuming
    in-memory PID variables still exist.

PowerShell (Windows — never use VAR=value prefix):
  # Spawn newly queued issues as background jobs
  foreach ($issue_n in $NEWLY_QUEUED) {
    $env:AGENT_REGISTRY_FILE = "$ASSET_ROOT\agent-registry.json"
    $env:GUI_MODE = if ($env:GUI_MODE) { $env:GUI_MODE } else { "0" }
    $env:RUN_WITH_IT_STATUS_FILE = "$RUN_WITH_IT_STATUS_FILE"
    $env:RUN_WITH_IT_EVENTS_LOG = "$RUN_WITH_IT_EVENTS_LOG"
    $env:RUN_WITH_IT_LOG_FILE = ".run-with-it\sub\sub-${issue_n}.log"
    $env:RUN_WITH_IT_DONE_FILE = ".run-with-it\done\issue-${issue_n}-sub-coord.done"
    $env:RUN_WITH_IT_ROLE = "sub-coord"
    $env:RUN_WITH_IT_ISSUE = "$issue_n"
    $Jobs[$issue_n] = Start-Job -ScriptBlock {
      & "$using:ASSET_ROOT\run-agent.ps1" --agent $using:SUB_COORD_AGENT `
        --model $using:SUB_COORD_MODEL `
        --context-file $using:SUB_COORD_CONTEXT_FILES[$using:issue_n] `
        --prompt-file "$using:ASSET_ROOT\sub-coordinator-prompt.md" --unattended
    }
  }
  # Rolling poll: on any completion, process report + fill slot immediately
  while ($Jobs.Count -gt 0) {
    Start-Sleep -Seconds $STATUS_POLL_SECONDS
    $finished = $Jobs.GetEnumerator() | Where-Object { $_.Value.State -ne 'Running' }
    foreach ($entry in $finished) {
      $issue_n = $entry.Key
      $Jobs.Remove($issue_n)
      # Collect report (Step E inline), update state + GitHub (Step F inline),
      # fill freed slot immediately (Step B logic inline).
    }
  }

**While monitoring: do not read log files into AI context. Do not infer agent or model choices.
Do not kill or restart any Sub-Coordinator. A stall for one pool member does not affect others —
continue the pool or mark the stalled issue blocked on user instruction.**

If `run-with-it-dispatch.sh` / `run-agent.sh` fails for an issue despite dangerouslyDisableSandbox: true, it is a true agent failure.
Emit: STATUS|type=runner-sandbox-retry-result|issue=<n>|outcome=failed

If a Sub-Coordinator runs longer than SUB_COORD_TIMEOUT_SECONDS without producing its report file:
  STATUS|type=stall|issue=<n>|idle_for=<seconds>|action=alert-user
  Print: "Sub-coordinator for issue #<n> has not completed after <t>s."
  Print: "Check log: .run-with-it/sub/sub-<n>.log"
  Other pool members continue running. Wait for user: type 'continue' to keep
  waiting, or 'skip' to mark as blocked and remove from pool (freeing the slot).

Do NOT use a stall or timeout as justification to inspect logs, infer routing, or restart.
GitHub operations triggered by pool completions are sequential per issue to avoid races.

══ GOTO STEP A ═════════════════════════════════════════════════════════════════
```

## `run-with-it-dispatch.sh` — Shared Role Launcher

`run-with-it-dispatch.sh` is the shared run-with-it orchestration primitive. The Main Orchestrator uses it with `--role sub-coord`; Sub-Coordinators use it with `--role complexity`, `--role impl`, `--role review`, or `--role modify`. It wraps `run-agent.sh`, forwards the role-specific `RUN_WITH_IT_*` environment, writes dispatch status lines, and monitors the done/result artifacts through `worker-watch.sh`.

```bash
run-with-it-dispatch.sh \
  --role <sub-coord|complexity|impl|review|modify|merge-recovery> \
  --issue <n> \
  --cycle <n> \
  --agent <agent> \
  --model <model> \
  --context-file <file> \
  --prompt-file <file> \
  --log-file <file> \
  --done-file <file> \
  --result-file <file> \
  --repo-root <worktree-or-repo-path> \
  --status-file <file> \
  --events-log <file>
```

Use `--dry-run` to print the wrapped `run-agent.sh` invocation, and `--validate-only` to verify inputs and emit `STATUS|type=dispatch-ready` without spawning.

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
| `--repo-root <path>` | `REPO_ROOT` | No | Working directory passed to the agent; Sub-Coordinators use issue worktrees |
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
- Delete all files under `.run-with-it/reports/`, `.run-with-it/sub/`, `.run-with-it/main/`, `.run-with-it/done/`, `.run-with-it/complexity/`, `.run-with-it/impl/`, `.run-with-it/review/`, `.run-with-it/modify/`, `.run-with-it/reviews/`, `.run-with-it/worktrees/`, `.run-with-it/locks/`, and `.run-with-it/merge-recovery/`.
- Remove issue worktrees with `git worktree remove` when possible. Preserve the shared run feature branch after final PR creation.
- Remove `.run-with-it/` directory if empty.
- For each of `technical_requirements.md`, `prd.md`, and `issues.md` present in the workspace root: run `git status --short <file>`. Delete the file **only if** it is untracked (`??`) or clean (not listed). If the file has user modifications (any other status), skip deletion and emit `STATUS|type=cleanup|action=skipped-dirty-file|file=<file>` — never delete user-modified workspace files.
- Ensure `.gitignore` contains entries for `.run-with-it/`, `technical_requirements.md`, `prd.md`, and `issues.md` using an idempotent append.
- If `.git/` exists, stage only the deleted files and `.gitignore`; commit with message `chore: remove skill-generated artifacts post-run`.
- Emit `STATUS|type=cleanup|action=completed|files_removed=<n>`.

### Failed or Interrupted Run

On failed or interrupted run:

- Keep all `.run-with-it/` files, including preserved worktrees and locks for inspection.
- Print the paths of preserved files (state, reports, logs, reviews).
- Offer `discard` command to force-delete and restart.

### Discard

On `discard`:

- Delete all preserved files including `.run-with-it/main-state.json`, all reports, logs, reviews, worktrees, locks, `technical_requirements.md`, `prd.md`, and `issues.md`.
- Update `.gitignore` and commit with message `chore: remove skill-generated artifacts (discarded run)`.
- Emit `STATUS|type=cleanup|action=discarded|files_removed=<n>`.
- Proceed as a fresh run.

## Appendix A: Main State Schema

The Main Orchestrator persists `.run-with-it/main-state.json` (schema_version 4). This is the Main Orchestrator's entire persistent memory.

```json
{
  "schema_version": 4,
  "run_id": "<uuid generated at run start>",
  "started_at": "<iso8601>",
  "run_branch": {
    "base_branch": "main",
    "base_sha": "abc123",
    "feature_branch": "run-with-it/<run-id>",
    "feature_branch_start_sha": "abc123",
    "remote": "origin",
    "pushed": true,
    "pr_url": null
  },
  "execution_plan": {
    "execution_mode": "sequential | rolling-pool",
    "parallel_jobs": 4,
    "topo_order": [36, 37],
    "dependency_tiers": [[36], [37]],
    "pool_config": {
      "max_concurrent": 4,
      "fill_strategy": "rolling"
    }
  },
  "issue_registry": {
    "36": {
      "status": "completed | in_progress | pending | merge_recovery | failed-review | failed-merge | blocked",
      "title": "issue title",
      "deps": [],
      "dependency_proof": "Blocked by: None",
      "report_file": ".run-with-it/reports/sub-36-report.json",
      "merge_recovery_report_file": ".run-with-it/reports/merge-recovery-36-report.json",
      "log_file": ".run-with-it/sub/sub-36.log",
      "issue_branch": "run-with-it/<run-id>/issue-36",
      "worktree_path": ".run-with-it/worktrees/issue-36",
      "commit_sha": "abc1234"
    }
  },
  "active_pool_issues": [37, 38],
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
- `run_branch` captures the shared feature branch used for the final PR
- `topo_order` and `dependency_tiers` are derived from issue dependency topological sorting
- `completed_summaries` accumulates one compact record per finished issue — this is what the main orchestrator reads back after compression
- `merge_recovery` is non-terminal; dependencies are satisfied only by `completed`
- `ledger_rows` stores verbatim STATUS lines for the final ledger printout
- `active_pool_issues` lists which issues had active Sub-Coordinators; on resume all `in_progress` issues in `active_pool_issues` are reset to `pending` (Sub-Coordinators are ephemeral — re-spawn them fresh)
- When `PARALLEL_JOBS=1`, `active_pool_issues` always has at most one entry

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

- plan: `STATUS|type=plan|total_issues=<n>|mode=<sequential|rolling-pool>|parallel_jobs=<PARALLEL_JOBS>|pending=<n>|blocked=<n>`
- pool fill: `STATUS|type=pool-fill|active=<count>|newly_queued=<count>|pending_remaining=<count>|parallel_jobs=<PARALLEL_JOBS>`
- pool slot filled: `STATUS|type=pool-slot-filled|issue=<n>|freed_by=<m>|pool_size=<count>`
- pool empty: `STATUS|type=pool-empty|pending_remaining=<n>`
- merge recovery queued: `STATUS|type=merge-recovery|issue=<n>|report_file=<path>|state=merge_recovery`
- memory refresh: `STATUS|type=memory-refresh|state_file=.run-with-it/main-state.json|tasks_loaded=<n>|completed=<n>|pending=<n>|failed=<n>`
- main loop: `STATUS|type=main-loop|iteration=<n>|pending=<count>|completed=<count>|failed=<count>`
- sub-coordinator spawn: `STATUS|type=sub-coord-spawn|issue=<n>|agent=<name>|model=<model>|report_file=<path>|log_file=<path>|pool_size=<n>|parallel_jobs=<PARALLEL_JOBS>`
- sub-coordinator pid-tracked: `STATUS|type=sub-coord-pid|issue=<n>|pid=<pid>|done_file=<path>|report_file=<path>`
- live agent start: `STATUS|type=agent-start|issue=<n>|role=<sub-coord|complexity|impl|review|modify>|agent=<name>|model=<model>`
- live agent complete: `STATUS|type=agent-complete|issue=<n>|role=<sub-coord|complexity|impl|review|modify>|agent=<name>|model=<model>|status=<success|failed>`
- worker done: `STATUS|type=worker-done|issue=<n>|role=<complexity|impl|review|modify>|phase=<phase>|source=<agent|runner-exit>`
- live heartbeat: `STATUS|type=heartbeat|issue=<n>|role=<impl|review|modify>|phase=<exploring|implementing|testing|review>|progress=<short-text>`
- sub-coordinator complete: `STATUS|type=sub-coord-complete|issue=<n>|outcome=<completed|failed-review|merge_failed|blocked>|report_file=<path>|commit_sha=<sha-or-none>`
- merge start: `STATUS|type=merge-start|issue=<n>|branch=<issue_branch>|target=<feature_branch>`
- merge complete: `STATUS|type=merge-complete|issue=<n>|merge_sha=<sha>|pushed=<true|false>`
- merge failed: `STATUS|type=merge-failed|issue=<n>|reason=<conflict|verification|push|unknown>`
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
2. Identify all issues with `status="in_progress"` — these had a Sub-Coordinator interrupted mid-run. Reset ALL of them to `status="pending"` (Sub-Coordinators are ephemeral; re-spawn them fresh). Also clear `active_pool_issues` to `[]`.
3. Identify all issues with `status="pending"` — these haven't started yet.
4. Identify all issues with `status="completed"`, `"failed-review"`, or `"blocked"` — skip these entirely.
5. Re-enter Main Loop at Step A (which fills the rolling pool immediately).
6. Emit: `STATUS|type=resume|tasks_restored=<n>|completed=<n>|re_queued_in_progress=<m>|parallel_jobs=<PARALLEL_JOBS>`

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
4. Issues with `status="in_progress"` are reset to `"pending"` — re-spawn their Sub-Coordinators fresh (pool members interrupted before compression are re-queued and the rolling pool refills them).
5. Clear `active_pool_issues` to `[]` in main-state.json.
6. Continue the Main Loop as if resuming from a clean slate.

Never derive issue state from conversation history after compression. The state file is always authoritative.

## Appendix D: Terminal Issue Comment Contract

### Terminal Issue Comments

Post issue comments only for terminal outcomes: `completed`, `blocked`, or `failed-review`.
Each terminal comment must be posted only after reading the compact report.
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
- **Never kill or restart individual Sub-Coordinators mid-run.** A stall alert for one pool member does not justify interrupting the others — continue the pool or mark the stalled issue blocked on user instruction.
- **GitHub operations on completion are sequential.** Even when Sub-Coordinators run in parallel, each issue's GitHub comment/close is processed one at a time as it completes to avoid race conditions.
- Preserve local fallback behavior when GitHub or git is unavailable.
- Keep changes minimal and focused to orchestration/control-plane behavior.
