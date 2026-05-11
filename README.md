# AI-Skills

> Personal AI skills for any coding agent — install once, use everywhere.

## What This Repo Does

AI-Skills is a reusable skill collection for coding agents (Copilot, Codex, Claude, Gemini, and others).
It gives you a practical workflow from requirement discovery to issue planning to execution, with shared runner assets so behavior stays consistent across agents and operating systems.

## Repository Structure

```text
AI-Skills/
├── README.md
├── LICENSE
├── gemini-extension.json
├── install.sh
├── install.ps1
├── uninstall.sh
├── uninstall.ps1
├── technical_requirements.md
├── assets/
│   ├── agent-registry.json
│   ├── complexity-prompt.md
│   ├── coordinator-rules.md
│   ├── main-orchestrator-rules.md
│   ├── modifier-prompt.md
│   ├── prompt.md
│   ├── review-prompt.md
│   ├── run-agent.sh
│   ├── run-agent.ps1
│   └── sub-coordinator-prompt.md
├── skills/
│   ├── break-req/SKILL.md
│   ├── create-git-issue/SKILL.md
│   ├── run-with-it/SKILL.md
│   ├── save-tokens/SKILL.md
│   └── tdd-implementation/SKILL.md
└── tests/
    ├── add-two-numbers.test.sh
    ├── break-req-contract.test.sh
    ├── create-git-issue-routing.test.sh
    ├── install-assets-contract.test.sh
    ├── run-agent.test.sh
    ├── run-with-it-routing.test.sh
    └── uninstall-contract.test.sh
```

## Skills At A Glance

- `break-req`: Discovers and resolves functional/non-functional decisions, constraints, and dependencies before planning starts.
- `create-git-issue`: Turns approved requirements into a PRD plus dependency-aware vertical-slice implementation issues.
- `tdd-implementation`: Implements assigned work in strict red-green-refactor cycles with behavior-first tests.
- `run-with-it`: Orchestrates execution end-to-end by routing issues to the right agent/model and tracking progress safely.
- `save-tokens`: Switches assistant narration into compact mode so long sessions consume fewer tokens.

## How To Use Them Together

1. Start with `break-req` to remove ambiguity and lock requirements.
2. Run `create-git-issue` to convert requirements into actionable issues.
4. Use `run-with-it`  to coordinate multi-issue execution and closure. it uses `tdd-implementation` and `save-tokens` internaly. 
5. Enable `save-tokens` anytime you want compressed assistant responses. This helps you using your context windows longer and reducing token costs.



## Benefits

- Better planning quality: fewer unclear requirements and fewer rework loops.
- Faster execution: issues are already sliced and dependency-aware.
- Consistent delivery: shared prompts, registry, and runners reduce cross-agent drift.
- Higher confidence: TDD discipline plus orchestration feedback loops improve correctness.
- Lower token cost: compact narration mode helps in long-running workflows.

## Quick Install

**macOS / Linux / Git Bash:**
```bash
curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.sh | bash
```

