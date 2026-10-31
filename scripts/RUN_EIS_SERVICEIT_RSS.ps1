# Requires PowerShell 5.1+, Python 3.12 .venv and a REAL official EIS RSS URL.
# No credentials, scraping, CRM writes, outreach or certificate bypass.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$RssUrl
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'EIS_PYTHON_VENV_NOT_FOUND: create the project .venv before running.'
}
Push-Location -LiteralPath $Repo
try {
    Write-Output 'EIS_SERVICEIT_STAGE=OFFLINE_TESTS'
    & $Python -m pytest -q -p no:cacheprovider tests/test_eis_rss.py
    if ($LASTEXITCODE -ne 0) {
        throw 'EIS_OFFLINE_TESTS_FAILED'
    }
    $Stamp = (Get-Date).ToString('yyyyMMdd-HHmmss-fff')
    $Output = 'exports/eis_research_' + $Stamp + '.json'
    Write-Output 'EIS_SERVICEIT_STAGE=ONE_OFFICIAL_RSS_GET'
    & $Python -m leadpilot.eis_rss --url $RssUrl --output $Output
    if ($LASTEXITCODE -ne 0) {
        Write-Output 'EIS_LIVE_CONNECTION=NOT_CONFIRMED; ZERO_QUALIFIED_LEADS_CLAIMED'
        throw 'EIS_OFFICIAL_RSS_NOT_VERIFIED'
    }
    $FullPath = Join-Path $Repo $Output
    if (-not (Test-Path -LiteralPath $FullPath -PathType Leaf)) {
        throw 'EIS_OUTPUT_MISSING'
    }
    $Report = Get-Content -LiteralPath $FullPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($Report.mode -ne 'EIS_OFFICIAL_RSS_RESEARCH_ONLY' -or $Report.crm_writes -ne 'NO') {
        throw 'EIS_OUTPUT_SCHEMA_OR_SAFETY_INVALID'
    }
    Write-Output ('EIS_LIVE_RESPONSE=PARSED; CANDIDATES_FOR_MANUAL_REVIEW=' +
                  $Report.counts.candidates_for_identity_review)
    Write-Output ('EIS_OUTPUT_PATH=' + $FullPath)
    Write-Output 'SMALL_BUSINESS_INN_FNS_SCOPE=UNVERIFIED'
    Write-Output 'CRM_READY=0; CRM_WRITES=NO; OUTREACH=NO'
} finally {
    Pop-Location
}
