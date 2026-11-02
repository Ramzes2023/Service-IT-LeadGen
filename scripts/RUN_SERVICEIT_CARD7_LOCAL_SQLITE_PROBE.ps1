# Local filesystem diagnostic only; no Bitrix/GigaChat secrets or network.
# Creates parent directory if missing and tests ONE random temporary SQLite
# file in the same folder; real backfill-v1.sqlite3 is not opened or altered.
[CmdletBinding()]
param([string]$PythonPath='')
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$Root=Split-Path $PSScriptRoot -Parent
if (-not $PythonPath) { $PythonPath=Join-Path $Root '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'APPROVED_PYTHON_NOT_FOUND'
}
if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA_NOT_CONFIGURED' }
Push-Location $Root
try {
    & $PythonPath -m leadpilot.serviceit_card7_sqlite_local_probe --local-only
    if ($LASTEXITCODE -ne 0) {
        throw 'LOCAL_SQLITE_PROBE_BLOCKED; NEVER_RETRY_HISTORICAL_APPLY'
    }
    Write-Output 'SERVICEIT_LOCAL_SQLITE_TEST=PASS; CRM_WRITES=0; REAL_LEDGER_NOT_CHANGED'
} finally {
    Pop-Location
}
