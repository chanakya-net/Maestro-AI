# AI-Skills

> **Open-source multi-agent orchestration system for AI coding agents** — dependency-aware scheduling, cost-optimized model routing, and automatic merge recovery across 4 providers and 27 models.

📖 **Learn more:** [explainer.html](explainer.html) — full walkthrough &nbsp;·&nbsp; [diagram.pdf](diagram.pdf) — architecture sequence diagram &nbsp;·&nbsp;

## Overview

AI-Skills coordinates multiple AI coding agents (Codex, Claude, Copilot, Gemini) through a two-layer orchestration runtime. It takes GitHub issues from "ready" to "merged PR" without human intervention — routing each task to the best agent/model based on complexity, managing parallel execution in isolated git worktrees, recovering from merge conflicts automatically, and opening a single final pull request.

The system runs end-to-end:
1. Analyze requirements and discover dependencies
2. Generate a PRD and break it into implementation issues
3. Route each issue to the right agent/model using real-time subscription-debt balancing
4. Execute in parallel with isolated worktrees, review loops, and automatic merge recovery
5. Open a single PR with issue links, model usage summaries, and verification results

## Requirements

- **Git** — orchestration runs in isolated `git worktree`s (works without git too; it just skips commit-history context)
- **Python 3** — the routing, state, artifact, and PR-body helpers are Python scripts
- **GitHub CLI (`gh`)**, authenticated — for issue intake, comments, and the final PR (optional; falls back to local files when unavailable)
- **At least one supported coding agent** — Codex, Claude Code, GitHub Copilot, Gemini/Antigravity, or OpenCode

## Installation

**macOS / Linux / Git Bash:**

```bash
curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.sh | bash
```

**Windows PowerShell:**

```powershell
irm https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.ps1 | iex
```

The installer detects which coding agents you have (Codex, Claude, Copilot, Gemini, Agy) and installs skills + shared assets for each. Assets go to `~/.ai-skill-collections/assets` (macOS/Linux) or `%USERPROFILE%\.ai-skill-collections\assets` (Windows).

**Per-agent install (without shared assets):**

```bash
claude plugin install github:chanakya-net/AI-Skills              # Claude Code
gemini extensions install github.com/chanakya-net/AI-Skills       # Gemini CLI
npx -y skills add chanakya-net/AI-Skills -a codex                # Codex
npx -y skills add chanakya-net/AI-Skills -a github-copilot        # GitHub Copilot
npx -y skills add chanakya-net/AI-Skills -a antigravity           # Antigravity
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

## Uninstall

```bash
# macOS / Linux / Git Bash
curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/uninstall.sh | bash

# Windows PowerShell
irm https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/uninstall.ps1 | iex
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
| `save-tokens` | Ultra-compressed narration mode — drops articles, filler, and pleasantries while keeping code and technical terms exact. |

## Runtime Assets

The `assets/` directory contains the shared prompts, scripts, and configuration that power `run-with-it`.

### Runner & Dispatcher

| File | What it does |
|------|-------------|
| `run-agent.sh` / `run-agent.ps1` | Cross-agent CLI runner — wraps Codex, Claude, Copilot, Agy, and OpenCode behind a unified interface with status bus, telemetry, and GUI-safe permission downgrading. |
| `run-with-it-dispatch.sh` / `run-with-it-dispatch.ps1` | Worker dispatcher — spawns background agent sessions via `run-agent`, monitors liveness, detects stalls, and recovers missing result artifacts from git state. |
| `run-with-it-pool.sh` / `run-with-it-pool.ps1` | Rolling-pool supervisor — fills available parallel slots with ready issues, spawns Sub-Coordinators, detects merge failures, and triggers recovery. |

### Prompts (agent instructions)

