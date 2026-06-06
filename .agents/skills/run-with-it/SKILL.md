---
name: run-with-it
description: Two-layer orchestration runtime — Main Orchestrator fetches all issues, plans execution order, maintains a rolling pool of Sub-Coordinators (up to PARALLEL_JOBS concurrently), fills freed slots immediately on completion, and updates GitHub. Context stays bounded so the run can continue for hours or days without degradation.
---

## Skill Isolation

Sole active authority once invoked — no other skill may activate unless called by name via `Skill` tool call; suppress spontaneous external skills until explicit termination or handoff. This isolation governs orchestration flow only; subordinate core behavior, native tool use, and reasoning remain fully operational and cannot be overridden by this skill.

## Critical Main Orchestrator Rules (compaction-safe — always enforce, even after context compression)

These rules apply for the entire lifetime of this skill session. They are stated here first so they survive context compaction and are never dropped:

- **Re-read `.run-with-it/main-state.json` before every loop iteration.** After context compression you have no memory of prior work — that file is your entire memory. Never derive issue state from conversation history.
- **Never implement work directly in this session.** All implementation belongs to Sub-Coordinators spawned via the platform dispatcher (`scripts/run-with-it-dispatch.sh` on Bash, `powershell/run-with-it-dispatch.ps1` on native PowerShell) with `role=sub-coord`, which wraps `scripts/run-agent.sh` / `powershell/run-agent.ps1` with `prompts/sub-coordinator-prompt.md`. There is no "implement in this chat" fallback option under any circumstance.
- **Never run tests, build commands, or compile the project** in this session. Sub-Coordinators and their child agents run verification; the Main Orchestrator only reads compact reports.
- **Never pause after planning to ask the user how to proceed.** Enter the Main Loop immediately after the execution plan is written.
- **Never present execution option menus** (Option A / B / C style choices).
- **Always pull issue data from GitHub** (`gh`) when a remote exists. Only fall back to local files if `gh` is unavailable, authentication fails, an approved permission-escalation attempt fails, or no GitHub remote exists.
- **Never delete user-modified files** during cleanup. Check `git status --short` before removing any workspace artifact.
- **Never load full sub-coordinator log files into context.** Sub-Coordinator logs live under `.run-with-it/issues/<n>/sub-coordinator.log`. Do not tail raw logs into AI context; only read the compact report JSON from `.run-with-it/issues/<n>/report.json`.
- **Never load live status logs into context.** Live progress is written to `.run-with-it/status/current.txt` and `.run-with-it/status/events.log`; shell watchers may print one changed line to the terminal, but the Main Orchestrator must not read those files into AI memory.
- **GitHub operations (close, comment, e.g., gh issue close) are the Main Orchestrator control plane's sole responsibility.** Sub-Coordinators never touch GitHub. The pool runner performs the per-issue terminal comment/close immediately after reading a terminal compact report.
- **Never inspect, infer, or act on a Sub-Coordinator's internal routing decisions.** Once a Sub-Coordinator is spawned, the agent and model it selects for its child workers are entirely its own responsibility — the Main Orchestrator has no visibility into, and no authority over, those internal choices. Do not read log files to determine which worker agent or model is running.
- **Never kill, cancel, or restart a Sub-Coordinator mid-run under any circumstance.** If a Sub-Coordinator appears to be using a different agent or model than expected, that is correct behavior — it is applying its own complexity-based routing. Do not intervene. The only valid responses to a running Sub-Coordinator are: (a) wait for it to complete and write its compact report, or (b) alert the user after `SUB_COORD_TIMEOUT_SECONDS` and wait for a 'continue' or 'skip' instruction.
- **Never inject AGENT or MODEL overrides into a Sub-Coordinator that has already been spawned.** Routing overrides (`AGENT`, `MODEL`, `COMPLEXITY_LEVEL`, `COMPLEXITY_SCORE`) may only be set before spawning, as part of the context file assembled in Step C. After the platform dispatcher calls `scripts/run-agent.sh` / `powershell/run-agent.ps1`, those values are locked and the Main Orchestrator must not attempt to change them.
- **Run the platform pool runner (`scripts/run-with-it-pool.sh` / `powershell/run-with-it-pool.ps1`) as the single rolling-pool supervisor.** The pool runner spawns Sub-Coordinator dispatch processes, captures each dispatcher PID, and persists `issue`, `pid`, `started_at`, `context_file`, `log_file`, `done_file`, and `report_file` before monitoring.
- **Use the platform worker watcher (`scripts/worker-watch.sh` / `powershell/worker-watch.ps1`) inside the dispatcher for Sub-Coordinator liveness checks during pool monitoring.** Pass each dispatch child PID, `done_file`, and `log_file`; treat PID liveness as diagnostic only. Completion requires the done sentinel and compact report artifacts.
- **Worker artifact failures are Sub-Coordinator recovery work, not Main Orchestrator work.** If an implementation, review, or modification worker exits without valid artifacts, the dispatcher records failure facts in the worker state file and the Sub-Coordinator must retry/salvage according to its role-specific recovery contract or write a blocked compact report with a concrete recovery handoff. The Main Orchestrator must not inspect logs or restart workers itself.
- **All judgments about implementation quality, routing correctness, and worker behavior come exclusively from the compact report JSON.** The Main Orchestrator has no other source of truth about what happened inside a Sub-Coordinator session.
- **GitHub operations on completion are immediate and sequential.** Even when Sub-Coordinators run in parallel, each issue's GitHub comment/close is processed one at a time as soon as that issue reaches a terminal outcome to avoid race conditions.
- **Preserve local fallback behavior when GitHub or git is unavailable.**
- **Keep changes minimal and focused to orchestration/control-plane behavior.**

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
- Maintains a rolling pool of up to `PARALLEL_JOBS` active **Sub-Coordinators** via the platform dispatcher — freed slots fill immediately when any job completes rather than waiting for whole batches
- As each Sub-Coordinator completes, reads its compact report, immediately posts the terminal GitHub comment and closes/updates that issue when it has a terminal outcome, then spawns the next ready issue into the freed slot
- Writes its own status log to `.run-with-it/main/main.log`
- Reads ONLY the compact report JSON — never the implementation diffs or log files
- Updates `main-state.json` after each issue (its full external memory)
- Posts terminal GitHub comments and closes/updates issues immediately per issue, not only after the full pool finishes
- Spawns a Merge Recovery Coordinator when a Sub-Coordinator reports `merge_failed`; Main Orchestrator never merges issue branches itself
- Creates one final PR from the shared run feature branch after all issues are terminal
- Re-reads `main-state.json` at the top of every loop iteration to survive context compression

