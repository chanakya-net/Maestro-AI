param(
    [string]$EventsLog = "",
    [string]$PoolStateFile = "",
    [string]$CursorFile = "",
    [int]$WaitSeconds = $(if ($env:POOL_WATCH_SECONDS) { [int]$env:POOL_WATCH_SECONDS } else { 240 }),
    [int]$PollSeconds = $(if ($env:STATUS_POLL_SECONDS) { [int]$env:STATUS_POLL_SECONDS } else { 10 }),
    [string]$MatchPattern = $(if ($env:RUN_WITH_IT_POOL_PATTERN) { $env:RUN_WITH_IT_POOL_PATTERN } else { "run-with-it-pool" })
)

# Bounded foreground watch over the run-with-it status bus. The Main
# Coordinator calls this repeatedly instead of blocking on the pool runner:
# each call prints status lines appended to the events log since the previous
# call (cursor persisted on disk), then exits well before any tool-call
# timeout. The final line is always a WATCH|result=... marker:
#   WATCH|result=pool-empty  — pool finished; Step D is complete (exit 0)
#   WATCH|result=running     — pool alive, watch window elapsed; call again (exit 0)
#   WATCH|result=pool-dead   — pool supervisor gone without pool-empty; relaunch
#                              the pool runner, it re-attaches (exit 3)

$ErrorActionPreference = "Stop"

function Fail([string]$message) {
    [Console]::Error.WriteLine("run-with-it-watch.ps1: $message")
    exit 2
}

if (-not $EventsLog) { Fail "-EventsLog is required" }
if (-not $PoolStateFile) { Fail "-PoolStateFile is required" }

# An unset env var passed as -PollSeconds binds as integer 0, which would never
# advance elapsed time and hang the bounded watch; fall back to the defaults.
if ($PollSeconds -le 0) { $PollSeconds = 10 }
if ($WaitSeconds -lt 0) { $WaitSeconds = 240 }

if (-not $CursorFile) {
    $CursorFile = Join-Path (Split-Path $EventsLog) "watch-cursor"
}
$cursorDir = Split-Path $CursorFile
if ($cursorDir) { New-Item -ItemType Directory -Force -Path $cursorDir | Out-Null }

function Read-Cursor {
    if (Test-Path $CursorFile) { return [int](Get-Content $CursorFile -Raw).Trim() }
    return 0
}

function Get-PoolLease {
    $lease = [pscustomobject]@{ ProcessId = 0; Start = "" }
    if (-not (Test-Path $PoolStateFile)) { return $lease }
    try {
        $data = Get-Content $PoolStateFile -Raw | ConvertFrom-Json
        $lease.ProcessId = [int]$data.pool_pid
        if ($data.PSObject.Properties["pool_pid_start"]) { $lease.Start = [string]$data.pool_pid_start }
    } catch {}
    return $lease
}

function Get-CommandLineFor([int]$processId) {
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
        if ($proc -and $proc.CommandLine) { return [string]$proc.CommandLine }
    } catch {}
    try {
        $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($proc) { return [string]$proc.Path }
    } catch {}
    return ""
}

# PID existence alone is not identity: verify the command line and, when the
# lease recorded one, the process start time, so a recycled PID belonging to an
# unrelated process reads as pool-dead instead of being watched forever.
function Test-PoolAlive($lease) {
    if ($lease.ProcessId -le 0) { return $false }
    $proc = Get-Process -Id $lease.ProcessId -ErrorAction SilentlyContinue
    if (-not $proc) { return $false }
    if ((Get-CommandLineFor $lease.ProcessId) -notmatch $MatchPattern) { return $false }
    # Process.StartTime is only reliable for foreign processes on Windows; on
    # Unix .NET can report values off by months, so the command-line match is
    # the identity signal there.
    if ($lease.Start -and ($env:OS -eq "Windows_NT" -or $IsWindows)) {
        $parsedStart = [datetime]::MinValue
        if ([datetime]::TryParse($lease.Start, [ref]$parsedStart)) {
            try {
                $delta = [math]::Abs(($proc.StartTime - $parsedStart).TotalSeconds)
                if ($delta -gt 2) { return $false }
            } catch {}
        }
    }
    return $true
}

# Collect events-log lines added since the cursor and advance the cursor.
# Returns a single structured object; the caller prints the lines. Emitting
# lines from inside this function would pollute the success stream and make
# any status line truthy in an `if` condition.
function Drain-NewLines {
    $result = [pscustomobject]@{ Lines = @(); SawEmpty = $false }
    $cursor = Read-Cursor
    if (-not (Test-Path $EventsLog)) { return $result }
    $lines = @(Get-Content $EventsLog)
    if ($lines.Count -gt $cursor) {
        $result.Lines = @($lines[$cursor..($lines.Count - 1)])
        foreach ($line in $result.Lines) {
            if ("$line" -like "*type=pool-empty*") { $result.SawEmpty = $true }
        }
        Set-Content -Path $CursorFile -Value "$($lines.Count)" -Encoding UTF8
    }
    return $result
}

function Write-DrainedLines($drain) {
    foreach ($line in $drain.Lines) { Write-Output $line }
}

$elapsed = 0
while ($true) {
    $drain = Drain-NewLines
    Write-DrainedLines $drain
    if ($drain.SawEmpty) {
        Write-Output "WATCH|result=pool-empty|events_log=$EventsLog"
        exit 0
    }
    $lease = Get-PoolLease
    $poolPid = $lease.ProcessId
    $alive = Test-PoolAlive $lease
    if (-not $alive) {
        # Drain once more: the pool may have written pool-empty and exited
        # between the drain above and the liveness check.
        $drain = Drain-NewLines
        Write-DrainedLines $drain
        if ($drain.SawEmpty) {
            Write-Output "WATCH|result=pool-empty|events_log=$EventsLog"
            exit 0
        }
        Write-Output "WATCH|result=pool-dead|pid=$poolPid|pool_state_file=$PoolStateFile"
        exit 3
    }
    if ($elapsed -ge $WaitSeconds) {
        Write-Output "WATCH|result=running|pid=$poolPid|elapsed=$elapsed"
        exit 0
    }
    Start-Sleep -Seconds $PollSeconds
    $elapsed += $PollSeconds
}
