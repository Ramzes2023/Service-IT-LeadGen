# PowerShell 5.1 no-network synthetic smoke. No real Bitrix calls or credentials.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$runner=Join-Path $PSScriptRoot 'RUN_SERVICEIT_KANBAN_BADGE_RECONCILIATION.ps1'
$tokens=$null;$parseErrors=$null
[Management.Automation.Language.Parser]::ParseFile($runner,[ref]$tokens,[ref]$parseErrors)|Out-Null
if ($parseErrors.Count -ne 0) { throw 'RECONCILIATION_RUNNER_PARSE_FAILED' }
$mock=Join-Path $env:TEMP ('serviceit-reconcile-'+[guid]::NewGuid().ToString('N')+'.ps1')
@'
if ($args -contains '--inspect-live') {
    if ($args -notcontains '--webhook-from-env' -or
        $env:SERVICEIT_AI_COCKPIT_WEBHOOK -cne 'https://serviseit.bitrix24.ru/rest/1607/SYNTHETICMOCKTOKEN123456') {
        throw 'SECURE_WEBHOOK_HANDOFF_FAILED'
    }
}
if ($args -contains '--apply-reconciliation') {
    if ($args -notcontains '--approve-one-reconciliation') { throw 'RECONCILIATION_APPROVAL_MISSING' }
} else {
    if ($args -contains '--approve-one-reconciliation') { throw 'APPROVAL_FOR_READONLY_FORBIDDEN' }
}
Write-Output 'SYNTHETIC_KANBAN_RECONCILIATION=PASS'
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
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) { throw 'WEBHOOK_WAS_NOT_CLEARED' }
    $blocked=$false
    try { & $runner -PythonPath $mock -InspectLive -ApplyReconciliation } catch {
        if ($_.Exception.Message -eq 'REPAIR_APPLY_REQUIRES_LIVE_AND_EXPLICIT_APPROVAL') {
            $blocked=$true
        } else { throw }
    }
    if (-not $blocked) { throw 'REPAIR_APPROVAL_GATE_MISSING' }
    & $runner -PythonPath $mock -InspectLive -ApplyReconciliation -ApproveOneReconciliation
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) { throw 'WEBHOOK_NOT_CLEARED_AFTER_APPROVED_TEST' }
    Write-Output 'SERVICEIT_WINDOWS_BADGE_RECONCILIATION=PASS; NETWORK=0; CRM_WRITES=0'
} finally {
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) {
        Remove-Item Env:\SERVICEIT_AI_COCKPIT_WEBHOOK -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $mock -Force -ErrorAction SilentlyContinue
}
