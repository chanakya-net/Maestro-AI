---
name: run-with-it
description: Two-layer orchestration runtime — Main Orchestrator fetches all issues, plans execution order, maintains a rolling pool of Sub-Coordinators (up to PARALLEL_JOBS concurrently), fills freed slots immediately on completion, and updates GitHub. Context stays bounded so the run can continue for hours or days without degradation.
---

<!-- NATIVE_AUTHORITY_START: keep this compaction-safe authority block synchronized in all four coordinator documents. -->
- **Authority after compaction:** re-read maintained rules, main/issue state, exact worker identities and role artifacts from disk; never reconstruct a paid attempt from memory or logs.
- **Independent selectors:** `SUB_COORD_AGENT` / `SUB_COORD_MODEL` select only the Sub-Coordinator. `FORCED_AGENT` / `FORCED_MODEL` select routed workers only when explicitly requested; ambient aliases never cross that boundary.
- **Verification and artifacts:** consume the validated outcome bound to the exact attempt, never raw process success. Managed DONE is dispatcher-owned after shutdown and report validation. Success requires role-valid artifacts and passing verification; failed/blocked/interrupted attempts publish failure even without a commit via the shared `artifact-protocol.md`. Negative coordinator issue outcomes are valid handoffs and never satisfy dependencies.
- **Check-in ownership:** implementation/modification workers commit only in their assigned issue worktree; Sub-Coordinators coordinate and own normal merge attempts; Main owns intake/state/GitHub; merge recovery owns conflict resolution.
- **Availability and review:** keep cumulative unavailable-route exclusions and separate infrastructure/capability budgets. Keep the sticky reviewer across a valid cycle; artifact retries never erase valid feedback or spend product-review verdict cycles.
- **Identity and recovery:** a live or unknown owned attempt is never relaunched. Persist in-flight identities before compaction handoff; stop pool admission and verify every owned tree is dead before destructive cleanup.
<!-- NATIVE_AUTHORITY_END -->

## Skill Isolation

Sole active authority once invoked — no other skill may activate unless called by name via `Skill` tool call; suppress spontaneous external skills until explicit termination or handoff. This isolation governs orchestration flow only; subordinate core behavior, native tool use, and reasoning remain fully operational and cannot be overridden by this skill.

<!-- SYNC: this section is intentionally duplicated in assets/main-orchestrator-rules.md; the repository copy is authoritative over any installed mirror. Edit both twins in the same commit — tests/contracts/test_instructions.py asserts key tokens match. -->
## Critical Main Orchestrator Rules (compaction-safe — always enforce, even after context compression)

These rules apply for the entire lifetime of this skill session. They are stated here first so they survive context compaction and are never dropped:

