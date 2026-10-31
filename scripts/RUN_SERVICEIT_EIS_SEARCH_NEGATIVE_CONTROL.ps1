# Diagnostic only: existing official RSS query vs never-match searchString.
# Two official HTTPS GET calls with TLS verification, zero CRM writes.
[CmdletBinding()]
param(
    [string]$SourceReport = '',
    [string]$PythonPath = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Python = if ($PythonPath) { $PythonPath } else { Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'EIS_SEARCH_PYTHON_NOT_FOUND' }
if (-not $SourceReport) {
    $SourceReport = Get-ChildItem "$env:LOCALAPPDATA\ServiceIT\EIS-Scope-Review" -Recurse -Filter 'eis_scope_*.json' -File |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $SourceReport -or -not (Test-Path -LiteralPath $SourceReport -PathType Leaf)) {
    throw 'EIS_SEARCH_PREVIOUS_REPORT_NOT_FOUND'
}
Push-Location -LiteralPath $Repo
try {
    Write-Output 'EIS_SEARCH_STAGE=TEST'
    $Tests = @('tests/test_eis_search_integrity.py',
               'tests/test_eis_source_diagnostics.py',
               'tests/test_eis_real_format.py',
               'tests/test_eis_rss.py')
    & $Python -m pytest -q -p no:cacheprovider @Tests
    if ($LASTEXITCODE -ne 0) { throw 'EIS_SEARCH_TEST_FAILED' }
    Write-Output 'EIS_SEARCH_STAGE=PRIOR_SAMPLE_ASCII'
    & $Python -m leadpilot.eis_source_diagnostics --report $SourceReport --show-public-samples
    if ($LASTEXITCODE -ne 0) { throw 'EIS_SEARCH_PRIOR_REPORT_INVALID' }
    $Stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
    $Target = ('exports/eis_search_integrity_' + $Stamp + '.json')
    Write-Output 'EIS_SEARCH_STAGE=TWO_OFFICIAL_RSS_GETS; TLS_VERIFY=ON; NO_RETRIES=YES'
    & $Python -m leadpilot.eis_search_integrity --previous-report $SourceReport --output $Target
    if ($LASTEXITCODE -ne 0) { throw 'EIS_SEARCH_NEGATIVE_CONTROL_FAILED' }
    Write-Output ('EIS_SEARCH_REPORT=' + (Resolve-Path -LiteralPath $Target).Path)
    Write-Output 'EIS_SEARCH_PROBE_COMPLETE=YES; QUALIFIED_LEADS=0; CRM_WRITES=NO'
} finally {
    Pop-Location
}
