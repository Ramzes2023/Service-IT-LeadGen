# Resume an ALREADY COMPLETED Codex execution after missing local Python deps.
# Existing worktree is preserved; this script never invokes Codex again.
# No live Bitrix, model calls, deployments, or automatic PR merge.
[CmdletBinding()]
param(
    [ValidatePattern('^[0-9]{8}-[0-9]{6}$')]
    [string]$RunStamp = '20261103-162103'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$RepositoryName = 'Ramzes2023/Service-IT-LeadGen'
$Repo = Join-Path $HOME 'Service_IT_GitHub_Repo\Service-IT-LeadGen'
$RunDir = Join-Path $env:LOCALAPPDATA ('ServiceIT\Bitrix-AI-Codex\' + $RunStamp)
$Worktree = Join-Path $RunDir 'repo'
$Report = Join-Path $Worktree 'codex-final-report.txt'
$CodexReport = Join-Path $RunDir 'codex-final-report.txt'
$Python = Join-Path $Repo '.venv\Scripts\python.exe'

function Require-ServiceIT([bool]$Condition, [string]$Failure) {
    if (-not $Condition) { throw $Failure }
}
function Invoke-ServiceITNative([string]$Executable, [string[]]$Arguments, [string]$Failure) {
    # PS5.1 promotes native stderr to terminating NativeCommandError with Stop.
    # Evaluate LASTEXITCODE, not warnings. Never silence a real failed command.
    $Saved = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Executable @Arguments
        $Exit = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $Saved }
    Require-ServiceIT ($Exit -eq 0) $Failure
}

Require-ServiceIT (Test-Path -LiteralPath (Join-Path $Repo '.git') -PathType Container) 'SERVICEIT_MAIN_REPOSITORY_MISSING'
Require-ServiceIT (Test-Path -LiteralPath (Join-Path $Worktree '.git') -PathType Leaf) 'SERVICEIT_CODEX_WORKTREE_MISSING'
Require-ServiceIT (Test-Path -LiteralPath $CodexReport -PathType Leaf) 'SERVICEIT_SUCCESSFUL_CODEX_REPORT_MISSING'
Require-ServiceIT ((Get-Item -LiteralPath $CodexReport).Length -gt 0) 'SERVICEIT_CODEX_REPORT_EMPTY'
Require-ServiceIT (Test-Path -LiteralPath $Python -PathType Leaf) 'SERVICEIT_EXISTING_VENV_PYTHON_MISSING'
$Remote = (& git -C $Repo remote get-url origin | Out-String).Trim()
Require-ServiceIT ($LASTEXITCODE -eq 0 -and $Remote -match 'Ramzes2023/Service-IT-LeadGen(?:\.git)?$') 'SERVICEIT_GITHUB_REMOTE_NOT_APPROVED'
$Top = (& git -C $Worktree rev-parse --show-toplevel | Out-String).Trim()
Require-ServiceIT ($LASTEXITCODE -eq 0 -and
                   [IO.Path]::GetFullPath($Top).TrimEnd('\') -ieq
                   [IO.Path]::GetFullPath($Worktree).TrimEnd('\')) 'SERVICEIT_WORKTREE_IDENTITY_INVALID'
$Branch = (& git -C $Worktree branch --show-current | Out-String).Trim()
Require-ServiceIT ($LASTEXITCODE -eq 0 -and
                   $Branch -match '^codex/serviceit-bitrix-ai-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$') 'SERVICEIT_UNEXPECTED_WORKTREE_BRANCH'
Write-Host ('SERVICEIT_RESUME_EXISTING_WORKTREE=' + $Worktree)
Write-Host 'SERVICEIT_CODEX_RERUN=NO'
Write-Host 'SERVICEIT_REAL_CRM_WRITES=0'
Write-Host 'SERVICEIT_AUTOMATIC_DEPLOY=NO'

Push-Location $Worktree
try {
    # The Codex sandbox has deliberately restricted outbound networking.
    # Perform package bootstrap in the owner's existing local virtualenv.
    Invoke-ServiceITNative $Python @('-m', 'pip', 'install', '--disable-pip-version-check',
        '-r', (Join-Path $Worktree 'requirements.txt')) 'SERVICEIT_PIP_REQUIREMENTS_FAILED_NO_PR'
    Invoke-ServiceITNative $Python @('-c', 'import cryptography, pytest, fastapi, httpx, defusedxml') 'SERVICEIT_PYTHON_DEPS_STILL_MISSING'
    Invoke-ServiceITNative $Python @('-m', 'pip', 'check') 'SERVICEIT_PYTHON_DEPENDENCY_CONFLICT'
    Write-Host 'SERVICEIT_PYTHON_DEPENDENCIES=PASS'
    Invoke-ServiceITNative $Python @('-m', 'compileall', '-q', 'leadpilot') 'SERVICEIT_COMPILE_FAILED'
    # Socket-denied offline full repository tests; do not contact production.
    Invoke-ServiceITNative $Python @('scripts/serviceit_offline_pytest.py') 'SERVICEIT_OFFLINE_FULL_TESTS_FAILED_NO_PR'
    Write-Host 'SERVICEIT_OFFLINE_FULL_TESTS=PASS'
    . (Join-Path $Repo 'scripts\ENSURE_SERVICEIT_NODE_LTS.ps1')
    $Node = Ensure-ServiceITNode
    Require-ServiceIT ($null -ne $Node) 'SERVICEIT_NODE_LTS_MISSING'
    Invoke-ServiceITNative $Node.Source @('integrations/bitrix_event_bridge/tests/test_bridge.cjs') 'SERVICEIT_BITRIX_JS_TESTS_FAILED'
    Write-Host 'SERVICEIT_BITRIX_JS_TESTS=PASS'

    # Check only the changed PowerShell files, without executing them.
    $Changed = @(& git diff --name-only --diff-filter=ACMR HEAD)
    Require-ServiceIT ($LASTEXITCODE -eq 0) 'SERVICEIT_DIFF_LIST_UNVERIFIED'
    foreach ($File in $Changed) {
        if ($File -notmatch '\.ps1$') { continue }
        $FullPath = Join-Path $Worktree $File
        $Tokens = $null
        $Errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile(
            $FullPath, [ref]$Tokens, [ref]$Errors) | Out-Null
        Require-ServiceIT ($Errors.Count -eq 0) 'SERVICEIT_PS51_PARSE_FAILED_NO_PR'
    }
    Invoke-ServiceITNative 'git' @('diff', '--check') 'SERVICEIT_WORKTREE_DIFF_CHECK_FAILED'

    # No secret-like file may be introduced by the execution.
    $Status = @(& git status --porcelain)
    Require-ServiceIT ($LASTEXITCODE -eq 0) 'SERVICEIT_STATUS_UNVERIFIED'
    foreach ($Entry in $Status) {
        Require-ServiceIT ($Entry -notmatch '(?i)(?:\.env|secrets\.gs|\.dpapi|\.pem|\.p12|\.key|credentials\.json)') 'SERVICEIT_SECRET_LIKE_CHANGE_REFUSED'
    }
    # Only the reviewed code/document/test paths may be committed.
    Invoke-ServiceITNative 'git' @('add', '--', '.github/workflows', 'leadpilot',
        'tests', 'scripts', 'docs', 'integrations') 'SERVICEIT_STAGE_FAILED'
    Invoke-ServiceITNative 'git' @('diff', '--cached', '--check') 'SERVICEIT_STAGED_DIFF_INVALID'
    $Unstaged = @(& git status --porcelain | Where-Object { $_ -match '^(\?\?|.[MADRCU])' })
    Require-ServiceIT ($Unstaged.Count -eq 0) 'SERVICEIT_NON_ALLOWLISTED_OR_UNSTAGED_CHANGE_REVIEW'
    $StagedFiles = @(& git diff --cached --name-only)
    Require-ServiceIT ($LASTEXITCODE -eq 0) 'SERVICEIT_STAGED_FILE_LIST_UNVERIFIED'
    foreach ($File in $StagedFiles) {
        Require-ServiceIT ($File -match '^(leadpilot/|tests/|scripts/|docs/|integrations/|\.github/workflows/)') 'SERVICEIT_STAGED_PATH_NOT_ALLOWED'
        Require-ServiceIT ($File -notmatch '(?i)(?:\.env|secrets\.gs|\.dpapi|\.pem|\.p12|\.key|credentials\.json)') 'SERVICEIT_SECRET_LIKE_FILE_STAGED'
    }
    if ($StagedFiles.Count -gt 0) {
        Invoke-ServiceITNative 'git' @('commit', '-m',
            'fix(serviceit): finish universal Bitrix new-card AI safety and manager handoff') 'SERVICEIT_CODEX_COMMIT_FAILED'
    }
    $Ahead = (& git rev-list --count ('origin/main..' + $Branch) | Out-String).Trim()
    Require-ServiceIT ($LASTEXITCODE -eq 0 -and $Ahead -match '^[0-9]+$') 'SERVICEIT_AHEAD_COUNT_UNVERIFIED'
    if ([int]$Ahead -eq 0) {
        Write-Host 'SERVICEIT_RESUME=NO_NEW_CODE_TO_PROPOSE'
        return
    }
    $Gh = Get-Command gh.exe -ErrorAction SilentlyContinue
    Require-ServiceIT ($null -ne $Gh) 'SERVICEIT_GH_CLI_REQUIRED_TO_OPEN_PR'
    Invoke-ServiceITNative 'git' @('push', '-u', 'origin', $Branch) 'SERVICEIT_GITHUB_BRANCH_PUSH_FAILED'
    $Existing = (& $Gh.Source pr view $Branch --repo $RepositoryName --json number --jq '.number' 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -eq 0 -and $Existing -match '^[0-9]+$') {
        Write-Host ('SERVICEIT_EXISTING_PR=' + $Existing)
    }
    else {
        $Body = 'Previously completed Headless Codex result, resumed from its original worktree after fixing missing cryptography in local virtualenv. Full socket-denied Python tests, Node bridge tests, PowerShell parsing and git diff checks passed locally. No real CRM/model calls, live rollout or secrets. REVIEW REQUIRED BEFORE MERGE OR ACTIVATION.'
        Invoke-ServiceITNative $Gh.Source @('pr', 'create', '--repo', $RepositoryName,
            '--base', 'main', '--head', $Branch,
            '--title', 'fix(serviceit): universal Bitrix new-card AI safety and manager handoff',
            '--body', $Body) 'SERVICEIT_CREATE_PR_FAILED'
    }
    Write-Host 'SERVICEIT_CODEX_RESUME=PR_CREATED_OR_EXISTS_WAITING_CI_AND_REVIEW'
    Write-Host 'SERVICEIT_BITRIX_LIVE=NOT_ACTIVATED'
}
finally { Pop-Location }
