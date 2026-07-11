param(
    [string]$EventsLog = "",
    [string]$PoolStateFile = "",
    [string]$CursorFile = "",
    [int]$WaitSeconds = $(if ($env:POOL_WATCH_SECONDS) { [int]$env:POOL_WATCH_SECONDS } else { 240 }),
    [int]$PollSeconds = $(if ($env:STATUS_POLL_SECONDS) { [int]$env:STATUS_POLL_SECONDS } else { 10 })
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

function Get-PoolPid {
    if (-not (Test-Path $PoolStateFile)) { return 0 }
    try {
        $data = Get-Content $PoolStateFile -Raw | ConvertFrom-Json
        return [int]$data.pool_pid
    } catch {
        return 0
    }
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
    $poolPid = Get-PoolPid
    $alive = $false
    if ($poolPid -gt 0) {
        $alive = [bool](Get-Process -Id $poolPid -ErrorAction SilentlyContinue)
    }
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
