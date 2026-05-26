param(
    [string]$AssetRoot = $env:ASSETS_DEST,
    [string]$StateFile = (Join-Path (Join-Path (Get-Location).Path ".run-with-it") "main-state.json"),
    [int]$ParallelJobs = 0,
    [string]$Agent = $(if ($env:SUB_COORD_AGENT) { $env:SUB_COORD_AGENT } else { "codex" }),
    [string]$Model = $(if ($env:SUB_COORD_MODEL) { $env:SUB_COORD_MODEL } else { "gpt-5.5" }),
    [string]$StatusFile = $(if ($env:RUN_WITH_IT_STATUS_FILE) { $env:RUN_WITH_IT_STATUS_FILE } else { Join-Path (Join-Path (Join-Path (Get-Location).Path ".run-with-it") "status") "current.txt" }),
    [string]$EventsLog = $(if ($env:RUN_WITH_IT_EVENTS_LOG) { $env:RUN_WITH_IT_EVENTS_LOG } else { Join-Path (Join-Path (Join-Path (Get-Location).Path ".run-with-it") "status") "events.log" }),
    [string]$MainLog = (Join-Path (Join-Path (Join-Path (Get-Location).Path ".run-with-it") "main") "main.log"),
    [int]$PollSeconds = $(if ($env:STATUS_POLL_SECONDS) { [int]$env:STATUS_POLL_SECONDS } else { 10 }),
    [int]$TimeoutSeconds = $(if ($env:SUB_COORD_TIMEOUT_SECONDS) { [int]$env:SUB_COORD_TIMEOUT_SECONDS } else { 3600 }),
    [switch]$DryRun,
    [switch]$ValidateOnly
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

function Get-Prop($object, [string]$name, $default = $null) {
    if ($null -eq $object) { return $default }
    $prop = $object.PSObject.Properties[$name]
    if ($prop) { return $prop.Value }
    return $default
}

function Set-Prop($object, [string]$name, $value) {
    if ($object.PSObject.Properties[$name]) {
        $object.$name = $value
    } else {
        $object | Add-Member -NotePropertyName $name -NotePropertyValue $value
    }
}

function Read-State {
    return Get-Content -Path $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Save-State($state) {
    $tmpFile = "$StateFile.tmp.$PID"
    $state | ConvertTo-Json -Depth 30 | Set-Content -Path $tmpFile -Encoding UTF8
    Move-Item -Force $tmpFile $StateFile
}

function Write-Status([string]$line) {
    Write-Output $line
    foreach ($path in @($MainLog, $StatusFile, $EventsLog)) { Ensure-ParentDir $path }
    Add-Content -Path $MainLog -Value $line -Encoding UTF8
    Set-Content -Path $StatusFile -Value $line -Encoding UTF8
    Add-Content -Path $EventsLog -Value $line -Encoding UTF8
}

function Set-GitHubUpdateState([string]$issue, [string]$status, [string]$detail) {
    $state = Read-State
    $entry = Get-IssueEntry $state $issue
    Set-Prop $entry "github_update_status" $status
    Set-Prop $entry "github_update_detail" $detail
    Set-Prop $entry "github_updated_at" ([DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ"))
    Save-State $state
}

function Get-TokenTotal($tokens, [string]$kind) {
    if ($null -eq $tokens) { return $null }
    $total = 0
    $found = $false
    foreach ($prop in $tokens.PSObject.Properties) {
        $name = $prop.Name.ToLowerInvariant()
        if ($kind -eq "input" -and -not $name.Contains("input")) { continue }
        if ($kind -eq "output" -and -not $name.Contains("output")) { continue }
        if ($kind -eq "cache" -and -not $name.Contains("cache")) { continue }
        if ($prop.Value -is [int] -or $prop.Value -is [long] -or $prop.Value -is [double]) {
            $total += [int64]$prop.Value
            $found = $true
        }
    }
    if ($found) { return $total }
    return $null
}

function Format-Token($value) {
    if ($null -eq $value) { return "unknown" }
    return "$value"
}

function New-TerminalComment([string]$reportFile, [string]$fallbackOutcome) {
    $report = [pscustomobject]@{}
    if ((Test-Path $reportFile) -and ((Get-Item $reportFile).Length -gt 0)) {
        try { $report = Get-Content -Path $reportFile -Raw -Encoding UTF8 | ConvertFrom-Json } catch {}
    }
    $outcome = Get-Prop $report "outcome" $fallbackOutcome
    if (-not $outcome) { $outcome = "blocked" }
    $summary = Get-Prop $report "summary" "No summary provided."
    $verification = Get-Prop $report "verification" ([pscustomobject]@{})
    $commands = @(Get-Prop $verification "commands_run" @())
    $evidence = Get-Prop $verification "evidence" "unknown"
    $passed = Get-Prop $verification "passed" $null
    $verificationState = if ($passed -eq $true) { "passed" } elseif ($passed -eq $false) { "failed" } else { "unknown" }
    $commandText = if ($commands.Count -gt 0) { ($commands -join ", ") } else { "unknown" }
    $review = Get-Prop $report "review_summary" ([pscustomobject]@{})
    $cycles = Get-Prop $review "cycles_used" $null
    $final = Get-Prop $review "final_verdict" "unknown"
    $reviewer = Get-Prop $review "reviewer_model" "unknown"
    if ($null -eq $cycles) {
        $reviewLine = "Review: unknown, final verdict: $final, reviewer model: $reviewer"
    } elseif ([int]$cycles -le 1 -and $final -eq "approve") {
        $reviewLine = "Review: approve (1 cycle), final verdict: $final, reviewer model: $reviewer"
    } else {
        $reviewLine = "Review: revise ($cycles cycles), final verdict: $final, reviewer model: $reviewer"
    }
    $tokens = Get-Prop $report "token_usage" ([pscustomobject]@{})
    $lines = @(
        "## Status",
        "$outcome",
        "",
        "## Summary",
        "$summary",
        "",
        "## Verification",
        "State: $verificationState",
        "Commands: $commandText",
        "Evidence: $evidence",
        "",
        "## Token Usage",
        "- Input tokens: $(Format-Token (Get-TokenTotal $tokens "input"))",
        "- Output tokens: $(Format-Token (Get-TokenTotal $tokens "output"))",
        "- Cache hit tokens: $(Format-Token (Get-TokenTotal $tokens "cache"))",
        "",
        "## Notes",
        "$reviewLine"
    )
    $commit = Get-Prop $report "commit_sha" $null
    if ($commit) { $lines += "Commit: $commit" }
    $merge = Get-Prop $report "merge" ([pscustomobject]@{})
    $mergeSha = Get-Prop $merge "merge_sha" $null
    if ($mergeSha) { $lines += "Merge: $mergeSha" }
    $blocking = @(Get-Prop $report "blocking_reasons" @())
    if ($blocking.Count -gt 0) {
        $lines += ""
        $lines += "## Blocking Reasons"
        foreach ($reason in $blocking) { $lines += "- $reason" }
    }
    return ($lines -join [Environment]::NewLine)
}

function Update-GitHubIssue([string]$issue, [string]$outcome, [string]$reportFile) {
    $closeIssue = $false
    if ($outcome -eq "completed") {
        $closeIssue = $true
    } elseif ($outcome -notin @("blocked", "failed-review", "failed-merge")) {
        return
    }
    if ($env:RUN_WITH_IT_GITHUB_UPDATES -eq "0") {
        Set-GitHubUpdateState $issue "skipped" "disabled"
        Write-Status "STATUS|type=github-update|issue=$issue|outcome=$outcome|action=skipped|reason=disabled"
        return
    }
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        Set-GitHubUpdateState $issue "skipped" "gh-not-found"
        Write-Status "STATUS|type=github-update|issue=$issue|outcome=$outcome|action=skipped|reason=gh-not-found"
        return
    }
    $remoteOutput = ""
    try { $remoteOutput = & git -C $runRoot remote -v 2>$null | Out-String } catch {}
    if ($remoteOutput -notmatch "github\.com") {
        Set-GitHubUpdateState $issue "skipped" "no-github-remote"
        Write-Status "STATUS|type=github-update|issue=$issue|outcome=$outcome|action=skipped|reason=no-github-remote"
        return
    }
    $commentFile = Join-Path ([System.IO.Path]::GetTempPath()) "run-with-it-comment-$PID-$issue.md"
    try {
        New-TerminalComment $reportFile $outcome | Set-Content -Path $commentFile -Encoding UTF8
        Push-Location $runRoot
        try {
            & gh issue comment $issue --body-file $commentFile | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "comment failed" }
            if ($closeIssue) {
                & gh issue close $issue | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "close failed" }
            }
        } finally {
            Pop-Location
        }
        Set-GitHubUpdateState $issue "updated" "commented;closed=$($closeIssue.ToString().ToLowerInvariant())"
        Write-Status "STATUS|type=github-update|issue=$issue|outcome=$outcome|action=commented|closed=$($closeIssue.ToString().ToLowerInvariant())"
    } catch {
        Set-GitHubUpdateState $issue "failed" "$_"
        Write-Status "STATUS|type=github-update|issue=$issue|outcome=$outcome|action=failed|reason=gh-failed"
    } finally {
        Remove-Item -Force $commentFile -ErrorAction SilentlyContinue
    }
}

function Remove-ProcessCapture($entry) {
    foreach ($path in @((Get-Prop $entry "stdout_file"), (Get-Prop $entry "stderr_file"))) {
        if ($path) { Remove-Item -Force $path -ErrorAction SilentlyContinue }
    }
}

function Get-IssueEntry($state, [string]$issue) {
    $registry = Get-Prop $state "issue_registry"
    if ($null -eq $registry) {
        $registry = [pscustomobject]@{}
        Set-Prop $state "issue_registry" $registry
    }
    $prop = $registry.PSObject.Properties[$issue]
    if ($prop) { return $prop.Value }
    $entry = [pscustomobject]@{}
    $registry | Add-Member -NotePropertyName $issue -NotePropertyValue $entry
    return $entry
}

function Get-ReadyIssues([int]$limit) {
    $state = Read-State
    $registry = Get-Prop $state "issue_registry" ([pscustomobject]@{})
    $completed = @{}
    foreach ($prop in $registry.PSObject.Properties) {
        if ((Get-Prop $prop.Value "status") -eq "completed") {
            $completed[[int]$prop.Name] = $true
        }
    }
    $topoOrder = @(Get-Prop (Get-Prop $state "execution_plan" ([pscustomobject]@{})) "topo_order" @())
    $ready = @()
    foreach ($issueNumber in $topoOrder) {
        if ($ready.Count -ge $limit) { break }
        $entry = Get-Prop $registry ([string]$issueNumber)
        if ($null -eq $entry) { continue }
        if ((Get-Prop $entry "status") -ne "pending") { continue }
        $deps = @(Get-Prop $entry "deps" @())
        $depsReady = $true
        foreach ($dep in $deps) {
            if (-not $completed.ContainsKey([int]$dep)) {
                $depsReady = $false
                break
            }
        }
        if (-not $depsReady) { continue }
        $contextFile = Get-Prop $entry "context_file"
        if (-not $contextFile) { $contextFile = Get-Prop $entry "sub_coord_context_file" }
        if ($contextFile) { $ready += [string]$issueNumber }
    }
    return $ready
}

function Get-ReadyMissingContextCount {
    $state = Read-State
    $registry = Get-Prop $state "issue_registry" ([pscustomobject]@{})
    $completed = @{}
    foreach ($prop in $registry.PSObject.Properties) {
        if ((Get-Prop $prop.Value "status") -eq "completed") {
            $completed[[int]$prop.Name] = $true
        }
    }
    $topoOrder = @(Get-Prop (Get-Prop $state "execution_plan" ([pscustomobject]@{})) "topo_order" @())
    $count = 0
    foreach ($issueNumber in $topoOrder) {
        $entry = Get-Prop $registry ([string]$issueNumber)
        if ($null -eq $entry) { continue }
        if ((Get-Prop $entry "status") -ne "pending") { continue }
        $depsReady = $true
        foreach ($dep in @(Get-Prop $entry "deps" @())) {
            if (-not $completed.ContainsKey([int]$dep)) {
                $depsReady = $false
                break
            }
        }
        if (-not $depsReady) { continue }
        $contextFile = Get-Prop $entry "context_file"
        if (-not $contextFile) { $contextFile = Get-Prop $entry "sub_coord_context_file" }
        if (-not $contextFile) { $count++ }
    }
    return $count
}

function Get-ContextFileFor([string]$issue) {
    $state = Read-State
    $entry = Get-IssueEntry $state $issue
    $contextFile = Get-Prop $entry "context_file"
    if (-not $contextFile) { $contextFile = Get-Prop $entry "sub_coord_context_file" }
    return $contextFile
}

function Get-IssueDirFor([string]$issue) {
    return Join-Path (Join-Path (Join-Path $RunRoot ".run-with-it") "issues") $issue
}

function Mark-InProgress([string]$issue, [int]$processId, [string]$contextFile, [string]$logFile, [string]$doneFile, [string]$reportFile, [string]$issueDir) {
    $state = Read-State
    $entry = Get-IssueEntry $state $issue
    Set-Prop $entry "status" "in_progress"
    Set-Prop $entry "context_file" $contextFile
    Set-Prop $entry "issue_dir" $issueDir
    Set-Prop $entry "pid" $processId
    Set-Prop $entry "started_at" ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())
    Set-Prop $entry "log_file" $logFile
    Set-Prop $entry "done_file" $doneFile
    Set-Prop $entry "report_file" $reportFile
    $active = @(Get-Prop $state "active_pool_issues" @() | ForEach-Object { [string]$_ })
    if ($active -notcontains $issue) { $active += $issue }
    Set-Prop $state "active_pool_issues" $active
    Save-State $state
}

