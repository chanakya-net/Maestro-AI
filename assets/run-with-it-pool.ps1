param(
    [string]$AssetRoot = $env:ASSETS_DEST,
    [string]$StateFile = (Join-Path (Join-Path (Get-Location).Path ".run-with-it") "main-state.json"),
    [int]$ParallelJobs = 0,
    [string]$Agent = $(if ($env:SUB_COORD_AGENT) { $env:SUB_COORD_AGENT } else { "codex" }),
    [string]$Model = $(if ($env:SUB_COORD_MODEL) { $env:SUB_COORD_MODEL } else { "gpt-5.6-sol" }),
    [string]$StatusFile = $(if ($env:RUN_WITH_IT_STATUS_FILE) { $env:RUN_WITH_IT_STATUS_FILE } else { Join-Path (Join-Path (Join-Path (Get-Location).Path ".run-with-it") "status") "current.txt" }),
    [string]$EventsLog = $(if ($env:RUN_WITH_IT_EVENTS_LOG) { $env:RUN_WITH_IT_EVENTS_LOG } else { Join-Path (Join-Path (Join-Path (Get-Location).Path ".run-with-it") "status") "events.log" }),
    [string]$MainLog = (Join-Path (Join-Path (Join-Path (Get-Location).Path ".run-with-it") "main") "main.log"),
    [int]$PollSeconds = $(if ($env:STATUS_POLL_SECONDS) { [int]$env:STATUS_POLL_SECONDS } else { 10 }),
    [int]$TimeoutSeconds = $(if ($env:SUB_COORD_TIMEOUT_SECONDS) { [int]$env:SUB_COORD_TIMEOUT_SECONDS } else { 3600 }),
    [int]$MaxSubCoordRecoveryAttempts = $(if ($env:MAX_SUB_COORD_RECOVERY_ATTEMPTS) { [int]$env:MAX_SUB_COORD_RECOVERY_ATTEMPTS } else { 2 }),
    [string]$PoolStateFile = "",
    [switch]$DryRun,
    [switch]$ValidateOnly,
    [switch]$Detach
)

$ErrorActionPreference = "Stop"

function Fail([string]$message) {
    [Console]::Error.WriteLine("run-with-it-pool.ps1: $message")
    exit 2
}

function Ensure-ParentDir([string]$path) {
    $dir = Split-Path $path
    if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
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
    $python3 = Get-Command python3 -ErrorAction SilentlyContinue
    if ($python3) { return $python3.Source }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return $python.Source }
    Fail "python helper runtime not found: python3"
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

function Write-Status([string]$line) {
    Write-Output $line
    foreach ($path in @($MainLog, $StatusFile, $EventsLog)) { Ensure-ParentDir $path }
    Add-Content -Path $MainLog -Value $line -Encoding UTF8
    Set-Content -Path $StatusFile -Value $line -Encoding UTF8
    Add-Content -Path $EventsLog -Value $line -Encoding UTF8
}

function Remove-ProcessCapture($entry) {
    foreach ($path in @($entry.stdout_file, $entry.stderr_file)) {
        if ($path) { Remove-Item -Force $path -ErrorAction SilentlyContinue }
    }
}

if (-not $AssetRoot) {
    $homeAssetRoot = Join-Path $env:USERPROFILE ".ai-skill-collections\assets"
    if (Test-Path (Join-Path $homeAssetRoot "run-with-it-dispatch.ps1")) {
        $AssetRoot = $homeAssetRoot
    } else {
        $AssetRoot = $PSScriptRoot
    }
}

$Dispatcher = Join-Path $AssetRoot "run-with-it-dispatch.ps1"
$PromptFile = Join-Path $AssetRoot "sub-coordinator-prompt.md"
$MergeRecoveryPromptFile = Join-Path $AssetRoot "merge-recovery-prompt.md"
$StateHelper = Join-Path $AssetRoot "run-with-it-state.py"
$GitHubUpdateHelper = Join-Path $AssetRoot "run-with-it-github-update.py"

