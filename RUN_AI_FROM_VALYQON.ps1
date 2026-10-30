# Service IT only. PowerShell 5.1 compatible. Read-only discovery of local VALYQON config.
# Never prints credentials, changes VALYQON, calls Docker/SSH, or writes CRM.
param(
    [string]$ValyqonEnvPath = '',
    [ValidateRange(1,10)][int]$Max = 2,
    [switch]$IncludePublicVacancies,
    [switch]$ProbeOnly,
    [switch]$PromptForKey
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) {
    throw 'SERVICE_IT_VENV_NOT_FOUND: run START_LAB.ps1 first'
}

# Search only explicitly known local paths. No recursive scanning, SSH, Docker, or GitHub.
$candidates = New-Object 'System.Collections.Generic.List[string]'
if ($ValyqonEnvPath) { [void]$candidates.Add($ValyqonEnvPath) }
foreach ($path in @(
    (Join-Path $HOME 'TenderLens-AI\.env'),
    (Join-Path $HOME 'TenderLens-AI\.env.local'),
    (Join-Path $HOME 'Documents\Codex\tender-ai-phase19\.env'),
    (Join-Path $HOME 'Documents\Codex\TenderLens-AI\.env'),
    (Join-Path $env:LOCALAPPDATA 'VALYQON\.env')
)) { [void]$candidates.Add($path) }

$originalKey = [Environment]::GetEnvironmentVariable('GIGACHAT_CREDENTIALS','Process')
$source = if ([string]::IsNullOrWhiteSpace($originalKey)) { '' } else { 'CURRENT_PROCESS' }
$filesChecked = 0
$filesExisting = 0
$seen = @{}
foreach ($path in $candidates) {
    $full = [System.IO.Path]::GetFullPath($path)
    if ($seen.ContainsKey($full.ToLowerInvariant())) { continue }
    $seen[$full.ToLowerInvariant()] = $true
    $filesChecked++
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { continue }
    $filesExisting++
    $foundHere = $false
    foreach ($line in [System.IO.File]::ReadAllLines($full, [System.Text.Encoding]::UTF8)) {
        if ($line -notmatch '^\s*(?:export\s+)?(GIGACHAT_CREDENTIALS|GIGACHAT_SCOPE|GIGACHAT_MODEL|GIGACHAT_CA_BUNDLE_FILE)\s*=\s*(.*)$') { continue }
        $name = $Matches[1]
        $value = $Matches[2].Trim()
        if ($value.Length -ge 2) {
            if (($value[0] -eq '"' -and $value[$value.Length-1] -eq '"') -or
                ($value[0] -eq "'" -and $value[$value.Length-1] -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        if ([string]::IsNullOrWhiteSpace($value)) { continue }
        if ($name -eq 'GIGACHAT_CA_BUNDLE_FILE' -and -not [System.IO.Path]::IsPathRooted($value)) {
            $value = Join-Path (Split-Path -Parent $full) $value
        }
        # Preserve values passed explicitly by the user in the current shell.
        if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name,'Process'))) {
            [Environment]::SetEnvironmentVariable($name,$value,'Process')
            if ($name -eq 'GIGACHAT_CREDENTIALS') {
                $source = 'LOCAL_ENV_FILE'
                $foundHere = $true
            }
        }
    }
    if ($foundHere) { break }
}
Write-Host 'SERVICE_IT_GIGACHAT_LOCAL_CHECK' -ForegroundColor Cyan
Write-Host ('LOCAL_PATHS_CHECKED=' + $filesChecked)
Write-Host ('LOCAL_ENV_FILES_FOUND=' + $filesExisting)
if (-not $env:GIGACHAT_CREDENTIALS -and $PromptForKey) {
    Write-Host 'Enter your own GigaChat authorization key in this terminal (input is hidden).' -ForegroundColor Yellow
    $sec = Read-Host 'GigaChat key' -AsSecureString
    $bstr = [IntPtr]::Zero
    try {
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
        $env:GIGACHAT_CREDENTIALS = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        $source = 'HIDDEN_CONSOLE_INPUT'
    } finally {
        if ($bstr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        if ($sec) { $sec.Dispose() }
    }
}
if ([string]::IsNullOrWhiteSpace($env:GIGACHAT_CREDENTIALS)) {
    Write-Host 'GIGACHAT_LOCAL_KEY=NOT_FOUND' -ForegroundColor Yellow
    Write-Host 'GIGACHAT_KEY_MAY_BE_SERVER_ONLY=YES'
    Write-Host 'No VALYQON server, Docker, files or keys were changed.'
    Write-Host 'If you have your own key, rerun with -PromptForKey; do NOT paste it in chat.'
    if ($ProbeOnly) { exit 0 } else { exit 2 }
}
Write-Host 'GIGACHAT_LOCAL_KEY=FOUND' -ForegroundColor Green
Write-Host ('GIGACHAT_KEY_SOURCE=' + $source)
Write-Host 'CREDENTIALS_VALUE=REDACTED'
if ($ProbeOnly) {
    Write-Host 'GIGACHAT_PROBE_ONLY=YES; NETWORK_CALLS=NO; VALYQON_CHANGES=NONE'
    exit 0
}
$env:AI_ENABLE = '1'
$env:AI_PROVIDER = 'gigachat'
if ($IncludePublicVacancies) {
    $db = Join-Path $PSScriptRoot 'data\research.sqlite3'
    if (-not (Test-Path -LiteralPath $db)) { throw 'RESEARCH_DB_NOT_FOUND' }
    $env:LEADPILOT_DB = $db
    Write-Host 'PUBLIC_AI_OPT_IN=YES; TITLE_AND_SIGNAL_TYPE_ONLY' -ForegroundColor Yellow
} else {
    $env:LEADPILOT_DB = Join-Path $PSScriptRoot 'data\demo.sqlite3'
    Write-Host 'AI_ONLY_SYNTHETIC_SIGNALS=YES' -ForegroundColor Cyan
}
& $py -m leadpilot.cli ai-config-check
if ($LASTEXITCODE -ne 0) { throw 'AI_CONFIG_FAILED' }
$cmd = @('-m','leadpilot.cli','ai-qualify','--max',[string]$Max)
if ($IncludePublicVacancies) { $cmd += '--include-public-vacancy' }
& $py @cmd
if ($LASTEXITCODE -ne 0) { throw 'AI_PROVIDER_UNAVAILABLE_OR_BAD_RESPONSE' }
Write-Host 'GIGACHAT_STEP_DONE=YES; CRM_WRITES=NO; VALYQON_CHANGES=NONE' -ForegroundColor Green
