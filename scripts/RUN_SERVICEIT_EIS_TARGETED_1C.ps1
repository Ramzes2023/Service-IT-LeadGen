# Four constrained 1C service RSS searches, one HTTPS GET per phrase, no CRM.
[CmdletBinding()]
param(
    [string]$PreviousReport = '',
    [string]$PythonPath = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = if ($PythonPath) { $PythonPath } else { Join-Path $Root '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'EIS_TARGET_PYTHON_MISSING' }
if (-not $PreviousReport) {
    $PreviousReport = Get-ChildItem "$env:LOCALAPPDATA\ServiceIT\EIS-Scope-Review" -Recurse -Filter 'eis_scope_*.json' -File |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $PreviousReport -or -not (Test-Path -LiteralPath $PreviousReport -PathType Leaf)) {
    throw 'EIS_TARGET_PREVIOUS_REPORT_NOT_FOUND'
}
Push-Location -LiteralPath $Root
try {
    Write-Output 'EIS_TARGETED_STAGE=TEST'
    $Tests = @('tests/test_eis_rss.py','tests/test_eis_real_format.py',
        'tests/test_eis_buyer_review.py','tests/test_eis_manager_queue.py',
        'tests/test_eis_bitrix_sandbox.py','tests/test_eis_transport_diagnostics.py',
        'tests/test_eis_windows_tls.py','tests/test_eis_source_diagnostics.py',
        'tests/test_eis_search_integrity.py','tests/test_eis_targeted_1c.py')
    & $Python -m pytest -q -p no:cacheprovider @Tests
    if ($LASTEXITCODE -ne 0) { throw 'EIS_TARGETED_TESTS_FAILED' }
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
    $prefix = 'exports/eis_targeted_1c_' + $stamp
    Write-Output 'EIS_TARGETED_STAGE=FOUR_OFFICIAL_RSS_GETS; NO_RETRIES=YES; TLS_VERIFY=ON'
    & $Python -m leadpilot.eis_targeted_1c --previous-report $PreviousReport --prefix $prefix
    if ($LASTEXITCODE -ne 0) { throw 'EIS_TARGETED_RSS_FAILED' }
    $result = Get-Content -LiteralPath ($prefix + '_summary.json') -Encoding UTF8 -Raw | ConvertFrom-Json
    if ($result.crm_writes -ne 'NO' -or $result.counts.crm_ready -ne 0) {
        throw 'EIS_TARGETED_SAFETY_CHECK_FAILED'
    }
    Write-Output ('EIS_TARGETED_UNIQUE_RESEARCH_NOTICES=' + $result.counts.unique_research_notices)
    Write-Output ('EIS_TARGETED_SUMMARY=' + (Resolve-Path -LiteralPath ($prefix + '_summary.json')).Path)
    Write-Output 'EIS_TARGETED_COMPLETE=YES; BUYER_FNS=UNVERIFIED; CRM_WRITES=NO'
} finally {
    Pop-Location
}
