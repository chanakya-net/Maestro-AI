param(
    [string]$RunRoot = "",
    [string]$PoolStateFile = "",
    [int]$TermWaitSeconds = 10,
    [string]$MatchPattern = "run-with-it-pool|run-with-it-dispatch|run-agent"
)

# Identity-checked shutdown of every detached run-with-it process for one run:
# the pool supervisor, every dispatcher, and every runner — including nested
# worker dispatchers and orphaned runners whose dispatcher already exited.
#
# Killing a dispatcher PID alone leaves its runner and provider CLI alive, so
# this helper terminates whole Windows process TREES: for each PID recorded in
# run state (pool_pid, dispatcher_pid, runner_pid) it verifies the process
# identity via its command line, expands descendants through Win32_Process
# ParentProcessId, stops every node, and verifies termination.
#
# Exit codes: 0 — every targeted process is gone (or none were ours/alive);
#             2 — usage/environment error;
#             3 — survivors remain; callers must refuse destructive follow-up
#                 actions (e.g. discard) and report the PIDs.

$ErrorActionPreference = "Stop"

function Fail([string]$message) {
    [Console]::Error.WriteLine("run-with-it-stop.ps1: $message")
    exit 2
}

if (-not $RunRoot) { Fail "-RunRoot is required" }
if (-not (Test-Path $RunRoot)) { Fail "run root not found: $RunRoot" }
if ($TermWaitSeconds -lt 0) { $TermWaitSeconds = 10 }
if (-not $PoolStateFile) {
    $PoolStateFile = Join-Path (Join-Path (Join-Path $RunRoot ".run-with-it") "main") "pool.state.json"
}

function Read-JsonFile([string]$path) {
    if (-not (Test-Path $path)) { return $null }
    try { return (Get-Content $path -Raw | ConvertFrom-Json) } catch { return $null }
}

# Collect deduplicated source/pid pairs from the pool state file and every
# dispatcher state file under the run root.
function Collect-Targets {
    $targets = @()
    $seen = @{}
    function Add-Target([string]$source, $pid_) {
        $parsed = 0
        if (-not [int]::TryParse("$pid_", [ref]$parsed)) { return }
        if ($parsed -le 1 -or $script:seen.ContainsKey($parsed)) { return }
        $script:seen[$parsed] = $true
        $script:targets += [pscustomobject]@{ Source = $source; ProcessId = $parsed }
    }
    $script:targets = $targets
    $script:seen = $seen

    $pool = Read-JsonFile $PoolStateFile
    if ($pool) { Add-Target "pool" $pool.pool_pid }

    $issueGlobs = @(
        (Join-Path $RunRoot ".run-with-it/issues/*/*.state.json"),
        (Join-Path $RunRoot ".run-with-it/issues/*/workers/*/*.state.json")
    )
    foreach ($glob in $issueGlobs) {
        foreach ($file in @(Get-ChildItem -Path $glob -ErrorAction SilentlyContinue)) {
            $data = Read-JsonFile $file.FullName
            if ($data) {
                Add-Target "dispatcher" $data.dispatcher_pid
                Add-Target "runner" $data.runner_pid
            }
        }
    }
    return $script:targets
}

function Get-CommandLine([int]$processId) {
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
        if ($proc) { return [string]$proc.CommandLine }
    } catch {}
    try {
        $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($proc) { return [string]$proc.Path }
    } catch {}
    return ""
}

function Get-ProcessTree([int]$rootId) {
    $tree = @($rootId)
    $frontier = @($rootId)
    while ($frontier.Count -gt 0) {
        $next = @()
        foreach ($parent in $frontier) {
            $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$parent" -ErrorAction SilentlyContinue)
            foreach ($child in $children) {
                if ($tree -notcontains [int]$child.ProcessId) {
                    $tree += [int]$child.ProcessId
                    $next += [int]$child.ProcessId
                }
            }
        }
        $frontier = $next
    }
    return $tree
}

function Test-Alive([int]$processId) {
    return [bool](Get-Process -Id $processId -ErrorAction SilentlyContinue)
}

$terminated = 0
$alreadyDead = 0
$skippedNotOurs = 0
$targetPids = @()

foreach ($target in @(Collect-Targets)) {
    $processId = $target.ProcessId
    if (-not (Test-Alive $processId)) {
        $alreadyDead++
        Write-Output "STOP|type=target|source=$($target.Source)|pid=$processId|action=already-dead"
        continue
    }
    $commandLine = Get-CommandLine $processId
    if ($commandLine -notmatch $MatchPattern) {
        $skippedNotOurs++
        Write-Output "STOP|type=target|source=$($target.Source)|pid=$processId|action=skip-not-ours"
        continue
    }
    $tree = @(Get-ProcessTree $processId)
    foreach ($node in $tree) {
        if ($targetPids -notcontains $node) { $targetPids += $node }
    }
    Write-Output "STOP|type=target|source=$($target.Source)|pid=$processId|tree_size=$($tree.Count)|action=stop-tree"
    foreach ($node in $tree) {
        Stop-Process -Id $node -ErrorAction SilentlyContinue
    }
    $terminated++
}

$waited = 0
while ($waited -lt $TermWaitSeconds -and @($targetPids | Where-Object { Test-Alive $_ }).Count -gt 0) {
    Start-Sleep -Seconds 1
    $waited++
}

$stillAlive = @($targetPids | Where-Object { Test-Alive $_ })
if ($stillAlive.Count -gt 0) {
    foreach ($node in $stillAlive) {
        Stop-Process -Id $node -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
}

$survivors = @($targetPids | Where-Object { Test-Alive $_ })
if ($survivors.Count -gt 0) {
    Write-Output "STOP|result=survivors|terminated=$terminated|already_dead=$alreadyDead|skipped_not_ours=$skippedNotOurs|survivors=$($survivors -join ',')"
    exit 3
}

Write-Output "STOP|result=clean|terminated=$terminated|already_dead=$alreadyDead|skipped_not_ours=$skippedNotOurs"
