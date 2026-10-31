# ServiceIT: re-triage saved 108 EIS notices, no new HTTP/CRM requests.
[CmdletBinding()]
param([string]$PriorReview = '', [string]$PythonPath = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Python = if ($PythonPath) { $PythonPath } else { Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'EIS_TRIAGE_PYTHON_MISSING'
}
if (-not $PriorReview) {
    $PriorRoot = Join-Path $env:LOCALAPPDATA 'ServiceIT\EIS-108-Review'
    if (-not (Test-Path -LiteralPath $PriorRoot -PathType Container)) {
        throw 'EIS_TRIAGE_PREVIOUS_REVIEW_ROOT_MISSING'
    }
    $Record = Get-ChildItem -LiteralPath $PriorRoot -Recurse -File -Filter 'eis_108_review_*.json' |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $Record) { throw 'EIS_TRIAGE_PREVIOUS_REVIEW_JSON_MISSING' }
    $PriorReview = $Record.FullName
}
Push-Location -LiteralPath $Repo
try {
    Write-Output 'EIS_TRIAGE_STAGE=OFFLINE_REGRESSION_TESTS'
    $Tests = @(
      'tests/test_eis_rss.py', 'tests/test_eis_real_format.py',
      'tests/test_eis_buyer_review.py', 'tests/test_eis_manager_queue.py',
      'tests/test_eis_bitrix_sandbox.py', 'tests/test_eis_transport_diagnostics.py',
      'tests/test_eis_windows_tls.py', 'tests/test_eis_source_diagnostics.py',
      'tests/test_eis_search_integrity.py', 'tests/test_eis_targeted_1c.py',
      'tests/test_eis_108_review.py', 'tests/test_eis_108_triage.py'
    )
    & $Python -m pytest -q -p no:cacheprovider @Tests
    if ($LASTEXITCODE -ne 0) { throw 'EIS_TRIAGE_TESTS_FAILED' }
    $Stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
    $DestJson = 'exports/eis_108_triage_' + $Stamp + '.json'
    $DestCsv = 'exports/eis_108_triage_' + $Stamp + '.csv'
    Write-Output 'EIS_TRIAGE_STAGE=RECHECK_SAVED_REAL_108_NOTICES'
    & $Python -m leadpilot.eis_108_triage --review-json $PriorReview --json $DestJson --csv $DestCsv
    if ($LASTEXITCODE -ne 0) { throw 'EIS_TRIAGE_CLASSIFICATION_FAILED' }
    $Report = Get-Content -LiteralPath $DestJson -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($Report.crm_writes -ne 'NO' -or $Report.counts.crm_ready -ne 0 -or
        $Report.counts.qualified_b2b_leads -ne 0) {
        throw 'EIS_TRIAGE_UNSAFE_LEAD_PROMOTION'
    }
    Write-Output ('EIS_TRIAGE_TOTAL_NOTICES=' + $Report.counts.unique_notices)
    Write-Output ('EIS_TRIAGE_PUBLIC_SECTOR_TEXT_HINTS=' + $Report.counts.public_sector_text_hints)
    Write-Output ('EIS_TRIAGE_CSV=' + (Resolve-Path -LiteralPath $DestCsv).Path)
    Write-Output ('EIS_TRIAGE_JSON=' + (Resolve-Path -LiteralPath $DestJson).Path)
    Write-Output 'EIS_TRIAGE_COMPLETE=YES; QUALIFIED_B2B_LEADS=0; CRM_WRITES=NO'
} finally {
    Pop-Location
}
