# run-copilot.ps1 — Windows PowerShell runner for Copilot (mirrors run-copilot.sh)
#
# Usage:
#   run-copilot.ps1 <context-payload-file> [prompt-file]
#
# Environment variables:
#   COPY_PROMPT, PRINT_PROMPT, COPILOT_PERMISSION_MODE, COPILOT_EXTRA_ARGS,
#   MAX_ITERATIONS, STOP_MARKER, AGENT_NAME_PREFIX, HEARTBEAT_INTERVAL_SECONDS,
#   COLOR_OUTPUT, REPO_ROOT

param(
    [string]$ContextPayloadFile = $env:CONTEXT_PAYLOAD_FILE,
    [string]$PromptFile         = $env:PROMPT_FILE
)

$SCRIPT_DIR               = $PSScriptRoot
$REPO_ROOT                = if ($env:REPO_ROOT) { $env:REPO_ROOT } else { $PWD.Path }
$COPY_PROMPT              = if ($env:COPY_PROMPT -ne $null)              { $env:COPY_PROMPT }              else { "1" }
$PRINT_PROMPT             = if ($env:PRINT_PROMPT -ne $null)             { $env:PRINT_PROMPT }             else { "0" }
$COPILOT_PERMISSION_MODE  = if ($env:COPILOT_PERMISSION_MODE)            { $env:COPILOT_PERMISSION_MODE }  else { "--allow-all-tools" }
$COPILOT_EXTRA_ARGS       = if ($env:COPILOT_EXTRA_ARGS)                 { $env:COPILOT_EXTRA_ARGS }       else { "" }
$MAX_ITERATIONS           = if ($env:MAX_ITERATIONS)                     { [int]$env:MAX_ITERATIONS }      else { 20 }
$STOP_MARKER              = if ($env:STOP_MARKER)                        { $env:STOP_MARKER }              else { "<promise>NO MORE TASKS</promise>" }
$AGENT_NAME_PREFIX        = if ($env:AGENT_NAME_PREFIX)                  { $env:AGENT_NAME_PREFIX }        else { "copilot-agent" }
$HEARTBEAT_INTERVAL_SEC   = if ($env:HEARTBEAT_INTERVAL_SECONDS)         { [int]$env:HEARTBEAT_INTERVAL_SECONDS } else { 15 }
$COLOR_OUTPUT             = if ($env:COLOR_OUTPUT)                       { $env:COLOR_OUTPUT }             else { "auto" }

if (-not $PromptFile) { $PromptFile = Join-Path $SCRIPT_DIR "prompt.md" }

# ── Validation ────────────────────────────────────────────────────────────────
if (-not (Get-Command "copilot" -ErrorAction SilentlyContinue)) {
    Write-Error "Missing required command: copilot"
    exit 1
}
if ([string]::IsNullOrEmpty($ContextPayloadFile)) {
    Write-Error "Context payload file is required. Pass as arg1 or set CONTEXT_PAYLOAD_FILE."
    exit 1
}
if (-not (Test-Path $ContextPayloadFile)) {
    Write-Error "Context payload file not found: $ContextPayloadFile"
    exit 1
}
if (-not (Test-Path $PromptFile)) {
    Write-Error "Prompt file not found: $PromptFile"
    exit 1
}

# ── Color support ─────────────────────────────────────────────────────────────
function Should-Color {
    switch ($COLOR_OUTPUT) {
        "always" { return $true }
        "never"  { return $false }
        default  { return $Host.UI.SupportsVirtualTerminal }
    }
}

$PALETTE = @(
    [System.ConsoleColor]::Cyan,
    [System.ConsoleColor]::Green,
    [System.ConsoleColor]::Yellow,
    [System.ConsoleColor]::Magenta,
    [System.ConsoleColor]::DarkCyan,
    [System.ConsoleColor]::DarkYellow,
    [System.ConsoleColor]::Blue,
    [System.ConsoleColor]::DarkGreen
)
$agentColors = @{}
$nextPaletteIdx = 0

function Get-AgentColor {
    param([string]$agent)
    if (-not $agentColors.ContainsKey($agent)) {
        $agentColors[$agent] = $PALETTE[$script:nextPaletteIdx % $PALETTE.Count]
        $script:nextPaletteIdx++
    }
    return $agentColors[$agent]
}

function Write-ColorLine {
    param([string]$line)
    if (-not (Should-Color)) { Write-Host $line; return }

    if ($line -match '^\|agent=([^\|]+)') {
        $agent = $Matches[1]
        Write-Host $line -ForegroundColor (Get-AgentColor $agent)
    } elseif ($line -match '^\[(\d{2}:\d{2}:\d{2})\]' -or $line -match '^== ') {
        Write-Host $line -ForegroundColor Gray
    } else {
        Write-Host $line
    }
}

# ── Temp files + cleanup ──────────────────────────────────────────────────────
$PAYLOAD_FILE = [System.IO.Path]::GetTempFileName()
$OUTPUT_FILE  = [System.IO.Path]::GetTempFileName()