- **Re-read `.run-with-it/main-state.json` before every loop iteration.** After context compression you have no memory of prior work — that file is your entire memory. Never derive issue state from conversation history.
- **Never implement work directly in this session.** All implementation belongs to Sub-Coordinators spawned via the native dispatcher (`ai-skills worker dispatch`) with `role=sub-coord`, which wraps `ai-skills agent run` with `sub-coordinator-prompt.md`. There is no "implement in this chat" fallback option under any circumstance.
- **Never run tests, build commands, or compile the project** in this session. Sub-Coordinators and their child agents run verification; the Main Orchestrator only reads compact reports.
- **Never pause after planning to ask the user how to proceed.** Enter the Main Loop immediately after the execution plan is written.
- **Never present execution option menus** (Option A / B / C style choices).
- **Always pull issue data from GitHub** (`gh`) when a remote exists. Only fall back to local files if `gh` is unavailable, authentication fails, an approved permission-escalation attempt fails, or no GitHub remote exists.
- **Never delete user-modified files** during cleanup. Check `git status --short` before removing any workspace artifact.
- **Never load full sub-coordinator log files into context.** Sub-Coordinator logs live under `.run-with-it/issues/<n>/sub-coordinator.log`. Do not tail raw logs into AI context; only read the compact report JSON from `.run-with-it/issues/<n>/report.json`.
- **Never load live status logs into context.** Live progress is written to `.run-with-it/status/current.txt` and `.run-with-it/status/events.log`; the native event watcher prints new status events to the terminal, but the Main Orchestrator must not read those files into AI memory.
- **Per-issue stage board.** The pool runner emits a compact `STATUS|type=run-board|board=...` line whenever the run's stages change (e.g. `#618 merge-recovery(cyc2) | #631 impl(cyc1) | #633 blocked:631 | #627 done`) for a "current stage, not detail" view. Print it on demand any time with `ai-skills state status-board --state-file .run-with-it/main-state.json` (read-only; add `--oneline` for the single-line form).
- **Pool liveness heartbeat.** The pool runner emits `STATUS|type=pool-heartbeat|pool_pid=<pid>|active=<n>|parallel_jobs=<n>|total=..|completed=..|in_progress=..|pending=..|blocked=..|waiting_context=..` every `POOL_HEARTBEAT_SECONDS` (default 60). A heartbeat in the watch output means the pool is alive even when nothing else changed; relay its counts to the user as the periodic progress update.
- **Assemble context files for ALL pending issues up front — dependents included.** The pool runner can only dispatch issues whose context files already exist on disk; an issue without a context file is invisible to slot filling (full rationale in Step B). If a `STATUS|type=pool-waiting-context` line ever appears, assemble the missing contexts immediately (Step C) while the pool keeps running.
- **Stay attached until every issue is terminal.** The Main Orchestrator session must keep running the watch loop, and after each watch window print a one-line user-facing progress update from the newest `run-board` / `pool-heartbeat` lines (e.g. `Pool alive — 2 running, 3 pending, 4 completed, 1 blocked`). Never end the turn, go silent, or declare the run finished while any issue is still `pending`, `in_progress`, or `merge_recovery`. `pool-empty` with pending issues remaining means GOTO Step A, not done.
- **GitHub operations (close, comment, e.g., gh issue close) are the Main Orchestrator control plane's sole responsibility.** Sub-Coordinators never touch GitHub. The pool runner performs the per-issue terminal comment/close immediately after reading a terminal compact report.
- **Never inspect, infer, or act on a Sub-Coordinator's internal routing decisions.** Once a Sub-Coordinator is spawned, the agent and model it selects for its child workers are entirely its own responsibility — the Main Orchestrator has no visibility into, and no authority over, those internal choices. Do not read log files to determine which worker agent or model is running.
- **Never kill, cancel, or restart a Sub-Coordinator mid-run.** If a Sub-Coordinator appears to be using a different agent or model than expected, that is correct behavior — it is applying its own complexity-based routing. Do not intervene. The only valid responses to a running Sub-Coordinator are: (a) wait for it to complete and write its compact report, or (b) alert the user after `SUB_COORD_TIMEOUT_SECONDS` and wait for a 'continue' or 'skip' instruction. **Sole exception:** a user-confirmed `discard`, which terminates the entire run — supervisor, dispatchers, and runners — through the native stop operation (`ai-skills run stop`) per the Cleanup Discard flow. Never hand-roll kills even then.
- **Never inject worker-routing overrides into a Sub-Coordinator that has already been spawned.** Canonical worker overrides (`FORCED_AGENT`, `FORCED_MODEL`, `COMPLEXITY_LEVEL`, `COMPLEXITY_SCORE`) may only be set before spawning, as part of the context file assembled in Step C. After the native dispatcher calls `ai-skills agent run`, those values are locked and the Main Orchestrator must not attempt to change them.
- **Run the native pool runner (`ai-skills run pool`) as the single rolling-pool supervisor.** The pool runner spawns Sub-Coordinator dispatch processes, captures each dispatcher PID, and persists `issue`, `pid`, `started_at`, `context_file`, `log_file`, `done_file`, and `report_file` before monitoring.
- **Use the native worker snapshot (`ai-skills worker inspect`) inside the dispatcher for Sub-Coordinator liveness checks during pool monitoring.** Pass the durable dispatcher `--state-file`; the inspector reconstructs its recorded configuration and validates process identity. Treat PID liveness as diagnostic only. Completion requires a known dead owned tree, its durable exit status and validated compact report artifacts; the done sentinel is advisory.
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
- Creates one shared run feature branch (`Maestro/<funny-action-animal>`) from the original base branch, pushes it when a GitHub remote exists, and uses it as the final PR head branch
- Determines execution order with a dependency graph and topological sort based primarily on each issue's `## Blocked by` section; cycles or unresolved external blockers are marked blocked before execution
- Maintains a rolling pool of up to `PARALLEL_JOBS` active **Sub-Coordinators** via the native dispatcher — freed slots fill immediately when any job completes rather than waiting for whole batches
- As each Sub-Coordinator completes, reads its compact report, immediately posts the terminal GitHub comment and closes/updates that issue when it has a terminal outcome, then spawns the next ready issue into the freed slot
- Writes its own status log to `.run-with-it/main/main.log`
- Reads ONLY the compact report JSON — never the implementation diffs or log files
- Updates `main-state.json` after each issue (its full external memory)
- Posts terminal GitHub comments and closes/updates issues immediately per issue, not only after the full pool finishes
- Spawns a Merge Recovery Coordinator when a Sub-Coordinator reports `merge_failed`; Main Orchestrator never merges issue branches itself
- Creates one final PR from the shared run feature branch after all issues are terminal, using `ai-skills report` to render the body from `.run-with-it/main-state.json`
- Re-reads `main-state.json` at the top of every loop iteration to survive context compression

**Sub-Coordinator** (spawned via `sub-coordinator-prompt.md`, runs in a child agent session):
- Handles exactly ONE issue end-to-end
- Creates an issue branch and issue worktree from the shared run feature branch
- Runs complexity analysis, deterministic routing, gated read-only planning, implementation, review, and modification loops
- Runs child workers with `REPO_ROOT` pointing at the issue worktree while keeping logs/reports under the root `.run-with-it/`
- Spawns an Artifact Recovery Worker when implementation/modification artifact retries are exhausted so dirty work can be inspected, verified, committed, or requeued before any terminal blocked report
- Attempts the normal merge back into the shared feature branch under `.run-with-it/locks/merge.lock`
- Writes a compact report JSON and full log file under `.run-with-it/issues/<n>/` when done
- Spawns worker agents whose logs/results/done sentinels are written under `.run-with-it/issues/<n>/workers/<role>/`
- Never touches GitHub; never updates `main-state.json`

**Plan Worker** (spawned via `plan-prompt.md`, gated, runs after complexity and before implementation):
- Reads the issue worktree **read-only** with a strong model and writes a concrete approach plan to `.run-with-it/issues/<n>/plan.md` plus a machine-readable `plan.json` under `workers/plan/`
- Never edits or commits — it runs before the baseline SHA so it cannot corrupt the implementer's diff
- Re-scores complexity from the real code; the Sub-Coordinator prefers that grounded band over the blind score when routing implementation and modification (the hybrid refinement)
- Gated by `RUN_WITH_IT_PLAN_MIN_COMPLEXITY` (default `medium-hard`) and toggled by `RUN_WITH_IT_PLAN_ENABLED` (default `1`); trivial issues skip planning and route weak regardless
- The implementer, reviewer, and modifier all consume `plan.md` via `RUN_WITH_IT_PLAN_FILE`

**Artifact Recovery Worker** (spawned via `artifact-recovery-prompt.md`, runs only after exhausted impl/modify artifact failures):
- Inspects the issue worktree, including dirty uncommitted work and preserved recovery patches
- Runs verification and commits salvaged work on the issue branch when the work is complete
- Writes the missing `impl` or `modify` result artifact only with concrete commit and verification evidence
- Returns a structured `synthesized-result`, `requeue`, or `blocked` decision to the Sub-Coordinator

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

