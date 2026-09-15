# AI-Skills

> **Open-source multi-agent orchestration system for AI coding agents** — dependency-aware scheduling, live stage visibility, cost-optimized model routing, artifact recovery, and automatic merge recovery across 4 providers and 30 models.

📖 **Learn more:** [explainer.html](explainer.html) — full walkthrough &nbsp;·&nbsp; [diagram.pdf](diagram.pdf) — architecture sequence diagram

## Overview

AI-Skills coordinates multiple AI coding agents (Codex, Claude, Gemini/Agy, OpenCode) through a multi-stage, two-layer orchestration runtime. It takes GitHub issues from "ready" to "merged PR" without human intervention — routing each task to the best agent/model based on complexity, managing parallel execution in isolated git worktrees, recovering worker artifacts and merge conflicts automatically, and opening a single final pull request. GitHub Copilot metadata is retained only for fail-fast blocking while the Copilot plan is exhausted.

The system runs end-to-end:
1. Analyze requirements and discover dependencies
2. Generate a PRD and break it into implementation issues
3. Route each issue to the right agent/model using real-time subscription-debt balancing
4. Execute in parallel with isolated worktrees, review loops, artifact/stall recovery, and automatic merge recovery
5. Open a single PR with issue links, model usage summaries, and verification results

## Requirements

- **Git** — orchestration runs in isolated `git worktree`s (works without git too; it just skips commit-history context)
- **Python 3** — the routing, state, artifact, and PR-body helpers are Python scripts
- **GitHub CLI (`gh`)**, authenticated — for issue intake, comments, and the final PR (optional; falls back to local files when unavailable)
- **At least one enabled supported coding agent** — Codex, Claude Code, Gemini/Antigravity, Agy, or OpenCode. GitHub Copilot is registry-disabled while the Copilot plan is exhausted.

## Installation

**macOS / Linux / Git Bash:**

```bash
curl -fsSL https://raw.githubusercontent.com/chanakya-net/Maestro-AI/main/install.sh | bash
```

**Windows PowerShell:**

```powershell
irm https://raw.githubusercontent.com/chanakya-net/Maestro-AI/main/install.ps1 | iex
```

The installer detects which coding agents you have (Codex, Claude, Gemini, Agy, OpenCode; Copilot metadata remains blocked) and installs skills + shared assets for each. Assets go to `~/.ai-skill-collections/assets` (macOS/Linux) or `%USERPROFILE%\.ai-skill-collections\assets` (Windows).

**Per-agent install (without shared assets):**

```bash
claude plugin install github:chanakya-net/Maestro-AI              # Claude Code
gemini extensions install github.com/chanakya-net/Maestro-AI       # Gemini CLI
npx -y skills add chanakya-net/Maestro-AI -a codex                # Codex
npx -y skills add chanakya-net/Maestro-AI -a github-copilot        # GitHub Copilot skill target; runtime use remains blocked while exhausted
npx -y skills add chanakya-net/Maestro-AI -a antigravity           # Antigravity
```

Override the asset destination or the git ref the installer pulls from:

```bash
ASSETS_DEST="$HOME/.my-ai-assets" ASSETS_REF=main bash install.sh
```

## Quick Start

The skills run inside your coding agent — invoke them by slash command (e.g. `/break-req` in Claude Code) or in plain language. A full run from idea to merged PR chains four skills:

```text
break-req  →  create-git-issue  →  run-with-it
   ▲                                    │
 (idea)                          (single final PR)
```

1. **Discover requirements** — `break-req` interviews you one question at a time and captures constraints.
2. **Create issues** — `create-git-issue` turns those decisions into a PRD and publishes dependency-aware `ready-for-agent` issues to GitHub.
3. **Run it** — `run-with-it` fetches every `ready-for-agent` issue, plans a topological order, and executes them in a rolling pool, opening one final PR when everything reaches a terminal state.

If you already have labeled issues, skip straight to `run-with-it`. Tune the run with environment variables — for example, sequential execution with a custom label:

```bash
PARALLEL_JOBS=1 ISSUE_LABEL=ready-for-agent  # then invoke run-with-it
```

## What's New

Recent `run-with-it` updates focus on keeping long multi-agent runs observable and recoverable:

- **Live stage board** — the pool runner emits `STATUS|type=run-board|board=...` whenever issue stages change. You can print the same read-only view with `python3 assets/run-with-it-state.py status-board --state-file .run-with-it/main-state.json` or add `--oneline`.
- **Artifact and stall recovery** — dispatchers now synthesize missing implementation/modification result artifacts from git ground truth, salvage stalled workers that left committed or dirty work behind, and escalate exhausted artifact failures to the Artifact Recovery Worker.
- **Availability-aware routing** — auth, quota, and unsupported-model failures are emitted as `STATUS|type=agent-unavailable`, excluded from subsequent routing, and do not consume the capability fallback budget.
- **Safer state repair** — `run-with-it-state.py requeue` quarantines stale terminal artifacts before a retry, and dependents blocked only by a newly completed issue are reset automatically.
- **Safer branch refs** — issue branches use the flat `${RUN_FEATURE_BRANCH}-issue-<n>` form instead of nesting under the shared branch, avoiding Git ref path conflicts.
- **Windows parity** — PowerShell runners now mirror the Bash dispatcher behavior for artifact synthesis, failure classification, and agent-unavailable reporting.

