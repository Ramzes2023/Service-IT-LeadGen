# Mock-only Windows syntax + SecureString read-only code path.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$runner=Join-Path $PSScriptRoot 'RUN_SERVICEIT_ALL_LEAD_CRM_SCHEMA_READONLY.ps1'
$tokens=$null;$parseErrors=$null
[Management.Automation.Language.Parser]::ParseFile($runner,[ref]$tokens,[ref]$parseErrors)|Out-Null
if ($parseErrors.Count) { throw 'ALL_LEAD_SCHEMA_POWERSHELL_PARSE_FAILED' }
$mock=Join-Path $env:TEMP ('serviceit-all-schema-'+[guid]::NewGuid().ToString('N')+'.ps1')
@'
if ($args -contains '--apply' -or $args -contains '--apply-demo' -or $args -contains '--apply-reconciliation') {
    throw 'WRITE_FLAGS_FORBIDDEN'
}
if ($args -contains '--inspect-live') {
    if ($args -notcontains '--webhook-from-env') { throw 'SECURE_FLAG_MISSING' }
    if ($env:SERVICEIT_AI_COCKPIT_WEBHOOK -cne 'https://serviseit.bitrix24.ru/rest/1607/SYNTHETICMOCKTOKEN123456') {
        throw 'SECURE_WEBHOOK_HANDOFF_INVALID'
    }
} else {
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) { throw 'OFFLINE_WEBHOOK_PRESENT' }
}
Write-Output '{"status":"SERVICEIT_ALL_LEAD_SCHEMA_READONLY_PASS","crm_writes":0}'
$global:LASTEXITCODE=0
'@ | Set-Content -LiteralPath $mock -Encoding UTF8
try {
    function Read-Host {
        param([string]$Prompt,[switch]$AsSecureString)
        if (-not $AsSecureString) { throw 'HIDDEN_INPUT_REQUIRED' }
        ConvertTo-SecureString 'https://serviseit.bitrix24.ru/rest/1607/SYNTHETICMOCKTOKEN123456' -AsPlainText -Force
    }
    & $runner -PythonPath $mock
    & $runner -PythonPath $mock -InspectLive
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) { throw 'WEBHOOK_NOT_REMOVED' }
    Write-Output 'SERVICEIT_ALL_LEAD_SCHEMA_WINDOWS=PASS; NETWORK=0; CRM_WRITES=0'
} finally {
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) {
        Remove-Item Env:\SERVICEIT_AI_COCKPIT_WEBHOOK -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $mock -Force -ErrorAction SilentlyContinue
}
