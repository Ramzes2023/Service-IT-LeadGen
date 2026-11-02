# Only read corporate smart-process metadata. NO CRM item read/write.
# Run from an isolated Git worktree of the unmerged draft branch.
[CmdletBinding()]
param([string]$PythonPath = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) { $PythonPath = Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    $ParentPython = Join-Path $env:USERPROFILE 'Service_IT_GitHub_Repo\Service-IT-LeadGen\.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $ParentPython -PathType Leaf) { $PythonPath = $ParentPython }
    else { throw 'SERVICEIT_EXISTING_VENV_REQUIRED_IN_WORKTREE' }
}
if (Test-Path Env:BITRIX24_B2B_READONLY_WEBHOOK) {
    throw 'BITRIX_B2B_WEBHOOK_ALREADY_IN_ENV_CLEAR_FIRST'
}
Push-Location -LiteralPath $Repo
try {
    Write-Output 'B2B_BITRIX_PREFLIGHT=START; METADATA_READ_ONLY=YES; CRM_WRITES=NO'
    & $PythonPath -m pytest -q -p no:cacheprovider tests/test_b2b_email_opportunity_bridge.py tests/test_b2b_bitrix_readiness.py
    if ($LASTEXITCODE -ne 0) { throw 'B2B_BRIDGE_REGRESSION_NOT_PASS' }
    & $PythonPath -m py_compile leadpilot/b2b_email_opportunity_bridge.py leadpilot/b2b_bitrix_readiness.py
    if ($LASTEXITCODE -ne 0) { throw 'B2B_BRIDGE_COMPILE_NOT_PASS' }
    Write-Output 'B2B_BRIDGE_TESTS=PASS; WILL_READ_ONLY_TYPE_FIELDS_CATEGORY_STAGES'
    $Secret = Read-Host -Prompt 'Введите одобренный корпоративный Bitrix24 webhook REST (скрытый ввод)' -AsSecureString
    $Bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    try {
        $Plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Bstr)
        if ([string]::IsNullOrWhiteSpace($Plain)) { throw 'BITRIX_READONLY_WEBHOOK_REQUIRED' }
        $env:BITRIX24_B2B_READONLY_WEBHOOK = $Plain
        $Plain = $null
        & $PythonPath -m leadpilot.b2b_bitrix_readiness --read-only
        if ($LASTEXITCODE -ne 0) { throw 'BITRIX_B2B_METADATA_NOT_VERIFIED' }
    }
    finally {
        Remove-Item Env:BITRIX24_B2B_READONLY_WEBHOOK -ErrorAction SilentlyContinue
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Bstr)
        $Secret.Dispose()
        $Plain = $null
    }
    Write-Output 'B2B_TO_BITRIX_READONLY_ACCEPTANCE=PASS; REAL_CRM_RECORDS_CREATED=0'
}
finally {
    Pop-Location
}
