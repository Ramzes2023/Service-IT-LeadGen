# Offline-only Bitrix handoff contract. Never requests a webhook or contacts real portal.
[CmdletBinding()]
param([string]$PythonPath = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) { $PythonPath = Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'SERVICEIT_OFFLINE_HANDOFF_VENV_REQUIRED'
}
$OutputDir=Join-Path $env:LOCALAPPDATA 'ServiceIT\Bitrix24-DryRun'
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$Date=(Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
$Output=Join-Path $OutputDir ('handoff-'+$Date+'.json')
Push-Location -LiteralPath $Repo
try {
    Write-Output 'BITRIX_OFFLINE_HANDOFF=TEST_START; CRM_WRITES=NO'
    & $PythonPath -m pytest -q -p no:cacheprovider tests/test_bitrix_handoff_dryrun.py
    if ($LASTEXITCODE -ne 0) { throw 'BITRIX_OFFLINE_HANDOFF_TESTS_FAILED' }
    & $PythonPath -m compileall -q leadpilot/bitrix_handoff_dryrun.py
    if ($LASTEXITCODE -ne 0) { throw 'BITRIX_OFFLINE_HANDOFF_COMPILE_FAILED' }
    & $PythonPath -m leadpilot.bitrix_handoff_dryrun --output $Output
    if ($LASTEXITCODE -ne 0) { throw 'BITRIX_OFFLINE_HANDOFF_PREVIEW_FAILED' }
    Write-Output 'BITRIX_OFFLINE_HANDOFF=PASS_LOCAL_ONLY; LIVE_BITRIX_NOT_CONNECTED'
    Write-Output ('BITRIX_OFFLINE_HANDOFF_FILE=' + $Output)
} finally {
    Pop-Location
}
