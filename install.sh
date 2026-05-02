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

# ── Flags (declare defaults first) ─────────────────────────────────────────
DRY=0
LIST_ONLY=0
NO_COLOR=0
ONLY=()
WOULD_INSTALL=()

# ── Result trackers ────────────────────────────────────────────────────────
INSTALLED=()
SKIPPED=()
FAILED=()

# ── Help ───────────────────────────────────────────────────────────────────
print_help() {
  cat <<'EOF'
ai-skill-collections installer

USAGE
  install.sh [flags]
  curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.sh | bash

FLAGS
  --dry-run         Print what would run, do nothing.
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

# ── Argument parsing (BEFORE color init) ───────────────────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)   DRY=1 ;;
    --list)      LIST_ONLY=1 ;;
    --no-color)  NO_COLOR=1 ;;
    --only)
      shift
      [ $# -eq 0 ] && { echo "error: --only requires an argument" >&2; exit 2; }
      ONLY+=("$1") ;;
    -h|--help)   print_help; exit 0 ;;
    *) echo "error: unknown flag: $1" >&2; echo "run 'install.sh --help' for usage" >&2; exit 2 ;;
  esac
  shift
done

if [ "$LIST_ONLY" = 1 ]; then print_help; exit 0; fi

# ── Color setup (AFTER arg parsing, so --no-color works) ───────────────────
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

# ── Helpers ─────────────────────────────────────────────────────────────────
only_filter() {
  local id="$1"
  [ "${#ONLY[@]}" -eq 0 ] && return 0
  local o
  for o in "${ONLY[@]+"${ONLY[@]}"}"; do
    [ "$o" = "$id" ] && return 0
  done
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
  if try claude plugin marketplace add "$REPO" && try claude plugin install "ai-skill-collections@ai-skill-collections"; then
    [ "$DRY" = 1 ] && WOULD_INSTALL+=("claude") || INSTALLED+=("claude")
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
  if try gemini extensions install "https://github.com/$REPO"; then
    [ "$DRY" = 1 ] && WOULD_INSTALL+=("gemini") || INSTALLED+=("gemini")
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
  else
    warn "  BUG: unknown detect_expr '$detect' for agent '$id'"
    return 0
  fi
  [ "$detected" = 0 ] && return 0

  say "→ $label detected"
  ensure_node || { SKIPPED+=("$id"); echo; return 0; }

  if try npx -y skills add "$REPO" -a "$profile" --yes --global; then
    [ "$DRY" = 1 ] && WOULD_INSTALL+=("$id") || INSTALLED+=("$id")
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
if [ ${#WOULD_INSTALL[@]} -gt 0 ]; then
  note "~ Would install (dry-run): ${WOULD_INSTALL[*]}"
fi
if [ ${#SKIPPED[@]} -gt 0 ]; then
  warn "⊘ Skipped (missing dep): ${SKIPPED[*]}"
fi
if [ ${#FAILED[@]} -gt 0 ]; then
  err "✗ Failed: ${FAILED[*]}"
fi
if [ ${#INSTALLED[@]} -eq 0 ] && [ ${#FAILED[@]} -eq 0 ] && [ ${#SKIPPED[@]} -eq 0 ] && [ ${#WOULD_INSTALL[@]} -eq 0 ]; then
  if [ "${#ONLY[@]}" -gt 0 ]; then
    warn "None of the specified agents were detected on this machine."
  else
    warn "No supported agents detected."
  fi
  note "Run 'install.sh --list' to see all supported agents."
fi
echo "────────────────────────────────────"

[ "${#FAILED[@]}" -gt 0 ] && exit 1
exit 0
