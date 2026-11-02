# READ-ONLY reconciliation after ambiguous PR78 -ApplyOnce.
# This operator has no apply flags, cannot call crm.item.update and never
# writes/repairs the original UNCERTAIN SQLite ledger.
[CmdletBinding()]
param([string]$PythonPath = '')
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$Root=Split-Path $PSScriptRoot -Parent
if (-not $PythonPath) { $PythonPath=Join-Path $Root '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'APPROVED_PYTHON_NOT_FOUND'
}
if (Test-Path Env:\SERVICEIT_B2B_CARD7_POSTAPPLY_WEBHOOK) {
    throw 'PREEXISTING_WEBHOOK_ENV_REFUSED'
}
$secret=Read-Host 'Corporate Bitrix24 REST webhook URL (hidden input)' -AsSecureString
if ($null -eq $secret -or $secret.Length -eq 0) {
    throw 'HIDDEN_WEBHOOK_MISSING'
}
Push-Location $Root
try {
    $ptr=[IntPtr]::Zero
    try {
        $ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
        $env:SERVICEIT_B2B_CARD7_POSTAPPLY_WEBHOOK=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    } finally {
        if ($ptr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        }
        $secret.Dispose()
    }
    if (-not $env:SERVICEIT_B2B_CARD7_POSTAPPLY_WEBHOOK) {
        throw 'HIDDEN_WEBHOOK_MISSING'
    }
    & $PythonPath -m leadpilot.serviceit_b2b_card7_postapply_readonly --inspect-live --webhook-from-env
    if ($LASTEXITCODE -ne 0) {
        throw 'B2B_CARD7_READONLY_RECONCILIATION_FAILED; DO_NOT_RETRY_APPLY'
    }
    Write-Output 'SERVICEIT_B2B_CARD7_READBACK=READ_ONLY; CRM_WRITES=0; LEDGER_CHANGES=0'
} finally {
    if (Test-Path Env:\SERVICEIT_B2B_CARD7_POSTAPPLY_WEBHOOK) {
        Remove-Item Env:\SERVICEIT_B2B_CARD7_POSTAPPLY_WEBHOOK -ErrorAction SilentlyContinue
    }
    Pop-Location
}
