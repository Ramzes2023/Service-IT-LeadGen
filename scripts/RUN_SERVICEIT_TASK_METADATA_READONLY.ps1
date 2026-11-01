# Corporate 1062 + CRM/task metadata READ ONLY. Zero CRM/task writes.
[CmdletBinding()]
param([string]$PythonPath = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) { $PythonPath = Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'SERVICEIT_EXISTING_PYTHON_REQUIRED'
}
$secret = $null
$ptr = [IntPtr]::Zero
Push-Location -LiteralPath $Repo
try {
    & $PythonPath -m compileall -q leadpilot
    if ($LASTEXITCODE -ne 0) { throw 'TASK_METADATA_SYNTAX_FAILED' }
    Write-Output 'SERVICEIT_TASK_METADATA=READ_ONLY_START'
    Write-Output 'ALLOWED=crm.type.get,crm.enum.ownertype,tasks.task.getFields,crm.item.get'
    Write-Output 'CRM_WRITES=0; TASK_ADDS=0; CUSTOMER_CONTACT=NO'
    $secret = Read-Host 'Authorized CRM+task webhook (hidden, never in chat)' -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    $env:SERVICEIT_TASK_READONLY_WEBHOOK = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    & $PythonPath -m leadpilot.bitrix_1062_task_metadata
    if ($LASTEXITCODE -ne 0) { throw 'SERVICEIT_TASK_METADATA_READONLY_BLOCKED' }
    Write-Output 'SERVICEIT_TASK_METADATA=READ_ONLY_PASS; NO_WRITES=YES'
} finally {
    Remove-Item Env:\SERVICEIT_TASK_READONLY_WEBHOOK -ErrorAction SilentlyContinue
    if ($ptr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
    Pop-Location
}
