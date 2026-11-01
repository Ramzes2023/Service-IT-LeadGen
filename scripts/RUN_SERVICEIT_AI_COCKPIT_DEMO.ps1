param(
    [ValidateSet('preview','provision','layout','populate')][string]$Mode = 'preview',
    [switch]$InspectLive,
    [switch]$ApplyDemo,
    [switch]$GigaChatOptIn,
    [switch]$UseExistingGigaChat,
    [switch]$SyntheticPrivacyOptIn,
    [string]$PythonPath = '',
    [string]$StateDir = (Join-Path $env:LOCALAPPDATA 'ServiceIT\AI-Cockpit-Demo-v1')
)
$ErrorActionPreference = 'Stop'
$pythonForCockpit = $PythonPath
if (-not $pythonForCockpit) { $pythonForCockpit = $env:SERVICEIT_PYTHON_FOR_TESTS }
if (-not $pythonForCockpit) { throw 'Specify -PythonPath to the existing approved interpreter.' }
if (-not (Test-Path -LiteralPath $pythonForCockpit -PathType Leaf)) { throw 'APPROVED_PYTHON_NOT_FOUND' }
if ($ApplyDemo -and -not $InspectLive) { throw 'Apply requires explicit InspectLive.' }
if ($Mode -eq 'populate' -and $ApplyDemo -and (-not $GigaChatOptIn -or -not $SyntheticPrivacyOptIn)) {
    throw 'Populate requires GigaChatOptIn and SyntheticPrivacyOptIn.'
}
if ($UseExistingGigaChat -and ($Mode -ne 'populate' -or -not $InspectLive -or -not $ApplyDemo -or
    -not $GigaChatOptIn -or -not $SyntheticPrivacyOptIn)) {
    throw 'LOCAL_GIGACHAT_KEY_OPT_IN_REQUIRES_EXACT_DEMO_POPULATE_FLAGS'
}
# Deliberately do not source any .env, token or webhook file. PowerShell securely
# prompts for the webhook locally; never put it in shell history or arguments.
# Process GIGACHAT_* may be used only with both opt-ins. The additional
# -UseExistingGigaChat is an explicit opt-in for the known local VALYQON paths.
$cockpitArguments = @('-m','leadpilot.bitrix_1062_ai_cockpit','--mode',$Mode)
if ($InspectLive) { $cockpitArguments += '--inspect-live' }
if ($ApplyDemo) { $cockpitArguments += @('--apply-demo','--state-dir',$StateDir) }
if ($GigaChatOptIn) { $cockpitArguments += '--gigachat-opt-in' }
if ($SyntheticPrivacyOptIn) { $cockpitArguments += '--synthetic-privacy-opt-in' }
# Reuse the proven Bitrix24 Windows operator pattern. On this host,
# Python getpass() ignores the operator's pasted credentials.
# Never put the webhook on argv, in a file, or in a diagnostic log.
if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) {
    throw 'PREEXISTING_WEBHOOK_ENV_REFUSED'
}
# Preserve the caller's model environment; no key/config is persisted.
$aiNames = @('GIGACHAT_CREDENTIALS','GIGACHAT_SCOPE','GIGACHAT_MODEL','GIGACHAT_CA_BUNDLE_FILE','AI_ENABLE','AI_PROVIDER')
$savedAI = @{}
if ($UseExistingGigaChat) {
    foreach ($name in $aiNames) {
        $savedAI[$name] = [Environment]::GetEnvironmentVariable($name,'Process')
    }
}
Push-Location (Split-Path $PSScriptRoot -Parent)
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
                    if ($line -notmatch '^\s*(?:export\s+)?(GIGACHAT_CREDENTIALS|GIGACHAT_SCOPE|GIGACHAT_MODEL|GIGACHAT_CA_BUNDLE_FILE)\s*=\s*(.*)
        $secret = Read-Host 'Authorized Bitrix24 webhook URL (hidden input)' -AsSecureString
        if ($null -eq $secret -or $secret.Length -eq 0) {
            throw 'BITRIX_WEBHOOK_MISSING'
        }
        $ptr = [IntPtr]::Zero
        try {
            $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
            $env:SERVICEIT_AI_COCKPIT_WEBHOOK = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        } finally {
            if ($ptr -ne [IntPtr]::Zero) {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
            }
            $secret.Dispose()
        }
        if (-not $env:SERVICEIT_AI_COCKPIT_WEBHOOK) {
            throw 'BITRIX_WEBHOOK_MISSING'
        }
        $cockpitArguments += '--webhook-from-env'
    }
    & $pythonForCockpit @cockpitArguments
    if ($LASTEXITCODE -ne 0) {
        throw 'Cockpit blocked or uncertain. Do not repeat writes; inspect ledger and CRM.'
    }
} finally {
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) {
        Remove-Item Env:\SERVICEIT_AI_COCKPIT_WEBHOOK -ErrorAction SilentlyContinue
    }
    if ($UseExistingGigaChat) {
        foreach ($name in $aiNames) {
            [Environment]::SetEnvironmentVariable($name,$savedAI[$name],'Process')
        }
    }
    Pop-Location
}
) {
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
    }
    if ($InspectLive) {
        $secret = Read-Host 'Authorized Bitrix24 webhook URL (hidden input)' -AsSecureString
        if ($null -eq $secret -or $secret.Length -eq 0) {
            throw 'BITRIX_WEBHOOK_MISSING'
        }
        $ptr = [IntPtr]::Zero
        try {
            $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
            $env:SERVICEIT_AI_COCKPIT_WEBHOOK = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        } finally {
            if ($ptr -ne [IntPtr]::Zero) {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
            }
            $secret.Dispose()
        }
        if (-not $env:SERVICEIT_AI_COCKPIT_WEBHOOK) {
            throw 'BITRIX_WEBHOOK_MISSING'
        }
        $cockpitArguments += '--webhook-from-env'
    }
    & $pythonForCockpit @cockpitArguments
    if ($LASTEXITCODE -ne 0) {
        throw 'Cockpit blocked or uncertain. Do not repeat writes; inspect ledger and CRM.'
    }
} finally {
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) {
        Remove-Item Env:\SERVICEIT_AI_COCKPIT_WEBHOOK -ErrorAction SilentlyContinue
    }
    Pop-Location
}
