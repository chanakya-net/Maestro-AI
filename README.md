# AI-Skills

> Personal AI skills for any coding agent — install once, use everywhere.

## Quick Install

```bash
curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.sh | bash
```

The smart installer detects your active agent(s) and wires everything up automatically.

---

## Per-Agent Install

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
├── skills/
│   └── save-tokens/
│       └── SKILL.md        # Skill definition with front-matter
└── docs/                   # Additional documentation
```