**Windows (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.ps1 | iex
```

The smart installer detects your active agent(s) and wires everything up automatically.
It also installs shared assets required by workflow skills:

- `prompt.md`
- `modifier-prompt.md`
- `review-prompt.md`
- `complexity-prompt.md`
- `run-agent.sh`
- `run-agent.ps1` on Windows
- `agent-registry.json`

Default asset location:

```
macOS/Linux:  ~/.ai-skill-collections/assets
Windows:      %USERPROFILE%\.ai-skill-collections\assets
```

Runner contract (execution-only):

Bash (macOS / Linux / Git Bash):
- `run-agent.sh --agent <agent> --context-file <context-payload-file> --prompt-file <prompt-file>`

PowerShell (Windows):
- `run-agent.ps1 --agent <agent> --context-file <context-payload-file> --prompt-file <prompt-file>`

`run-with-it` prepares context and invokes the runner. The runner does not fetch GitHub issues or git history itself.

## Codebase Overview

This repository packages a small skill collection plus the shared runner assets those skills need.

The codebase has four main surfaces:

- `skills/` contains the agent-facing skill instructions. Each skill is a standalone `SKILL.md` with YAML front matter.
- `assets/` contains runtime assets used by `run-with-it`, especially the unified runner scripts and agent/model registry.
- `install.sh` / `install.ps1` detect local agent tools and install the collection for each supported environment.
- `uninstall.sh` / `uninstall.ps1` remove agent registrations, shared assets, and this collection's installed skill directories for a clean reinstall.

The repository intentionally keeps the runtime contract simple: skills prepare context, then `run-with-it` selects an agent/model and invokes `assets/run-agent.sh` or `assets/run-agent.ps1`. The runner executes a prepared payload; it does not create issues, fetch issue bodies, or infer project history on its own.

## Unified Routing Workflow

Use this sequence for issue-driven execution:

1. `break-req` captures and resolves requirements/constraints.
2. `create-git-issue` publishes `prd.md` + implementation slices with routing hints.
3. `run-with-it` performs final runtime routing and invokes `run-agent.sh`.

`create-git-issue` hints are advisory only. `run-with-it` remains final routing authority at execution time.

## Routing and Registry Overrides

`run-with-it` uses `agent-registry.json` + complexity scoring to select agent/model.
Automatic routing allows Google/Gemini only for `quite-easy` and `easy` tasks. Direct Claude is reserved as a fallback when Copilot/Codex-compatible models cannot perform the task; shared Claude models such as Haiku 4.5 prefer GitHub Copilot before direct Claude.

Supported overrides and filters:

- `AGENT_REGISTRY_FILE` override registry path (default: `<asset-root>/agent-registry.json`)
- `AGENT_ALLOWLIST` comma-separated agent slugs to permit
- `AGENT_DENYLIST` comma-separated agent slugs to block (denylist wins conflicts)
- `AGENT` force agent selection (must be installed/valid)
- `MODEL` force model selection for selected agent
- `COMPLEXITY_LEVEL` force complexity band
- `COMPLEXITY_SCORE` force numeric score (`8-40`)

Override asset destination or git ref:

Bash:
```bash
ASSETS_DEST="$HOME/.my-ai-assets" ASSETS_REF=main curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.sh | bash
```

PowerShell:
```powershell
$env:ASSETS_DEST="$env:USERPROFILE\.my-ai-assets"; $env:ASSETS_REF="main"
irm https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.ps1 | iex
```

## Troubleshooting

### "No asset files" when running run-with-it

This means the shared files were not found in either:

- `~/.ai-skill-collections/assets` (macOS/Linux) or `%USERPROFILE%\.ai-skill-collections\assets` (Windows)
- `./assets` (current working directory)

Quick fix from this repository root:

Bash (macOS / Linux / Git Bash):
```bash
mkdir -p "$HOME/.ai-skill-collections/assets" && cp -f ./assets/prompt.md ./assets/modifier-prompt.md ./assets/review-prompt.md ./assets/complexity-prompt.md ./assets/run-agent.sh ./assets/run-agent.ps1 ./assets/agent-registry.json "$HOME/.ai-skill-collections/assets/" && chmod +x "$HOME/.ai-skill-collections/assets/run-agent.sh"
```

PowerShell (Windows):
```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.ai-skill-collections\assets"; Copy-Item -Force .\assets\prompt.md, .\assets\modifier-prompt.md, .\assets\review-prompt.md, .\assets\complexity-prompt.md, .\assets\run-agent.ps1, .\assets\run-agent.sh, .\assets\agent-registry.json "$env:USERPROFILE\.ai-skill-collections\assets\"
```

Or re-run installer:

Bash: `bash install.sh`
PowerShell: `.\install.ps1`

### No git repo yet

`run-with-it` can still run without git initialization. It will skip commit-history context and continue with issue/local context.

---

## Uninstall

Run the full uninstaller to remove skills per agent and delete shared assets. This leaves the machine ready for a clean install.

macOS / Linux / Git Bash:
```bash
curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/uninstall.sh | bash
```

Windows (PowerShell):
```powershell
irm https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/uninstall.ps1 | iex
```

Fresh reinstall from this repository root:

Bash:
```bash
bash uninstall.sh && bash install.sh
```

PowerShell:
```powershell
.\uninstall.ps1; .\install.ps1
```

The uninstaller deletes shared assets by default:

```
macOS/Linux:  ~/.ai-skill-collections
Windows:      %USERPROFILE%\.ai-skill-collections
```

It also removes this collection's installed skill directories from standard skill roots such as `~/.agents/skills`, `~/.codex/skills`, and `~/.Codex/skills`.

To preview removals:

Bash:
```bash
bash uninstall.sh --dry-run
```

PowerShell:
```powershell
.\uninstall.ps1 -DryRun
```

Manual commands are also available if you want to remove one agent by hand.

### Claude Code

```bash
claude plugin uninstall ai-skill-collections
```

### Gemini CLI

```bash
gemini extensions uninstall https://github.com/chanakya-net/AI-Skills
```

### Codex / Copilot / Antigravity (via npx)

```bash
npx -y skills remove chanakya-net/AI-Skills --global
```

### Delete shared assets

macOS / Linux:
```bash
rm -rf "$HOME/.ai-skill-collections"
```

Windows (PowerShell):
```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.ai-skill-collections"
```

---

## Per-Agent Install

Per-agent commands below install skills for that specific agent. To guarantee shared assets are installed too, prefer the `install.sh` one-liner above.

### Claude Code

```bash
claude plugin install github:chanakya-net/AI-Skills
```

### Gemini CLI

```bash
gemini extensions install github.com/chanakya-net/AI-Skills
```

### All Other Agents (via npx)

```bash
npx -y skills add chanakya-net/AI-Skills -a <agent>
```

| Agent | `--agent` slug |
|-------|---------------|
| OpenAI Codex (CLI + GUI) | `codex` |
| GitHub Copilot (CLI + VS Code) | `github-copilot` |
| Gemini GUI (Antigravity) | `antigravity` |

OpenCode note: OpenCode users must configure their preferred model in their local OpenCode setup; this repo does not set OpenCode model defaults for you.

**Example:**

```bash
npx -y skills add chanakya-net/AI-Skills -a github-copilot
```

---

## Skills

| Skill | Description |
|-------|-------------|
| [`save-tokens`](skills/save-tokens/SKILL.md) | Compresses AI responses using symbols/abbreviations to cut token usage ~75% |
| [`break-req`](skills/break-req/SKILL.md) | Interviews relentlessly to break down complex requirements and resolve design dependencies |
| [`create-git-issue`](skills/create-git-issue/SKILL.md) | Synthesizes a PRD from context, then creates dependency-aware tracer-bullet implementation issues |
| [`tdd-implementation`](skills/tdd-implementation/SKILL.md) | Enforces red-green-refactor with behavior-focused tests and thin vertical implementation slices |
| [`run-with-it`](skills/run-with-it/SKILL.md) | Routes execution by complexity/capability, selects agent+model from registry, and invokes the unified `run-agent.sh` runner |

## Runtime Assets

| File | Purpose |
|------|---------|
| [`assets/agent-registry.json`](assets/agent-registry.json) | Agent aliases, detection commands, invocation templates, model catalog, and routing metadata. |
| [`assets/run-agent.sh`](assets/run-agent.sh) | Bash runner used by macOS, Linux, Git Bash, and GUI-launched Unix-like agent workflows. |
| [`assets/run-agent.ps1`](assets/run-agent.ps1) | PowerShell runner for Windows workflows. |
| [`assets/prompt.md`](assets/prompt.md) | Shared execution prompt used by the runner workflow. |
| [`assets/review-prompt.md`](assets/review-prompt.md) | Review prompt material for follow-up quality gates. |
| [`assets/modifier-prompt.md`](assets/modifier-prompt.md) | Modification prompt for addressing reviewer comments and rerunning verification. |
| [`assets/complexity-prompt.md`](assets/complexity-prompt.md) | Complexity scoring prompt material used by routing workflows. |

## Tests

The test suite is mostly shell-based contract coverage around installer behavior, runner behavior, routing documentation, and skill workflow rules.

Focused commands:

```bash
bash tests/install-assets-contract.test.sh
bash tests/uninstall-contract.test.sh
bash tests/run-agent.test.sh
bash tests/break-req-contract.test.sh
bash tests/create-git-issue-routing.test.sh
bash tests/run-with-it-routing.test.sh
```

Note: `tests/add-two-numbers.test.sh` is a legacy tracer-bullet test file; the calculator script it references is not part of the current codebase.

---

## Adding a Skill

1. Create `skills/<name>/SKILL.md` with YAML front-matter:

```markdown
---
name: skill-name
description: What this skill does and when to use it
---

<!-- rules, examples, and any supporting content below -->
```

2. Add your rules, examples, or reference material in the body.
3. Commit and push — the skill is immediately available to all agents on next install/update.

---

## Repo Structure

```
AI-Skills/
├── README.md               # Project overview, install docs, and repo map
├── LICENSE
├── install.sh              # Smart one-liner installer
├── install.ps1             # Windows installer
├── uninstall.sh            # Full uninstaller for fresh reinstalls
├── uninstall.ps1           # Windows uninstaller
├── gemini-extension.json   # Gemini CLI extension manifest
├── technical_requirements.md
├── assets/                 # Shared prompt + runner scripts
│   ├── agent-registry.json
│   ├── complexity-prompt.md
│   ├── modifier-prompt.md
│   ├── prompt.md
│   ├── review-prompt.md
│   ├── run-agent.ps1
│   └── run-agent.sh
├── skills/
│   ├── break-req/
│   │   └── SKILL.md
│   ├── create-git-issue/
│   │   └── SKILL.md
│   ├── run-with-it/
│   │   └── SKILL.md
│   ├── save-tokens/
│   │   └── SKILL.md
│   └── tdd-implementation/
│       └── SKILL.md
└── tests/
    ├── add-two-numbers.test.sh       # Legacy tracer-bullet test
    ├── break-req-contract.test.sh
    ├── create-git-issue-routing.test.sh
    ├── install-assets-contract.test.sh
    ├── run-agent.test.sh
    ├── run-with-it-routing.test.sh
    └── uninstall-contract.test.sh
```
