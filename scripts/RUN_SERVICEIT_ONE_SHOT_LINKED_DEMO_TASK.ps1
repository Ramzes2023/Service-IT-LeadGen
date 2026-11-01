# One real internal synthetic task, linked to existing CRM 1062/#3.
# It may notify the named employee. No customer outreach. No CRM item writes.
[CmdletBinding()]
param([string]$PythonPath = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) { $PythonPath = Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'EXISTING_VENV_REQUIRED' }

$secret = $null
$ptr = [IntPtr]::Zero
Push-Location -LiteralPath $Repo
try {
    & $PythonPath -m compileall -q leadpilot
    if ($LASTEXITCODE -ne 0) { throw 'ONE_SHOT_SYNTAX_FAILED' }
    Write-Output 'SERVICEIT_ONE_DEMO_TASK=HUMAN_GATE_START'
    Write-Output 'ONLY_EXISTING_CRM_CARD=1062/21/3; SOURCES=OFF; CRM_WRITES=0'
    Write-Output 'WARNING: this task may send an INTERNAL Bitrix notification to the responsible employee.'
    Write-Output 'First open the intended internal employee profile in the corporate Bitrix24 portal.'
    Write-Output 'Confirm the numeric user ID from /company/personal/user/ID/ before continuing.'
    $Responsible = Read-Host 'Confirmed responsible employee user ID (not client ID)'
    if ($Responsible -notmatch '^[1-9][0-9]{0,8}$') { throw 'RESPONSIBLE_ID_FORMAT_INVALID' }
    $ConfirmRecipient = Read-Host 'Have you verified this ID belongs to the intended internal employee? Type VERIFIED'
    if ($ConfirmRecipient -cne 'VERIFIED') { throw 'RECIPIENT_NOT_VERIFIED' }
    $Phrase = Read-Host 'Type AUTHORIZE_ONE_SERVICEIT_DEMO_TASK_1062_3 to create ONE internal DEMO task'
    if ($Phrase -cne 'AUTHORIZE_ONE_SERVICEIT_DEMO_TASK_1062_3') {
        throw 'ONE_TASK_EXPLICIT_APPROVAL_NOT_GRANTED'
    }

    Write-Output 'REMOTE_METADATA_AND_TASK_DEDUPE=RECHECK_BEFORE_WRITE'
    $secret = Read-Host 'Authorized CRM + task REST webhook (hidden)' -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    $env:SERVICEIT_TASK_READONLY_WEBHOOK = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    & $PythonPath -m leadpilot.bitrix_1062_one_shot_task --responsible-id ([int]$Responsible) --confirm-once $Phrase
    $Code = $LASTEXITCODE
    if ($Code -ne 0) {
        Write-Output 'SERVICEIT_ONE_DEMO_TASK=BLOCKED_OR_UNCERTAIN'
        Write-Output 'DO_NOT_RERUN_POST; REVIEW_LOCAL_LEDGER_AND_REMOTE_TASKS'
        exit $Code
    }
    Write-Output 'SERVICEIT_ONE_DEMO_TASK=VERIFIED_IN_BITRIX'
} finally {
    Remove-Item Env:\SERVICEIT_TASK_READONLY_WEBHOOK -ErrorAction SilentlyContinue
    if ($ptr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
    Pop-Location
}
