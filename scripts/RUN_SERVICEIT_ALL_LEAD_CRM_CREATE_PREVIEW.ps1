# One-command offline preview for ALL assessed source leads.
# Never request Bitrix webhook and NEVER call CRM mutations.
[CmdletBinding()]
param(
    [string]$PythonPath = '',
    [string]$InputJson = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if (-not $PythonPath) { $PythonPath = Join-Path $root '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'APPROVED_PYTHON_NOT_FOUND' }
if ($InputJson -and (-not (Test-Path -LiteralPath $InputJson -PathType Leaf))) {
    throw 'INPUT_JSON_NOT_FOUND'
}
$pythonArgs = @('-m', 'leadpilot.serviceit_all_lead_crm_create_intents')
if ($InputJson) { $pythonArgs += @('--input', $InputJson) }
Push-Location $root
try {
    & $PythonPath @pythonArgs
    if ($LASTEXITCODE -ne 0) {
        throw 'ALL_LEAD_CREATE_INTENT_PREVIEW_BLOCKED; NO_POSTS_OCCURRED'
    }
    Write-Output 'SERVICEIT_ALL_LEAD_CRM_CREATE_INTENTS=OFFLINE_ONLY; CRM_WRITES=0'
} finally {
    Pop-Location
}