function Finalize-Issue([string]$issue, [string]$reportFile) {
    $outcome = "blocked"
    $report = [pscustomobject]@{}
    if ((Test-Path $reportFile) -and ((Get-Item $reportFile).Length -gt 0)) {
        try {
            $report = Get-Content -Path $reportFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $outcome = Get-Prop $report "outcome" "blocked"
        } catch {
            $outcome = "blocked"
        }
    }
    $status = if ($outcome -eq "merge_failed") { "merge_recovery" } else { $outcome }
    $state = Read-State
    $entry = Get-IssueEntry $state $issue
    Set-Prop $entry "status" $status
    if ($outcome -eq "merge_failed") {
        Set-Prop $entry "failed_merge_report_file" $reportFile
        $reasons = @(Get-Prop $entry "blocking_reasons" @())
        if ($reasons -notcontains "merge recovery required") { $reasons += "merge recovery required" }
        Set-Prop $entry "blocking_reasons" $reasons
    }
    $active = @(Get-Prop $state "active_pool_issues" @() | ForEach-Object { [string]$_ } | Where-Object { $_ -ne $issue })
    Set-Prop $state "active_pool_issues" $active
    $summary = [ordered]@{
        issue = [int]$issue
        outcome = $status
        files_modified_count = Get-Prop $report "files_modified_count" 0
        lines_added = Get-Prop $report "lines_added" 0
        lines_deleted = Get-Prop $report "lines_deleted" 0
        review_cycles = Get-Prop $report "review_cycles" 0
        commit_sha = Get-Prop $report "commit_sha" $null
    }
    if ($status -ne "merge_recovery") {
        Set-Prop $state "completed_summaries" (@(Get-Prop $state "completed_summaries" @()) + $summary)
    } else {
        Set-Prop $state "merge_recovery_summaries" (@(Get-Prop $state "merge_recovery_summaries" @()) + $summary)
    }
    Set-Prop $state "ledger_rows" (@(Get-Prop $state "ledger_rows" @()) + "STATUS|type=ledger|task=$issue|outcome=$status|report=$reportFile")
    Save-State $state
    return $status
}

