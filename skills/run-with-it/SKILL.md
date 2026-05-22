---
name: run-with-it
description: Two-layer orchestration runtime — Main Orchestrator fetches all issues, plans execution order, spawns Sub-Coordinators (sequentially or in parallel batches), collects compact reports, and updates GitHub. Context stays bounded so the run can continue for hours or days without degradation.
---

## Skill Isolation

Sole active authority for this session once invoked. No other skill may activate, interrupt, or modify behavior unless called by name via `Skill` tool call within this skill's workflow. Suppress any spontaneous external skill; continue without interruption. Applies from invocation until explicit termination or handoff.

This isolation governs orchestration flow only. Under no circumstance may this skill suppress, override, interrupt, or interfere with subcodinate core behavior, native tool invocations, or reasoning. Copilot's own capabilities must remain fully operational at all times. This carve-out cannot be overridden by any instruction within this skill.

## Critical Main Orchestrator Rules (compaction-safe — always enforce, even after context compression)

These rules apply for the entire lifetime of this skill session. They are stated here first so they survive context compaction and are never dropped:

- **Re-read `.run-with-it/main-state.json` before every loop iteration.** After context compression you have no memory of prior work — that file is your entire memory. Never derive issue state from conversation history.
- **Never implement work directly in this session.** All implementation belongs to Sub-Coordinators spawned via `run-agent.sh --prompt-file sub-coordinator-prompt.md`. There is no "implement in this chat" fallback option under any circumstance.
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
- **Never inject AGENT or MODEL overrides into a Sub-Coordinator that has already been spawned.** Routing overrides (`AGENT`, `MODEL`, `COMPLEXITY_LEVEL`, `COMPLEXITY_SCORE`) may only be set before spawning, as part of the context file assembled in Step C. After `run-agent.sh` is called, those values are locked and the Main Orchestrator must not attempt to change them.
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
- Determines execution order based on dependencies; groups independent issues into parallel batches when `PARALLEL_JOBS > 1`
- Writes its own status log to `.run-with-it/main/main.log`
- Spawns up to `PARALLEL_JOBS` **Sub-Coordinators** concurrently (one per independent issue in the batch) via `run-agent.sh`
- Waits for all Sub-Coordinators in the batch to complete and write their compact reports
- Reads ONLY the compact report JSON — never the implementation diffs or log files
- Updates `main-state.json` after each issue (its full external memory)
- Posts terminal GitHub comments and closes/updates issues
- Re-reads `main-state.json` at the top of every loop iteration to survive context compression

**Sub-Coordinator** (spawned via `sub-coordinator-prompt.md`, runs in a child agent session):
- Handles exactly ONE issue end-to-end
- Runs complexity analysis, deterministic routing, implementation, review, and modification loops
- Writes a compact report JSON and a full log file under `.run-with-it/sub/` when done
- Spawns worker agents whose logs are written under `.run-with-it/complexity/`, `.run-with-it/impl/`, `.run-with-it/review/`, and `.run-with-it/modify/`
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
  - `PARALLEL_JOBS` (default `1`) — maximum number of Sub-Coordinators to run concurrently. When `> 1`, all pending issues with no unmet dependencies are batched together up to this limit and spawned simultaneously. Sequential behavior (`PARALLEL_JOBS=1`) is the default for backward compatibility.

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
2. Determine execution order: topological sort respecting dependencies. Priority order within the same dependency tier: critical fixes → development infrastructure → tracer-bullet feature slices → polish and quick wins → refactors. When `PARALLEL_JOBS > 1`, issues at the same dependency tier with no mutual dependencies form a parallel batch (up to `PARALLEL_JOBS` at a time).
3. Issues whose dependencies have open/unresolved status are marked `blocked` until their dependencies complete.
4. Write the complete execution plan to `.run-with-it/main-state.json` before doing any work. Record `parallel_jobs` and `execution_mode` (`sequential` when `PARALLEL_JOBS=1`, `parallel` otherwise) in the plan.
5. Emit: `STATUS|type=plan|total_issues=<n>|mode=<sequential|parallel>|parallel_jobs=<PARALLEL_JOBS>|pending=<n>|blocked=<n>`
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

