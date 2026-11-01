# One explicit operator run: auto-select the webhook owner, never assign to Mikhail.
# Only synthetic internal task bound to the existing Bitrix24 1062/21/#3.
# The owner may receive an internal Bitrix notification.
[CmdletBinding()]
param([string]$PythonPath = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) { $PythonPath = Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'EXISTING_PYTHON_VENV_REQUIRED' }
$secret = $null
$ptr = [IntPtr]::Zero
Push-Location -LiteralPath $Repo
try {
    & $PythonPath -m compileall -q leadpilot
    if ($LASTEXITCODE -ne 0) { throw 'AUTO_OWNER_SYNTAX_FAILED' }
    Write-Output 'SERVICEIT_AUTO_SELF_DEMO_TASK=START'
    Write-Output 'OWNER_ID=READ_FROM_PORTAL_PROFILE; NO_ID_ENTRY=YES; NO_MIKHAIL_ASSIGNMENT=YES'
    Write-Output 'EXACT_CRM_ITEM=1062/21/3; MAX_ONE_INTERNAL_TASK=YES; CLIENT_CONTACT=NO'
    Write-Output 'NOTICE=Portal may notify the webhook owner about an internal DEMO task.'
    Write-Output 'PRECHECK=EXACT_CARD_AND_DUPLICATES; SQLITE_ONE_SHOT_FENCE=YES'
    $secret = Read-Host 'Authorized corporate Bitrix24 CRM+task webhook (hidden)' -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    $env:SERVICEIT_TASK_READONLY_WEBHOOK = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    & $PythonPath -m leadpilot.bitrix_1062_auto_owner_demo_task
    $rc = $LASTEXITCODE
    if ($rc -ne 0) {
        Write-Output 'SERVICEIT_AUTO_SELF_DEMO_TASK=BLOCKED_OR_UNCERTAIN'
        Write-Output 'DO_NOT_REPEAT_MUTATION; CHECK_REMOTE_AND_LOCAL_LEDGER_FIRST'
    } else {
        Write-Output 'SERVICEIT_AUTO_SELF_DEMO_TASK=VERIFIED_ONE_TASK'
    }
    exit $rc
} finally {
    Remove-Item Env:\SERVICEIT_TASK_READONLY_WEBHOOK -ErrorAction SilentlyContinue
    if ($ptr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
    Pop-Location
}