## Uninstall

```bash
# macOS / Linux / Git Bash
curl -fsSL https://raw.githubusercontent.com/chanakya-net/Maestro-AI/main/uninstall.sh | bash

# Windows PowerShell
irm https://raw.githubusercontent.com/chanakya-net/Maestro-AI/main/uninstall.ps1 | iex
```

Preview before removing: use `--dry-run` (bash) or `-DryRun` (PowerShell).

## Skills

Each skill is a standalone `SKILL.md` file that AI coding agents load as specialized instructions.

| Skill | What it does |
|-------|-------------|
| `break-req` | Interviews you one question at a time to discover requirements, map dependencies, and capture technical constraints before planning. |
| `create-git-issue` | Turns resolved requirements into a PRD and publishes dependency-aware tracer-bullet implementation issues to GitHub. |
| `run-with-it` | Two-layer orchestration runtime — schedules ready issues with topological ordering, routes work to the best agent/model, runs them in parallel pools with isolated worktrees, and recovers from merge conflicts automatically. |
| `tdd-implementation` | Strict red-green-refactor loop — one test at a time, never cuts horizontal slices, verifies everything before committing. |
| `help-me-debug` | Deep diagnosis workflow that produces both a human-readable root-cause report and a deterministic LLM-ready context file for handoff. |
| `pair-colleague` | Two AI solution colleagues explore alternatives on the same problem across bounded rounds while the invoking agent coordinates, decides when the discussion has converged, and writes a final decision dossier. |
| `save-tokens` | Ultra-compressed narration mode — drops articles, filler, and pleasantries while keeping code and technical terms exact. |

## Runtime Assets

The `assets/` directory contains the shared prompts, scripts, and configuration that power `run-with-it`.

### Runner & Dispatcher

| File | What it does |
|------|-------------|
| `run-agent.sh` / `run-agent.ps1` | Cross-agent CLI runner — wraps Codex, Claude, Agy, and OpenCode behind a unified interface with status bus, telemetry, GUI-safe permission downgrading, and structured agent-unavailable reporting; `github-copilot`/`copilot` fails fast while disabled. |
| `pair-colleague.sh` | `pair-colleague` control plane (Bash only) — run state machine, frozen per-round context, round policy enforcement, and paired agent launches through `run-agent.sh`. |
| `pair-colleague-process.py` | Concurrency and file primitives for `pair-colleague` — two children in separate process groups with independent timeouts, group termination without GNU `timeout`, atomic JSON/file writes, and Markdown contract validation. |
| `run-with-it-dispatch.sh` / `run-with-it-dispatch.ps1` | Worker dispatcher — spawns background agent sessions via `run-agent`, monitors liveness, detects stalls, classifies failures as infrastructure vs. capability, and recovers missing result artifacts from git state. |
| `run-with-it-pool.sh` / `run-with-it-pool.ps1` | Rolling-pool supervisor — fills available parallel slots with ready issues, spawns Sub-Coordinators, emits the live run-board, detects merge/sub-coordinator failures, and triggers recovery. |
| `run-with-it-watch.sh` / `run-with-it-watch.ps1` | Bounded status watcher — each call prints status lines appended since the previous call and exits within its watch window; reports `pool-empty`, `running`, or `pool-dead` so the Main Coordinator stays attached without long blocking calls. |
| `run-with-it-stop.sh` / `run-with-it-stop.ps1` | Identity-checked run shutdown — terminates and verifies complete process groups/trees for every recorded pool, dispatcher, and runner PID; used by confirmed `discard`. |

### Prompts (agent instructions)

| File | Role |
|------|------|
| `sub-coordinator-prompt.md` | Full Sub-Coordinator instructions — worktree bootstrap, complexity scoring, routing, implementation, review, modify loop, and merge back to shared branch. |
| `prompt.md` | Implementation worker — writes code, commits to issue worktree, produces result artifact JSON with verification evidence. |
| `review-prompt.md` | Review worker — read-only diff analysis producing JSON verdict with file/line/severity/fix comments. |
| `modifier-prompt.md` | Modify worker — addresses reviewer comments, re-verifies, commits fixes on the issue branch. |
| `artifact-recovery-prompt.md` | Artifact Recovery Worker — inspects dirty impl/modify work after artifact retry exhaustion, verifies or commits salvage, and writes a synthesized-result/requeue/blocked decision. |
| `complexity-prompt.md` | Complexity scoring agent — scores issues on 9 dimensions (dependency risk, architecture risk, blast radius, etc.) for routing decisions. |
| `merge-recovery-prompt.md` | Merge Recovery Coordinator — resolves conflicts when an issue branch can't merge into the shared feature branch. |
| `pair-colleague-agent-prompt.md` | Solution colleague prompt — rendered per agent and per round with the colleague's name, the round number, the round bounds, and the effort level. |
| `coordinator-rules.md` | Compact Sub-Coordinator rules re-read before every major phase for compaction survival. |
| `main-orchestrator-rules.md` | Compact Main Orchestrator rules re-read every loop iteration after context compression. |

