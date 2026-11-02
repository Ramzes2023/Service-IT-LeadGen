# Synthetic PowerShell 5.1 parser + secure argument handoff. No network.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$script=Join-Path $PSScriptRoot 'RUN_SERVICEIT_B2B_CARD7_PREWRITE_STAGE_READONLY.ps1'
$tokens=$null;$errors=$null
[Management.Automation.Language.Parser]::ParseFile($script,[ref]$tokens,[ref]$errors)|Out-Null
if ($errors.Count -ne 0) { throw 'PREWRITE_PS_PARSE_ERROR' }
$mock=Join-Path $env:TEMP ('serviceit-prewrite-mock-'+[guid]::NewGuid().ToString('N')+'.ps1')
@'
if ($args -notcontains '-m' -or $args -notcontains 'leadpilot.serviceit_b2b_card7_prewrite_stage_readonly') { throw 'UNEXPECTED_MODULE' }
if ($args -contains '--apply-once' -or $args -contains '--approve-exact-item-7') { throw 'WRITE_FLAGS_NOT_ALLOWED' }
if ($args -notcontains '--inspect-live' -or $args -notcontains '--webhook-from-env') { throw 'READ_FLAGS_NOT_FOUND' }
if ($env:SERVICEIT_B2B_CARD7_PREWRITE_WEBHOOK -cne 'https://serviseit.bitrix24.ru/rest/2/SYNTHETIC_FOR_TEST_123456') { throw 'SECRET_NOT_HANDLED' }
Write-Output '{"status":"B2B_CARD7_PREWRITE_STAGE_READONLY","crm_writes":0}'
$global:LASTEXITCODE=0
'@ | Set-Content -LiteralPath $mock -Encoding UTF8
try {
    function Read-Host {
        param([string]$Prompt,[switch]$AsSecureString)
        if (-not $AsSecureString) { throw 'SECRET_NOT_HIDDEN' }
        ConvertTo-SecureString 'https://serviseit.bitrix24.ru/rest/2/SYNTHETIC_FOR_TEST_123456' -AsPlainText -Force
    }
    & $script -PythonPath $mock
    if (Test-Path Env:\SERVICEIT_B2B_CARD7_PREWRITE_WEBHOOK) { throw 'WEBHOOK_NOT_CLEARED' }
    Write-Output 'SERVICEIT_B2B_CARD7_PREWRITE_PS5=PASS; CRM_WRITES=0'
} finally {
    if (Test-Path Env:\SERVICEIT_B2B_CARD7_PREWRITE_WEBHOOK) { Remove-Item Env:\SERVICEIT_B2B_CARD7_PREWRITE_WEBHOOK -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $mock -Force -ErrorAction SilentlyContinue
}
