# Independent, separately approved reconciliation AFTER badge incident audit.
# No reuse/reset of original UNCERTAIN write. Never alter state directory.
[CmdletBinding()]
param(
    [switch]$InspectLive,
    [switch]$ApplyReconciliation,
    [switch]$ApproveOneReconciliation,
    [string]$PythonPath = '',
    [string]$StateDir = (Join-Path $env:LOCALAPPDATA 'ServiceIT\Kanban-Score-Badge-v1')
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $PythonPath) { $PythonPath = Join-Path (Split-Path $PSScriptRoot -Parent) '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'APPROVED_PYTHON_NOT_FOUND' }
if ($ApplyReconciliation -and (-not $InspectLive -or -not $ApproveOneReconciliation)) {
    throw 'REPAIR_APPLY_REQUIRES_LIVE_AND_EXPLICIT_APPROVAL'
}
if ($ApproveOneReconciliation -and -not $ApplyReconciliation) {
    throw 'REPAIR_APPROVAL_NOT_FOR_READONLY'
}
if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) { throw 'PREEXISTING_WEBHOOK_ENV_REFUSED' }
$argsPython = @('-m','leadpilot.serviceit_kanban_badge_reconciliation','--state-dir',$StateDir)
if ($InspectLive) { $argsPython += '--inspect-live' }
if ($ApplyReconciliation) {
    $argsPython += @('--apply-reconciliation','--approve-one-reconciliation')
}
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
        $argsPython += '--webhook-from-env'
    }
    & $PythonPath @argsPython
    if ($LASTEXITCODE -ne 0) {
        throw 'RECONCILIATION_BLOCKED_OR_UNCERTAIN; NEVER_REPEAT_APPLY; INSPECT_LEDGER'
    }
} finally {
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) {
        Remove-Item Env:\SERVICEIT_AI_COCKPIT_WEBHOOK -ErrorAction SilentlyContinue
    }
    Pop-Location
}
