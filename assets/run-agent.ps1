# run-agent.ps1 — Windows PowerShell unified agent runner (mirrors run-agent.sh)
#
# Usage:
#   run-agent.ps1 --agent <agent> [--model <model>] --context-file <file> --prompt-file <file> [--dry-run] [--unattended]
#   run-agent.ps1 --list-agents [--detected-only]
#   run-agent.ps1 --list-models <agent>
#
# Environment equivalents:
#   AGENT, MODEL, CONTEXT_PAYLOAD_FILE, PROMPT_FILE, PRINT_PROMPT,
#   AGENT_PERMISSION_MODE, AGENT_EXTRA_ARGS, AGENT_REGISTRY_FILE, UNATTENDED, GUI_MODE,
#   RUN_WITH_IT_STATUS_FILE, RUN_WITH_IT_EVENTS_LOG, RUN_WITH_IT_LOG_FILE, RUN_WITH_IT_DONE_FILE, RUN_WITH_IT_RESULT_FILE, RUN_WITH_IT_STATE_FILE

$ErrorActionPreference = "Stop"

$_bootstrapPath = if ($env:RUN_AGENT_BOOTSTRAP_PATH) { $env:RUN_AGENT_BOOTSTRAP_PATH } else { "1" }
if ($_bootstrapPath -ne "0") {
    foreach ($pathDir in @(
        "$env:USERPROFILE\.npm-global\bin",
        "$env:USERPROFILE\.local\bin",
        "$env:USERPROFILE\.cargo\bin",
        "$env:USERPROFILE\.bun\bin",
        "$env:USERPROFILE\.dotnet\tools"
    )) {
        if ($pathDir -and (Test-Path $pathDir) -and (($env:PATH -split ';') -notcontains $pathDir)) {
            $env:PATH = "$pathDir;$env:PATH"
        }
    }
}
Remove-Variable _bootstrapPath

$SCRIPT_DIR        = $PSScriptRoot
$REPO_ROOT         = if ($env:REPO_ROOT) { $env:REPO_ROOT } else { $PWD.Path }
if ((Test-Path (Join-Path $REPO_ROOT ".codegraph")) -and (Get-Command codegraph -ErrorAction SilentlyContinue)) {
    Push-Location $REPO_ROOT
    try { & codegraph unlock *> $null } catch {} finally { Pop-Location }
}
$AGENT             = $env:AGENT
$MODEL             = $env:MODEL
$CONTEXT_FILE      = $env:CONTEXT_PAYLOAD_FILE
$PROMPT_FILE_VAL   = $env:PROMPT_FILE
$PRINT_PROMPT      = if ($env:PRINT_PROMPT) { $env:PRINT_PROMPT } else { "0" }
$AGENT_PERM_MODE   = $env:AGENT_PERMISSION_MODE
$AGENT_EXTRA_ARGS  = $env:AGENT_EXTRA_ARGS
$REGISTRY_FILE     = if ($env:AGENT_REGISTRY_FILE) { $env:AGENT_REGISTRY_FILE } else { Join-Path $SCRIPT_DIR "agent-registry.json" }
$PERMANENTLY_BLOCKED_AGENTS = @("github-copilot")
$PERMANENTLY_BLOCKED_AGENT_REASON = "GitHub Copilot plan is exhausted; blocked from automatic routing and direct run-agent use."
$UNATTENDED        = $env:UNATTENDED -eq "1"
$GUI_MODE          = if ($env:GUI_MODE) { $env:GUI_MODE } else { "auto" }
$RUN_STATUS_FILE   = $env:RUN_WITH_IT_STATUS_FILE
$RUN_EVENTS_LOG    = $env:RUN_WITH_IT_EVENTS_LOG
$RUN_LOG_FILE      = $env:RUN_WITH_IT_LOG_FILE
$RUN_DONE_FILE     = $env:RUN_WITH_IT_DONE_FILE
$RUN_RESULT_FILE   = $env:RUN_WITH_IT_RESULT_FILE
$RUN_STATE_FILE    = $env:RUN_WITH_IT_STATE_FILE
$RUN_ROLE          = if ($env:RUN_WITH_IT_ROLE) { $env:RUN_WITH_IT_ROLE } else { "agent" }
$RUN_ISSUE         = if ($env:RUN_WITH_IT_ISSUE) { $env:RUN_WITH_IT_ISSUE } else { "unknown" }
$DRY_RUN           = $false
$LIST_AGENTS       = $false
$DETECTED_ONLY     = $false
$LIST_MODELS_AGENT = ""