if (-not (Test-Path $Dispatcher)) { Fail "dispatcher not found: $Dispatcher" }
if (-not (Test-Path $PromptFile)) { Fail "sub-coordinator prompt not found: $PromptFile" }
if (-not (Test-Path $MergeRecoveryPromptFile)) { Fail "merge recovery prompt not found: $MergeRecoveryPromptFile" }
if (-not (Test-Path $StateHelper)) { Fail "state helper not found: $StateHelper" }
if (-not (Test-Path $GitHubUpdateHelper)) { Fail "GitHub update helper not found: $GitHubUpdateHelper" }
if (-not (Test-Path $StateFile)) { Fail "state file not found: $StateFile" }

$RunRoot = (Resolve-Path (Join-Path (Split-Path $StateFile) "..")).Path
$PowerShellExe = Get-PowerShellExe
$PythonExe = Get-PythonExe

if (-not $PoolStateFile) {
    $PoolStateFile = Join-Path (Join-Path (Join-Path $RunRoot ".run-with-it") "main") "pool.state.json"
}

function Write-PoolState([int]$poolPid) {
    Ensure-ParentDir $PoolStateFile
    $payload = @{
        pool_pid = $poolPid
        started_at = [int][double]::Parse((Get-Date -UFormat %s))
        state_file = $StateFile
    }
    Set-Content -Path $PoolStateFile -Value ($payload | ConvertTo-Json -Compress) -Encoding UTF8
}

function Invoke-StateHelper([object[]]$helperArgs) {
    $output = & $PythonExe $StateHelper @helperArgs
    if ($LASTEXITCODE -ne 0) { Fail "state helper failed: $($helperArgs -join ' ')" }
    return $output
}

function Get-ReadyIssues([int]$limit) {
    $output = Invoke-StateHelper @("ready-issues", "--state-file", $StateFile, "--limit", "$limit")
    if (-not $output) { return @() }
    return @("$output" -split "\s+" | Where-Object { $_ })
}

function Get-ReadyMissingContextCount {
    $output = Invoke-StateHelper @("ready-missing-context-count", "--state-file", $StateFile)
    return [int]$output
}

function Get-ContextFileFor([string]$issue) {
    return [string](Invoke-StateHelper @("context-file-for", "--state-file", $StateFile, "--issue", $issue))
}

function Get-IssueDirFor([string]$issue) {
    return Join-Path $RunRoot ".run-with-it/issues/$issue"
}

function Mark-InProgress([string]$issue, [int]$processId, [string]$contextFile, [string]$logFile, [string]$doneFile, [string]$reportFile, [string]$issueDir) {
    Invoke-StateHelper @(
        "mark-in-progress",
        "--state-file", $StateFile,
        "--issue", $issue,
        "--pid", "$processId",
        "--context-file", $contextFile,
        "--log-file", $logFile,
        "--done-file", $doneFile,
        "--report-file", $reportFile,
        "--issue-dir", $issueDir
    ) | Out-Null
}

function Finalize-Issue([string]$issue, [string]$reportFile) {
    return [string](Invoke-StateHelper @("finalize-issue", "--state-file", $StateFile, "--issue", $issue, "--report-file", $reportFile))
}

function Analyze-SubCoordFailure([string]$issue, [string]$reportFile) {
    $json = Invoke-StateHelper @(
        "analyze-sub-coord-failure",
        "--state-file", $StateFile,
        "--issue", $issue,
        "--report-file", $reportFile,
        "--max-attempts", "$MaxSubCoordRecoveryAttempts"
    )
    return (($json -join "`n") | ConvertFrom-Json)
}

function Write-SubCoordRecoveryContext([string]$issue, [string]$contextFile, [int]$attempt, [string]$reason) {
    Invoke-StateHelper @(
        "write-sub-coord-recovery-context",
        "--state-file", $StateFile,
        "--issue", $issue,
        "--context-file", $contextFile,
        "--attempt", "$attempt",
        "--reason", $reason
    ) | Out-Null
}

function Mark-SubCoordRecoveryStarted([string]$issue, [int]$attempt, [string]$reason, [string]$contextFile) {
    Invoke-StateHelper @(
        "mark-sub-coord-recovery-started",
        "--state-file", $StateFile,
        "--issue", $issue,
        "--attempt", "$attempt",
        "--reason", $reason,
        "--context-file", $contextFile
    ) | Out-Null
}

