# PowerShell 5.1 secure-input + guarded one-shot argument smoke, fake interpreter only.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$runner=Join-Path $PSScriptRoot 'RUN_SERVICEIT_B2B_CARD7_PRELIMINARY_BACKFILL.ps1'
$tokens=$null;$errors=$null
[Management.Automation.Language.Parser]::ParseFile($runner,[ref]$tokens,[ref]$errors)|Out-Null
if ($errors.Count -ne 0) { throw 'B2B_BACKFILL_SCRIPT_PARSE_FAILED' }
$mock=Join-Path $env:TEMP ('serviceit-b2b-backfill-mock-'+[guid]::NewGuid().ToString('N')+'.ps1')
@'
if ($args -notcontains '-m' -or
    $args -notcontains 'leadpilot.serviceit_b2b_card7_preliminary_backfill') {
    throw 'MODULE_MISMATCH'
}
if ($args -contains '--inspect-live') {
    if ($env:SERVICEIT_B2B_BACKFILL_WEBHOOK -cne 'https://serviseit.bitrix24.ru/rest/2/SYNTHETIC_TOKEN_123456789') {
        throw 'SECURE_HANDOFF_FAIL'
    }
} else {
    if (Test-Path Env:\SERVICEIT_B2B_BACKFILL_WEBHOOK) { throw 'OFFLINE_LEAK' }
}
if ($args -contains '--apply-once' -and $args -notcontains '--approve-exact-item-7') {
    throw 'EXACT_APPROVAL_MISSING'
}
if ($args -contains '--approve-exact-item-7' -and $args -notcontains '--apply-once') {
    throw 'EXACT_APPLY_MISSING'
}
Write-Output '{"status":"SYNTHETIC_OPERATOR_PASS","crm_writes":0}'
$global:LASTEXITCODE=0
'@ | Set-Content -LiteralPath $mock -Encoding UTF8
try {
    function Read-Host {
        param([string]$Prompt,[switch]$AsSecureString)
        if (-not $AsSecureString) { throw 'VISIBLE_WEBHOOK_FORBIDDEN' }
        ConvertTo-SecureString 'https://serviseit.bitrix24.ru/rest/2/SYNTHETIC_TOKEN_123456789' -AsPlainText -Force
    }
    & $runner -PythonPath $mock
    & $runner -PythonPath $mock -InspectLive
    $blocked=$false
    try {
        & $runner -PythonPath $mock -InspectLive -ApplyOnce
    } catch {
        if ($_.Exception.Message -eq 'B2B_CARD7_EXACT_WRITE_APPROVAL_REQUIRED') { $blocked=$true }
        else { throw }
    }
    if (-not $blocked) { throw 'APPROVAL_GATE_FAILED' }
    & $runner -PythonPath $mock -InspectLive -ApplyOnce -ApproveExactItem7
    if (Test-Path Env:\SERVICEIT_B2B_BACKFILL_WEBHOOK) { throw 'WEBHOOK_NOT_CLEARED' }
    Write-Output 'SERVICEIT_B2B_CARD7_WINDOWS=PASS; REAL_CRM_WRITES=0'
} finally {
    if (Test-Path Env:\SERVICEIT_B2B_BACKFILL_WEBHOOK) {
        Remove-Item Env:\SERVICEIT_B2B_BACKFILL_WEBHOOK -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $mock -Force -ErrorAction SilentlyContinue
}
