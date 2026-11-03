# One-time owner-authorized cutover from local Windows to FREE GitHub Actions.
# Reads only the dedicated ServiceIT same-user DPAPI secrets. No secret in stdout,
# command-line arguments, Git working tree, or the public encrypted-state branch.
# Requires GitHub CLI (gh) interactive auth and owner confirmation.
[CmdletBinding()]
param([switch]$Apply, [switch]$DiagnoseCutover, [int]$CurrentCardId = 0)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repository = 'Ramzes2023/Service-IT-LeadGen'
$Repo = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Root = Join-Path $env:LOCALAPPDATA 'ServiceIT\AI-Autofill'
$TaskName = 'ServiceIT-AI-Autofill-1062-21'
$KeyFile = Join-Path $Root 'github-ledger-key.dpapi'
$BitrixFile = Join-Path $Root 'bitrix-rest.dpapi'
$ModelFile = Join-Path $Root 'gigachat.dpapi'
$ScopeFile = Join-Path $Root 'gigachat-scope.txt'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'SERVICEIT_PYTHON_MISSING' }
if (-not (Test-Path -LiteralPath $BitrixFile -PathType Leaf)) { throw 'SERVICEIT_BITRIX_DPAPI_MISSING' }
if (-not (Test-Path -LiteralPath $ModelFile -PathType Leaf)) { throw 'SERVICEIT_GIGACHAT_DPAPI_MISSING' }
if (-not (Test-Path -LiteralPath $ScopeFile -PathType Leaf)) { throw 'SERVICEIT_GIGACHAT_SCOPE_MISSING' }
$Scope = (Get-Content -LiteralPath $ScopeFile -Raw -Encoding UTF8).Trim()
if ($Scope -notin @('GIGACHAT_API_PERS','GIGACHAT_API_B2B','GIGACHAT_API_CORP')) {
    throw 'SERVICEIT_SCOPE_NOT_ALLOWED'
}
$GhCommand = Get-Command gh -ErrorAction SilentlyContinue
$Gh = if ($GhCommand) { $GhCommand.Source } else { Join-Path $env:ProgramFiles 'GitHub CLI\gh.exe' }
if (-not (Test-Path -LiteralPath $Gh -PathType Leaf)) {
    Write-Host 'GH_CLI_MISSING=YES'
    Write-Host 'Install official GitHub CLI using: winget install --id GitHub.cli --exact'
    Write-Host 'Then run this script again. No CRM or GitHub changes made.'
    exit 2
}
& $Gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'GITHUB_LOGIN_REQUIRED=YES'
    Write-Host 'A browser login is required. Your GitHub password/token is NOT needed here.'
    & $Gh auth login --web --git-protocol https --scopes 'repo,workflow'
    if ($LASTEXITCODE -ne 0) { throw 'GITHUB_LOGIN_NOT_COMPLETED' }
}
$FullName = (& $Gh api "repos/$Repository" --jq '.full_name' | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $FullName -cne $Repository) { throw 'GITHUB_REPO_IDENTITY_FAILED' }
$Visibility = (& $Gh api "repos/$Repository" --jq '.private' | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $Visibility -cne 'false') { throw 'PUBLIC_FREE_REPOSITORY_REQUIRED' }

$Ledger = (& $Gh api "repos/$Repository/contents/.serviceit/encrypted-ledger.txt?ref=serviceit-ai-encrypted-ledger" --jq '.sha' 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($Ledger)) {
    throw 'DURABLE_ENCRYPTED_GITHUB_LEDGER_BRANCH_UNAVAILABLE'
}
Write-Host "FREE_GITHUB_REPO=VERIFIED"
Write-Host 'ENCRYPTED_LEDGER_BRANCH=VERIFIED'
Write-Host "SCOPE=$Scope"
if (-not $Apply -and -not $DiagnoseCutover) {
    Write-Host 'CLOUD_SETUP=READONLY_PREFLIGHT_PASS'
    Write-Host 'Run again with -Apply to securely transfer DPAPI secrets and enable the FREE cloud.'
    exit 0
}

