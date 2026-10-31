# EIS -> Bitrix24 readiness from actual confirmed EIS SQLite, no CRM requests.
[CmdletBinding()]
param([string]$PythonPath = '', [string]$StateDir = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Python = if ($PythonPath) { $PythonPath } else { Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'EIS_BITRIX_READINESS_PYTHON_MISSING'
}
if (-not $StateDir) {
    $StateDir = Join-Path $env:LOCALAPPDATA 'ServiceIT\EIS-Daily-Operational'
}
Push-Location -LiteralPath $Repo
try {
    Write-Output 'EIS_BITRIX_STAGE=OFFLINE_TESTS; CRM_CONNECTION=NO'
    $Tests = @('tests/test_eis_bitrix_readiness.py', 'tests/test_eis_daily_health.py', 'tests/test_eis_bitrix_sandbox.py')
    & $Python -m pytest -q -p no:cacheprovider @Tests
    if ($LASTEXITCODE -ne 0) { throw 'EIS_BITRIX_READINESS_TESTS_FAILED' }
    Write-Output 'EIS_BITRIX_STAGE=READ_ONLY_PERSISTENT_EIS_HISTORY'
    & $Python -m leadpilot.eis_bitrix_readiness --state-dir $StateDir
    if ($LASTEXITCODE -ne 0) { throw 'EIS_BITRIX_READINESS_BLOCKED' }
    Write-Output 'EIS_BITRIX_ACCEPTANCE=PASS_RESEARCH_ONLY; CRM_WRITES=NO; APPROVED_LEADS=0'
} finally {
    Pop-Location
}
