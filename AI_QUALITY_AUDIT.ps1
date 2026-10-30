# No network, no secrets, no writes. Inspect existing AI outputs only.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { throw 'SERVICE_IT_VENV_MISSING' }
$db = Join-Path $PSScriptRoot 'data\demo.sqlite3'
if (-not (Test-Path -LiteralPath $db)) { throw 'DEMO_DB_MISSING' }
& $py -m leadpilot.ai_quality_audit $db
if ($LASTEXITCODE -ne 0) { throw 'AI_AUDIT_FAILED' }
Write-Host 'AI_QUALITY_LOCAL_AUDIT_DONE=YES; CRM_WRITES=NO; NETWORK_CALLS=NO; VALYQON_CHANGES=NONE'
