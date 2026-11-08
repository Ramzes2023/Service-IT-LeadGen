# Owner-run coordinated GitHub cloud -> native VPS core cutover.
# Review before running. PREVIEW makes NO GitHub variable or CRM changes.
# On failure AFTER GitHub fence goes YES, do not blindly restore old writers.
[CmdletBinding()]
param(
    [ValidateSet('PREVIEW','ACTIVATE')][string]$Mode='PREVIEW',
    [string]$SourceHosts='hh.ru,b2b-center.ru,zakupki.gov.ru,bidzaar.com,trudvsem.ru,superjob.ru,rabota.ru',
    [string]$CompanyHosts=''
)
$ErrorActionPreference='Stop'
$Repo='Ramzes2023/Service-IT-LeadGen'
$Server='serviceitdeploy@80.66.79.156'
$Key=Join-Path $env:USERPROFILE '.ssh\serviceit-vps-actions'
$Approval='ACTIVATE_NATIVE_CORE_1062_21_OLGA605_V1'
$LegacyFiles=@(
  'serviceit-bitrix-all-new-batch-ai.yml',
  'serviceit-bitrix-new-card-ai.yml',
  'serviceit-free-cloud-ai-cards.yml',
  'serviceit-crm-duplicate-auto.yml',
  'serviceit-finance-hybrid-live.yml',
  'serviceit-fns-msp-crm-result.yml',
  'serviceit-kanban-three-signal-title-auto.yml',
  'serviceit-leadgen-free-msp-internal-score.yml',
  'serviceit-own-signal-stop-auto.yml',
  'serviceit-postcall-manager-approved-auto.yml',
  'serviceit-stage3-all-new-task-automation.yml',
  'serviceit-stage4-all-new-signed-score-writer.yml',
  'serviceit-stage4-prelim-score-live.yml',
  'serviceit-bitrix-poll-fallback.yml'
)
function Assert-Exit([string]$Step) {
  if ($LASTEXITCODE -ne 0) { throw "$Step FAILED; do not override the GitHub fence" }
}
function Check-PublicHosts([string]$List, [bool]$Required) {
  $names=@($List.Split(',') | ForEach-Object { $_.Trim().ToLowerInvariant() } | Where-Object { $_ -ne '' })
  if (($Required -and $names.Count -eq 0) -or $names.Count -gt 20) {
    throw 'PUBLIC_SOURCE_HOST_APPROVALS_MISSING_OR_EXCESSIVE'
  }
  foreach($hostName in $names) {
    if ($hostName -notmatch '^(?:[a-z0-9-]{1,63}\.)+[a-z]{2,20}$' -or $hostName -match '\.(test|invalid|local|localhost)$') {
      throw 'PUBLIC_SOURCE_HOST_INVALID'
    }
  }
  return ($names | Select-Object -Unique) -join ','
}
function GitHub-ActiveLegacyRuns {
  $result=@()
  foreach($state in @('in_progress','queued','waiting','requested')) {
    $Raw= & gh api "repos/$Repo/actions/runs?status=$state&per_page=100"
    Assert-Exit "GITHUB_WORKFLOW_LIST_$state"
    $Data=$Raw | ConvertFrom-Json
    foreach($job in $Data.workflow_runs) {
      $Leaf=(($job.path -split '@')[0] -split '/')[-1]
      if($LegacyFiles -contains $Leaf) {
        $result += [pscustomobject]@{Workflow=$Leaf;State=$state}
      }
    }
  }
  return @($result)
}
function Shadow-DeployNewest([string]$CurrentSha) {
  Write-Host 'Refreshing latest approved GitHub main on VPS in READONLY...'
  $Created= & gh workflow run serviceit-vps-native-shadow-deploy.yml --repo $Repo -f 'approval=SHADOW_READONLY_ONLY'
  Assert-Exit 'READONLY_SHADOW_DISPATCH'
  $match=[regex]::Match(($Created -join [Environment]::NewLine),'actions/runs/(\d+)')
  if(-not $match.Success) { throw 'READONLY_SHADOW_RUN_ID_MISSING' }
  $Id=$match.Groups[1].Value
  & gh run watch $Id --repo $Repo --exit-status
  Assert-Exit 'READONLY_SHADOW_DEPLOY'
  $ResolvedSha= & gh run view $Id --repo $Repo --json headSha --jq '.headSha'
  Assert-Exit 'READONLY_SHADOW_RELEASE_SHA'
  if(($ResolvedSha -join [Environment]::NewLine).Trim() -ne $CurrentSha) {
    throw 'SHADOW_RELEASE_SHA_CHANGED_DURING_DEPLOY'
  }
}
function Remote-Cutover([string]$Arguments) {
  & ssh -T -i $Key -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=15 -o ConnectionAttempts=2 $Server $Arguments
  Assert-Exit 'VPS_REMOTE_COMMAND'
}
if(-not (Test-Path -LiteralPath $Key -PathType Leaf)) { throw 'VPS_SSH_KEY_MISSING' }
$SourceHosts=Check-PublicHosts $SourceHosts $true
$CompanyHosts=Check-PublicHosts $CompanyHosts $false
$Sha=(& gh api "repos/$Repo/branches/main" --jq '.commit.sha')
Assert-Exit 'GITHUB_MAIN_SHA'
$Sha=($Sha -join [Environment]::NewLine).Trim()
if($Sha -notmatch '^[a-f0-9]{40}$') { throw 'GITHUB_MAIN_SHA_INVALID' }
$Vars= & gh variable list --repo $Repo --json name,value | ConvertFrom-Json
Assert-Exit 'GITHUB_REPO_VARIABLE_LIST'
$Active=@($Vars | Where-Object {$_.name -eq 'SERVICEIT_VPS_CUTOVER_ACTIVE'})
if($Active.Count -gt 1) { throw 'CUTOVER_VAR_DUPLICATED' }
if($Active.Count -eq 1 -and $Active[0].value -eq 'YES') {
  throw 'GH_CUTOVER_ALREADY_ACTIVE; MANUAL REVIEW REQUIRED'
}