### Routing & State

| File | What it does |
|------|-------------|
| `agent-registry.json` | Agent catalog — detection commands, invocation templates, 30-model catalog with complexity weights, routing rules, and subscription distribution targets. |
| `run-with-it-router.py` | Deterministic model router — selects agent/model pairs using usage-debt minimization across usable providers with role-specific and complexity-band-specific targets (default: Codex 60%, Claude 35%, Agy 5%; GitHub Copilot is registry-disabled while the plan is exhausted). |
| `run-with-it-state.py` | State mutation helper — atomic JSON reads/writes for issue readiness, dependency resolution, context file generation, status-board rendering, requeue repair, auto-unblocking, and merge recovery transitions. |
| `run-with-it-artifacts.py` | Artifact validator — validates worker result JSONs, classifies artifact failures, accepts verified no-ops, and safely synthesizes missing artifacts from git commits, log output, or canonical retry data. |
| `run-with-it-github-update.py` | GitHub terminal updater — posts issue comments with status/verification/token summaries and closes completed issues via `gh` CLI. |
| `run-with-it-pr-body.py` | Final PR body renderer — generates markdown with closed issue links, per-issue model usage tables, and verification summaries. |
| `worker-watch.sh` / `worker-watch.ps1` | Liveness watcher — checks PID existence, done sentinel presence, and log tail changes for background workers. |

## Runtime Architecture

`run-with-it` uses a multi-stage architecture designed to survive LLM context compression over multi-hour runs.

### Stage 1: Planning

The Main Orchestrator fetches all issues labeled `ready-for-agent` from GitHub (or local files), parses `## Blocked by` sections to build a dependency graph, detects cycles, and computes a topological execution order. It preserves each issue's `parallel_safe` and normalized `ownership_scope` metadata, creates one shared run feature branch that will eventually hold all merged work, and writes the execution plan and initial state to `.run-with-it/main-state.json`.

### Stage 2: Execution (Rolling Pool)

The pool runner maintains up to `PARALLEL_JOBS` (default 4) concurrent Sub-Coordinators. Each Sub-Coordinator gets exactly one issue and follows this lifecycle:

1. **Worktree bootstrap** — Fetches the shared branch and creates an isolated issue branch/worktree from `origin/$RUN_FEATURE_BRANCH` when available, with an explicit local fallback. The selected `issue_base_sha` and source are persisted. Issue branches use `${RUN_FEATURE_BRANCH}-issue-<n>` so Git refs stay flat.
2. **Complexity scoring** — Spawns a complexity agent to score the issue on 9 dimensions
3. **Model routing** — `run-with-it-router.py` selects the best agent/model pair based on complexity band, role-specific usage targets, and current subscription debt
4. **Implementation** — Worker agent writes code in the issue worktree, commits, and produces a result JSON with verification evidence
5. **Review** — Review worker analyzes the diff and produces a verdict (approve/request-changes)
6. **Modify** (if needed) — Modify worker addresses reviewer comments and re-verifies. Up to `MAX_ITERATIONS` review/modify cycles (default 20).
7. **Merge** — Sub-Coordinator acquires the merge lock, merges in a fresh throwaway worktree, verifies, and pushes so a conflict cannot dirty the shared checkout

After each Sub-Coordinator completes, the pool immediately fills the freed slot with the next compatible ready issue. `parallel_safe=false` or missing concurrency metadata runs exclusively; explicitly safe issues share the pool only when ownership scopes are non-overlapping. It also emits a compact run-board whenever stages change, for example `#12 impl(cyc1) | #13 blocked:12 | #14 done`. The Main Orchestrator reads only compact report JSONs — never raw logs — keeping its context window bounded regardless of run duration.

### Stage 3: Worker Recovery

Worker completion is artifact-driven: a role is complete only when the done sentinel and required JSON artifacts are valid and implementation/modification verification passed. Result commits are canonicalized before comparison, so unique abbreviated SHAs are accepted safely. If a worker exits after committing work but before writing its artifact, the dispatcher preserves it as `artifact-recovery-required`; unverified synthesized work never advances as normal success. Wrapper-owned heartbeats keep non-streaming CLIs alive, while a separate hard limit bounds truly stuck workers.

