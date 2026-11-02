# Safe, owner-run one-time updater for the existing Google Apps Script project.
# No trigger, Gmail transmission or Bitrix write is activated by this script.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Repo = Join-Path $env:USERPROFILE 'Service_IT_GitHub_Repo\Service-IT-LeadGen'
$Branch = 'feat/serviceit-v2-durable-gmail-core-20261102'
$ExpectedAccount = 'ramzesisayev444@gmail.com'
function Check-Exit([string]$label) { if ($LASTEXITCODE -ne 0) { throw "$label failed: $LASTEXITCODE" } }
if (-not (Test-Path -LiteralPath (Join-Path $Repo '.git'))) { throw "Local Service IT Git clone not found at $Repo" }
foreach ($bin in @('git.exe','node.exe','npm.cmd','npx.cmd')) {
  if (-not (Get-Command $bin -ErrorAction SilentlyContinue)) { throw "Required tool missing: $bin" }
}
$Temp = Join-Path $env:TEMP ('serviceit-appsscript-install-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Temp -Force | Out-Null
$Completed = $false
try {
  & git.exe -C $Repo fetch origin $Branch
  Check-Exit 'Git fetch'
  $Archive = Join-Path $Temp 'serviceit-appsscript.zip'
  & git.exe -C $Repo archive --format=zip --output=$Archive "origin/$Branch" 'integrations/apps_script/Code.gs' 'integrations/apps_script/appsscript.json'
  Check-Exit 'Git source archive'
  Expand-Archive -LiteralPath $Archive -DestinationPath (Join-Path $Temp 'extracted')
  $Src = Join-Path $Temp 'extracted\integrations\apps_script'
  $Stage = Join-Path $Temp 'stage'
  $Backup = Join-Path $Temp 'backup'
  New-Item -ItemType Directory -Path $Stage, $Backup | Out-Null
  Copy-Item -LiteralPath (Join-Path $Src 'Code.gs') -Destination $Stage
  Copy-Item -LiteralPath (Join-Path $Src 'appsscript.json') -Destination $Stage
  # Google OAuth must be approved by the account owner; no password is saved here.
  $authorized = (& npx.cmd --yes '@google/clasp@latest' show-authorized-user --json 2>&1 | Out-String)
  if ($LASTEXITCODE -ne 0 -or $authorized -notmatch [regex]::Escape($ExpectedAccount)) {
    Write-Host "A one-time Google browser authorization is required for $ExpectedAccount."
    & npx.cmd --yes '@google/clasp@latest' login
    Check-Exit 'Google OAuth login'
    $authorized = (& npx.cmd --yes '@google/clasp@latest' show-authorized-user --json 2>&1 | Out-String)
    Check-Exit 'Google identity check'
  }
  if ($authorized -notmatch [regex]::Escape($ExpectedAccount)) { throw 'Wrong Google account; refusing to update another script.' }
  $listing = (& npx.cmd --yes '@google/clasp@latest' list-scripts 2>&1 | Out-String)
  Check-Exit 'Apps Script list'
  $candidates = @()
  foreach ($line in ($listing -split "\n")) {
    if ($line -match 'Service IT\s*[—–-]\s*Gmail Tender Aut') {
      $ids = [regex]::Matches($line,'[A-Za-z0-9_-]{25,}')
      if ($ids.Count -eq 1) { $candidates += $ids[0].Value }
    }
  }
  $candidates = @($candidates | Sort-Object -Unique)
  if ($candidates.Count -ne 1) { throw 'Could not identify exactly one existing Service IT Apps Script; no changes made.' }
  $config = @{ scriptId = $candidates[0]; rootDir = '.' } | ConvertTo-Json -Compress
  [IO.File]::WriteAllText((Join-Path $Backup '.clasp.json'),$config,[Text.UTF8Encoding]::new($false))
  [IO.File]::WriteAllText((Join-Path $Stage '.clasp.json'),$config,[Text.UTF8Encoding]::new($false))
  Push-Location $Backup
  try { & npx.cmd --yes '@google/clasp@latest' pull; Check-Exit 'Backup existing Apps Script' }
  finally { Pop-Location }
  Push-Location $Stage
  try { & npx.cmd --yes '@google/clasp@latest' push --force; Check-Exit 'Upload Apps Script' }
  finally { Pop-Location }
  $Completed = $true
  Write-Host 'SERVICEIT_APPSSCRIPT_CODE_INSTALLED=YES' -ForegroundColor Green
  Write-Host 'TRIGGER_INSTALLED=NO; EXTERNAL_TRANSMISSION=NO; CRM_WRITES=0'
}
finally {
  if ($Completed) { Remove-Item -LiteralPath $Temp -Recurse -Force -ErrorAction SilentlyContinue }
  else { Write-Host ('SERVICEIT_BACKUP_OR_DIAGNOSTIC_DIR=' + $Temp) }
}
