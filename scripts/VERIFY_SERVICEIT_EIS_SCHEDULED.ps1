# Verify a Windows scheduled EIS task and its last independent source audit.
# Read-only: no Task Scheduler changes, HTTP requests, CRM or DB writes.
[CmdletBinding()]
param([string]$StateDir = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$TaskName = 'ServiceIT-EIS-Official-Research-Only'
if (-not $StateDir) {
    $StateDir = Join-Path $env:LOCALAPPDATA 'ServiceIT\EIS-Daily-Operational'
}
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
$Info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction Stop
Write-Output ('EIS_SCHEDULE_VERIFY_TASK=' + $Task.TaskName)
Write-Output ('EIS_SCHEDULE_VERIFY_STATE=' + $Task.State)
Write-Output ('EIS_SCHEDULE_VERIFY_NEXT=' + $Info.NextRunTime.ToString('o'))
Write-Output ('EIS_SCHEDULE_VERIFY_LAST=' + $Info.LastRunTime.ToString('o'))
Write-Output ('EIS_SCHEDULE_VERIFY_LAST_RESULT=' + $Info.LastTaskResult)
if ([string]$Task.State -eq 'Queued' -or [string]$Task.State -eq 'Running') {
    Write-Output 'EIS_SCHEDULE_VERIFY_EVIDENCE=NOT_COMPLETED_TASK_QUEUED_OR_RUNNING'
    exit 2
}
if ($Info.LastTaskResult -ne 0) {
    Write-Output 'EIS_SCHEDULE_VERIFY_EVIDENCE=LAST_TASK_RESULT_NONZERO'
    exit 2
}
$Latest = $null
if (Test-Path -LiteralPath $StateDir -PathType Container) {
    $Latest = Get-ChildItem -LiteralPath $StateDir -File -Filter 'scheduled-*.txt' |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
}
if (-not $Latest) {
    Write-Output 'EIS_SCHEDULE_VERIFY_EVIDENCE=NO_COMPLETED_SCHEDULED_LOG_YET'
    exit 2
}
Write-Output ('EIS_SCHEDULE_VERIFY_LATEST_LOG=' + $Latest.FullName)
if ($Latest.LastWriteTime -lt $Info.LastRunTime.AddMinutes(-2)) {
    Write-Output 'EIS_SCHEDULE_VERIFY_EVIDENCE=STALE_LOG_NOT_LATEST_TASK_RUN'
    exit 2
}
$Lines = Get-Content -LiteralPath $Latest.FullName -Encoding UTF8
$Finished = @($Lines | Where-Object { $_ -eq 'EIS_SCHEDULED_EXIT=0' }).Count -gt 0
$Healthy = @($Lines | Where-Object { $_ -like 'EIS_HEALTH_STATUS=PASS*' }).Count -gt 0
if (-not $Finished -or -not $Healthy) {
    Write-Output 'EIS_SCHEDULE_VERIFY_EVIDENCE=PENDING_OR_FAILED; DO_NOT_CLAIM_READY'
    exit 2
}
Write-Output 'EIS_SCHEDULE_VERIFY_EVIDENCE=PASS; EIS_ONLY=YES; CRM_WRITES=NO'