**Sub-Coordinator** (spawned via `prompts/sub-coordinator-prompt.md`, runs in a child agent session):
- Handles exactly ONE issue end-to-end
- Creates an issue branch and issue worktree from the shared run feature branch
- Runs complexity analysis, deterministic routing, implementation, review, and modification loops
- Runs child workers with `REPO_ROOT` pointing at the issue worktree while keeping logs/reports under the root `.run-with-it/`
- Attempts the normal merge back into the shared feature branch under `.run-with-it/locks/merge.lock`
- Writes a compact report JSON and full log file under `.run-with-it/issues/<n>/` when done
- Spawns worker agents whose logs/results/done sentinels are written under `.run-with-it/issues/<n>/workers/<role>/`
- Never touches GitHub; never updates `main-state.json`

**Merge Recovery Coordinator** (spawned via `prompts/merge-recovery-prompt.md`, runs only after `merge_failed`):
- Handles one failed issue-branch merge
- Reads the shared feature branch holistically because it contains prior Sub-Coordinator work
- Resolves conflicts or merge-induced verification failures under the same merge lock
- Pushes the shared feature branch on success and writes a compact recovery report
- Never closes issues, creates the final PR, or updates `main-state.json`

This isolation means each issue's implementation complexity is contained to its own isolated Sub-Coordinator session. The Main Orchestrator's context grows by only one compact JSON record per completed issue, allowing runs of hours or days without context degradation.

## Hard Boundaries

- Do not synthesize PRDs.
- Do not author initial issue templates.
- Do not redefine reviewer JSON schema ownership (owned by `assets/prompts/review-prompt.md`).
- Do not modify runner script implementation details.
- Do not mutate registry data definitions in `assets/agent-registry.json`.

## OS Detection

Detect the current OS before asset discovery and runner selection, and capture it in the `OS_FAMILY` environment variable:

- **Windows (native PowerShell) (`OS_FAMILY=windows`):** use `.ps1` runners (`powershell/run-with-it-pool.ps1`, `powershell/run-with-it-dispatch.ps1`, `powershell/worker-watch.ps1`, `powershell/run-agent.ps1`) and `$env:USERPROFILE` for home dir.
- **macOS / Linux / Git Bash / WSL (`OS_FAMILY=unix`):** `uname -s` returns `Darwin`, `Linux`, `MINGW*`, `MSYS*`, or `CYGWIN*`. Use `.sh` runners and `$HOME` for home dir.

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

