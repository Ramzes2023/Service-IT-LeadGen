[CmdletBinding()]
param([switch]$Apply)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repository = 'Ramzes2023/Service-IT-LeadGen'
$Branch = 'main'
$ExpectedAccount = 'ramzesisayev444@gmail.com'
$Repo = Join-Path $HOME 'Service_IT_GitHub_Repo\Service-IT-LeadGen'
$Root = Join-Path $env:LOCALAPPDATA 'ServiceIT\AI-Autofill'
$BitrixFile = Join-Path $Root 'bitrix-rest.dpapi'
$GigaFile = Join-Path $Root 'gigachat.dpapi'
$ScopeFile = Join-Path $Root 'gigachat-scope.txt'
$LedgerFile = Join-Path $Root 'github-ledger-key.dpapi'
$HmacFile = Join-Path $Root 'b2b-ingest-hmac.dpapi'
$ProjectRoot = Join-Path $Root 'Gmail-B2B-V9'
$ProjectIdFile = Join-Path $ProjectRoot 'script-id.txt'
$ProjectName = 'Service IT Gmail B2B Automated Intake V9'

function FailIfExit([string]$Name) { if ($LASTEXITCODE -ne 0) { throw ($Name + '_FAILED') } }
function Read-Dpapi([string]$Path) {
    $s = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertTo-SecureString
    $p = [IntPtr]::Zero
    try { $p=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($p) }
    finally { if ($p -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p) }; $s.Dispose() }
}
function Write-Dpapi([string]$Path,[string]$Value) {
    $s=ConvertTo-SecureString -String $Value -AsPlainText -Force
    try { [IO.File]::WriteAllText($Path,(ConvertFrom-SecureString $s),[Text.UTF8Encoding]::new($false)) }
    finally { $s.Dispose() }
}
function New-Key([bool]$Pad=$false) {
    $b=New-Object byte[] 32; $rng=[Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($b) } finally { $rng.Dispose() }
    $v=[Convert]::ToBase64String($b).Replace('+','-').Replace('/','_')
    if (-not $Pad) { $v=$v.TrimEnd('=') }; return $v
}
function Set-GhSecret([string]$Name,[string]$Value) {
    $p=New-Object System.Diagnostics.ProcessStartInfo
    $p.FileName=$Gh; $p.Arguments="secret set $Name --repo $Repository"
    $p.UseShellExecute=$false; $p.RedirectStandardInput=$true
    $p.RedirectStandardOutput=$true; $p.RedirectStandardError=$true; $p.CreateNoWindow=$true
    $x=[Diagnostics.Process]::Start($p)
    try { $x.StandardInput.Write($Value); $x.StandardInput.Close(); if(-not $x.WaitForExit(120000)){ $x.Kill(); throw 'GITHUB_SECRET_TIMEOUT' }; if($x.ExitCode -ne 0){throw ('GITHUB_SECRET_FAILED_'+$Name)} }
    finally { $x.Dispose() }
}
function Set-GhVar([string]$Name,[string]$Value) {
    & $Gh variable set $Name --repo $Repository --body $Value *> $null
    if($LASTEXITCODE -ne 0){throw ('GITHUB_VARIABLE_FAILED_'+$Name)}
}