function Mark-SubCoordRecoveryDispatchFailed([string]$issue, [string]$reportFile) {
    Invoke-StateHelper @(
        "mark-sub-coord-recovery-dispatch-failed",
        "--state-file", $StateFile,
        "--issue", $issue,
        "--report-file", $reportFile
    ) | Out-Null
}

function Write-MergeRecoveryContext([string]$issue, [string]$contextFile, [string]$recoveryReportFile) {
    Invoke-StateHelper @(
        "write-merge-recovery-context",
        "--state-file", $StateFile,
        "--issue", $issue,
        "--context-file", $contextFile,
        "--recovery-report-file", $recoveryReportFile
    ) | Out-Null
}

function Finalize-MergeRecovery([string]$issue, [string]$reportFile) {
    return [string](Invoke-StateHelper @("finalize-merge-recovery", "--state-file", $StateFile, "--issue", $issue, "--report-file", $reportFile))
}

function Mark-MergeRecoveryDispatchFailed([string]$issue, [string]$reportFile) {
    Invoke-StateHelper @("mark-merge-recovery-dispatch-failed", "--state-file", $StateFile, "--issue", $issue, "--report-file", $reportFile) | Out-Null
}

# Archive any stale sub-coordinator terminal markers before a (re)dispatch so the
# fresh runner never reuses a poisoned report and no-ops into a phantom "recovery
# dispatcher failed". Preserves sub-state.json / workers/ for resume.
function Quarantine-SubCoordMarkers([string]$issue, [string]$issueDir) {
    $archived = ([string](Invoke-StateHelper @("quarantine-sub-coord-markers", "--issue-dir", $issueDir))).Trim()
    if ($archived) {
        Write-Status "STATUS|type=sub-coord-markers-quarantined|issue=$issue|archive_dir=$archived"
    }
}

function Update-GitHubIssue([string]$issue, [string]$outcome, [string]$reportFile) {
    $lines = & $PythonExe $GitHubUpdateHelper update --state-file $StateFile --run-root $RunRoot --issue $issue --outcome $outcome --report-file $reportFile
    if ($LASTEXITCODE -ne 0) { Fail "GitHub update helper failed for issue $issue" }
    foreach ($line in @($lines)) {
        if ($line) { Write-Status $line }
    }
}

if ($ParallelJobs -le 0) {
    $ParallelJobs = [int](Invoke-StateHelper @("parallel-jobs", "--state-file", $StateFile))
}

# An unset env var passed as -PollSeconds binds as integer 0, which would
# busy-spin the monitor loop; fall back to the default.
if ($PollSeconds -le 0) { $PollSeconds = 10 }

foreach ($path in @($MainLog, $StatusFile, $EventsLog)) { Ensure-ParentDir $path }

if ($Detach -and -not $ValidateOnly -and -not $DryRun) {
    # Relaunch this script without -Detach as a durable supervisor process and
    # return after recording its PID so bounded tool calls can watch it.
    $relaunchArgs = @(
        "-NoProfile",
        "-File", $PSCommandPath,
        "-AssetRoot", $AssetRoot,
        "-StateFile", $StateFile,
        "-ParallelJobs", "$ParallelJobs",
        "-Agent", $Agent,
        "-Model", $Model,
        "-StatusFile", $StatusFile,
        "-EventsLog", $EventsLog,
        "-MainLog", $MainLog,
        "-PollSeconds", "$PollSeconds",
        "-TimeoutSeconds", "$TimeoutSeconds",
        "-MaxSubCoordRecoveryAttempts", "$MaxSubCoordRecoveryAttempts",
        "-PoolStateFile", $PoolStateFile
    )
    $poolProcess = Start-Process -FilePath $PowerShellExe -ArgumentList (Join-ProcessArguments $relaunchArgs) -PassThru
    Write-PoolState $poolProcess.Id
    Write-Status "STATUS|type=pool-detached|pid=$($poolProcess.Id)|pool_state_file=$PoolStateFile"
    exit 0
}

$readyInitial = @(Get-ReadyIssues $ParallelJobs)
Write-Status "STATUS|type=pool-ready|parallel_jobs=$ParallelJobs|ready=$($readyInitial.Count)|state_file=$StateFile"