Provide a task summary before execution. All other inputs are optional overrides.

| Variable | Default | Description |
|----------|---------|-------------|
| `ASSETS_DEST` | — | Asset root override |
| `AGENT_REGISTRY_FILE` | — | Registry file override |
| `ISSUE_LABEL` | `ready-for-agent` | Label filter for issue intake |
| `ISSUE_LIMIT` | `1000` | Max issues to fetch (fetches all by default) |
| `ISSUE_STATE` | `open` | Issue state filter |
| `COMMITS_LIMIT` | `5` | Recent commits included in Sub-Coordinator context |
| `MAX_ITERATIONS` | `20` | Max review/modify cycles per Sub-Coordinator |
| `SUB_COORD_AGENT` | `codex` | Agent slug for every Sub-Coordinator |
| `SUB_COORD_MODEL` | `gpt-5.5` | Model for every Sub-Coordinator (Sub-Coordinators route their own children independently) |
| `SUB_COORD_TIMEOUT_SECONDS` | `3600` | Seconds before stall alert for a non-completing Sub-Coordinator |
| `STATUS_POLL_SECONDS` | `10` | Shell polling cadence for status line output |
| `LOG_TAIL_POLL_SECONDS` | `120` | Shell polling cadence for sub-coordinator log tail |
| `RUN_WITH_IT_STATUS_FILE` | `.run-with-it/status/current.txt` | Single-line status bus (overwritten each update) |
| `RUN_WITH_IT_EVENTS_LOG` | `.run-with-it/status/events.log` | Append-only event log — terminal inspection only; never load into AI context |
| `RUN_WITH_IT_ISSUE_DIR` | `.run-with-it/issues/<n>` | Issue-scoped artifact folder created by the Sub-Coordinator/pool |
| `RUN_WITH_IT_LOG_FILE` | role-specific | Sub-Coordinators: `.run-with-it/issues/<n>/sub-coordinator.log`; workers: `.run-with-it/issues/<n>/workers/<role>/cycle-<cycle>.log` |
| `RUN_WITH_IT_DONE_FILE` | role-specific | Workers: `.run-with-it/issues/<n>/workers/<role>/cycle-<cycle>.done` |
| `RUN_WITH_IT_RESULT_FILE` | role-specific | Workers: `.run-with-it/issues/<n>/workers/<role>/cycle-<cycle>-result.json` |
| `RUN_WITH_IT_STATE_FILE` | role-specific | Workers: `.run-with-it/issues/<n>/workers/<role>/cycle-<cycle>.state.json`; dispatcher-maintained watchdog state |
| `AGENT` | — | Routing override passed through to Sub-Coordinators |
| `MODEL` | — | Routing override passed through to Sub-Coordinators |
| `COMPLEXITY_LEVEL` | — | Routing override passed through to Sub-Coordinators |
| `COMPLEXITY_SCORE` | — | Routing override passed through to Sub-Coordinators |
| `AGENT_ALLOWLIST` | — | Comma-separated; passed through to Sub-Coordinators |
| `AGENT_DENYLIST` | — | Comma-separated; passed through to Sub-Coordinators |
| `MAX_AGENT_FALLBACKS` | `2` | Max agent fallback attempts; passed through |
| `DELEGATED_REVIEW` | `true` | Enable Sub-Coordinator delegated review; passed through |
| `MAX_AGENT_DEPTH` | `1` | Always injected; prevents Sub-Coordinator children from spawning sub-agents |
| `PARALLEL_JOBS` | `4` | Rolling pool size. Freed slots fill immediately. Set to `1` for sequential. |
| `RUN_WITH_IT_HELPER_RUNTIME` | `python` | Helper runtime selector: `python` or `csharp` |
| `PYTHON_BIN` | `python3` | Python executable for helper scripts |
| `DOTNET_BIN` | `dotnet` | .NET executable for C# helper scripts (`RUN_WITH_IT_HELPER_RUNTIME=csharp`; requires .NET SDK 10+) |

## Asset Discovery (Required)

Resolve assets in this order:

1. `$ASSETS_DEST` if set and complete.
2. `$HOME/.ai-skill-collections/assets`.
3. `./assets`.

Shared required files:

- `prompts/prompt.md`
- `agent-registry.json`
- `prompts/review-prompt.md`
- `prompts/modifier-prompt.md`
- `prompts/complexity-prompt.md`
- `prompts/coordinator-rules.md`
- `prompts/sub-coordinator-prompt.md`
- `prompts/main-orchestrator-rules.md`
- `prompts/merge-recovery-prompt.md`
- `python/run-with-it-state.py`
- `python/run-with-it-github-update.py`
- `python/run-with-it-pr-body.py`
- `python/run-with-it-router.py`
- `python/run-with-it-artifacts.py`

Bash required helper files:

- `scripts/run-agent.sh`
- `scripts/run-with-it-dispatch.sh`
- `scripts/run-with-it-pool.sh`
- `scripts/worker-watch.sh`

PowerShell required helper files:

- `powershell/run-agent.ps1`
- `powershell/run-with-it-dispatch.ps1`
- `powershell/run-with-it-pool.ps1`
- `powershell/worker-watch.ps1`

Selection rules:

- Use first path that contains all shared files plus the helper files for the detected platform.
- Bash/macOS/Linux/Git Bash/WSL runs must not require `.ps1` helper files.
- Native PowerShell runs must not require `.sh` helper files.
- If `RUN_WITH_IT_HELPER_RUNTIME=csharp`, require `DOTNET_BIN` (requires `.NET SDK 10+`) for shared helper scripts. If `RUN_WITH_IT_HELPER_RUNTIME=python`, require `python3` or `PYTHON_BIN` for shared helper scripts.
- If none are complete, stop and report missing files.
- Do not require git to resolve assets.
- Resolved asset root is the single source for that run.

### Fresh/No-Git Project Notes

- This skill must work in folders that are not initialized with git.
- Asset discovery is filesystem-based, not git-root-based.
- If assets are missing, report the platform-appropriate one-command fix:

**PowerShell (Windows):**
```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.ai-skill-collections\assets\prompts", "$env:USERPROFILE\.ai-skill-collections\assets\powershell", "$env:USERPROFILE\.ai-skill-collections\assets\python" | Out-Null; $dest = "$env:USERPROFILE\.ai-skill-collections\assets"; Copy-Item -Force .\assets\python\run-with-it-state.py, .\assets\python\run-with-it-github-update.py, .\assets\python\run-with-it-pr-body.py, .\assets\python\run-with-it-router.py, .\assets\python\run-with-it-artifacts.py "$dest\python\" -ErrorAction SilentlyContinue; Copy-Item -Force .\assets\prompts\prompt.md, .\assets\prompts\sub-coordinator-prompt.md, .\assets\prompts\main-orchestrator-rules.md, .\assets\prompts\merge-recovery-prompt.md, .\assets\prompts\complexity-prompt.md, .\assets\prompts\review-prompt.md, .\assets\prompts\modifier-prompt.md, .\assets\prompts\coordinator-rules.md "$dest\prompts\"; Copy-Item -Force .\assets\powershell\run-agent.ps1, .\assets\powershell\run-with-it-dispatch.ps1, .\assets\powershell\run-with-it-pool.ps1, .\assets\powershell\worker-watch.ps1 "$dest\powershell\"; Copy-Item -Force .\assets\agent-registry.json "$dest\"
```