if (-not $Apply) {
    Write-Host 'SERVICEIT_GMAIL_B2B_V9=PREPARED_ONLY'
    Write-Host 'ONE_TIME_INSTALL=-Apply; NO_CRM_WRITES; NO_CLOUD_ENABLE'
    Write-Host 'PER_CARD_MANUAL_WORK=NOT_REQUIRED_AFTER_AUTHORIZED_TRIGGER'
    exit 0
}
foreach($f in @($BitrixFile,$GigaFile,$ScopeFile)){if(-not(Test-Path -LiteralPath $f -PathType Leaf)){throw 'SERVICEIT_DPAPI_INPUT_MISSING'}}
if(-not(Test-Path -LiteralPath (Join-Path $Repo '.git') -PathType Container)){throw 'SERVICEIT_GITHUB_CLONE_MISSING'}
if(-not(Get-Command 'node.exe' -ErrorAction SilentlyContinue) -or
   -not(Get-Command 'npm.cmd' -ErrorAction SilentlyContinue) -or
   -not(Get-Command 'npx.cmd' -ErrorAction SilentlyContinue)){
    $Winget=Get-Command winget -ErrorAction SilentlyContinue
    if(-not $Winget){throw 'NODE_LTS_MISSING_AND_WINGET_UNAVAILABLE'}
    & $Winget.Source install --id OpenJS.NodeJS.LTS --exact --source winget --accept-package-agreements --accept-source-agreements
    if($LASTEXITCODE -ne 0){throw 'NODE_LTS_INSTALL_FAILED'}
    $NodeDir=Join-Path $env:ProgramFiles 'nodejs'
    if(Test-Path -LiteralPath $NodeDir){$env:Path=$NodeDir+';'+$env:Path}
}
foreach($c in @('node.exe','npm.cmd','npx.cmd','git.exe')){if(-not(Get-Command $c -ErrorAction SilentlyContinue)){throw ('TOOL_MISSING_'+$c)}}
$Scope=(Get-Content -LiteralPath $ScopeFile -Raw -Encoding UTF8).Trim()
if($Scope -notin @('GIGACHAT_API_PERS','GIGACHAT_API_B2B','GIGACHAT_API_CORP')){throw 'GIGACHAT_SCOPE_INVALID'}
$gc=Get-Command gh -ErrorAction SilentlyContinue
$Gh=if($gc){$gc.Source}else{Join-Path $env:ProgramFiles 'GitHub CLI\gh.exe'}
if(-not(Test-Path -LiteralPath $Gh -PathType Leaf)){throw 'GITHUB_CLI_MISSING'}
& $Gh auth status *> $null
if($LASTEXITCODE -ne 0){& $Gh auth login --web --git-protocol https --scopes 'repo,workflow';FailIfExit 'GITHUB_LOGIN'}
$Auth=(& $Gh auth status 2>&1 | Out-String)
if($Auth -notmatch '(?i)\brepo\b'){& $Gh auth login --web --git-protocol https --scopes 'repo,workflow';FailIfExit 'GITHUB_SCOPE_LOGIN'}
$GitHubToken=(& $Gh auth token | Out-String).Trim()
if($GitHubToken.Length -lt 20){throw 'GITHUB_TOKEN_MISSING'}
$Full=(& $Gh api "repos/$Repository" --jq '.full_name' | Out-String).Trim()
if($LASTEXITCODE -ne 0 -or $Full -cne $Repository){throw 'GITHUB_REPO_IDENTITY_FAILED'}
$Private=(& $Gh api "repos/$Repository" --jq '.private' | Out-String).Trim()
if($LASTEXITCODE -ne 0 -or $Private -cne 'false'){throw 'PUBLIC_REPO_EXPECTED_FOR_FREE_ACTIONS'}
$ShareConsent=Read-Host 'Confirm the existing Google Apps Script project is owner-only and is not shared with other editors. Type YES'
if($ShareConsent -cne 'YES'){throw 'APPS_SCRIPT_OWNER_ONLY_CONFIRMATION_MISSING'}
$Agree=Read-Host 'Enable free Gmail-to-Bitrix automation? Only tender title, organizer, public URL, tender ID and Gmail message ID go to GitHub; raw email and contacts stay in Gmail. Type YES'
if($Agree -cne 'YES'){throw 'OWNER_INGEST_CONSENT_MISSING'}
$GigaConsent=Read-Host 'Allow GigaChat to receive ONLY tender title text for an unverified HOLD hypothesis? Type YES or NO'
$UseGiga=$GigaConsent -ceq 'YES'
$AdminConsent=Read-Host 'Are you a Bitrix24 administrator and approve the shared manager-form update? Type YES or NO'
$UseAdmin=$AdminConsent -ceq 'YES'

