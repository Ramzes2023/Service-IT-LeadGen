# Existing DEMO CRM card 1062/21/#3 ONLY. Never connect real prospects.
# Preview by default; apply only when -ApplyDemo is explicitly selected.
[CmdletBinding()]
param(
    [string]$PythonPath = '',
    [switch]$ApplyDemo,
    [switch]$WithSyntheticAI
)
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
    if ($LASTEXITCODE -ne 0) { throw 'QUALIFICATION_SYNTAX_FAILED' }
    Write-Output 'SERVICEIT_QUALIFICATION_BRIDGE=ONE_DEMO_1062_ITEM_3'
    Write-Output 'REAL_CUSTOMERS=NO; OUTREACH=NO; REAL_CRM_DEDUP=NOT_VERIFIED'
    if ($ApplyDemo) {
        Write-Output 'NOTICE=ONE_EXISTING_DEMO_CRM_ITEM_CAN_BE_UPDATED; NO_NEW_CARDS_OR_TASKS'
    } else {
        Write-Output 'MODE=PREVIEW_READ_ONLY'
    }
    if ($WithSyntheticAI) {
        if ($env:AI_ENABLE -cne '1') { throw 'EXPLICIT_AI_ENABLE_REQUIRED' }
        Write-Output 'AI=OPT_IN_EXTERNAL_PROVIDER; SYNTHETIC_TEXT_ONLY; NOT_A_REAL_LEAD'
    } else {
        Write-Output 'AI=NOT_RUN; DETERMINISTIC_SCORING_ONLY'
    }
    $secret = Read-Host 'Authorized corporate Bitrix24 CRM webhook (hidden)' -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    $env:SERVICEIT_TASK_READONLY_WEBHOOK = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    $flags = @()
    if ($ApplyDemo) { $flags += '--apply-demo' }
    if ($WithSyntheticAI) { $flags += '--ai-demo' }
    & $PythonPath -m leadpilot.bitrix_1062_demo_qualification @flags
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        Write-Output 'SERVICEIT_QUALIFICATION_BRIDGE=BLOCKED_OR_UNCERTAIN'
        Write-Output 'DO_NOT_BLINDLY_REPEAT_APPLY; REVIEW_READBACK_AND_LEDGER'
    } else {
        Write-Output 'SERVICEIT_QUALIFICATION_BRIDGE=COMPLETED'
    }
    exit $code
} finally {
    Remove-Item Env:\SERVICEIT_TASK_READONLY_WEBHOOK -ErrorAction SilentlyContinue
    if ($ptr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
    Pop-Location
}
