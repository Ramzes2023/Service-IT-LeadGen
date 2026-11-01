param(
    [ValidateSet('preview','provision','layout','populate')][string]$Mode = 'preview',
    [switch]$InspectLive,
    [switch]$ApplyDemo,
    [switch]$GigaChatOptIn,
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
# Deliberately do not source any .env, token or webhook file. Python securely
# prompts for the webhook locally; never put it in shell history or arguments.
# Existing process GIGACHAT_* variables may be used only with both opt-ins.
$cockpitArguments = @('-m','leadpilot.bitrix_1062_ai_cockpit','--mode',$Mode)
if ($InspectLive) { $cockpitArguments += '--inspect-live' }
if ($ApplyDemo) { $cockpitArguments += @('--apply-demo','--state-dir',$StateDir) }
if ($GigaChatOptIn) { $cockpitArguments += '--gigachat-opt-in' }
if ($SyntheticPrivacyOptIn) { $cockpitArguments += '--synthetic-privacy-opt-in' }
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & $pythonForCockpit @cockpitArguments
    if ($LASTEXITCODE -ne 0) { throw 'Cockpit blocked or uncertain. Do not repeat writes; inspect ledger and CRM.' }
} finally { Pop-Location }
