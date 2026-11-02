# Strictly READ-ONLY diagnostics for existing corporate 1062/21 item #7.
# Does not invoke PR78's update-capable module entrypoint, and cannot apply.
[CmdletBinding()]
param([string]$PythonPath = '')
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$RepoRoot=Split-Path $PSScriptRoot -Parent
if (-not $PythonPath) {
    $PythonPath=Join-Path $RepoRoot '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'PYTHON_NOT_FOUND'
}
if (Test-Path Env:\SERVICEIT_B2B_CARD7_DIAG_WEBHOOK) {
    throw 'PREEXISTING_DIAGNOSTIC_SECRET_REFUSED'
}
$secret=Read-Host 'Corporate Bitrix24 REST webhook (hidden input)' -AsSecureString
if ($null -eq $secret -or $secret.Length -eq 0) {
    throw 'READONLY_WEBHOOK_REQUIRED'
}
Push-Location $RepoRoot
try {
    $ptr=[IntPtr]::Zero
    try {
        $ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
        $env:SERVICEIT_B2B_CARD7_DIAG_WEBHOOK=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    } finally {
        if ($ptr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        }
        $secret.Dispose()
    }
    if (-not $env:SERVICEIT_B2B_CARD7_DIAG_WEBHOOK) {
        throw 'READONLY_WEBHOOK_REQUIRED'
    }
    & $PythonPath -m leadpilot.serviceit_b2b_card7_forensic_readonly --inspect-live --webhook-from-env
    if ($LASTEXITCODE -ne 0) {
        throw 'B2B_CARD7_FORENSIC_READONLY_BLOCKED; NEVER_USE_APPLY'
    }
    Write-Output 'SERVICEIT_CARD7_FORENSIC=READ_ONLY; CRM_WRITES=0; GIGACHAT_CALLS=0'
} finally {
    if (Test-Path Env:\SERVICEIT_B2B_CARD7_DIAG_WEBHOOK) {
        Remove-Item Env:\SERVICEIT_B2B_CARD7_DIAG_WEBHOOK -ErrorAction SilentlyContinue
    }
    Pop-Location
}
