# AI-Skills Repo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a shareable GitHub repo of AI skills that installs cleanly across 30+ AI coding agents via a single `install.sh` one-liner.

**Architecture:** Skills live in `skills/<name>/SKILL.md`. Claude Code gets a native `.claude-plugin/` config; Gemini gets `gemini-extension.json`; every other agent (Copilot, Cursor, Codex, OpenCode, Windsurf, Cline, Junie, Amp, etc.) is served via `npx -y skills add chanakya-net/AI-Skills -a <profile>`. The installer auto-detects which agents are present and routes accordingly.

**Tech Stack:** Bash (install.sh), Markdown (SKILL.md, README), JSON (plugin configs)

---

## File Map

| File | Purpose |
|------|---------|
| `skills/save-tokens/SKILL.md` | Starter skill — token-compressed response mode |
| `.claude-plugin/marketplace.json` | Claude Code marketplace registration |
| `.claude-plugin/plugin.json` | Claude Code plugin metadata |
| `gemini-extension.json` | Gemini CLI extension metadata |
| `install.sh` | Smart multi-agent installer |
| `README.md` | Usage docs + agent support table |

---

### Task 1: Starter skill — `save-tokens`

**Files:**
- Create: `skills/save-tokens/SKILL.md`

- [ ] **Step 1: Create the skills directory and SKILL.md**

```bash
mkdir -p skills/save-tokens
```

Create `skills/save-tokens/SKILL.md`:

```markdown
---
name: save-tokens
description: >
  Ultra-compressed response mode. Cuts token usage by dropping articles, filler,
  pleasantries, and hedging. Uses symbols for relationships. Technical terms and
  code blocks remain exact and uncompressed.
  Use when user says "save tokens", "RTU mode", "compress", or "be brief".
---

## Rules

- Drop articles, filler, pleasantries, hedging, and transitional phrases
- Use symbols: `->` leads-to, `<-` triggered-by, `=>` returns, `~` approx, `∵` because, `∴` therefore, `|` or, `!` not
- Prefer short words: big/fix/use over extensive/implement/utilize
- Fragments are acceptable. Technical terms and code blocks remain exact and uncompressed.

## Example

Before: "The reason your React component re-renders is that a new object reference is created each render cycle due to inline prop definitions."  
After:  "Inline prop -> new obj ref each render -> re-render. Shallow compare != same ref. Fix: useMemo."

## Boundaries

- Code blocks, commits, and PRs: write normally
- Type "stop" or "normal mode" to exit and revert to standard responses
```

- [ ] **Step 2: Verify file exists and front-matter is valid**

```bash
head -10 skills/save-tokens/SKILL.md
```

Expected output starts with `---` and includes `name: save-tokens`.

---

### Task 2: Claude Code plugin config

**Files:**
- Create: `.claude-plugin/marketplace.json`
- Create: `.claude-plugin/plugin.json`

- [ ] **Step 1: Create the `.claude-plugin` directory**

```bash
mkdir -p .claude-plugin
```

- [ ] **Step 2: Create `marketplace.json`**

Create `.claude-plugin/marketplace.json`:

```json
{
  "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
  "name": "ai-skill-collections",
  "description": "A personal collection of AI skills — token-saving modes, workflow helpers, and productivity boosters for any AI coding agent.",
  "owner": {
    "name": "Chanakya",
    "url": "https://github.com/chanakya-net"
  },
  "plugins": [
    {
      "name": "ai-skill-collections",
      "description": "Token-saving modes, workflow helpers, and productivity boosters.",
      "source": "./",
      "category": "productivity"
    }
  ]
}
```

- [ ] **Step 3: Create `plugin.json`**

Create `.claude-plugin/plugin.json`:

```json
{
  "name": "ai-skill-collections",
  "description": "A personal collection of AI skills — token-saving modes, workflow helpers, and productivity boosters.",
  "author": {
    "name": "Chanakya",
    "url": "https://github.com/chanakya-net"
  }
}
```

- [ ] **Step 4: Verify both files are valid JSON**

```bash
python3 -c "import json; json.load(open('.claude-plugin/marketplace.json')); print('marketplace.json OK')"
python3 -c "import json; json.load(open('.claude-plugin/plugin.json')); print('plugin.json OK')"
```

Expected: both print `OK`.

---

### Task 3: Gemini CLI extension config

**Files:**
- Create: `gemini-extension.json`

- [ ] **Step 1: Create `gemini-extension.json`**

Create `gemini-extension.json`:

```json
{
  "name": "ai-skill-collections",
  "description": "A personal collection of AI skills — token-saving modes, workflow helpers, and productivity boosters.",
  "version": "1.0.0"
}
```

- [ ] **Step 2: Verify valid JSON**