**Bash (macOS / Linux / Git Bash):**
```bash
mkdir -p "$HOME/.ai-skill-collections/assets"/{prompts,scripts,python}; mkdir -p "$HOME/.ai-skill-collections/assets" && cp -f ./assets/python/run-with-it-state.py ./assets/python/run-with-it-github-update.py ./assets/python/run-with-it-pr-body.py ./assets/python/run-with-it-router.py ./assets/python/run-with-it-artifacts.py "$HOME/.ai-skill-collections/assets/python/" && cp -f ./assets/prompts/prompt.md ./assets/prompts/sub-coordinator-prompt.md ./assets/prompts/main-orchestrator-rules.md ./assets/prompts/merge-recovery-prompt.md ./assets/prompts/complexity-prompt.md ./assets/prompts/review-prompt.md ./assets/prompts/modifier-prompt.md ./assets/prompts/coordinator-rules.md "$HOME/.ai-skill-collections/assets/prompts/" && cp -f ./assets/scripts/run-agent.sh ./assets/scripts/run-with-it-dispatch.sh ./assets/scripts/run-with-it-pool.sh ./assets/scripts/worker-watch.sh "$HOME/.ai-skill-collections/assets/scripts/" && cp -f ./assets/agent-registry.json "$HOME/.ai-skill-collections/assets/" && chmod +x "$HOME/.ai-skill-collections/assets/scripts/run-agent.sh" "$HOME/.ai-skill-collections/assets/scripts/run-with-it-dispatch.sh" "$HOME/.ai-skill-collections/assets/scripts/run-with-it-pool.sh" "$HOME/.ai-skill-collections/assets/scripts/worker-watch.sh" "$HOME/.ai-skill-collections/assets/python/run-with-it-state.py" "$HOME/.ai-skill-collections/assets/python/run-with-it-github-update.py" "$HOME/.ai-skill-collections/assets/python/run-with-it-pr-body.py" "$HOME/.ai-skill-collections/assets/python/run-with-it-router.py" "$HOME/.ai-skill-collections/assets/python/run-with-it-artifacts.py"
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

1. Resolved asset root exists and contains all required files listed in Asset Discovery. On Bash, runners (`scripts/run-agent.sh`, `scripts/run-with-it-dispatch.sh`, `scripts/run-with-it-pool.sh`, `scripts/worker-watch.sh`) and helper executables are present according to `RUN_WITH_IT_HELPER_RUNTIME`. For `python`, required files are `python/run-with-it-state.py`, `python/run-with-it-github-update.py`, `python/run-with-it-pr-body.py`, `python/run-with-it-router.py`, `python/run-with-it-artifacts.py`. For `csharp`, require `csharp` helper executables and `DOTNET_BIN` (`.NET SDK 10+`). On native PowerShell, verify the `.ps1` runners exist; executable bits are not required.
2. `RUN_WITH_IT_HELPER_RUNTIME` is `python` (default) or `csharp`. For `python`, require `python3` or `PYTHON_BIN` for helper scripts. For `csharp`, require `DOTNET_BIN` and a local `.NET SDK 10+`.
3. `gh` auth when GitHub intake is required.
4. `SUB_COORD_AGENT` is installed (detected): on Bash, run `"$ASSET_ROOT/scripts/run-agent.sh" --list-agents --detected-only`; on native PowerShell, run `& (Join-Path $ASSET_ROOT "powershell/run-agent.ps1") --list-agents --detected-only`. Confirm `SUB_COORD_AGENT` appears.
5. `SUB_COORD_MODEL` is in `SUB_COORD_AGENT`'s `known_models` in `agent-registry.json`.
6. **Existing-state detection** (resume vs. discard prompt): before any issue intake or fresh task selection, check whether `.run-with-it/main-state.json` exists in the current working directory.

   - If it exists, pause and present exactly this prompt to the user:

     ```
     Existing run state found at .run-with-it/main-state.json.
     Type "resume" to continue the previous run, or "discard" to delete it and start fresh.
     ```

   - **`resume`**: do not delete the file. Proceed to the Resume Flow section.
   - **`discard`**: apply the Cleanup `Discard` policy, then continue with normal preflight and fresh issue intake as if no prior state existed.
   - Do not start any new task, fetch any issue, or spawn any Sub-Coordinator until the user responds.

If any required file from Asset Discovery is missing at the resolved asset root, fail fast with the same platform-appropriate one-line fix message used in asset discovery.

## Initial Batch Issue Fetch

If issue data is missing in context, fetch only open issues with the configured intake label (`ready-for-agent` by default) at startup.

Use `ISSUE_LIMIT` (default `1000`) as the `--limit` argument — this fetches all matching issues by default. Do not cap the result unless the user explicitly sets `ISSUE_LIMIT` to a lower value.

```bash
gh issue list --state "${ISSUE_STATE:-open}" --label "${ISSUE_LABEL:-ready-for-agent}" --limit "${ISSUE_LIMIT:-1000}" --json number,title,labels,body,url
```

Fallback policy:

- Primary: GitHub issues via `gh`. **Always use GitHub when the repo has a GitHub remote. Never silently fall back to a local file when GitHub may be reachable.**
- If `gh` fails because the current tool is sandboxed (permission error, named-pipe, socket), use that tool's explicit approved permission-escalation flow when available before considering fallback. If escalation is unavailable or denied, emit `STATUS|type=intake-fallback|reason=gh-permission-blocked` and use local fallback only when allowed below.
- Fallback: local `issues.md` (`LOCAL_ISSUES_FILE` override supported) — **only** when `gh` is unavailable, authentication fails, an approved permission-escalation attempt fails, or no GitHub remote exists. Emit `STATUS|type=intake-fallback|reason=<no-gh-auth|no-remote|gh-permission-blocked|gh-failed-after-escalation>` before using local file.
- If git metadata is unavailable, continue with empty commit context.

Before fetching work begins, create the shared run feature branch:

1. Capture original base branch and SHA.
2. Create `run-with-it/<run-id>` from that base.
3. Push the branch when a GitHub remote exists.
4. Record `run_branch.base_branch`, `run_branch.base_sha`, `run_branch.feature_branch`, `run_branch.feature_branch_start_sha`, `run_branch.remote`, and `run_branch.pushed`.

After fetching all issues:

1. Filter the fetched issue set before planning: every executable issue must have the configured intake label (`ready-for-agent` by default). Do not add unlabelled issues, PRD/parent issues, `needs-triage` issues, or issues discovered only through cross-references to `main-state.json`.
2. Build a dependency graph only from each executable issue's `## Blocked by` section. Normalize `#123`, full GitHub issue URLs, and plain issue numbers. Treat `None - can start immediately` as no dependencies.
3. Treat PRD/parent references as context, not dependencies. Ignore issue references from `## Parent`, titles such as `PRD: ...`, labels such as `needs-triage`, and incidental issue links elsewhere in the body when computing `deps`.
4. A dependency is actionable only if it points to another fetched executable issue in the same intake set. If `## Blocked by` names a PRD/parent issue or an issue outside the intake set, ignore it and record the ignored reference in `dependency_proof` as non-blocking context rather than marking the issue blocked.
5. Detect cycles and unresolved dependencies among executable issues only; mark affected issues `blocked` with `dependency_proof` and `blocking_reasons`.
6. Determine execution order: topological sort respecting dependencies. Priority order within the same dependency tier: critical fixes → development infrastructure → tracer-bullet feature slices → polish and quick wins → refactors. When `PARALLEL_JOBS > 1`, issues fill a rolling pool (up to `PARALLEL_JOBS` active at a time) — freed slots are filled immediately rather than waiting for a full batch to complete.
7. Issues whose executable dependencies have open/unresolved status, `merge_recovery`, `failed-merge`, or `blocked` are not ready until the dependency becomes `completed`. The pool runner dispatches merge recovery for `merge_recovery` issues before dependents become ready.
8. Write the complete execution plan to `.run-with-it/main-state.json` before doing any work. Record `parallel_jobs`, `execution_mode` (`sequential` when `PARALLEL_JOBS=1`, `rolling-pool` otherwise), `topo_order`, `dependency_tiers`, and each issue's `dependency_proof`.
9. Emit: `STATUS|type=plan|total_issues=<n>|mode=<sequential|rolling-pool>|parallel_jobs=<PARALLEL_JOBS>|pending=<n>|blocked=<n>`
10. Emit: `STATUS|type=memory-refresh|state_file=.run-with-it/main-state.json|tasks_loaded=<n>|completed=0|pending=<n>`

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
    Leave issue status="pending" until the platform pool runner spawns it.
    Ensure its context file path is recorded in main-state.json during Step C.
    The pool runner marks status="in_progress" and appends <n> to active_pool_issues when it captures the dispatcher PID.
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
     If gh fails because the current tool is sandboxed, use that tool's explicit approved permission-escalation flow when available. If the approved retry fails and local file exists,
     use cached issue body with a note.
  2. Last COMMITS_LIMIT (default 5) recent commits:
       git log --oneline -<COMMITS_LIMIT>
  3. If .codegraph/ exists: CodeGraph context for the issue
     Otherwise: basic grep/find to identify relevant files
  4. Environment configuration block (append at end of context file):
     SUB_COORD_ISSUE_NUMBER=<n>
     OS_FAMILY=<unix|windows>
     RUN_WITH_IT_ISSUE_DIR=<abs-path-to-.run-with-it/issues/<n>>
     SUB_COORD_REPORT_FILE=<abs-path-to-.run-with-it/issues/<n>/report.json>
     SUB_COORD_LOG_FILE=<abs-path-to-.run-with-it/issues/<n>/sub-coordinator.log>
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
  mkdir -p .run-with-it/main .run-with-it/issues/<n>/workers .run-with-it/status .run-with-it/worktrees .run-with-it/locks