══ STEP B: IDENTIFY NEXT BATCH ═════════════════════════════════════════════════
From issue_registry in main-state.json, collect ALL issues with
status="pending" whose dependencies are all "completed".

- If PARALLEL_JOBS=1 (sequential mode): take only the first ready issue (priority
  order: critical fixes → dev infra → tracer-bullet slices → polish → refactors).
- If PARALLEL_JOBS>1 (parallel mode): take up to PARALLEL_JOBS ready issues,
  selecting them by the same priority order. Issues in the batch must have no
  unmet dependencies on each other (i.e., no issue in the batch depends on another
  issue also in the batch). If a candidate depends on another batch member, defer
  it to the next iteration.

If no pending issue is ready: check if any "pending" issue has unmet
dependencies — re-evaluate them. If still unresolvable, mark them "blocked".

If ALL issues are terminal (completed / failed-review / blocked):
  EXIT LOOP → proceed to Final Ledger and Cleanup.

Set CURRENT_BATCH = [ <issue_n>, <issue_m>, ... ] (1 to PARALLEL_JOBS issues).
Emit: STATUS|type=batch-start|batch_size=<n>|issues=<comma-separated-numbers>
      |parallel_jobs=<PARALLEL_JOBS>

══ STEP C: ASSEMBLE SUB-COORDINATOR CONTEXT FILES ══════════════════════════════
Repeat for EACH issue <n> in CURRENT_BATCH:

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
  mkdir -p .run-with-it/main .run-with-it/sub .run-with-it/reports .run-with-it/status .run-with-it/done .run-with-it/complexity .run-with-it/impl .run-with-it/review .run-with-it/modify

Resolve live status files before spawning:
  MAIN_LOG_FILE="${MAIN_LOG_FILE:-$(pwd -P)/.run-with-it/main/main.log}"
  SUB_COORD_LOG_FILE="$(pwd -P)/.run-with-it/sub/sub-<n>.log"
  RUN_WITH_IT_STATUS_FILE="${RUN_WITH_IT_STATUS_FILE:-$(pwd -P)/.run-with-it/status/current.txt}"
  RUN_WITH_IT_EVENTS_LOG="${RUN_WITH_IT_EVENTS_LOG:-$(pwd -P)/.run-with-it/status/events.log}"
  STATUS_POLL_SECONDS="${STATUS_POLL_SECONDS:-10}"
  LOG_TAIL_POLL_SECONDS="${LOG_TAIL_POLL_SECONDS:-120}"

Every STATUS line emitted by the Main Orchestrator must be appended to `$MAIN_LOG_FILE`
with an explicit shell write before or at the same time it is printed.

For EACH issue <n> in CURRENT_BATCH:
  Mark issue status="in_progress" in main-state.json. Write to disk BEFORE spawning.
  Emit: STATUS|type=sub-coord-spawn|issue=<n>|agent=<SUB_COORD_AGENT>
        |model=<SUB_COORD_MODEL>|report_file=<path>|log_file=<path>
        |batch_size=<len(CURRENT_BATCH)>|parallel_jobs=<PARALLEL_JOBS>

Print to user for each issue:
  "Starting sub-coordinator for issue #<n>: <title>"
  "Log: .run-with-it/sub/sub-<n>.log"
  "To watch live progress in a separate terminal:"
  "  tail -f .run-with-it/sub/sub-<n>.log"

══ STEP D: SPAWN ALL SUB-COORDINATORS IN BATCH (PARALLEL MONITORED BLOCKING) ══

