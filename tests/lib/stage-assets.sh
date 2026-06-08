#!/usr/bin/env bash
# Shared test helper.
#
# The repo keeps run-with-it assets in language subfolders
# (assets/prompts, assets/python, assets/shell, assets/powershell) with the
# shared agent-registry.json at the assets/ root. The *installer* flattens all
# of these into a single directory (~/.ai-skill-collections/assets), and the
# runtime scripts resolve their siblings from that one flat AssetRoot.
#
# stage_flat_assets reproduces that flat install layout from the source tree so
# tests can hand a runner a valid --asset-root without depending on a real
# install.
#
# Usage: stage_flat_assets <repo_assets_dir> <dest_dir>
stage_flat_assets() {
  local src="$1" dest="$2"
  mkdir -p "$dest"
  cp "$src"/agent-registry.json "$dest"/
  cp "$src"/prompts/*.md "$dest"/
  cp "$src"/python/*.py "$dest"/
  cp "$src"/shell/*.sh "$dest"/
  cp "$src"/powershell/*.ps1 "$dest"/
  chmod +x "$dest"/*.sh "$dest"/*.py 2>/dev/null || true
}
