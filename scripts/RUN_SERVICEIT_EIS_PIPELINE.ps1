# Service IT EIS: test -> official RSS -> buyer FNS -> manager review.
# All operational outputs stay local in ignored exports; no CRM writes.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$RssUrl,
    [string]$FnsFile = '',
    [string]$BuyerFacts = '',
    [string]$CaBundle = '',
    [string]$PythonPath = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Python = if ($PythonPath) { $PythonPath } else { Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'SERVICEIT_EIS_PYTHON_MISSING'
}
Push-Location -LiteralPath $Repo
try {
    Write-Output 'SERVICEIT_EIS_PIPELINE=START'
    $Tests = @(
      'tests/test_eis_rss.py',
      'tests/test_eis_real_format.py',
      'tests/test_eis_buyer_review.py',
      'tests/test_eis_manager_queue.py',
      'tests/test_eis_bitrix_sandbox.py',
      'tests/test_eis_transport_diagnostics.py',
      'tests/test_eis_windows_tls.py'
    )
    foreach ($Test in $Tests) {
        if (-not (Test-Path -LiteralPath $Test -PathType Leaf)) {
            throw ('SERVICEIT_EIS_TEST_FILE_MISSING=' + $Test)
        }
    }
    & $Python -m pytest -q -p no:cacheprovider $Tests
    if ($LASTEXITCODE -ne 0) { throw 'SERVICEIT_EIS_TESTS_FAILED' }
    $Id = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
    $Source = ('exports/eis_source_' + $Id + '.json')
    $Cmd = @('-m', 'leadpilot.eis_rss', '--url', $RssUrl, '--output', $Source)
    if ($CaBundle) {
        if (-not (Test-Path -LiteralPath $CaBundle -PathType Leaf)) {
            throw 'SERVICEIT_EIS_CA_NOT_FOUND'
        }
        $Cmd += @('--ca-bundle', $CaBundle)
    }
    Write-Output 'SERVICEIT_EIS_STAGE=OFFICIAL_RSS_READ'
    & $Python @Cmd
    if ($LASTEXITCODE -ne 0) {
        Write-Output 'SERVICEIT_EIS_REAL_SOURCE=NOT_VERIFIED'
        Write-Output 'SERVICEIT_EIS_QUALIFIED_LEADS=0; CRM_WRITES=NO'
        throw 'SERVICEIT_EIS_RSS_UNAVAILABLE'
    }
    $Parsed = Get-Content -LiteralPath $Source -Encoding UTF8 -Raw | ConvertFrom-Json
    if ($Parsed.mode -ne 'EIS_OFFICIAL_RSS_RESEARCH_ONLY' -or $Parsed.crm_writes -ne 'NO') {
        throw 'SERVICEIT_EIS_SOURCE_SCHEMA_INVALID'
    }
    Write-Output ('SERVICEIT_EIS_RSS_SOURCE=' + (Resolve-Path -LiteralPath $Source).Path)
    Write-Output ('SERVICEIT_EIS_RESEARCH_CANDIDATES=' + $Parsed.counts.candidates_for_identity_review)
    if (([bool]$FnsFile) -xor ([bool]$BuyerFacts)) {
        throw 'SERVICEIT_EIS_FNS_AND_BUYER_FACTS_MUST_BOTH_BE_PROVIDED'
    }
    if (-not $FnsFile -and -not $BuyerFacts) {
        Write-Output 'SERVICEIT_EIS_BUYER_FNS_REVIEW=PENDING_OPERATOR_INPUT'
        Write-Output 'SERVICEIT_EIS_MANAGER_QUEUE=NOT_GENERATED_NO_BUYER_VALIDATION'
        Write-Output 'SERVICEIT_EIS_CRM_WRITES=NO; OUTBOUND=NO'
        return
    }
    if (-not (Test-Path -LiteralPath $FnsFile -PathType Leaf) -or
        -not (Test-Path -LiteralPath $BuyerFacts -PathType Leaf)) {
        throw 'SERVICEIT_EIS_REQUIRED_FNS_OR_BUYER_FACTS_NOT_FOUND'
    }
    $Audit = ('exports/eis_buyer_audit_' + $Id + '.json')
    Write-Output 'SERVICEIT_EIS_STAGE=EXACT_INN_FNS_MANUAL_GATE'
    $AuditArgs = @('-m', 'leadpilot.eis_buyer_review', '--eis', $Source,
        '--fns', $FnsFile, '--buyer-facts', $BuyerFacts, '--output', $Audit)
    & $Python @AuditArgs
    if ($LASTEXITCODE -ne 0) { throw 'SERVICEIT_EIS_BUYER_FNS_GATE_FAILED' }
    $ManagerJson = ('exports/eis_manager_queue_' + $Id + '.json')
    $ManagerCsv = ('exports/eis_manager_queue_' + $Id + '.csv')
    Write-Output 'SERVICEIT_EIS_STAGE=MANAGER_REVIEW_QUEUE'
    $QueueArgs = @('-m', 'leadpilot.eis_manager_queue', '--audit', $Audit,
        '--json', $ManagerJson, '--csv', $ManagerCsv)
    & $Python @QueueArgs
    if ($LASTEXITCODE -ne 0) { throw 'SERVICEIT_EIS_MANAGER_QUEUE_FAILED' }
    $Final = Get-Content -LiteralPath $ManagerJson -Encoding UTF8 -Raw | ConvertFrom-Json
    if ($Final.mode -ne 'EIS_MANUAL_MANAGER_QUEUE' -or
        $Final.crm_writes -ne 'NO' -or $Final.counts.crm_ready -ne 0) {
        throw 'SERVICEIT_EIS_EXPORT_POLICY_FAILED'
    }
    Write-Output ('SERVICEIT_EIS_MANAGER_REVIEW_COUNT=' + $Final.counts.manager_review_companies)
    Write-Output ('SERVICEIT_EIS_MANAGER_CSV=' + (Resolve-Path -LiteralPath $ManagerCsv).Path)
    Write-Output ('SERVICEIT_EIS_MANAGER_JSON=' + (Resolve-Path -LiteralPath $ManagerJson).Path)
    Write-Output 'SERVICEIT_EIS_READY_FOR_MANAGER_HUMAN_REVIEW=YES'
    Write-Output 'SERVICEIT_EIS_CRM_DELIVERY=BLOCKED_PENDING_APPROVALS_AND_TEST_PORTAL'
    Write-Output 'SERVICEIT_EIS_CRM_WRITES=NO; OUTBOUND=NO'
} finally {
    Pop-Location
}