# Install latest safely reviewed main as READONLY before any live operation.
Shadow-DeployNewest $Sha
$RemoteScript="serviceit/releases/$Sha/deploy/serviceit-vps-live-core-cutover.py"
$Preflight="python3 $RemoteScript --mode preflight --sha $Sha --source-hosts '$SourceHosts' --company-hosts '$CompanyHosts'"
Remote-Cutover $Preflight
$before=@(GitHub-ActiveLegacyRuns)
if($before.Count -gt 0) {
  Write-Host ('LEGACY_WRITERS_ACTIVE=' + $before.Count)
  throw 'CLOUD_WRITER_NOT_DRAINED_BEFORE_FENCE; nothing activated'
}
if($Mode -eq 'PREVIEW') {
  Write-Host 'VPS_CORE_CUTOVER_PREVIEW=PASS'
  Write-Host 'WRITER_MODE=READONLY;GH_CUTOVER_NOT_SET;TASKS_CREATED=0'
  return
}

# Exact cutover boundary before disabling old cloud writers, so source HOLD
# cards created during the handoff are eligible for future native polling.
$Utc=(Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
& gh variable set SERVICEIT_VPS_CUTOVER_ACTIVE --repo $Repo --body YES
Assert-Exit 'BLOCK_GITHUB_CLOUD_WRITERS'
$Confirmed=(& gh variable get SERVICEIT_VPS_CUTOVER_ACTIVE --repo $Repo)
Assert-Exit 'VERIFY_GITHUB_CLOUD_FENCE'
if(($Confirmed -join [Environment]::NewLine).Trim() -ne 'YES') {
  throw 'GITHUB_CLOUD_FENCE_NOT_CONFIRMED'
}
Write-Host 'GH_OLD_CRM_WRITERS_NEW_JOBS=FENCED'
for($attempt=1;$attempt -le 3;$attempt++) {
  $remaining=@(GitHub-ActiveLegacyRuns)
  if($remaining.Count -eq 0) { break }
  Start-Sleep -Seconds 5
}
if($remaining.Count -ne 0) {
  throw 'GH_CLOUD_WRITERS_STILL_RUNNING; KEEP FENCE YES; DO NOT START LIVE'
}
$RunCommand="python3 $RemoteScript --mode activate --sha $Sha " +
  "--source-hosts '$SourceHosts' --company-hosts '$CompanyHosts' " +
  "--approval $Approval --cloud-writers-fenced YES --utc $Utc"
try {
  Remote-Cutover $RunCommand
} catch {
  Write-Host 'CUTOVER_FAILED: old GitHub CRM writers remain fenced.'
  Write-Host 'Do NOT set SERVICEIT_VPS_CUTOVER_ACTIVE to NO without receipt review.'
  throw
}
Write-Host 'CORE_BITRIX_VPS_LIVE_CUTOVER=COMPLETE'
Write-Host 'SOURCES_UNCHANGED;OLGA605_TASKS=ON;PERSONAL_STOP=EMPTY'
Write-Host 'BROWSER=OFF;FINANCE=OFF;POSTCALL=OFF;FNS_MSP_SCAN=OFF'
