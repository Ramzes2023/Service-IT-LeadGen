# Scheduled official EIS RSS run after one verified manual acceptance.
# Uses pinned worktree source and existing original venv, no further Git fetch.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [Parameter(Mandatory=$true)][string]$PreviousReport,
    [Parameter(Mandatory=$true)][string]$StateDir
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $PreviousReport -PathType Leaf)) {
    throw 'EIS_SCHEDULED_DEPENDENCY_NOT_FOUND'
}
New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
$Stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$Log = Join-Path $StateDir ('scheduled-' + $Stamp + '.txt')
$FinalCode = 1
Push-Location -LiteralPath $Repo
try {
    Write-Output 'EIS_SCHEDULED_START=OFFICIAL_FOUR_RSS_GETS'
    & $PythonPath -m leadpilot.eis_daily --source-report $PreviousReport --state-dir $StateDir 2>&1 |
        ForEach-Object {
            $Line = [string]$_
            Write-Output $Line
            Add-Content -LiteralPath $Log -Encoding UTF8 -Value $Line
        }
    $FinalCode = $LASTEXITCODE
} finally {
    Pop-Location
}
('EIS_SCHEDULED_EXIT=' + $FinalCode) | Tee-Object -FilePath $Log -Append
if ($FinalCode -ne 0) { exit $FinalCode }
Write-Output ('EIS_SCHEDULED_LOG=' + $Log)
Write-Output 'EIS_SCHEDULED_DONE=YES; CRM_WRITES=NO'
