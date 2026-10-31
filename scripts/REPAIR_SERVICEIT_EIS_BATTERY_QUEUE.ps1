# Repair ONLY the queued Service IT EIS task's power restrictions.
# No other source, no CRM, no database rewrite. Explicit -ApplyFix required.
[CmdletBinding()]
param(
    [switch]$ApplyFix,
    [switch]$TestRun,
    [string]$StateDir = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$TaskName = 'ServiceIT-EIS-Official-Research-Only'
$AcceptedSha = 'f15d029fd9df769670d11d7383bd0abb90308074'
if (-not $ApplyFix) { throw 'EIS_REPAIR_EXPLICIT_APPLYFIX_REQUIRED' }
if (-not $StateDir) { $StateDir = Join-Path $env:LOCALAPPDATA 'ServiceIT\EIS-Daily-Operational' }
$before = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
if ($before.TaskName -ne $TaskName -or @($before.Actions).Count -ne 1 -or
    @($before.Triggers).Count -ne 1 -or
    [string]$before.Principal.LogonType -ne 'Interactive') {
    throw 'EIS_REPAIR_TASK_IDENTITY_NOT_EXPECTED'
}
$action = $before.Actions[0]
$arguments = [string]$action.Arguments
$match = [regex]::Match($arguments, '(?i)(?:^|\s)-File\s+"([^"]+)"')
if (-not $match.Success -or
    [IO.Path]::GetFileName($match.Groups[1].Value) -ne 'RUN_SERVICEIT_EIS_SCHEDULED.ps1' -or
    -not (Test-Path -LiteralPath $match.Groups[1].Value -PathType Leaf)) {
    throw 'EIS_REPAIR_SCHEDULED_POWERSHELL_SCRIPT_NOT_TRUSTED'
}
$repo = Split-Path -Parent (Split-Path -Parent $match.Groups[1].Value)
$observed = (& git -C $repo rev-parse HEAD 2>$null)
if ($LASTEXITCODE -ne 0 -or ([string]$observed).Trim() -ne $AcceptedSha) {
    throw 'EIS_REPAIR_WORKTREE_SHA_MISMATCH'
}
$preExe = [string]$action.Execute
$preArgs = [string]$action.Arguments
$preUser = [string]$before.Principal.UserId
$preTrigger = [string]$before.Triggers[0].StartBoundary
$backupRoot = Join-Path $env:LOCALAPPDATA 'ServiceIT\EIS-Scheduler-Repair'
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
$backup = Join-Path $backupRoot ('task-before-' + $stamp + '.xml')
Export-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Set-Content -LiteralPath $backup -Encoding UTF8
Write-Output ('EIS_REPAIR_TASK_BACKUP=' + $backup)
Write-Output ('EIS_REPAIR_STATE_BEFORE=' + [string]$before.State)
Write-Output ('EIS_REPAIR_POWER_BLOCK_BEFORE=' + [string]$before.Settings.DisallowStartIfOnBatteries)
Write-Output ('EIS_REPAIR_STOP_ON_BATTERY_BEFORE=' + [string]$before.Settings.StopIfGoingOnBatteries)
try {
    $battery = @(Get-CimInstance Win32_Battery -ErrorAction Stop)
    if ($battery.Count -eq 0) {
        Write-Output 'EIS_REPAIR_BATTERY_HARDWARE=NOT_DETECTED'
    } else {
        $battery | ForEach-Object {
            Write-Output ('EIS_REPAIR_BATTERY_STATUS=' + [string]$_.BatteryStatus)
        }
    }
} catch {
    Write-Output 'EIS_REPAIR_BATTERY_HARDWARE=UNAVAILABLE'
}
if ($before.Settings.DisallowStartIfOnBatteries -or
    $before.Settings.StopIfGoingOnBatteries) {
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
    Set-ScheduledTask -TaskName $TaskName -Settings $settings -ErrorAction Stop | Out-Null
}
$after = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
if ($after.Settings.DisallowStartIfOnBatteries -or
    $after.Settings.StopIfGoingOnBatteries -or
    @($after.Actions).Count -ne 1 -or
    [string]$after.Actions[0].Execute -ne $preExe -or
    [string]$after.Actions[0].Arguments -ne $preArgs -or
    [string]$after.Principal.UserId -ne $preUser -or
    @($after.Triggers).Count -ne 1 -or
    [string]$after.Triggers[0].StartBoundary -ne $preTrigger) {
    throw 'EIS_REPAIR_POST_CHANGE_CHECK_FAILED_USE_BACKUP'
}
Write-Output 'EIS_REPAIR_POWER_BLOCK_AFTER=False'
Write-Output 'EIS_REPAIR_STOP_ON_BATTERY_AFTER=False'
Write-Output ('EIS_REPAIR_STATE_AFTER=' + [string]$after.State)
Write-Output 'EIS_REPAIR_ACTION_TRIGGER_USER_UNCHANGED=YES'
if ($TestRun) {
    $startedAt = Get-Date
    if ([string]$after.State -ne 'Running') {
        Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        Write-Output 'EIS_REPAIR_PROBE=START_REQUESTED'
    } else {
        Write-Output 'EIS_REPAIR_PROBE=TASK_ALREADY_RUNNING'
    }
    $success = $false
    for ($k = 0; $k -lt 10; $k++) {
        Start-Sleep -Seconds 2
        $logs = @(Get-ChildItem -LiteralPath $StateDir -Filter 'scheduled-*.txt' -File -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTime -ge $startedAt.AddSeconds(-2) } |
            Sort-Object LastWriteTime -Descending)
        if ($logs.Count -eq 0) { continue }
        $lines = @(Get-Content -LiteralPath $logs[0].FullName -Encoding UTF8 -ErrorAction SilentlyContinue)
        if (@($lines | Where-Object { $_ -eq 'EIS_SCHEDULED_EXIT=0' }).Count -gt 0 -and
            @($lines | Where-Object { $_ -like 'EIS_HEALTH_STATUS=PASS*' }).Count -gt 0) {
            Write-Output ('EIS_REPAIR_PROBE_PASS_LOG=' + $logs[0].FullName)
            Write-Output 'EIS_REPAIR_PROBE=PASS'
            $success = $true
            break
        }
    }
    if (-not $success) {
        $current = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        Write-Output ('EIS_REPAIR_PROBE_STATE=' + [string]$current.State)
        Write-Output 'EIS_REPAIR_PROBE=NOT_YET_VERIFIED'
    }
}
Write-Output 'EIS_REPAIR_FINISHED=YES; ONLY_SCHEDULER_SETTINGS_CHANGED=YES; CRM_WRITES=NO'
