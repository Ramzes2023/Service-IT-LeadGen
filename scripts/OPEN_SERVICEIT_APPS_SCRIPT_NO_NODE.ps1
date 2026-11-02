#Requires -Version 5.1
# SAFE LOCAL HANDOFF: no Node.js, npm, npx, clasp, passwords or REST writes.
# This script ONLY verifies reviewed Git blobs and copies ONE source file to
# the Windows clipboard for manual paste into the owner's existing Apps Script.
[CmdletBinding()]
param(
  [ValidateSet('Code', 'Manifest')]
  [string]$Part = 'Code',
  [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Repo = Join-Path $env:USERPROFILE 'Service_IT_GitHub_Repo\Service-IT-LeadGen'
$Branch = 'feat/serviceit-v2-durable-gmail-core-20261102'
$CodePath = 'integrations/apps_script/Code.gs'
$ManifestPath = 'integrations/apps_script/appsscript.json'
# Exact reviewed Git blob SHAs from PR #84 at 2026-11-02.
$CodeBlob = 'bdbf13f2144498641eed36479fb2b76e5eb1d1e0'
$ManifestBlob = '90f4b1d5afb9738caa836bdcb7879674d275a779'

function Assert-Exit([string]$Phase) {
  if ($LASTEXITCODE -ne 0) { throw ("SERVICEIT_" + $Phase + "_FAILED_EXIT_" + $LASTEXITCODE) }
}
if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) { throw 'GIT_EXE_NOT_FOUND' }
if (-not (Get-Command Set-Clipboard -ErrorAction SilentlyContinue)) { throw 'SET_CLIPBOARD_UNAVAILABLE' }
if (-not (Test-Path -LiteralPath (Join-Path $Repo '.git'))) {
  throw 'SERVICEIT_REPO_NOT_FOUND'
}

$Temp = Join-Path $env:TEMP ('serviceit-appscript-no-node-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Temp -Force | Out-Null
try {
  # Fetch the reviewed PR branch without changing working files or main.
  & git.exe -C $Repo fetch --quiet --no-tags origin $Branch
  Assert-Exit 'FETCH'

  $CurrentCode = ((& git.exe -C $Repo rev-parse ('FETCH_HEAD:' + $CodePath)) | Out-String).Trim()
  Assert-Exit 'CODE_SHA'
  $CurrentManifest = ((& git.exe -C $Repo rev-parse ('FETCH_HEAD:' + $ManifestPath)) | Out-String).Trim()
  Assert-Exit 'MANIFEST_SHA'
  if ($CurrentCode -ne $CodeBlob -or $CurrentManifest -ne $ManifestBlob) {
    throw 'UPSTREAM_FILES_CHANGED_REVIEW_REQUIRED_NO_CLIPBOARD_WRITE'
  }

  $Archive = Join-Path $Temp 'reviewed-source.zip'
  & git.exe -C $Repo archive --format=zip ("--output=" + $Archive) FETCH_HEAD $CodePath $ManifestPath
  Assert-Exit 'ARCHIVE'
  $Expanded = Join-Path $Temp 'expanded'
  Expand-Archive -LiteralPath $Archive -DestinationPath $Expanded

  if ($Part -eq 'Code') { $SourcePath = $CodePath }
  else { $SourcePath = $ManifestPath }

  $File = Join-Path $Expanded ($SourcePath -replace '/', '\')
  if (-not (Test-Path -LiteralPath $File)) { throw 'REVIEWED_SOURCE_NOT_IN_ARCHIVE' }
  $Text = [System.IO.File]::ReadAllText($File, [System.Text.Encoding]::UTF8)
  if ([string]::IsNullOrWhiteSpace($Text)) { throw 'EMPTY_REVIEWED_SOURCE' }
  if ($Part -eq 'Manifest') {
    $Manifest = ConvertFrom-Json -InputObject $Text
    if ($Manifest.runtimeVersion -ne 'V8') { throw 'INVALID_RUNTIME' }
    if (@($Manifest.dependencies.enabledAdvancedServices).Count -ne 1) { throw 'INVALID_GMAIL_SERVICE_COUNT' }
  } else {
    if (-not $Text.Contains('function checkB2BCenter()')) { throw 'MISSING_SAFE_PREVIEW' }
    if (-not $Text.Contains('function stopPollingTrigger()')) { throw 'MISSING_STOP_CONTROL' }
  }

  Set-Clipboard -Value $Text
  Write-Host ('SERVICEIT_CLIPBOARD_PART=' + $Part)
  Write-Host 'REVIEWED_GIT_BLOBS=PASS'
  Write-Host 'NODE_REQUIRED=NO; CLASP_REQUIRED=NO'
  Write-Host 'REMOTE_GOOGLE_SCRIPT_MODIFIED=NO'
  Write-Host 'GMAIL_EXTERNAL_TRANSMISSION=NO; BITRIX_WRITES=0; TRIGGER_INSTALLED=NO'
  Write-Host 'PASTE ONLY INTO EXISTING Service IT - Gmail Tender Automation AFTER BACKUP'
  Write-Host 'Google account must be ramzesisayev444@gmail.com'
  if (-not $NoBrowser) {
    Start-Process 'https://script.google.com/home/projects'
  }
} finally {
  Remove-Item -LiteralPath $Temp -Recurse -Force -ErrorAction SilentlyContinue
}
