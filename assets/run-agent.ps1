# run-agent.ps1 — Windows PowerShell unified agent runner (mirrors run-agent.sh)
#
# Usage:
#   run-agent.ps1 --agent <agent> [--model <model>] --context-file <file> --prompt-file <file> [--dry-run] [--unattended]
#   run-agent.ps1 --list-agents [--detected-only]
#   run-agent.ps1 --list-models <agent>
#
# Environment equivalents:
#   AGENT, MODEL, CONTEXT_PAYLOAD_FILE, PROMPT_FILE, PRINT_PROMPT,
#   AGENT_PERMISSION_MODE, AGENT_EXTRA_ARGS, AGENT_REGISTRY_FILE, UNATTENDED

$ErrorActionPreference = "Stop"

$SCRIPT_DIR        = $PSScriptRoot
$REPO_ROOT         = if ($env:REPO_ROOT) { $env:REPO_ROOT } else { $PWD.Path }
$AGENT             = $env:AGENT
$MODEL             = $env:MODEL
$CONTEXT_FILE      = $env:CONTEXT_PAYLOAD_FILE
$PROMPT_FILE_VAL   = $env:PROMPT_FILE
$PRINT_PROMPT      = if ($env:PRINT_PROMPT) { $env:PRINT_PROMPT } else { "0" }
$AGENT_PERM_MODE   = $env:AGENT_PERMISSION_MODE
$AGENT_EXTRA_ARGS  = $env:AGENT_EXTRA_ARGS
$REGISTRY_FILE     = if ($env:AGENT_REGISTRY_FILE) { $env:AGENT_REGISTRY_FILE } else { Join-Path $SCRIPT_DIR "agent-registry.json" }
$UNATTENDED        = $false
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
  AGENT, MODEL, CONTEXT_PAYLOAD_FILE, PROMPT_FILE, PRINT_PROMPT, AGENT_PERMISSION_MODE, AGENT_REGISTRY_FILE, UNATTENDED
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
    [Console]::Error.WriteLine("STATUS|type=telemetry|agent=$telemetryAgent|model=$telemetryModel|input_tokens=unknown|output_tokens=unknown|cache_hit_tokens=unknown|status=$status|source=runner-default")
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

function Get-AgentDef([string]$id) {
    $id = Resolve-AgentId $id
    if ($agentMap.PSObject.Properties[$id]) {
        return $agentMap.PSObject.Properties[$id].Value
    }
    return $null
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
if (-not $CONTEXT_FILE) { Fail "context payload file is required. Pass --context-file or set CONTEXT_PAYLOAD_FILE." }
if (-not (Test-Path $CONTEXT_FILE))    { Fail "context payload file not found: $CONTEXT_FILE" }
if (-not (Test-Path $PROMPT_FILE_VAL)) { Fail "prompt file not found: $PROMPT_FILE_VAL" }

# ── Resolve model + permission mode ──────────────────────────────────────────
if (-not $MODEL)          { $MODEL = $agentDef.model.default }
if (-not $AGENT_PERM_MODE) { $AGENT_PERM_MODE = $agentDef.permission_modes.default }
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
                    -replace '\{\{permission_mode\}\}', ($AGENT_PERM_MODE ?? "") `
                    -replace '\{\{model_flag\}\}',      $modelFlag `
                    -replace '\{\{extra_args\}\}',      ($AGENT_EXTRA_ARGS ?? "") `
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

    & $invokeCmd @cmdArgs
    $commandExitCode = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
    if ($commandExitCode -eq 0) {
        Write-Telemetry "success"
    } else {
        Write-Telemetry "failed"
    }
    exit $commandExitCode
}
finally {
    Remove-Item -Force $PAYLOAD_FILE -ErrorAction SilentlyContinue
}
