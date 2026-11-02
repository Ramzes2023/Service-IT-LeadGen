# Pure local SQLite probe, Windows PowerShell 5.1, no corporate REST.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$runner=Join-Path $PSScriptRoot 'RUN_SERVICEIT_CARD7_LOCAL_SQLITE_PROBE.ps1'
$tokens=$null;$parseErrors=$null
[Management.Automation.Language.Parser]::ParseFile($runner,[ref]$tokens,[ref]$parseErrors)|Out-Null
if ($parseErrors.Count) { throw 'LOCAL_SQLITE_PROBE_PS_PARSE_FAILED' }
$mock=Join-Path $env:TEMP ('serviceit-sqlite-local-'+[guid]::NewGuid().ToString('N')+'.ps1')
@'
if ($args -notcontains '-m' -or
    $args -notcontains 'leadpilot.serviceit_card7_sqlite_local_probe' -or
    $args -notcontains '--local-only') { throw 'INVALID_SQLITE_ONLY_CLI' }
if ($args -contains '--inspect-live' -or $args -contains '--apply-once') {
    throw 'LIVE_FLAGS_FORBIDDEN'
}
Write-Output '{"status":"LOCAL_SQLITE_PROBE_PASS","crm_writes":0}'
$global:LASTEXITCODE=0
'@ | Set-Content -LiteralPath $mock -Encoding UTF8
try {
    & $runner -PythonPath $mock
    Write-Output 'SERVICEIT_LOCAL_SQLITE_PS5_SMOKE=PASS; CRM_WRITES=0'
} finally {
    Remove-Item -LiteralPath $mock -Force -ErrorAction SilentlyContinue
}
