#!/usr/bin/env bash
# ai-skill-collections — smart multi-agent installer (v2)
#
# One line (macOS / Linux / Git Bash):
#   curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.sh | bash
#
# Windows (PowerShell) — use install.ps1 instead:
#   irm https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/install.ps1 | iex
#
# Detects which AI coding agents are on your machine and installs the skills
# for each one. Skips agents that aren't installed. Safe to re-run.

set -euo pipefail

REPO="chanakya-net/AI-Skills"
ASSETS_REF="${ASSETS_REF:-main}"
ASSETS_DEST="${ASSETS_DEST:-$HOME/.ai-skill-collections/assets}"

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

ENVIRONMENT
  ASSETS_DEST       Where shared assets are installed.
                    Default: ~/.ai-skill-collections/assets
  ASSETS_REF        Git ref used to download assets from GitHub.
                    Default: main

SUPPORTED AGENTS
  Native:
    claude       Claude Code CLI + App  claude plugin install
    gemini       Gemini CLI              gemini extensions install
  Via npx skills add:
    codex        Codex CLI + GUI
    copilot      GitHub Copilot CLI + VS Code
    antigravity  Gemini GUI (Antigravity)
    agy          Antigravity CLI (agy)

EXAMPLES
  install.sh                        # auto-detect all agents
  install.sh --only claude          # Claude Code only
  install.sh --only copilot --only codex
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

install_assets() {
  say "→ Installing shared assets"

  local files=("prompt.md" "sub-coordinator-prompt.md" "main-orchestrator-rules.md" "artifact-recovery-prompt.md" "merge-recovery-prompt.md" "complexity-prompt.md" "plan-prompt.md" "review-prompt.md" "modifier-prompt.md" "coordinator-rules.md" "run-with-it-state.py" "run-with-it-github-update.py" "run-with-it-pr-body.py" "run-with-it-router.py" "run-with-it-artifacts.py" "run-agent.sh" "run-with-it-dispatch.sh" "run-with-it-pool.sh" "worker-watch.sh" "agent-registry.json")
  local base_url="https://raw.githubusercontent.com/${REPO}/${ASSETS_REF}/assets"

  if [ "$DRY" = 1 ]; then
    note "  [dry-run] mkdir -p $ASSETS_DEST"
    local f
    for f in "${files[@]}"; do
      note "  [dry-run] curl -fsSL ${base_url}/${f} -o ${ASSETS_DEST}/${f}"
    done
    note "  [dry-run] chmod +x ${ASSETS_DEST}/run-agent.sh"
    note "  [dry-run] chmod +x ${ASSETS_DEST}/run-with-it-dispatch.sh"
    note "  [dry-run] chmod +x ${ASSETS_DEST}/run-with-it-pool.sh"
    note "  [dry-run] chmod +x ${ASSETS_DEST}/run-with-it-state.py"
    note "  [dry-run] chmod +x ${ASSETS_DEST}/run-with-it-github-update.py"
    note "  [dry-run] chmod +x ${ASSETS_DEST}/run-with-it-pr-body.py"
    note "  [dry-run] chmod +x ${ASSETS_DEST}/run-with-it-router.py"
    note "  [dry-run] chmod +x ${ASSETS_DEST}/run-with-it-artifacts.py"
    note "  [dry-run] chmod +x ${ASSETS_DEST}/worker-watch.sh"
    WOULD_INSTALL+=("assets")
    echo
    return 0
  fi

  if ! command -v curl >/dev/null 2>&1; then
    FAILED+=("assets")
    err "  curl not found; cannot download shared assets"
    echo
    return 1
  fi

  mkdir -p "$ASSETS_DEST"

  local f url tmp
  for f in "${files[@]}"; do
    url="${base_url}/${f}"
    tmp="${ASSETS_DEST}/${f}.tmp"

    if ! curl -fsSL "$url" -o "$tmp"; then
      rm -f "$tmp"
      FAILED+=("assets")
      err "  failed to download ${url}"
      echo
      return 1
    fi

    mv "$tmp" "${ASSETS_DEST}/${f}"
  done

  chmod +x "${ASSETS_DEST}/run-agent.sh"
  chmod +x "${ASSETS_DEST}/run-with-it-dispatch.sh"
  chmod +x "${ASSETS_DEST}/run-with-it-pool.sh"
  chmod +x "${ASSETS_DEST}/run-with-it-state.py"
  chmod +x "${ASSETS_DEST}/run-with-it-github-update.py"
  chmod +x "${ASSETS_DEST}/run-with-it-pr-body.py"
  chmod +x "${ASSETS_DEST}/run-with-it-router.py"
  chmod +x "${ASSETS_DEST}/run-with-it-artifacts.py"
  chmod +x "${ASSETS_DEST}/worker-watch.sh"

  INSTALLED+=("assets")
  note "  assets installed at: $ASSETS_DEST"
  echo
}