## Native runtime

Use the installed `ai-skills` command on macOS, Linux, and native Windows. Python owns process sessions/Windows Jobs, argv, files, JSON, locks and timeouts. Do not recreate platform-specific launch or polling algorithms. `ai-skills doctor --json` checks the selected interpreter and maintained resources. The run's immutable engine selection and inherited `AI_SKILLS_RUN_ROOT` remain authoritative after updates; never replace them with the issue worktree path.

## Inputs

Provide a task summary before execution. All other inputs are optional overrides.

| Variable | Default | Description |
|----------|---------|-------------|
| `ASSETS_DEST` | — | Installation root containing the active runtime manifest; resources come from the selected package |
| `AGENT_REGISTRY_FILE` | — | Registry file override |
| `ISSUE_LABEL` | `ready-for-agent` | Label filter for issue intake |
| `ISSUE_LIMIT` | `1000` | Max issues to fetch (fetches all by default) |
| `ISSUE_STATE` | `open` | Issue state filter |
| `COMMITS_LIMIT` | `5` | Recent commits included in Sub-Coordinator context |
| `MAX_ITERATIONS` | `20` | Deprecated / no effect — the review/modify loop cap is hardcoded to 8 cycles in `sub-coordinator-prompt.md` (Appendix B); still forwarded in context files for backward compatibility but not consulted |
| `RUN_WITH_IT_PLAN_ENABLED` | `1` | Master switch for the pre-implementation plan phase; `0` disables it (every issue skips planning) |
| `RUN_WITH_IT_PLAN_MIN_COMPLEXITY` | `medium-hard` | Minimum blind complexity band that triggers a plan; below it the phase is skipped (trivial issues route weak regardless) |
| `SUB_COORD_AGENT` | `codex` | Agent slug for every Sub-Coordinator |
| `SUB_COORD_MODEL` | `gpt-6.1-sol` | Model for every Sub-Coordinator (Sub-Coordinators route their own children independently) |
| `SUB_COORD_TIMEOUT_SECONDS` | `3600` | Seconds before stall alert for a non-completing Sub-Coordinator |
| `STATUS_POLL_SECONDS` | `10` | Native polling cadence for status line output |
| `POOL_WATCH_SECONDS` | `240` | Watch-window length for each bounded `ai-skills run watch` call in Step D |
| `LOG_TAIL_POLL_SECONDS` | `120` | Native polling cadence for sub-coordinator log tail |
| `RUN_WITH_IT_STATUS_FILE` | `.run-with-it/status/current.txt` | Single-line status bus (overwritten each update) |
| `RUN_WITH_IT_EVENTS_LOG` | `.run-with-it/status/events.log` | Append-only event log — terminal inspection only; never load into AI context |
| `RUN_WITH_IT_ISSUE_DIR` | `.run-with-it/issues/<n>` | Issue-scoped artifact folder created by the Sub-Coordinator/pool |
| `RUN_WITH_IT_LOG_FILE` | role-specific | Sub-Coordinators: `.run-with-it/issues/<n>/sub-coordinator.log`; workers: `.run-with-it/issues/<n>/workers/<role>/cycle-<cycle>.log` |
| `RUN_WITH_IT_DONE_FILE` | role-specific | Workers: `.run-with-it/issues/<n>/workers/<role>/cycle-<cycle>.done` |
| `RUN_WITH_IT_RESULT_FILE` | role-specific | Workers: `.run-with-it/issues/<n>/workers/<role>/cycle-<cycle>-result.json` |
| `RUN_WITH_IT_STATE_FILE` | role-specific | Workers: `.run-with-it/issues/<n>/workers/<role>/cycle-<cycle>.state.json`; dispatcher-maintained watchdog state |
| `FORCED_AGENT` | — | Canonical explicit child-worker agent override passed through to Sub-Coordinators |
| `FORCED_MODEL` | — | Canonical explicit child-worker model override passed through to Sub-Coordinators |
| `AGENT` | — | Deprecated top-level alias; only an explicitly user-supplied value is normalized to `FORCED_AGENT`; ambient `AGENT` runner telemetry is ignored |
| `MODEL` | — | Deprecated top-level alias; only an explicitly user-supplied value is normalized to `FORCED_MODEL`; ambient `MODEL` runner telemetry is ignored |
| `COMPLEXITY_LEVEL` | — | Routing override passed through to Sub-Coordinators |
| `COMPLEXITY_SCORE` | — | Routing override passed through to Sub-Coordinators |
| `AGENT_ALLOWLIST` | — | Comma-separated; passed through to Sub-Coordinators |
| `AGENT_DENYLIST` | — | Comma-separated; passed through to Sub-Coordinators |
| `MAX_AGENT_FALLBACKS` | `2` | Max agent fallback attempts; passed through |
| `DELEGATED_REVIEW` | `true` | Enable Sub-Coordinator delegated review; passed through |
| `MAX_AGENT_DEPTH` | `1` | Always injected; prevents Sub-Coordinator children from spawning sub-agents |
| `PARALLEL_JOBS` | `4` | Rolling pool size. Freed slots fill immediately. Set to `1` for sequential. |
| `POOL_HEARTBEAT_SECONDS` | `60` | Cadence of the pool runner's `STATUS|type=pool-heartbeat` liveness line (per-status counts + active pool size) |
| `MAX_SPAWN_BOOTSTRAP_ATTEMPTS` | `3` | Consecutive failed spawn attempts before the pool runner finalizes an issue as terminal instead of retrying |
| `RUN_WITH_IT_WORKER_STALE_SECONDS` | `600` | A worker state file untouched this long is treated as an orphaned snapshot, not a running worker. A live dispatcher rewrites it every poll (~20s), so silence this long means the dispatcher died. Floored at 60. |
| `MAX_WORKER_WAIT_SECONDS` | `3600` | Ceiling on waiting for one in-flight worker after its Sub-Coordinator exits, even while the worker still looks alive. `0` disables the ceiling — the staleness bound is then the only backstop. |
| `MAX_SUB_COORD_COMPACTION_HANDOFFS` | `6` | Context-budget handoffs allowed per issue. Budgeted apart from `MAX_SUB_COORD_RECOVERY_ATTEMPTS` because a compaction stop is a contracted handoff, not a failure. |
| `WAIT_STATUS_INTERVAL_SECONDS` | `300` | Minimum gap between repeats of an unchanged `sub-coord-recovery-wait` line. Changes in worker/role/reason always emit. |

