# ServiceIT: owner-run Headless Codex CLI Execution via PowerShell stdin piping.
# Strictly BITRIX24 smart process 1062/category 21. NO source integrations.
# Never read DPAPI credentials from Codex's sandbox; install them separately.
[CmdletBinding()]
param([switch]$Deploy, [switch]$NoPullRequest)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Repo = Join-Path $HOME 'Service_IT_GitHub_Repo\Service-IT-LeadGen'
if (-not (Test-Path (Join-Path $Repo '.git') -PathType Container)) {
    throw 'SERVICEIT_REPO_NOT_FOUND'
}
$Remote = (& git -C $Repo remote get-url origin | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $Remote -notmatch 'Ramzes2023/Service-IT-LeadGen(?:\.git)?$') {
    throw 'SERVICEIT_WRONG_REMOTE'
}
$Codex = $null
foreach ($Name in @('codex.cmd','codex.exe','codex')) {
    $Found = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($Found) { $Codex = $Found.Source; break }
}
if (-not $Codex) {
    foreach ($Base in @(
        (Join-Path $env:LOCALAPPDATA 'Programs\OpenAI\Codex\bin'),
        (Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\bin')
    )) {
        if (Test-Path $Base) {
            $Match = Get-ChildItem $Base -Filter codex.exe -Recurse -File -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending | Select-Object -First 1
            if ($Match) { $Codex = $Match.FullName; break }
        }
    }
}
if (-not $Codex) { throw 'CODEX_CLI_NOT_FOUND' }
& $Codex --version
if ($LASTEXITCODE -ne 0) { throw 'CODEX_CLI_NOT_AUTHENTICATED_OR_UNAVAILABLE' }

$GhCommand = Get-Command gh -ErrorAction SilentlyContinue
$Gh = if ($GhCommand) { $GhCommand.Source } else { '' }
if (-not $NoPullRequest -and -not $Gh) { throw 'GITHUB_CLI_REQUIRED_FOR_PR' }
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path $Python -PathType Leaf)) {
    $PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $PythonCommand) { throw 'PYTHON_NOT_FOUND' }
    $Python = $PythonCommand.Source
}
# Check before creating another Codex worktree: local model development may
# finish successfully even while its sandbox cannot install Python packages.
# Install required dependencies in the owner's isolated venv, NEVER in Codex.
$PreviousErrorActionPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    & $Python -c 'import pytest, cryptography, fastapi, httpx, defusedxml' 2>$null
    $DepsAvailable = ($LASTEXITCODE -eq 0)
}
finally { $ErrorActionPreference = $PreviousErrorActionPreference }
if (-not $DepsAvailable) {
    Write-Host 'SERVICEIT_PYTHON_DEPENDENCIES=INSTALLING_FROM_REQUIREMENTS'
    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Python -m pip install --disable-pip-version-check -r (Join-Path $Repo 'requirements.txt')
        $PipStatus = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $PreviousErrorActionPreference }
    if ($PipStatus -ne 0) { throw 'SERVICEIT_PYTHON_REQUIREMENTS_INSTALL_FAILED_NO_CODEX_RUN' }
    & $Python -c 'import pytest, cryptography, fastapi, httpx, defusedxml'
    if ($LASTEXITCODE -ne 0) { throw 'SERVICEIT_PYTHON_DEPENDENCY_IMPORT_FAILED_NO_CODEX_RUN' }
}
Write-Host 'SERVICEIT_PYTHON_DEPENDENCIES=READY'

. (Join-Path $PSScriptRoot 'ENSURE_SERVICEIT_NODE_LTS.ps1')
$Node = Ensure-ServiceITNode
if (-not $Node) { throw 'SERVICEIT_NODE_BOOTSTRAP_DID_NOT_RETURN_NODE' }