$DEFAULT_PROMPT_FILE = Join-Path $SCRIPT_DIR "prompt.md"
if (-not $PROMPT_FILE_VAL) { $PROMPT_FILE_VAL = $DEFAULT_PROMPT_FILE }

# ── Arg parsing ───────────────────────────────────────────────────────────────
$i = 0
while ($i -lt $args.Count) {
    $arg = $args[$i]
    if ($arg -eq "--agent") {
        $AGENT = $args[++$i]
    } elseif ($arg -eq "--model") {
        $MODEL = $args[++$i]
    } elseif ($arg -in "--context-file", "--context-payload-file") {
        $CONTEXT_FILE = $args[++$i]
    } elseif ($arg -eq "--prompt-file") {
        $PROMPT_FILE_VAL = $args[++$i]
    } elseif ($arg -eq "--permission-mode") {
        $AGENT_PERM_MODE = $args[++$i]
    } elseif ($arg -eq "--extra-arg") {
        $extra = $args[++$i]
        $AGENT_EXTRA_ARGS = if ($AGENT_EXTRA_ARGS) { "$AGENT_EXTRA_ARGS $extra" } else { $extra }
    } elseif ($arg -eq "--dry-run") {
        $DRY_RUN = $true
    } elseif ($arg -eq "--unattended") {
        $UNATTENDED = $true
    } elseif ($arg -eq "--list-agents") {
        $LIST_AGENTS = $true
    } elseif ($arg -eq "--detected-only") {
        $DETECTED_ONLY = $true
    } elseif ($arg -eq "--list-models") {
        $LIST_MODELS_AGENT = $args[++$i]
    } elseif ($arg -in "-h", "--help") {
        Write-Host @"
Usage:
  run-agent.ps1 --agent <agent> [--model <model>] --context-file <file> --prompt-file <file> [--dry-run] [--unattended]
  run-agent.ps1 --list-agents [--detected-only]
  run-agent.ps1 --list-models <agent>

Environment equivalents:
  AGENT, MODEL, CONTEXT_PAYLOAD_FILE, PROMPT_FILE, PRINT_PROMPT, AGENT_PERMISSION_MODE, AGENT_REGISTRY_FILE, UNATTENDED, GUI_MODE,
  RUN_WITH_IT_STATUS_FILE, RUN_WITH_IT_EVENTS_LOG, RUN_WITH_IT_LOG_FILE, RUN_WITH_IT_DONE_FILE, RUN_WITH_IT_RESULT_FILE, RUN_WITH_IT_STATE_FILE
"@
        exit 0
    } elseif ($arg.StartsWith("-")) {
        [Console]::Error.WriteLine("error: unknown argument: $arg")
        exit 1
    } else {
        if (-not $CONTEXT_FILE) {
            $CONTEXT_FILE = $arg
        } elseif ($PROMPT_FILE_VAL -eq $DEFAULT_PROMPT_FILE) {
            $PROMPT_FILE_VAL = $arg
        } else {
            [Console]::Error.WriteLine("error: unexpected positional argument: $arg")
            exit 1
        }
    }
    $i++
}

# ── Helpers ───────────────────────────────────────────────────────────────────
function Fail([string]$msg) {
    [Console]::Error.WriteLine("error: $msg")
    exit 1
}

function Normalize-TelemetryValue([string]$value) {
    if ([string]::IsNullOrWhiteSpace($value)) {
        return "unknown"
    }

    return ($value -replace "`r", " " -replace "`n", " ")
}

function Write-Telemetry([string]$status) {
    $telemetryAgent = Normalize-TelemetryValue $AGENT
    $telemetryModel = Normalize-TelemetryValue $MODEL
    $line = "STATUS|type=telemetry|agent=$telemetryAgent|model=$telemetryModel|input_tokens=unknown|output_tokens=unknown|cache_hit_tokens=unknown|status=$status|source=runner-default"
    Write-LogLine $line
    [Console]::Error.WriteLine($line)
}

