# Publish completed Bitrix AI Codex worktree as a DRAFT PR for review.
# Use only when the existing completed worktree fails a code contract test.
# Never rerun Codex, alter main, merge a PR or connect to corporate CRM.
[CmdletBinding()]
param(
    [ValidatePattern('^[0-9]{8}-[0-9]{6}$')]
    [string]$RunStamp = '20261103-162103'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$RepoName = 'Ramzes2023/Service-IT-LeadGen'
$Repo = Join-Path $HOME 'Service_IT_GitHub_Repo\Service-IT-LeadGen'
$Worktree = Join-Path $env:LOCALAPPDATA ('ServiceIT\Bitrix-AI-Codex\' + $RunStamp + '\repo')
$Report = Join-Path $env:LOCALAPPDATA ('ServiceIT\Bitrix-AI-Codex\' + $RunStamp + '\codex-final-report.txt')
function Guard([bool]$Condition,[string]$Code) {
    if (-not $Condition) { throw $Code }
}
function SafeNative([string]$File,[string[]]$Arguments,[string]$Failure) {
    $Before = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $File @Arguments
        $Status = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $Before }
    Guard ($Status -eq 0) $Failure
}
Guard (Test-Path -LiteralPath (Join-Path $Repo '.git') -PathType Container) 'SERVICEIT_MAIN_REPO_MISSING'
Guard (Test-Path -LiteralPath (Join-Path $Worktree '.git') -PathType Leaf) 'SERVICEIT_CODEX_WORKTREE_MISSING'
Guard (Test-Path -LiteralPath $Report -PathType Leaf) 'SERVICEIT_CODEX_COMPLETED_REPORT_MISSING'
Guard ((Get-Item -LiteralPath $Report).Length -gt 0) 'SERVICEIT_CODEX_COMPLETED_REPORT_EMPTY'
$Remote = (& git -C $Repo remote get-url origin | Out-String).Trim()
Guard ($LASTEXITCODE -eq 0 -and $Remote -match 'Ramzes2023/Service-IT-LeadGen(?:\.git)?$') 'SERVICEIT_GITHUB_REPOSITORY_MISMATCH'
$RemoteWorktree = (& git -C $Worktree remote get-url origin | Out-String).Trim()
Guard ($LASTEXITCODE -eq 0 -and $RemoteWorktree -ceq $Remote) 'SERVICEIT_WORKTREE_REMOTE_MISMATCH'
$Branch = (& git -C $Worktree branch --show-current | Out-String).Trim()
Guard ($LASTEXITCODE -eq 0 -and $Branch -match '^codex/serviceit-bitrix-ai-[0-9]{8}-[0-9]{6}-[a-f0-9]{6}$') 'SERVICEIT_CODEX_BRANCH_NOT_APPROVED'
$Top = (& git -C $Worktree rev-parse --show-toplevel | Out-String).Trim()
Guard ($LASTEXITCODE -eq 0 -and [IO.Path]::GetFullPath($Top).TrimEnd('\') -ieq [IO.Path]::GetFullPath($Worktree).TrimEnd('\')) 'SERVICEIT_WORKTREE_PATH_MISMATCH'
$Gh = Get-Command gh.exe -ErrorAction SilentlyContinue
Guard ($null -ne $Gh) 'SERVICEIT_GITHUB_CLI_MISSING'
SafeNative $Gh.Source @('auth', 'status') 'SERVICEIT_GITHUB_NOT_LOGGED_IN'
Write-Host 'SERVICEIT_CODEX_REUSE=PREVIOUS_FINISHED_WORKTREE'
Write-Host ('SERVICEIT_WORKTREE=' + $Worktree)
Write-Host 'SERVICEIT_DRAFT_REVIEW_ONLY=YES'
Write-Host 'SERVICEIT_REAL_CRM_WRITES=NO'
Write-Host 'SERVICEIT_CODEX_RERUN=NO'

# Allow only the dedicated source, docs, test and workflow paths.
$Allowed = '^(leadpilot/|tests/|scripts/|docs/|integrations/|\.github/workflows/)'
$Banned = '(?i)(?:^|/)(?:\.env(?:\.|$)|secrets\.gs$|\.clasp\.json$|credentials\.json$|id_[er]sa$|[^/]+\.(?:dpapi|pem|p12|pfx|key|sqlite3?|db|log)$)'
$Original = @(& git -C $Worktree status --porcelain --untracked-files=all)
Guard ($LASTEXITCODE -eq 0) 'SERVICEIT_STATUS_UNAVAILABLE'
foreach ($Entry in $Original) {
    $File = ($Entry -replace '^...\s*','').Replace('\','/')
    Guard ($File -match $Allowed) 'SERVICEIT_OUTSIDE_ALLOWED_PATH_NOT_PUBLISHED'
    Guard ($File -notmatch $Banned) 'SERVICEIT_SECRET_OR_RUNTIME_FILE_BLOCKED'
}
Guard ($Original.Count -gt 0) 'SERVICEIT_NO_UNPUBLISHED_CODE_FOUND'
SafeNative 'git' @('-C', $Worktree, 'diff', '--check') 'SERVICEIT_UNSTAGED_WHITESPACE_ERROR'
SafeNative 'git' @('-C', $Worktree, 'add', '--',
    '.github/workflows', 'leadpilot', 'tests', 'scripts', 'docs', 'integrations') 'SERVICEIT_STAGING_FAILED'
SafeNative 'git' @('-C', $Worktree, 'diff', '--cached', '--check') 'SERVICEIT_STAGED_WHITESPACE_ERROR'

$Names = @(& git -C $Worktree diff --cached --name-only --diff-filter=ACMR)
Guard ($LASTEXITCODE -eq 0) 'SERVICEIT_STAGED_FILES_UNVERIFIED'
Guard ($Names.Count -gt 0) 'SERVICEIT_STAGED_CHANGESET_EMPTY'
foreach ($Name in $Names) {
    $Normalized = $Name.Replace('\','/')
    Guard ($Normalized -match $Allowed) 'SERVICEIT_STAGED_UNAPPROVED_PATH'
    Guard ($Normalized -notmatch $Banned) 'SERVICEIT_STAGED_SECRET_PATH_BLOCKED'
}
$StagedDiff = (& git -C $Worktree diff --cached --no-ext-diff --unified=0 | Out-String)
Guard ($LASTEXITCODE -eq 0) 'SERVICEIT_STAGED_DIFF_UNAVAILABLE'
$Sensitive = '(?i)(?:github_pat_[A-Za-z0-9_]{50,}|gh[pousr]_[A-Za-z0-9]{30,}|AIza[A-Za-z0-9_-]{35}|sk-proj-[A-Za-z0-9_-]{20,})'
Guard ($StagedDiff -notmatch $Sensitive) 'SERVICEIT_SUSPECT_CREDENTIAL_BLOCKED'

# The owner will review the draft PR before any merge. This command only
# publishes the existing code for CI diagnostics, never runs it.
SafeNative 'git' @('-C', $Worktree, 'commit', '-m',
    'wip(serviceit): review finished Bitrix 1062/21 Codex changes') 'SERVICEIT_WIP_COMMIT_FAILED'
SafeNative 'git' @('-C', $Worktree, 'push', '-u', 'origin', $Branch) 'SERVICEIT_DRAFT_BRANCH_PUSH_FAILED'
$BeforeCheck = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    $Existing = (& $Gh.Source pr view $Branch --repo $RepoName --json number --jq '.number' 2>$null | Out-String).Trim()
    $ExistingStatus = $LASTEXITCODE
}
finally { $ErrorActionPreference = $BeforeCheck }
if ($ExistingStatus -eq 0 -and $Existing -match '^[0-9]+
    Write-Host ('SERVICEIT_EXISTING_REVIEW_PR=' + $Existing)
}
else {
    $Body = 'DRAFT / NOT FOR MERGE: completed Codex changes from owner worktree 20261103-162103, shared for review because local full offline acceptance has one failing installer canary contract. Original run reports 1518 passed, 1 failed, 1 skipped; cryptography installed in owner venv. Needs substantive audit of nine-field readback gate before any merge or live activation. Never run production webhooks or write CRM for this review.'
    SafeNative $Gh.Source @('pr', 'create', '--draft', '--repo', $RepoName, '--base', 'main',
        '--head', $Branch, '--title',
        'WIP(serviceit): inspect completed universal Bitrix AI worktree and canary gate',
        '--body', $Body) 'SERVICEIT_DRAFT_PR_CREATE_FAILED'
}
Write-Host 'SERVICEIT_CODEX_DRAFT_PR=READY_FOR_REVIEW_DO_NOT_MERGE'
Write-Host 'SERVICEIT_BITRIX_AUTOMATION_LIVE=NO'
) {
    Write-Host ('SERVICEIT_EXISTING_REVIEW_PR=' + $Existing)
}
else {
    $Body = 'DRAFT / NOT FOR MERGE: completed Codex changes from owner worktree 20261103-162103, shared for review because local full offline acceptance has one failing installer canary contract. Original run reports 1518 passed, 1 failed, 1 skipped; cryptography installed in owner venv. Needs substantive audit of nine-field readback gate before any merge or live activation. Never run production webhooks or write CRM for this review.'
    SafeNative $Gh.Source @('pr', 'create', '--draft', '--repo', $RepoName, '--base', 'main',
        '--head', $Branch, '--title',
        'WIP(serviceit): inspect completed universal Bitrix AI worktree and canary gate',
        '--body', $Body) 'SERVICEIT_DRAFT_PR_CREATE_FAILED'
}
Write-Host 'SERVICEIT_CODEX_DRAFT_PR=READY_FOR_REVIEW_DO_NOT_MERGE'
Write-Host 'SERVICEIT_BITRIX_AUTOMATION_LIVE=NO'
