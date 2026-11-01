# Corporate single-DEMO task duplicate check; STRICT READ ONLY.
[CmdletBinding()]
param([string]$PythonPath = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) { $PythonPath = Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'EXISTING_PYTHON_ENV_REQUIRED'
}
$secret = $null
$ptr = [IntPtr]::Zero
Push-Location -LiteralPath $Repo
try {
    & $PythonPath -m compileall -q leadpilot
    if ($LASTEXITCODE -ne 0) { throw 'TASK_DEDUPE_SYNTAX_FAILED' }
    Write-Output 'SERVICEIT_ONE_DEMO_TASK_DUPLICATES=READ_ONLY_START'
    Write-Output 'NO_TASK_CREATION; NO_CRM_WRITES; NO_EXTERNAL_CONTACTS'
    $secret = Read-Host 'Authorized CRM + tasks webhook (hidden)' -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    $env:SERVICEIT_TASK_READONLY_WEBHOOK = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    & $PythonPath -m leadpilot.bitrix_1062_task_dedupe_readonly
    $rc = $LASTEXITCODE
    if ($rc -ne 0) {
        Write-Output 'SERVICEIT_ONE_DEMO_TASK_DUPLICATES=BLOCKED; NO_WRITES'
    } else {
        Write-Output 'SERVICEIT_ONE_DEMO_TASK_DUPLICATES=READ_ONLY_DONE; TASK_NOT_CREATED'
    }
    exit $rc
} finally {
    Remove-Item Env:\SERVICEIT_TASK_READONLY_WEBHOOK -ErrorAction SilentlyContinue
    if ($ptr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
    Pop-Location
}
