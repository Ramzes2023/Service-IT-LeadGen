# No CRM writes; accepts only a test webhook in environment variable.
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$py=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw 'RUN_START_LAB_FIRST' }
if (-not $env:BITRIX24_TEST_WEBHOOK) { throw 'SET_BITRIX24_TEST_WEBHOOK_LOCALLY; NEVER_PASTE_TOKEN_IN_CHAT' }
$env:LEADPILOT_DB = Join-Path $PSScriptRoot 'data\research.sqlite3'
& $py -m leadpilot.cli crm-readonly-preflight
if ($LASTEXITCODE -ne 0) { throw 'CRM_READONLY_FAILED' }
Write-Host 'CRM_PREFLIGHT_COMPLETE; CRM_WRITES=NO' -ForegroundColor Green
