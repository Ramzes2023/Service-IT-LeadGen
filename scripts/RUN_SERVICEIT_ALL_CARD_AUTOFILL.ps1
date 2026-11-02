# Restricted owner-only runtime for ServiceIT 1062/21 scheduled polling.
# No plaintext credentials on disk, command line, git, stdout, scheduled-task args.
[CmdletBinding()]
param([string]$PythonPath = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Root = Join-Path $env:LOCALAPPDATA 'ServiceIT\AI-Autofill'
$WebhookPath = Join-Path $Root 'bitrix-rest.dpapi'
if (-not $PythonPath) { $PythonPath = Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'SERVICEIT_PYTHON_NOT_FOUND' }
if (-not (Test-Path -LiteralPath $WebhookPath -PathType Leaf)) { throw 'ENCRYPTED_WEBHOOK_NOT_FOUND' }
if (Test-Path Env:\SERVICEIT_BITRIX_WEBHOOK) { throw 'PREEXISTING_SECRET_ENV_NOT_ALLOWED' }
$ptrs = New-Object 'System.Collections.Generic.List[System.IntPtr]'
try {
    $sec = Get-Content -LiteralPath $WebhookPath -Raw -Encoding UTF8 | ConvertTo-SecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    [void]$ptrs.Add($ptr)
    $env:SERVICEIT_BITRIX_WEBHOOK = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    $sec.Dispose()
    $env:SERVICEIT_WINDOWS_AUTOFILL_ENABLED = 'YES_1062_21'
    $env:SERVICEIT_AUTOFILL_STATE_DIR = Join-Path $Root 'state'
    $env:SERVICEIT_APPROVAL_DIR = Join-Path $Root 'approvals'
    $env:SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED = 'NO'
    $ModelPath = Join-Path $Root 'gigachat.dpapi'
    if (Test-Path -LiteralPath $ModelPath -PathType Leaf) {
        $secret = Get-Content -LiteralPath $ModelPath -Raw -Encoding UTF8 | ConvertTo-SecureString
        $modelPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
        [void]$ptrs.Add($modelPtr)
        $env:GIGACHAT_CREDENTIALS = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($modelPtr)
        $secret.Dispose()
        $env:SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED = 'YES'
        $env:AI_ENABLE = '1'
        $env:AI_PROVIDER = 'gigachat'
        $scopeFile = Join-Path $Root 'gigachat-scope.txt'
        $env:GIGACHAT_SCOPE = if (Test-Path $scopeFile) {
            (Get-Content $scopeFile -Raw).Trim()
        } else { 'GIGACHAT_API_PERS' }
    }
    Push-Location $Repo
    try {
        & $PythonPath -m leadpilot.serviceit_windows_autofill --once
        if ($LASTEXITCODE -ne 0) { throw 'SERVICEIT_AUTOFILL_CYCLE_FAILED' }
    } finally { Pop-Location }
} finally {
    foreach ($name in @('SERVICEIT_BITRIX_WEBHOOK', 'GIGACHAT_CREDENTIALS', 'GIGACHAT_SCOPE',
                        'AI_ENABLE','AI_PROVIDER','SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED',
                        'SERVICEIT_WINDOWS_AUTOFILL_ENABLED','SERVICEIT_AUTOFILL_STATE_DIR',
                        'SERVICEIT_APPROVAL_DIR')) {
        Remove-Item "Env:\$name" -ErrorAction SilentlyContinue
    }
    foreach ($ptr in $ptrs) {
        if ($ptr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        }
    }
}
