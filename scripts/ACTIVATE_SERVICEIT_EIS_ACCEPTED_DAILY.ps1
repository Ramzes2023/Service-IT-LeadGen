# Activate a CURRENT-USER Windows schedule for an already-accepted EIS worktree.
# Exact Windows acceptance SHA is pinned, no Git fetch and no main modifications.
[CmdletBinding()]
param(
    [switch]$Enable,
    [switch]$StartNow,
    [ValidatePattern('^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')]
    [string]$DailyAt = '09:30',
    [string]$ProjectRepo = '',
    [string]$SourceReport = '',
    [string]$StateDir = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$AcceptedSha = 'f15d029fd9df769670d11d7383bd0abb90308074'
$TaskName = 'ServiceIT-EIS-Official-Research-Only'
if (-not $Enable) {
    throw 'EIS_ACTIVATE_EXPLICIT_ENABLE_REQUIRED; NO_TASK_CREATED'
}
if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
    throw 'EIS_WINDOWS_SCHEDULER_NOT_AVAILABLE'
}
if (-not $ProjectRepo) {
    $ProjectRepo = Join-Path $env:USERPROFILE 'Service_IT_GitHub_Repo\Service-IT-LeadGen'
}
$Python = Join-Path $ProjectRepo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'EIS_ACCEPTED_PYTHON_NOT_FOUND'
}
if (-not $StateDir) {
    $StateDir = Join-Path $env:LOCALAPPDATA 'ServiceIT\EIS-Daily-Operational'
}
if (-not $SourceReport) {
    $SourceRoot = Join-Path $env:LOCALAPPDATA 'ServiceIT\EIS-Scope-Review'
    if (-not (Test-Path -LiteralPath $SourceRoot -PathType Container)) {
        throw 'EIS_SOURCE_REPORT_FOLDER_NOT_FOUND'
    }
    $Latest = Get-ChildItem -LiteralPath $SourceRoot -Recurse -Filter 'eis_scope_*.json' -File |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $Latest) { throw 'EIS_ACCEPTED_SOURCE_REPORT_NOT_FOUND' }
    $SourceReport = $Latest.FullName
}
if (-not (Test-Path -LiteralPath $SourceReport -PathType Leaf)) {
    throw 'EIS_SOURCE_REPORT_NOT_FOUND'
}
$WorkRoot = Join-Path $env:LOCALAPPDATA 'ServiceIT\EIS-EndToEnd'
if (-not (Test-Path -LiteralPath $WorkRoot -PathType Container)) {
    throw 'EIS_ACCEPTED_WORKTREE_FOLDER_NOT_FOUND'
}
$Accepted = $null
foreach ($entry in @(Get-ChildItem -LiteralPath $WorkRoot -Directory |
        Sort-Object LastWriteTime -Descending)) {
    $runner = Join-Path $entry.FullName 'scripts\RUN_SERVICEIT_EIS_SCHEDULED.ps1'
    if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) { continue }
    $head = (& git -C $entry.FullName rev-parse HEAD 2>$null)
    if ($LASTEXITCODE -eq 0 -and [string]$head -eq $AcceptedSha) {
        $Accepted = $entry.FullName
        break
    }
}
if (-not $Accepted) { throw 'EIS_PREVIOUS_236_PASS_PINNED_WORKTREE_NOT_FOUND' }
$Runner = Join-Path $Accepted 'scripts\RUN_SERVICEIT_EIS_SCHEDULED.ps1'

# A read-only cross-check must succeed before registration.
Push-Location -LiteralPath $Accepted
try {
    Write-Output 'EIS_SCHEDULER_PREFLIGHT=RUNNING_READONLY_HEALTH'
    & $Python -m leadpilot.eis_daily_health --state-dir $StateDir
    if ($LASTEXITCODE -ne 0) {
        throw 'EIS_SCHEDULER_PERSISTENT_STATE_UNHEALTHY_NO_TASK_CREATED'
    }
} finally {
    Pop-Location
}

# Idempotent and safe: do NOT overwrite an existing named scheduled task.
$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Existing) {
    Write-Output 'EIS_SCHEDULER_EXISTING_TASK=YES; NO_CHANGES'
    throw 'EIS_SCHEDULER_NAME_ALREADY_REGISTERED_REVIEW_MANUALLY'
}
$CurrentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited
$Time = [datetime]::ParseExact($DailyAt, 'HH:mm',
    [System.Globalization.CultureInfo]::InvariantCulture)
# Reject path injection rather than guessing escaping.
foreach ($value in @($Runner,$Python,$SourceReport,$StateDir)) {
    if ($value.Contains('"') -or $value.Contains("'") -or
        $value.Contains([char]10) -or $value.Contains([char]13)) {
        throw 'EIS_SCHEDULER_UNSAFE_LOCAL_PATH'
    }
}
$Args = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    $Runner + '" -PythonPath "' + $Python + '" -PreviousReport "' +
    $SourceReport + '" -StateDir "' + $StateDir + '"'
$Action = New-ScheduledTaskAction -Execute (Join-Path $PSHOME 'powershell.exe') -Argument $Args
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description 'Service IT official EIS public RSS; current user only; no CRM/outbound' | Out-Null
$Registered = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
$RegisteredInfo = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction Stop
Write-Output ('EIS_SCHEDULER_TASK_NAME=' + $Registered.TaskName)
Write-Output ('EIS_SCHEDULER_USER=' + $CurrentUser)
Write-Output ('EIS_SCHEDULER_AT_LOCAL=' + $DailyAt)
Write-Output ('EIS_SCHEDULER_ACCEPTED_SHA=' + $AcceptedSha)
Write-Output ('EIS_SCHEDULER_NEXT_RUN=' + $RegisteredInfo.NextRunTime.ToString('o'))
Write-Output 'EIS_SCHEDULER_INSTALLED=YES; SOURCES=EIS_ONLY; CRM_WRITES=NO'
if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Write-Output 'EIS_SCHEDULER_START_NOW=REQUESTED; COMPLETION_NOT_YET_VERIFIED'
    Write-Output ('EIS_SCHEDULER_LOG_FOLDER=' + $StateDir)
}
