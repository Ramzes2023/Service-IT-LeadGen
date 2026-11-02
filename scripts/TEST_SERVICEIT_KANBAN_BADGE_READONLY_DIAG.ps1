# Offline Windows syntax/SecureString smoke, synthetic-only interpreter stub.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$runner = Join-Path $PSScriptRoot 'RUN_SERVICEIT_KANBAN_BADGE_READONLY_DIAG.ps1'
$tokens=$null; $errors=$null
[Management.Automation.Language.Parser]::ParseFile($runner,[ref]$tokens,[ref]$errors) | Out-Null
if ($errors.Count -ne 0) { throw 'POWERSHELL_READONLY_DIAG_SYNTAX_FAILED' }
$mock = Join-Path $env:TEMP ('serviceit-diag-mock-'+[guid]::NewGuid().ToString('N')+'.ps1')
@'
if ($args -contains '--apply-demo') { throw 'WRITE_FLAG_FORBIDDEN' }
if ($args -contains '--inspect-live') {
    if ($args -notcontains '--webhook-from-env') { throw 'SECURE_FLAG_MISSING' }
    if ($env:SERVICEIT_AI_COCKPIT_WEBHOOK -cne 'https://serviseit.bitrix24.ru/rest/1607/SYNTHETICMOCKTOKEN123456') {
        throw 'WEBHOOK_HANDOFF_INVALID'
    }
} else {
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) { throw 'OFFLINE_WEBHOOK_PRESENT' }
}
Write-Output 'BADGE_DIAGNOSTIC_SYNTHETIC=PASS'
$global:LASTEXITCODE=0
'@ | Set-Content -LiteralPath $mock -Encoding UTF8
try {
    function Read-Host {
        param([string]$Prompt,[switch]$AsSecureString)
        if (-not $AsSecureString) { throw 'SECURE_STRING_REQUIRED' }
        ConvertTo-SecureString 'https://serviseit.bitrix24.ru/rest/1607/SYNTHETICMOCKTOKEN123456' -AsPlainText -Force
    }
    & $runner -PythonPath $mock
    & $runner -PythonPath $mock -InspectLive
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) { throw 'WEBHOOK_NOT_CLEARED' }
    Write-Output 'SERVICEIT_BADGE_READONLY_WINDOWS=PASS; CRM_WRITES=0'
} finally {
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) {
        Remove-Item Env:\SERVICEIT_AI_COCKPIT_WEBHOOK -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $mock -Force -ErrorAction SilentlyContinue
}