function Read-DpapiSecret([string]$Path) {
    $Secure = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertTo-SecureString
    $Ptr = [IntPtr]::Zero
    try {
        $Ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Ptr)
    } finally {
        if ($Ptr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Ptr)
        }
        $Secure.Dispose()
    }
}

# Read-only diagnosis: no secret transfer, no task disable, no cloud enable.
if ($DiagnoseCutover) {
    if ($CurrentCardId -ne 0 -and ($CurrentCardId -lt 8 -or $CurrentCardId -gt 2000000000)) {
        throw 'READONLY_CURRENT_CARD_ID_NOT_ALLOWED'
    }
    $BitrixDiagnostic = Read-DpapiSecret $BitrixFile
    try {
        $env:SERVICEIT_BITRIX_WEBHOOK = $BitrixDiagnostic
        Push-Location $Repo
        try {
            $DiagnosticLines = @(& $Python -m leadpilot.serviceit_cloud_cutover_readonly 2>$null)
            $DiagnosticExit = $LASTEXITCODE
        } finally {
            Pop-Location
        }
        foreach ($Line in $DiagnosticLines) {
            if ($Line -cmatch '^SERVICEIT_CLOUD_CUTOVER_(MAX_ID=[0-9]{1,10}|CRM_WRITES=0|DIAGNOSTIC=[A-Z0-9_:]+)$' -or
                $Line -ceq 'SERVICEIT_CLOUD_CUTOVER=UNVERIFIED_READ_ONLY') {
                Write-Host $Line
            }
        }
        Write-Host "CUTOVER_DIAGNOSTIC_EXIT_CODE=$DiagnosticExit"
        if ($DiagnosticExit -ne 0) {
            # Additional bounded read-only investigation: never include CRM
            # record contents or API error descriptions in the console.
            $ProbeLines = @()
            $ProbeExit = 2
            if ($CurrentCardId -ge 8) {
                $env:SERVICEIT_CUTOVER_CURRENT_CARD_ID = [string]$CurrentCardId
            }
            try {
                Push-Location $Repo
                try {
                    $ProbeLines = @(& $Python -m leadpilot.serviceit_cutover_access_probe 2>$null)
                    $ProbeExit = $LASTEXITCODE
                } finally {
                    Pop-Location
                }
            } catch {
                # Never print native command errors; they can contain a
                # webhook path or unexpected CRM data in future versions.
                Write-Host 'CUTOVER_ACCESS_PROBE=UNAVAILABLE_SAFE'
            } finally {
                Remove-Item Env:\SERVICEIT_CUTOVER_CURRENT_CARD_ID -ErrorAction SilentlyContinue
            }
            foreach ($Line in $ProbeLines) {
                if ($Line -cmatch '^SERVICEIT_CUTOVER_PROBE_(TYPE|LIST_MINIMAL|LIST_CATEGORY_ONLY|GET_CURRENT|GET_3|CRM_WRITES|GIGACHAT_CALLS|STATUS)=[A-Z0-9_]+$') {
                    Write-Host $Line
                }
            }
            if ($ProbeExit -ne 0) {
                Write-Host 'CUTOVER_ACCESS_PROBE_EXIT=NOT_OK'
            }
            exit 2
        }
        exit 0
    } finally {
        Remove-Item Env:\SERVICEIT_BITRIX_WEBHOOK -ErrorAction SilentlyContinue
        $BitrixDiagnostic = $null
    }
}

function Set-SecretFromStdin([string]$Name, [string]$Secret) {
    # PowerShell 5.1 external stdin pipeline appends a newline. Use redirected
    # stdin of the GitHub CLI to send EXACT text with NO trailing newline.
    $Start = New-Object System.Diagnostics.ProcessStartInfo
    $Start.FileName = $Gh
    $Start.Arguments = "secret set $Name --repo $Repository"
    $Start.UseShellExecute = $false
    $Start.RedirectStandardInput = $true
    $Start.RedirectStandardOutput = $true
    $Start.RedirectStandardError = $true
    $Start.CreateNoWindow = $true
    $Process = [System.Diagnostics.Process]::Start($Start)
    try {
        $Process.StandardInput.Write($Secret)
        $Process.StandardInput.Close()
        if (-not $Process.WaitForExit(120000)) {
            $Process.Kill()
            throw "GITHUB_SECRET_UPLOAD_TIMEOUT_$Name"
        }
        if ($Process.ExitCode -ne 0) { throw "GITHUB_SECRET_UPLOAD_FAILED_$Name" }
    } finally {
        $Process.Dispose()
    }
}

