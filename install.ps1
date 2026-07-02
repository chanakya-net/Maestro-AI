# ai-skill-collections — smart multi-agent installer for Windows (v2)
#
# One line:
#   irm https://raw.githubusercontent.com/chanakya-net/Maestro-AI/main/install.ps1 | iex
#
# Detects which AI coding agents are on your machine and installs the skills
# for each one. Skips agents that aren't installed. Safe to re-run.

[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$List,
    [switch]$NoColor,
    [string[]]$Only = @(),
    [switch]$Help
)

$REPO         = "chanakya-net/Maestro-AI"
$ASSETS_REF   = if ($env:ASSETS_REF)  { $env:ASSETS_REF }  else { "main" }
$ASSETS_DEST  = if ($env:ASSETS_DEST) { $env:ASSETS_DEST } else { "$env:USERPROFILE\.ai-skill-collections\assets" }

$INSTALLED     = [System.Collections.Generic.List[string]]::new()
$SKIPPED       = [System.Collections.Generic.List[string]]::new()
$FAILED        = [System.Collections.Generic.List[string]]::new()
$WOULD_INSTALL = [System.Collections.Generic.List[string]]::new()

$HELP_TEXT = @'
ai-skill-collections installer (Windows)

USAGE
  install.ps1 [flags]
  irm https://raw.githubusercontent.com/chanakya-net/Maestro-AI/main/install.ps1 | iex

FLAGS
  -DryRun           Print what would run, do nothing.
  -Only <agent>     Install only for the named agent. Repeatable: -Only claude -Only gemini
  -List             Print the agent support matrix and exit.
  -NoColor          Disable ANSI color codes.
  -Help             Show this help and exit.

ENVIRONMENT
  ASSETS_DEST       Where shared assets are installed.
                    Default: %USERPROFILE%\.ai-skill-collections\assets
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
  install.ps1                        # auto-detect all agents
  install.ps1 -Only claude           # Claude Code only
  install.ps1 -Only copilot -Only codex
  install.ps1 -DryRun
  install.ps1 -List
'@

if ($Help -or $List) { Write-Host $HELP_TEXT; exit 0 }

# ── Color setup ──────────────────────────────────────────────────────────────
$useColor = -not $NoColor -and $Host.UI.SupportsVirtualTerminal

function Say  { param($msg) if ($useColor) { Write-Host "`e[0;32m$msg`e[0m" } else { Write-Host $msg } }
function Warn { param($msg) if ($useColor) { Write-Host "`e[0;33m$msg`e[0m" } else { Write-Host $msg } }
function Err  { param($msg) $line = if ($useColor) { "`e[0;31m$msg`e[0m" } else { $msg }; [Console]::Error.WriteLine($line) }
function Note { param($msg) if ($useColor) { Write-Host "`e[2m$msg`e[0m"    } else { Write-Host $msg } }

# ── Helpers ──────────────────────────────────────────────────────────────────
function Only-Filter {
    param([string]$id)
    if ($Only.Count -eq 0) { return $true }
    return $Only -contains $id
}

function Has-Command {
    param([string]$name)
    return $null -ne (Get-Command $name -ErrorAction SilentlyContinue)
}

function Ensure-Node {
    if (Has-Command "node") { return $true }
    Warn "  node/npx not found — skipping (install Node.js from https://nodejs.org)"
    return $false
}

function Try-Run {
    param([scriptblock]$block, [string]$description)
    if ($DryRun) { Note "  [dry-run] $description"; return $true }
    try { & $block; return $true }
    catch { return $false }
}

# ── Install shared assets ─────────────────────────────────────────────────────
function Install-Assets {
    Say "→ Installing shared assets"

    $files   = @("prompt.md", "sub-coordinator-prompt.md", "main-orchestrator-rules.md", "artifact-recovery-prompt.md", "merge-recovery-prompt.md", "complexity-prompt.md", "plan-prompt.md", "review-prompt.md", "modifier-prompt.md", "coordinator-rules.md", "run-with-it-state.py", "run-with-it-github-update.py", "run-with-it-pr-body.py", "run-with-it-router.py", "run-with-it-artifacts.py", "run-agent.ps1", "run-with-it-dispatch.ps1", "run-with-it-pool.ps1", "worker-watch.ps1", "agent-registry.json")
    $baseUrl = "https://raw.githubusercontent.com/$REPO/$ASSETS_REF/assets"

    if ($DryRun) {
        Note "  [dry-run] New-Item -ItemType Directory -Force '$ASSETS_DEST'"
        foreach ($f in $files) {
            Note "  [dry-run] Invoke-WebRequest $baseUrl/$f -OutFile $ASSETS_DEST\$f"
        }
        $WOULD_INSTALL.Add("assets")
        Write-Host ""
        return
    }

    New-Item -ItemType Directory -Force -Path $ASSETS_DEST | Out-Null

    foreach ($f in $files) {
        $url  = "$baseUrl/$f"
        $tmp  = "$ASSETS_DEST\$f.tmp"
        $dest = "$ASSETS_DEST\$f"
        try {
            Invoke-WebRequest -Uri $url -OutFile $tmp -ErrorAction Stop
            Move-Item -Force $tmp $dest
        }
        catch {
            Remove-Item -Force $tmp -ErrorAction SilentlyContinue
            $FAILED.Add("assets")
            Err "  failed to download $url"
            Write-Host ""
            return
        }
    }

    $INSTALLED.Add("assets")
    Note "  assets installed at: $ASSETS_DEST"
    Write-Host ""
}

