# Windows PowerShell 5.1 syntax and offline-only operator smoke, no CRM or AI access.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$runner = Join-Path $PSScriptRoot 'RUN_SERVICEIT_ALL_LEAD_SCORE_PREVIEW.ps1'
$tokens = $null; $errors = $null
[Management.Automation.Language.Parser]::ParseFile($runner,[ref]$tokens,[ref]$errors) | Out-Null
if ($errors.Count -ne 0) { throw 'SOURCE_BATCH_PS_PARSE_ERROR' }
$mock = Join-Path $env:TEMP ('serviceit-offline-batch-' + [guid]::NewGuid().ToString('N') + '.ps1')
$inputFile = Join-Path (Split-Path $PSScriptRoot -Parent) 'fixtures\serviceit_source_batch_demo.json'
@'
if ($args -notcontains '-m') { throw 'NO_MODULE_ARGUMENT' }
if ($args -notcontains 'leadpilot.serviceit_source_batch_pipeline') { throw 'WRONG_MODULE' }
if ($args -contains '--webhook-from-env' -or $args -contains '--apply-demo' -or
    $args -contains '--apply-reconciliation' -or $args -contains 'crm.item.update') {
    throw 'WRITE_OR_WEBHOOK_ARGUMENT_FORBIDDEN'
}
Write-Output '{"status":"SERVICEIT_ALL_LEAD_BATCH_PREVIEW","crm_writes":0}'
$global:LASTEXITCODE = 0
'@ | Set-Content -LiteralPath $mock -Encoding UTF8
try {
    & $runner -PythonPath $mock
    & $runner -PythonPath $mock -InputJson $inputFile
    Write-Output 'SERVICEIT_SOURCE_BATCH_PS_SMOKE=PASS; NETWORK=0; CRM_WRITES=0'
} finally {
    Remove-Item -LiteralPath $mock -Force -ErrorAction SilentlyContinue
}
