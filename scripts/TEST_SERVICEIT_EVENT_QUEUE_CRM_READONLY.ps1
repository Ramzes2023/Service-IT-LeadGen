# No network, only PS5.1 parser and synthetic CLI handoff.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$runner=Join-Path $PSScriptRoot 'RUN_SERVICEIT_EVENT_QUEUE_CRM_READONLY.ps1'
$tokens=$null;$errors=$null
[Management.Automation.Language.Parser]::ParseFile($runner,[ref]$tokens,[ref]$errors)|Out-Null
if ($errors.Count) { throw 'EVENT_QUEUE_WORKER_PS_PARSE_FAILED' }
$fake=Join-Path $env:TEMP ('serviceit-queue-worker-'+[guid]::NewGuid().ToString('N')+'.ps1')
@'
if ($args -notcontains '-m' -or
    $args -notcontains 'leadpilot.serviceit_bitrix_event_queue_readonly_worker') {
    throw 'INVALID_OFFLINE_MODULE'
}
if ($args -contains '--inspect-live' -or $args -contains '--webhook-from-env') {
    throw 'UNAUTHORIZED_LIVE_FLAG'
}
Write-Output '{"status":"SERVICEIT_EVENT_QUEUE_WORKER_OFFLINE","crm_writes":0}'
$global:LASTEXITCODE=0
'@ | Set-Content -LiteralPath $fake -Encoding UTF8
try {
    & $runner -PythonPath $fake
    Write-Output 'SERVICEIT_EVENT_QUEUE_PS5_SMOKE=PASS; CRM_WRITES=0'
} finally {
    Remove-Item -LiteralPath $fake -Force -ErrorAction SilentlyContinue
}
