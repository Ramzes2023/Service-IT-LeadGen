[CmdletBinding()]
param([string]$PythonPath = 'py', [string]$OutputDirectory = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $Repo ('docs\manager-demo-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
}
Push-Location -LiteralPath $Repo
try {
    & $PythonPath -B -m leadpilot.bitrix_manager_demo --output $OutputDirectory
    if ($LASTEXITCODE -ne 0) { throw 'OFFLINE_DEMO_FAILED' }
    Write-Output ('DEMO_HTML=' + (Join-Path $OutputDirectory 'index.html'))
    Write-Output ('DEMO_REPORT=' + (Join-Path $OutputDirectory 'report.json'))
} finally { Pop-Location }
