# ai-skill-collections — full uninstaller for Windows
#
# One line:
#   irm https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/uninstall.ps1 | iex
#
# Removes agent registrations and shared assets so the next install starts clean.

[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$List,
    [switch]$NoColor,
    [string[]]$Only = @(),
    [switch]$Help
)

$REPO = "chanakya-net/AI-Skills"
$ASSETS_DEST = if ($env:ASSETS_DEST) { $env:ASSETS_DEST } else { "$env:USERPROFILE\.ai-skill-collections\assets" }
$DEFAULT_ASSETS_ROOT = "$env:USERPROFILE\.ai-skill-collections"
$SKILL_NAMES = @("break-req", "create-git-issue", "help-me-debug", "run-with-it", "save-tokens", "tdd-implementation")
$SKILL_ROOTS = @("$env:USERPROFILE\.agents\skills", "$env:USERPROFILE\.codex\skills", "$env:USERPROFILE\.Codex\skills")

$REMOVED = [System.Collections.Generic.List[string]]::new()
$SKIPPED = [System.Collections.Generic.List[string]]::new()
$FAILED = [System.Collections.Generic.List[string]]::new()
$WOULD_REMOVE = [System.Collections.Generic.List[string]]::new()

$HELP_TEXT = @'
ai-skill-collections uninstaller (Windows)

USAGE
  uninstall.ps1 [flags]
  irm https://raw.githubusercontent.com/chanakya-net/AI-Skills/main/uninstall.ps1 | iex

FLAGS
  -DryRun           Print what would run, do nothing.
  -Only <target>    Remove only the named target. Repeatable: -Only assets -Only codex
                    Targets: assets, skills, claude, gemini, codex, copilot, antigravity, agy
  -List             Print supported uninstall targets and exit.
  -NoColor          Disable ANSI color codes.
  -Help             Show this help and exit.

ENVIRONMENT
  ASSETS_DEST       Shared assets directory to remove.
                    Default: %USERPROFILE%\.ai-skill-collections\assets

EXAMPLES
  uninstall.ps1                         # remove all detected installs + assets
  uninstall.ps1 -Only skills            # remove installed skill directories only
  uninstall.ps1 -Only codex             # remove Codex skill registration only
  uninstall.ps1 -Only assets            # delete shared assets only
  uninstall.ps1 -DryRun
'@

if ($Help -or $List) { Write-Host $HELP_TEXT; exit 0 }

$useColor = -not $NoColor -and $Host.UI.SupportsVirtualTerminal

function Say  { param($msg) if ($useColor) { Write-Host "`e[0;32m$msg`e[0m" } else { Write-Host $msg } }
function Warn { param($msg) if ($useColor) { Write-Host "`e[0;33m$msg`e[0m" } else { Write-Host $msg } }
function Err  { param($msg) $line = if ($useColor) { "`e[0;31m$msg`e[0m" } else { $msg }; [Console]::Error.WriteLine($line) }
function Note { param($msg) if ($useColor) { Write-Host "`e[2m$msg`e[0m" } else { Write-Host $msg } }

function Only-Filter {
    param([string]$id)
    if ($Only.Count -eq 0) { return $true }
    return $Only -contains $id
}

function Has-Command {
    param([string]$name)
    return $null -ne (Get-Command $name -ErrorAction SilentlyContinue)
}

function Try-Run {
    param([scriptblock]$block, [string]$description)
    if ($DryRun) { Note "  [dry-run] $description"; return $true }
    try { & $block; return $true }
    catch { return $false }
}

function Remove-Assets {
    if (-not (Only-Filter "assets")) { return }
    Say "→ Removing shared assets"

    $target = $ASSETS_DEST
    if ($ASSETS_DEST -eq "$DEFAULT_ASSETS_ROOT\assets") {
        $target = $DEFAULT_ASSETS_ROOT
    }

    if ($DryRun) {
        Note "  [dry-run] Remove-Item -Recurse -Force '$target'"
        $WOULD_REMOVE.Add("assets")
        Write-Host ""
        return
    }

    Remove-Item -Recurse -Force $target -ErrorAction SilentlyContinue
    $REMOVED.Add("assets")
    Note "  removed: $target"
    Write-Host ""
}

