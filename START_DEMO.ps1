$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
Write-Host 'SERVICE_IT_LEADGEN_DEMO_START' -ForegroundColor Cyan
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw 'Python не найден. Установите Python 3.12 и повторите.' }
if (-not (Test-Path '.venv\Scripts\python.exe')) {
  python -m venv .venv
  if ($LASTEXITCODE -ne 0) { throw 'VENV_CREATE_FAILED' }
}
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $py -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'DEPENDENCIES_INSTALL_FAILED' }
& $py -m leadpilot.cli init
if ($LASTEXITCODE -ne 0) { throw 'DB_INIT_FAILED' }
& $py -m leadpilot.cli seed
if ($LASTEXITCODE -ne 0) { throw 'DEMO_DATA_FAILED' }
& $py -m leadpilot.cli suppress 7700000006 all 'Учебный пример отказа'
if ($LASTEXITCODE -ne 0) { throw 'SUPPRESSION_FAILED' }
& $py -m leadpilot.cli qualify --today 2026-10-30
if ($LASTEXITCODE -ne 0) { throw 'QUALIFICATION_FAILED' }
& $py -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'TESTS_FAILED' }
& $py -m leadpilot.cli crm-preview
if ($LASTEXITCODE -ne 0) { throw 'CRM_PREVIEW_FAILED' }
Write-Host 'TESTS_PASSED=YES; LIVE_CRM_WRITE=NO; OPEN http://127.0.0.1:8765' -ForegroundColor Green
& $py -m uvicorn leadpilot.web:app --host 127.0.0.1 --port 8765
