# One-command batch Score + color preview. Strictly OFFLINE, no CRM webhook.
[CmdletBinding()]
param(
    [string]$PythonPath = '',
    [string]$InputJson = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path $PSScriptRoot -Parent
if (-not $PythonPath) { $PythonPath = Join-Path $RepoRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'APPROVED_PYTHON_NOT_FOUND'
}
if ($InputJson) {
    if (-not (Test-Path -LiteralPath $InputJson -PathType Leaf)) {
        throw 'INPUT_JSON_NOT_FOUND'
    }
}
$pythonArgs = @('-m','leadpilot.serviceit_source_batch_pipeline')
if ($InputJson) { $pythonArgs += @('--input',$InputJson) }

# No Bitrix secret or AI credentials are accepted or consumed in this operator.
# Do not export raw source records to GitHub or paste personal data in chat.
Push-Location $RepoRoot
try {
    & $PythonPath @pythonArgs
    if ($LASTEXITCODE -ne 0) {
        throw 'SOURCE_BATCH_OFFLINE_VALIDATION_BLOCKED'
    }
    Write-Output 'SERVICEIT_BATCH_COLOR_SCORE=OFFLINE_PREVIEW; CRM_WRITES=0; CUSTOMER_CONTACT=0'
} finally {
    Pop-Location
}
