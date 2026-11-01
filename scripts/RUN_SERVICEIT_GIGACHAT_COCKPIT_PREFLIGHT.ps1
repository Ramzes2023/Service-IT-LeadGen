# No Bitrix REST, CRM writes, ledger operations, tasks or customer data.
# -UseExistingGigaChat uses precisely the prior approved local VALYQON key paths.
[CmdletBinding()]
param(
    [switch]$UseExistingGigaChat,
    [string]$PythonPath = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) { $PythonPath = Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'APPROVED_PYTHON_NOT_FOUND' }
$names = @('GIGACHAT_CREDENTIALS','GIGACHAT_SCOPE','GIGACHAT_MODEL','GIGACHAT_CA_BUNDLE_FILE','AI_ENABLE','AI_PROVIDER')
$saved = @{}
foreach ($name in $names) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name,'Process')
}
Push-Location -LiteralPath $Repo
try {
    if ($UseExistingGigaChat) {
        if ([string]::IsNullOrWhiteSpace($env:GIGACHAT_CREDENTIALS)) {
            # Exact same fixed allowlist used by the successful
            # RUN_SERVICEIT_AI_LEAD_GENERAL_DEMO.ps1 -UseExistingGigaChat.
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
            throw 'GIGACHAT_CREDENTIALS_NOT_FOUND_LOCALLY; CRM_WRITES=0'
        }
        $env:AI_ENABLE = '1'
        $env:AI_PROVIDER = 'gigachat'
        Write-Output 'GIGACHAT=LOCAL_KEY_FOUND_REDACTED; SOURCE=FIXED_SYNTHETIC_ONLY'
    } else {
        Write-Output 'GIGACHAT=NOT_CALLED; PREFLIGHT_OFFLINE'
    }
    $arguments = @('-m','leadpilot.serviceit_gigachat_cockpit_preflight')
    if ($UseExistingGigaChat) { $arguments += '--live-synthetic' }
    & $PythonPath @arguments
    if ($LASTEXITCODE -ne 0) { throw 'GIGACHAT_PREFLIGHT_FAILED_NO_CRM_WRITE' }
    Write-Output 'SERVICEIT_GIGACHAT_PREFLIGHT=COMPLETE; CRM_WRITES=0; LEDGER_RESERVATIONS=0'
} finally {
    foreach ($name in $names) {
        [Environment]::SetEnvironmentVariable($name,$saved[$name],'Process')
    }
    Pop-Location
}