Failures are classified as:

- `infrastructure` — account/auth, quota, or unsupported-model availability failures. The failed route is excluded and retried without consuming `MAX_AGENT_FALLBACKS`.
- `capability` — a runner started but could not produce a valid artifact. These attempts consume the fallback budget.

When implementation or modification artifact retries are exhausted, the Artifact Recovery Worker inspects the issue worktree, runs verification, commits salvage when safe, and returns `synthesized-result`, `requeue`, or `blocked`.

### Stage 4: Merge Recovery

When an issue branch conflicts with the shared feature branch, the Sub-Coordinator reports `outcome=merge_failed`. The pool runner transitions the issue to `merge_recovery` status and spawns a Merge Recovery Coordinator — a specialized agent that:

- Acquires the exclusive merge lock
- Has holistic access to both the shared branch and the failed issue branch
- Resolves conflicts, runs verification, commits, and pushes
- Writes a compact recovery report

Issues waiting on a `merge_recovery` issue remain blocked until recovery succeeds. Unrelated issues continue running in parallel.

### Stage 5: Final PR

When all issues reach a terminal state (completed, failed, or blocked), the Main Orchestrator creates a single pull request from the shared feature branch. The PR body includes:

- Processed issue list with statuses
- Per-issue model usage table (which agent/model ran each role and cycle)
- Verification summaries
- Links to closed GitHub issues

### Compaction Survival

The system is built to survive LLM context compression — the Main Orchestrator's session may be compressed to a fraction of its original size after long runs. Key design decisions:

- All state lives in `.run-with-it/main-state.json`, re-read before every loop iteration
- Compact rules files (`coordinator-rules.md`, `main-orchestrator-rules.md`) are re-read after compression
- The Main Orchestrator never loads worker logs — only compact JSON reports
- Sub-Coordinators never touch GitHub state — only the pool runner does
- Worker completion requires both done sentinels and result artifacts; PID liveness alone is diagnostic
- The live status bus is terminal-visible only: `.run-with-it/status/current.txt` is overwritten with the latest status, while `.run-with-it/status/events.log` is append-only

## Pair Colleague

`pair-colleague` is a separate workflow from `run-with-it`: instead of executing issues, it runs a bounded design discussion between two AI solution colleagues and produces a decision.

### Roles

| Role | Who | Responsibility |
|------|-----|----------------|
| Coordinator | **The AI agent you invoked the skill in** | Initializes and resumes runs, launches rounds, reads both responses, maintains the candidate-solution ledger, sets each round's focus, decides when to accept, writes the final dossier. |
| Agent 1 | A harness + model + effort you choose | Equal solution colleague. |
| Agent 2 | A harness + model + effort you choose | Equal solution colleague. |

The coordinator is not a third launched model — there is no coordinator harness or model to configure. Agent 1 and Agent 2 are peers: neither reviews, grades, or judges the other, and completion order gives neither privileged context.

### Explicit configuration for both colleagues

Harness, model, and effort are **never guessed** and registry defaults are **never** silently substituted. Supply all six values (CLI flags override the matching environment variables):

```bash
assets/pair-colleague.sh start task.md \
  --agent-1-harness codex  --agent-1-model gpt-5.6-terra   --agent-1-effort high \
  --agent-2-harness claude --agent-2-model claude-sonnet-5 --agent-2-effort high
```

```bash
printf '%s\n' 'Design a caching strategy for our API' | assets/pair-colleague.sh start - \
  --agent-1-harness codex  --agent-1-model gpt-5.6-terra   --agent-1-effort high \
  --agent-2-harness claude --agent-2-model claude-sonnet-5 --agent-2-effort high
```

Both colleagues launch **only** through `run-agent.sh` against `agent-registry.json`, in safe permission mode. The registry stays responsible for aliases, executable detection, invocation templates, model flags, effort settings, and supported-model metadata. There is no per-harness command builder, no direct provider CLI call, and no arbitrary command override — `AGENT_1_CMD`-style escape hatches do not exist by design.

Safe mode is not "no flag". `--permission-mode safe` resolves to the harness's registered `permission_modes.read_only` profile — `--sandbox=read-only` for Codex, `--permission-mode=plan` for Claude Code — because colleagues must not modify the repository they are discussing. A harness with no registered read-only profile is refused at `start`, naming the harness, rather than quietly falling back to a writable default. `PAIR_COLLEAGUE_ALLOW_UNVERIFIED_SAFE=1` overrides that refusal for a harness you have verified yourself; the run then records `safety_profile: unverified` and warns that the repository may be modified.

Before creating a run, the script rejects an empty task, missing harness/model/effort values, and invalid efforts, then confirms through the runner that each harness is detected and each model is registered for it:

```bash
assets/run-agent.sh --list-agents --detected-only
assets/run-agent.sh --list-models <agent>
```

