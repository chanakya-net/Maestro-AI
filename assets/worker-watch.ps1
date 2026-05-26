param(
    [Parameter(Mandatory = $true)][Alias("Pid")][int]$ProcessId,
    [Parameter(Mandatory = $true)][string]$DoneFile,
    [string]$LogFile = "",
    [string]$TailStateFile = "",
    [int]$TailLines = 5
)

$ErrorActionPreference = "Stop"

function Get-BooleanString([bool]$value) {
    if ($value) { return "true" }
    return "false"
}

function Get-TextHash([string]$text) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($text)
        $hashBytes = $sha.ComputeHash($bytes)
        return (($hashBytes | ForEach-Object { $_.ToString("x2") }) -join "")
    }
    finally {
        $sha.Dispose()
    }
}

$alive = $false
try {
    $null = Get-Process -Id $ProcessId -ErrorAction Stop
    $alive = $true
}
catch {
    $alive = $false
}

$donePresent = (Test-Path $DoneFile) -and ((Get-Item $DoneFile).Length -gt 0)
$logPresent = $false
$logTailChanged = $false
$tailHash = "none"

if ($LogFile -and (Test-Path $LogFile) -and ((Get-Item $LogFile).Length -gt 0)) {
    $logPresent = $true
    $tailText = ((Get-Content -Path $LogFile -Tail $TailLines -Encoding UTF8) -join "`n")
    $tailHash = Get-TextHash $tailText

    $previousHash = ""
    if ($TailStateFile -and (Test-Path $TailStateFile)) {
        $previousHash = (Get-Content -Path $TailStateFile -Raw -Encoding UTF8).Trim()
    }

    if ($tailHash -ne $previousHash) {
        $logTailChanged = $true
        if ($TailStateFile) {
            $tailDir = Split-Path $TailStateFile
            if ($tailDir) {
                New-Item -ItemType Directory -Force -Path $tailDir | Out-Null
            }
            Set-Content -Path $TailStateFile -Value $tailHash -Encoding UTF8
        }
    }
}

"WORKER|pid=$ProcessId|alive=$(Get-BooleanString $alive)|done=$(Get-BooleanString $donePresent)|log_present=$(Get-BooleanString $logPresent)|log_tail_changed=$(Get-BooleanString $logTailChanged)|tail_hash=$tailHash"