Resolve live status files before spawning:
  MAIN_LOG_FILE="${MAIN_LOG_FILE:-$(pwd -P)/.run-with-it/main/main.log}"
  RUN_WITH_IT_ISSUE_DIR="$(pwd -P)/.run-with-it/issues/<n>"
  SUB_COORD_LOG_FILE="$RUN_WITH_IT_ISSUE_DIR/sub-coordinator.log"
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
  "Log: .run-with-it/issues/<n>/sub-coordinator.log"
  "To watch live progress in a separate terminal:"
  "  tail -f .run-with-it/issues/<n>/sub-coordinator.log"

══ STEP D: SPAWN NEWLY QUEUED + ROLLING POOL MONITOR ════════════════════════════

Execution-mode requirement (critical):
  Run Step D as ONE long-lived shell session that performs both spawn and monitor.
  Do not split spawn and monitor into separate shell invocations. Do not write a
  bespoke rolling-pool script in the Main Orchestrator session. Use the shared
  platform pool runner; it maintains the pool and calls the platform dispatcher
  with `role=sub-coord` for each active issue.

  Required handoff before Step D:
  - Each ready issue in `main-state.json` must have `issue_registry[<n>].context_file`
    (or `sub_coord_context_file`) pointing at the context file assembled in Step C.

  Bash (macOS / Linux / Git Bash):

    nohup "$ASSET_ROOT/scripts/run-with-it-pool.sh" \
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

  PowerShell (Windows):

    $poolProcess = Start-Process -FilePath "powershell" -ArgumentList @(
      "-NoProfile", "-File", (Join-Path $ASSET_ROOT "run-with-it-pool.ps1"),
      "-AssetRoot", $ASSET_ROOT,
      "-StateFile", (Join-Path (Join-Path (Get-Location).Path ".run-with-it") "main-state.json"),
      "-ParallelJobs", $env:PARALLEL_JOBS,
      "-Agent", $env:SUB_COORD_AGENT,
      "-Model", $env:SUB_COORD_MODEL,
      "-StatusFile", $env:RUN_WITH_IT_STATUS_FILE,
      "-EventsLog", $env:RUN_WITH_IT_EVENTS_LOG,
      "-MainLog", $MAIN_LOG_FILE,
      "-PollSeconds", $env:STATUS_POLL_SECONDS,
      "-TimeoutSeconds", $env:SUB_COORD_TIMEOUT_SECONDS
    ) -PassThru

    $POOL_PID = $poolProcess.Id

  Persist `POOL_PID` in `main-state.json` for recovery visibility, then monitor that single process until
  it emits `STATUS|type=pool-empty`. Per-issue dispatcher PIDs are persisted by the platform pool runner.
  The pool runner must also perform each terminal per-issue GitHub update immediately after finalizing that issue's compact report: post the terminal comment populated from the report, close the issue when `outcome=completed`, leave `blocked` and `failed-review` issues open after commenting, and emit `STATUS|type=github-update|issue=<n>|outcome=<outcome>|action=<commented|skipped|failed>|closed=<true|false>`.

