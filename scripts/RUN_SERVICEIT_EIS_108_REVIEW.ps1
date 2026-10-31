# Service IT: official EIS research to human evidence-review queue.
# Offline source files only; no new EIS requests, Bitrix24 or outreach.
[CmdletBinding()]
param(
    [string]$TargetSummary = '',
    [string]$PythonPath = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Python = if ($PythonPath) { $PythonPath } else { Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'EIS_REVIEW_PYTHON_MISSING' }
if (-not $TargetSummary) {
    $Cache = Join-Path $env:LOCALAPPDATA 'ServiceIT\EIS-Targeted-1C'
    if (-not (Test-Path -LiteralPath $Cache -PathType Container)) {
        throw 'EIS_REVIEW_TARGETED_RESULTS_MISSING'
    }
    $TargetSummary = Get-ChildItem -LiteralPath $Cache -Recurse -Filter 'eis_targeted_1c_*_summary.json' -File |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $TargetSummary -or -not (Test-Path -LiteralPath $TargetSummary -PathType Leaf)) {
    throw 'EIS_REVIEW_TARGETED_SUMMARY_NOT_FOUND'
}
Push-Location -LiteralPath $Repo
try {
    Write-Output 'EIS_REVIEW_STAGE=TEST'
    $Tests = @('tests/test_eis_rss.py','tests/test_eis_real_format.py',
        'tests/test_eis_buyer_review.py','tests/test_eis_manager_queue.py',
        'tests/test_eis_bitrix_sandbox.py','tests/test_eis_transport_diagnostics.py',
        'tests/test_eis_windows_tls.py','tests/test_eis_source_diagnostics.py',
        'tests/test_eis_search_integrity.py','tests/test_eis_targeted_1c.py',
        'tests/test_eis_108_review.py')
    & $Python -m pytest -q -p no:cacheprovider @Tests
    if ($LASTEXITCODE -ne 0) { throw 'EIS_REVIEW_TESTS_FAILED' }
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
    $jsonOut = ('exports/eis_108_review_' + $stamp + '.json')
    $csvOut = ('exports/eis_108_review_' + $stamp + '.csv')
    Write-Output 'EIS_REVIEW_STAGE=BUILD_OFFLINE_HUMAN_QUEUE'
    & $Python -m leadpilot.eis_108_review --summary $TargetSummary --json $jsonOut --csv $csvOut
    if ($LASTEXITCODE -ne 0) { throw 'EIS_REVIEW_BUILD_FAILED' }
    $result = Get-Content -LiteralPath $jsonOut -Encoding UTF8 -Raw | ConvertFrom-Json
    if ($result.crm_writes -ne 'NO' -or $result.counts.qualified_b2b_leads -ne 0 -or
        $result.counts.crm_ready -ne 0) { throw 'EIS_REVIEW_SAFETY_GATE_FAILED' }
    Write-Output ('EIS_REVIEW_NOTICE_TOTAL=' + $result.counts.unique_notices_to_review)
    Write-Output ('EIS_REVIEW_SUSPECTED_PUBLIC_BUYERS=' + $result.counts.suspected_public_institutions)
    Write-Output ('EIS_REVIEW_JSON=' + (Resolve-Path -LiteralPath $jsonOut).Path)
    Write-Output ('EIS_REVIEW_CSV=' + (Resolve-Path -LiteralPath $csvOut).Path)
    Write-Output 'EIS_REVIEW_COMPLETE=YES; EXACT_INN_FNS_NOT_YET_VERIFIED; CRM_WRITES=NO'
} finally {
    Pop-Location
}