## Automatic Worker Routing Policy

The selected runtime's `agent-registry.json` is the single source of model
membership, usage targets, and effort. Do not copy a model matrix into the
orchestration instructions or select models from historical examples.

Use `ai-skills.registry.load_registry(path)` to expand version 2 model entries,
CLI definitions, and defaults into the router's policy. For each effective
non-complexity band, the expanded
`model_routing.non_complexity_band_policy` supplies the eligible models and
provider additions. `model_routing.usage_distribution` supplies the global and
role/band usage targets. `model_routing.effort_by_model_and_band` supplies effort
overrides, falling back to each model's `reasoning_effort`.

Complexity scoring keeps its separate weight/band policy. Review applies its
one-band increase and planning its two-band increase before selecting a model.
Explicit `FORCED_MODEL` values bypass automatic band membership but still pass
compatibility and availability checks. Preserve the entire decision returned by
`ai-skills route`; pass its agent, model, and effort to the worker unchanged.

The generic route effort becomes Codex `model_reasoning_effort` or Claude Code
`--effort`; Agy receives no effort flag.

## Runtime and maintained rules

Run `ai-skills doctor --json` and `ai-skills agent list --detected-only`. Resolve maintained prompt/rule Markdown and `agent-registry.json` from the selected installed package (or its authoritative source checkout during development). `ASSETS_DEST` selects the installation manifest root, not a flat prompt directory. Use `AGENT_REGISTRY_FILE` for an explicit registry override. Executables and resources come from the same pinned package. Use the active manifest's `resources` path as `ASSET_ROOT`; for a source checkout use its `assets/` directory. For an existing run use the pinned package's resources, even if the active manifest changed. The Python API `ai_skills.resources.resource_path(name)` resolves an individual maintained file from that interpreter. No OS-dependent helper inventory is needed.

Use file tools to copy maintained `main-orchestrator-rules.md` and `coordinator-rules.md` into `.run-with-it/` for compaction-safe re-reads. The repository copy remains authoritative over an installed mirror. Verify GitHub authentication, issue intake label, run branch/base, coordinator availability, and supplied context/artifact paths before admission. For a new authorized project initialize Git with `git init`; do not require Git merely to locate maintained resources. Never launch paid work in preflight.

## Initial Batch Issue Fetch

If issue data is missing in context, fetch only open issues with the configured intake label (`ready-for-agent` by default) at startup.

Use `ISSUE_LIMIT` (default `1000`) as the `--limit` argument — this fetches all matching issues by default. Do not cap the result unless the user explicitly sets `ISSUE_LIMIT` to a lower value.

```text
gh issue list --label ready-for-agent --state open --limit 1000 --json number,title,body,labels,url,comments
```

Adjust only trusted user intake parameters and save the returned issue batch with file tools. Parse executable intake issues and their `## Blocked by` dependency sections; incidental parent/PRD links never become blockers.

Fallback policy:

- Primary: GitHub issues via `gh`. **Always use GitHub when the repo has a GitHub remote. Never silently fall back to a local file when GitHub may be reachable.**
- If `gh` fails because the current tool is sandboxed (permission error, named-pipe, socket), use that tool's explicit approved permission-escalation flow when available before considering fallback. If escalation is unavailable or denied, emit `STATUS|type=intake-fallback|reason=gh-permission-blocked` and use local fallback only when allowed below.
- Fallback: local `issues.md` (`LOCAL_ISSUES_FILE` override supported) — **only** when `gh` is unavailable, authentication fails, an approved permission-escalation attempt fails, or no GitHub remote exists. Emit `STATUS|type=intake-fallback|reason=<no-gh-auth|no-remote|gh-permission-blocked|gh-failed-after-escalation>` before using local file.
- If git metadata is unavailable, continue with empty commit context.

Before fetching work begins, create the shared run feature branch:

1. Capture original base branch and SHA.
2. Generate a human-readable branch name as `Maestro/<funny-action-animal>` instead of a UUID branch.
   - Use a lowercase, hyphenated slug with exactly two words after the prefix: `<action-or-trait>-<animal>`.
   - Prefer funny but work-safe names such as `cunning-fox`, `unfaithful-lion`, `scheming-otter`, `dramatic-llama`, `sneaky-raven`, `tapdancing-badger`, `plotting-penguin`, or `chaotic-hamster`.
   - Do not use raw UUIDs in the branch name.
   - If the generated branch already exists locally or on the remote, generate a different slug; only append a short numeric suffix when several reasonable retries collide.
3. Create `Maestro/<funny-action-animal>` from that base.
4. Push the branch when a GitHub remote exists.
5. Record `run_branch.base_branch`, `run_branch.base_sha`, `run_branch.feature_branch`, `run_branch.feature_branch_start_sha`, `run_branch.remote`, and `run_branch.pushed`.

After fetching all issues:

1. Filter the fetched issue set before planning: every executable issue must have the configured intake label (`ready-for-agent` by default). Do not add unlabelled issues, PRD/parent issues, `needs-triage` issues, or issues discovered only through cross-references to `main-state.json`.
2. Build a dependency graph only from each executable issue's `## Blocked by` section. Normalize `#123`, full GitHub issue URLs, and plain issue numbers. Treat `None - can start immediately` as no dependencies.
3. Treat PRD/parent references as context, not dependencies. Ignore issue references from `## Parent`, titles such as `PRD: ...`, labels such as `needs-triage`, and incidental issue links elsewhere in the body when computing `deps`.
4. A dependency is actionable only if it points to another fetched executable issue in the same intake set. If `## Blocked by` names a PRD/parent issue or an issue outside the intake set, ignore it and record the ignored reference in `dependency_proof` as non-blocking context rather than marking the issue blocked.
5. Detect cycles and unresolved dependencies among executable issues only; mark affected issues `blocked` with `dependency_proof` and `blocking_reasons`.
6. Determine execution order: topological sort respecting dependencies. Priority order within the same dependency tier: critical fixes → development infrastructure → tracer-bullet feature slices → polish and quick wins → refactors. When `PARALLEL_JOBS > 1`, issues fill a rolling pool (up to `PARALLEL_JOBS` active at a time) — freed slots are filled immediately rather than waiting for a full batch to complete.
7. Issues whose executable dependencies have open/unresolved status, `merge_recovery`, `failed-merge`, or `blocked` are not ready until the dependency becomes `completed`. The pool runner dispatches merge recovery for `merge_recovery` issues before dependents become ready.
8. Write the complete execution plan to `.run-with-it/main-state.json` before doing any work. Record `parallel_jobs`, `execution_mode` (`sequential` when `PARALLEL_JOBS=1`, `rolling-pool` otherwise), `topo_order`, `dependency_tiers`, and each issue's `dependency_proof`, `parallel_safe`, and normalized `ownership_scope`. Derive the concurrency metadata like this:
   - `ownership_scope`: the list of top-level directories (or deeper paths when the issue is precise) the issue's body, title, and acceptance criteria name. Use plain repo-relative directory paths without glob characters; a glob-bearing scope is compared by its literal directory prefix only, and absolute paths, drive/UNC paths, and `..`-escaping paths are rejected as malformed (the issue then runs exclusively).
   - `parallel_safe`: `true` unless the issue is a repo-wide refactor, migration, formatting sweep, or otherwise touches files that cannot be attributed to a bounded scope — then set `parallel_safe: false` to force exclusive execution.
   - `concurrency_policy`: newly written plans MUST set `execution_plan.concurrency_policy: "strict"` and derive the metadata above for every issue. Under `strict`, an issue with missing concurrency metadata runs exclusively — worktrees isolate filesystem conflicts only, not semantic conflicts, migrations, generated files, or shared external resources.
   - Legacy states without `concurrency_policy` run `permissive`: missing metadata admits in parallel, relying on worktree isolation plus merge recovery. This fail-open behavior exists only for backward compatibility with states written before the flag; do not write new plans without the flag.
   - Explicit `parallel_safe: false`, root/malformed metadata, or a proven `ownership_scope` overlap always defers an issue regardless of policy; the pool runner reports deferrals as `STATUS|type=pool-admission-deferred|count=<n>|deferrals=<issue:reason,...>`.
9. Emit: `STATUS|type=plan|total_issues=<n>|mode=<sequential|rolling-pool>|parallel_jobs=<PARALLEL_JOBS>|pending=<n>|blocked=<n>`
10. Emit: `STATUS|type=memory-refresh|state_file=.run-with-it/main-state.json|tasks_loaded=<n>|completed=0|pending=<n>`

## Main Orchestrator Loop

After writing the execution plan, enter this loop without an execution menu:

1. Re-read `.run-with-it/main-orchestrator-rules.md` and `main-state.json` at the top of every iteration and after compaction. Disk state is authoritative.
2. Assemble context files for **every pending executable issue**, including dependencies that are not ready yet; save each path in `issue_registry`. This lets the rolling pool fill newly free slots without waiting for another coordinator batch. Preserve dependency proofs, ownership scopes and strict concurrency policy. Sub-Coordinators consume this supplied issue body and never fetch GitHub issues.
3. Include absolute issue/report/log/worktree paths, original base and shared branch, bounded recovery settings, `MAX_AGENT_DEPTH=1`, independent `SUB_COORD_AGENT`/`SUB_COORD_MODEL`, and only explicitly requested worker `FORCED_AGENT`/`FORCED_MODEL` overrides. Ambient legacy aliases are not overrides.
4. Write `pool.json` below, validate it, then launch the native detached pool once. The pool owns admission, leases, worker adoption, immediate refill, dependency readiness, terminal report finalization, merge recovery and sequential GitHub updates. Do not launch issues yourself alongside it.
5. Repeatedly call the bounded watch command. After each call report a concise user-facing stage update from the latest board/heartbeat. `running` means watch again; `pool-dead` (exit 3) means re-enter the pool with the same config so it adopts existing attempts; `pool-empty` means re-read main state before deciding whether the run is finished. Missing contexts must be supplied while the pool remains active.
6. Stay attached until all issues are terminal. `merge_recovery` is nonterminal; only completed dependencies unblock dependents. Explain terminal root blockers and their downstream unreachable issues. Never bulk-reset in-progress issues or clear active identities during recovery.
7. Only then render the final ledger/PR and perform authorized cleanup. Each terminal issue's GitHub update happens immediately rather than waiting for unrelated issues.

The JSON keys below are accepted by `PoolConfig.from_record`; paths must be absolute in real runs. `SUB_COORD_AGENT` and `SUB_COORD_MODEL` configure this pool's coordinator only. Worker `FORCED_*` selection stays separate in trusted intake state/context.