```bash
python3 -c "import json; json.load(open('gemini-extension.json')); print('gemini-extension.json OK')"
```

Expected: `gemini-extension.json OK`

---

### Task 4: `install.sh` — smart multi-agent installer

**Files:**
- Create: `install.sh`

- [ ] **Step 1: Create `install.sh`**

Create `install.sh` with the following content:

```bash
#!/usr/bin/env bash
# ai-skill-collections — smart multi-agent installer
#
# One line:
#   curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.sh | bash
#
# Detects which AI coding agents are on your machine and installs the skills
# for each one. Skips agents that aren't installed. Safe to re-run.

set -euo pipefail

REPO="chanakya-net/AI-Skills"

# ── Flags ──────────────────────────────────────────────────────────────────
DRY=0
FORCE=0
LIST_ONLY=0
NO_COLOR=0
ONLY=()

# ── Result trackers ────────────────────────────────────────────────────────
INSTALLED=()
SKIPPED=()
FAILED=()

# ── Color setup ────────────────────────────────────────────────────────────
if [ ! -t 1 ]; then NO_COLOR=1; fi
c_green=""; c_yellow=""; c_red=""; c_dim=""; c_reset=""
if [ "$NO_COLOR" = 0 ]; then
  c_green=$'\033[0;32m'
  c_yellow=$'\033[0;33m'
  c_red=$'\033[0;31m'
  c_dim=$'\033[2m'
  c_reset=$'\033[0m'
fi

say()  { echo "${c_green}$*${c_reset}"; }
warn() { echo "${c_yellow}$*${c_reset}"; }
err()  { echo "${c_red}$*${c_reset}" >&2; }
note() { echo "${c_dim}$*${c_reset}"; }

# ── Help ───────────────────────────────────────────────────────────────────
print_help() {
  cat <<'EOF'
ai-skill-collections installer

USAGE
  install.sh [flags]
  curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.sh | bash

FLAGS
  --dry-run         Print what would run, do nothing.
  --force           Re-run even for already-installed agents.
  --only <agent>    Install only for the named agent. Repeatable.
  --list            Print the agent support matrix and exit.
  --no-color        Disable ANSI color codes.
  -h, --help        Show this help and exit.

SUPPORTED AGENTS
  Native:
    claude       Claude Code            claude plugin install
    gemini       Gemini CLI             gemini extensions install
  Via npx skills add:
    codex        Codex CLI
    copilot      GitHub Copilot CLI + VS Code
    cursor       Cursor IDE
    windsurf     Windsurf IDE
    cline        Cline (VS Code)
    roo          Roo Code (VS Code)
    continue     Continue (VS Code)
    opencode     opencode CLI
    junie        JetBrains Junie / Rider
    amp          Sourcegraph Amp
    kilo         Kilo Code
    augment      Augment Code
    goose        Block Goose
    warp         Warp
    devin        Devin
    openhands    OpenHands
    trae         Trae
    qwen         Qwen Code
    rovodev      Atlassian Rovo Dev
    bob          IBM Bob
    forgecode    ForgeCode
    mistral      Mistral Vibe
    tabnine      Tabnine CLI
    replit       Replit Agent

EXAMPLES
  install.sh                        # auto-detect all agents
  install.sh --only claude          # Claude Code only
  install.sh --only copilot --only cursor
  install.sh --dry-run
  install.sh --list
EOF
}

# ── Argument parsing ────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)   DRY=1 ;;
    --force)     FORCE=1 ;;
    --list)      LIST_ONLY=1 ;;
    --no-color)  NO_COLOR=1 ;;
    --only)
      shift
      [ $# -eq 0 ] && { err "error: --only requires an argument"; exit 2; }
      ONLY+=("$1") ;;
    -h|--help)   print_help; exit 0 ;;
    *) err "error: unknown flag: $1"; echo "run 'install.sh --help' for usage" >&2; exit 2 ;;
  esac
  shift
done

if [ "$LIST_ONLY" = 1 ]; then print_help; exit 0; fi

# ── Helpers ─────────────────────────────────────────────────────────────────
only_filter() {
  local id="$1"
  [ ${#ONLY[@]} -eq 0 ] && return 0
  for o in "${ONLY[@]}"; do [ "$o" = "$id" ] && return 0; done
  return 1
}

try() {
  if [ "$DRY" = 1 ]; then
    note "  [dry-run] $*"
    return 0
  fi
  "$@"
}

ensure_node() {
  command -v node >/dev/null 2>&1 && return 0
  warn "  node/npx not found — skipping (install Node.js from https://nodejs.org)"
  return 1
}

# ── Native: Claude Code ─────────────────────────────────────────────────────
install_claude() {
  only_filter "claude" || return 0
  command -v claude >/dev/null 2>&1 || return 0
  say "→ Claude Code detected"
  if try claude plugin install "github:$REPO"; then
    INSTALLED+=("claude")
  else
    FAILED+=("claude")
    err "  claude plugin install failed"
  fi
  echo
}

# ── Native: Gemini CLI ──────────────────────────────────────────────────────
install_gemini() {
  only_filter "gemini" || return 0
  command -v gemini >/dev/null 2>&1 || return 0
  say "→ Gemini CLI detected"
  if try gemini extensions install "github.com/$REPO"; then
    INSTALLED+=("gemini")
  else
    FAILED+=("gemini")
    err "  gemini extensions install failed"
  fi
  echo
}

# ── Generic: npx skills add ─────────────────────────────────────────────────
# Usage: install_via_skills <id> <label> <detect_expr> <profile>
# detect_expr: "cmd:<name>" to check command, "dir:<path>" to check directory
install_via_skills() {
  local id="$1"
  local label="$2"
  local detect="$3"
  local profile="$4"

  only_filter "$id" || return 0

  # Detection
  local detected=0
  if [[ "$detect" == cmd:* ]]; then
    command -v "${detect#cmd:}" >/dev/null 2>&1 && detected=1
  elif [[ "$detect" == dir:* ]]; then
    [ -d "${detect#dir:}" ] && detected=1
  fi
  [ "$detected" = 0 ] && return 0

  say "→ $label detected"
  ensure_node || { SKIPPED+=("$id"); echo; return 0; }

  if try npx -y skills add "$REPO" -a "$profile"; then
    INSTALLED+=("$id")
  else
    FAILED+=("$id")
    err "  npx skills add failed (profile: $profile)"
  fi
  echo
}

# ── Run installs ─────────────────────────────────────────────────────────────
install_claude
install_gemini

install_via_skills "codex"     "Codex CLI"              "cmd:codex"                                   "codex"
install_via_skills "copilot"   "GitHub Copilot"         "cmd:gh"                                      "github-copilot"
install_via_skills "cursor"    "Cursor IDE"             "dir:$HOME/.cursor"                           "cursor"
install_via_skills "windsurf"  "Windsurf"               "dir:$HOME/.codeium/windsurf"                 "windsurf"
install_via_skills "cline"     "Cline"                  "dir:$HOME/.cline"                            "cline"
install_via_skills "roo"       "Roo Code"               "dir:$HOME/.roo-cline"                        "roo"
install_via_skills "continue"  "Continue"               "dir:$HOME/.continue"                         "continue"
install_via_skills "opencode"  "opencode"               "cmd:opencode"                                "opencode"
install_via_skills "junie"     "JetBrains Junie"        "dir:$HOME/.junie"                            "junie"
install_via_skills "amp"       "Sourcegraph Amp"        "cmd:amp"                                     "amp"
install_via_skills "kilo"      "Kilo Code"              "dir:$HOME/.kilo"                             "kilo"
install_via_skills "augment"   "Augment Code"           "dir:$HOME/.augment"                          "augment"
install_via_skills "goose"     "Block Goose"            "cmd:goose"                                   "goose"
install_via_skills "warp"      "Warp"                   "dir:$HOME/.warp"                             "warp"
install_via_skills "devin"     "Devin"                  "cmd:devin"                                   "devin"
install_via_skills "openhands" "OpenHands"              "cmd:openhands"                               "openhands"
install_via_skills "trae"      "Trae"                   "cmd:trae"                                    "trae"
install_via_skills "qwen"      "Qwen Code"              "cmd:qwen-code"                               "qwen-code"
install_via_skills "rovodev"   "Atlassian Rovo Dev"     "cmd:rovo"                                    "rovodev"
install_via_skills "bob"       "IBM Bob"                "cmd:bob"                                     "bob"
install_via_skills "forgecode" "ForgeCode"              "cmd:forgecode"                               "forgecode"
install_via_skills "mistral"   "Mistral Vibe"           "cmd:mistral"                                 "mistral-vibe"
install_via_skills "tabnine"   "Tabnine CLI"            "cmd:tabnine"                                 "tabnine-cli"
install_via_skills "replit"    "Replit Agent"           "cmd:replit"                                  "replit"

# ── Summary ──────────────────────────────────────────────────────────────────
echo "────────────────────────────────────"
if [ ${#INSTALLED[@]} -gt 0 ]; then
  say "✓ Installed: ${INSTALLED[*]}"
fi
if [ ${#SKIPPED[@]} -gt 0 ]; then
  warn "⊘ Skipped (missing dep): ${SKIPPED[*]}"
fi
if [ ${#FAILED[@]} -gt 0 ]; then
  err "✗ Failed: ${FAILED[*]}"
fi
if [ ${#INSTALLED[@]} -eq 0 ] && [ ${#FAILED[@]} -eq 0 ] && [ ${#SKIPPED[@]} -eq 0 ]; then
  warn "No supported agents detected."
  note "Run with --only <agent> to force install for a specific agent."
  note "Supported agents: claude gemini codex copilot cursor windsurf cline roo continue opencode junie amp"
fi
echo "────────────────────────────────────"
```

