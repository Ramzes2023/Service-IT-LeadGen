# Windows PowerShell 5.1 local Gmail source with DPAPI encrypted local credential.
# Setup once, test -Poll, then opt in to hourly -InstallSchedule.
# NEVER reads or changes CRM, marks emails read, sends outbound messages or bids.
# Uses Gmail IMAP only after user separately authorizes app-password access.
[CmdletBinding()]
param(
    [switch]$Setup,
    [switch]$Poll,
    [switch]$InstallSchedule,
    [switch]$Status,
    [string]$GmailAddress = 'ramzesisayev444@gmail.com',
    [string]$PythonPath = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ModeCount = @($Setup, $Poll, $InstallSchedule, $Status | Where-Object { $_ }).Count
# Explicit count avoids silently scheduling/unintended password prompting.
$ModeCount = [int][bool]$Setup + [int][bool]$Poll + [int][bool]$InstallSchedule + [int][bool]$Status
if ($ModeCount -ne 1) { throw 'CHOOSE_EXACTLY_ONE_MODE_SETUP_POLL_INSTALLSCHEDULE_STATUS' }

$Repo = Split-Path -Parent $PSScriptRoot
$State = Join-Path $env:LOCALAPPDATA 'ServiceIT\B2B-Gmail-Source'
$CredFile = Join-Path $State 'gmail-access.dpapi.xml'
$Marker = Join-Path $State 'last-poll-success.txt'
$Task = 'ServiceIT-B2B-Gmail-ReadOnly'
if (-not $PythonPath) {
    $PythonPath = Join-Path $Repo '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        $PythonPath = Join-Path $env:USERPROFILE 'Service_IT_GitHub_Repo\Service-IT-LeadGen\.venv\Scripts\python.exe'
    }
}
if ($Status) {
    Write-Output ('B2B_SOURCE_CREDENTIAL_CONFIGURED=' + (Test-Path -LiteralPath $CredFile))
    Write-Output ('B2B_SOURCE_FIRST_POLL_CONFIRMED=' + (Test-Path -LiteralPath $Marker))
    $Queue = Join-Path $State 'b2b-review-queue.json'
    Write-Output ('B2B_SOURCE_LOCAL_REVIEW_QUEUE=' + (Test-Path -LiteralPath $Queue))
    Write-Output 'B2B_SOURCE_STATUS_ONLY; CRM_WRITES=0'
    return
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'APPROVED_PYTHON_NOT_FOUND' }
if (-not (Test-Path -LiteralPath (Join-Path $Repo 'leadpilot\b2b_gmail_incremental_source.py') -PathType Leaf)) {
    throw 'SOURCE_CODE_NOT_IN_THIS_WORKTREE'
}
if ($Setup) {
    if (Test-Path -LiteralPath $CredFile -PathType Leaf) {
        throw 'LOCAL_GMAIL_CREDENTIAL_ALREADY_EXISTS_DO_NOT_OVERWRITE'
    }
    if ($GmailAddress -cne 'ramzesisayev444@gmail.com') {
        throw 'GMAIL_ACCOUNT_UNEXPECTED_FOR_THIS_PILOT'
    }
    Write-Output 'GMAIL_SOURCE_SETUP=LOCAL_WINDOWS_ONLY'
    Write-Output 'Google recommends OAuth over app passwords. Use a dedicated app password only if your account permits it.'
    Write-Output 'Do not paste the app password in chat, files or GitHub.'
    $approved = Read-Host 'Authorize READ-ONLY polling code to access this mailbox via Gmail IMAP? Type YES'
    if ($approved -cne 'YES') { throw 'EXPLICIT_GMAIL_MAILBOX_APPROVAL_MISSING' }
    $password = Read-Host 'Google Gmail app password (hidden input)' -AsSecureString
    if ($null -eq $password -or $password.Length -lt 12) { throw 'GMAIL_APP_PASSWORD_MISSING' }
    New-Item -ItemType Directory -Path $State -Force | Out-Null
    $creds = New-Object System.Management.Automation.PSCredential($GmailAddress, $password)
    # Export-Clixml encrypts PSCredential password with current Windows user DPAPI.
    $creds | Export-Clixml -LiteralPath $CredFile
    $password.Dispose()
    Write-Output 'B2B_GMAIL_LOCAL_CREDENTIAL=CONFIGURED_DPAPI_CURRENT_WINDOWS_USER'
    Write-Output 'B2B_SOURCE_NEXT=RUN_POLL; CRM_WRITES=0'
    return
}
if ($InstallSchedule) {
    if (-not (Test-Path -LiteralPath $CredFile -PathType Leaf) -or
        -not (Test-Path -LiteralPath $Marker -PathType Leaf)) {
        throw 'SETUP_AND_SUCCESSFUL_MANUAL_POLL_REQUIRED_BEFORE_SCHEDULE'
    }
    $agree = Read-Host 'Install an hourly Windows task to fetch B2B mail and build LOCAL review queue only? Type INSTALL'
    if ($agree -cne 'INSTALL') { throw 'SCHEDULE_NOT_APPROVED' }
    $existing = & schtasks.exe /Query /TN $Task 2>$null
    if ($LASTEXITCODE -eq 0) { throw 'TASK_ALREADY_EXISTS_DO_NOT_REPLACE' }
    $runner = (Resolve-Path -LiteralPath $PSCommandPath).Path
    $taskCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + $runner + '" -Poll'
    & schtasks.exe /Create /SC HOURLY /MO 1 /TN $Task /TR $taskCommand
    if ($LASTEXITCODE -ne 0) { throw 'SCHEDULE_INSTALL_FAILED' }
    Write-Output 'B2B_SOURCE_HOURLY_LOCAL_RESEARCH=INSTALLED; CRM_WRITES=0; NO_OUTREACH'
    return
}
if ($Poll) {
    if (-not (Test-Path -LiteralPath $CredFile -PathType Leaf)) {
        throw 'RUN_SETUP_FIRST'
    }
    if (Test-Path Env:\SERVICEIT_B2B_GMAIL_APP_PASSWORD) {
        throw 'PREEXISTING_GMAIL_AUTH_ENV_NOT_ALLOWED'
    }
    Push-Location -LiteralPath $Repo
    try {
        $Credential = Import-Clixml -LiteralPath $CredFile
        if (-not ($Credential -is [System.Management.Automation.PSCredential]) -or
            $Credential.UserName -cne $GmailAddress) {
            throw 'DPAPI_CREDENTIAL_INVALID'
        }
        $ptr = [IntPtr]::Zero
        try {
            $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Credential.Password)
            $env:SERVICEIT_B2B_GMAIL_APP_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
            $env:SERVICEIT_B2B_GMAIL_ADDRESS = $GmailAddress
            Write-Output 'B2B_GMAIL_SOURCE=READ_ONLY_POLL_START; NO_BITRIX_WRITE'
            & $PythonPath -m leadpilot.b2b_gmail_incremental_source --poll
            if ($LASTEXITCODE -ne 0) { throw 'GMAIL_POLL_NOT_VERIFIED' }
        }
        finally {
            Remove-Item Env:\SERVICEIT_B2B_GMAIL_APP_PASSWORD -ErrorAction SilentlyContinue
            Remove-Item Env:\SERVICEIT_B2B_GMAIL_ADDRESS -ErrorAction SilentlyContinue
            if ($ptr -ne [IntPtr]::Zero) {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
            }
        }
        # Written only following successful read-only poll + persisted local queue.
        Set-Content -LiteralPath $Marker -Value ([DateTime]::UtcNow.ToString('o')) -Encoding ASCII
        Write-Output 'B2B_GMAIL_SOURCE=POLL_PASS; QUALIFIED_LEADS=0_UNTIL_FNS_GATE; CRM_WRITES=0'
    }
    finally {
        Pop-Location
    }
}