# ── Native: Claude Code ───────────────────────────────────────────────────────
function Install-Claude {
    if (-not (Only-Filter "claude")) { return }
    if (-not (Has-Command "claude")) { return }
    Say "→ Claude Code detected"

    $ok = Try-Run {
        & claude plugin marketplace add $REPO
        & claude plugin install "ai-skill-collections@ai-skill-collections"
    } "claude plugin marketplace add $REPO && claude plugin install ai-skill-collections@ai-skill-collections"

    if ($ok) {
        if ($DryRun) { $WOULD_INSTALL.Add("claude") } else { $INSTALLED.Add("claude") }
    } else {
        $FAILED.Add("claude")
        Err "  claude plugin install failed"
    }
    Write-Host ""
}

# ── Native: Gemini CLI ────────────────────────────────────────────────────────
function Install-Gemini {
    if (-not (Only-Filter "gemini")) { return }
    if (-not (Has-Command "gemini")) { return }
    Say "→ Gemini CLI detected"

    $integrityFile = "$env:USERPROFILE\.gemini\extension_integrity.json"
    if (Test-Path $integrityFile) {
        try { $null = Get-Content $integrityFile -Raw | ConvertFrom-Json -ErrorAction Stop }
        catch {
            Note "  clearing corrupted Gemini integrity store"
            if (-not $DryRun) { Remove-Item -Force $integrityFile }
        }
    }

    if ($DryRun) {
        Note "  [dry-run] gemini extensions install --consent https://github.com/$REPO"
        $WOULD_INSTALL.Add("gemini")
        Write-Host ""
        return
    }

    try {
        $output = & gemini extensions install --consent "https://github.com/$REPO" 2>&1
        Write-Host $output
        $INSTALLED.Add("gemini")
    }
    catch {
        $output = $_.Exception.Message
        Write-Host $output
        if ($output -match "already installed") {
            Note "  Gemini extension already installed; continuing"
            $INSTALLED.Add("gemini")
        } else {
            $FAILED.Add("gemini")
            Err "  gemini extensions install failed"
        }
    }
    Write-Host ""
}

# ── Generic: npx skills add ──────────────────────────────────────────────────
function Install-Via-Skills {
    param(
        [string]$id,
        [string]$label,
        [string]$detect,
        [string]$profile
    )

    if (-not (Only-Filter $id)) { return }

    $detected = $false
    if ($detect.StartsWith("cmd:")) {
        $cmd = $detect.Substring(4)
        $detected = Has-Command $cmd
    } elseif ($detect.StartsWith("dir:")) {
        $dir = $detect.Substring(4)
        $detected = Test-Path $dir
    } else {
        Warn "  BUG: unknown detect_expr '$detect' for agent '$id'"
        return
    }

    if (-not $detected) { return }

    Say "→ $label detected"
    if (-not (Ensure-Node)) { $SKIPPED.Add($id); Write-Host ""; return }

    $ok = Try-Run {
        & npx -y skills add $REPO -a $profile --yes --global
    } "npx -y skills add $REPO -a $profile --yes --global"

    if ($ok) {
        if ($DryRun) { $WOULD_INSTALL.Add($id) } else { $INSTALLED.Add($id) }
    } else {
        $FAILED.Add($id)
        Err "  npx skills add failed (profile: $profile)"
    }
    Write-Host ""
}

# ── Run installs ──────────────────────────────────────────────────────────────
Install-Assets

Install-Claude
Install-Gemini

Install-Via-Skills "codex"       "Codex CLI + GUI"             "cmd:codex" "codex"
Install-Via-Skills "copilot"     "GitHub Copilot CLI + VS Code" "cmd:gh"   "github-copilot"
Install-Via-Skills "antigravity" "Gemini GUI (Antigravity)"    "dir:$env:USERPROFILE\.antigravity" "antigravity"
Install-Via-Skills "agy"         "Antigravity CLI (agy)"       "cmd:agy"   "agy"

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host "────────────────────────────────────"
if ($INSTALLED.Count   -gt 0) { Say  "✓ Installed: $($INSTALLED -join ', ')" }
if ($WOULD_INSTALL.Count -gt 0) { Note "~ Would install (dry-run): $($WOULD_INSTALL -join ', ')" }
if ($SKIPPED.Count     -gt 0) { Warn "⊘ Skipped (missing dep): $($SKIPPED -join ', ')" }
if ($FAILED.Count      -gt 0) { Err  "✗ Failed: $($FAILED -join ', ')" }

if ($INSTALLED.Count -eq 0 -and $FAILED.Count -eq 0 -and $SKIPPED.Count -eq 0 -and $WOULD_INSTALL.Count -eq 0) {
    if ($Only.Count -gt 0) {
        Warn "None of the specified agents were detected on this machine."
    } else {
        Warn "No supported agents detected."
    }
    Note "Run 'install.ps1 -List' to see all supported agents."
}
Write-Host "────────────────────────────────────"

if ($FAILED.Count -gt 0) { exit 1 }
exit 0
