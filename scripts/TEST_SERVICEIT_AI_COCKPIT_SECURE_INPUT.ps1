# Offline Windows-only regression: no portal, tokens, network, or CRM writes.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$operator = Join-Path $PSScriptRoot 'RUN_SERVICEIT_AI_COCKPIT_DEMO.ps1'
$tokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile($operator, [ref]$tokens, [ref]$parseErrors) | Out-Null
if ($parseErrors.Count -ne 0) { throw 'POWERSHELL_OPERATOR_PARSE_FAILED' }
$fixture = 'https://serviseit.bitrix24.ru/rest/1607/SYNTHETICMOCKTOKEN123456'
$mock = Join-Path $env:TEMP ('serviceit-cockpit-mock-' + [guid]::NewGuid().ToString('N') + '.ps1')
@'
if ($env:SERVICEIT_AI_COCKPIT_WEBHOOK -cne 'https://serviseit.bitrix24.ru/rest/1607/SYNTHETICMOCKTOKEN123456') {
    throw 'SYNTHETIC_HANDOFF_FAILED'
}
if ($args -notcontains '--webhook-from-env') { throw 'SECURE_HANDOFF_FLAG_MISSING' }
if ($args -notcontains '--inspect-live') { throw 'INSPECT_ONLY_FLAG_MISSING' }
if ($args -contains '--apply-demo') { throw 'WRITE_FLAG_FORBIDDEN' }
$global:LASTEXITCODE = 0
Write-Output 'SYNTHETIC_SECURE_HANDOFF=PASS'
'@ | Set-Content -LiteralPath $mock -Encoding UTF8
try {
    function Read-Host {
        param([string]$Prompt, [switch]$AsSecureString)
        if (-not $AsSecureString) { throw 'SECURESTRING_PROMPT_REQUIRED' }
        return (ConvertTo-SecureString 'https://serviseit.bitrix24.ru/rest/1607/SYNTHETICMOCKTOKEN123456' -AsPlainText -Force)
    }
    & $operator -Mode preview -InspectLive -PythonPath $mock
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) { throw 'WEBHOOK_ENV_NOT_CLEANED' }
    Write-Output 'SERVICEIT_WINDOWS_SECURE_INPUT_SMOKE=PASS; NETWORK=0; CRM_WRITES=0'
} finally {
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) {
        Remove-Item Env:\SERVICEIT_AI_COCKPIT_WEBHOOK -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $mock -Force -ErrorAction SilentlyContinue
}
