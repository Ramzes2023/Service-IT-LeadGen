# Explicit opt-in installer for local scheduled official EIS RSS research.
# Execute only AFTER manual acceptance with -Enable.
[CmdletBinding()]
param(
    [switch]$Enable,
    [string]$PythonPath = '',
    [string]$PreviousReport = '',
    [string]$StateDir = '',
    [ValidatePattern('^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')]
    [string]$DailyAt = '09:30'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $Enable) {
    throw 'EIS_DAILY_TASK_EXPLICIT_ENABLE_REQUIRED; NOTHING_REGISTERED'
}
if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
    throw 'EIS_WINDOWS_TASK_SCHEDULER_UNAVAILABLE'
}
$Repo = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $PSScriptRoot 'RUN_SERVICEIT_EIS_SCHEDULED.ps1'
if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) {
    throw 'EIS_DAILY_SCHEDULED_RUNNER_MISSING'
}
if (-not $PythonPath) {
    $PythonPath = Join-Path $Repo '.venv\Scripts\python.exe'
}
if (-not $PreviousReport) {
    $SourceRoot = Join-Path $env:LOCALAPPDATA 'ServiceIT\EIS-Scope-Review'
    if (-not (Test-Path -LiteralPath $SourceRoot -PathType Container)) {
        throw 'EIS_DAILY_SOURCE_REPORT_MISSING'
    }
    $Previous = Get-ChildItem -LiteralPath $SourceRoot -Recurse -File -Filter 'eis_scope_*.json' |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $Previous) { throw 'EIS_DAILY_SOURCE_REPORT_MISSING' }
    $PreviousReport = $Previous.FullName
}
if (-not $StateDir) {
    $StateDir = Join-Path $env:LOCALAPPDATA 'ServiceIT\EIS-Daily-Operational'
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $PreviousReport -PathType Leaf)) {
    throw 'EIS_DAILY_TASK_REQUIREMENTS_MISSING'
}
$Task = 'ServiceIT-EIS-Official-Research-Only'
$Time = [datetime]::ParseExact($DailyAt, 'HH:mm', [System.Globalization.CultureInfo]::InvariantCulture)
$ScriptArgs = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    $Runner + '" -PythonPath "' + $PythonPath + '" -PreviousReport "' +
    $PreviousReport + '" -StateDir "' + $StateDir + '"'
$Action = New-ScheduledTaskAction -Execute (Join-Path $PSHOME 'powershell.exe') -Argument $ScriptArgs
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
# Install for the current user only, without stored credentials.
Register-ScheduledTask -TaskName $Task -Action $Action -Trigger $Trigger -Settings $Settings -Description 'Official public EIS 1C RSS; no CRM writes, no emails' -Force | Out-Null
$Installed = Get-ScheduledTask -TaskName $Task -ErrorAction Stop
Write-Output ('EIS_DAILY_TASK=' + $Installed.TaskName)
Write-Output ('EIS_DAILY_TASK_AT_LOCAL_TIME=' + $DailyAt)
Write-Output 'EIS_DAILY_TASK_INSTALLED=YES; RESEARCH_ONLY=YES; CRM_WRITES=NO'