function Cleanup {
    Remove-Item -Force $PAYLOAD_FILE -ErrorAction SilentlyContinue
    Remove-Item -Force $OUTPUT_FILE  -ErrorAction SilentlyContinue
}

# ── Build payload ─────────────────────────────────────────────────────────────
function Build-Payload {
    $contextContent = Get-Content $ContextPayloadFile -Raw -Encoding UTF8
    $promptContent  = Get-Content $PromptFile         -Raw -Encoding UTF8
    "$contextContent`nInstructions:`n`n$promptContent`n" | Set-Content $PAYLOAD_FILE -Encoding UTF8 -NoNewline
}

# ── Clipboard ─────────────────────────────────────────────────────────────────
function Copy-ToClipboard {
    param([string]$file)
    try {
        Get-Content $file -Raw -Encoding UTF8 | Set-Clipboard
    } catch {
        # clipboard unavailable — silently skip
    }
}

# ── Run with heartbeat ────────────────────────────────────────────────────────
function Run-WithStatus {
    param([string]$runName, [string[]]$cmdArgs)

    Set-Content $OUTPUT_FILE "" -Encoding UTF8

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName               = "copilot"
    $startInfo.Arguments              = ($cmdArgs | ForEach-Object { "`"$_`"" }) -join " "
    $startInfo.UseShellExecute        = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError  = $true
    $startInfo.WorkingDirectory       = $REPO_ROOT

    $proc = [System.Diagnostics.Process]::new()
    $proc.StartInfo = $startInfo

    $proc.add_OutputDataReceived({
        param($sender, $e)
        if ($null -ne $e.Data) {
            Add-Content -Path $OUTPUT_FILE -Value $e.Data -Encoding UTF8
            Write-ColorLine $e.Data
        }
    })
    $proc.add_ErrorDataReceived({
        param($sender, $e)
        if ($null -ne $e.Data) {
            Add-Content -Path $OUTPUT_FILE -Value $e.Data -Encoding UTF8
            Write-Host $e.Data -ForegroundColor DarkYellow
        }
    })

    $proc.Start() | Out-Null
    $proc.BeginOutputReadLine()
    $proc.BeginErrorReadLine()

    $startedAt = [datetime]::UtcNow
    $ts = [datetime]::Now.ToString("HH:mm:ss")
    Write-Host "[$ts] ${runName}: started (pid=$($proc.Id))" -ForegroundColor Gray

    while (-not $proc.WaitForExit($HEARTBEAT_INTERVAL_SEC * 1000)) {
        $elapsed = [int](([datetime]::UtcNow - $startedAt).TotalSeconds)
        $lines   = (Get-Content $OUTPUT_FILE -ErrorAction SilentlyContinue | Measure-Object -Line).Lines
        $ts      = [datetime]::Now.ToString("HH:mm:ss")
        Write-Host "[$ts] ${runName}: running (elapsed=${elapsed}s, output_lines=$lines)" -ForegroundColor DarkGray
    }

    $exitCode     = $proc.ExitCode
    $finishedAt   = [datetime]::UtcNow
    $elapsedTotal = [int](($finishedAt - $startedAt).TotalSeconds)
    $ts           = [datetime]::Now.ToString("HH:mm:ss")
    Write-Host "[$ts] ${runName}: finished (exit=$exitCode, elapsed=${elapsedTotal}s)" -ForegroundColor Gray

    return $exitCode
}

# ── Main loop ─────────────────────────────────────────────────────────────────
try {
    Set-Location $REPO_ROOT

    for ($iteration = 1; $iteration -le $MAX_ITERATIONS; $iteration++) {
        $runName = "${AGENT_NAME_PREFIX}-iter-${iteration}"
        Write-Host "== Copilot iteration ${iteration}/${MAX_ITERATIONS} [$runName] ==" -ForegroundColor Gray

        Build-Payload

        if ($COPY_PROMPT -eq "1") { Copy-ToClipboard $PAYLOAD_FILE }

        if ($PRINT_PROMPT -eq "1") {
            Get-Content $PAYLOAD_FILE -Raw -Encoding UTF8
            exit 0
        }

        $payloadText = Get-Content $PAYLOAD_FILE -Raw -Encoding UTF8

        $cmdArgs = @($COPILOT_PERMISSION_MODE)
        if ($COPILOT_EXTRA_ARGS) { $cmdArgs += $COPILOT_EXTRA_ARGS }
        $cmdArgs += @("-p", $payloadText)

        $exitCode = Run-WithStatus $runName $cmdArgs

        $outputContent = Get-Content $OUTPUT_FILE -Raw -ErrorAction SilentlyContinue
        if ($outputContent -and $outputContent.Contains($STOP_MARKER)) {
            exit 0
        }
    }

    Write-Error "Reached MAX_ITERATIONS=$MAX_ITERATIONS without seeing $STOP_MARKER."
    exit 1
}
finally {
    Cleanup
}