function Write-LogLine([string]$line) {
    if (-not $RUN_LOG_FILE) { return }

    $logDir = Split-Path $RUN_LOG_FILE
    if ($logDir) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
    Add-Content -Path $RUN_LOG_FILE -Value $line -Encoding UTF8
}

function Write-StatusLine([string]$line) {
    if ($RUN_STATUS_FILE) {
        $statusDir = Split-Path $RUN_STATUS_FILE
        if ($statusDir) { New-Item -ItemType Directory -Force -Path $statusDir | Out-Null }
        Set-Content -Path $RUN_STATUS_FILE -Value $line -Encoding UTF8
    }

    if ($RUN_EVENTS_LOG) {
        $eventsDir = Split-Path $RUN_EVENTS_LOG
        if ($eventsDir) { New-Item -ItemType Directory -Force -Path $eventsDir | Out-Null }
        Add-Content -Path $RUN_EVENTS_LOG -Value $line -Encoding UTF8
    }
}

function Initialize-DoneFile {
    if (-not $RUN_DONE_FILE) { return }

    $doneDir = Split-Path $RUN_DONE_FILE
    if ($doneDir) { New-Item -ItemType Directory -Force -Path $doneDir | Out-Null }
    Remove-Item -Force $RUN_DONE_FILE -ErrorAction SilentlyContinue
}

function Write-DoneFile([string]$status, [string]$source) {
    if (-not $RUN_DONE_FILE) { return }

    $doneDir = Split-Path $RUN_DONE_FILE
    if ($doneDir) { New-Item -ItemType Directory -Force -Path $doneDir | Out-Null }
    $line = "DONE|issue=$(Normalize-TelemetryValue $RUN_ISSUE)|role=$(Normalize-TelemetryValue $RUN_ROLE)|agent=$(Normalize-TelemetryValue $AGENT)|model=$(Normalize-TelemetryValue $MODEL)|status=$(Normalize-TelemetryValue $status)|source=$(Normalize-TelemetryValue $source)|completed_at=$([datetime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'))"
    Add-Content -Path $RUN_DONE_FILE -Value $line -Encoding UTF8
    Write-LogLine $line
}

function Write-RunStatus([string]$type, [string]$status = "") {
    if (-not $RUN_STATUS_FILE -and -not $RUN_EVENTS_LOG -and -not $RUN_LOG_FILE) { return }
    $statusField = if ($status) { "|status=$status" } else { "" }
    $line = "STATUS|type=$type|issue=$(Normalize-TelemetryValue $RUN_ISSUE)|role=$(Normalize-TelemetryValue $RUN_ROLE)|agent=$(Normalize-TelemetryValue $AGENT)|model=$(Normalize-TelemetryValue $MODEL)$statusField"
    Write-StatusLine $line
    Write-LogLine $line
    [Console]::Error.WriteLine($line)
}

function Get-AgentUnavailableReason {
    if (-not $RUN_LOG_FILE -or -not (Test-Path $RUN_LOG_FILE)) { return $null }
    $tail = ((Get-Content -Path $RUN_LOG_FILE -Tail 200 -ErrorAction SilentlyContinue) -join "`n")
    $lower = $tail.ToLowerInvariant()
    if ($lower -match 'failed to authenticate|invalid authentication credentials|api error: 401|authentication failed') { return 'auth' }
    if ($lower -match 'usage limit|quota|rate limit') { return 'quota' }
    if ($lower -match 'not supported when using codex with a chatgpt account|unsupported model|model is not supported|not supported') { return 'model-unsupported' }
    return $null
}

function Emit-AgentUnavailableStatus([string]$reason) {
    $line = "STATUS|type=agent-unavailable|issue=$(Normalize-TelemetryValue $RUN_ISSUE)|role=$(Normalize-TelemetryValue $RUN_ROLE)|agent=$(Normalize-TelemetryValue $AGENT)|model=$(Normalize-TelemetryValue $MODEL)|reason=$(Normalize-TelemetryValue $reason)|action=exclude-route"
    Write-StatusLine $line
    Write-LogLine $line
    [Console]::Error.WriteLine($line)
}

function Forward-AgentLine([string]$line, [string]$stream) {
    Write-LogLine $line

    if ($line.StartsWith("STATUS|") -or $line.StartsWith("ROUTE|") -or $line.StartsWith("COMPLEXITY|")) {
        Write-StatusLine $line
    }

    if ($line.StartsWith("STATUS|type=heartbeat|")) {
        return
    }

    if ($stream -eq "stderr") {
        [Console]::Error.WriteLine($line)
    } else {
        [Console]::Out.WriteLine($line)
    }
}