```json
{
  "schema_version": 1,
  "state_file": "/absolute/run/.run-with-it/main-state.json",
  "run_root": "/absolute/run",
  "parallel_jobs": 4,
  "sub_coord_agent": "codex",
  "sub_coord_model": "gpt-6.1-sol",
  "prompt_file": "/absolute/assets/sub-coordinator-prompt.md",
  "merge_prompt_file": "/absolute/assets/merge-recovery-prompt.md",
  "status_file": "/absolute/run/.run-with-it/status/current.txt",
  "events_log": "/absolute/run/.run-with-it/status/events.log",
  "main_log": "/absolute/run/.run-with-it/main/main.log",
  "pool_state_file": "/absolute/run/.run-with-it/main/pool.state.json",
  "poll_seconds": 10,
  "timeout_seconds": 3600,
  "hard_limit_seconds": 7200,
  "detach": true
}
```

```text
ai-skills run pool --config pool.json --validate-only
ai-skills run pool --config pool.json
ai-skills run watch --events-log .run-with-it/status/events.log --pool-state-file .run-with-it/main/pool.state.json --wait-seconds 240 --poll-seconds 10
ai-skills state status-board --state-file .run-with-it/main-state.json
```

## Native Worker Configuration

Write one JSON configuration per role/cycle/attempt using file tools; use absolute paths, JSON strings for model names, and arrays for exact extra argv. `agent`/`model` below are examples: replace them with the persisted route decision and preserve `effort` and `selection_reason` in Sub-Coordinator state. Worker selectors never inherit `SUB_COORD_AGENT` or `SUB_COORD_MODEL`.

```json
{
  "agent": "codex",
  "model": "gpt-6.1-sol",
  "effort": "high",
  "role": "impl",
  "issue": "42",
  "cycle": "1",
  "context_file": "/absolute/run/.run-with-it/issues/42/context.md",
  "prompt_file": "/absolute/assets/prompt.md",
  "repo_root": "/absolute/run/.run-with-it/worktrees/issue-42",
  "issue_dir": "/absolute/run/.run-with-it/issues/42",
  "log_file": "/absolute/run/.run-with-it/issues/42/workers/impl/cycle-1-attempt-1.log",
  "done_file": "/absolute/run/.run-with-it/issues/42/workers/impl/cycle-1-attempt-1.done",
  "result_file": "/absolute/run/.run-with-it/issues/42/workers/impl/cycle-1-attempt-1-result.json",
  "state_file": "/absolute/run/.run-with-it/issues/42/workers/impl/cycle-1-attempt-1.state.json",
  "status_file": "/absolute/run/.run-with-it/status/current.txt",
  "events_log": "/absolute/run/.run-with-it/status/events.log",
  "gui_mode": "0",
  "unattended": true,
  "poll_seconds": 20,
  "quiet_seconds": 120,
  "stall_seconds": 600,
  "hard_limit_seconds": 7200,
  "detach": true
}
```

```text
ai-skills worker dispatch --config worker.json --validate-only
ai-skills worker dispatch --config worker.json
ai-skills worker inspect --state-file /absolute/run/.run-with-it/issues/42/workers/impl/cycle-1-attempt-1.state.json
```

Validation starts no provider. Dispatch creates directories, atomically persists admission and a credential-free config, and returns only after durable provider identity or terminal failed evidence. Persist the identity/state path, role, cycle, route, effort, selection reason, log/done/result paths and start time in `sub-state.json` before monitoring. Reusing an admitted attempt is refused; inspect/adopt it instead of replaying the paid launch. A retry uses a fresh attempt-specific state/log/result path only after the previous tree is verified dead.

DONE is advisory. Advance only after the owned tree is dead, its exit status is known, and the role artifact validates. Quiet live providers survive output stalls until an explicit deadline. At the hard limit preserve committed/dirty implementation progress for typed artifact recovery; unverified salvage is never success. Unknown identity blocks relaunch and destructive cleanup.

## Final PR Creation

After all issues are terminal and before `gh pr create`, render the PR body from the persisted run state:

```text
ai-skills report render --state-file .run-with-it/main-state.json
```

Save the returned Markdown with file tools to `.run-with-it/final-pr-body.md` and pass that exact file to `gh pr create --body-file .run-with-it/final-pr-body.md`.

The rendered body must list completed/closed issues as plain issue links such as `#123`. Do not use auto-closing keywords in the final PR body because issues are already closed by the per-issue GitHub update flow. Ban case-insensitive auto-closing keyword variants adjacent to issue refs: `close`, `closes`, `closed`, `fix`, `fixes`, `fixed`, `resolve`, `resolves`, `resolved`.

## Native Agent Reference

```text
ai-skills agent list --detected-only
ai-skills agent models codex
ai-skills agent run --agent codex --model gpt-6.1-sol --effort high --context-file context.md --prompt-file prompt.md --permission-mode safe --unattended --dry-run
```

Pass model/effort explicitly from routing. `safe` resolves to a registered read-only profile and fails closed if absent. Default writable permission profiles require explicit unattended authorization; GUI mode applies the registered safer noninteractive permissions. Inline prompt/context values retain exact argv boundaries, Unicode, quotes, spaces and empty extra arguments. Registry and prompt resources come from the pinned package unless explicitly overridden. `AI_SKILLS_RUN_ROOT` remains the shared run root when providers operate inside issue worktrees.

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

Run `ai-skills run stop --run-root /absolute/run` before deleting anything. It first halts pool admission, checks exact creation-time identities, rescans in-flight admissions, and verifies complete provider/dispatcher process trees. `STOP|state=dead` with exit 0 permits proceeding. Unknown ownership or survivors return exit 3: preserve state and report the remaining blocker.

Print the exact cleanup targets and obtain user authorization immediately before destructive deletion of branches/worktrees or user files. Preserve dirty user files and original run branches. After authorized cleanup update ignore entries and report what was removed. Resume never triggers cleanup.

## Appendix A: Main State Schema

The Main Orchestrator persists `.run-with-it/main-state.json` (schema_version 4). This is the Main Orchestrator's entire persistent memory.

