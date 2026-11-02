# Controlled one-item corporate DEMO only. No bulk CRM writes, AI, tasks, outbound.
# Default is offline and does not prompt for webhook.
[CmdletBinding()]
param(
    [switch]$InspectLive,
    [switch]$ApplyDemo,
    [switch]$ConfirmDemoOnly,
    [string]$PythonPath = '',
    [string]$StateDir = (Join-Path $env:LOCALAPPDATA 'ServiceIT\Kanban-Score-Badge-v1')
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $PythonPath) { $PythonPath = Join-Path (Split-Path $PSScriptRoot -Parent) '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'APPROVED_PYTHON_NOT_FOUND' }
if ($ApplyDemo -and (-not $InspectLive -or -not $ConfirmDemoOnly)) {
    throw 'APPLY_REQUIRES_INSPECT_LIVE_AND_CONFIRM_DEMO_ONLY'
}
if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) {
    throw 'PREEXISTING_WEBHOOK_ENV_REFUSED'
}
$argsForPython = @('-m', 'leadpilot.serviceit_kanban_score_badge')
if ($InspectLive) { $argsForPython += '--inspect-live' }
if ($ApplyDemo) {
    $argsForPython += @('--apply-demo', '--confirm-demo-only', '--state-dir', $StateDir)
}
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    if ($InspectLive) {
        $secret = Read-Host 'Authorized Bitrix24 webhook URL (hidden input)' -AsSecureString
        if ($null -eq $secret -or $secret.Length -eq 0) { throw 'BITRIX_WEBHOOK_MISSING' }
        $ptr = [IntPtr]::Zero
        try {
            $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
            $env:SERVICEIT_AI_COCKPIT_WEBHOOK = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        } finally {
            if ($ptr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
            $secret.Dispose()
        }
        if (-not $env:SERVICEIT_AI_COCKPIT_WEBHOOK) { throw 'BITRIX_WEBHOOK_MISSING' }
        $argsForPython += '--webhook-from-env'
    }
    & $PythonPath @argsForPython
    if ($LASTEXITCODE -ne 0) {
        throw 'SCORE_BADGE_BLOCKED_OR_UNCERTAIN; DO_NOT_REPEAT_APPLY; INSPECT_SAFE_STATUS'
    }
} finally {
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) {
        Remove-Item Env:\SERVICEIT_AI_COCKPIT_WEBHOOK -ErrorAction SilentlyContinue
    }
    Pop-Location
}