function Quote-ProcessArgument([string]$arg) {
    if ($null -eq $arg) { return '""' }
    if ($arg.Length -eq 0) { return '""' }
    if ($arg -notmatch '[\s"]') { return $arg }

    $result = '"'
    $backslashes = 0
    foreach ($char in $arg.ToCharArray()) {
        if ($char -eq '\') {
            $backslashes++
        } elseif ($char -eq '"') {
            if ($backslashes -gt 0) { $result += ('\' * ($backslashes * 2)) }
            $result += '\"'
            $backslashes = 0
        } else {
            if ($backslashes -gt 0) { $result += ('\' * $backslashes) }
            $result += $char
            $backslashes = 0
        }
    }

    if ($backslashes -gt 0) { $result += ('\' * ($backslashes * 2)) }
    $result += '"'
    return $result
}

function Join-ProcessArguments([object[]]$arguments) {
    return (($arguments | ForEach-Object { Quote-ProcessArgument ([string]$_) }) -join " ")
}

function Forward-CaptureFile {
    param(
        [string]$Path,
        [string]$Stream,
        [ref]$Offset,
        [ref]$Partial,
        [bool]$FlushPartial
    )

    if (-not (Test-Path $Path)) { return }

    $content = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    if ($Offset.Value -gt $content.Length) {
        $Offset.Value = 0
        $Partial.Value = ""
    }

    if ($Offset.Value -eq $content.Length -and -not $FlushPartial) { return }

    $newText = ""
    if ($Offset.Value -lt $content.Length) {
        $newText = $content.Substring($Offset.Value)
        $Offset.Value = $content.Length
    }

    $text = ($Partial.Value + $newText).Replace("`r`n", "`n").Replace("`r", "`n")
    if (-not $text) { return }

    $endsWithNewline = $text.EndsWith("`n")
    $parts = $text.Split([string[]]@("`n"), [System.StringSplitOptions]::None)
    $lineCount = $parts.Count - 1
    for ($idx = 0; $idx -lt $lineCount; $idx++) {
        Forward-AgentLine $parts[$idx] $Stream
    }

    if ($endsWithNewline) {
        $Partial.Value = ""
    } else {
        $Partial.Value = $parts[-1]
    }

    if ($FlushPartial -and $Partial.Value) {
        Forward-AgentLine $Partial.Value $Stream
        $Partial.Value = ""
    }
}

function Invoke-AgentCommandWithCapture([string]$filePath, [System.Collections.Generic.List[string]]$arguments) {
    $stdoutCapture = [System.IO.Path]::GetTempFileName()
    $stderrCapture = [System.IO.Path]::GetTempFileName()
    $stdoutOffset = 0
    $stderrOffset = 0
    $stdoutPartial = ""
    $stderrPartial = ""

    try {
        $argumentString = Join-ProcessArguments $arguments.ToArray()
        $process = Start-Process `
            -FilePath $filePath `
            -ArgumentList $argumentString `
            -RedirectStandardOutput $stdoutCapture `
            -RedirectStandardError $stderrCapture `
            -NoNewWindow `
            -PassThru

        while (-not $process.HasExited) {
            Forward-CaptureFile -Path $stdoutCapture -Stream "stdout" -Offset ([ref]$stdoutOffset) -Partial ([ref]$stdoutPartial) -FlushPartial:$false
            Forward-CaptureFile -Path $stderrCapture -Stream "stderr" -Offset ([ref]$stderrOffset) -Partial ([ref]$stderrPartial) -FlushPartial:$false
            Start-Sleep -Milliseconds 200
            $process.Refresh()
        }

        $process.WaitForExit()
        Forward-CaptureFile -Path $stdoutCapture -Stream "stdout" -Offset ([ref]$stdoutOffset) -Partial ([ref]$stdoutPartial) -FlushPartial:$true
        Forward-CaptureFile -Path $stderrCapture -Stream "stderr" -Offset ([ref]$stderrOffset) -Partial ([ref]$stderrPartial) -FlushPartial:$true
        return $process.ExitCode
    }
    finally {
        Remove-Item -Force $stdoutCapture -ErrorAction SilentlyContinue
        Remove-Item -Force $stderrCapture -ErrorAction SilentlyContinue
    }
}

function Test-GuiMode {
    if ($env:VSCODE_PID) { return $true }
    if ($env:TERM_PROGRAM -eq "vscode") { return $true }
    if ($env:ELECTRON_RUN_AS_NODE) { return $true }
    if ($env:ANTIGRAVITY_APP) { return $true }
    if ($env:CURSOR_TRACE_ID) { return $true }
    if ($env:CLAUDE_CODE_ENTRYPOINT) { return $true }
    return $false
}

function Resolve-GuiMode {
    switch ($GUI_MODE) {
        "auto" {
            if (Test-GuiMode) { $script:GUI_MODE = "1" } else { $script:GUI_MODE = "0" }
        }
        { $_ -in @("1", "true", "TRUE", "yes", "YES", "on", "ON") } {
            $script:GUI_MODE = "1"
        }
        { $_ -in @("0", "false", "FALSE", "no", "NO", "off", "OFF") } {
            $script:GUI_MODE = "0"
        }
        default {
            Fail "GUI_MODE must be auto, 1, or 0"
        }
    }
}

function Apply-GuiPermissionMode {
    if ($GUI_MODE -ne "1") { return }

    $script:UNATTENDED = $true
    switch ($AGENT) {
        "codex" {
            if (-not $AGENT_PERM_MODE -or $AGENT_PERM_MODE -eq "--dangerously-bypass-approvals-and-sandbox") {
                $script:AGENT_PERM_MODE = "--sandbox=workspace-write"
            }
        }
        "claude" {
            if (-not $AGENT_PERM_MODE -or $AGENT_PERM_MODE -eq "--dangerously-skip-permissions") {
                $script:AGENT_PERM_MODE = "--permission-mode=acceptEdits"
            }
        }
        "github-copilot" {
            if (-not $AGENT_PERM_MODE -or $AGENT_PERM_MODE -in @("--allow-all", "--yolo")) {
                $script:AGENT_PERM_MODE = "--allow-all-tools"
            }
        }
        "agy" {
            if (-not $AGENT_PERM_MODE -or $AGENT_PERM_MODE -eq "--dangerously-skip-permissions") {
                $script:AGENT_PERM_MODE = "--sandbox"
            }
        }
    }
}

if (-not (Test-Path $REGISTRY_FILE)) { Fail "agent registry file not found: $REGISTRY_FILE" }

$registry = Get-Content $REGISTRY_FILE -Raw -Encoding UTF8 | ConvertFrom-Json
$agentMap  = $registry.agents
$aliasMap  = $registry.aliases

function Resolve-AgentId([string]$id) {
    if ($aliasMap -and $aliasMap.PSObject.Properties[$id]) {
        return $aliasMap.PSObject.Properties[$id].Value
    }
    return $id
}

function Test-AgentPermanentlyBlocked([string]$id) {
    $resolved = Resolve-AgentId $id
    return $PERMANENTLY_BLOCKED_AGENTS -contains $resolved
}

function Get-AgentDef([string]$id) {
    $id = Resolve-AgentId $id
    if ($agentMap.PSObject.Properties[$id]) {
        return $agentMap.PSObject.Properties[$id].Value
    }
    return $null
}

function Test-AgentDisabled([string]$id) {
    if (Test-AgentPermanentlyBlocked $id) { return $true }
    $def = Get-AgentDef $id
    if (-not $def) { return $false }
    return ($def.routing_disabled -eq $true -or $def.usage_disabled -eq $true)
}

function Get-AgentDisabledReason([string]$id) {
    if (Test-AgentPermanentlyBlocked $id) { return $PERMANENTLY_BLOCKED_AGENT_REASON }
    $def = Get-AgentDef $id
    if (-not $def) { return "agent disabled by registry" }
    if ($def.routing_disabled_reason) { return $def.routing_disabled_reason }
    if ($def.disabled_reason) { return $def.disabled_reason }
    return "agent disabled by registry"
}

function Expand-ConfigPath([string]$path) {
    $home = $env:USERPROFILE
    $path = $path -replace '^\~', $home
    $path = $path -replace '\$HOME', $home
    $path = [System.Environment]::ExpandEnvironmentVariables($path)
    if ($path -match '^\./') { $path = Join-Path $REPO_ROOT $path.Substring(2) }
    return $path
}

function Test-AgentConfigured([string]$id) {
    $def = Get-AgentDef $id
    if (-not $def) { return $false }
    $req = $def.user_model_configuration.requires_user_model_config
    if (-not $req) { return $true }
    foreach ($cfgPath in $def.user_model_configuration.config_paths) {
        if (Test-Path (Expand-ConfigPath $cfgPath)) { return $true }
    }
    return $false
}

function Get-AgentStatus([string]$id) {
    $def = Get-AgentDef $id
    if (-not $def) { return "missing", "unknown agent" }
    if (Test-AgentDisabled $id) { return "disabled", (Get-AgentDisabledReason $id) }
    $cmd = $def.detection.command
    if (-not $cmd) { return "missing", "missing detection command" }
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { return "missing", "missing command: $cmd" }
    if (-not (Test-AgentConfigured $id)) {
        $msg = $def.user_model_configuration.skip_message
        return "missing", $(if ($msg) { $msg } else { "missing user model configuration" })
    }
    return "detected", "detected"
}

# ── List agents ───────────────────────────────────────────────────────────────
if ($LIST_AGENTS) {
    foreach ($prop in $agentMap.PSObject.Properties) {
        $id      = $prop.Name
        $display = $prop.Value.display_name
        $status, $reason = Get-AgentStatus $id
        if ($DETECTED_ONLY -and $status -ne "detected") { continue }
        Write-Host ("{0}`t{1}`t{2}`t{3}" -f $id, $display, $status, $reason)
    }
    exit 0
}

# ── List models ───────────────────────────────────────────────────────────────
if ($LIST_MODELS_AGENT) {
    $id  = Resolve-AgentId $LIST_MODELS_AGENT
    $def = Get-AgentDef $id
    if (-not $def) { Fail "unknown agent: $LIST_MODELS_AGENT" }
    if (Test-AgentDisabled $id) { Fail "agent is disabled: $id ($(Get-AgentDisabledReason $id))" }
    $models = $def.model.known_models
    if (-not $models) { Write-Host "No configured models for ${id}."; exit 0 }
    $models | ForEach-Object { Write-Host $_ }
    exit 0
}

# ── Validate ──────────────────────────────────────────────────────────────────
if (-not $AGENT)        { Fail "agent is required. Pass --agent or set AGENT." }
$AGENT    = Resolve-AgentId $AGENT
$agentDef = Get-AgentDef $AGENT
if (-not $agentDef)     { Fail "unknown agent: $AGENT" }
if (Test-AgentDisabled $AGENT) { Fail "agent is disabled: $AGENT ($(Get-AgentDisabledReason $AGENT))" }
if (-not $CONTEXT_FILE) { Fail "context payload file is required. Pass --context-file or set CONTEXT_PAYLOAD_FILE." }
if (-not (Test-Path $CONTEXT_FILE))    { Fail "context payload file not found: $CONTEXT_FILE" }
if (-not (Test-Path $PROMPT_FILE_VAL)) { Fail "prompt file not found: $PROMPT_FILE_VAL" }

# ── Resolve model + permission mode ──────────────────────────────────────────
if (-not $MODEL)          { $MODEL = $agentDef.model.default }
if (-not $AGENT_PERM_MODE) { $AGENT_PERM_MODE = $agentDef.permission_modes.default }
Resolve-GuiMode
Apply-GuiPermissionMode
if ($AGENT_PERM_MODE -eq "safe") { $AGENT_PERM_MODE = "" }

$PAYLOAD_FILE = [System.IO.Path]::GetTempFileName()
try {
    # ── Build payload ─────────────────────────────────────────────────────────
    $contextContent = Get-Content $CONTEXT_FILE    -Raw -Encoding UTF8
    $promptContent  = Get-Content $PROMPT_FILE_VAL -Raw -Encoding UTF8
    "$contextContent`nInstructions:`n`n$promptContent`n" | Set-Content $PAYLOAD_FILE -Encoding UTF8 -NoNewline

    if ($PRINT_PROMPT -eq "1") {
        Get-Content $PAYLOAD_FILE -Raw -Encoding UTF8
        exit 0
    }

    if ($AGENT_PERM_MODE -and -not $UNATTENDED) {
        Fail "unattended permission mode requires --unattended or UNATTENDED=1"
    }

    $promptPayload = Get-Content $PAYLOAD_FILE -Raw -Encoding UTF8
    if ($promptPayload.Length -gt 131072) {
        [Console]::Error.WriteLine("warn: prompt exceeds 128KB ($($promptPayload.Length) bytes); may be truncated in sandboxed contexts")
    }

    # ── Resolve model flag ────────────────────────────────────────────────────
    $modelFlag = ""
    if ($MODEL) {
        $flagTpl = $agentDef.model.flag_template
        if ($flagTpl) { $modelFlag = $flagTpl -replace '\{\{model\}\}', $MODEL }
    }

    # ── Build command from args_template ──────────────────────────────────────
    $invokeCmd = $agentDef.invocation.command
    if (-not $invokeCmd) { Fail "agent has no invocation command: $AGENT" }

    $cmdArgs = [System.Collections.Generic.List[string]]::new()
    foreach ($tpl in $agentDef.invocation.args_template) {
        switch ($tpl) {
            "{{prompt}}" {
                $cmdArgs.Add($promptPayload)
            }
            "{{repo_root}}" {
                $cmdArgs.Add($REPO_ROOT)
            }
            "{{permission_mode}}" {
                if ($AGENT_PERM_MODE) { $cmdArgs.Add($AGENT_PERM_MODE) }
            }
            "{{model_flag}}" {
                if ($modelFlag) {
                    $modelFlag.Split(' ', [System.StringSplitOptions]::RemoveEmptyEntries) |
                        ForEach-Object { $cmdArgs.Add($_) }
                }
            }
            "{{extra_args}}" {
                if ($AGENT_EXTRA_ARGS) {
                    $AGENT_EXTRA_ARGS.Split(' ', [System.StringSplitOptions]::RemoveEmptyEntries) |
                        ForEach-Object { $cmdArgs.Add($_) }
                }
            }
            default {
                $rendered = $tpl `
                    -replace '\{\{repo_root\}\}',       $REPO_ROOT `
                    -replace '\{\{permission_mode\}\}', $(if ($AGENT_PERM_MODE) { $AGENT_PERM_MODE } else { "" }) `
                    -replace '\{\{model_flag\}\}',      $modelFlag `
                    -replace '\{\{extra_args\}\}',      $(if ($AGENT_EXTRA_ARGS) { $AGENT_EXTRA_ARGS } else { "" }) `
                    -replace '\{\{prompt\}\}',          $promptPayload
                if ($rendered) { $cmdArgs.Add($rendered) }
            }
        }
    }

    # ── Execute ───────────────────────────────────────────────────────────────
    if ($DRY_RUN) {
        $quotedArgs = $cmdArgs | ForEach-Object { "'$_'" }
        Write-Host "$invokeCmd $($quotedArgs -join ' ')"
        exit 0
    }

    Initialize-DoneFile
    Write-RunStatus "agent-start"
    if ($RUN_STATUS_FILE -or $RUN_EVENTS_LOG -or $RUN_LOG_FILE) {
        $commandExitCode = Invoke-AgentCommandWithCapture $invokeCmd $cmdArgs
    } else {
        & $invokeCmd @cmdArgs
        $commandExitCode = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
    }
    if ((Test-Path (Join-Path $REPO_ROOT ".codegraph")) -and (Get-Command codegraph -ErrorAction SilentlyContinue)) {
        Push-Location $REPO_ROOT
        try { & codegraph mark-dirty *> $null } catch {} finally { Pop-Location }
    }
    if ($commandExitCode -eq 0) {
        Write-DoneFile "success" "runner-exit"
        Write-RunStatus "worker-done" "success"
        Write-RunStatus "agent-complete" "success"
        Write-Telemetry "success"
    } else {
        $unavailableReason = Get-AgentUnavailableReason
        if ($unavailableReason) {
            Emit-AgentUnavailableStatus $unavailableReason
        }
        Write-DoneFile "failed" "runner-exit"
        Write-RunStatus "worker-done" "failed"
        Write-RunStatus "agent-complete" "failed"
        Write-Telemetry "failed"
    }
    exit $commandExitCode
}
finally {
    Remove-Item -Force $PAYLOAD_FILE -ErrorAction SilentlyContinue
}