# ── Native: Claude Code ─────────────────────────────────────────────────────

# Idempotent: adds run-with-it allowlist entries to ~/.claude/settings.json
patch_claude_permissions() {
  local settings="$HOME/.claude/settings.json"

  if [ ! -f "$settings" ]; then
    note "  ~/.claude/settings.json not found; skipping permission patch"
    return 0
  fi

  if [ "$DRY" = 1 ]; then
    note "  [dry-run] add run-agent allowlist entries to $settings"
    return 0
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    warn "  python3 not found; skipping permission patch (add these manually to $settings):"
    note "    Bash(*run-agent.sh*)"
    note "    Bash(*run-with-it-dispatch.sh*)"
    note "    Bash(*run-with-it-pool.sh*)"
    note "    Bash(codex *)"
    note "    Bash(opencode *)"
    note "    Bash(gemini *)"
    return 0
  fi

  python3 - "$settings" <<'PY'
import json, sys

ENTRIES = [
    "Bash(*run-agent.sh*)",
    "Bash(*run-with-it-dispatch.sh*)",
    "Bash(*run-with-it-pool.sh*)",
    "Bash(codex *)",
    "Bash(opencode *)",
    "Bash(gemini *)",
]

path = sys.argv[1]
with open(path, "r") as f:
    cfg = json.load(f)

cfg.setdefault("permissions", {}).setdefault("allow", [])
allow = cfg["permissions"]["allow"]

added = [e for e in ENTRIES if e not in allow]
if added:
    allow.extend(added)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
    for e in added:
        print(f"    + {e}")
else:
    print("    permissions already configured")
PY
}

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
  note "  patching Claude Code permissions for run-with-it agents"
  patch_claude_permissions
  echo
}

# ── Native: Gemini CLI ──────────────────────────────────────────────────────
install_gemini() {
  only_filter "gemini" || return 0
  command -v gemini >/dev/null 2>&1 || return 0
  say "→ Gemini CLI detected"
  local gemini_output=""
  # Clear corrupted integrity store if present (causes install to abort)
  local integrity="$HOME/.gemini/extension_integrity.json"
  if [ -f "$integrity" ] && ! python3 -m json.tool "$integrity" >/dev/null 2>&1; then
    note "  clearing corrupted Gemini integrity store"
    [ "$DRY" = 0 ] && rm -f "$integrity"
  fi

  if [ "$DRY" = 1 ]; then
    note "  [dry-run] gemini extensions install --consent https://github.com/$REPO"
    WOULD_INSTALL+=("gemini")
    echo
    return 0
  fi

  if gemini_output=$(gemini extensions install --consent "https://github.com/$REPO" 2>&1); then
    echo "$gemini_output"
    INSTALLED+=("gemini")
  else
    echo "$gemini_output"
    if echo "$gemini_output" | grep -qi "already installed"; then
      note "  Gemini extension already installed; continuing"
      INSTALLED+=("gemini")
    else
      FAILED+=("gemini")
      err "  gemini extensions install failed"
    fi
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
install_assets || true

install_claude
install_gemini

install_via_skills "codex"     "Codex CLI + GUI"        "cmd:codex"                                   "codex"
install_via_skills "copilot"   "GitHub Copilot CLI + VS Code" "cmd:gh"                                "github-copilot"
install_via_skills "antigravity" "Gemini GUI (Antigravity)" "dir:$HOME/.antigravity"                     "antigravity"
install_via_skills "agy"         "Antigravity CLI (agy)"  "cmd:agy"                                     "agy"

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
