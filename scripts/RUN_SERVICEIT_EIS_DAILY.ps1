# Official EIS: bounded live RSS daily operation, persistent SQLite, no CRM.
# Run only one instance at a time; previous successful records are preserved.
[CmdletBinding()]
param(
    [string]$PreviousReport = '',
    [string]$PythonPath = '',
    [string]$StateDir = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Python = if ($PythonPath) { $PythonPath } else { Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'EIS_DAILY_EXISTING_VENV_MISSING'
}
if (-not $PreviousReport) {
    $History = Join-Path $env:LOCALAPPDATA 'ServiceIT\EIS-Scope-Review'
    if (-not (Test-Path -LiteralPath $History -PathType Container)) {
        throw 'EIS_DAILY_PRIOR_SCOPE_REPORT_MISSING'
    }
    $Report = Get-ChildItem -LiteralPath $History -Recurse -Filter 'eis_scope_*.json' -File |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $Report) { throw 'EIS_DAILY_PRIOR_SCOPE_REPORT_MISSING' }
    $PreviousReport = $Report.FullName
}
if (-not (Test-Path -LiteralPath $PreviousReport -PathType Leaf)) {
    throw 'EIS_DAILY_PREVIOUS_REPORT_FILE_MISSING'
}
if (-not $StateDir) {
    $StateDir = Join-Path $env:LOCALAPPDATA 'ServiceIT\EIS-Daily-Operational'
}
Push-Location -LiteralPath $Repo
try {
    Write-Output 'EIS_DAILY_STAGE=TESTS'
    $Tests = @(
      'tests/test_eis_rss.py','tests/test_eis_real_format.py',
      'tests/test_eis_buyer_review.py','tests/test_eis_manager_queue.py',
      'tests/test_eis_bitrix_sandbox.py','tests/test_eis_transport_diagnostics.py',
      'tests/test_eis_windows_tls.py','tests/test_eis_source_diagnostics.py',
      'tests/test_eis_search_integrity.py','tests/test_eis_targeted_1c.py',
      'tests/test_eis_108_review.py','tests/test_eis_108_triage.py',
      'tests/test_eis_daily.py'
    )
    & $Python -m pytest -q -p no:cacheprovider @Tests
    if ($LASTEXITCODE -ne 0) { throw 'EIS_DAILY_OFFLINE_TESTS_FAILED' }
    Write-Output 'EIS_DAILY_STAGE=LIVE_FOUR_OFFICIAL_GETS; TLS_VERIFY=ON; NO_RETRIES=YES'
    & $Python -m leadpilot.eis_daily --source-report $PreviousReport --state-dir $StateDir
    if ($LASTEXITCODE -ne 0) {
        throw 'EIS_DAILY_LIVE_OR_STORAGE_FAILED'
    }
    Write-Output ('EIS_DAILY_PERSISTENT_STATE_DIR=' + $StateDir)
    Write-Output 'EIS_DAILY_OPERATOR_EXIT=0; CRM_WRITES=NO; QUALIFIED_B2B_LEADS=0'
} finally {
    Pop-Location
}
