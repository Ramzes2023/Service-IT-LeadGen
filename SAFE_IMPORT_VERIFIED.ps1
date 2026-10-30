# PowerShell 5.1 safe. Audits saved Trudvsem snapshots. Research DB only on -Import.
param([switch]$Import)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { throw 'LOCAL_PYTHON_MISSING' }
foreach ($r in @('77','50')) {
    $f = Join-Path $PSScriptRoot "exports\trudvsem_$r.json"
    if (-not (Test-Path -LiteralPath $f)) { throw "MISSING_SCAN_$r`: Run both region scans first" }
}
$arg = @('-m','leadpilot.verified_import','--snapshots','exports','--db','data/research.sqlite3')
if ($Import) { $arg += '--import-to-research' }
& $py @arg
if ($LASTEXITCODE -ne 0) { throw 'RESEARCH_AUDIT_OR_IMPORT_FAILED' }