function Remove-SkillDirs {
    if (-not (Only-Filter "skills")) { return }
    Say "→ Removing installed skill directories"

    $found = $false
    foreach ($root in $SKILL_ROOTS) {
        foreach ($skill in $SKILL_NAMES) {
            $dir = Join-Path $root $skill
            if (-not (Test-Path $dir)) { continue }
            $found = $true
            if ($DryRun) {
                Note "  [dry-run] Remove-Item -Recurse -Force '$dir'"
            } else {
                Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
                Note "  removed: $dir"
            }
        }
    }

    if (-not $found) { return }
    if ($DryRun) { $WOULD_REMOVE.Add("skills") } else { $REMOVED.Add("skills") }
    Write-Host ""
}

function Remove-Claude {
    if (-not (Only-Filter "claude")) { return }
    if (-not (Has-Command "claude")) { return }
    Say "→ Claude Code detected"

    if ($DryRun) {
        Note "  [dry-run] claude plugin uninstall ai-skill-collections"
        $WOULD_REMOVE.Add("claude")
        Write-Host ""
        return
    }

    $output = & claude plugin uninstall ai-skill-collections 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host $output
        $REMOVED.Add("claude")
    } elseif (($output | Out-String) -match "not found") {
        Write-Host $output
        $SKIPPED.Add("claude")
        Note "  Claude plugin already absent"
    } else {
        Write-Host $output
        $FAILED.Add("claude")
        Err "  claude plugin uninstall failed"
    }
    Write-Host ""
}

function Remove-Gemini {
    if (-not (Only-Filter "gemini")) { return }
    if (-not (Has-Command "gemini")) { return }
    Say "→ Gemini CLI detected"

    if ($DryRun) {
        Note "  [dry-run] gemini extensions uninstall https://github.com/$REPO"
        $WOULD_REMOVE.Add("gemini")
        Write-Host ""
        return
    }

    $output = & gemini extensions uninstall "https://github.com/$REPO" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host $output
        $REMOVED.Add("gemini")
    } elseif (($output | Out-String) -match "not found") {
        Write-Host $output
        $SKIPPED.Add("gemini")
        Note "  Gemini extension already absent"
    } else {
        Write-Host $output
        $FAILED.Add("gemini")
        Err "  gemini extensions uninstall failed"
    }
    Write-Host ""
}

function Npx-Target-Selected {
    return (Only-Filter "codex") -or (Only-Filter "copilot") -or (Only-Filter "antigravity") -or (Only-Filter "agy")
}

function Npx-Target-Detected {
    if ((Has-Command "codex") -and (Only-Filter "codex")) { return $true }
    if ((Has-Command "gh") -and (Only-Filter "copilot")) { return $true }
    if ((Test-Path "$env:USERPROFILE\.antigravity") -and (Only-Filter "antigravity")) { return $true }
    if ((Has-Command "agy") -and (Only-Filter "agy")) { return $true }
    return $false
}

function Remove-Via-Skills {
    if (-not (Npx-Target-Selected)) { return }
    if (-not (Npx-Target-Detected)) { return }

    Say "→ npx-managed skills detected"
    if (-not (Has-Command "node")) {
        $SKIPPED.Add("npx-managed")
        Warn "  node/npx not found — skipping"
        Write-Host ""
        return
    }

    $ok = Try-Run {
        & npx -y skills remove $REPO --global
    } "npx -y skills remove $REPO --global"

    if ($ok) {
        if ($DryRun) { $WOULD_REMOVE.Add("npx-managed") } else { $REMOVED.Add("npx-managed") }
    } else {
        $FAILED.Add("npx-managed")
        Err "  npx skills remove failed"
    }
    Write-Host ""
}

Remove-Assets
Remove-SkillDirs
Remove-Claude
Remove-Gemini
Remove-Via-Skills

Write-Host "────────────────────────────────────"
if ($REMOVED.Count -gt 0) { Say "✓ Removed: $($REMOVED -join ', ')" }
if ($WOULD_REMOVE.Count -gt 0) { Note "~ Would remove (dry-run): $($WOULD_REMOVE -join ', ')" }
if ($SKIPPED.Count -gt 0) { Warn "⊘ Skipped (missing dep): $($SKIPPED -join ', ')" }
if ($FAILED.Count -gt 0) { Err "✗ Failed: $($FAILED -join ', ')" }

if ($REMOVED.Count -eq 0 -and $FAILED.Count -eq 0 -and $SKIPPED.Count -eq 0 -and $WOULD_REMOVE.Count -eq 0) {
    if ($Only.Count -gt 0) {
        Warn "None of the specified targets were detected on this machine."
    } else {
        Warn "No supported agent installs detected."
    }
    Note "Shared assets are always removed unless filtered out with -Only."
}
Write-Host "────────────────────────────────────"

if ($FAILED.Count -gt 0) { exit 1 }
exit 0
