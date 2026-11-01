# Windows PowerShell 5.1 offline smoke only. No keys, no model, no Bitrix.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$runner = Join-Path $PSScriptRoot 'RUN_SERVICEIT_GIGACHAT_COCKPIT_PREFLIGHT.ps1'
$tokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile($runner, [ref]$tokens, [ref]$parseErrors) | Out-Null
if ($parseErrors.Count -ne 0) { throw 'PREFLIGHT_POWERSHELL_PARSE_FAILED' }
$mock = Join-Path $env:TEMP ('serviceit-giga-mock-' + [guid]::NewGuid().ToString('N') + '.ps1')
@'
if ($args -contains '--live-synthetic') {
    if ($env:GIGACHAT_CREDENTIALS -ne 'SYNTHETIC_MOCK_CREDENTIAL') {
        throw 'SYNTHETIC_CREDENTIAL_NOT_IN_CHILD_ENV'
    }
    if ($env:AI_ENABLE -ne '1' -or $env:AI_PROVIDER -ne 'gigachat') {
        throw 'MODEL_OPT_IN_MISSING'
    }
    Write-Output 'SYNTHETIC_LIVE_MOCK=PASS'
} else {
    if ($args -contains '--live-synthetic') { throw 'OFFLINE_MODEL_NOT_ALLOWED' }
    Write-Output 'SYNTHETIC_OFFLINE_MOCK=PASS'
}
$global:LASTEXITCODE = 0
'@ | Set-Content -LiteralPath $mock -Encoding UTF8
$names = @('GIGACHAT_CREDENTIALS','AI_ENABLE','AI_PROVIDER')
$saved = @{}
foreach ($name in $names) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name,'Process')
}
try {
    Remove-Item Env:\GIGACHAT_CREDENTIALS -ErrorAction SilentlyContinue
    & $runner -PythonPath $mock
    if ($LASTEXITCODE -ne 0) { throw 'OFFLINE_FAILED' }
    $env:GIGACHAT_CREDENTIALS = 'SYNTHETIC_MOCK_CREDENTIAL'
    & $runner -PythonPath $mock -UseExistingGigaChat
    if ($LASTEXITCODE -ne 0) { throw 'SYNTHETIC_MOCK_FAILED' }
    if ($env:GIGACHAT_CREDENTIALS -ne 'SYNTHETIC_MOCK_CREDENTIAL') {
        throw 'ENV_ORIGINAL_NOT_RESTORED'
    }
    Write-Output 'SERVICEIT_WINDOWS_GIGACHAT_PREFLIGHT=PASS; EXTERNAL_CALLS=0; CRM_WRITES=0'
} finally {
    foreach ($name in $names) {
        [Environment]::SetEnvironmentVariable($name,$saved[$name],'Process')
    }
    Remove-Item -LiteralPath $mock -Force -ErrorAction SilentlyContinue
}
