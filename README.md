# AI-Skills

> 📖 **[explainer.html](explainer.html)** — detailed walkthrough. &nbsp;|&nbsp; 📊 **[diagram.pdf](diagram.pdf)** — sequence diagram.

> Personal AI skills for coding agents — install once, use across Codex, Claude, Copilot, Gemini, and related tools.

## What This Repo Does

AI-Skills is a portable skill collection plus shared runtime assets for coding agents.
It supports an issue-driven workflow from requirement discovery to PRD/slice creation to coordinated multi-agent execution.

Core flow:

1. `break-req` resolves functional/non-functional requirements.
2. `create-git-issue` turns those decisions into a PRD and dependency-aware implementation issues.
3. `run-with-it` schedules ready issues, routes work to suitable agents/models, and coordinates execution.
4. `tdd-implementation` guides assigned implementation with red-green-refactor discipline.
5. `save-tokens` compresses assistant narration for long sessions.
6. `help-me-debug` performs deep diagnosis and generates human/LLM debugging reports.

## Repository Structure

```text
AI-Skills/
├── README.md
├── LICENSE
├── explainer.html
├── diagram.pdf
├── gemini-extension.json
├── technical_requirements.md
├── install.sh
├── install.ps1
├── uninstall.sh
├── uninstall.ps1
├── add-two-numbers.sh
├── assets/
│   ├── agent-registry.json
│   ├── complexity-prompt.md
│   ├── coordinator-rules.md
│   ├── main-orchestrator-rules.md
│   ├── merge-recovery-prompt.md
│   ├── modifier-prompt.md
│   ├── prompt.md
│   ├── review-prompt.md
│   ├── run-agent.sh
│   ├── run-agent.ps1
│   ├── run-with-it-dispatch.sh
│   ├── run-with-it-dispatch.ps1
│   ├── run-with-it-artifacts.py
│   ├── run-with-it-github-update.py
│   ├── run-with-it-pool.sh
│   ├── run-with-it-pool.ps1
│   ├── run-with-it-router.py
│   ├── run-with-it-state.py
│   ├── sub-coordinator-prompt.md
│   ├── worker-watch.sh
│   └── worker-watch.ps1
├── docs/
│   └── superpowers/plans/
├── skills/
│   ├── break-req/SKILL.md
│   ├── create-git-issue/SKILL.md
│   ├── help-me-debug/SKILL.md
│   ├── run-with-it/SKILL.md
│   ├── save-tokens/SKILL.md
│   └── tdd-implementation/SKILL.md
└── tests/
    ├── add-two-numbers.test.sh
    ├── break-req-contract.test.sh
    ├── create-git-issue-routing.test.sh
    ├── help-me-debug-contract.test.sh
    ├── install-assets-contract.test.sh
    ├── install-assets-powershell-contract.test.sh
    ├── run-agent-status-bus.test.sh
    ├── run-agent-ps1-status-bus.test.sh
    ├── run-agent.test.sh
    ├── run-with-it-dispatch-ps1.test.sh
    ├── run-with-it-dispatch.test.sh
    ├── run-with-it-helpers.test.sh
    ├── run-with-it-log-harness.test.sh
    ├── run-with-it-pool-actual-flow.test.sh
    ├── run-with-it-pool-ps1.test.sh
    ├── run-with-it-pool.test.sh
    ├── run-with-it-routing-windows.test.sh
    ├── run-with-it-routing.test.sh
    ├── uninstall-contract.test.sh
    ├── worker-watch-ps1.test.sh
    └── worker-watch.test.sh
```

## Skills

| Skill | Purpose |
|-------|---------|
| [`break-req`](skills/break-req/SKILL.md) | Requirements discovery, dependency mapping, and technical constraint capture before planning. |
| [`create-git-issue`](skills/create-git-issue/SKILL.md) | Creates a PRD and dependency-aware tracer-bullet implementation issues. |
| [`help-me-debug`](skills/help-me-debug/SKILL.md) | Deep diagnosis workflow that produces human-readable and LLM-ready root-cause reports. |
| [`run-with-it`](skills/run-with-it/SKILL.md) | Final runtime authority for issue scheduling, routing, execution coordination, merge recovery, and closure. |
| [`tdd-implementation`](skills/tdd-implementation/SKILL.md) | Test-first implementation workflow using red-green-refactor and behavior-focused tests. |
| [`save-tokens`](skills/save-tokens/SKILL.md) | Ultra-compressed assistant narration mode for lower token usage. |

