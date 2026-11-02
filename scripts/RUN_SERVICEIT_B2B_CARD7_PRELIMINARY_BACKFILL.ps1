# The default is READ-ONLY. One explicit future owner approval is required
# for a one-shot write of NINE preliminary AI HOLD fields to exact item #7.
# Never retry an uncertain write, delete state, or change StateDir.
[CmdletBinding()]
param(
    [switch]$InspectLive,
    [switch]$ApplyOnce,
    [switch]$ApproveExactItem7,
    [string]$PythonPath = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
if (-not $PythonPath) { $PythonPath=Join-Path $root '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'APPROVED_PYTHON_NOT_FOUND'
}
if ($ApplyOnce -or $ApproveExactItem7) {
    if (-not ($ApplyOnce -and $ApproveExactItem7 -and $InspectLive)) {
        throw 'B2B_CARD7_EXACT_WRITE_APPROVAL_REQUIRED'
    }
}
if (Test-Path Env:\SERVICEIT_B2B_BACKFILL_WEBHOOK) {
    throw 'PREEXISTING_WEBHOOK_ENV_REFUSED'
}
$argsPython=@('-m','leadpilot.serviceit_b2b_card7_preliminary_backfill')
if ($InspectLive) { $argsPython+='--inspect-live' }
if ($ApplyOnce) { $argsPython+='--apply-once' }
if ($ApproveExactItem7) { $argsPython+='--approve-exact-item-7' }
Push-Location $root
try {
    if ($InspectLive) {
        $secret=Read-Host 'Corporate Bitrix24 webhook URL (hidden input)' -AsSecureString
        if ($null -eq $secret -or $secret.Length -eq 0) { throw 'BITRIX_WEBHOOK_MISSING' }
        $ptr=[IntPtr]::Zero
        try {
            $ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
            $env:SERVICEIT_B2B_BACKFILL_WEBHOOK=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        } finally {
            if ($ptr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
            $secret.Dispose()
        }
        if (-not $env:SERVICEIT_B2B_BACKFILL_WEBHOOK) { throw 'BITRIX_WEBHOOK_MISSING' }
    }
    & $PythonPath @argsPython
    if ($LASTEXITCODE -ne 0) { throw 'B2B_CARD7_BACKFILL_BLOCKED_OR_UNCERTAIN; NEVER_RETRY_APPLY' }
    Write-Output 'SERVICEIT_B2B_CARD7_OPERATOR=DONE; INSPECT_OUTPUT_FOR_VERIFIED_STATUS'
} finally {
    if (Test-Path Env:\SERVICEIT_B2B_BACKFILL_WEBHOOK) {
        Remove-Item Env:\SERVICEIT_B2B_BACKFILL_WEBHOOK -ErrorAction SilentlyContinue
    }
    Pop-Location
}
