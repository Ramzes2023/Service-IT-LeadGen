# Windows PowerShell 5.1; synthetic-only smoke of the *write operator's*
# environment handoff. The interpreter is mocked: zero Bitrix and AI calls.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$runner = Join-Path $PSScriptRoot 'RUN_SERVICEIT_AI_COCKPIT_DEMO.ps1'
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile($runner, [ref]$tokens, [ref]$errors) | Out-Null
if ($errors.Count -ne 0) { throw 'COCKPIT_POWERSHELL_PARSE_FAILED' }
$mock = Join-Path $env:TEMP ('serviceit-giga-handoff-mock-' + [guid]::NewGuid().ToString('N') + '.ps1')
@'
if ($env:SERVICEIT_AI_COCKPIT_WEBHOOK -cne 'https://serviseit.bitrix24.ru/rest/1607/SYNTHETICMOCKTOKEN123456') {
    throw 'SYNTHETIC_BITRIX_HANDOFF_FAILED'
}
if ($env:GIGACHAT_CREDENTIALS -ne 'SYNTHETIC_MOCK_CREDENTIAL') {
    throw 'SYNTHETIC_GIGACHAT_HANDOFF_FAILED'
}
if ($env:AI_ENABLE -ne '1' -or $env:AI_PROVIDER -ne 'gigachat') {
    throw 'GIGACHAT_MISSING_LOCAL_OPT_IN'
}
foreach ($flag in @('--webhook-from-env','--inspect-live','--apply-demo',
                    '--gigachat-opt-in','--synthetic-privacy-opt-in','populate')) {
    if ($args -notcontains $flag) { throw 'REQUIRED_OPERATOR_FLAG_MISSING' }
}
Write-Output 'SYNTHETIC_COCKPIT_GIGACHAT_HANDOFF=PASS'
$global:LASTEXITCODE = 0
'@ | Set-Content -LiteralPath $mock -Encoding UTF8
$names = @('GIGACHAT_CREDENTIALS','GIGACHAT_SCOPE','GIGACHAT_MODEL',
           'GIGACHAT_CA_BUNDLE_FILE','AI_ENABLE','AI_PROVIDER')
$saved = @{}
foreach ($name in $names) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name,'Process')
}
try {
    function Read-Host {
        param([string]$Prompt, [switch]$AsSecureString)
        if (-not $AsSecureString) { throw 'SECURE_PROMPT_REQUIRED' }
        ConvertTo-SecureString 'https://serviseit.bitrix24.ru/rest/1607/SYNTHETICMOCKTOKEN123456' -AsPlainText -Force
    }
    $blocked = $false
    try {
        & $runner -Mode preview -InspectLive -UseExistingGigaChat -PythonPath $mock
    } catch {
        if ($_.Exception.Message -eq 'LOCAL_GIGACHAT_KEY_OPT_IN_REQUIRES_EXACT_DEMO_POPULATE_FLAGS') {
            $blocked = $true
        } else { throw }
    }
    if (-not $blocked) { throw 'NON_DEMO_LOCAL_KEY_USE_NOT_BLOCKED' }
    $env:GIGACHAT_CREDENTIALS = 'SYNTHETIC_MOCK_CREDENTIAL'
    $env:AI_ENABLE = '0'
    $env:AI_PROVIDER = ''
    & $runner -Mode populate -InspectLive -ApplyDemo -GigaChatOptIn -SyntheticPrivacyOptIn -UseExistingGigaChat -PythonPath $mock
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) { throw 'BITRIX_WEBHOOK_NOT_CLEANED' }
    if ($env:GIGACHAT_CREDENTIALS -ne 'SYNTHETIC_MOCK_CREDENTIAL' -or
        $env:AI_ENABLE -ne '0' -or (-not [string]::IsNullOrEmpty($env:AI_PROVIDER))) {
        throw 'CALLER_AI_ENV_NOT_RESTORED'
    }
    Write-Output 'SERVICEIT_WINDOWS_COCKPIT_GIGACHAT_HANDOFF=PASS; NETWORK=0; CRM_WRITES=0'
} finally {
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) {
        Remove-Item Env:\SERVICEIT_AI_COCKPIT_WEBHOOK -ErrorAction SilentlyContinue
    }
    foreach ($name in $names) {
        [Environment]::SetEnvironmentVariable($name,$saved[$name],'Process')
    }
    Remove-Item -LiteralPath $mock -Force -ErrorAction SilentlyContinue
}
