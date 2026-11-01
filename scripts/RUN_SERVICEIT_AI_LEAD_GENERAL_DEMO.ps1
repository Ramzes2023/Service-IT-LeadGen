# One-command ServiceIT AI Lead → corporate smart-process DEMO 1062/#3.
# By default PREVIEW ONLY. -ApplyDemo permits exactly one protected update on
# the existing synthetic CRM card; -UseExistingGigaChat sends ONLY a fixed
# fictional sentence to the provider, never a corporate CRM record.
[CmdletBinding()]
param(
    [switch]$ApplyDemo,
    [switch]$UseExistingGigaChat,
    [string]$PythonPath = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $PSScriptRoot 'RUN_SERVICEIT_DEMO_QUALIFICATION_BRIDGE.ps1'
if (-not $PythonPath) { $PythonPath = Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath)) { throw 'PYTHON_ENV_REQUIRED' }
if (-not (Test-Path -LiteralPath $Runner)) { throw 'DEMO_BRIDGE_NOT_FOUND' }

$names = @('GIGACHAT_CREDENTIALS','GIGACHAT_SCOPE','GIGACHAT_MODEL','GIGACHAT_CA_BUNDLE_FILE')
$saved = @{}
foreach ($name in $names) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name,'Process')
}
$saved['AI_ENABLE'] = [Environment]::GetEnvironmentVariable('AI_ENABLE','Process')
$saved['AI_PROVIDER'] = [Environment]::GetEnvironmentVariable('AI_PROVIDER','Process')
try {
    Write-Output 'SERVICEIT_AI_LEAD_GENERAL_ENGINE=VERSIONED_SCORING_AND_SEVEN_POLICY_GATES'
    Write-Output 'TARGET_SMART_PROCESS=1062/21; PRODUCTION_CUSTOMERS=BLOCKED'
    Write-Output 'DEMO_CARD=3; EXISTING_TASK=26163; NO_NEW_TASKS=YES'
    if ($UseExistingGigaChat) {
        if ([string]::IsNullOrWhiteSpace($env:GIGACHAT_CREDENTIALS)) {
            $paths = @(
                (Join-Path $HOME 'TenderLens-AI\.env'),
                (Join-Path $HOME 'TenderLens-AI\.env.local'),
                (Join-Path $HOME 'Documents\Codex\tender-ai-phase19\.env'),
                (Join-Path $HOME 'Documents\Codex\TenderLens-AI\.env'),
                (Join-Path $env:LOCALAPPDATA 'VALYQON\.env')
            )
            foreach ($path in $paths) {
                if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
                foreach ($line in [IO.File]::ReadAllLines($path, [Text.Encoding]::UTF8)) {
                    if ($line -notmatch '^\s*(?:export\s+)?(GIGACHAT_CREDENTIALS|GIGACHAT_SCOPE|GIGACHAT_MODEL|GIGACHAT_CA_BUNDLE_FILE)\s*=\s*(.*)$') {
                        continue
                    }
                    $name = $Matches[1]; $value = $Matches[2].Trim()
                    if ($value.Length -ge 2) {
                        if (($value[0] -eq '"' -and $value[$value.Length-1] -eq '"') -or
                            ($value[0] -eq "'" -and $value[$value.Length-1] -eq "'")) {
                            $value = $value.Substring(1, $value.Length - 2)
                        }
                    }
                    if ($name -eq 'GIGACHAT_CA_BUNDLE_FILE' -and $value -and -not [IO.Path]::IsPathRooted($value)) {
                        $value = Join-Path (Split-Path -Parent $path) $value
                    }
                    if ($value -and -not [Environment]::GetEnvironmentVariable($name,'Process')) {
                        [Environment]::SetEnvironmentVariable($name,$value,'Process')
                    }
                }
                if ($env:GIGACHAT_CREDENTIALS) { break }
            }
        }
        if ([string]::IsNullOrWhiteSpace($env:GIGACHAT_CREDENTIALS)) {
            throw 'GIGACHAT_CREDENTIALS_NOT_FOUND_LOCALLY; NO_DEMO_WRITE'
        }
        $env:AI_ENABLE = '1'
        $env:AI_PROVIDER = 'gigachat'
        Write-Output 'GIGACHAT=LOCAL_KEY_FOUND_REDACTED; EXTERNAL_REQUEST=FIXED_SYNTHETIC_TEXT_ONLY'
    } else {
        Write-Output 'GIGACHAT=NOT_REQUESTED; NO_EXTERNAL_AI_CALL'
    }
    $args = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$Runner,'-PythonPath',$PythonPath)
    if ($ApplyDemo) { $args += '-ApplyDemo' }
    if ($UseExistingGigaChat) { $args += '-WithSyntheticAI' }
    & powershell.exe @args
    if ($LASTEXITCODE -ne 0) { throw 'BRIDGE_BLOCKED_OR_UNCERTAIN_DO_NOT_RETRY_APPLY' }
    Write-Output 'SERVICEIT_AI_LEAD_GENERAL_DEMO=COMPLETE'
} finally {
    foreach ($name in $names) {
        [Environment]::SetEnvironmentVariable($name,$saved[$name],'Process')
    }
    [Environment]::SetEnvironmentVariable('AI_ENABLE',$saved['AI_ENABLE'],'Process')
    [Environment]::SetEnvironmentVariable('AI_PROVIDER',$saved['AI_PROVIDER'],'Process')
}