function Set-Variable([string]$Name, [string]$Value) {
    & $Gh variable set $Name --repo $Repository --body $Value *> $null
    if ($LASTEXITCODE -ne 0) { throw "GITHUB_VARIABLE_WRITE_FAILED_$Name" }
}
$ExistingVariables = @(& $Gh variable list --repo $Repository --json 'name,value' | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw 'GITHUB_VARIABLE_LIST_FAILED' }
if (@($ExistingVariables | Where-Object { $_.name -eq 'SERVICEIT_FREE_CLOUD_ENABLED' -and $_.value -eq 'YES' }).Count) {
    throw 'CLOUD_ALREADY_ENABLED_REFUSE_SECRET_OR_CURSOR_OVERWRITE'
}
$ExistingSecrets = @(& $Gh secret list --repo $Repository --json 'name' | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw 'GITHUB_SECRET_LIST_FAILED' }
$RemoteKeyExists = @($ExistingSecrets | Where-Object { $_.name -eq 'SERVICEIT_GH_LEDGER_KEY' }).Count -gt 0
if ($RemoteKeyExists -and -not (Test-Path -LiteralPath $KeyFile -PathType Leaf)) {
    throw 'REMOTE_LEDGER_KEY_EXISTS_NO_LOCAL_MATCH_REFUSE_ROTATION'
}
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
if ($Task.State -eq 'Running') { throw 'LOCAL_TASK_RUNNING_DO_NOT_SWITCH_WHILE_CRM_WRITE_POSSIBLE' }
$Agree = Read-Host 'Activate FREE cloud, transfer protected ServiceIT secrets, and disable local scheduler? Type YES'
if ($Agree -cne 'YES') { throw 'OWNER_CLOUD_CONSENT_MISSING' }

