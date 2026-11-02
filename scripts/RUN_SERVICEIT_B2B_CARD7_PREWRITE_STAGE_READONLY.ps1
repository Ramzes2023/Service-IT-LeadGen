# READ ONLY: reproduces PR78 pre-reservation reads, never runs update/reserve.
[CmdletBinding()]
param([string]$PythonPath='')
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$Root=Split-Path $PSScriptRoot -Parent
if (-not $PythonPath) { $PythonPath=Join-Path $Root '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'PYTHON_NOT_FOUND' }
if (Test-Path Env:\SERVICEIT_B2B_CARD7_PREWRITE_WEBHOOK) {
    throw 'PREEXISTING_WEBHOOK_ENV_REFUSED'
}
$secret=Read-Host 'Corporate Bitrix24 REST webhook (hidden input)' -AsSecureString
if ($null -eq $secret -or $secret.Length -eq 0) { throw 'WEBHOOK_MISSING' }
Push-Location $Root
try {
    $ptr=[IntPtr]::Zero
    try {
        $ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
        $env:SERVICEIT_B2B_CARD7_PREWRITE_WEBHOOK=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    } finally {
        if ($ptr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
        $secret.Dispose()
    }
    & $PythonPath -m leadpilot.serviceit_b2b_card7_prewrite_stage_readonly --inspect-live --webhook-from-env
    if ($LASTEXITCODE -ne 0) { throw 'B2B_CARD7_PREWRITE_READONLY_BLOCKED; NO_WRITES' }
    Write-Output 'SERVICEIT_B2B_CARD7_PREWRITE=READ_ONLY; CRM_WRITES=0'
} finally {
    if (Test-Path Env:\SERVICEIT_B2B_CARD7_PREWRITE_WEBHOOK) {
        Remove-Item Env:\SERVICEIT_B2B_CARD7_PREWRITE_WEBHOOK -ErrorAction SilentlyContinue
    }
    Pop-Location
}
