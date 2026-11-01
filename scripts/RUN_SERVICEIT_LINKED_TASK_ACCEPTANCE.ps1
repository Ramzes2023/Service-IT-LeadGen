# Safe offline acceptance for bounded linked DEMO task preview; no credentials.
[CmdletBinding()]
param([string]$PythonPath = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) {
    $PythonPath = Join-Path $Repo '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'SERVICEIT_EXISTING_PYTHON_VENV_REQUIRED'
}
Push-Location -LiteralPath $Repo
try {
    Write-Output 'SERVICEIT_LINKED_TASK_ACCEPTANCE=START; REAL_TASKS=0; CRM_WRITES=0'
    & $PythonPath -m compileall -q leadpilot tests
    if ($LASTEXITCODE -ne 0) { throw 'SYNTAX_FAILED' }
    & $PythonPath -m pytest -q -p no:cacheprovider tests/test_ai_manager_workspace.py tests/test_bitrix_1062_manager_task_preview.py
    if ($LASTEXITCODE -ne 0) { throw 'TARGETED_TESTS_FAILED' }
    & $PythonPath -m pytest -q -p no:cacheprovider tests
    if ($LASTEXITCODE -ne 0) { throw 'FULL_TESTS_FAILED' }
    git diff --check
    if ($LASTEXITCODE -ne 0) { throw 'DIFF_CHECK_FAILED' }
    Write-Output 'SERVICEIT_LINKED_TASK_ACCEPTANCE=PASS; REAL_TASKS=0; CUSTOMER_CONTACT=NO'
} finally {
    Pop-Location
}
