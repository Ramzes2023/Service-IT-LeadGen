# Diagnose Task Scheduler queue for the already accepted Service IT EIS worker.
# Read-only: no Start/Stop, changes to scheduler, HTTPS calls, database or CRM.
[CmdletBinding()]
param(
    [string]$TaskName = 'ServiceIT-EIS-Official-Research-Only',
    [string]$StateDir = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $StateDir) {
    $StateDir = Join-Path $env:LOCALAPPDATA 'ServiceIT\EIS-Daily-Operational'
}
Write-Output 'EIS_TASK_DIAGNOSTIC=START; READ_ONLY=YES; EIS_ONLY=YES'
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
$info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction Stop
Write-Output ('EIS_TASK_STATE=' + [string]$task.State)
Write-Output ('EIS_TASK_ENABLED=' + [string]$task.Settings.Enabled)
Write-Output ('EIS_TASK_LAST_RUN=' + $info.LastRunTime.ToString('o'))
Write-Output ('EIS_TASK_LAST_RESULT=' + [string]$info.LastTaskResult)
Write-Output ('EIS_TASK_NEXT_RUN=' + $info.NextRunTime.ToString('o'))
Write-Output ('EIS_TASK_MISSED_RUNS=' + [string]$info.NumberOfMissedRuns)
Write-Output ('EIS_TASK_LOGON_TYPE=' + [string]$task.Principal.LogonType)
Write-Output ('EIS_TASK_USER=' + [string]$task.Principal.UserId)
Write-Output ('EIS_TASK_CURRENT_USER=' + [Security.Principal.WindowsIdentity]::GetCurrent().Name)
$settings = $task.Settings
foreach ($key in @('RunOnlyIfNetworkAvailable','DisallowStartIfOnBatteries',
                    'StopIfGoingOnBatteries','RunOnlyIfIdle','StartWhenAvailable',
                    'MultipleInstances','WakeToRun','ExecutionTimeLimit')) {
    $property = $settings.PSObject.Properties[$key]
    if ($null -ne $property) {
        Write-Output ('EIS_TASK_SETTING_' + $key + '=' + [string]$property.Value)
    }
}
$actionNumber = 0
foreach ($action in @($task.Actions)) {
    $actionNumber += 1
    Write-Output ('EIS_TASK_ACTION_' + $actionNumber + '_EXECUTE=' + $action.Execute)
    Write-Output ('EIS_TASK_ACTION_' + $actionNumber + '_ARGUMENTS=' + $action.Arguments)
    Write-Output ('EIS_TASK_ACTION_' + $actionNumber + '_WORKDIR=' + $action.WorkingDirectory)
    $file = [Environment]::ExpandEnvironmentVariables([string]$action.Execute).Trim('"')
    Write-Output ('EIS_TASK_ACTION_' + $actionNumber + '_EXE_EXISTS=' + [string](Test-Path -LiteralPath $file -PathType Leaf))
    $argument = [string]$action.Arguments
    $scriptMatch = [regex]::Match($argument,'(?i)(?:^|\s)-File\s+"([^"]+)"')
    if ($scriptMatch.Success) {
        Write-Output ('EIS_TASK_ACTION_' + $actionNumber + '_PS1_EXISTS=' +
                      [string](Test-Path -LiteralPath $scriptMatch.Groups[1].Value -PathType Leaf))
    } else {
        Write-Output ('EIS_TASK_ACTION_' + $actionNumber + '_PS1_PATH=UNPARSED')
    }
}
if (Test-Path -LiteralPath $StateDir -PathType Container) {
    $logs = @(Get-ChildItem -LiteralPath $StateDir -Filter 'scheduled-*.txt' -File |
        Sort-Object LastWriteTime -Descending)
    Write-Output ('EIS_TASK_SCHEDULED_LOG_COUNT=' + $logs.Count)
    if ($logs.Count -gt 0) {
        Write-Output ('EIS_TASK_LATEST_LOG=' + $logs[0].FullName)
        $lines = @(Get-Content -LiteralPath $logs[0].FullName -Encoding UTF8 -Tail 24)
        foreach ($line in $lines) {
            Write-Output ('EIS_TASK_LOG_LINE=' + [string]$line)
        }
        $complete = @($lines | Where-Object { $_ -eq 'EIS_SCHEDULED_EXIT=0' }).Count -gt 0
        $healthy = @($lines | Where-Object { $_ -like 'EIS_HEALTH_STATUS=PASS*' }).Count -gt 0
        if ($complete -and $healthy) {
            Write-Output 'EIS_TASK_SCHEDULED_EVIDENCE=PASS'
        } else {
            Write-Output 'EIS_TASK_SCHEDULED_EVIDENCE=INCOMPLETE_OR_FAILED'
        }
    } else {
        Write-Output 'EIS_TASK_SCHEDULED_EVIDENCE=NO_LOG_FILE'
    }
} else {
    Write-Output 'EIS_TASK_SCHEDULED_EVIDENCE=STATE_DIR_MISSING'
}
$eventLog = 'Microsoft-Windows-TaskScheduler/Operational'
try {
    $log = Get-WinEvent -ListLog $eventLog -ErrorAction Stop
    Write-Output ('EIS_TASK_OPERATIONAL_LOG_ENABLED=' + [string]$log.IsEnabled)
    if ($log.IsEnabled) {
        $cutoff = (Get-Date).AddHours(-6)
        $events = @(Get-WinEvent -FilterHashtable @{
            LogName=$eventLog; StartTime=$cutoff
        } -MaxEvents 500 -ErrorAction Stop | Where-Object {
            $_.Message -and $_.Message.Contains($TaskName)
        } | Select-Object -First 35)
        Write-Output ('EIS_TASK_RELATED_EVENT_COUNT=' + $events.Count)
        foreach ($evt in $events) {
            $summary = (($evt.Message -replace '\s+', ' ').Trim())
            if ($summary.Length -gt 240) { $summary = $summary.Substring(0,240) }
            Write-Output ('EIS_TASK_EVENT=' + $evt.TimeCreated.ToString('o') +
                          ';ID=' + $evt.Id + ';LEVEL=' + $evt.LevelDisplayName +
                          ';MESSAGE=' + $summary)
        }
    } else {
        Write-Output 'EIS_TASK_EVENT_DIAGNOSTICS=OPERATIONAL_LOG_DISABLED'
    }
} catch {
    Write-Output ('EIS_TASK_EVENT_DIAGNOSTICS=UNAVAILABLE; TYPE=' +
                  $_.Exception.GetType().Name)
}
if ([string]$task.State -eq 'Queued') {
    Write-Output 'EIS_TASK_DIAGNOSIS=QUEUED; NO_PROOF_WORKER_STARTED'
} elseif ([string]$task.State -eq 'Running') {
    Write-Output 'EIS_TASK_DIAGNOSIS=RUNNING; VERIFY_COMPLETION_FROM_LOG'
} else {
    Write-Output ('EIS_TASK_DIAGNOSIS=' + [string]$task.State +
                  '; VERIFY_ACTION_AND_OPERATIONAL_EVENTS')
}
Write-Output 'EIS_TASK_DIAGNOSTIC=COMPLETE; MODIFICATIONS=NONE'
