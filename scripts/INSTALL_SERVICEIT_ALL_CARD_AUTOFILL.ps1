# One-time installation. Owner must execute locally with interactive Windows session.
# Poll every 5 min only while the same Windows user is logged on.
# Strictly limited to ServiceIT 1062/21, new item IDs >=8. No source changes.
[CmdletBinding()]
param(
    [string]$PythonPath = '',
    [switch]$UseExistingGigaChat,
    [switch]$ConfigureGigaChat,
    [switch]$ApplyCommonDetailLayout
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) { $PythonPath = Join-Path $Repo '.venv\Scripts\python.exe' }
$Runner = Join-Path $Repo 'scripts\RUN_SERVICEIT_ALL_CARD_AUTOFILL.ps1'
if (!(Test-Path -LiteralPath $PythonPath -PathType Leaf) -or
    !(Test-Path -LiteralPath $Runner -PathType Leaf)) {
    throw 'SERVICEIT_RUNTIME_NOT_FOUND'
}

$Root = Join-Path $env:LOCALAPPDATA 'ServiceIT\AI-Autofill'
$Name = 'ServiceIT-AI-Autofill-1062-21'
if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
    throw 'AUTOFILL_ALREADY_REGISTERED; REFUSE_UNSAFE_OVERWRITE'
}

Write-Host 'Автозаполнение: только новые карточки 1062/21; ID >= 8.' -ForegroundColor Cyan
Write-Host 'Без корпоративного разрешения каждая новая карточка получит 9 полей HOLD, но НЕ вымышленный Score.'
Write-Host 'Полный GigaChat+Score применяется только при независимой approval-записи с 9 проверками.'
Write-Host 'Задача работает с интервалом 5 минут, пока пользователь вошёл в Windows.'
$Agree = Read-Host 'Подтверждаете автоматические записи этих 9 полей в новые корпоративные карточки? Введите YES'
if ($Agree -cne 'YES') { throw 'OWNER_AUTOFILL_CONSENT_MISSING' }

New-Item -ItemType Directory -Path $Root -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Root 'state') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Root 'approvals') -Force | Out-Null
$RestFile = Join-Path $Root 'bitrix-rest.dpapi'
if (Test-Path $RestFile) { throw 'ENCRYPTED_WEBHOOK_ALREADY_EXISTS_NO_OVERWRITE' }
$Rest = Read-Host 'Корпоративный Bitrix24 REST вебхук (скрыт)' -AsSecureString
if ($null -eq $Rest -or $Rest.Length -lt 30) { throw 'REST_WEBHOOK_MISSING' }
try {
    $Rest | ConvertFrom-SecureString | Set-Content -LiteralPath $RestFile -Encoding UTF8 -NoNewline
} finally { $Rest.Dispose() }

# Owner supplies a dedicated, explicitly authorized GigaChat key into an invisible prompt.
# Never import credentials from other projects or scan unrelated local .env files.
# Legacy -UseExistingGigaChat now means interactive secure prompt, not automatic reuse.
if ($UseExistingGigaChat -or $ConfigureGigaChat) {
    Write-Host 'Подключение GigaChat только для корпоративно проверенных карточек.'
    $Consent = Read-Host 'Подтверждена корпоративная политика передачи одобренных сигналов GigaChat? Введите YES'
    if ($Consent -cne 'YES') {
        Write-Host 'GIGACHAT=NOT_INSTALLED_NO_PRIVACY_APPROVAL'
    } else {
        $scope = Read-Host 'GigaChat scope (GIGACHAT_API_PERS / GIGACHAT_API_B2B / GIGACHAT_API_CORP, Enter = PERS)'
        if ([string]::IsNullOrWhiteSpace($scope)) { $scope = 'GIGACHAT_API_PERS' }
        if ($scope -notin @('GIGACHAT_API_PERS', 'GIGACHAT_API_B2B', 'GIGACHAT_API_CORP')) {
            throw 'GIGACHAT_SCOPE_UNSUPPORTED'
        }
        $ModelFile = Join-Path $Root 'gigachat.dpapi'
        if (Test-Path $ModelFile) { throw 'GIGACHAT_SECRET_ALREADY_CONFIGURED' }
        $Secret = Read-Host 'Отдельный GigaChat API-ключ, разрешенный для Сервис ИТ (ввод скрыт)' -AsSecureString
        if ($null -eq $Secret -or $Secret.Length -lt 16) { throw 'GIGACHAT_CREDENTIAL_MISSING' }
        try {
            $Secret | ConvertFrom-SecureString |
                Set-Content -LiteralPath $ModelFile -Encoding UTF8 -NoNewline
        } finally {
            $Secret.Dispose()
        }
        $scope | Set-Content -LiteralPath (Join-Path $Root 'gigachat-scope.txt') -Encoding Ascii
        Write-Host 'GIGACHAT=DEDICATED_CREDENTIAL_DPAPI_CURRENT_USER' -ForegroundColor Green
    }
}

$ActionArgs = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $Runner + '" -PythonPath "' + $PythonPath + '"'
$Action = New-ScheduledTaskAction -Execute (Join-Path $PSHOME 'powershell.exe') -Argument $ActionArgs -WorkingDirectory $Repo
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 365)
$Principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $Name -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Description 'Guarded new-card 1062/21 AI review worker, owner interactive Windows session' | Out-Null
Write-Host 'SERVICEIT_AUTOFILL_TASK_REGISTERED=YES' -ForegroundColor Green
Write-Host 'INTERVAL_MINUTES=5; HISTORICAL_IDS_3_7=EXCLUDED; STATUS=ENABLED_ON_WINDOWS_LOGIN'
Write-Host 'Run manually to see a redacted first cycle:'
Write-Host ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + $Runner + '" -PythonPath "' + $PythonPath + '"')

# Optional same-install, admin-only common detail form. No Kanban tile write.
if ($ApplyCommonDetailLayout) {
    $LayoutConsent = Read-Host 'Подтверждаете изменение общей раскладки карточки 1062/21 для всех пользователей? Введите YES'
    if ($LayoutConsent -cne 'YES') {
        Write-Host 'COMMON_DETAIL_LAYOUT=NOT_AUTHORIZED'
    } else {
        $SecretAgain = Get-Content -LiteralPath $RestFile -Raw -Encoding UTF8 | ConvertTo-SecureString
        $Handle = [IntPtr]::Zero
        try {
            $Handle = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecretAgain)
            $env:SERVICEIT_BITRIX_WEBHOOK = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Handle)
            $env:SERVICEIT_AUTOFILL_STATE_DIR = Join-Path $Root 'state'
            $env:SERVICEIT_COMMON_LAYOUT_APPROVED = 'YES_1062_21'
            Push-Location $Repo
            try {
                & $PythonPath -m leadpilot.serviceit_common_card_layout --apply-common
                if ($LASTEXITCODE -ne 0) {
                    Write-Warning 'COMMON_DETAIL_LAYOUT=BLOCKED_OR_UNCERTAIN; NO_RETRY'
                }
            } finally { Pop-Location }
        } finally {
            Remove-Item Env:\SERVICEIT_BITRIX_WEBHOOK -ErrorAction SilentlyContinue
            Remove-Item Env:\SERVICEIT_COMMON_LAYOUT_APPROVED -ErrorAction SilentlyContinue
            Remove-Item Env:\SERVICEIT_AUTOFILL_STATE_DIR -ErrorAction SilentlyContinue
            if ($Handle -ne [IntPtr]::Zero) {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Handle)
            }
            $SecretAgain.Dispose()
        }
    }
}