## Runtime Architecture

The repo has four main surfaces:

- `skills/`: agent-facing instructions. Each skill is a standalone `SKILL.md` with YAML front matter.
- `assets/`: shared prompts, registry data, runner scripts, dispatcher scripts, pool runner, and worker watcher.
- `install.sh` / `install.ps1`: smart installers that detect supported local agents and install both skills and shared assets.
- `tests/`: shell contract tests for skill boundaries, installer behavior, routing documentation, runner behavior, status bus, pool scheduling, and merge recovery flow.

`run-with-it` uses a two-layer runtime:

- Main Orchestrator: fetches ready issues, builds dependency order, creates a shared feature branch, manages a rolling pool, reads compact reports, updates GitHub/local state, and opens the final PR.
- Sub-Coordinator: handles one issue in an isolated issue branch/worktree, runs implementation/review/modify workers, verifies, then attempts to merge back into the shared feature branch.
- Merge Recovery Coordinator: runs only when an issue branch cannot merge cleanly into the shared feature branch.

Main Orchestrator does not implement code or perform issue-branch merges directly.

Durable state and logs live under `.run-with-it/` during orchestration. Worker completion requires done sentinels and compact report artifacts; PID liveness alone is diagnostic.

## Runtime Assets

| File | Purpose |
|------|---------|
| [`assets/agent-registry.json`](assets/agent-registry.json) | Agent aliases, detection commands, invocation templates, model catalog, and routing metadata. |
| [`assets/run-agent.sh`](assets/run-agent.sh) | Unix runner for macOS, Linux, Git Bash, and GUI-launched Unix-like workflows. |
| [`assets/run-agent.ps1`](assets/run-agent.ps1) | PowerShell runner for Windows workflows. |
| [`assets/run-with-it-dispatch.sh`](assets/run-with-it-dispatch.sh) | Shared dispatcher that validates inputs, spawns `run-agent.sh`, writes status events, and monitors done/result files. |
| [`assets/run-with-it-dispatch.ps1`](assets/run-with-it-dispatch.ps1) | PowerShell dispatcher for native Windows `run-with-it` orchestration. |
| [`assets/run-with-it-pool.sh`](assets/run-with-it-pool.sh) | Rolling-pool scheduler helper for ready issues and merge recovery handling. |
| [`assets/run-with-it-pool.ps1`](assets/run-with-it-pool.ps1) | PowerShell rolling-pool scheduler for native Windows orchestration. |
| [`assets/run-with-it-state.py`](assets/run-with-it-state.py) | Shared state transition helper used by both pool runners. |
| [`assets/run-with-it-github-update.py`](assets/run-with-it-github-update.py) | Shared terminal issue comment/close helper used by both pool runners. |
| [`assets/run-with-it-router.py`](assets/run-with-it-router.py) | Deterministic subscription-aware worker agent/model router and usage ledger writer. |
| [`assets/run-with-it-artifacts.py`](assets/run-with-it-artifacts.py) | Shared role artifact validator and safe synthesis helper used by both dispatchers. |
| [`assets/worker-watch.sh`](assets/worker-watch.sh) | Liveness/log-tail watcher for background workers. |
| [`assets/worker-watch.ps1`](assets/worker-watch.ps1) | PowerShell liveness/log-tail watcher for background workers. |
| [`assets/prompt.md`](assets/prompt.md) | Implementation worker prompt. |
| [`assets/sub-coordinator-prompt.md`](assets/sub-coordinator-prompt.md) | One-issue Sub-Coordinator prompt. |
| [`assets/merge-recovery-prompt.md`](assets/merge-recovery-prompt.md) | Merge Recovery Coordinator prompt. |
| [`assets/review-prompt.md`](assets/review-prompt.md) | Review worker prompt. |
| [`assets/modifier-prompt.md`](assets/modifier-prompt.md) | Modify worker prompt for addressing review feedback. |
| [`assets/complexity-prompt.md`](assets/complexity-prompt.md) | Complexity scoring and routing prompt. |
| [`assets/coordinator-rules.md`](assets/coordinator-rules.md) | Shared coordinator rules. |
| [`assets/main-orchestrator-rules.md`](assets/main-orchestrator-rules.md) | Main Orchestrator rule material. |

