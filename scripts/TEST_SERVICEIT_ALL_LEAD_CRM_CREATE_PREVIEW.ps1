# Windows PowerShell 5.1 syntax and offline-only run contract; NO network.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$runner = Join-Path $PSScriptRoot 'RUN_SERVICEIT_ALL_LEAD_CRM_CREATE_PREVIEW.ps1'
$tokens=$null;$errors=$null
[Management.Automation.Language.Parser]::ParseFile($runner,[ref]$tokens,[ref]$errors)|Out-Null
if ($errors.Count) { throw 'ALL_LEAD_CREATE_PREVIEW_PARSE_FAILED' }
$mock = Join-Path $env:TEMP ('serviceit-create-preview-'+[guid]::NewGuid().ToString('N')+'.ps1')
@'
if ($args -notcontains '-m' -or
    $args -notcontains 'leadpilot.serviceit_all_lead_crm_create_intents') {
    throw 'MODULE_NOT_PINNED'
}
if ($args -contains '--apply' -or $args -contains '--inspect-live' -or
    $args -contains '--webhook-from-env' -or $args -contains '--apply-demo') {
    throw 'LIVE_OR_WRITE_FLAG_FORBIDDEN'
}
Write-Output '{"status":"SERVICEIT_ALL_LEAD_CRM_CREATE_INTENTS_PREVIEW","crm_writes":0}'
$global:LASTEXITCODE=0
'@ | Set-Content -LiteralPath $mock -Encoding UTF8
try {
    & $runner -PythonPath $mock
    & $runner -PythonPath $mock -InputJson (Join-Path (Split-Path $PSScriptRoot -Parent) 'fixtures\serviceit_source_batch_demo.json')
    Write-Output 'SERVICEIT_ALL_LEAD_CREATE_WINDOWS=PASS; CRM_WRITES=0; NO_WEBHOOK=YES'
} finally {
    Remove-Item -LiteralPath $mock -Force -ErrorAction SilentlyContinue
}
