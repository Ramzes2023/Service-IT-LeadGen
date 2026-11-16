# One official public RSS GET for 1CLancer tasks. Research-only, no CRM.
[CmdletBinding()]
param([string]$PythonPath = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Python = if ($PythonPath) { $PythonPath } else { Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'CLANCER_PYTHON_VENV_MISSING'
}
Push-Location -LiteralPath $Repo
try {
    Write-Output 'CLANCER_STAGE=TEST'
    & $Python -m pytest -q -p no:cacheprovider tests/test_oneclancer_rss.py
    if ($LASTEXITCODE -ne 0) { throw 'CLANCER_TESTS_FAILED' }
    $Stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
    $JsonOut = 'exports/oneclancer_tasks_' + $Stamp + '.json'
    $CsvOut = 'exports/oneclancer_tasks_' + $Stamp + '.csv'
    Write-Output 'CLANCER_STAGE=ONE_OFFICIAL_TASK_RSS_GET'
    & $Python -m leadpilot.oneclancer_rss --json $JsonOut --csv $CsvOut
    if ($LASTEXITCODE -ne 0) { throw 'CLANCER_PUBLIC_RSS_NOT_VERIFIED' }
    $Doc = Get-Content -LiteralPath $JsonOut -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($Doc.mode -ne 'OFFICIAL_1CLANCER_RSS_TASK_RESEARCH_ONLY' -or
        $Doc.crm_writes -ne 'NO' -or $Doc.outbound_messages -ne 'NO' -or
        $Doc.counts.qualified_b2b_leads -ne 0 -or $Doc.counts.crm_ready -ne 0) {
        throw 'CLANCER_UNSAFE_RESEARCH_REPORT'
    }
    Write-Output ('CLANCER_RSS_ITEMS=' + $Doc.counts.items_seen)
    Write-Output ('CLANCER_RESEARCH_TASKS=' + $Doc.counts.research_task_signals)
    Write-Output 'CLANCER_FIRST_PUBLIC_TASKS_ASCII:'
    $Evidence = @($Doc.items) | Select-Object -First 8
    $Evidence | ForEach-Object { 
        Write-Output ('TASK_ID=' + $_.task_id + '; TITLE=' + $_.title +
            '; URL=' + $_.source_url + '; PUBLISHED=' + $_.published_on)
    }
    Write-Output ('CLANCER_OUTPUT_JSON=' + (Resolve-Path -LiteralPath $JsonOut).Path)
    Write-Output ('CLANCER_OUTPUT_CSV=' + (Resolve-Path -LiteralPath $CsvOut).Path)
    Write-Output 'CLANCER_RSS_ACCEPTANCE=PASS; READY_B2B_LEADS=0; CRM_WRITES=NO'
} finally {
    Pop-Location
}