The two colleagues may share a harness or model. That is allowed but usually narrows the initial solution set — different harnesses or models tend to open with genuinely different ideas.

### Isolated round 1, frozen context afterwards

Round 1 contains only the problem and its constraints, and asks each colleague to develop **at least two materially distinct credible solutions** independently (or explain why alternatives are not viable). Neither colleague sees the other's ideas, which is what keeps the opening set unanchored.

From round 2 onward both colleagues receive the **exact same immutable snapshot bytes**, with the SHA-256 digest recorded and re-verified before every launch:

```text
# Original problem                        # Rejected candidates
# Constraints and success criteria        # Unresolved questions and risks
# Candidate-solution ledger               # Coordinator focus for this round
# Current synthesis or preferred direction# Previous round: Agent 1
# Agreements                              # Previous round: Agent 2
# Disagreements
```

Only the **previous** round appears verbatim, alongside the coordinator's compact cumulative ledger — so context stays bounded as rounds accumulate. The full history is preserved on disk. Within a round, neither colleague can see the other's current response: both are launched concurrently in separate process groups, and the coordinator reads both only after the round completes.

### Round bounds and coordinator authority

| Setting | Default | Valid range |
|---------|---------|-------------|
| `MIN_ROUNDS` / `--min-rounds` | `3` | `2 <= MIN_ROUNDS <= MAX_ROUNDS` |
| `MAX_ROUNDS` / `--max-rounds` | `8` | `MAX_ROUNDS <= 8` |
| `TIMEOUT_SECONDS` / `--timeout-seconds` | `3600` (60 min) | positive integer, per agent per attempt |
| `MAX_AGENT_ATTEMPTS` | `3` | positive integer |
| `PAIR_COLLEAGUE_RUN_ROOT` / `--run-root` | `.pair-colleague/runs` | any writable directory |
| `PAIR_COLLEAGUE_TERMINATION_GRACE_SECONDS` | `60` | seconds an agent may overrun its deadline before being terminated as hung |

A round counts as complete only when **both** colleagues return valid responses to the same snapshot. Each colleague ends its reply with `DISCUSSION_STATUS: CONTINUE` or `DISCUSSION_STATUS: READY`; those statuses are **advisory evidence only**. `READY` from both never bypasses the minimum, and never overrides the coordinator.

`record-decision` enforces the policy mechanically: `ACCEPT` before the minimum is rejected without changing state, and `CONTINUE` at the maximum round is rejected because round `MAX_ROUNDS` is a hard stop. Within those bounds the coordinator decides on semantic judgment — accept a solution both colleagues support, select one when they disagree, or synthesize compatible parts of several candidates.

Completion reasons are deterministic:

| Reason | When |
|--------|------|
| `coordinator-accepted` | `ACCEPT` recorded after the minimum and before the maximum |
| `maximum-rounds` | `ACCEPT` recorded for the maximum round, even if that round also reached agreement |
| `blocked` | Infrastructure failures exhausted the retry budget before the discussion could finish |

A `blocked` run **cannot** be finalized — it must not produce a falsely conclusive dossier. Unresolved disagreement is never smoothed over: it is carried in the coordinator decision and reproduced in the final dossier alongside the coordinator's resolution.

### Run artifacts and resumption

Every run gets its own atomically created directory (timestamp plus random suffix, so simultaneous starts cannot collide):

```text
.pair-colleague/runs/20260823-092500-a1b2c3/
  task.md  config.json  state.json
  rounds/round-01/
    snapshot.md  snapshot.sha256
    agent-1-prompt.md  agent-1-output.md  agent-1-stderr.log  agent-1-run.json
    agent-2-prompt.md  agent-2-output.md  agent-2-stderr.log  agent-2-run.json
    agent-2-attempt-01-output.md          # failed attempts are kept, never overwritten
    agent-2-attempt-01-run.json           # attempt manifest: host, pid, pgid, deadline, outcome
    coordinator-decision-template.md  coordinator-decision.md
  final-solution.md
  .lock/                                  # present only while a mutating command is running
```

`state.json` is the source of truth for phase, round, completed colleagues, retry counts, decisions, and completion reason; it and every canonical artifact are written atomically, so a failed command leaves the last committed state untouched. Only non-secret effective configuration is stored — no environment dumps, tokens, or credentials. If one colleague fails, its result is preserved and only the failed colleague is retried against the unchanged snapshot.

### Concurrency, interruption, and recovery

Agents cost money and can outlive the command that started them, so the control plane never guesses:

- **One writer at a time.** Every mutating command holds an exclusive per-run lock. A second `run-round` against the same run exits `4` with `LAUNCH=busy` and changes nothing, instead of allocating the same attempt number twice. A lock left by a dead process on this host is cleared automatically; a lock recorded on a different host needs `--break-lock`, because its liveness cannot be checked here.
- **Breaking a lock never authorizes a relaunch.** Each attempt records its own manifest — host, supervisor pid, process-group id, deadline, snapshot digest — before the child exists. `run-round` reconciles those manifests first: an agent still running means exit `4`, an agent that finished is collected and promoted without being launched again, and an agent past its deadline plus `PAIR_COLLEAGUE_TERMINATION_GRACE_SECONDS` is terminated and recorded as timed out.
- **Interruption cleans up.** `SIGINT`/`SIGTERM`/`SIGHUP` to the supervisor terminate every launched process group — children and grandchildren — and mark each attempt manifest `interrupted`.
- **Retries converge.** Repeating a mutating command with byte-identical input finishes the interrupted transition and commits the same state; repeating it with different input fails with exit `2` and names the conflicting artifact rather than overwriting it. This holds across every artifact/state boundary: attempt collection, output promotion, the coordinator decision, the next snapshot, and the final dossier.
- **Deliberate abandonment.** `reap --run-dir <dir> --force` terminates live agents and records the attempt as failed. Without `--force` it refuses while work is in flight.

### Workspace identity

A run is bound at `start` to the repository it was created in and records it as `workspace_root`. Every agent is launched with that directory as its working directory and its `REPO_ROOT`, so resuming from another directory — or inheriting a conflicting `REPO_ROOT` — cannot make a later round review a different repository. Runs created before this binding existed report an empty `WORKSPACE_ROOT` and must be bound once, explicitly, with `--bind-workspace <dir>`.

### Artifact integrity

The frozen snapshot is verified before the agents launch **and again after they exit**, so a snapshot that changed while they worked invalidates the round instead of silently reshaping the context. Promoted colleague responses, coordinator decisions, and the final dossier are hashed when recorded and re-verified before they are used downstream. This protects against accidental and concurrent mutation; it is not a defence against a hostile process running as the same user, which could rewrite an artifact and its recorded digest together. Asset digests (runner, process helper, prompt template) are recorded at `start` and drift is reported as a warning.

Resume any run with `status`, which prints the phase and paths without changing state:

```bash
assets/pair-colleague.sh status --run-dir .pair-colleague/runs/<run-id>
```

| `STATUS` | Next step |
|----------|-----------|
| `initialized` / `awaiting-agents` | `run-round` |
| `awaiting-coordinator` | Read the round, write the decision, `record-decision` |
| `continue` | `run-round` on the newly generated snapshot |
| `awaiting-final` | Write the dossier, `finalize` |
| `complete` | Present `final-solution.md` |
| `error` + `COMPLETION_REASON=blocked` | Report the blocker; the run cannot be finalized |

`status` also reports `WORKSPACE_ROOT` and `LIVE_AGENTS`. It never takes the lock and never mutates state, so it stays usable while a round is running.

### The final dossier

