#!/usr/bin/env bash
# ai-skill-collections — full uninstaller
#
# One line (macOS / Linux / Git Bash):
#   curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/uninstall.sh | bash
#
# Removes agent registrations and shared assets so the next install starts clean.

set -euo pipefail

REPO="chanakya-net/AI-Skills"
ASSETS_DEST="${ASSETS_DEST:-$HOME/.ai-skill-collections/assets}"
DEFAULT_ASSETS_ROOT="$HOME/.ai-skill-collections"
SKILL_NAMES=("break-req" "create-git-issue" "run-with-it" "save-tokens" "tdd-implementation")
SKILL_ROOTS=("$HOME/.agents/skills" "$HOME/.codex/skills" "$HOME/.Codex/skills")

DRY=0
LIST_ONLY=0
NO_COLOR=0
ONLY=()

REMOVED=()
SKIPPED=()
FAILED=()
WOULD_REMOVE=()

print_help() {
  cat <<'EOF'
ai-skill-collections uninstaller

USAGE
  uninstall.sh [flags]
  curl -fsSL https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/uninstall.sh | bash

FLAGS
  --dry-run         Print what would run, do nothing.
  --only <target>   Remove only the named target. Repeatable.
                    Targets: assets, skills, claude, gemini, codex, copilot, antigravity
  --list            Print supported uninstall targets and exit.
  --no-color        Disable ANSI color codes.
  -h, --help        Show this help and exit.

ENVIRONMENT
  ASSETS_DEST       Shared assets directory to remove.
                    Default: ~/.ai-skill-collections/assets

EXAMPLES
  uninstall.sh                         # remove all detected installs + assets
  uninstall.sh --only skills           # remove installed skill directories only
  uninstall.sh --only codex            # remove Codex skill registration only
  uninstall.sh --only assets           # delete shared assets only
  uninstall.sh --dry-run
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)  DRY=1 ;;
    --list)     LIST_ONLY=1 ;;
    --no-color) NO_COLOR=1 ;;
    --only)
      shift
      [ $# -eq 0 ] && { echo "error: --only requires an argument" >&2; exit 2; }
      ONLY+=("$1") ;;
    -h|--help)  print_help; exit 0 ;;
    *) echo "error: unknown flag: $1" >&2; echo "run 'uninstall.sh --help' for usage" >&2; exit 2 ;;
  esac
  shift
done

if [ "$LIST_ONLY" = 1 ]; then print_help; exit 0; fi

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

remove_assets() {
  only_filter "assets" || return 0
  say "→ Removing shared assets"

  local target="$ASSETS_DEST"
  if [ "$ASSETS_DEST" = "$DEFAULT_ASSETS_ROOT/assets" ]; then
    target="$DEFAULT_ASSETS_ROOT"
  fi

  if [ "$DRY" = 1 ]; then
    note "  [dry-run] rm -rf $target"
    WOULD_REMOVE+=("assets")
    echo
    return 0
  fi

  rm -rf "$target"
  REMOVED+=("assets")
  note "  removed: $target"
  echo
}

remove_skill_dirs() {
  only_filter "skills" || return 0
  say "→ Removing installed skill directories"

  local found=0
  local root skill dir
  for root in "${SKILL_ROOTS[@]}"; do
    for skill in "${SKILL_NAMES[@]}"; do
      dir="$root/$skill"
      [ -d "$dir" ] || continue
      found=1
      if [ "$DRY" = 1 ]; then
        note "  [dry-run] rm -rf $dir"
      else
        rm -rf "$dir"
        note "  removed: $dir"
      fi
    done
  done

  [ "$found" = 0 ] && return 0
  [ "$DRY" = 1 ] && WOULD_REMOVE+=("skills") || REMOVED+=("skills")
  echo
}

remove_claude() {
  only_filter "claude" || return 0
  command -v claude >/dev/null 2>&1 || return 0
  say "→ Claude Code detected"
  if [ "$DRY" = 1 ]; then
    note "  [dry-run] claude plugin uninstall ai-skill-collections"
    WOULD_REMOVE+=("claude")
  elif output=$(claude plugin uninstall ai-skill-collections 2>&1); then
    echo "$output"
    REMOVED+=("claude")
  elif echo "$output" | grep -qi "not found"; then
    echo "$output"
    SKIPPED+=("claude")
    note "  Claude plugin already absent"
  else
    echo "$output"
    FAILED+=("claude")
    err "  claude plugin uninstall failed"
  fi
  echo
}

remove_gemini() {
  only_filter "gemini" || return 0
  command -v gemini >/dev/null 2>&1 || return 0
  say "→ Gemini CLI detected"
  if [ "$DRY" = 1 ]; then
    note "  [dry-run] gemini extensions uninstall https://github.com/$REPO"
    WOULD_REMOVE+=("gemini")
  elif output=$(gemini extensions uninstall "https://github.com/$REPO" 2>&1); then
    echo "$output"
    REMOVED+=("gemini")
  elif echo "$output" | grep -qi "not found"; then
    echo "$output"
    SKIPPED+=("gemini")
    note "  Gemini extension already absent"
  else
    echo "$output"
    FAILED+=("gemini")
    err "  gemini extensions uninstall failed"
  fi
  echo
}

npx_target_selected() {
  only_filter "codex" || only_filter "copilot" || only_filter "antigravity"
}

npx_target_detected() {
  command -v codex >/dev/null 2>&1 && only_filter "codex" && return 0
  command -v gh >/dev/null 2>&1 && only_filter "copilot" && return 0
  [ -d "$HOME/.antigravity" ] && only_filter "antigravity" && return 0
  return 1
}

remove_via_skills() {
  npx_target_selected || return 0
  npx_target_detected || return 0

  say "→ npx-managed skills detected"
  if ! command -v node >/dev/null 2>&1; then
    SKIPPED+=("npx-managed")
    warn "  node/npx not found — skipping"
    echo
    return 0
  fi

  if try npx -y skills remove "$REPO" --global; then
    [ "$DRY" = 1 ] && WOULD_REMOVE+=("npx-managed") || REMOVED+=("npx-managed")
  else
    FAILED+=("npx-managed")
    err "  npx skills remove failed"
  fi
  echo
}

remove_assets
remove_skill_dirs
remove_claude
remove_gemini
remove_via_skills

echo "────────────────────────────────────"
if [ ${#REMOVED[@]} -gt 0 ]; then
  say "✓ Removed: ${REMOVED[*]}"
fi
if [ ${#WOULD_REMOVE[@]} -gt 0 ]; then
  note "~ Would remove (dry-run): ${WOULD_REMOVE[*]}"
fi
if [ ${#SKIPPED[@]} -gt 0 ]; then
  warn "⊘ Skipped (missing dep): ${SKIPPED[*]}"
fi
if [ ${#FAILED[@]} -gt 0 ]; then
  err "✗ Failed: ${FAILED[*]}"
fi
if [ ${#REMOVED[@]} -eq 0 ] && [ ${#FAILED[@]} -eq 0 ] && [ ${#SKIPPED[@]} -eq 0 ] && [ ${#WOULD_REMOVE[@]} -eq 0 ]; then
  if [ "${#ONLY[@]}" -gt 0 ]; then
    warn "None of the specified targets were detected on this machine."
  else
    warn "No supported agent installs detected."
  fi
  note "Shared assets are always removed unless filtered out with --only."
fi
echo "────────────────────────────────────"

[ "${#FAILED[@]}" -gt 0 ] && exit 1
exit 0
