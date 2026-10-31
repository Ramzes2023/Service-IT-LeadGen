# Authorized Bitrix24 metadata audit. Only safe API metadata reads. Never paste webhook in chat.
[CmdletBinding()]
param(
    [switch]$Live,
    [string]$PythonPath = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) { $PythonPath = Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'BITRIX_AUDIT_EXISTING_PYTHON_VENV_REQUIRED'
}
$Directory = Join-Path $env:LOCALAPPDATA 'ServiceIT\Bitrix24-Metadata'
New-Item -ItemType Directory -Path $Directory -Force | Out-Null
$Stamp=(Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
$Output=Join-Path $Directory ('report-' + $Stamp + '.json')
Push-Location -LiteralPath $Repo
try {
    Write-Output 'SERVICEIT_BITRIX_METADATA_TESTS=START; NO_CUSTOMER_RECORDS; CRM_WRITES=NO'
    & $PythonPath -m pytest -q -p no:cacheprovider tests/test_bitrix_metadata_audit.py
    if ($LASTEXITCODE -ne 0) { throw 'BITRIX_METADATA_TESTS_FAILED' }
    & $PythonPath -m compileall -q leadpilot/bitrix_metadata_audit.py
    if ($LASTEXITCODE -ne 0) { throw 'BITRIX_METADATA_COMPILE_FAILED' }
    Write-Output 'SERVICEIT_BITRIX_METADATA_TESTS=PASS'
    if ($Live) {
        Write-Output 'SERVICEIT_BITRIX_LIVE=READ_ONLY_METADATA; COMPANY_RECORDS=NOT_REQUESTED'
        Write-Output 'WARNING: A Bitrix webhook can grant broader rights than this script uses.'
        $authorization = Read-Host 'Has an administrator approved the read-only metadata audit? Type YES to continue'
        if ($authorization -cne 'YES') { throw 'BITRIX_METADATA_OPERATOR_APPROVAL_REQUIRED' }
        $secret = Read-Host 'Enter the authorized webhook URL on this PC (input is hidden)' -AsSecureString
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
        try {
            $env:BITRIX24_AUDIT_WEBHOOK = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        }
        if (-not $env:BITRIX24_AUDIT_WEBHOOK) {
            throw 'BITRIX_METADATA_WEBHOOK_MISSING'
        }
        & $PythonPath -m leadpilot.bitrix_metadata_audit --read-only --output $Output
    } else {
        Write-Output 'SERVICEIT_BITRIX=OFFLINE_PLAN_ONLY; PASS -Live only with authorized webhook'
        & $PythonPath -m leadpilot.bitrix_metadata_audit --offline --output $Output
    }
    if ($LASTEXITCODE -ne 0) { throw 'BITRIX_METADATA_AUDIT_FAILED' }
    Write-Output ('SERVICEIT_BITRIX_SAFE_REPORT=' + $Output)
    Write-Output 'SERVICEIT_BITRIX_METADATA_DONE=YES; CRM_WRITES=NO; REAL_CLIENT_RECORDS_READ=0'
} finally {
    if (Test-Path Env:\BITRIX24_AUDIT_WEBHOOK) {
        Remove-Item Env:\BITRIX24_AUDIT_WEBHOOK -ErrorAction SilentlyContinue
    }
    Pop-Location
}