if ($ValidateOnly) { exit 0 }

function Get-DispatchArgs([string]$issue, [string]$contextFile, [string]$issueDir, [string]$logFile, [string]$doneFile, [string]$reportFile, [string]$promptFile) {
    return @(
        "-NoProfile",
        "-File", $Dispatcher,
        "-AssetRoot", $AssetRoot,
        "-Role", "sub-coord",
        "-Issue", $issue,
        "-Agent", $Agent,
        "-Model", $Model,
        "-ContextFile", $contextFile,
        "-PromptFile", $promptFile,
        "-LogFile", $logFile,
        "-DoneFile", $doneFile,
        "-ResultFile", $reportFile,
        "-IssueDir", $issueDir,
        "-StatusFile", $StatusFile,
        "-EventsLog", $EventsLog,
        "-PollSeconds", "$PollSeconds",
        "-TimeoutSeconds", "$TimeoutSeconds"
    )
}

if ($DryRun) {
    foreach ($issue in $readyInitial) {
        $contextFile = Get-ContextFileFor $issue
        $issueDir = Get-IssueDirFor $issue
        $logFile = Join-Path $issueDir "sub-coordinator.log"
        $doneFile = Join-Path $issueDir "sub-coordinator.done"
        $reportFile = Join-Path $issueDir "report.json"
        Write-Output "$PowerShellExe $(Join-ProcessArguments (Get-DispatchArgs $issue $contextFile $issueDir $logFile $doneFile $reportFile $PromptFile))"
    }
    exit 0
}

$pool = @{}

function Spawn-Issue([string]$issue) {
    $contextFile = Get-ContextFileFor $issue
    if (-not (Test-Path $contextFile)) { Fail "context file missing for issue ${issue}: $contextFile" }
    $issueDir = Get-IssueDirFor $issue
    $logFile = Join-Path $issueDir "sub-coordinator.log"
    $doneFile = Join-Path $issueDir "sub-coordinator.done"
    $reportFile = Join-Path $issueDir "report.json"
    New-Item -ItemType Directory -Force -Path $issueDir | Out-Null
    Quarantine-SubCoordMarkers $issue $issueDir
    $args = Get-DispatchArgs $issue $contextFile $issueDir $logFile $doneFile $reportFile $PromptFile
    $stdoutFile = Join-Path $issueDir "sub-coordinator.dispatch.stdout.tmp"
    $stderrFile = Join-Path $issueDir "sub-coordinator.dispatch.stderr.tmp"
    $process = Start-Process -FilePath $PowerShellExe -ArgumentList (Join-ProcessArguments $args) -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile -PassThru
    $pool[$issue] = [pscustomobject]@{ process = $process; report_file = $reportFile; stdout_file = $stdoutFile; stderr_file = $stderrFile }
    Mark-InProgress $issue $process.Id $contextFile $logFile $doneFile $reportFile $issueDir
    Write-Status "STATUS|type=sub-coord-spawn|issue=$issue|pid=$($process.Id)|agent=$Agent|model=$Model|pool_size=$($pool.Count)|parallel_jobs=$ParallelJobs"
}

function Spawn-RecoveryIssue([string]$issue, $decision, $oldEntry) {
    $issueDir = Get-IssueDirFor $issue
    $attempt = [int]$decision.recovery_attempt
    $reason = [string]$decision.reason
    $contextFile = Join-Path $issueDir "sub-coordinator-recovery-$attempt-context.md"
    $logFile = Join-Path $issueDir "sub-coordinator-recovery-$attempt.log"
    $doneFile = Join-Path $issueDir "sub-coordinator-recovery-$attempt.done"
    $reportFile = Join-Path $issueDir "report.json"
    New-Item -ItemType Directory -Force -Path $issueDir | Out-Null
    Quarantine-SubCoordMarkers $issue $issueDir
    Write-SubCoordRecoveryContext $issue $contextFile $attempt $reason
    Mark-SubCoordRecoveryStarted $issue $attempt $reason $contextFile
    if ($oldEntry) { Remove-ProcessCapture $oldEntry }
    $args = Get-DispatchArgs $issue $contextFile $issueDir $logFile $doneFile $reportFile $PromptFile
    $stdoutFile = Join-Path $issueDir "sub-coordinator-recovery-$attempt.dispatch.stdout.tmp"
    $stderrFile = Join-Path $issueDir "sub-coordinator-recovery-$attempt.dispatch.stderr.tmp"
    $process = Start-Process -FilePath $PowerShellExe -ArgumentList (Join-ProcessArguments $args) -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile -PassThru
    $pool[$issue] = [pscustomobject]@{ process = $process; report_file = $reportFile; stdout_file = $stdoutFile; stderr_file = $stderrFile }
    Mark-InProgress $issue $process.Id $contextFile $logFile $doneFile $reportFile $issueDir
    Write-Status "STATUS|type=sub-coord-recovery-spawn|issue=$issue|attempt=$attempt|pid=$($process.Id)|agent=$Agent|model=$Model|reason=$reason"
}

