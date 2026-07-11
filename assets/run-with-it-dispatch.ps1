param(
    [string]$AssetRoot = $env:ASSETS_DEST,
    [Parameter(Mandatory = $true)][string]$Role,
    [Parameter(Mandatory = $true)][string]$Issue,
    [string]$Cycle = "",
    [Parameter(Mandatory = $true)][string]$Agent,
    [Parameter(Mandatory = $true)][string]$Model,
    [string]$Effort = "",
    [Parameter(Mandatory = $true)][string]$ContextFile,
    [Parameter(Mandatory = $true)][string]$PromptFile,
    [Parameter(Mandatory = $true)][string]$LogFile,
    [Parameter(Mandatory = $true)][string]$DoneFile,
    [Parameter(Mandatory = $true)][string]$ResultFile,
    [string]$StateFile = $env:RUN_WITH_IT_STATE_FILE,
    [string]$RepoRoot = "",
    [string]$IssueDir = "",
    [string]$StatusFile = $env:RUN_WITH_IT_STATUS_FILE,
    [string]$EventsLog = $env:RUN_WITH_IT_EVENTS_LOG,
    [string]$TailStateFile = "",
    [int]$PollSeconds = $(if ($env:WORKER_POLL_SECONDS) { [int]$env:WORKER_POLL_SECONDS } else { 20 }),
    [int]$QuietSeconds = $(if ($env:RUN_WITH_IT_WORKER_QUIET_SECONDS) { [int]$env:RUN_WITH_IT_WORKER_QUIET_SECONDS } else { 120 }),
    [int]$StallSeconds = $(if ($env:RUN_WITH_IT_WORKER_STALL_SECONDS) { [int]$env:RUN_WITH_IT_WORKER_STALL_SECONDS } else { 300 }),
    [int]$TimeoutSeconds = $(if ($env:RUN_WITH_IT_DISPATCH_TIMEOUT_SECONDS) { [int]$env:RUN_WITH_IT_DISPATCH_TIMEOUT_SECONDS } else { 0 }),
    [object]$HardLimitSeconds = $(if ($env:RUN_WITH_IT_WORKER_HARD_LIMIT_SECONDS) { $env:RUN_WITH_IT_WORKER_HARD_LIMIT_SECONDS } else { 7200 }),
    [string]$AutoFailStalledRoles = $(if ($env:RUN_WITH_IT_AUTO_FAIL_STALLED_ROLES) { $env:RUN_WITH_IT_AUTO_FAIL_STALLED_ROLES } else { "complexity,impl,modify,plan" }),
    [string]$DispatchOutFile = "",
    [switch]$Detach,
    [switch]$DetachedChild,
    [switch]$DryRun,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"

$hardLimitExplicit = $PSBoundParameters.ContainsKey("HardLimitSeconds") -or [bool]$env:RUN_WITH_IT_WORKER_HARD_LIMIT_SECONDS
$parsedHardLimitSeconds = 0
if (-not [int]::TryParse([string]$HardLimitSeconds, [ref]$parsedHardLimitSeconds) -or $parsedHardLimitSeconds -lt 0) {
    $parsedHardLimitSeconds = 7200
    $hardLimitExplicit = $false
}
if (-not $hardLimitExplicit -and $Role -notin @("complexity", "impl", "modify", "review")) {
    $parsedHardLimitSeconds = 0
}
$HardLimitSeconds = $parsedHardLimitSeconds

Remove-Item Env:AGENT, Env:MODEL, Env:RUN_WITH_IT_EXPLICIT_LEGACY_OVERRIDES -ErrorAction SilentlyContinue

function Fail([string]$message) {
    [Console]::Error.WriteLine("run-with-it-dispatch.ps1: $message")
    exit 2
}

function Ensure-ParentDir([string]$path) {
    $dir = Split-Path $path
    if ($dir) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
}

function Get-PowerShellExe {
    try {
        $path = (Get-Process -Id $PID).Path
        if ($path) { return $path }
    } catch {}
    $candidate = Join-Path $PSHOME "pwsh"
    if (Test-Path $candidate) { return $candidate }
    $candidate = Join-Path $PSHOME "powershell.exe"
    if (Test-Path $candidate) { return $candidate }
    return "powershell"
}

function Get-PythonExe {
    if ($env:PYTHON_BIN) { return $env:PYTHON_BIN }
    $candidate = Get-Command python3 -ErrorAction SilentlyContinue
    if ($candidate) { return $candidate.Source }
    $candidate = Get-Command python -ErrorAction SilentlyContinue
    if ($candidate) { return $candidate.Source }
    Fail "python helper runtime not found"
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

function Get-IsoNow {
    return [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
}

function Get-UnixNow {
    return [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
}

function Get-FileMtimeEpoch([string]$path) {
    if (-not (Test-Path $path)) { return 0 }
    return ([DateTimeOffset](Get-Item $path).LastWriteTimeUtc).ToUnixTimeSeconds()
}

function Get-FileSize([string]$path) {
    if (-not (Test-Path $path)) { return 0 }
    return (Get-Item $path).Length
}

function Get-LogSignature {
    if (-not (Test-Path $LogFile)) { return "missing" }
    return "$(Get-FileSize $LogFile):$(Get-FileMtimeEpoch $LogFile)"
}

function Get-LatestHeartbeatLine {
    if (-not (Test-Path $LogFile)) { return "" }
    $match = Select-String -Path $LogFile -Pattern '^STATUS\|type=(wrapper-)?heartbeat\|' -ErrorAction SilentlyContinue | Select-Object -Last 1
    if ($match) { return $match.Line }
    return ""
}

function Write-Status([string]$line) {
    Write-Output $line
    Ensure-ParentDir $LogFile
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    if ($StatusFile) {
        Ensure-ParentDir $StatusFile
        Set-Content -Path $StatusFile -Value $line -Encoding UTF8
    }
    if ($EventsLog) {
        Ensure-ParentDir $EventsLog
        Add-Content -Path $EventsLog -Value $line -Encoding UTF8
    }
    $script:lastLogSignature = Get-LogSignature
}

function Refresh-LogActivity([int64]$nowEpoch) {
    $signature = Get-LogSignature
    if ($signature -ne $script:lastLogSignature) {
        $script:lastLogSignature = $signature
        $script:lastOutputEpoch = $nowEpoch
        $script:lastOutputAt = Get-IsoNow
        $heartbeat = Get-LatestHeartbeatLine
        if ($heartbeat -and $heartbeat -ne $script:lastHeartbeatLine) {
            $script:lastHeartbeatLine = $heartbeat
            $script:lastHeartbeatEpoch = $nowEpoch
            $script:lastHeartbeatAt = $script:lastOutputAt
        }
    }
}

function Write-WorkerState(
    [string]$state,
    [bool]$alive,
    [Nullable[int]]$exitCode = $null,
    [string]$stallReason = "",
    [string]$failureClass = ""
) {
    $nowEpoch = Get-UnixNow
    $secondsSinceOutput = $nowEpoch - $script:lastOutputEpoch
    $secondsSinceHeartbeat = $null
    if ($script:lastHeartbeatEpoch -gt 0) {
        $secondsSinceHeartbeat = $nowEpoch - $script:lastHeartbeatEpoch
    }

    $payload = [ordered]@{
        schema_version = 1
        issue = $Issue
        role = $Role
        cycle = $(if ($Cycle) { $Cycle } else { $null })
        state = $state
        dispatcher_pid = $PID
        runner_pid = $(if ($script:runnerPid) { $script:runnerPid } else { $null })
        agent = $Agent
        model = $Model
        effort = $Effort
        alive = $alive
        done = ((Test-Path $DoneFile) -and ((Get-Item $DoneFile).Length -gt 0))
        result_present = ((Test-Path $ResultFile) -and ((Get-Item $ResultFile).Length -gt 0))
        log_present = ((Test-Path $LogFile) -and ((Get-Item $LogFile).Length -gt 0))
        log_file = $LogFile
        done_file = $DoneFile
        result_file = $ResultFile
        state_file = $StateFile
        log_size_bytes = Get-FileSize $LogFile
        log_mtime_epoch = Get-FileMtimeEpoch $LogFile
        quiet_seconds = $QuietSeconds
        stall_seconds = $StallSeconds
        hard_limit_seconds = $HardLimitSeconds
        seconds_since_last_output = $secondsSinceOutput
        seconds_since_last_heartbeat = $secondsSinceHeartbeat
        started_at = $script:startedIso
        last_output_at = $script:lastOutputAt
        last_heartbeat_at = $(if ($script:lastHeartbeatAt) { $script:lastHeartbeatAt } else { $null })
        updated_at = Get-IsoNow
        stall_reason = $(if ($stallReason) { $stallReason } else { $null })
        failure_class = $(if ($failureClass) { $failureClass } else { $null })
        exit_code = $exitCode
    }

    Ensure-ParentDir $StateFile
    $tmpFile = "$StateFile.tmp.$PID"
    $payload | ConvertTo-Json -Depth 8 | Set-Content -Path $tmpFile -Encoding UTF8
    Move-Item -Force $tmpFile $StateFile
}

function Test-ImplementationRole {
    return ($Role -eq "impl" -or $Role -eq "modify")
}

function Test-AutoFailStalledRole {
    foreach ($roleName in ($AutoFailStalledRoles -split ",")) {
        if ($roleName.Trim() -eq $Role) { return $true }
    }
    return $false
}

function Get-RepoRootForWorker {
    if ($RepoRoot) { return $RepoRoot }
    if ($env:REPO_ROOT) { return $env:REPO_ROOT }
    return (Get-Location).Path
}

function Invoke-ArtifactHelper([string]$command, [switch]$FromStall) {
    $repoRootValue = Get-RepoRootForWorker
    $preSpawnHead = if ($script:preSpawnHead) { $script:preSpawnHead } else { "" }
    $helperArgs = @(
        $script:ArtifactHelper, $command,
        "--role", $Role,
        "--issue", $Issue,
        "--result-file", $ResultFile,
        "--done-file", $DoneFile,
        "--log-file", $LogFile,
        "--issue-dir", $IssueDir,
        "--repo-root", $repoRootValue,
        "--pre-spawn-head", $preSpawnHead
    )
    if ($FromStall) { $helperArgs += "--from-stall" }
    & $script:PythonExe @helperArgs
}

function Get-ResultArtifactFailureReason {
    $output = Invoke-ArtifactHelper "failure-reason" 2>$null
    if ($LASTEXITCODE -ne 0) {
        return "artifact-helper-failed"
    }
    return (($output -join "`n").Trim())
}

function Try-SynthesizeResultArtifact([switch]$FromStall) {
    Invoke-ArtifactHelper "synthesize" -FromStall:$FromStall *> $null
    return ($LASTEXITCODE -eq 0)
}

function Get-ResultArtifactFailureClass {
    $output = Invoke-ArtifactHelper "failure-class" 2>$null
    if ($LASTEXITCODE -ne 0) {
        return "capability"
    }
    return (($output -join "`n").Trim())
}

function Get-CompletionFailureReason {
    if (-not ((Test-Path $DoneFile) -and ((Get-Item $DoneFile).Length -gt 0))) {
        return "missing-done-sentinel"
    }
    return Get-ResultArtifactFailureReason
}

function Test-CompletionReady {
    if (-not ((Test-Path $DoneFile) -and ((Get-Item $DoneFile).Length -gt 0))) {
        return $false
    }
    return ((Get-ResultArtifactFailureReason) -eq "")
}

if (-not $AssetRoot) {
    $homeAssetRoot = Join-Path $env:USERPROFILE ".ai-skill-collections\assets"
    if (Test-Path (Join-Path $homeAssetRoot "run-agent.ps1")) {
        $AssetRoot = $homeAssetRoot
    } else {
        $AssetRoot = $PSScriptRoot
    }
}

$RunAgent = Join-Path $AssetRoot "run-agent.ps1"
$WorkerWatch = Join-Path $AssetRoot "worker-watch.ps1"
$RegistryFile = Join-Path $AssetRoot "agent-registry.json"
$script:ArtifactHelper = Join-Path $AssetRoot "run-with-it-artifacts.py"
$script:PythonExe = Get-PythonExe

if (-not (Test-Path $RunAgent)) { Fail "runner not found: $RunAgent" }
if (-not (Test-Path $WorkerWatch)) { Fail "worker watcher not found: $WorkerWatch" }
if (-not (Test-Path $RegistryFile)) { Fail "agent registry not found: $RegistryFile" }
if (-not (Test-Path $script:ArtifactHelper)) { Fail "artifact helper not found: $script:ArtifactHelper" }
if (-not (Test-Path $ContextFile)) { Fail "context file not found: $ContextFile" }
if (-not (Test-Path $PromptFile)) { Fail "prompt file not found: $PromptFile" }
if ($RepoRoot -and -not (Test-Path $RepoRoot)) { Fail "repo root not found: $RepoRoot" }

if (-not $IssueDir) {
    $IssueDir = if ($env:RUN_WITH_IT_ISSUE_DIR) {
        $env:RUN_WITH_IT_ISSUE_DIR
    } else {
        Join-Path (Join-Path (Join-Path (Get-Location).Path ".run-with-it") "issues") $Issue
    }
}

if (-not $StateFile) {
    $logDir = Split-Path $LogFile
    $logBase = [System.IO.Path]::GetFileNameWithoutExtension($LogFile)
    $StateFile = Join-Path $logDir "$logBase.state.json"
}

foreach ($path in @($LogFile, $DoneFile, $ResultFile, $StateFile)) {
    Ensure-ParentDir $path
}
if ($StatusFile) { Ensure-ParentDir $StatusFile }
if ($EventsLog) { Ensure-ParentDir $EventsLog }
if (-not $TailStateFile) {
    $cyclePart = if ($Cycle) { $Cycle } else { "0" }
    $TailStateFile = Join-Path (Join-Path (Get-Location).Path ".run-with-it") "status"
    $TailStateFile = Join-Path $TailStateFile "issue-$Issue-$Role-cycle-$cyclePart.tail.sha"
}
New-Item -ItemType Directory -Force -Path $IssueDir | Out-Null

$cycleField = if ($Cycle) { "|cycle=$Cycle" } else { "" }

if ($DryRun) {
    $repoRootValue = if ($RepoRoot) { $RepoRoot } elseif ($env:REPO_ROOT) { $env:REPO_ROOT } else { (Get-Location).Path }
    Write-Output "GUI_MODE=0 AGENT_REGISTRY_FILE=$RegistryFile REPO_ROOT=$repoRootValue RUN_WITH_IT_ISSUE_DIR=$IssueDir RUN_WITH_IT_STATUS_FILE=$StatusFile RUN_WITH_IT_EVENTS_LOG=$EventsLog RUN_WITH_IT_LOG_FILE=$LogFile RUN_WITH_IT_DONE_FILE=$DoneFile RUN_WITH_IT_RESULT_FILE=$ResultFile RUN_WITH_IT_STATE_FILE=$StateFile RUN_WITH_IT_ARTIFACT_HELPER=$script:ArtifactHelper RUN_WITH_IT_ROLE=$Role RUN_WITH_IT_ISSUE=$Issue $RunAgent --agent $Agent --model $Model --effort $Effort --context-file $ContextFile --prompt-file $PromptFile --unattended"
    exit 0
}

if ($Detach -and -not $DetachedChild -and -not $ValidateOnly) {
    if (-not $DispatchOutFile) {
        if ($LogFile.EndsWith(".log", [StringComparison]::OrdinalIgnoreCase)) {
            $DispatchOutFile = "$($LogFile.Substring(0, $LogFile.Length - 4)).dispatch.out"
        } else {
            $DispatchOutFile = "$LogFile.dispatch.out"
        }
    }
    Ensure-ParentDir $DispatchOutFile
    $dispatchErrFile = "$DispatchOutFile.err"
    Ensure-ParentDir $dispatchErrFile
    $powerShellExe = Get-PowerShellExe
    $detachArgs = @(
        "-NoProfile",
        "-File", $PSCommandPath,
        "-AssetRoot", $AssetRoot,
        "-Role", $Role,
        "-Issue", $Issue,
        "-Cycle", $Cycle,
        "-Agent", $Agent,
        "-Model", $Model,
        "-Effort", $Effort,
        "-ContextFile", $ContextFile,
        "-PromptFile", $PromptFile,
        "-LogFile", $LogFile,
        "-DoneFile", $DoneFile,
        "-ResultFile", $ResultFile,
        "-StateFile", $StateFile,
        "-RepoRoot", $RepoRoot,
        "-IssueDir", $IssueDir,
        "-StatusFile", $StatusFile,
        "-EventsLog", $EventsLog,
        "-TailStateFile", $TailStateFile,
        "-PollSeconds", $PollSeconds,
        "-QuietSeconds", $QuietSeconds,
        "-StallSeconds", $StallSeconds,
        "-TimeoutSeconds", $TimeoutSeconds,
        "-HardLimitSeconds", $HardLimitSeconds,
        "-AutoFailStalledRoles", $AutoFailStalledRoles,
        "-DispatchOutFile", $DispatchOutFile,
        "-Detach",
        "-DetachedChild"
    )
    $process = Start-Process -FilePath $powerShellExe -ArgumentList (Join-ProcessArguments $detachArgs) -RedirectStandardOutput $DispatchOutFile -RedirectStandardError $dispatchErrFile -PassThru
    Write-Status "STATUS|type=dispatch-detached|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|out_file=$DispatchOutFile"
    exit 0
}

$script:startedAt = Get-UnixNow
$script:startedIso = Get-IsoNow
$script:lastOutputEpoch = $script:startedAt
$script:lastOutputAt = $script:startedIso
$script:lastHeartbeatEpoch = 0
$script:lastHeartbeatAt = ""
$script:lastHeartbeatLine = ""
$script:lastLogSignature = Get-LogSignature
$script:lastState = "ready"
$script:runnerPid = $null
$script:preSpawnHead = ""
if (Test-ImplementationRole) {
    try {
        $repoRootValue = Get-RepoRootForWorker
        & git -C $repoRootValue rev-parse --is-inside-work-tree *> $null
        if ($LASTEXITCODE -eq 0) {
            $script:preSpawnHead = ((& git -C $repoRootValue rev-parse HEAD 2>$null) -join "").Trim()
        }
    } catch {
        $script:preSpawnHead = ""
    }
}

try {
    Write-Status "STATUS|type=dispatch-ready|issue=$Issue|role=$Role$cycleField|agent=$Agent|model=$Model|effort=$Effort|result_file=$ResultFile"
    Write-WorkerState "ready" $false
} catch {
    Write-Status "STATUS|type=dispatch-pre-start-failed|issue=$Issue|role=$Role$cycleField|reason=state-write-failed|state_file=$StateFile"
    throw
}

if ($ValidateOnly) {
    exit 0
}

Write-Status "STATUS|type=dispatch-start|issue=$Issue|role=$Role$cycleField|agent=$Agent|model=$Model|effort=$Effort"
$script:lastState = "starting"
Write-WorkerState "starting" $false

$powerShellExe = Get-PowerShellExe
$runnerArgs = @(
    "-NoProfile",
    "-File", $RunAgent,
    "--agent", $Agent,
    "--model", $Model,
    "--effort", $Effort,
    "--context-file", $ContextFile,
    "--prompt-file", $PromptFile,
    "--unattended"
)

$envBackup = @{}
foreach ($name in @(
    "GUI_MODE",
    "AGENT_REGISTRY_FILE",
    "REPO_ROOT",
    "RUN_WITH_IT_ISSUE_DIR",
    "RUN_WITH_IT_STATUS_FILE",
    "RUN_WITH_IT_EVENTS_LOG",
    "RUN_WITH_IT_LOG_FILE",
    "RUN_WITH_IT_DONE_FILE",
    "RUN_WITH_IT_RESULT_FILE",
    "RUN_WITH_IT_STATE_FILE",
    "RUN_WITH_IT_ARTIFACT_HELPER",
    "RUN_WITH_IT_ROLE",
    "RUN_WITH_IT_ISSUE"
)) {
    $envBackup[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

try {
    $env:GUI_MODE = if ($env:GUI_MODE) { $env:GUI_MODE } else { "0" }
    $env:AGENT_REGISTRY_FILE = $RegistryFile
    $env:REPO_ROOT = if ($RepoRoot) { $RepoRoot } elseif ($env:REPO_ROOT) { $env:REPO_ROOT } else { (Get-Location).Path }
    $env:RUN_WITH_IT_ISSUE_DIR = $IssueDir
    $env:RUN_WITH_IT_STATUS_FILE = $StatusFile
    $env:RUN_WITH_IT_EVENTS_LOG = $EventsLog
    $env:RUN_WITH_IT_LOG_FILE = $LogFile
    $env:RUN_WITH_IT_DONE_FILE = $DoneFile
    $env:RUN_WITH_IT_RESULT_FILE = $ResultFile
    $env:RUN_WITH_IT_STATE_FILE = $StateFile
    $env:RUN_WITH_IT_ARTIFACT_HELPER = $script:ArtifactHelper
    $env:RUN_WITH_IT_ROLE = $Role
    $env:RUN_WITH_IT_ISSUE = $Issue

    $process = Start-Process -FilePath $powerShellExe -ArgumentList (Join-ProcessArguments $runnerArgs) -PassThru
}
finally {
    foreach ($name in $envBackup.Keys) {
        [Environment]::SetEnvironmentVariable($name, $envBackup[$name], "Process")
    }
}

$script:runnerPid = $process.Id
Write-Status "STATUS|type=dispatch-pid|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|done_file=$DoneFile|result_file=$ResultFile"
$script:lastState = "running"
Write-WorkerState "running" $true

while ($true) {
    Start-Sleep -Seconds $PollSeconds
    $now = Get-UnixNow
    Refresh-LogActivity $now

    try {
        & $powerShellExe -NoProfile -File $WorkerWatch -Pid $process.Id -DoneFile $DoneFile -LogFile $LogFile -TailStateFile $TailStateFile -TailLines 5 | Out-Null
    } catch {}

    if (Test-CompletionReady) {
        $process.Refresh()
        Write-WorkerState "completed" (-not $process.HasExited)
        Write-Status "STATUS|type=dispatch-complete|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|result_file=$ResultFile"
        exit 0
    }

    $process.Refresh()
    if ($process.HasExited) {
        $exitCode = $process.ExitCode
        # Synthesis is gated by git ground-truth inside the helper (HEAD must
        # have advanced past pre_spawn_head with committed files), so it is safe
        # to attempt regardless of exit code: a worker that committed real work
        # then crashed (e.g. a provider auth/quota failure mid-run) is salvaged
        # instead of burning a fallback attempt.
        if (Try-SynthesizeResultArtifact) {
            Write-Status "STATUS|type=result-artifact-synthesized|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|exit_code=$exitCode|result_file=$ResultFile"
        }
        $artifactReason = Get-ResultArtifactFailureReason
        if ($artifactReason -eq "artifact-recovery-required") {
            Write-WorkerState "artifact-recovery-required" $false $exitCode $artifactReason "capability"
            Write-Status "STATUS|type=dispatch-recovery-required|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|reason=$artifactReason|result_file=$ResultFile"
            exit 75
        }
        if (Test-CompletionReady) {
            Write-WorkerState "completed" $false $exitCode
            Write-Status "STATUS|type=dispatch-complete|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|result_file=$ResultFile"
            exit 0
        }
        $failureReason = Get-CompletionFailureReason
        if (-not $failureReason) { $failureReason = "process-exited-missing-done-or-result" }
        $failureClass = Get-ResultArtifactFailureClass
        Write-WorkerState "failed" $false $exitCode $failureReason $failureClass
        Write-Status "STATUS|type=dispatch-failed|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|reason=$failureReason|failure_class=$failureClass|done_file=$DoneFile|result_file=$ResultFile"
        exit 1
    }

    $silenceSeconds = $now - $script:lastOutputEpoch
    $state = "running"
    $stallReason = ""
    if ($silenceSeconds -ge $StallSeconds) {
        $state = "stalled"
        $stallReason = "alive-but-silent"
    } elseif ($silenceSeconds -ge $QuietSeconds) {
        $state = "quiet"
        $stallReason = "alive-but-quiet"
    }

    Write-WorkerState $state $true $null $stallReason
    if ($state -ne $script:lastState) {
        if ($state -eq "quiet") {
            Write-Status "STATUS|type=worker-quiet|issue=$Issue|role=$Role$cycleField|reason=alive-but-quiet|silence_seconds=$silenceSeconds|state_file=$StateFile"
        } elseif ($state -eq "stalled") {
            Write-Status "STATUS|type=worker-stalled|issue=$Issue|role=$Role$cycleField|reason=alive-but-silent|silence_seconds=$silenceSeconds|state_file=$StateFile"
        }
        $script:lastState = $state
    }

    $elapsed = $now - $script:startedAt
    if ($HardLimitSeconds -ne 0 -and $elapsed -ge $HardLimitSeconds) {
        if (Test-CompletionReady) {
            Write-WorkerState "completed" $true 0
            Write-Status "STATUS|type=dispatch-complete|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|result_file=$ResultFile"
            exit 0
        }
        if (Try-SynthesizeResultArtifact -FromStall) {
            $artifactReason = Get-ResultArtifactFailureReason
            if ($artifactReason -eq "artifact-recovery-required") {
                Write-Status "STATUS|type=worker-hard-limit|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|elapsed=$elapsed|action=preserve-for-recovery"
                try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
                Write-WorkerState "artifact-recovery-required" $false 75 $artifactReason "capability"
                Write-Status "STATUS|type=dispatch-recovery-required|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|reason=$artifactReason|result_file=$ResultFile"
                exit 75
            }
        }
        if (Test-CompletionReady) {
            Write-Status "STATUS|type=worker-hard-limit|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|elapsed=$elapsed|action=salvage-and-terminate"
            try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
            Write-WorkerState "completed" $false 0 "salvaged-at-hard-limit"
            Write-Status "STATUS|type=dispatch-complete|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|result_file=$ResultFile"
            exit 0
        }
        Write-Status "STATUS|type=worker-hard-limit|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|elapsed=$elapsed|action=terminate-runner"
        try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
        $failureClass = Get-ResultArtifactFailureClass
        Write-WorkerState "failed" $false 124 "hard-limit-exceeded" $failureClass
        Write-Status "STATUS|type=dispatch-failed|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|reason=hard-limit-exceeded|failure_class=$failureClass|done_file=$DoneFile|result_file=$ResultFile"
        exit 124
    }

    if (($state -eq "stalled") -and (Test-AutoFailStalledRole)) {
        if (Try-SynthesizeResultArtifact -FromStall) {
            $artifactReason = Get-ResultArtifactFailureReason
            if ($artifactReason -eq "artifact-recovery-required") {
                Write-Status "STATUS|type=worker-stall-timeout|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|reason=alive-but-silent|action=preserve-for-recovery"
                Write-Status "STATUS|type=result-artifact-synthesized|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|reason=stall-salvage|result_file=$ResultFile"
                try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
                Write-WorkerState "artifact-recovery-required" $false 75 $artifactReason "capability"
                Write-Status "STATUS|type=dispatch-recovery-required|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|reason=$artifactReason|result_file=$ResultFile"
                exit 75
            }
        }
        if (Test-CompletionReady) {
            Write-Status "STATUS|type=worker-stall-timeout|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|reason=alive-but-silent|action=salvage-and-terminate"
            try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
            Write-WorkerState "completed" $false 0 "salvaged-from-stall"
            Write-Status "STATUS|type=dispatch-complete|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|result_file=$ResultFile"
            exit 0
        }
        Write-Status "STATUS|type=worker-stall-timeout|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|reason=alive-but-silent|action=terminate-runner"
        try {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        } catch {}
        $failureClass = Get-ResultArtifactFailureClass
        Write-WorkerState "failed" $false 124 "alive-but-silent" $failureClass
        Write-Status "STATUS|type=dispatch-failed|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|reason=alive-but-silent|failure_class=$failureClass|done_file=$DoneFile|result_file=$ResultFile"
        exit 1
    }

    if ($TimeoutSeconds -ne 0) {
        if ($elapsed -ge $TimeoutSeconds) {
            Write-Status "STATUS|type=dispatch-stall|issue=$Issue|role=$Role$cycleField|pid=$($process.Id)|elapsed=$elapsed|action=alert-user"
            $TimeoutSeconds = 0
        }
    }
}
