# Strictly Windows PowerShell 5.1 parse and fake interpreter; no network.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$runner=Join-Path $PSScriptRoot 'RUN_SERVICEIT_B2B_CARD7_POSTAPPLY_READONLY.ps1'
$tokens=$null;$errors=$null
[Management.Automation.Language.Parser]::ParseFile($runner,[ref]$tokens,[ref]$errors)|Out-Null
if ($errors.Count) { throw 'POSTAPPLY_READONLY_PS_PARSE_FAILED' }
$mock=Join-Path $env:TEMP ('serviceit-postapply-readonly-'+[guid]::NewGuid().ToString('N')+'.ps1')
@'
if ($args -notcontains '-m' -or
    $args -notcontains 'leadpilot.serviceit_b2b_card7_postapply_readonly') {
    throw 'WRONG_READONLY_MODULE'
}
if ($args -contains '--apply-once' -or
    $args -contains '--approve-exact-item-7' -or
    $args -contains 'crm.item.update') {
    throw 'ANY_WRITE_FLAGS_FORBIDDEN'
}
if ($args -notcontains '--inspect-live' -or
    $args -notcontains '--webhook-from-env') { throw 'READONLY_FLAGS_MISSING' }
if ($env:SERVICEIT_B2B_CARD7_POSTAPPLY_WEBHOOK -cne
    'https://serviseit.bitrix24.ru/rest/2/FAKE_POSTAPPLY_TOKEN123456') {
    throw 'HIDDEN_SECRET_HANDOFF_BROKEN'
}
Write-Output '{"status":"B2B_CARD7_POSTAPPLY_READONLY_RECONCILIATION","crm_writes":0}'
$global:LASTEXITCODE=0
'@ | Set-Content -LiteralPath $mock -Encoding UTF8
try {
    function Read-Host {
        param([string]$Prompt,[switch]$AsSecureString)
        if (-not $AsSecureString) { throw 'SECRET_SHOULD_BE_HIDDEN' }
        ConvertTo-SecureString 'https://serviseit.bitrix24.ru/rest/2/FAKE_POSTAPPLY_TOKEN123456' -AsPlainText -Force
    }
    & $runner -PythonPath $mock
    if (Test-Path Env:\SERVICEIT_B2B_CARD7_POSTAPPLY_WEBHOOK) {
        throw 'SECRET_NOT_CLEARED'
    }
    Write-Output 'SERVICEIT_B2B_POSTAPPLY_WINDOWS=PASS; CRM_WRITES=0'
} finally {
    if (Test-Path Env:\SERVICEIT_B2B_CARD7_POSTAPPLY_WEBHOOK) {
        Remove-Item Env:\SERVICEIT_B2B_CARD7_POSTAPPLY_WEBHOOK -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $mock -Force -ErrorAction SilentlyContinue
}
