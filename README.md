# AI-Skills

> Personal AI skills for any coding agent — install once, use everywhere.

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
- `run-agent.sh`
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

## Unified Routing Workflow

Use this sequence for issue-driven execution:

1. `break-req` captures and resolves requirements/constraints.
2. `create-git-issue` publishes `prd.md` + implementation slices with routing hints.
3. `run-with-it` performs final runtime routing and invokes `run-agent.sh`.

`create-git-issue` hints are advisory only. `run-with-it` remains final routing authority at execution time.

## Routing and Registry Overrides

`run-with-it` uses `agent-registry.json` + complexity scoring to select agent/model.
Automatic routing reserves Google/Gemini models for last-resort fallback only. Explicit `AGENT` or `MODEL` overrides can still select Gemini after normal validation.

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
mkdir -p "$HOME/.ai-skill-collections/assets" && cp -f ./assets/prompt.md ./assets/run-agent.sh ./assets/agent-registry.json "$HOME/.ai-skill-collections/assets/" && chmod +x "$HOME/.ai-skill-collections/assets/run-agent.sh"
```

PowerShell (Windows):
```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.ai-skill-collections\assets"; Copy-Item -Force .\assets\prompt.md, .\assets\run-agent.ps1, .\assets\run-agent.sh, .\assets\agent-registry.json "$env:USERPROFILE\.ai-skill-collections\assets\"
```

Or re-run installer:

Bash: `bash install.sh`
PowerShell: `.\install.ps1`

### No git repo yet

`run-with-it` can still run without git initialization. It will skip commit-history context and continue with issue/local context.

---

## Uninstall

Remove skills per agent, then delete shared assets.

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

---

## Minimal Calculator CLI

The repo includes a tiny Bash tracer-bullet for adding two numbers:

```bash
./add-two-numbers.sh 1.5 2
```

Run its focused tests with:

```bash
bash tests/add-two-numbers.test.sh
```

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
├── install.sh              # Smart one-liner installer
├── gemini-extension.json   # Gemini CLI extension manifest
├── assets/                 # Shared prompt + runner scripts
├── skills/
│   └── save-tokens/
│       └── SKILL.md        # Skill definition with front-matter
└── docs/                   # Additional documentation
```
