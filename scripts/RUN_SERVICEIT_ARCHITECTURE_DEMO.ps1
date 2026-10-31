# Demo non-source architecture with real synthetic local flows.
# Does not read customer's CRM, contact sources or live EIS SQLite.
[CmdletBinding()]
param(
    [string]$PythonPath = '',
    [string]$HtmlPath = '',
    [string]$JsonPath = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) { $PythonPath = Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not $HtmlPath) { $HtmlPath = Join-Path $Repo 'exports\architecture_showcase.html' }
if (-not $JsonPath) { $JsonPath = Join-Path $Repo 'exports\architecture_showcase.json' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'ARCH_DEMO_EXISTING_PYTHON_VENV_REQUIRED'
}
Push-Location -LiteralPath $Repo
try {
    Write-Output 'SERVICEIT_ARCH_DEMO=TEST_START; MODE=SYNTHETIC_ONLY; CRM_WRITES=NO'
    $Tests=@('tests/test_workflow_ops.py', 'tests/test_manager_workspace_demo.py', 'tests/test_architecture_demo.py')
    & $PythonPath -m pytest -q -p no:cacheprovider @Tests
    if ($LASTEXITCODE -ne 0) { throw 'SERVICEIT_ARCH_DEMO_TESTS_FAILED' }
    Write-Output 'SERVICEIT_ARCH_DEMO=GENERATING_STATIC_HTML'
    & $PythonPath -m leadpilot.architecture_demo --html $HtmlPath --json $JsonPath
    if ($LASTEXITCODE -ne 0) { throw 'SERVICEIT_ARCH_DEMO_GENERATION_FAILED' }
    if (-not (Test-Path -LiteralPath $HtmlPath -PathType Leaf)) {
        throw 'SERVICEIT_ARCH_DEMO_HTML_NOT_FOUND'
    }
    Write-Output ('SERVICEIT_ARCH_DEMO_HTML=' + $HtmlPath)
    Write-Output 'SERVICEIT_ARCH_DEMO=PASS_LOCAL_SYNTHETIC_ONLY; REAL_CRM=DISCONNECTED'
} finally {
    Pop-Location
}