function Write-MergeRecoveryContext([string]$issue, [string]$contextFile, [string]$recoveryReportFile) {
    $state = Read-State
    $entry = Get-IssueEntry $state $issue
    $payload = [ordered]@{
        issue = [ordered]@{
            number = [int]$issue
            title = Get-Prop $entry "title" ""
            deps = @(Get-Prop $entry "deps" @())
            issue_branch = Get-Prop $entry "issue_branch" $null
            worktree_path = Get-Prop $entry "worktree_path" $null
        }
        run_branch = Get-Prop $state "run_branch" ([pscustomobject]@{})
        failed_merge_report_file = $(if (Get-Prop $entry "failed_merge_report_file") { Get-Prop $entry "failed_merge_report_file" } else { Get-Prop $entry "report_file" })
        failed_merge_summary = [ordered]@{
            blocking_reasons = @(Get-Prop $entry "blocking_reasons" @())
            dependency_proof = Get-Prop $entry "dependency_proof" $null
        }
        completed_summaries = @(Get-Prop $state "completed_summaries" @())
    }
    Ensure-ParentDir $contextFile
    @(
        "You are receiving merge recovery task data only.",
        "Resolve only the failed merge for this issue. Do not select new issues, close GitHub issues, create a final PR, or modify main-state.json.",
        "",
        "MERGE_RECOVERY_REPORT_FILE=$recoveryReportFile",
        "RUN_WITH_IT_RESULT_FILE=$recoveryReportFile",
        "OUTCOME=completed",
        "",
        "MERGE_RECOVERY_CONTEXT_JSON:",
        ($payload | ConvertTo-Json -Depth 20)
    ) | Set-Content -Path $contextFile -Encoding UTF8
}

