# AI-Skills

> Personal AI skills for any coding agent — install once, use everywhere.

## Quick Install

```bash
curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.sh | bash
```

The smart installer detects your active agent(s) and wires everything up automatically.
It also installs shared assets required by workflow skills:

- `prompt.md`
- `run-codex.sh`
- `run-copilot.sh`

Default asset location:

```bash
~/.ai-skill-collections/assets
```

Runner contract (execution-only):

- `run-codex.sh <context-payload-file> <prompt-file>`
- `run-copilot.sh <context-payload-file> <prompt-file>`

These scripts no longer fetch GitHub issues or git history themselves; `run-with-it` prepares context and invokes them.

Override asset destination or git ref:

```bash
ASSETS_DEST="$HOME/.my-ai-assets" ASSETS_REF=main curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.sh | bash
```

## Troubleshooting

### "No asset files" when running run-with-it

This means the shared files were not found in either:

- `~/.ai-skill-collections/assets`
- `./assets` (current working directory)

Quick fix from this repository root:

```bash
mkdir -p "$HOME/.ai-skill-collections/assets" && cp -f ./assets/prompt.md ./assets/run-codex.sh ./assets/run-copilot.sh "$HOME/.ai-skill-collections/assets/" && chmod +x "$HOME/.ai-skill-collections/assets/run-codex.sh" "$HOME/.ai-skill-collections/assets/run-copilot.sh"
```

Or re-run installer:

```bash
bash install.sh
```

### No git repo yet

`run-with-it` can still run without git initialization. It will skip commit-history context and continue with issue/local context.

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
| [`run-with-it`](skills/run-with-it/SKILL.md) | Routes execution to Codex or Copilot runner scripts based on task complexity and runs the selected workflow |

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
