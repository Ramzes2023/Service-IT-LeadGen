param(
    [Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$Message,
    [switch]$NoPush
)
$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath($PSScriptRoot)
if ($root -match '(?i)(TenderLens-AI|VALYQON)') { throw 'WRONG_PROJECT_FOLDER' }
Set-Location -LiteralPath $root
if (-not (Test-Path .git)) { throw 'NO_GIT_REPO: run PUBLISH_PRIVATE_GITHUB.ps1 first' }
$repoRoot = (& git rev-parse --show-toplevel).Trim()
if (-not [string]::Equals([System.IO.Path]::GetFullPath($repoRoot).TrimEnd('\','/'),$root.TrimEnd('\','/'),[StringComparison]::OrdinalIgnoreCase)) { throw 'GIT_ROOT_MISMATCH' }
$remote = (& git remote get-url origin 2>$null)
if ($LASTEXITCODE -ne 0 -or $remote.Trim() -notin @('https://github.com/Ramzes2023/Service-IT-LeadGen.git','git@github.com:Ramzes2023/Service-IT-LeadGen.git')) { throw 'UNEXPECTED_REMOTE: cannot push outside Service IT' }
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw 'VENV_NOT_FOUND: first run START_LAB.ps1' }
& $py -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'TESTS_FAILED: commit/push cancelled' }
# Only project source and documentation are staged. No git add -A.
& git add -u
& git add -- README.md README_RU.md requirements.txt .gitignore .env.example leadpilot tests samples docs .github *.ps1
if ($LASTEXITCODE -ne 0) { throw 'GIT_ADD_FAILED' }
$staged = @(& git diff --cached --name-only)
foreach ($f in $staged) {
    if ($f -match '(?i)(^|/)(\.env($|\.)|data/|exports/|secrets/|credentials/|local_config/|\.venv/)' -and $f -notmatch '\.env\.example$') { throw ('SENSITIVE_STAGED_FILE: '+$f) }
    if ($f -match '(?i)\.(sqlite3?|db|pfx|pem|key|jsonl)$') { throw ('BLOCKED_STAGED_FILE: '+$f) }
}
if ($staged.Count -eq 0) { Write-Host 'NO_CODE_CHANGES_TO_SAVE'; exit 0 }
& git commit -m $Message
if ($LASTEXITCODE -ne 0) { throw 'COMMIT_FAILED' }
Write-Host 'LOCAL_COMMIT_SAVED=YES' -ForegroundColor Green
if (-not $NoPush) {
    & git push origin main
    if ($LASTEXITCODE -ne 0) { throw 'GITHUB_PUSH_FAILED: local commit is preserved' }
    Write-Host 'GITHUB_STEP_SAVED=YES' -ForegroundColor Green
}