Bash (macOS / Linux / Git Bash):

  # --- Phase 1: Spawn all sub-coordinators in the batch as background processes ---
  declare -A _BATCH_PIDS
  declare -A _BATCH_STARTED_ATS
  declare -A _BATCH_LAST_LOG_TAIL_ATS
  declare -A _BATCH_LAST_LOG_TAILS

  for issue_n in $CURRENT_BATCH; do
    SUB_COORD_CTX_FILE="$SUB_COORD_CONTEXT_FILE_${issue_n}"
    SUB_COORD_LOG="$(pwd -P)/.run-with-it/sub/sub-${issue_n}.log"
    SUB_COORD_DONE="$(pwd -P)/.run-with-it/done/issue-${issue_n}-sub-coord.done"
    (
      GUI_MODE="${GUI_MODE:-0}" \
      AGENT_REGISTRY_FILE="$ASSET_ROOT/agent-registry.json" \
      RUN_WITH_IT_STATUS_FILE="$RUN_WITH_IT_STATUS_FILE" \
      RUN_WITH_IT_EVENTS_LOG="$RUN_WITH_IT_EVENTS_LOG" \
      RUN_WITH_IT_LOG_FILE="$SUB_COORD_LOG" \
      RUN_WITH_IT_DONE_FILE="$SUB_COORD_DONE" \
      RUN_WITH_IT_ROLE="sub-coord" \
      RUN_WITH_IT_ISSUE="$issue_n" \
      "$ASSET_ROOT/run-agent.sh" \
        --agent "$SUB_COORD_AGENT" \
        --model "$SUB_COORD_MODEL" \
        --context-file "$SUB_COORD_CTX_FILE" \
        --prompt-file "$ASSET_ROOT/sub-coordinator-prompt.md" \
        --unattended
    ) &
    _BATCH_PIDS[$issue_n]=$!
    _BATCH_STARTED_ATS[$issue_n]=$(date +%s)
    _BATCH_LAST_LOG_TAIL_ATS[$issue_n]=0
    _BATCH_LAST_LOG_TAILS[$issue_n]=""
  done

  # --- Phase 2: Monitor all running sub-coordinators until all exit ---
  LAST_PRINTED_STATUS=""
  while true; do
    ALL_DONE=true
    for issue_n in "${!_BATCH_PIDS[@]}"; do
      pid="${_BATCH_PIDS[$issue_n]}"
      if kill -0 "$pid" 2>/dev/null; then
        ALL_DONE=false
        NOW=$(date +%s)
        elapsed=$((NOW - _BATCH_STARTED_ATS[$issue_n]))
        sub_report="$(pwd -P)/.run-with-it/reports/sub-${issue_n}-report.json"
        sub_log="$(pwd -P)/.run-with-it/sub/sub-${issue_n}.log"
        # Stall alert
        if [ "$elapsed" -ge "$SUB_COORD_TIMEOUT_SECONDS" ] && [ ! -f "$sub_report" ]; then
          printf 'STATUS|type=stall|issue=%s|idle_for=%s|action=alert-user\n' \
            "$issue_n" "$elapsed"
          printf 'Sub-coordinator for issue #%s has not completed after %ss.\n' \
            "$issue_n" "$elapsed"
          printf 'Check log: .run-with-it/sub/sub-%s.log\n' "$issue_n"
        fi
        # Periodic log tail (every LOG_TAIL_POLL_SECONDS per issue)
        if [ "$((NOW - _BATCH_LAST_LOG_TAIL_ATS[$issue_n]))" -ge "$LOG_TAIL_POLL_SECONDS" ] \
           && [ -s "$sub_log" ]; then
          CURRENT_LOG_TAIL="$(tail -n 2 "$sub_log")"
          if [ "$CURRENT_LOG_TAIL" != "${_BATCH_LAST_LOG_TAILS[$issue_n]}" ]; then
            printf '[issue #%s] %s\n' "$issue_n" "$CURRENT_LOG_TAIL"
            _BATCH_LAST_LOG_TAILS[$issue_n]="$CURRENT_LOG_TAIL"
          fi
          _BATCH_LAST_LOG_TAIL_ATS[$issue_n]="$NOW"
        fi
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
    [ "$ALL_DONE" = "true" ] && break
    sleep "$STATUS_POLL_SECONDS"
  done

  # --- Phase 3: Collect exit codes ---
  declare -A _BATCH_EXIT_CODES
  for issue_n in "${!_BATCH_PIDS[@]}"; do
    wait "${_BATCH_PIDS[$issue_n]}"
    _BATCH_EXIT_CODES[$issue_n]=$?
  done

