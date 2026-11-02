# Incident-specific audit. NEVER use an -Apply switch or manipulate the ledger.
[CmdletBinding()]
param(
    [switch]$InspectLive,
    [string]$PythonPath = '',
    [string]$StateDir = (Join-Path $env:LOCALAPPDATA 'ServiceIT\Kanban-Score-Badge-v1')
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $PythonPath) { $PythonPath = Join-Path (Split-Path $PSScriptRoot -Parent) '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'APPROVED_PYTHON_NOT_FOUND' }
if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) { throw 'PREEXISTING_WEBHOOK_ENV_REFUSED' }
$arguments = @('-m','leadpilot.serviceit_kanban_badge_recovery','--state-dir',$StateDir)
if ($InspectLive) { $arguments += '--inspect-live' }
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    if ($InspectLive) {
        $secret = Read-Host 'Authorized Bitrix24 webhook URL (hidden input)' -AsSecureString
        if ($null -eq $secret -or $secret.Length -eq 0) { throw 'BITRIX_WEBHOOK_MISSING' }
        $ptr = [IntPtr]::Zero
        try {
            $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
            $env:SERVICEIT_AI_COCKPIT_WEBHOOK = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        } finally {
            if ($ptr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
            $secret.Dispose()
        }
        if (-not $env:SERVICEIT_AI_COCKPIT_WEBHOOK) { throw 'BITRIX_WEBHOOK_MISSING' }
        $arguments += '--webhook-from-env'
    }
    & $PythonPath @arguments
    if ($LASTEXITCODE -ne 0) { throw 'READONLY_DIAGNOSTIC_BLOCKED; NO_RETRY_OF_APPLY' }
    Write-Output 'SERVICEIT_BADGE_DIAGNOSTIC=READ_ONLY; CRM_WRITES=0; LEDGER_MUTATIONS=0'
} finally {
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) {
        Remove-Item Env:\SERVICEIT_AI_COCKPIT_WEBHOOK -ErrorAction SilentlyContinue
    }
    Pop-Location
}
