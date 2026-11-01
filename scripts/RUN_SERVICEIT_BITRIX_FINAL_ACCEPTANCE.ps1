[CmdletBinding()]
param([string]$PythonPath = 'py')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $Repo
try {
    $Base = '6665576ce712147bece233b47909ab6884d037d5'
    $Head = & git rev-parse HEAD
    if ($LASTEXITCODE -ne 0) { throw 'SOURCE_SHA_FAILED' }
    Write-Output ('SOURCE_SHA=' + $Head + '; BASE_SHA=' + $Base)
    & $PythonPath -B -m compileall -q leadpilot tests
    if ($LASTEXITCODE -ne 0) { throw 'COMPILE_FAILED' }
    & $PythonPath -B scripts/serviceit_offline_pytest.py
    if ($LASTEXITCODE -ne 0) { throw 'PYTEST_FAILED_OR_UNAVAILABLE' }
    & git diff --check $Base -- leadpilot tests scripts docs
    if ($LASTEXITCODE -ne 0) { throw 'DIFF_CHECK_FAILED' }
    & $PythonPath -B -m unittest discover -s tests -p test_bitrix_manager_demo.py -q
    if ($LASTEXITCODE -ne 0) { throw 'DEMO_SAFETY_FAILED' }
    Write-Output 'ACCEPTANCE=PASS; CREDENTIALS_NOT_REQUIRED=YES; REAL_REST_CALLS=0; PRODUCTION_READY=NO; REAL_LEADS_DELIVERED=NO'
} finally { Pop-Location }