`finalize` validates the dossier before saving it as `final-solution.md`, rejecting missing or duplicated headings, placeholder text, an inconsistent round count, or a completion reason that conflicts with the run state. Validation is fence-aware: Markdown inside a fenced code block is an example, never document structure, so a dossier may quote the template it was built from without tripping the heading, status-line, or placeholder rules — while an untouched template still fails. The `## Discussion record` provenance must appear inside that section exactly once, and the `Agent 1` / `Agent 2` lines must match the harness, model, and effort the run actually used. It covers the executive summary; problem and success criteria; constraints and assumptions; every candidate considered; the trade-off comparison; the selected or synthesized solution; its detailed design and operation; how it maps onto the requirements; the implementation approach; the decision rationale; where the colleagues agreed; any remaining disagreement and how the coordinator resolved it; rejected alternatives; risks and mitigations; open questions needing a human; and the discussion record (both colleagues' configuration, round counts, completion reason, and run directory).

### Scope

Version 1 is **Bash only** — macOS, Linux, and WSL. Native Windows (Git Bash, MSYS, Cygwin) is **not** supported: the process helper depends on POSIX sessions, process groups, and signal delivery to terminate agent process trees, so `pair-colleague.sh` refuses to run there and points at WSL. There is no native PowerShell implementation, and `install.ps1` deliberately does not install these assets rather than shipping a Bash feature whose runner assets are missing. Orchestration is entirely local; the harnesses you configure may still call remote models.

## Routing Controls

Override routing behavior with environment variables:

| Variable | Effect |
|----------|--------|
| `FORCED_AGENT` | Canonical explicit child-worker agent override (codex, claude, agy). `github-copilot`/`copilot` fails fast while the Copilot plan is exhausted. |
| `FORCED_MODEL` | Canonical explicit child-worker model override |
| `AGENT` | Deprecated compatibility alias: normalize only an explicitly user-supplied value to `FORCED_AGENT`; never read ambient runtime values. |
| `MODEL` | Deprecated compatibility alias: normalize only an explicitly user-supplied value to `FORCED_MODEL`; never read ambient runtime values. |
| `AGENT_ALLOWLIST` | Comma-separated agent slugs to permit |
| `AGENT_DENYLIST` | Comma-separated agent slugs to block |
| `RUN_WITH_IT_MODEL_DENYLIST` | Comma-separated models or `agent:model` routes to exclude after availability failures |
| `RUN_WITH_IT_MODEL_AVAILABILITY_FILE` | Optional persisted route-availability file. A missing file is empty state; a present malformed file is an error. |
| `COMPLEXITY_LEVEL` | Force complexity band (quite-easy through holy-fuck) |
| `COMPLEXITY_SCORE` | Force a numeric complexity score |
| `AGENT_REGISTRY_FILE` | Override the path to `agent-registry.json` |

### Automatic routing matrix

The router calculates a base complexity band, applies the role adjustment, and
then limits every non-complexity automatic route to this exact set:

| Effective band | Automatic models |
|---|---|
| quite-easy / easy | GPT-5.4, Codex Spark, GPT-5.6 Luna, Claude Sonnet 5, Claude Haiku 4.5, eligible Gemini models exposed by Agy |
| medium | GPT-5.6 Terra, Codex Spark, Claude Sonnet 5 |
| medium-hard | GPT-5.5, GPT-5.6 Sol, Claude Opus 5 |
| complex | GPT-5.6 Sol, Claude Opus 5 |
| holy-fuck | GPT-5.6 Sol, GPT-6 Astra, Claude Opus 5, Claude Fable 5.1 |

Complexity scoring keeps its independent lightweight routing. Review applies a
one-band increase and planning applies a two-band increase before this matrix.
Explicit forced-model overrides may select outside the automatic matrix but
still must pass agent compatibility and availability checks.

Effort is also based on the effective band: Sol uses `high` at medium-hard and
`xhigh` at complex/holy-fuck; Sonnet 5 uses `low`, `medium`, and `medium` from
quite-easy through medium; Opus 5 uses `high` at medium-hard, `xhigh` at
complex, and `max` at holy-fuck; Astra and Fable 5.1 use `max` at holy-fuck. The runner
translates these to Codex `model_reasoning_effort` or
Claude Code `--effort`.

### Orchestration knobs

Control how `run-with-it` schedules and intakes work:

| Variable | Default | Effect |
|----------|---------|--------|
| `PARALLEL_JOBS` | `4` | Rolling pool size — freed slots fill immediately. Set to `1` for sequential execution. |
| `ISSUE_LABEL` | `ready-for-agent` | Label filter for issue intake |
| `ISSUE_STATE` | `open` | Issue state filter |
| `ISSUE_LIMIT` | `1000` | Maximum number of matching issues to fetch |
| `SUB_COORD_AGENT` | `codex` | Agent used to run Sub-Coordinators |
| `SUB_COORD_MODEL` | `gpt-5.6-sol` | Model used to run Sub-Coordinators |
| `SUB_COORD_TIMEOUT_SECONDS` | `3600` | Seconds before a non-completing Sub-Coordinator raises a stall alert |
| `STATUS_POLL_SECONDS` | `10` | Pool status polling cadence |
| `MAX_AGENT_FALLBACKS` | `2` | Capability-failure retry budget per worker role |
| `RUN_WITH_IT_AUTO_FAIL_STALLED_ROLES` | `complexity,impl,modify` | Worker roles the dispatcher may terminate after stall detection |
| `RUN_WITH_IT_WORKER_QUIET_SECONDS` | `120` | Seconds of worker silence before `quiet` status |
| `RUN_WITH_IT_WORKER_STALL_SECONDS` | platform default | Seconds of worker silence before `stalled` status |
| `RUN_WITH_IT_HEARTBEAT_SECONDS` | `30` | Runner-owned heartbeat cadence, independent of model stdout |
| `RUN_WITH_IT_WORKER_HARD_LIMIT_SECONDS` | `7200` | Hard elapsed worker bound; `0` disables it |

The pool dispatcher's `--agent` and `--model` values, including the default Sol
model, configure only each Sub-Coordinator process. They never become
child-worker routing overrides; use explicit `FORCED_AGENT` and `FORCED_MODEL`
for that policy.

For deprecated compatibility, the Main Orchestrator normalizes `AGENT` or
`MODEL` only when that alias is explicitly named in the user's request. A
matching canonical `FORCED_*` request takes precedence. Ambient `AGENT` and
`MODEL` are never inspected for aliases and are discarded at the dispatcher
boundary, so they cannot affect Sub-Coordinator routing.

## Testing

```bash
# Run all tests
for test_file in tests/*.test.sh; do bash "$test_file"; done

# Focused
bash tests/run-agent.test.sh
bash tests/run-with-it-dispatch.test.sh
bash tests/run-with-it-pool.test.sh
bash tests/run-with-it-routing.test.sh
bash tests/pair-colleague.test.sh
```

The suite is contract-heavy — verifying exact string presence in output, skill boundaries, routing language, status events, done sentinels, artifact synthesis, failure classification, PowerShell parity, and orchestration state transitions.

## Troubleshooting

### Missing shared assets
Re-run the installer: `bash install.sh` or `.\install.ps1`

### Manual asset repair (Unix)

```bash
mkdir -p "$HOME/.ai-skill-collections/assets"
for f in assets/*; do cp "$f" "$HOME/.ai-skill-collections/assets/"; done
chmod +x "$HOME/.ai-skill-collections/assets/"*.sh "$HOME/.ai-skill-collections/assets/"*.py
```

### No git repo
`run-with-it` works without git — it skips commit-history context and continues with issue/local context.

## Adding a Skill

1. Create `skills/<name>/SKILL.md` with YAML front matter (`name`, `description`)
2. Add supporting files under `assets/` if needed
3. Add or update contract tests under `tests/`
4. Re-run relevant tests before publishing

## Repository Structure

```
AI-Skills/
├── README.md
├── LICENSE
├── explainer.html                         # Detailed project walkthrough
├── diagram.pdf                            # Architecture sequence diagram
├── gemini-extension.json                  # Gemini CLI extension manifest
├── technical_requirements.md              # run-with-it feature spec (break-req output)
├── install.sh / install.ps1               # Smart cross-platform installers
├── uninstall.sh / uninstall.ps1           # Full cleanup utilities
├── skills-lock.json                       # SHA-256 integrity hashes for all skills
│
├── skills/                                # Agent-facing skill instructions
│   ├── break-req/SKILL.md
│   ├── create-git-issue/SKILL.md
│   ├── help-me-debug/SKILL.md
│   ├── pair-colleague/SKILL.md
│   ├── run-with-it/SKILL.md
│   ├── save-tokens/SKILL.md
│   └── tdd-implementation/SKILL.md
│
├── assets/                                # Shared prompts, scripts, and configs
│   ├── agent-registry.json                # Agent detection, invocation, model catalog
│   ├── run-agent.sh / run-agent.ps1       # Cross-agent CLI runner
│   ├── pair-colleague.sh                  # Two-agent solution coordinator control plane (Bash only)
│   ├── pair-colleague-process.py          # Concurrent agent execution, timeouts, atomic writes, validators
│   ├── pair-colleague-agent-prompt.md     # Solution colleague prompt template
│   ├── run-with-it-dispatch.sh / run-with-it-dispatch.ps1 # Worker dispatcher with stall detection
│   ├── run-with-it-pool.sh / run-with-it-pool.ps1         # Rolling-pool supervisor
│   ├── run-with-it-watch.sh / run-with-it-watch.ps1       # Bounded status watcher
│   ├── run-with-it-stop.sh / run-with-it-stop.ps1         # Identity-checked run shutdown
│   ├── run-with-it-router.py              # Deterministic usage-debt model router
│   ├── run-with-it-state.py               # Atomic JSON state mutations
│   ├── run-with-it-artifacts.py           # Artifact validation and synthesis
│   ├── run-with-it-github-update.py       # GitHub issue comment/close helper
│   ├── run-with-it-pr-body.py             # Final PR body renderer
│   ├── worker-watch.sh / worker-watch.ps1 # Worker liveness watcher
│   ├── prompt.md                          # Implementation worker prompt
│   ├── sub-coordinator-prompt.md          # Sub-Coordinator prompt
│   ├── merge-recovery-prompt.md           # Merge Recovery Coordinator prompt
│   ├── review-prompt.md                   # Review worker prompt
│   ├── modifier-prompt.md                 # Modify worker prompt
│   ├── artifact-recovery-prompt.md        # Artifact recovery worker prompt
│   ├── complexity-prompt.md               # Complexity scoring prompt
│   ├── coordinator-rules.md               # Compact Sub-Coordinator rules
│   └── main-orchestrator-rules.md         # Compact Main Orchestrator rules
│
├── tests/                                 # Contract test suite (31 files)
│   ├── run-agent.test.sh                  # Runner behavior, dry-run, telemetry
│   ├── run-with-it-dispatch.test.sh       # Dispatcher smoke tests, artifact recovery
│   ├── run-with-it-pool.test.sh           # Pool scheduling, dependency awareness
│   ├── run-with-it-routing.test.sh        # Router behavior, score-to-level mapping
│   ├── pair-colleague.test.sh             # Two-agent coordinator contract, frozen context, round policy
│   ├── install-assets-contract.test.sh    # Installer output verification
│   └── ... (19 more)
│
├── docs/                                  # Design plans and specs
│   └── superpowers/
│       ├── plans/                         # Architecture decision documents
│       └── specs/                         # Design specifications
│
├── .claude-plugin/                        # Claude Code marketplace entry
└── .agents/skills/                        # Duplicate skills for multi-agent discovery
```

## License

Released under the [ALGP License](LICENSE).