Runner contract:

```bash
run-agent.sh --agent <agent> --context-file <context-payload-file> --prompt-file <prompt-file>
```

Windows:

```powershell
run-agent.ps1 --agent <agent> --context-file <context-payload-file> --prompt-file <prompt-file>
```

The runner executes a prepared payload. It does not fetch GitHub issues, synthesize requirements, or infer project history on its own.

## Installation

macOS / Linux / Git Bash:

```bash
curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.ps1 | iex
```

Default shared asset locations:

```text
macOS/Linux:  ~/.ai-skill-collections/assets
Windows:      %USERPROFILE%\.ai-skill-collections\assets
```

Override asset destination or installer source ref:

```bash
ASSETS_DEST="$HOME/.my-ai-assets" ASSETS_REF=main bash install.sh
```

```powershell
$env:ASSETS_DEST="$env:USERPROFILE\.my-ai-assets"; $env:ASSETS_REF="main"; .\install.ps1
```

## Per-Agent Install

Prefer the smart installer above when possible because it also installs shared assets.

Claude Code:

```bash
claude plugin install github:chanakya-net/AI-Skills
```

Gemini CLI:

```bash
gemini extensions install github.com/chanakya-net/AI-Skills
```

Codex, GitHub Copilot, and Antigravity via `npx`:

```bash
npx -y skills add chanakya-net/AI-Skills -a <agent>
```

| Agent | `--agent` slug |
|-------|----------------|
| OpenAI Codex CLI/GUI | `codex` |
| GitHub Copilot CLI / VS Code | `github-copilot` |
| Gemini GUI / Antigravity | `antigravity` |

OpenCode users should configure model defaults in their own OpenCode setup.

## Routing Controls

`run-with-it` uses `agent-registry.json`, complexity scoring, and `run-with-it-router.py` to select agent/model combinations. The default subscription distribution target is Codex 50%, Agy 20%, GitHub Copilot 20%, and Claude 10%, with role-specific protections for complexity scoring, review, and merge recovery.

Supported overrides:

- `AGENT_REGISTRY_FILE`: registry path override.
- `AGENT_ALLOWLIST`: comma-separated agent slugs to permit.
- `AGENT_DENYLIST`: comma-separated agent slugs to block; denylist wins conflicts.
- `AGENT`: force agent selection.
- `MODEL`: force model selection for selected agent.
- `COMPLEXITY_LEVEL`: force complexity band.
- `COMPLEXITY_SCORE`: force numeric score.

`create-git-issue` may publish routing hints, but `run-with-it` remains final runtime routing authority.

## Tests

Run all shell tests:

```bash
for test_file in tests/*.test.sh; do
  bash "$test_file"
done
```

Focused tests:

```bash
bash tests/install-assets-contract.test.sh
bash tests/install-assets-powershell-contract.test.sh
bash tests/uninstall-contract.test.sh
bash tests/run-agent.test.sh
bash tests/run-agent-status-bus.test.sh
bash tests/run-agent-ps1-status-bus.test.sh
bash tests/run-with-it-dispatch.test.sh
bash tests/run-with-it-dispatch-ps1.test.sh
bash tests/run-with-it-helpers.test.sh
bash tests/run-with-it-pool.test.sh
bash tests/run-with-it-pool-ps1.test.sh
bash tests/run-with-it-pool-actual-flow.test.sh
bash tests/run-with-it-routing-windows.test.sh
bash tests/worker-watch-ps1.test.sh
bash tests/worker-watch.test.sh
```

The suite is contract-heavy. It checks skill boundaries, exact prompt/routing language, installed asset lists, status-event propagation, done sentinels, dispatcher validation, and orchestration state transitions.

## Troubleshooting

### Missing Shared Assets

If `run-with-it` cannot find shared assets, re-run the installer:

```bash
bash install.sh
```