══ GOTO STEP A ═════════════════════════════════════════════════════════════════
```

## Platform Dispatchers — Shared Role Launcher

`scripts/run-with-it-dispatch.sh` and `powershell/run-with-it-dispatch.ps1` are the shared run-with-it orchestration primitives. The Main Orchestrator uses them with `role=sub-coord`; Sub-Coordinators use them with `role=complexity`, `impl`, `review`, or `modify`. They wrap `scripts/run-agent.sh` / `powershell/run-agent.ps1`, forward the role-specific `RUN_WITH_IT_*` environment, write dispatch status lines, capture stdout/stderr into the role log, monitor done/result artifacts through `scripts/worker-watch.sh` / `powershell/worker-watch.ps1`, and write a dispatcher-owned watchdog state file. Worker heartbeats are legacy advisory hints only; the state file is the source of truth for liveness and silent-worker detection.

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
  --state-file <file> \
  --repo-root <worktree-or-repo-path> \
  --status-file <file> \
  --events-log <file> \
  --quiet-seconds <seconds> \
  --stall-seconds <seconds>
```

PowerShell uses the same field names with PowerShell-style parameters, for example `-Role impl -Issue 123 -LogFile <file> -DoneFile <file> -ResultFile <file> -StateFile <file>`.

Use `--dry-run` / `-DryRun` to print the wrapped runner invocation, and `--validate-only` / `-ValidateOnly` to verify inputs and emit `STATUS|type=dispatch-ready` without spawning.

Worker watchdog files use the issue-scoped layout `cycle-<cycle>.state.json`. `state="quiet"` means the runner is alive but the captured role log has been silent beyond the quiet threshold. `state="stalled"` with `stall_reason="alive-but-silent"` means the worker is alive, incomplete, and silent beyond the stall threshold. Completion still requires the done sentinel and valid role-specific result artifacts.

## `scripts/run-agent.sh` — Full Syntax Reference

