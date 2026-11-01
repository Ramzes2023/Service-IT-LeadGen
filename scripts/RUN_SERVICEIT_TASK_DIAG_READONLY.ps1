# Diagnose corporate CRM + tasks scopes read-only. No task or CRM writes.
[CmdletBinding()]
param([string]$PythonPath = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) { $PythonPath = Join-Path $Root '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'SERVICEIT_EXISTING_PYTHON_VENV_REQUIRED'
}
$secret = $null
$ptr = [IntPtr]::Zero
Push-Location -LiteralPath $Root
try {
    & $PythonPath -m compileall -q leadpilot
    if ($LASTEXITCODE -ne 0) { throw 'SERVICEIT_TASK_DIAG_SYNTAX_FAILED' }
    Write-Output 'SERVICEIT_TASK_DIAG=READ_ONLY_START'
    Write-Output 'ONLY_METHODS=crm.type.get,crm.enum.ownertype,tasks.task.getFields,crm.item.get'
    Write-Output 'NO_CUSTOMER_READS; NO_TASKS_CREATED; NO_CRM_WRITES'
    $secret = Read-Host 'Authorized CRM + tasks REST webhook (hidden)' -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    $env:SERVICEIT_TASK_READONLY_WEBHOOK = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    & $PythonPath -m leadpilot.bitrix_1062_task_diagnostics
    $rc = $LASTEXITCODE
    if ($rc -ne 0) {
        Write-Output 'SERVICEIT_TASK_DIAG=PERMISSION_OR_CONFIGURATION_REQUIRES_REVIEW'
        Write-Output 'NO_WRITES_PERFORMED'
    } else {
        Write-Output 'SERVICEIT_TASK_DIAG=READ_ONLY_PASS; TASK_NOT_CREATED'
    }
    exit $rc
} finally {
    Remove-Item Env:\SERVICEIT_TASK_READONLY_WEBHOOK -ErrorAction SilentlyContinue
    if ($ptr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
    Pop-Location
}
