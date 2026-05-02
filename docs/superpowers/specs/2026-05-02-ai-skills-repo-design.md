# AI-Skills Repo Design

**Date:** 2026-05-02  
**Repo:** chanakya-net/AI-Skills

---

## Problem

A personal/shareable repository of AI skills that installs cleanly across all major AI coding agents (Claude Code, Gemini CLI, GitHub Copilot CLI + VS Code, Cursor, Codex, OpenCode, Windsurf, JetBrains Junie/Rider, Cline, Roo, Amp, and 20+ more).

---

## Approach

Mirror the installation pattern from [juliusbrussee/caveman](https://github.com/juliusbrussee/caveman):

- Skills live in `skills/<name>/SKILL.md` with YAML front-matter
- Claude Code gets a native plugin via `.claude-plugin/`
- Gemini CLI gets a native extension via `gemini-extension.json`
- All other agents (30+) are served via `npx -y skills add chanakya-net/AI-Skills -a <profile>`
- A single `install.sh` detects which agents are installed and routes to the right method
- One-liner install: `curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.sh | bash`

---

## Repository Structure

```
AI-Skills/
├── .claude-plugin/
│   ├── marketplace.json    ← Claude Code marketplace schema + plugin list
│   └── plugin.json         ← Claude Code plugin config (name, description, author)
├── gemini-extension.json   ← Gemini CLI extension metadata
├── skills/
│   └── save-tokens/
│       └── SKILL.md        ← YAML front-matter (name, description) + skill body
├── install.sh              ← Smart multi-agent installer
├── README.md               ← Usage, install instructions, agent matrix
└── LICENSE
```

---

## Skill Format

Each skill is a directory under `skills/` containing a `SKILL.md`:

```markdown
---
name: skill-name
description: >
  One-sentence description of what the skill does and when it triggers.
---

## Skill body content here
```

---

## Starter Skill: `save-tokens`

Trigger phrases: "save tokens", "RTU mode", "compress", "be brief".

Rules:
- Drop articles, filler, pleasantries, hedging, transitional phrases
- Use symbols: `->` leads-to, `<-` triggered-by, `=>` returns, `~` approx, `∵` because, `∴` therefore, `|` or, `!` not
- Short synonyms: big/fix/use over extensive/implement/utilize
- Fragments OK. Technical terms, code blocks: exact, uncompressed.

Boundaries: code blocks/commits/PRs written normally. "stop" or "normal mode" reverts.

---

## Installation Script (`install.sh`)

The script:
1. Checks for each agent via `command -v` or config directory presence
2. For each detected agent, runs the appropriate install command
3. Reports installed / skipped / failed per agent
4. Supports `--dry-run`, `--only <agent>`, `--minimal`, `--list`, `--help`

Agent routing:

| Agent(s) | Command |
|----------|---------|
| Claude Code | `claude plugin install github:chanakya-net/AI-Skills` |
| Gemini CLI | `gemini extensions install github.com/chanakya-net/AI-Skills` |
| Codex, Copilot, Cursor, Windsurf, Cline, Roo, OpenCode, Junie, Amp, and 20+ others | `npx -y skills add chanakya-net/AI-Skills -a <profile>` |

---

## Claude Code Plugin Config

**`.claude-plugin/marketplace.json`** — conforms to `https://anthropic.com/claude-code/marketplace.schema.json`.

**`.claude-plugin/plugin.json`** — minimal config: name, description, author. No hooks needed for a pure-skills plugin.

---

## Gemini Extension Config

**`gemini-extension.json`** — name, description, version, contextFileName pointing to `GEMINI.md` (if needed) or omitted for skill-only extensions.

---

## Adding New Skills

Drop a new directory under `skills/`:

```
skills/
└── my-new-skill/
    └── SKILL.md
```

The installer picks it up automatically on next `npx skills add` invocation. No changes to install.sh or plugin config needed.

---

## README

Covers:
- What this repo is
- One-liner install command
- Full agent support table
- How to add a new skill
- License

---

## Self-Review Notes

- No TBDs or placeholders remain
- Structure is consistent throughout (all paths match)
- Scope is focused: skill storage + multi-agent install. No hooks, no MCP, no per-repo rule files (can be added later)
- Agent profile names for `npx skills add` match caveman's established slugs (codex, github-copilot, cursor, etc.)