function Finalize-PoolIssue([string]$issue, [string]$reportFile, $entry) {
    $outcome = Finalize-Issue $issue $reportFile
    if ($entry) { Remove-ProcessCapture $entry }
    $pool.Remove($issue)
    Write-Status "STATUS|type=sub-coord-complete|issue=$issue|outcome=$outcome|report_file=$reportFile"
    if ($outcome -eq "merge_recovery") {
        Run-MergeRecovery $issue
    } else {
        Update-GitHubIssue $issue $outcome $reportFile
    }
    Fill-FreeSlots $issue
}

function Handle-SubCoordExit([string]$issue, $entry) {
    $reportFile = $entry.report_file
    $decision = Analyze-SubCoordFailure $issue $reportFile
    $action = [string]$decision.action
    $reason = [string]$decision.reason
    if ($action -eq "wait_worker") {
        Write-Status "STATUS|type=sub-coord-recovery-wait|issue=$issue|role=$($decision.worker_role)|worker_state=$($decision.worker_state)|state_file=$($decision.worker_state_file)|reason=$reason"
        return
    }
    if ($action -eq "spawn_recovery") {
        Spawn-RecoveryIssue $issue $decision $entry
        return
    }
    if ($action -eq "finalize") {
        Finalize-PoolIssue $issue $reportFile $entry
        return
    }
    if ($action -ne "block") {
        Write-Status "STATUS|type=sub-coord-recovery-analysis-failed|issue=$issue|action=$action|reason=$reason"
    }
    Mark-SubCoordRecoveryDispatchFailed $issue $reportFile
    Finalize-PoolIssue $issue $reportFile $entry
}

function Fill-FreeSlots([string]$reason) {
    $freeSlots = $ParallelJobs - $pool.Count
    if ($freeSlots -le 0) { return }
    foreach ($queuedIssue in @(Get-ReadyIssues $freeSlots)) {
        if ($pool.ContainsKey($queuedIssue)) { continue }
        Spawn-Issue $queuedIssue
        Write-Status "STATUS|type=pool-slot-filled|issue=$queuedIssue|freed_by=$reason|pool_size=$($pool.Count)"
    }
}

function Run-MergeRecovery([string]$issue) {
    $issueDir = Get-IssueDirFor $issue
    $contextFile = Join-Path $issueDir "merge-recovery-context.md"
    $logFile = Join-Path $issueDir "merge-recovery.log"
    $doneFile = Join-Path $issueDir "merge-recovery.done"
    $reportFile = Join-Path $issueDir "merge-recovery-report.json"
    New-Item -ItemType Directory -Force -Path $issueDir | Out-Null
    Write-MergeRecoveryContext $issue $contextFile $reportFile
    Write-Status "STATUS|type=merge-recovery|issue=$issue|report_file=$reportFile|state=started"
    $args = @(
        "-NoProfile",
        "-File", $Dispatcher,
        "-AssetRoot", $AssetRoot,
        "-Role", "merge-recovery",
        "-Issue", $issue,
        "-Agent", $Agent,
        "-Model", $Model,
        "-ContextFile", $contextFile,
        "-PromptFile", $MergeRecoveryPromptFile,
        "-LogFile", $logFile,
        "-DoneFile", $doneFile,
        "-ResultFile", $reportFile,
        "-IssueDir", $issueDir,
        "-StatusFile", $StatusFile,
        "-EventsLog", $EventsLog,
        "-PollSeconds", "$PollSeconds",
        "-TimeoutSeconds", "$TimeoutSeconds"
    )
    $stdoutFile = Join-Path $issueDir "merge-recovery.dispatch.stdout.tmp"
    $stderrFile = Join-Path $issueDir "merge-recovery.dispatch.stderr.tmp"
    $process = Start-Process -FilePath $PowerShellExe -ArgumentList (Join-ProcessArguments $args) -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile -PassThru
    $process.WaitForExit()
    Remove-Item -Force $stdoutFile -ErrorAction SilentlyContinue
    Remove-Item -Force $stderrFile -ErrorAction SilentlyContinue
    $status = Finalize-MergeRecovery $issue $reportFile
    if ($process.ExitCode -ne 0 -and $status -eq "completed") {
        $status = "blocked"
        Mark-MergeRecoveryDispatchFailed $issue $reportFile
    }
    Write-Status "STATUS|type=merge-recovery|issue=$issue|report_file=$reportFile|state=$status"
    Update-GitHubIssue $issue $status $reportFile
}

