# Explicit single read-only query to official open-vacancy API; no CRM or messaging.
param(
  [ValidateSet('77','50','06')][string]$Region = '77',
  [ValidateSet('1С','системный администратор','IT-инженер')][string]$Term = '1С',
  [ValidateRange(1,50)][int]$Limit = 15,
  [switch]$ImportToResearchDB
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { throw 'RUN_START_LAB_FIRST_TO_CREATE_LOCAL_VENV' }
$env:LEADPILOT_DB = Join-Path $PSScriptRoot 'data\research.sqlite3'
Write-Host 'SOURCE=TRUDVSEM_OPEN_VACANCIES; CONTACTS=NOT_COLLECTED; CRM_WRITES=NO' -ForegroundColor Cyan
$argsList = @('-m','leadpilot.cli','source-scan','--region',$Region,'--term',$Term,
              '--limit',[string]$Limit,'--output','exports\trudvsem_probe.json')
if ($ImportToResearchDB) { $argsList += '--import-to-local' }
& $py @argsList
if ($LASTEXITCODE -ne 0) { throw 'SOURCE_PROBE_FAILED_OR_UNAVAILABLE: no data assumed' }
Write-Host ('SOURCE_SCAN_DONE=YES; IMPORT_TO_LOCAL=' + [string]([bool]$ImportToResearchDB)) -ForegroundColor Green
