param(
    [switch]$ExistingRemote
)

# Windows PowerShell 5.1 compatible; changes only the Service IT project.
# No Python installation, no VALYQON changes, no credential upload.
$ErrorActionPreference = 'Continue'
Set-StrictMode -Version 2.0

$repoFull = 'Ramzes2023/Service-IT-LeadGen'
$expectedRemote = 'https://github.com/Ramzes2023/Service-IT-LeadGen.git'
$sshRemote = 'git@github.com:Ramzes2023/Service-IT-LeadGen.git'
$root = [System.IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\', '/')

if ($root -match '(?i)(TenderLens-AI|VALYQON)') {
    throw 'WRONG_PROJECT_FOLDER: VALYQON is protected'
}
Set-Location -LiteralPath $root -ErrorAction Stop
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'GIT_MISSING' }

if (-not (Test-Path -LiteralPath (Join-Path $root '.git'))) {
    & git init -b main
    if ($LASTEXITCODE -ne 0) { throw 'GIT_INIT_FAILED' }
}

$repoRoot = (& git rev-parse --show-toplevel)
if ($LASTEXITCODE -ne 0 -or -not $repoRoot) { throw 'GIT_ROOT_NOT_FOUND' }
$repoRoot = [System.IO.Path]::GetFullPath(([string]$repoRoot).Trim()).TrimEnd('\', '/')
if (-not [string]::Equals($repoRoot, $root, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'GIT_ROOT_MISMATCH: refusing to touch another repository'
}

# IMPORTANT: do not run 'git remote get-url origin' before testing its existence.
# In Windows PowerShell 5.1 that missing remote + stderr + Stop can terminate execution.
$remotes = @(& git remote)
if ($LASTEXITCODE -ne 0) { throw 'GIT_REMOTE_LIST_FAILED' }
$originExists = $remotes -contains 'origin'
if ($originExists) {
    $origin = (& git remote get-url origin)
    if ($LASTEXITCODE -ne 0) { throw 'ORIGIN_READ_FAILED' }
    $origin = ([string]$origin).Trim()
    if ($origin -ne $expectedRemote -and $origin -ne $sshRemote) {
        throw ('UNEXPECTED_ORIGIN: ' + $origin)
    }
}

# Explicit allowlist. Never stage local database, .env, API snapshots, or valyqon paths.
$allowed = @(
    '.gitignore', '.env.example', 'README.md', 'README_RU.md',
    'PLAN_TO_MONDAY.md', 'DELIVERY_STATUS.md', 'requirements.txt',
    'leadpilot', 'tests', 'samples', 'docs', '.github',
    'START_LAB.ps1', 'START_DEMO.ps1', 'RUN_AI_FROM_VALYQON.ps1',
    'SCAN_PUBLIC_VACANCIES.ps1', 'CRM_READONLY_CHECK.ps1',
    'SAFE_IMPORT_VERIFIED.ps1', 'AI_QUALITY_AUDIT.ps1',
    'PUBLISH_PRIVATE_GITHUB.ps1', 'SAVE_STEP.ps1', 'COPY_LOCAL_RESEARCH.ps1'
)
& git add -- $allowed
if ($LASTEXITCODE -ne 0) { throw 'SAFE_STAGE_FAILED' }
$staged = @(& git diff --cached --name-only)
if ($LASTEXITCODE -ne 0) { throw 'STAGED_FILE_LIST_FAILED' }
foreach ($f in $staged) {
    $normalized = ([string]$f).Replace('\','/')
    if ($normalized -match '(?i)(^|/)(\.env($|\.)|data/|exports/|secrets/|credentials/|local_config/|\.venv/)' -and
        $normalized -notmatch '(?i)(^|/)\.env\.example$') {
        throw ('SENSITIVE_STAGED_FILE: ' + $normalized)
    }
    if ($normalized -match '(?i)\.(sqlite3?|db|pfx|pem|key|jsonl)$') {
        throw ('BLOCKED_STAGED_FILE: ' + $normalized)
    }
}
Write-Host ('SAFE_FILES_STAGED=' + $staged.Count)
if ($staged.Count -gt 0) {
    & git commit -m 'chore: initialize isolated Service IT LeadGen MVP'
    if ($LASTEXITCODE -ne 0) { throw 'COMMIT_FAILED: check git user.name and user.email' }
    Write-Host 'LOCAL_COMMIT_SAVED=YES' -ForegroundColor Green
} else {
    Write-Host 'NOTHING_NEW_TO_COMMIT'
}

if (-not $originExists) {
    if (-not $ExistingRemote -and (Get-Command gh -ErrorAction SilentlyContinue)) {
        & gh repo create $repoFull --private --description 'Isolated Service IT LeadGen test MVP' --source . --remote origin --push
        if ($LASTEXITCODE -eq 0) {
            Write-Host ('REPO_CREATED_AND_PUSHED=https://github.com/' + $repoFull) -ForegroundColor Green
            exit 0
        }
        Write-Host 'GH_CREATE_FAILED: use https://github.com/new (PRIVATE; no README)' -ForegroundColor Yellow
    }
    if (-not $ExistingRemote) {
        Write-Host 'CREATE_ON_GITHUB=https://github.com/new' -ForegroundColor Yellow
        Write-Host 'Owner=Ramzes2023; Name=Service-IT-LeadGen; Visibility=PRIVATE; do not add README'
        Write-Host 'Then run: powershell -NoProfile -ExecutionPolicy Bypass -File .\PUBLISH_PRIVATE_GITHUB.ps1 -ExistingRemote'
        exit 2
    }
    # Before pushing to a manually created remote, ask the owner to confirm privacy.
    $confirm = Read-Host 'Confirm your new GitHub repository is PRIVATE. Type PRIVATE to push'
    if ($confirm -cne 'PRIVATE') { throw 'PRIVATE_CONFIRMATION_REQUIRED: no push occurred' }
    & git remote add origin $expectedRemote
    if ($LASTEXITCODE -ne 0) { throw 'ORIGIN_ADD_FAILED' }
}

& git branch -M main
if ($LASTEXITCODE -ne 0) { throw 'BRANCH_FAILED' }
& git push -u origin main
if ($LASTEXITCODE -ne 0) { throw 'PUSH_FAILED: check GitHub authentication, repository existence and access' }
Write-Host ('GITHUB_PUSHED=https://github.com/' + $repoFull) -ForegroundColor Green