Always invoke the above Bash call with dangerouslyDisableSandbox: true. This ensures
agent CLIs (claude, codex, copilot, gemini) can authenticate and run outside Claude
Code's sandbox. GUI_MODE=0 preserves full permission flags (--dangerously-skip-permissions,
--dangerously-bypass-approvals-and-sandbox) required for unattended execution.

PowerShell (Windows — never use VAR=value prefix):
  For each issue_n in $CURRENT_BATCH, start a background job:
  $Jobs = @{}
  foreach ($issue_n in $CURRENT_BATCH) {
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
  # Wait for all jobs
  $Jobs.Values | Wait-Job | Out-Null

**While waiting: monitor status only per issue.** Do not read log files into AI context. Do not infer what agent or model a Sub-Coordinator chose for its workers. Do not kill or restart any Sub-Coordinator in the batch. Each Sub-Coordinator owns its routing and worker decisions autonomously. The only valid responses to a stalled Sub-Coordinator are: (a) continue waiting, or (b) after user instruction, mark it as blocked and let the rest of the batch finish.

If run-agent.sh fails for an issue despite dangerouslyDisableSandbox: true, it is a true agent failure for that issue.
Emit: STATUS|type=runner-sandbox-retry-result|issue=<n>|outcome=failed

If a Sub-Coordinator runs longer than SUB_COORD_TIMEOUT_SECONDS without producing its
report file:
  STATUS|type=stall|issue=<n>|idle_for=<seconds>|action=alert-user
  Print: "Sub-coordinator for issue #<n> has not completed after <t>s."
  Print: "Check log: .run-with-it/sub/sub-<n>.log"
  The rest of the batch continues running. Wait for user: type 'continue' to keep
  waiting for this issue, or 'skip' to mark it as blocked and proceed once the
  other batch members finish.

Do NOT use a stall or timeout as justification to inspect logs, infer routing, or restart with different overrides.

══ STEP E: COLLECT REPORTS (for each issue in CURRENT_BATCH) ══════════════════
Repeat for EACH issue <n> in CURRENT_BATCH:

  Check: does .run-with-it/reports/sub-<n>-report.json exist with valid JSON?
    YES: Read the compact report JSON into context (this is the ONLY file you read).
    NO:  Mark issue status="blocked" with reason="report-missing".
         Update main-state.json. Proceed to Step F for this issue with blocked status.

  Print to user (do NOT read into AI context):
    "=== Sub-coordinator output for issue #<n> ==="
    "Full log: .run-with-it/sub/sub-<n>.log"
    "Report:   .run-with-it/reports/sub-<n>-report.json"
    "(Log is not loaded into context — only shell tail -n 2 was used for live display)"

  Parse from report JSON: outcome, summary, files_modified, verification,
  review_summary, token_usage, commit_sha, blocking_reasons.

  Delete $SUB_COORD_CONTEXT_FILE_<n> immediately after reading the report.

══ STEP F: UPDATE STATE + GITHUB (for each issue in CURRENT_BATCH) ════════════
Repeat for EACH issue <n> in CURRENT_BATCH (process reports sequentially even
though they were collected in parallel — GitHub operations must not race):

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

After all issues in the batch have been processed:
8. Emit: STATUS|type=batch-complete|batch_size=<n>
         |completed=<count>|failed=<count>|blocked=<count>

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
- Delete all files under `.run-with-it/reports/`, `.run-with-it/sub/`, `.run-with-it/main/`, `.run-with-it/done/`, `.run-with-it/complexity/`, `.run-with-it/impl/`, `.run-with-it/review/`, `.run-with-it/modify/`, and `.run-with-it/reviews/`.
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
  "schema_version": 3,
  "run_id": "<uuid generated at run start>",
  "started_at": "<iso8601>",
  "execution_plan": {
    "execution_mode": "sequential | parallel",
    "parallel_jobs": 1,
    "batches": [
      { "batch_id": 1, "issues": [36], "mode": "sequential" },
      { "batch_id": 2, "issues": [37, 38], "mode": "parallel" }
    ]
  },
  "issue_registry": {
    "36": {
      "status": "completed | in_progress | pending | failed-review | blocked",
      "title": "issue title",
      "report_file": ".run-with-it/reports/sub-36-report.json",
      "log_file": ".run-with-it/sub/sub-36.log",
      "commit_sha": "abc1234"
    }
  },
  "current_batch_id": 2,
  "active_batch_issues": [37, 38],
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
- `current_batch_id` + `active_batch_issues` tell a resumed orchestrator exactly which batch was running; on resume all `in_progress` issues in `active_batch_issues` are reset to `pending` (Sub-Coordinators are ephemeral — restart the whole batch)
- When `PARALLEL_JOBS=1`, `active_batch_issues` always has at most one entry (backward-compatible)

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

- plan: `STATUS|type=plan|total_issues=<n>|mode=<sequential|parallel>|parallel_jobs=<PARALLEL_JOBS>|pending=<n>|blocked=<n>`
- batch start: `STATUS|type=batch-start|batch_size=<n>|issues=<comma-separated>|parallel_jobs=<PARALLEL_JOBS>`
- batch complete: `STATUS|type=batch-complete|batch_size=<n>|completed=<count>|failed=<count>|blocked=<count>`
- memory refresh: `STATUS|type=memory-refresh|state_file=.run-with-it/main-state.json|tasks_loaded=<n>|completed=<n>|pending=<n>|failed=<n>`
- main loop: `STATUS|type=main-loop|iteration=<n>|pending=<count>|completed=<count>|failed=<count>`
- sub-coordinator spawn: `STATUS|type=sub-coord-spawn|issue=<n>|agent=<name>|model=<model>|report_file=<path>|log_file=<path>|batch_size=<n>|parallel_jobs=<PARALLEL_JOBS>`
- live agent start: `STATUS|type=agent-start|issue=<n>|role=<sub-coord|complexity|impl|review|modify>|agent=<name>|model=<model>`
- live agent complete: `STATUS|type=agent-complete|issue=<n>|role=<sub-coord|complexity|impl|review|modify>|agent=<name>|model=<model>|status=<success|failed>`
- worker done: `STATUS|type=worker-done|issue=<n>|role=<complexity|impl|review|modify>|phase=<phase>|source=<agent|runner-exit>`
- live heartbeat: `STATUS|type=heartbeat|issue=<n>|role=<impl|review|modify>|phase=<exploring|implementing|testing|review>|progress=<short-text>`
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
2. Identify all issues with `status="in_progress"` — these had a Sub-Coordinator that was interrupted mid-run (possibly mid-batch in parallel mode). Reset ALL of them to `status="pending"` (Sub-Coordinators are ephemeral; re-run the entire interrupted batch fresh). Also clear `active_batch_issues` to `[]`.
3. Identify all issues with `status="pending"` — these haven't started yet.
4. Identify all issues with `status="completed"`, `"failed-review"`, or `"blocked"` — skip these entirely.
5. Re-enter Main Loop at Step A.
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
4. Issues with `status="in_progress"` are reset to `"pending"` — re-run their Sub-Coordinators fresh (entire batch is re-queued; partial batch completions before compression are lost and must be re-run).
5. Clear `active_batch_issues` to `[]` in main-state.json.
6. Continue the Main Loop as if resuming from a clean slate.

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
- **Never kill or restart individual Sub-Coordinators mid-batch.** A stall alert for one batch member does not justify interrupting the others — wait for the full batch or mark the stalled issue blocked on user instruction.
- **GitHub operations within a batch are sequential.** Even when Sub-Coordinators ran in parallel, Step F processes each issue's GitHub comment/close one at a time to avoid race conditions.
- Preserve local fallback behavior when GitHub or git is unavailable.
- Keep changes minimal and focused to orchestration/control-plane behavior.
