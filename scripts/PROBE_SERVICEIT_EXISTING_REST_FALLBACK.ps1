# Read-only diagnosis for an existing, authorized Bitrix24 REST connection.
# Used when the owner cannot open Developer resources / outgoing webhooks.
# No privilege changes, no outgoing webhook creation, CRM writes or model calls.
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Dpapi = Join-Path $env:LOCALAPPDATA 'ServiceIT\AI-Autofill\bitrix-rest.dpapi'
if (-not (Test-Path -LiteralPath (Join-Path $Repo '.git') -PathType Container)) {
    throw 'SERVICEIT_LOCAL_REPO_MISSING'
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'SERVICEIT_LOCAL_PYTHON_VENV_MISSING'
}
if (-not (Test-Path -LiteralPath $Dpapi -PathType Leaf)) {
    throw 'SERVICEIT_EXISTING_REST_DPAPI_MISSING'
}
$Secure = Get-Content -LiteralPath $Dpapi -Raw -Encoding UTF8 | ConvertTo-SecureString
$Ptr = [IntPtr]::Zero
$Webhook = $null
try {
    $Ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    $Webhook = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Ptr)
}
finally {
    if ($Ptr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Ptr)
    }
    $Secure.Dispose()
}
if ($Webhook -notmatch '^https://serviseit\.bitrix24\.ru/rest/[0-9]+/[A-Za-z0-9_-]+/?$') {
    $Webhook = $null
    throw 'SERVICEIT_REST_SCOPE_UNVERIFIED'
}
Write-Host 'SERVICEIT_INTEGRATION_ACCESS=UI_BLOCKED_OPERATOR_REPORTED'
Write-Host 'SERVICEIT_FALLBACK_DIAGNOSTIC=EXISTING_REST_READONLY'
Write-Host 'SERVICEIT_CRM_WRITES=0'
Write-Host 'SERVICEIT_GIGACHAT_CALLS=0'
Write-Host 'SERVICEIT_EVENT_GATE_CHANGE=NO'
Write-Host 'SERVICEIT_EXISTING_GOOGLE_BRIDGE_CHANGE=NO'

$Saved = $ErrorActionPreference
$ProbeStatus = 2
$CutoverStatus = 2
try {
    # Never print or persist the decrypted REST token. It exists only in this
    # PowerShell process and its authorized Python child while executing.
    $env:SERVICEIT_BITRIX_WEBHOOK = $Webhook
    $Webhook = $null
    Push-Location $Repo
    try {
        $ErrorActionPreference = 'Continue'
        & $Python -m leadpilot.serviceit_cutover_access_probe
        $ProbeStatus = $LASTEXITCODE
        if ($ProbeStatus -eq 0) {
            & $Python -m leadpilot.serviceit_cloud_cutover_readonly
            $CutoverStatus = $LASTEXITCODE
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    $ErrorActionPreference = $Saved
    Remove-Item Env:\SERVICEIT_BITRIX_WEBHOOK -ErrorAction SilentlyContinue
    $Webhook = $null
}
if ($ProbeStatus -ne 0 -or $CutoverStatus -ne 0) {
    Write-Host 'SERVICEIT_NO_OUTGOING_FALLBACK=NOT_VERIFIED_NO_AUTOMATION'
    exit 2
}
Write-Host 'SERVICEIT_NO_OUTGOING_FALLBACK=READONLY_DISCOVERY_POSSIBLE_NOT_LIVE'
Write-Host 'SERVICEIT_NO_OUTGOING_FALLBACK_NEXT=BOUND_NEW_CARD_ONLY_CANARY_REQUIRED'