$Bitrix = $null
$Giga = $null
$Fernet = $null
try {
    $Bitrix = Read-DpapiSecret $BitrixFile
    $Giga = Read-DpapiSecret $ModelFile
    if ([string]::IsNullOrWhiteSpace($Bitrix) -or [string]::IsNullOrWhiteSpace($Giga)) {
        throw 'EMPTY_DPAPI_CREDENTIAL'
    }
    # Read-only snapshot of the last REAL existing card (no source/customer content).
    $env:SERVICEIT_BITRIX_WEBHOOK = $Bitrix
    try {
        Push-Location $Repo
        try {
            $Check = @(& $Python -m leadpilot.serviceit_cloud_cutover_readonly)
            if ($LASTEXITCODE -ne 0) {
                foreach ($Line in $Check) {
                    if ($Line -cmatch '^SERVICEIT_CLOUD_CUTOVER_DIAGNOSTIC=[A-Z0-9_:]+$') {
                        Write-Host $Line
                    }
                }
                throw 'CUTOVER_READONLY_INVENTORY_FAILED'
            }
        } finally { Pop-Location }
    } finally {
        Remove-Item Env:\SERVICEIT_BITRIX_WEBHOOK -ErrorAction SilentlyContinue
    }
    $Matches = @($Check | Where-Object { $_ -match '^SERVICEIT_CLOUD_CUTOVER_MAX_ID=([0-9]+)$' })
    if ($Matches.Count -ne 1) { throw 'CUTOVER_ID_NOT_CONFIRMED' }
    $InitialCutover = [int]($Matches[0] -split '=')[1]
    Write-Host "CLOUD_CUTOVER_READONLY_MAX_ID=$InitialCutover"

    if (Test-Path -LiteralPath $KeyFile -PathType Leaf) {
        $Fernet = Read-DpapiSecret $KeyFile
    } else {
        $Bytes = New-Object byte[] 32
        $Random = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $Random.GetBytes($Bytes) } finally { $Random.Dispose() }
        $Fernet = [Convert]::ToBase64String($Bytes).Replace('+','-').Replace('/','_')
        $KeySecure = ConvertTo-SecureString -String $Fernet -AsPlainText -Force
        try {
            $KeySecure | ConvertFrom-SecureString |
                Set-Content -LiteralPath $KeyFile -Encoding UTF8 -NoNewline
        } finally { $KeySecure.Dispose() }
    }

    # Secure GitHub transfer. Passwords never sent as process argv or written to git.
    Set-SecretFromStdin 'SERVICEIT_BITRIX_WEBHOOK' $Bitrix
    Set-SecretFromStdin 'GIGACHAT_CREDENTIALS' $Giga
    Set-SecretFromStdin 'SERVICEIT_GH_LEDGER_KEY' $Fernet
    Set-Variable 'GIGACHAT_SCOPE' $Scope
    Set-Variable 'SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED' 'YES'
    Write-Host 'GITHUB_ENCRYPTED_SECRETS=CONFIGURED'

    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    if ($Task.State -eq 'Running') { throw 'LOCAL_TASK_RUNNING_CUTOVER_ABORTED' }
    if ($Task.State -ne 'Disabled') {
        Disable-ScheduledTask -TaskName $TaskName | Out-Null
    }
    if ((Get-ScheduledTask -TaskName $TaskName).State -ne 'Disabled') {
        throw 'LOCAL_TASK_DISABLE_UNVERIFIED'
    }
    Write-Host 'WINDOWS_TASK_DISABLED=YES'

    # Once the local writer is stopped, capture the final upper bound again.
    $env:SERVICEIT_BITRIX_WEBHOOK = $Bitrix
    try {
        Push-Location $Repo
        try {
            $FinalCheck = @(& $Python -m leadpilot.serviceit_cloud_cutover_readonly)
            if ($LASTEXITCODE -ne 0) {
                foreach ($Line in $FinalCheck) {
                    if ($Line -cmatch '^SERVICEIT_CLOUD_CUTOVER_DIAGNOSTIC=[A-Z0-9_:]+$') {
                        Write-Host $Line
                    }
                }
                throw 'FINAL_CUTOVER_READONLY_INVENTORY_FAILED'
            }
        } finally { Pop-Location }
    } finally {
        Remove-Item Env:\SERVICEIT_BITRIX_WEBHOOK -ErrorAction SilentlyContinue
    }
    $FinalMatches = @($FinalCheck | Where-Object { $_ -match '^SERVICEIT_CLOUD_CUTOVER_MAX_ID=([0-9]+)$' })
    if ($FinalMatches.Count -ne 1) { throw 'FINAL_CUTOVER_ID_UNVERIFIED' }
    $FinalCutover = [int]($FinalMatches[0] -split '=')[1]
    if ($FinalCutover -lt $InitialCutover) { throw 'CUTOVER_RESULT_MOVED_BACKWARD' }
    Set-Variable 'SERVICEIT_GH_START_AFTER_ID' ([string]$FinalCutover)
    # Enable last; if any earlier step fails, cloud remains disabled.
    Set-Variable 'SERVICEIT_FREE_CLOUD_ENABLED' 'YES'
    Write-Host "SERVICEIT_FREE_GITHUB_CLOUD=ENABLED"
    Write-Host "CLOUD_CUTOVER_ID=$FinalCutover"
    Write-Host 'CHECK_GITHUB_ACTIONS=ServiceIT Free Cloud AI Cards'
    Write-Host 'NOTE=HOLD remains preliminary until independent source approval; real GigaChat+Score requires separate acceptance.'
} finally {
    Remove-Item Env:\SERVICEIT_BITRIX_WEBHOOK -ErrorAction SilentlyContinue
    $Bitrix = $null
    $Giga = $null
    $Fernet = $null
}
