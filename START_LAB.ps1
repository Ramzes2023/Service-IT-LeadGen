# Windows PowerShell 5.1 compatible. No Python installation; no VALYQON changes.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
Write-Host 'SERVICE_IT_LEADGEN_V03_START' -ForegroundColor Cyan
$launcher = Get-Command py -ErrorAction SilentlyContinue
if (-not $launcher) { throw 'PY_LAUNCHER_MISSING: run py -0p for diagnosis' }
& py -3.12 --version
if ($LASTEXITCODE -ne 0) { throw 'PYTHON_312_NOT_AVAILABLE: run py -0p' }
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) {
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'VENV_CREATE_FAILED' }
}
& $py -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'DEPENDENCIES_FAILED' }
$env:LEADPILOT_DB = Join-Path $PSScriptRoot 'data\demo.sqlite3'
& $py -m leadpilot.cli init
if ($LASTEXITCODE -ne 0) { throw 'DB_FAILED' }
& $py -m leadpilot.cli seed
if ($LASTEXITCODE -ne 0) { throw 'SEED_FAILED' }
& $py -m leadpilot.cli suppress 7700000006 all 'synthetic opt-out'
if ($LASTEXITCODE -ne 0) { throw 'SUPPRESSION_FAILED' }
& $py -m leadpilot.cli qualify --today 2026-10-30
if ($LASTEXITCODE -ne 0) { throw 'QUALIFY_FAILED' }
& $py -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'TESTS_FAILED' }
& $py -m leadpilot.cli crm-preview --output 'exports\bitrix_demo_preview.json'
if ($LASTEXITCODE -ne 0) { throw 'CRM_PREVIEW_FAILED' }
Write-Host 'TESTS_PASSED=YES; CRM_WRITES=NO; REAL_SOURCE_REQUESTS=NO' -ForegroundColor Green
Write-Host 'OPEN=http://127.0.0.1:8766' -ForegroundColor Green
& $py -m uvicorn leadpilot.web:app --host 127.0.0.1 --port 8766