- [ ] **Step 2: Make install.sh executable**

```bash
chmod +x install.sh
```

- [ ] **Step 3: Verify the script is valid bash and help flag works**

```bash
bash -n install.sh && echo "Syntax OK"
bash install.sh --help
```

Expected: `Syntax OK` followed by the help text.

- [ ] **Step 4: Dry-run test**

```bash
bash install.sh --dry-run
```

Expected: script runs, prints detected agents (if any), all commands are prefixed with `[dry-run]`.

---

### Task 5: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Create `README.md`**

Create `README.md`:

````markdown
# AI-Skills

A personal collection of AI skills that install across 30+ AI coding agents with a single command.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.sh | bash
```

The installer auto-detects which agents you have and installs the skills for each one.

### Install for a specific agent

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.sh) --only claude
bash <(curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.sh) --only copilot
bash <(curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.sh) --only cursor
```

### Manual install (Claude Code)

```bash
claude plugin install github:chanakya-net/AI-Skills
```

### Manual install (Gemini CLI)

```bash
gemini extensions install github.com/chanakya-net/AI-Skills
```

### Manual install (all other agents)

```bash
npx -y skills add chanakya-net/AI-Skills
```

---

## Skills

| Skill | Description | Trigger |
|-------|-------------|---------|
| `save-tokens` | Ultra-compressed responses — drops filler, uses symbols, keeps technical accuracy | "save tokens", "RTU mode", "compress", "be brief" |