```
run-agent.sh --agent <agent> [--model <model>] --context-file <file> [--prompt-file <file>]
             [--permission-mode <mode>] [--extra-arg <arg>] [--unattended] [--dry-run]
run-agent.sh --list-agents [--detected-only]
run-agent.sh --list-models <agent>
```

| Flag | Env var equivalent | Required | Description |
|------|--------------------|----------|-------------|
| `--agent <agent>` | `AGENT` | Yes | Agent slug (e.g. `codex`, `github-copilot`, `claude`, `agy`) |
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
- Before deleting tracked files, worktrees, or any file outside `.run-with-it/`, print the planned cleanup targets and ask the user to confirm. After confirmation, delete generated files under `.run-with-it/issues/`, `.run-with-it/main/`, `.run-with-it/status/`, `.run-with-it/worktrees/`, and `.run-with-it/locks/`.
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

- Print the planned discard targets and ask the user to confirm before deleting. After confirmation, delete preserved generated files including `.run-with-it/main-state.json`, reports, logs, reviews, worktrees, locks, `technical_requirements.md`, `prd.md`, and `issues.md`.
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
      "issue_dir": ".run-with-it/issues/36",
      "report_file": ".run-with-it/issues/36/report.json",
      "merge_recovery_report_file": ".run-with-it/issues/36/merge-recovery-report.json",
      "log_file": ".run-with-it/issues/36/sub-coordinator.log",
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
- merge recovery queued: `STATUS|type=merge-recovery|issue=<n>|report_file=<path>|state=<started|completed|failed-merge|blocked>`
- memory refresh: `STATUS|type=memory-refresh|state_file=.run-with-it/main-state.json|tasks_loaded=<n>|completed=<n>|pending=<n>|failed=<n>`
- main loop: `STATUS|type=main-loop|iteration=<n>|pending=<count>|completed=<count>|failed=<count>`
- sub-coordinator spawn: `STATUS|type=sub-coord-spawn|issue=<n>|agent=<name>|model=<model>|report_file=<path>|log_file=<path>|pool_size=<n>|parallel_jobs=<PARALLEL_JOBS>`
- sub-coordinator pid-tracked: `STATUS|type=sub-coord-pid|issue=<n>|pid=<pid>|done_file=<path>|report_file=<path>`
- live agent start: `STATUS|type=agent-start|issue=<n>|role=<sub-coord|complexity|impl|review|modify>|agent=<name>|model=<model>`
- live agent complete: `STATUS|type=agent-complete|issue=<n>|role=<sub-coord|complexity|impl|review|modify>|agent=<name>|model=<model>|status=<success|failed>`
- worker done: `STATUS|type=worker-done|issue=<n>|role=<complexity|impl|review|modify>|phase=<phase>|source=<agent|runner-exit>`
- sub-coordinator complete: `STATUS|type=sub-coord-complete|issue=<n>|outcome=<completed|failed-review|merge_failed|blocked>|report_file=<path>|commit_sha=<sha-or-none>`
- merge start: `STATUS|type=merge-start|issue=<n>|branch=<issue_branch>|target=<feature_branch>`
- merge complete: `STATUS|type=merge-complete|issue=<n>|merge_sha=<sha>|pushed=<true|false>`
- merge failed: `STATUS|type=merge-failed|issue=<n>|reason=<conflict|verification|push|unknown>`
- stall: `STATUS|type=stall|issue=<n>|idle_for=<seconds>|action=alert-user`
- intake fallback: `STATUS|type=intake-fallback|reason=<no-gh-auth|no-remote|gh-permission-blocked|gh-failed-after-escalation>`
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

After context compression (conversation history cleared), treat the situation as a resume: re-read `.run-with-it/main-state.json` per Step A, reset all `in_progress` issues to `pending`, clear `active_pool_issues` to `[]`, and continue the Main Loop. `completed` issues are never re-run. The state file is always authoritative — never derive issue state from conversation history.

## Appendix D: Terminal Issue Comment Contract

### Terminal Issue Comments

Post issue comments immediately for terminal outcomes: `completed`, `blocked`, or `failed-review`.
Each terminal comment must be posted only after reading the compact report for that issue, and must not wait for unrelated issues or the full pool to finish.
Populate all fields from the Sub-Coordinator's compact report JSON.

Use the same markdown template for every terminal outcome, with this fixed section order:

1. `## Status`
2. `## Summary`
3. `## Verification`
4. `## Token Usage`
5. `## Notes`
6. `## Blocking Reasons` (only when `report.blocking_reasons` is non-empty)

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