$Temp=Join-Path $env:TEMP ('serviceit-b2b-v9-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Temp -Force | Out-Null
$Bitrix=$null;$Giga=$null;$Fernet=$null;$Hmac=$null;$done=$false
try {
    $Bitrix=Read-Dpapi $BitrixFile; $Giga=Read-Dpapi $GigaFile
    if([string]::IsNullOrWhiteSpace($Bitrix) -or [string]::IsNullOrWhiteSpace($Giga)){throw 'SERVICEIT_DPAPI_EMPTY'}
    $names=@(& $Gh secret list --repo $Repository --json name | ConvertFrom-Json | ForEach-Object {$_.name})
    if(Test-Path -LiteralPath $LedgerFile){$Fernet=Read-Dpapi $LedgerFile}
    elseif($names -contains 'SERVICEIT_GH_LEDGER_KEY'){throw 'REMOTE_FERNET_KEY_WITHOUT_LOCAL_COPY'}
    else{$Fernet=New-Key -Pad $true;Write-Dpapi $LedgerFile $Fernet}
    if(Test-Path -LiteralPath $HmacFile){$Hmac=Read-Dpapi $HmacFile}
    elseif($names -contains 'SERVICEIT_B2B_INGEST_HMAC_KEY'){throw 'REMOTE_HMAC_KEY_WITHOUT_LOCAL_COPY'}
    else{$Hmac=New-Key;Write-Dpapi $HmacFile $Hmac}

    Set-GhSecret 'SERVICEIT_BITRIX_WEBHOOK' $Bitrix
    Set-GhSecret 'SERVICEIT_GH_LEDGER_KEY' $Fernet
    Set-GhSecret 'SERVICEIT_B2B_INGEST_HMAC_KEY' $Hmac
    if($UseGiga){Set-GhSecret 'GIGACHAT_CREDENTIALS' $Giga}
    Set-GhVar 'GIGACHAT_SCOPE' $Scope
    Set-GhVar 'SERVICEIT_B2B_INGEST_ENABLED' 'NO'
    Set-GhVar 'SERVICEIT_B2B_GIGACHAT_AUTO_ENABLED' $(if($UseGiga){'YES'}else{'NO'})
    Set-GhVar 'SERVICEIT_B2B_GIGACHAT_PRIVACY_APPROVED' $(if($UseGiga){'YES'}else{'NO'})
    Set-GhVar 'SERVICEIT_DIRECT_ID_HANDOFF_ENABLED' 'YES'
    Set-GhVar 'SERVICEIT_MANAGER_LAYOUT_ADMIN_APPROVED' $(if($UseAdmin){'YES'}else{'NO'})

    & git.exe -C $Repo fetch origin $Branch;FailIfExit 'GIT_FETCH'
    $zip=Join-Path $Temp 'src.zip'
    & git.exe -C $Repo archive --format=zip --output=$zip "origin/$Branch" 'integrations/apps_script/Code.gs' 'integrations/apps_script/appsscript.json'
    FailIfExit 'GIT_ARCHIVE'
    $extract=Join-Path $Temp 'extract';Expand-Archive -LiteralPath $zip -DestinationPath $extract
    $source=Join-Path $extract 'integrations\apps_script'
    $stage=Join-Path $Temp 'stage';$backup=Join-Path $Temp 'backup';$readback=Join-Path $Temp 'readback'
    New-Item -ItemType Directory -Path $stage,$backup,$readback -Force | Out-Null
    Copy-Item (Join-Path $source 'Code.gs') $stage
    Copy-Item (Join-Path $source 'appsscript.json') $stage
    $authz=(& npx.cmd --yes '@google/clasp@latest' show-authorized-user --json 2>&1 | Out-String)
    if($LASTEXITCODE -ne 0 -or $authz -notmatch [regex]::Escape($ExpectedAccount)){
        & npx.cmd --yes '@google/clasp@latest' login;FailIfExit 'GOOGLE_LOGIN'
        $authz=(& npx.cmd --yes '@google/clasp@latest' show-authorized-user --json 2>&1 | Out-String)
    }
    if($authz -notmatch [regex]::Escape($ExpectedAccount)){throw 'WRONG_GOOGLE_ACCOUNT'}
    # Never select or overwrite another Google project, including the
    # previously created Bitrix event bridge. Own this script ID locally.
    if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
        New-Item -Path $ProjectRoot -ItemType Directory -Force | Out-Null
    }
    if (Test-Path -LiteralPath $ProjectIdFile -PathType Leaf) {
        $ScriptId=(Get-Content -LiteralPath $ProjectIdFile -Raw -Encoding UTF8).Trim()
        if ($ScriptId -notmatch '^[A-Za-z0-9_-]{25,}$') {
            throw 'SERVICEIT_GMAIL_PROJECT_ID_INVALID'
        }
        Write-Host 'SERVICEIT_GMAIL_PROJECT=REUSING_PREVIOUS_V9_ONLY'
    } else {
        $create=Join-Path $Temp 'create'
        New-Item -Path $create -ItemType Directory -Force | Out-Null
        Push-Location $create
        try {
            & npx.cmd --yes '@google/clasp@latest' create --type standalone --title $ProjectName
            FailIfExit 'SERVICEIT_GMAIL_CREATE_DEDICATED_SCRIPT'
            $created=Join-Path $create '.clasp.json'
            if (-not (Test-Path -LiteralPath $created -PathType Leaf)) {
                throw 'SERVICEIT_GMAIL_CREATE_ID_MISSING'
            }
            $detail=Get-Content -LiteralPath $created -Raw -Encoding UTF8 | ConvertFrom-Json
            $ScriptId=[string]$detail.scriptId
            if ($ScriptId -notmatch '^[A-Za-z0-9_-]{25,}$') {
                throw 'SERVICEIT_GMAIL_CREATE_INVALID_ID'
            }
            # Persist before pushing, so recovery NEVER creates a second
            # Gmail project or touches the owner's Bitrix event bridge.
            [IO.File]::WriteAllText($ProjectIdFile,$ScriptId,[Text.UTF8Encoding]::new($false))
            Write-Host 'SERVICEIT_GMAIL_PROJECT=NEW_CREATED_ID_PERSISTED'
        } finally { Pop-Location }
    }
    $clasp=@{scriptId=$ScriptId;rootDir='.'}|ConvertTo-Json -Compress
    foreach($d in @($stage,$backup,$readback)){[IO.File]::WriteAllText((Join-Path $d '.clasp.json'),$clasp,[Text.UTF8Encoding]::new($false))}
    $private=@{expectedMailbox=$ExpectedAccount;githubToken=$GitHubToken;hmacSecret=$Hmac;ownerApproval='YES';cutoverMs=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()}|ConvertTo-Json -Compress
    $secretSource='const SERVICEIT_PRIVATE_CONFIG = Object.freeze('+$private+');'+[Environment]::NewLine
    [IO.File]::WriteAllText((Join-Path $stage 'secrets.gs'),$secretSource,[Text.UTF8Encoding]::new($false))
    Push-Location $backup;try{& npx.cmd --yes '@google/clasp@latest' pull;FailIfExit 'APPS_SCRIPT_BACKUP'}finally{Pop-Location}
    Push-Location $stage;try{& npx.cmd --yes '@google/clasp@latest' push --force;FailIfExit 'APPS_SCRIPT_PUSH'}finally{Pop-Location}
    Push-Location $readback;try{& npx.cmd --yes '@google/clasp@latest' pull;FailIfExit 'APPS_SCRIPT_READBACK'}finally{Pop-Location}
    foreach($name in @('Code.gs','appsscript.json','secrets.gs')){
        $a=(Get-FileHash (Join-Path $stage $name) -Algorithm SHA256).Hash
        $b=(Get-FileHash (Join-Path $readback $name) -Algorithm SHA256).Hash
        if($a -cne $b){throw ('APPS_SCRIPT_READBACK_MISMATCH_'+$name)}
    }
    Set-GhVar 'SERVICEIT_B2B_INGEST_ENABLED' 'YES'
    if($UseAdmin){& $Gh issue comment 74 --repo $Repository --body 'SERVICEIT_MANAGER_LAYOUT_APPLY_V1' *> $null;FailIfExit 'MANAGER_LAYOUT_MARKER'}
    $done=$true
    Write-Host 'SERVICEIT_APPS_SCRIPT_V9=INSTALLED_AND_READBACK_VERIFIED' -ForegroundColor Green
    Write-Host 'GITHUB_B2B_INGEST=ENABLED'
    Write-Host ('GIGACHAT_TITLE_ONLY='+$(if($UseGiga){'ENABLED'}else{'DISABLED'}))
    Write-Host ('MANAGER_LAYOUT_APPLY='+$(if($UseAdmin){'REQUESTED'}else{'NOT_AUTHORIZED'}))
    Write-Host 'NEXT_OWNER_ACTION=One-time Google consent for installPollingTrigger() required; no per-card manual work.'
    Write-Host 'LOCAL_WINDOWS_TASK=UNCHANGED_UNTIL_CLOUD_TRIGGER_IS_CONFIRMED'
    Push-Location $stage;try{& npx.cmd --yes '@google/clasp@latest' open-script}finally{Pop-Location}
} finally {
    Remove-Item -LiteralPath (Join-Path $Temp 'stage\secrets.gs') -Force -ErrorAction SilentlyContinue
    $Bitrix=$null;$Giga=$null;$Fernet=$null;$Hmac=$null;$GitHubToken=$null;$private=$null
    if($done){Remove-Item -LiteralPath $Temp -Recurse -Force -ErrorAction SilentlyContinue}
    else{Write-Host ('SERVICEIT_BACKUP_AND_DIAGNOSTICS='+$Temp)}
}
