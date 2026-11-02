# Offline-only PowerShell 5.1 smoke. No Bitrix API, no model, no CRM data.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$runner = Join-Path $PSScriptRoot 'RUN_SERVICEIT_KANBAN_SCORE_BADGE.ps1'
$tokens = $null; $parseErrors = $null
[Management.Automation.Language.Parser]::ParseFile($runner, [ref]$tokens, [ref]$parseErrors) | Out-Null
if ($parseErrors.Count) { throw 'BADGE_RUNNER_PARSE_ERROR' }
$mock = Join-Path $env:TEMP ('serviceit-badge-mock-' + [guid]::NewGuid().ToString('N') + '.ps1')
@'
if ($args -contains '--inspect-live') {
    if ($env:SERVICEIT_AI_COCKPIT_WEBHOOK -cne
        'https://serviseit.bitrix24.ru/rest/1607/SYNTHETICMOCKTOKEN123456') {
        throw 'SECURE_HANDOFF_FAILED'
    }
    if ($args -notcontains '--webhook-from-env') { throw 'NO_WEBHOOK_FROM_ENV_FLAG' }
    if ($args -contains '--apply-demo') {
        if ($args -notcontains '--confirm-demo-only') { throw 'DEMO_APPROVAL_FLAG_MISSING' }
        if ($args -notcontains '--state-dir') { throw 'NO_FIXED_STATE_DIRECTORY' }
    } else {
        if ($args -contains '--confirm-demo-only') { throw 'READONLY_IS_NOT_READONLY' }
    }
} else {
    if ($args -contains '--apply-demo') { throw 'OFFLINE_CANNOT_WRITE' }
}
Write-Output 'SCORE_BADGE_SYNTHETIC_OPERATOR=PASS'
$global:LASTEXITCODE = 0
'@ | Set-Content -LiteralPath $mock -Encoding UTF8
try {
    function Read-Host {
        param([string]$Prompt, [switch]$AsSecureString)
        if (-not $AsSecureString) { throw 'EXPECTED_SECURE_STRING' }
        ConvertTo-SecureString 'https://serviseit.bitrix24.ru/rest/1607/SYNTHETICMOCKTOKEN123456' -AsPlainText -Force
    }
    & $runner -PythonPath $mock
    if ($LASTEXITCODE -ne 0) { throw 'OFFLINE_SMOKE_FAILED' }
    & $runner -PythonPath $mock -InspectLive
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) { throw 'WEBHOOK_PERSISTED' }
    $blocked = $false
    try { & $runner -PythonPath $mock -ApplyDemo } catch {
        if ($_.Exception.Message -eq 'APPLY_REQUIRES_INSPECT_LIVE_AND_CONFIRM_DEMO_ONLY') {
            $blocked = $true
        } else { throw }
    }
    if (-not $blocked) { throw 'NO_DEMO_APPROVAL_GATE' }
    & $runner -PythonPath $mock -InspectLive -ApplyDemo -ConfirmDemoOnly
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) { throw 'WEBHOOK_NOT_CLEARED' }
    Write-Output 'SERVICEIT_WINDOWS_SCORE_BADGE=PASS; CRM_WRITES=0; NETWORK=0'
} finally {
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) {
        Remove-Item Env:\SERVICEIT_AI_COCKPIT_WEBHOOK -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $mock -Force -ErrorAction SilentlyContinue
}