```powershell
.\install.ps1
```

`install.sh` installs the Bash/macOS/Linux/Git Bash helper family only. `install.ps1` installs the native PowerShell helper family only. Both install the shared prompt, rule, and registry assets.

Manual Unix repair from repo root:

```bash
mkdir -p "$HOME/.ai-skill-collections/assets"
cp -f \
  ./assets/prompt.md \
  ./assets/sub-coordinator-prompt.md \
  ./assets/merge-recovery-prompt.md \
  ./assets/modifier-prompt.md \
  ./assets/review-prompt.md \
  ./assets/complexity-prompt.md \
  ./assets/coordinator-rules.md \
  ./assets/main-orchestrator-rules.md \
  ./assets/run-with-it-state.py \
  ./assets/run-with-it-github-update.py \
  ./assets/run-with-it-router.py \
  ./assets/run-with-it-artifacts.py \
  ./assets/run-agent.sh \
  ./assets/run-with-it-dispatch.sh \
  ./assets/run-with-it-pool.sh \
  ./assets/worker-watch.sh \
  ./assets/agent-registry.json \
  "$HOME/.ai-skill-collections/assets/"
chmod +x \
  "$HOME/.ai-skill-collections/assets/run-agent.sh" \
  "$HOME/.ai-skill-collections/assets/run-with-it-dispatch.sh" \
  "$HOME/.ai-skill-collections/assets/run-with-it-pool.sh" \
  "$HOME/.ai-skill-collections/assets/run-with-it-state.py" \
  "$HOME/.ai-skill-collections/assets/run-with-it-github-update.py" \
  "$HOME/.ai-skill-collections/assets/run-with-it-router.py" \
  "$HOME/.ai-skill-collections/assets/run-with-it-artifacts.py" \
  "$HOME/.ai-skill-collections/assets/worker-watch.sh"
```

Manual PowerShell repair from repo root:

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.ai-skill-collections\assets"
Copy-Item -Force .\assets\prompt.md, .\assets\sub-coordinator-prompt.md, .\assets\merge-recovery-prompt.md, .\assets\modifier-prompt.md, .\assets\review-prompt.md, .\assets\complexity-prompt.md, .\assets\coordinator-rules.md, .\assets\main-orchestrator-rules.md, .\assets\run-with-it-state.py, .\assets\run-with-it-github-update.py, .\assets\run-with-it-router.py, .\assets\run-with-it-artifacts.py, .\assets\run-agent.ps1, .\assets\run-with-it-dispatch.ps1, .\assets\run-with-it-pool.ps1, .\assets\worker-watch.ps1, .\assets\agent-registry.json "$env:USERPROFILE\.ai-skill-collections\assets\"
```

### No Git Repo

`run-with-it` can still run without git initialization. It skips commit-history context and continues with issue/local context.

## Uninstall

macOS / Linux / Git Bash:

```bash
curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/uninstall.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/uninstall.ps1 | iex
```

Preview removals:

```bash
bash uninstall.sh --dry-run
```

```powershell
.\uninstall.ps1 -DryRun
```

Fresh local reinstall:

```bash
bash uninstall.sh && bash install.sh
```

```powershell
.\uninstall.ps1; .\install.ps1
```

The uninstaller removes shared assets by default:

```text
macOS/Linux:  ~/.ai-skill-collections
Windows:      %USERPROFILE%\.ai-skill-collections
```

It also removes this collection's installed skill directories from standard skill roots such as `~/.agents/skills`, `~/.codex/skills`, and `~/.Codex/skills`.

Manual per-agent cleanup:

```bash
claude plugin uninstall ai-skill-collections
gemini extensions uninstall https://github.com/chanakya-net/AI-Skills
npx -y skills remove chanakya-net/AI-Skills --global
```

## Adding a Skill

1. Create `skills/<name>/SKILL.md` with YAML front matter:

```markdown
---
name: skill-name
description: What this skill does and when to use it.
---

## Purpose

Describe the workflow, boundaries, inputs, and outputs.
```

2. Add any supporting assets under `assets/` if the skill needs runtime files.
3. Add or update contract tests under `tests/`.
4. Re-run the relevant tests before publishing.