```json
{
  "schema_version": 4,
  "run_id": "<funny-action-animal slug generated at run start>",
  "started_at": "<iso8601>",
  "run_branch": {
    "base_branch": "main",
    "base_sha": "abc123",
    "feature_branch": "Maestro/cunning-fox",
    "feature_branch_start_sha": "abc123",
    "remote": "origin",
    "pushed": true,
    "pr_url": null
  },
  "execution_plan": {
    "execution_mode": "sequential | rolling-pool",
    "concurrency_policy": "strict",
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
      "parallel_safe": true,
      "ownership_scope": ["src/orders"],
      "issue_dir": ".run-with-it/issues/36",
      "report_file": ".run-with-it/issues/36/report.json",
      "merge_recovery_report_file": ".run-with-it/issues/36/merge-recovery-report.json",
      "log_file": ".run-with-it/issues/36/sub-coordinator.log",
      "issue_branch": "Maestro/cunning-fox-issue-36",
      "worktree_path": ".run-with-it/worktrees/issue-36",
      "issue_base_sha": "abc1234",
      "issue_base_source": "remote-tracking | local-fallback",
      "commit_sha": "abc1234"
    }
  },
  "active_pool_issues": [37, 38],
  "completed_summaries": [
    {
      "issue": 36,
      "outcome": "completed",
      "summary": "One paragraph compact report summary.",
      "files_modified_count": 3,
      "lines_added": 42,
      "lines_deleted": 7,
      "review_cycles": 1,
      "commit_sha": "abc1234",
      "verification": {
        "passed": true,
        "evidence": "15 tests passed, 0 failed"
      },
      "model_usage": [
        {
          "role": "impl",
          "cycle": 1,
          "agent": "codex",
          "model": "gpt-6.1-sol",
          "selection_reason": "under-target"
        }
      ],
      "report_file": ".run-with-it/issues/36/report.json"
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
- `completed_summaries` accumulates one compact record per finished issue, including compact `summary`, `verification`, `model_usage`, and `report_file` fields — this is what the main orchestrator reads back after compression
- `merge_recovery` is non-terminal; dependencies are satisfied only by `completed`
- `ledger_rows` stores verbatim STATUS lines for the final ledger printout
- `active_pool_issues` lists which issues have active Sub-Coordinators; on resume follow the Resume Flow in Appendix C — validate the supervisor lease and preserve active state, because detached dispatchers may still be running
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
- pool detached: `STATUS|type=pool-detached|pid=<pid>|pool_state_file=<path>`
- pool already running (duplicate launch refused, exit 2): `STATUS|type=pool-already-running|pid=<pid>|pool_state_file=<path>`
- pool reattached: `STATUS|type=pool-reattached|count=<n>|state_file=<path>`
- sub-coordinator reattach: `STATUS|type=sub-coord-reattach|issue=<n>|pid=<pid>|identity=<live|stale|dead>|report_file=<path>` — `stale` means the recorded process creation identity no longer matches (recycled PID); it is never adopted as owned. Unknown inspection preserves state and prohibits replacement
- pool admission deferred: `STATUS|type=pool-admission-deferred|count=<n>|deferrals=<issue:reason,...>`
- pool unreachable: `STATUS|type=pool-unreachable|count=<n>|unreachable=<issue:root,root;...>` — issues that can never become ready because a dependency ended terminal-but-not-completed. `root` names the terminal issue to blame, attributed transitively. Repeated with `|final=1` when the pool drains, so a run that ends early always says which failures stranded what.
- sub-coordinator recovery wait: `STATUS|type=sub-coord-recovery-wait|issue=<n>|role=<role>|worker_state=<state>|state_file=<path>|reason=in-flight-worker-running|waited_seconds=<n>|worker_age_seconds=<n>|max_wait_seconds=<n>` — throttled to `WAIT_STATUS_INTERVAL_SECONDS`; `worker_age_seconds` is how long since any of that worker's artifacts moved
- pool empty: `STATUS|type=pool-empty|pending_remaining=<n>`
- merge recovery queued: `STATUS|type=merge-recovery|issue=<n>|report_file=<path>|state=<started|completed|failed-merge|blocked>`
- memory refresh: `STATUS|type=memory-refresh|state_file=.run-with-it/main-state.json|tasks_loaded=<n>|completed=<n>|pending=<n>|failed=<n>`
- main loop: `STATUS|type=main-loop|iteration=<n>|pending=<count>|completed=<count>|failed=<count>`
- sub-coordinator spawn: `STATUS|type=sub-coord-spawn|issue=<n>|agent=<name>|model=<model>|report_file=<path>|log_file=<path>|pool_size=<n>|parallel_jobs=<PARALLEL_JOBS>`
- sub-coordinator pid-tracked: `STATUS|type=sub-coord-pid|issue=<n>|pid=<pid>|done_file=<path>|report_file=<path>`
- live agent start: `STATUS|type=agent-start|issue=<n>|role=<sub-coord|complexity|impl|review|modify>|agent=<name>|model=<model>`
- managed provider exit: `STATUS|type=provider-exit|issue=<n>|role=<sub-coord|complexity|impl|review|modify>|agent=<name>|model=<model>|return_code=<n>` — process observation only
- validated handoff: `STATUS|type=dispatch-complete|issue=<n>|role=<role>|status=completed|result_file=<path>|run_id=<run>|attempt_id=<attempt>` — managed terminal decision; use the bound outcome journal
- sub-coordinator complete: `STATUS|type=sub-coord-complete|issue=<n>|outcome=<completed|failed-review|merge_failed|blocked>|report_file=<path>|commit_sha=<sha-or-none>`
- merge start: `STATUS|type=merge-start|issue=<n>|branch=<issue_branch>|target=<feature_branch>`
- merge complete: `STATUS|type=merge-complete|issue=<n>|merge_sha=<sha>|pushed=<true|false>`
- merge failed: `STATUS|type=merge-failed|issue=<n>|reason=<conflict|verification|push|unknown>`
- stall: `STATUS|type=stall|issue=<n>|idle_for=<seconds>|action=alert-user`
- intake fallback: `STATUS|type=intake-fallback|reason=<no-gh-auth|no-remote|gh-permission-blocked|gh-failed-after-escalation>`
- runner sandbox retry: `STATUS|type=runner-sandbox-retry|agent=<agent>|model=<model>|reason=<error-summary>`
- runner sandbox retry result: `STATUS|type=runner-sandbox-retry-result|outcome=<success|failed>`
- cleanup completed: `STATUS|type=cleanup|action=completed|files_removed=<n>`
- discard shutdown (from the stop helper): `STOP|type=target|source=<pool|dispatcher|runner>|pid=<pid>|action=<term-group|term-pid|stop-tree|already-dead|skip-not-ours>` followed by `STOP|result=<clean|survivors>|terminated=<n>|already_dead=<n>|skipped_not_ours=<n>`
- cleanup discarded: `STATUS|type=cleanup|action=discarded|files_removed=<n>`
- final ledger row: `STATUS|type=ledger|task=<task-id>|role=impl|cycle=0|agent=<agent-name>|model=<model-id>|added=<n>|deleted=<n>|total=<n>|reason=sub-coordinator|input_tokens=<n-or-unknown>|output_tokens=<n-or-unknown>|cache_hit_tokens=<n-or-unknown>|telemetry_source=sub-coordinator-report`

### Final Ledger

After the loop exits and before cleanup, print the aggregated final ledger by reading `ledger_rows` from `main-state.json`. This means the ledger survives compression and captures all issues including those completed before the context was compressed.

Also print a final summary of all `completed_summaries` entries showing:
- Total issues processed
- Completed / failed-review / failed-merge / blocked counts
- Total lines added/deleted across all issues
- Task-level model usage (`role`, `cycle`, `agent`, `model`, `selection_reason`) from each issue's `model_usage`
- Aggregate token usage (sum `token_usage` fields from all report JSONs for issues that have completed)

## Appendix C: Resume and State Contract

### Resume Flow

On startup, if `.run-with-it/main-state.json` exists, prompt the user (per Preflight Check 6, existing-state detection). On `resume`:

1. Re-read `.run-with-it/main-state.json`.
2. **Validate the supervisor lease first.** The native pool owns `.run-with-it/main/pool.state.json` and its lease. Match the recorded host, PID and process creation time, not command-line text or PID alone. Use `ai-skills run watch` to observe an existing supervisor. If a replacement supervisor is needed, invoke the same `ai-skills run pool --config pool.json`; its native lease and attempt reconciliation decide whether admission is safe. A live owner is never replaced. Foreign, inaccessible, malformed or otherwise unknown ownership requires inspection and must never be treated as dead.
3. **Never bulk-reset `in_progress` issues or clear `active_pool_issues`.**
   Resetting an issue whose detached dispatcher is still alive dispatches it a
   second time: duplicate work, merges, state writes, token cost, and GitHub
   comments. Requeue an individual issue to `pending` (via
   `ai-skills state requeue`) only when structured evidence proves its
   entire owned dispatcher/provider tree is proven dead by creation identity — AND no issue-scoped `sub-state.json` supports recovery. The pool
   runner's re-attach path already performs this analysis; prefer relaunching
   the pool over manual requeues.
4. Identify all issues with `status="pending"` — these haven't started yet.
5. Identify all issues with `status="completed"`, `"failed-review"`, `"failed-merge"`, or `"blocked"` — skip these entirely.
6. Re-enter Main Loop at Step A.
7. Emit: `STATUS|type=resume|tasks_restored=<n>|completed=<n>|supervisor=<alive|relaunched>|re_queued_in_progress=<m>|parallel_jobs=<PARALLEL_JOBS>`

If `.run-with-it/main-state.json` is missing or unparseable when the user types `resume`, emit:

```
STATUS|type=resume-error|reason=<missing|parse-error>|action=user-required
```

Then stop and ask the user whether to proceed as a fresh run.

### Compression Survival

After context compression (conversation history cleared), treat the situation as a resume: re-read `.run-with-it/main-state.json` per Step A, then follow the Resume Flow above — validate the supervisor lease and preserve active state. The pool supervisor and dispatchers run detached and are unaffected by compression; they are almost certainly still alive, so re-enter the watch loop rather than touching issue state. `completed` issues are never re-run. The state file is always authoritative — never derive issue state from conversation history.

## Appendix D: Terminal Issue Comment Contract

### Terminal Issue Comments

Post issue comments immediately for terminal outcomes: `completed`, `blocked`, `failed-review`, or `failed-merge` (set after merge recovery fails).
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
<completed|blocked|failed-review|failed-merge>

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
- `Notes` must include exactly one review summary line when `DELEGATED_REVIEW=true` and review ran. Format: `Review: <verdict-path>, final verdict: <approve|reject>, reviewer model: <model-id>`. For a straight approval write `approve (1 cycle)`; for a revise-then-approve write `revise (N cycles)`. When `report.review_skipped` is true, write `Review: skipped (trivial-change)` instead.
- `Blocking Reasons` section must be included **only** when `report.blocking_reasons` is non-empty. Render each entry as a separate markdown bullet. Omit the section entirely otherwise.

## Native GitHub terminal update

Only the Main control plane performs this operation; Sub-Coordinators never do. After validated terminal reporting call once per issue (the helper maintains its idempotent update ledger):

```text
ai-skills github update --state-file .run-with-it/main-state.json --run-root /absolute/run --issue 42 --outcome completed --report-file .run-with-it/issues/42/report.json
```

It comments and closes only completed issues; failed/blocked issues stay open with evidence. Preserve failed update intent for retry.