function Finalize-MergeRecovery([string]$issue, [string]$reportFile) {
    $outcome = "blocked"
    $report = [pscustomobject]@{}
    if ((Test-Path $reportFile) -and ((Get-Item $reportFile).Length -gt 0)) {
        try {
            $report = Get-Content -Path $reportFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $outcome = Get-Prop $report "outcome" "blocked"
        } catch {
            $outcome = "blocked"
        }
    }
    $status = if ($outcome -eq "completed") { "completed" } elseif ($outcome -in @("failed-merge", "blocked")) { $outcome } else { "blocked" }
    $state = Read-State
    $entry = Get-IssueEntry $state $issue
    Set-Prop $entry "status" $status
    Set-Prop $entry "merge_recovery_report_file" $reportFile
    if ($status -eq "completed") {
        $reasons = @(Get-Prop $entry "blocking_reasons" @() | Where-Object { $_ -ne "merge recovery required" })
        Set-Prop $entry "blocking_reasons" $reasons
        Set-Prop $entry "commit_sha" $(if (Get-Prop $report "merge_sha") { Get-Prop $report "merge_sha" } else { Get-Prop $report "commit_sha" $null })
    } else {
        Set-Prop $entry "blocking_reasons" (@(Get-Prop $entry "blocking_reasons" @()) + @(Get-Prop $report "blocking_reasons" @()))
    }
    $files = @(Get-Prop $report "files_modified" @())
    $summary = [ordered]@{
        issue = [int]$issue
        outcome = $status
        files_modified_count = Get-Prop $report "files_modified_count" $files.Count
        lines_added = Get-Prop $report "lines_added" 0
        lines_deleted = Get-Prop $report "lines_deleted" 0
        review_cycles = Get-Prop $report "review_cycles" 0
        commit_sha = $(if (Get-Prop $report "merge_sha") { Get-Prop $report "merge_sha" } else { Get-Prop $report "commit_sha" $null })
    }
    if ($status -eq "completed") {
        Set-Prop $state "completed_summaries" (@(Get-Prop $state "completed_summaries" @()) + $summary)
    } else {
        Set-Prop $state "merge_recovery_summaries" (@(Get-Prop $state "merge_recovery_summaries" @()) + $summary)
    }
    Set-Prop $state "ledger_rows" (@(Get-Prop $state "ledger_rows" @()) + "STATUS|type=ledger|task=$issue|outcome=$status|report=$reportFile|role=merge-recovery")
    Save-State $state
    return $status
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

if (-not (Test-Path $Dispatcher)) { Fail "dispatcher not found: $Dispatcher" }
if (-not (Test-Path $PromptFile)) { Fail "sub-coordinator prompt not found: $PromptFile" }
if (-not (Test-Path $MergeRecoveryPromptFile)) { Fail "merge recovery prompt not found: $MergeRecoveryPromptFile" }
if (-not (Test-Path $StateFile)) { Fail "state file not found: $StateFile" }

$RunRoot = (Resolve-Path (Join-Path (Split-Path $StateFile) "..")).Path
$PowerShellExe = Get-PowerShellExe

if ($ParallelJobs -le 0) {
    $state = Read-State
    $ParallelJobs = [int](Get-Prop (Get-Prop $state "execution_plan" ([pscustomobject]@{})) "parallel_jobs" 4)
}

foreach ($path in @($MainLog, $StatusFile, $EventsLog)) { Ensure-ParentDir $path }

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
    $args = Get-DispatchArgs $issue $contextFile $issueDir $logFile $doneFile $reportFile $PromptFile
    $stdoutFile = Join-Path $issueDir "sub-coordinator.dispatch.stdout.tmp"
    $stderrFile = Join-Path $issueDir "sub-coordinator.dispatch.stderr.tmp"
    $process = Start-Process -FilePath $PowerShellExe -ArgumentList (Join-ProcessArguments $args) -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile -PassThru
    $pool[$issue] = [pscustomobject]@{ process = $process; report_file = $reportFile; stdout_file = $stdoutFile; stderr_file = $stderrFile }
    Mark-InProgress $issue $process.Id $contextFile $logFile $doneFile $reportFile $issueDir
    Write-Status "STATUS|type=sub-coord-spawn|issue=$issue|pid=$($process.Id)|agent=$Agent|model=$Model|pool_size=$($pool.Count)|parallel_jobs=$ParallelJobs"
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
        $state = Read-State
        $entry = Get-IssueEntry $state $issue
        Set-Prop $entry "status" "blocked"
        Set-Prop $entry "merge_recovery_report_file" $reportFile
        Set-Prop $entry "blocking_reasons" (@(Get-Prop $entry "blocking_reasons" @()) + "merge recovery dispatcher failed")
        Save-State $state
        $status = "blocked"
    }
    Write-Status "STATUS|type=merge-recovery|issue=$issue|report_file=$reportFile|state=$status"
    Update-GitHubIssue $issue $status $reportFile
}

