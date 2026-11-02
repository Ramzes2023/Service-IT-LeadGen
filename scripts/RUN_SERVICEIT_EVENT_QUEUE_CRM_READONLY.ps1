# Strictly read-only: process pending Bitrix24 1062 IDs from PR72 event queue.
# No CRM writes, GigaChat, calls, tasks, webhook logs or ledger resets.
[CmdletBinding()]
param(
    [switch]$InspectLive,
    [string]$PythonPath = '',
    [string]$QueueDb = '',
    [ValidateRange(1,10)][int]$Limit = 10
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if (-not $PythonPath) { $PythonPath = Join-Path $root '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'APPROVED_PYTHON_NOT_FOUND' }
if (Test-Path Env:\SERVICEIT_EVENT_READONLY_WEBHOOK) { throw 'PREEXISTING_WEBHOOK_ENV_REFUSED' }
$pythonArgs = @('-m','leadpilot.serviceit_bitrix_event_queue_readonly_worker')
if ($InspectLive) {
    if (-not $QueueDb -or -not (Test-Path -LiteralPath $QueueDb -PathType Leaf)) {
        throw 'PERSISTENT_EVENT_QUEUE_REQUIRED'
    }
    if (-not [IO.Path]::IsPathRooted($QueueDb)) { throw 'QUEUE_PATH_MUST_BE_ABSOLUTE' }
    $pythonArgs += @('--inspect-live','--webhook-from-env','--queue-db',$QueueDb,'--limit',"$Limit")
}
Push-Location $root
try {
    if ($InspectLive) {
        $secret = Read-Host 'Authorized corporate Bitrix24 REST webhook (hidden input)' -AsSecureString
        if ($null -eq $secret -or $secret.Length -eq 0) { throw 'BITRIX_WEBHOOK_MISSING' }
        $ptr=[IntPtr]::Zero
        try {
            $ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
            $env:SERVICEIT_EVENT_READONLY_WEBHOOK = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        } finally {
            if ($ptr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
            $secret.Dispose()
        }
        if (-not $env:SERVICEIT_EVENT_READONLY_WEBHOOK) { throw 'BITRIX_WEBHOOK_MISSING' }
    }
    & $PythonPath @pythonArgs
    if ($LASTEXITCODE -ne 0) { throw 'SERVICEIT_EVENT_QUEUE_READ_ONLY_BLOCKED' }
    Write-Output 'SERVICEIT_EVENT_CRM_READ_ONLY=PASS; CRM_WRITES=0; MODEL_CALLS=0'
} finally {
    if (Test-Path Env:\SERVICEIT_EVENT_READONLY_WEBHOOK) {
        Remove-Item Env:\SERVICEIT_EVENT_READONLY_WEBHOOK -ErrorAction SilentlyContinue
    }
    Pop-Location
}
