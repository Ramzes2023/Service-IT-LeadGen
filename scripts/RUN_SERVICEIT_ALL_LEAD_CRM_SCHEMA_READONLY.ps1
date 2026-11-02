# Read-only corporate Bitrix24 smart process 1062 schema inspection.
# Never writes CRM, changes shared layout, or resets any UNCERTAIN ledger.
[CmdletBinding()]
param(
    [switch]$InspectLive,
    [string]$PythonPath = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path $PSScriptRoot -Parent
if (-not $PythonPath) { $PythonPath = Join-Path $RepoRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'APPROVED_PYTHON_NOT_FOUND' }
if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) { throw 'PREEXISTING_WEBHOOK_ENV_REFUSED' }
$argsPython = @('-m','leadpilot.serviceit_all_lead_crm_schema_preflight')
if ($InspectLive) { $argsPython += '--inspect-live' }
Push-Location $RepoRoot
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
        $argsPython += '--webhook-from-env'
    }
    & $PythonPath @argsPython
    if ($LASTEXITCODE -ne 0) { throw 'ALL_LEAD_SCHEMA_READONLY_BLOCKED; NO_CRM_MUTATIONS_OCCURRED' }
    Write-Output 'SERVICEIT_ALL_LEAD_CRM_SCHEMA=READ_ONLY; CRM_WRITES=0'
} finally {
    if (Test-Path Env:\SERVICEIT_AI_COCKPIT_WEBHOOK) {
        Remove-Item Env:\SERVICEIT_AI_COCKPIT_WEBHOOK -ErrorAction SilentlyContinue
    }
    Pop-Location
}