foreach ($issue in $readyInitial) {
    Spawn-Issue $issue
}

$lastWaitingContextCount = ""
while ($pool.Count -gt 0) {
    Start-Sleep -Seconds $PollSeconds
    foreach ($issue in @($pool.Keys)) {
        $process = $pool[$issue].process
        $process.Refresh()
        if (-not $process.HasExited) { continue }
        $reportFile = $pool[$issue].report_file
        $outcome = Finalize-Issue $issue $reportFile
        Remove-ProcessCapture $pool[$issue]
        $pool.Remove($issue)
        Write-Status "STATUS|type=sub-coord-complete|issue=$issue|outcome=$outcome|report_file=$reportFile"
        if ($outcome -eq "merge_recovery") {
            Run-MergeRecovery $issue
        } else {
            Update-GitHubIssue $issue $outcome $reportFile
        }
        Fill-FreeSlots $issue
    }
    Fill-FreeSlots "tick"
    $waitingCount = Get-ReadyMissingContextCount
    if ($waitingCount -ne 0 -and "$waitingCount" -ne "$lastWaitingContextCount") {
        Write-Status "STATUS|type=pool-waiting-context|count=$waitingCount|state_file=$StateFile"
    }
    $lastWaitingContextCount = "$waitingCount"
}

Write-Status "STATUS|type=pool-empty|state_file=$StateFile"
