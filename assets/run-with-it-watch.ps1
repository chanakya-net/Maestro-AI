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

# Print events-log lines added since the cursor, advance the cursor, and
# return whether pool-empty was observed.
function Drain-NewLines {
    $cursor = Read-Cursor
    if (-not (Test-Path $EventsLog)) { return $false }
    $lines = @(Get-Content $EventsLog)
    $sawEmpty = $false
    if ($lines.Count -gt $cursor) {
        foreach ($line in $lines[$cursor..($lines.Count - 1)]) {
            Write-Output $line
            if ("$line" -like "*type=pool-empty*") { $sawEmpty = $true }
        }
        Set-Content -Path $CursorFile -Value "$($lines.Count)" -Encoding UTF8
    }
    return $sawEmpty
}

$elapsed = 0
while ($true) {
    if (Drain-NewLines) {
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
        if (Drain-NewLines) {
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
