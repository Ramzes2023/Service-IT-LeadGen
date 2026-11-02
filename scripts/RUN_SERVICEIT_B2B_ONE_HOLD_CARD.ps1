# Manual, one-time B2B email -> existing Bitrix24 smart-process 1062/21.
# PREVIEW by default; -ApplyOneHold is an explicit corporate CRM mutation.
# NEVER schedule this script for automatic writes; one exact tender per launch.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string[]]$EmailFiles,
    [Parameter(Mandatory=$true)][ValidatePattern('^\d{5,12}$')][string]$TenderId,
    [switch]$ApplyOneHold,
    [string]$PythonPath = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) { $PythonPath = Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    $ParentPython = Join-Path $env:USERPROFILE 'Service_IT_GitHub_Repo\Service-IT-LeadGen\.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $ParentPython -PathType Leaf) { $PythonPath = $ParentPython }
    else { throw 'EXISTING_PROJECT_PYTHON_VENV_REQUIRED' }
}
foreach ($Path in $EmailFiles) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'EML_FILE_NOT_FOUND' }
}
Push-Location -LiteralPath $Repo
try {
    Write-Output 'B2B_TO_BITRIX_OPERATOR=START; TARGET=1062/21; REAL_LEADS=0'
    & $PythonPath -m pytest -q -p no:cacheprovider tests/test_b2b_email_opportunity_bridge.py tests/test_b2b_bitrix_readiness.py tests/test_b2b_bitrix_hold_delivery.py
    if ($LASTEXITCODE -ne 0) { throw 'B2B_TO_BITRIX_TESTS_NOT_PASS' }
    & $PythonPath -m leadpilot.b2b_bitrix_operator --eml @EmailFiles --tender-id $TenderId
    if ($LASTEXITCODE -ne 0) { throw 'B2B_HOLD_PREVIEW_BLOCKED' }
    if (-not $ApplyOneHold) {
        Write-Output 'B2B_TO_BITRIX=PREVIEW_ONLY; CRM_WRITES=0; USE_EXPLICIT_APPLY_ONLY_AFTER_AUTHORIZATION'
        return
    }
    $Check = Read-Host 'Открыли оригинальную закупку и проверили ее статус? Напишите ПРОВЕРЕНО'
    if ($Check -cne 'ПРОВЕРЕНО') { throw 'ORIGINAL_PROCUREMENT_NOT_VERIFIED' }
    $Check = Read-Host 'Подтверждаете ОДНУ карточку HOLD в 1062/21 без контактов, задач и лидов? Напишите СОГЛАСЕН'
    if ($Check -cne 'СОГЛАСЕН') { throw 'ONE_CARD_HOLD_APPROVAL_MISSING' }
    if (Test-Path Env:BITRIX24_B2B_ONE_SHOT_WEBHOOK) {
        throw 'BITRIX_B2B_ONE_SHOT_WEBHOOK_ALREADY_SET_CLEAR_FIRST'
    }
    $Secret = Read-Host -Prompt 'Введите корпоративный Bitrix24 webhook (скрытый ввод)' -AsSecureString
    $Ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    try {
        $Plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Ptr)
        if ([string]::IsNullOrWhiteSpace($Plain)) { throw 'BITRIX_WEBHOOK_REQUIRED' }
        $env:BITRIX24_B2B_ONE_SHOT_WEBHOOK = $Plain
        $Plain = $null
        & $PythonPath -m leadpilot.b2b_bitrix_operator --eml @EmailFiles --tender-id $TenderId --apply-one --verified-original --approval 'AUTHORIZE ONE B2B TENDER HOLD CARD'
        if ($LASTEXITCODE -ne 0) {
            throw 'ONE_CARD_HOLD_UNCERTAIN_STOP_REVIEW_STATE_NO_RETRY'
        }
    }
    finally {
        Remove-Item Env:BITRIX24_B2B_ONE_SHOT_WEBHOOK -ErrorAction SilentlyContinue
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Ptr)
        $Secret.Dispose()
        $Plain = $null
    }
    Write-Output 'ONE_TENDER_HOLD_OPERATOR=COMPLETE; MANAGER_REVIEW_REQUIRED; NO_CONTACTS_OR_OUTREACH'
}
finally {
    Pop-Location
}