| File | Role |
|------|------|
| `sub-coordinator-prompt.md` | Full Sub-Coordinator instructions — worktree bootstrap, complexity scoring, routing, implementation, review, modify loop, and merge back to shared branch. |
| `prompt.md` | Implementation worker — writes code, commits to issue worktree, produces result artifact JSON with verification evidence. |
| `review-prompt.md` | Review worker — read-only diff analysis producing JSON verdict with file/line/severity/fix comments. |
| `modifier-prompt.md` | Modify worker — addresses reviewer comments, re-verifies, commits fixes on the issue branch. |
| `complexity-prompt.md` | Complexity scoring agent — scores issues on 9 dimensions (dependency risk, architecture risk, blast radius, etc.) for routing decisions. |
| `merge-recovery-prompt.md` | Merge Recovery Coordinator — resolves conflicts when an issue branch can't merge into the shared feature branch. |
| `coordinator-rules.md` | Compact Sub-Coordinator rules re-read before every major phase for compaction survival. |
| `main-orchestrator-rules.md` | Compact Main Orchestrator rules re-read every loop iteration after context compression. |

### Routing & State

| File | What it does |
|------|-------------|
| `agent-registry.json` | Agent catalog — detection commands, invocation templates, 27-model catalog with complexity weights, routing rules, and subscription distribution targets. |
| `run-with-it-router.py` | Deterministic model router — selects agent/model pairs using usage-debt minimization across 4 providers with role-specific and complexity-band-specific targets (default: Codex 50%, Agy 20%, Copilot 20%, Claude 10%). |
| `run-with-it-state.py` | State mutation helper — atomic JSON reads/writes for issue readiness, dependency resolution, context file generation, and merge recovery state transitions. |
| `run-with-it-artifacts.py` | Artifact validator — validates worker result JSONs and safely synthesizes missing artifacts from git commits, log output, or canonical retry data. |
| `run-with-it-github-update.py` | GitHub terminal updater — posts issue comments with status/verification/token summaries and closes completed issues via `gh` CLI. |
| `run-with-it-pr-body.py` | Final PR body renderer — generates markdown with closed issue links, per-issue model usage tables, and verification summaries. |
| `worker-watch.sh` / `worker-watch.ps1` | Liveness watcher — checks PID existence, done sentinel presence, and log tail changes for background workers. |

## Runtime Architecture

`run-with-it` uses a three-tier architecture designed to survive LLM context compression over multi-hour runs.

### Stage 1: Planning

The Main Orchestrator fetches all issues labeled `ready-for-agent` from GitHub (or local files), parses `## Blocked by` sections to build a dependency graph, detects cycles, and computes a topological execution order. It creates a shared feature branch (`Maestro/<funny-action-animal>`, for example `Maestro/cunning-fox`) that will eventually hold all merged work. The execution plan and initial state are written to `.run-with-it/main-state.json`.

### Stage 2: Execution (Rolling Pool)

The pool runner maintains up to `PARALLEL_JOBS` (default 4) concurrent Sub-Coordinators. Each Sub-Coordinator gets exactly one issue and follows this lifecycle:

1. **Worktree bootstrap** — Creates an isolated issue branch and `git worktree` from the shared feature branch
2. **Complexity scoring** — Spawns a complexity agent to score the issue on 9 dimensions
3. **Model routing** — `run-with-it-router.py` selects the best agent/model pair based on complexity band, role-specific usage targets, and current subscription debt
4. **Implementation** — Worker agent writes code in the issue worktree, commits, and produces a result JSON with verification evidence
5. **Review** — Review worker analyzes the diff and produces a verdict (approve/request-changes)
6. **Modify** (if needed) — Modify worker addresses reviewer comments and re-verifies. Up to 8 review/modify cycles.
7. **Merge** — Sub-Coordinator acquires the merge lock, fetches latest shared branch, merges the issue branch, verifies, and pushes

After each Sub-Coordinator completes, the pool immediately fills the freed slot with the next ready issue. The Main Orchestrator reads only compact report JSONs — never raw logs — keeping its context window bounded regardless of run duration.

### Stage 3: Merge Recovery

When an issue branch conflicts with the shared feature branch, the Sub-Coordinator reports `outcome=merge_failed`. The pool runner transitions the issue to `merge_recovery` status and spawns a Merge Recovery Coordinator — a specialized agent that:

- Acquires the exclusive merge lock
- Has holistic access to both the shared branch and the failed issue branch
- Resolves conflicts, runs verification, commits, and pushes
- Writes a compact recovery report

Issues waiting on a `merge_recovery` issue remain blocked until recovery succeeds. Unrelated issues continue running in parallel.

### Stage 4: Final PR

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

## Routing Controls

Override routing behavior with environment variables:

| Variable | Effect |
|----------|--------|
| `AGENT` | Force a specific agent (codex, claude, github-copilot, agy) |
| `MODEL` | Force a specific model |
| `AGENT_ALLOWLIST` | Comma-separated agent slugs to permit |
| `AGENT_DENYLIST` | Comma-separated agent slugs to block |
| `COMPLEXITY_LEVEL` | Force complexity band (quite-easy through holy-fuck) |
| `COMPLEXITY_SCORE` | Force a numeric complexity score |
| `AGENT_REGISTRY_FILE` | Override the path to `agent-registry.json` |

### Orchestration knobs

Control how `run-with-it` schedules and intakes work:

| Variable | Default | Effect |
|----------|---------|--------|
| `PARALLEL_JOBS` | `4` | Rolling pool size — freed slots fill immediately. Set to `1` for sequential execution. |
| `ISSUE_LABEL` | `ready-for-agent` | Label filter for issue intake |
| `ISSUE_STATE` | `open` | Issue state filter |
| `SUB_COORD_TIMEOUT_SECONDS` | `3600` | Seconds before a non-completing Sub-Coordinator raises a stall alert |

## Testing

```bash
# Run all tests
for test_file in tests/*.test.sh; do bash "$test_file"; done

# Focused
bash tests/run-agent.test.sh
bash tests/run-with-it-dispatch.test.sh
bash tests/run-with-it-pool.test.sh
bash tests/run-with-it-routing.test.sh
```

The suite is contract-heavy — verifying exact string presence in output, skill boundaries, routing language, status events, done sentinels, and orchestration state transitions.

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
│   ├── run-with-it/SKILL.md
│   ├── save-tokens/SKILL.md
│   └── tdd-implementation/SKILL.md
│
├── assets/                                # Shared prompts, scripts, and configs
│   ├── agent-registry.json                # Agent detection, invocation, model catalog
│   ├── run-agent.sh / run-agent.ps1       # Cross-agent CLI runner
│   ├── run-with-it-dispatch.sh / run-with-it-dispatch.ps1 # Worker dispatcher with stall detection
│   ├── run-with-it-pool.sh / run-with-it-pool.ps1         # Rolling-pool supervisor
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
│   ├── complexity-prompt.md               # Complexity scoring prompt
│   ├── coordinator-rules.md               # Compact Sub-Coordinator rules
│   └── main-orchestrator-rules.md         # Compact Main Orchestrator rules
│
├── tests/                                 # Contract test suite (22 files)
│   ├── run-agent.test.sh                  # Runner behavior, dry-run, telemetry
│   ├── run-with-it-dispatch.test.sh       # Dispatcher smoke tests, artifact recovery
│   ├── run-with-it-pool.test.sh           # Pool scheduling, dependency awareness
│   ├── run-with-it-routing.test.sh        # Router behavior, score-to-level mapping
│   ├── install-assets-contract.test.sh    # Installer output verification
│   └── ... (18 more)
│
├── docs/                                  # Design plans and specs
│   └── superpowers/
│       ├── plans/                         # Architecture decision documents
│       └── specs/                         # Design specifications
│
├── apps/                                  # Optional companion applications
│   └── control/                           # Agent control panel (Next.js)
│
├── .claude/                               # Claude Code plugin config
├── .claude-plugin/                        # Claude Code marketplace entry
└── .agents/skills/                        # Duplicate skills for multi-agent discovery
```

## License

Released under the [GNU General Public License v3.0](LICENSE).