---

## Supported Agents

| Agent | Method |
|-------|--------|
| Claude Code | `claude plugin install` |
| Gemini CLI | `gemini extensions install` |
| GitHub Copilot (CLI + VS Code) | `npx skills add` |
| Cursor IDE | `npx skills add` |
| Codex CLI | `npx skills add` |
| Windsurf | `npx skills add` |
| Cline (VS Code) | `npx skills add` |
| Roo Code | `npx skills add` |
| Continue (VS Code) | `npx skills add` |
| opencode | `npx skills add` |
| JetBrains Junie / Rider | `npx skills add` |
| Sourcegraph Amp | `npx skills add` |
| Kilo Code | `npx skills add` |
| Augment Code | `npx skills add` |
| Block Goose | `npx skills add` |
| Warp | `npx skills add` |
| Devin | `npx skills add` |
| OpenHands | `npx skills add` |
| Trae | `npx skills add` |
| Qwen Code | `npx skills add` |
| Atlassian Rovo Dev | `npx skills add` |
| Mistral Vibe | `npx skills add` |
| Tabnine CLI | `npx skills add` |
| Replit Agent | `npx skills add` |

---

## Adding a New Skill

1. Create a directory under `skills/`:

```
skills/
└── my-skill/
    └── SKILL.md
```

2. Add YAML front-matter and skill content to `SKILL.md`:

```markdown
---
name: my-skill
description: >
  What this skill does and when to trigger it.
---

## Skill content here
```

3. Commit and push. The next `npx skills add` will pick it up automatically.

---

## License

MIT
````

- [ ] **Step 2: Verify file exists**

```bash
wc -l README.md
```

Expected: `> 50` lines.

---

### Task 6: Final check

- [ ] **Step 1: Verify repo structure**

```bash
find . -not -path './.git/*' -not -path './docs/*' | sort
```

Expected output:

```
.
./LICENSE
./.claude-plugin
./.claude-plugin/marketplace.json
./.claude-plugin/plugin.json
./gemini-extension.json
./install.sh
./README.md
./skills
./skills/save-tokens
./skills/save-tokens/SKILL.md
```

- [ ] **Step 2: Validate all JSON files**

```bash
for f in .claude-plugin/marketplace.json .claude-plugin/plugin.json gemini-extension.json; do
  python3 -c "import json; json.load(open('$f'))" && echo "$f OK"
done
```

Expected: all three print `OK`.

- [ ] **Step 3: Validate install.sh syntax**

```bash
bash -n install.sh && echo "install.sh syntax OK"
```

Expected: `install.sh syntax OK`.

- [ ] **Step 4: Verify SKILL.md front-matter**

```bash
head -8 skills/save-tokens/SKILL.md
```

Expected: starts with `---`, contains `name: save-tokens` and `description:`.