# Surface concurrency-gate deferrals so a serialized pool is visible in the
# status bus instead of silently degrading to one issue at a time.
$lastAdmissionDeferrals = ""
function Emit-AdmissionDeferrals {
    $deferrals = ([string](Invoke-StateHelper @("admission-deferrals", "--state-file", $StateFile))).Trim()
    if ($deferrals -and $deferrals -ne $script:lastAdmissionDeferrals) {
        $count = @($deferrals -split ",").Count
        Write-Status "STATUS|type=pool-admission-deferred|count=$count|deferrals=$deferrals"
    }
    $script:lastAdmissionDeferrals = $deferrals
}

# Re-adopt in-flight issues from a previous pool supervisor (e.g. one killed by
# a bounded tool-call timeout). Live dispatchers keep running unsupervised; the
# monitor loop below re-attaches to them, and dead ones flow through the normal
# exit analysis on the first tick instead of producing a false pool-empty.
function Reattach-ActivePool {
    $reattached = 0
    foreach ($line in @(Invoke-StateHelper @("active-pool-entries", "--state-file", $StateFile))) {
        if (-not $line) { continue }
        $fields = "$line" -split "`t"
        $issue = $fields[0]
        if (-not $issue) { continue }
        $processId = if ($fields.Count -gt 1 -and $fields[1]) { [int]$fields[1] } else { 0 }
        $reportFile = if ($fields.Count -gt 2 -and $fields[2]) { $fields[2] } else { Join-Path (Get-IssueDirFor $issue) "report.json" }
        $process = $null
        if ($processId -gt 0) {
            $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        }
        $pool[$issue] = [pscustomobject]@{ process = $process; report_file = $reportFile; stdout_file = $null; stderr_file = $null }
        $reattached++
        Write-Status "STATUS|type=sub-coord-reattach|issue=$issue|pid=$processId|report_file=$reportFile"
    }
    if ($reattached -gt 0) {
        Write-Status "STATUS|type=pool-reattached|count=$reattached|state_file=$StateFile"
    }
}

Write-PoolState $PID
Reattach-ActivePool
Fill-FreeSlots "startup"
Emit-AdmissionDeferrals

$lastWaitingContextCount = ""
while ($pool.Count -gt 0) {
    Start-Sleep -Seconds $PollSeconds
    foreach ($issue in @($pool.Keys)) {
        $process = $pool[$issue].process
        if ($process) {
            $process.Refresh()
            if (-not $process.HasExited) { continue }
        }
        Handle-SubCoordExit $issue $pool[$issue]
    }
    Fill-FreeSlots "tick"
    $waitingCount = Get-ReadyMissingContextCount
    if ($waitingCount -ne 0 -and "$waitingCount" -ne "$lastWaitingContextCount") {
        Write-Status "STATUS|type=pool-waiting-context|count=$waitingCount|state_file=$StateFile"
    }
    $lastWaitingContextCount = "$waitingCount"
    Emit-AdmissionDeferrals
}

Write-Status "STATUS|type=pool-empty|state_file=$StateFile"