$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $OutputEncoding
$Stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss')
$Branch = 'codex/serviceit-bitrix-ai-' + $Stamp + '-' + [guid]::NewGuid().ToString('N').Substring(0,6)
$RunDir = Join-Path $env:LOCALAPPDATA ('ServiceIT\Bitrix-AI-Codex\' + $Stamp)
$Worktree = Join-Path $RunDir 'repo'
$Report = Join-Path $RunDir 'codex-final-report.txt'
$CodexLog = Join-Path $RunDir 'codex.log'
$TestsLog = Join-Path $RunDir 'tests.log'
New-Item -ItemType Directory -Path $RunDir -Force | Out-Null

& git -C $Repo fetch origin main
if ($LASTEXITCODE -ne 0) { throw 'GIT_FETCH_FAILED' }
& git -C $Repo worktree add -b $Branch $Worktree origin/main
if ($LASTEXITCODE -ne 0) { throw 'GIT_WORKTREE_FAILED' }
$PromptPath = Join-Path $Worktree 'scripts\SERVICEIT_BITRIX_AI_CODEX_PROMPT.txt'
if (-not (Test-Path $PromptPath -PathType Leaf)) { throw 'HEADLESS_PROMPT_MISSING' }
$Instructions = Get-Content -LiteralPath $PromptPath -Raw -Encoding UTF8
$CodexHelp = (& $Codex --help 2>&1 | Out-String)
$Prefix = @()
if ($CodexHelp.Contains('--ask-for-approval')) {
    $Prefix = @('--ask-for-approval', 'never')
} elseif ($CodexHelp.Contains('--approve-for-me')) {
    $Prefix = @('--approve-for-me')
}
$Args = $Prefix + @('exec', '-C', $Worktree, '--sandbox', 'workspace-write',
                   '--output-last-message', $Report, '-')
Write-Host 'SERVICEIT_CODEX_METHOD=HEADLESS_STDIN'
Write-Host ('SERVICEIT_WORKTREE=' + $Worktree)
Write-Host 'BITRIX_SCOPE=1062_21_ALL_NEW_CARDS'
Write-Host 'CODEX_REAL_CRM_WRITES=FORBIDDEN'
# Windows PowerShell 5.1 promotes native stderr to NativeCommandError when
# ErrorActionPreference=Stop. Codex can print a non-fatal model-catalog
# refresh timeout and still complete successfully. Capture the REAL exit
# status; do not treat a diagnostic stderr line as a deployment success/fail.
$CodexExit = $null
for ($Attempt = 1; $Attempt -le 2; $Attempt++) {
    Write-Host ('SERVICEIT_CODEX_ATTEMPT=' + $Attempt)
    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $Instructions | & $Codex @Args 2>&1 |
            ForEach-Object { $_.ToString() } |
            Tee-Object -FilePath $CodexLog -Append
        $CodexExit = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    if ($CodexExit -eq 0) { break }

    # A network timeout justifies ONE retry only if the isolated worktree
    # is still pristine. Never blindly rerun modified code or a CRM call.
    $ModifiedBeforeRetry = @(& git -C $Worktree status --porcelain)
    if ($LASTEXITCODE -ne 0) { throw 'CODEX_WORKTREE_RETRY_STATE_UNVERIFIED' }
    $TimedOut = (Test-Path -LiteralPath $CodexLog -PathType Leaf) -and
        (Select-String -LiteralPath $CodexLog -SimpleMatch -Pattern 'failed to refresh available models: request timed out' -Quiet)
    if ($Attempt -ge 2 -or $ModifiedBeforeRetry.Count -ne 0 -or -not $TimedOut) {
        Write-Host ('CODEX_LOG=' + $CodexLog)
        Write-Host ('CODEX_EXIT_CODE=' + $CodexExit)
        throw 'CODEX_HEADLESS_NONZERO_REVIEW_REQUIRED'
    }
    Write-Host 'SERVICEIT_CODEX_NETWORK_TIMEOUT=RETRY_ONCE_PRISTINE_WORKTREE'
    Start-Sleep -Seconds 5
}
if ($CodexExit -ne 0) { throw 'CODEX_HEADLESS_NONZERO' }
if (-not (Test-Path -LiteralPath $Report -PathType Leaf) -or
    (Get-Item -LiteralPath $Report).Length -eq 0) {
    Write-Host ('CODEX_LOG=' + $CodexLog)
    throw 'CODEX_FINAL_REPORT_MISSING_DEPLOY_BLOCKED'
}
Write-Host 'SERVICEIT_CODEX_EXIT=0_FINAL_REPORT_PRESENT'

Push-Location $Worktree
try {
    & $Python -m compileall -q leadpilot
    if ($LASTEXITCODE -ne 0) { throw 'BITRIX_PYTHON_COMPILE_FAILED' }
    $TestFiles = @(
        'tests/test_serviceit_bitrix_dispatch_worker.py',
        'tests/test_serviceit_bitrix_event_autofill.py',
        'tests/test_serviceit_preliminary_gigachat.py',
        'tests/test_serviceit_windows_autofill.py'
    )
    & $Python -m pytest -q @TestFiles 2>&1 | Tee-Object -FilePath $TestsLog
    if ($LASTEXITCODE -ne 0) { throw 'BITRIX_LOCAL_PYTEST_FAILED' }
    & $Node.Source 'integrations/bitrix_event_bridge/tests/test_bridge.cjs'
    if ($LASTEXITCODE -ne 0) { throw 'BITRIX_BRIDGE_NODE_TESTS_FAILED' }
    & git diff --check
    if ($LASTEXITCODE -ne 0) { throw 'GIT_DIFF_CHECK_FAILED' }
    $InvalidFiles = @(& git status --porcelain | Where-Object {
        $_ -match '(?i)(?:\.env|secrets\.gs|\.dpapi|\.pem|\.p12|\.key|credentials\.json)'
    })
    if ($InvalidFiles.Count -gt 0) { throw 'SECRET_LIKE_FILES_REFUSED' }
    $Modified = @(& git status --porcelain)
    if ($Modified.Count -gt 0) {
        & git add -- .github/workflows leadpilot tests scripts docs integrations
        if ($LASTEXITCODE -ne 0) { throw 'GIT_ADD_FAILED' }
        & git diff --cached --check
        if ($LASTEXITCODE -ne 0) { throw 'GIT_STAGED_DIFF_INVALID' }
        & git commit -m 'fix(serviceit): finish Bitrix event AI manager integration'
        if ($LASTEXITCODE -ne 0) { throw 'GIT_COMMIT_FAILED' }
    }
    if (-not $NoPullRequest -and $Modified.Count -gt 0) {
        & git push -u origin $Branch
        if ($LASTEXITCODE -ne 0) { throw 'GITHUB_PUSH_FAILED' }
        & $Gh pr create --repo 'Ramzes2023/Service-IT-LeadGen' --base main --head $Branch --title 'fix(serviceit): complete Bitrix event AI and manager integration' --body 'Bitrix-only automatic event-to-GigaChat-to-manager workflow, with fenced writes, synthetic E2E tests and owner-approved installer. No production credentials or CRM writes in PR.'
        if ($LASTEXITCODE -ne 0) { throw 'GITHUB_PR_CREATE_FAILED' }
        Write-Host 'GITHUB_PR=CREATED_PENDING_CI'
        if ($Deploy) {
            & $Gh pr checks $Branch --repo 'Ramzes2023/Service-IT-LeadGen' --watch --fail-fast
            if ($LASTEXITCODE -ne 0) {
                throw 'GITHUB_PR_CI_NOT_GREEN_PRODUCTION_INSTALL_BLOCKED'
            }
            & $Gh pr merge $Branch --repo 'Ramzes2023/Service-IT-LeadGen' --squash
            if ($LASTEXITCODE -ne 0) {
                throw 'GITHUB_PR_MERGE_REQUIRES_REVIEW_PRODUCTION_INSTALL_BLOCKED'
            }
            Write-Host 'GITHUB_PR=MERGED_AFTER_CI'
        }
    } else {
        Write-Host 'GITHUB_PR=NO_NEW_CHANGES'
    }
    if ($Deploy) {
        if ($NoPullRequest -and $Modified.Count -gt 0) {
            throw 'UNMERGED_LOCAL_CHANGE_PRODUCTION_INSTALL_BLOCKED'
        }
        & git -C $Repo pull --ff-only origin main
        if ($LASTEXITCODE -ne 0) { throw 'GIT_MAIN_SYNC_FAILED_BEFORE_DEPLOY' }
        $Installer = Join-Path $Repo 'scripts\INSTALL_SERVICEIT_BITRIX_EVENT_BRIDGE.ps1'
        if (-not (Test-Path $Installer -PathType Leaf)) {
            throw 'BITRIX_EVENT_BRIDGE_INSTALLER_NOT_FOUND_IN_MAIN'
        }
        Write-Host 'INSTALLER_STATUS=STARTING_OWNER_APPROVAL_AND_BITRIX_WEBHOOK_SETUP'
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Installer -Apply
        if ($LASTEXITCODE -ne 0) {
            throw 'BITRIX_CLOUD_SETUP_BLOCKED_SEE_EXACT_OPERATOR_GATE'
        }
    }
}
finally { Pop-Location }
Write-Host ('SERVICEIT_CODEX_REPORT=' + $Report)
Write-Host ('SERVICEIT_TEST_LOG=' + $TestsLog)
Write-Host 'RESULT=CODE_VERIFIED_LOCALLY_LIVE_ACTIVATION_REQUIRES_AUTHORIZED_INSTALL'
