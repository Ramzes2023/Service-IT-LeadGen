# EIS scope research: ONE official RSS GET, show why 200 items were excluded.
# Never changes main, no Bitrix24, no messages, no browser protection bypass.
[CmdletBinding()]
param(
    [string]$SourceReport = '',
    [string]$RssUrl = '',
    [string]$PythonPath = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Python = if ($PythonPath) { $PythonPath } else { Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'EIS_SCOPE_PYTHON_NOT_FOUND'
}
if (-not $SourceReport) {
    $SourceReport = Get-ChildItem "$env:LOCALAPPDATA\ServiceIT\EIS-Acceptance" -Recurse -Filter 'eis_source_*.json' -File |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $SourceReport -or -not (Test-Path -LiteralPath $SourceReport -PathType Leaf)) {
    throw 'EIS_SCOPE_PREVIOUS_SOURCE_REPORT_NOT_FOUND'
}
$Previous = Get-Content -LiteralPath $SourceReport -Encoding UTF8 -Raw | ConvertFrom-Json
if ($Previous.mode -ne 'EIS_OFFICIAL_RSS_RESEARCH_ONLY' -or $Previous.crm_writes -ne 'NO' -or
    -not $Previous.search_rss_url) {
    throw 'EIS_SCOPE_PREVIOUS_REPORT_NOT_SAFE'
}
if (-not $RssUrl) {
    $RssUrl = [string]$Previous.search_rss_url
    Write-Output 'EIS_SCOPE_SEARCH=REUSING_PREVIOUS_TEST_SEARCH; MOSCOW_1C_SEARCH_NOT_VERIFIED'
} else {
    Write-Output 'EIS_SCOPE_SEARCH=USER_SUPPLIED_OFFICIAL_RSS; FILTERS_NOT_PRE_VERIFIED'
}
Push-Location -LiteralPath $Repo
try {
    Write-Output 'EIS_SCOPE_STAGE=TEST'
    $Tests = @('tests/test_eis_real_format.py', 'tests/test_eis_source_diagnostics.py', 'tests/test_eis_rss.py')
    & $Python -m pytest -q -p no:cacheprovider @Tests
    if ($LASTEXITCODE -ne 0) { throw 'EIS_SCOPE_TEST_FAILED' }
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
    $output = ('exports/eis_scope_' + $stamp + '.json')
    Write-Output 'EIS_SCOPE_STAGE=LIVE_OFFICIAL_RSS_SINGLE_REQUEST'
    & $Python -m leadpilot.eis_rss --url $RssUrl --output $output
    if ($LASTEXITCODE -ne 0) { throw 'EIS_SCOPE_LIVE_RSS_UNAVAILABLE' }
    Write-Output 'EIS_SCOPE_STAGE=BREAKDOWN'
    & $Python -m leadpilot.eis_source_diagnostics --report $output --show-public-samples
    if ($LASTEXITCODE -ne 0) { throw 'EIS_SCOPE_DIAGNOSIS_FAILED' }
    $report = Get-Content -LiteralPath $output -Encoding UTF8 -Raw | ConvertFrom-Json
    if ($report.crm_writes -ne 'NO' -or $report.outbound_messages -ne 'NO') {
        throw 'EIS_SCOPE_UNSAFE_REPORT'
    }
    Write-Output 'EIS_SCOPE_PUBLIC_SAMPLES=ASCII_ESCAPED_JSON_ABOVE'
    Write-Output ('EIS_SCOPE_REPORT=' + (Resolve-Path -LiteralPath $output).Path)
    Write-Output 'EIS_SCOPE_COMPLETE=YES; QUALIFIED_LEADS=0; CRM_WRITES=NO'
} finally {
    Pop-Location
}
