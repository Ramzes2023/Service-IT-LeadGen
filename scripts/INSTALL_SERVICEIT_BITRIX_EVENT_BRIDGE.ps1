# One-time owner-local bridge install. Run ONLY after the CI-reviewed PR is merged.
# This script never edits old CRM cards and never starts a poller.
[CmdletBinding()]
param([switch]$Apply)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$Repo='Ramzes2023/Service-IT-LeadGen'
$LocalRepo=Join-Path $HOME 'Service_IT_GitHub_Repo\Service-IT-LeadGen'
$Root=Join-Path $env:LOCALAPPDATA 'ServiceIT\Bitrix-Event-Bridge'
$Project=Join-Path $Root 'script-project'
$SecretDir=Join-Path $env:LOCALAPPDATA 'ServiceIT\AI-Autofill'
$RestFile=Join-Path $SecretDir 'bitrix-rest.dpapi'
$HmacFile=Join-Path $Root 'bridge-hmac.dpapi'

function Check-Exit([string]$Tag) { if($LASTEXITCODE -ne 0){throw ($Tag+'_FAILED')} }
function Read-Dpapi([string]$File) {
    $secure=Get-Content -LiteralPath $File -Raw -Encoding UTF8 | ConvertTo-SecureString
    $ptr=[IntPtr]::Zero
    try { $ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
          return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { if($ptr -ne [IntPtr]::Zero){[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)};$secure.Dispose() }
}
function Plain([Security.SecureString]$Secret) {
    $ptr=[IntPtr]::Zero
    try { $ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
          return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { if($ptr -ne [IntPtr]::Zero){[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)} }
}
function Save-Dpapi([string]$File,[string]$Value) {
    $secure=ConvertTo-SecureString $Value -AsPlainText -Force
    try { [IO.File]::WriteAllText($File,(ConvertFrom-SecureString $secure),[Text.UTF8Encoding]::new($false)) }
    finally { $secure.Dispose() }
}
function New-Key([switch]$Fernet) {
    $bytes=New-Object byte[] 32
    $rng=[Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $key=[Convert]::ToBase64String($bytes).Replace('+','-').Replace('/','_')
    if(-not $Fernet){$key=$key.TrimEnd('=')}
    return $key
}
function Set-GhSecret([string]$Name,[string]$Value) {
    $start=New-Object Diagnostics.ProcessStartInfo
    $start.FileName=$Gh
    $start.Arguments="secret set $Name --repo $Repo"
    $start.UseShellExecute=$false
    $start.RedirectStandardInput=$true
    $start.RedirectStandardOutput=$true
    $start.RedirectStandardError=$true
    $start.CreateNoWindow=$true
    $proc=[Diagnostics.Process]::Start($start)
    try {
        $proc.StandardInput.Write($Value)
        $proc.StandardInput.Close()
        if(-not $proc.WaitForExit(120000)){$proc.Kill();throw 'GITHUB_SECRET_TIMEOUT'}
        if($proc.ExitCode -ne 0){throw ('GITHUB_SECRET_UPLOAD_FAILED_'+$Name)}
    } finally { $proc.Dispose() }
}
function Set-GhVar([string]$Name,[string]$Value) {
    & $Gh variable set $Name --repo $Repo --body $Value 1>$null 2>$null
    Check-Exit ('GITHUB_VARIABLE_'+$Name)
}
function New-PrivateSource([string]$Token,[string]$Github,[string]$BridgeKey,[string]$Enabled) {
    $private=@{ownerApproved='YES';enabled=$Enabled;
               outgoingApplicationToken=$Token;githubToken=$Github;
               hmacKey=$BridgeKey} | ConvertTo-Json -Compress
    [IO.File]::WriteAllText((Join-Path $Project 'Private.gs'),
        ('const SERVICEIT_BITRIX_BRIDGE_PRIVATE = Object.freeze('+$private+');'),
        [Text.UTF8Encoding]::new($false))
}
function Call-ReadOnly([string]$Webhook,[string]$Method,[hashtable]$Body) {
    $base=$Webhook.TrimEnd('/')
    if($base -notmatch '^https://serviseit\.bitrix24\.ru/rest/[0-9]+/[A-Za-z0-9_-]+$'){
        throw 'BITRIX_WEBHOOK_URL_SCOPE_INVALID'
    }
    try {
        return Invoke-RestMethod -Uri ($base+'/'+$Method+'.json') -Method Post -Body $Body -TimeoutSec 30
    } catch {
        throw ('BITRIX_READONLY_'+$Method.Replace('.','_')+'_FAILED')
    }
}
if(-not $Apply){
    Write-Host 'PREPARED_ONLY; use -Apply after corporate manager authorizes AI fields and outgoing event.'
    Write-Host 'NO_CRM_WRITES; NO_GIGACHAT; NO_GITHUB_ENABLE.'
    exit 0
}
if(-not (Test-Path $RestFile -PathType Leaf)){throw 'LOCAL_DPAPI_BITRIX_WEBHOOK_MISSING'}
if(-not (Test-Path (Join-Path $LocalRepo '.git') -PathType Container)){throw 'LOCAL_SERVICEIT_REPO_MISSING'}
$ghcmd=Get-Command gh -ErrorAction SilentlyContinue
if(-not $ghcmd){throw 'GITHUB_CLI_NOT_AVAILABLE'}
$Gh=$ghcmd.Source
. (Join-Path $PSScriptRoot 'ENSURE_SERVICEIT_NODE_LTS.ps1')
$node=Ensure-ServiceITNode
$npx=Get-Command npx.cmd -ErrorAction SilentlyContinue
if(-not $node -or -not $npx){throw 'SERVICEIT_NODE_BOOTSTRAP_INCOMPLETE'}
New-Item -ItemType Directory -Path $Root,$Project -Force | Out-Null

$Allowed=Read-Host 'Is corporate ServiceIT manager approval given to write ONLY new 1062/21 AI fields? Type YES'
if($Allowed -cne 'YES'){throw 'CORPORATE_CRM_AI_WRITE_APPROVAL_REQUIRED'}
$OwnerOnly=Read-Host 'Will the new Google Apps Script project be owner-only with no other editors? Type YES'
if($OwnerOnly -cne 'YES'){throw 'GOOGLE_SCRIPT_OWNER_ONLY_REQUIRED'}
$IdApproval=Read-Host 'Allow forwarding ONLY new Bitrix internal numeric card IDs to GitHub Actions (no company details)? Type YES'
if($IdApproval -cne 'YES'){throw 'GITHUB_EVENT_CARD_ID_PRIVACY_CONSENT_REQUIRED'}
$Privacy=Read-Host 'Send public tender title ONLY to GigaChat? Requires corporate privacy approval. Type YES or NO'
$GigaAllowed=$Privacy -ceq 'YES'
Write-Host 'STEP=SECURE_PREFLIGHT_START'
& $Gh auth status 1>$null 2>$null;Check-Exit 'GITHUB_AUTH'
$repoIdentity=(& $Gh api "repos/$Repo" --jq '.full_name' | Out-String).Trim()
if($LASTEXITCODE -ne 0 -or $repoIdentity -cne $Repo){throw 'GITHUB_IDENTITY_FAILED'}

$Webhook=$null;$Hmac=$null;$GithubToken=$null;$AppToken=$null
try {
    $Webhook=Read-Dpapi $RestFile
    $typ=Call-ReadOnly $Webhook 'crm.type.getByEntityTypeId' @{entityTypeId=1062}
    if(-not $typ.result.type -or [string]$typ.result.type.entityTypeId -ne '1062'){
        throw 'BITRIX_SMART_PROCESS_NOT_CONFIRMED'
    }
    $categories=Call-ReadOnly $Webhook 'crm.category.list' @{entityTypeId=1062}
    if(-not @($categories.result.categories|Where-Object { [string]$_.id -eq '21' }).Count){
        throw 'BITRIX_CATEGORY_21_NOT_CONFIRMED'
    }
    $fields=Call-ReadOnly $Webhook 'crm.item.fields' @{entityTypeId=1062;useOriginalUfNames='Y'}
    if(-not $fields.result.fields){throw 'BITRIX_CRM_FIELDS_NOT_VISIBLE'}
    Write-Host 'BITRIX_READONLY_SCOPE=PASS'
    $GithubToken=(& $Gh auth token | Out-String).Trim()
    if($LASTEXITCODE -ne 0 -or $GithubToken.Length -lt 20){throw 'GITHUB_TOKEN_NOT_AVAILABLE'}

    if(Test-Path $HmacFile){
        $Hmac=Read-Dpapi $HmacFile
    } else {
        $secretNames=@(& $Gh secret list --repo $Repo --json name | ConvertFrom-Json | ForEach-Object {$_.name})
        if($secretNames -contains 'SERVICEIT_BITRIX_EVENT_BRIDGE_KEY'){
            throw 'REMOTE_HMAC_EXISTS_NO_LOCAL_KEY_REFUSE_ROTATION'
        }
        $Hmac=New-Key
        Save-Dpapi $HmacFile $Hmac
    }

    Set-GhVar 'SERVICEIT_BITRIX_EVENT_AI_ENABLED' 'NO'
    Set-GhSecret 'SERVICEIT_BITRIX_EVENT_BRIDGE_KEY' $Hmac
    Set-GhSecret 'SERVICEIT_BITRIX_WEBHOOK' $Webhook
    $remoteNames=@(& $Gh secret list --repo $Repo --json name | ConvertFrom-Json | ForEach-Object {$_.name})
    if($remoteNames -notcontains 'SERVICEIT_GH_LEDGER_KEY'){
        $ledgerKey=New-Key -Fernet
        Set-GhSecret 'SERVICEIT_GH_LEDGER_KEY' $ledgerKey
        $ledgerKey=$null
    }
    if($GigaAllowed){
        $GigaFile=Join-Path $SecretDir 'gigachat.dpapi'
        $ScopeFile=Join-Path $SecretDir 'gigachat-scope.txt'
        if(-not(Test-Path $GigaFile -PathType Leaf) -or -not(Test-Path $ScopeFile -PathType Leaf)){
            throw 'GIGACHAT_LOCAL_DPAPI_OR_SCOPE_MISSING'
        }
        $Scope=(Get-Content -LiteralPath $ScopeFile -Raw -Encoding UTF8).Trim()
        if($Scope -notin @('GIGACHAT_API_PERS','GIGACHAT_API_B2B','GIGACHAT_API_CORP')){
            throw 'GIGACHAT_SCOPE_INVALID'
        }
        $Giga=Read-Dpapi $GigaFile
        try {
            if([string]::IsNullOrWhiteSpace($Giga)){throw 'GIGACHAT_SECRET_EMPTY'}
            Set-GhSecret 'GIGACHAT_CREDENTIALS' $Giga
        } finally { $Giga=$null }
        Set-GhVar 'GIGACHAT_SCOPE' $Scope
    }
    Set-GhVar 'SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED' $(if($GigaAllowed){'YES'}else{'NO'})
    Set-GhVar 'SERVICEIT_PRELIMINARY_PUBLIC_TITLE_AI_ENABLED' $(if($GigaAllowed){'YES'}else{'NO'})
    Set-GhVar 'SERVICEIT_PRELIMINARY_PUBLIC_HOSTS' 'b2b-center.ru,zakupki.gov.ru'

    # Never reuse the Gmail Apps Script; create a standalone Bitrix project.
    $sourceCode=Join-Path $LocalRepo 'integrations\bitrix_event_bridge\Code.gs'
    $sourceManifest=Join-Path $LocalRepo 'integrations\bitrix_event_bridge\appsscript.json'
    if(-not(Test-Path $sourceCode) -or -not(Test-Path $sourceManifest)){
        throw 'BRIDGE_SOURCE_NOT_IN_MAIN'
    }
    Copy-Item $sourceCode (Join-Path $Project 'Code.gs') -Force
    Copy-Item $sourceManifest (Join-Path $Project 'appsscript.json') -Force
    if(-not(Test-Path (Join-Path $Project '.clasp.json'))){
        Push-Location $Project
        try {
            & $npx.Source --yes '@google/clasp@latest' login
            Check-Exit 'GOOGLE_CLASP_LOGIN'
            $Authorized=(& $npx.Source --yes '@google/clasp@latest' show-authorized-user --json 2>&1 | Out-String)
            if($LASTEXITCODE -ne 0 -or
               $Authorized -notmatch [regex]::Escape('ramzesisayev444@gmail.com')){
                throw 'WRONG_GOOGLE_ACCOUNT_FOR_SERVICEIT_OWNER_ONLY_BRIDGE'
            }
            & $npx.Source --yes '@google/clasp@latest' create --type webapp --title 'ServiceIT Bitrix24 AI New Card Bridge'
            Check-Exit 'NEW_OWNER_ONLY_GOOGLE_SCRIPT_CREATION'
        } finally {Pop-Location}
    }
    # First push is disabled. Deployment is configured by owner in Google UI.
    New-PrivateSource ('NOT-YET-ACTIVATED-OUTGOING-TOKEN-000000000') $GithubToken $Hmac 'NO'
    Push-Location $Project
    try{
        & $npx.Source --yes '@google/clasp@latest' push --force
        Check-Exit 'GOOGLE_CLASP_PUSH'
        & $npx.Source --yes '@google/clasp@latest' open-script
        Check-Exit 'GOOGLE_SCRIPT_OPEN'
    } finally {Pop-Location}
    Write-Host 'GOOGLE_DEPLOY=In editor: Deploy -> New deployment -> Web app; Execute as Me; Access Anyone.'
    Write-Host 'GOOGLE_DEPLOY=Authorize; then copy the HTTPS web app /exec URL.'
    $Receiver=Read-Host 'Paste just the new Google Apps Script /exec URL'
    if($Receiver -notmatch '^https://script\.google\.com/macros/s/[A-Za-z0-9_-]+/exec$'){
        throw 'GOOGLE_RECEIVER_URL_NOT_VERIFIED'
    }
    Write-Host 'BITRIX_SETUP=Developer resources > Other > Outgoing webhook.'
    Write-Host 'BITRIX_SETUP=Handler URL is the Google receiver above. Choose OnCrmDynamicItemAdd or specific 1062 event. Save.'
    $SecureToken=Read-Host 'Paste Bitrix-generated OUTGOING application_token (hidden)' -AsSecureString
    try { $AppToken=Plain $SecureToken } finally {$SecureToken.Dispose()}
    if($AppToken.Length -lt 24){throw 'BITRIX_OUTGOING_APPLICATION_TOKEN_NOT_VALID'}
    New-PrivateSource $AppToken $GithubToken $Hmac 'YES'
    Push-Location $Project
    try {
        & $npx.Source --yes '@google/clasp@latest' push --force
        Check-Exit 'GOOGLE_BRIDGE_SECRET_PUSH'
        Write-Host 'GOOGLE_WEBAPP=Deploy > Manage deployments > Edit > New version > Deploy.'
        Write-Host 'The existing public /exec URL MUST remain unchanged.'
        & $npx.Source --yes '@google/clasp@latest' open-script
        Check-Exit 'GOOGLE_EDIT_DEPLOYMENT'
    } finally {Pop-Location}
    $Confirmed=Read-Host 'Updated deployment to new version with same /exec URL? Type YES'
    if($Confirmed -cne 'YES'){throw 'GOOGLE_WEBAPP_VERSION_UNVERIFIED'}
    try{
        $response=Invoke-WebRequest -Uri $Receiver -TimeoutSec 25 -UseBasicParsing
        if($response.StatusCode -ne 200 -or
           $response.Content -notmatch 'SERVICEIT_BITRIX_BRIDGE'){
            throw 'GOOGLE_ANONYMOUS_WEBAPP_NOT_ACCESSIBLE'
        }
    }catch{throw 'GOOGLE_ANONYMOUS_WEBAPP_NOT_ACCESSIBLE'}
    Write-Host 'FREE_RECEIVER_PUBLIC_GET=PASS'
    $Enable=Read-Host 'ENABLE new-card AI/HOLD writes for FUTURE Bitrix 1062/21 events only? Type YES'
    if($Enable -cne 'YES'){throw 'OWNER_LIVE_ACTIVATION_MISSING'}
    Set-GhVar 'SERVICEIT_BITRIX_EVENT_AI_ENABLED' 'YES_1062_21_CLOUD'
    Write-Host 'SERVICEIT_BITRIX_EVENT_AI=CANARY_MODE_STARTED'
    Write-Host 'Create ONE NEW approved TEST card in Bitrix smart process 1062/category 21 NOW.'
    Write-Host 'Do not use or edit historical cards, real customers or deleted card 7.'
    $CanaryText=Read-Host 'Paste ONLY the internal numeric ID of the NEW TEST card'
    if($CanaryText -notmatch '^[1-9][0-9]{0,9}$' -or
       [long]$CanaryText -lt 8 -or [long]$CanaryText -gt 2000000000){
        Set-GhVar 'SERVICEIT_BITRIX_EVENT_AI_ENABLED' 'NO'
        throw 'CANARY_ITEM_ID_MISSING_CLOUD_DISABLED'
    }
    $Canary=[int]$CanaryText
    $ExpectedFields=@(
        'UF_CRM_13_SVCIT_AI_STATUS',
        'UF_CRM_13_SVCIT_AI_SUMMARY',
        'UF_CRM_13_SVCIT_AI_HYPOTHESIS',
        'UF_CRM_13_SVCIT_AI_QUOTE',
        'UF_CRM_13_SVCIT_AI_RECOMMENDATION',
        'UF_CRM_13_SVCIT_AI_LIMITATIONS',
        'UF_CRM_13_SVCIT_AI_FULL_REPORT',
        'UF_CRM_13_SVCIT_AI_MANAGER_BRIEF',
        'UF_CRM_13_SVCIT_AI_NEXT_ACTION'
    )
    $ExactReadback=$false
    for($Attempt=0;$Attempt -lt 15;$Attempt++){
        Start-Sleep -Seconds 12
        try{
            $Read=Call-ReadOnly $Webhook 'crm.item.get' @{
                entityTypeId=1062;id=$Canary;useOriginalUfNames='Y'
            }
            $Item=$Read.result.item
            if(-not $Item -or [string]$Item.id -ne $CanaryText -or
               [string]$Item.entityTypeId -ne '1062' -or
               [string]$Item.categoryId -ne '21'){continue}
            $Present=0
            foreach($Code in $ExpectedFields){
                $FieldProperty=$Item.PSObject.Properties[$Code]
                if($null -ne $FieldProperty -and $FieldProperty.Value -is [string] -and
                   -not [string]::IsNullOrWhiteSpace($FieldProperty.Value)){$Present++}
            }
            if($Present -eq 9){$ExactReadback=$true;break}
        }catch{
            # No raw REST error, CRM data or secret in console output.
        }
    }
    if(-not $ExactReadback){
        Set-GhVar 'SERVICEIT_BITRIX_EVENT_AI_ENABLED' 'NO'
        Write-Host 'GITHUB_WORKFLOW=Check ServiceIT new Bitrix card to AI manager'
        throw 'CANARY_NINE_AI_FIELDS_NOT_CONFIRMED_CLOUD_DISABLED_NO_REPLAY'
    }
    Write-Host 'SERVICEIT_BITRIX_EVENT_AI=LIVE_CANARY_NINE_FIELDS_READBACK_PASS'
    Write-Host 'BITRIX_MANAGER_REPORT=CONFIRMED'
    Write-Host 'LAPTOP_REQUIRED_AFTER_INSTALL=NO'
    Write-Host 'NOTE=GIGACHAT_TITLE_ONLY_REQUIRES_SEPARATE_PRIVACY_AND_SOURCE_APPROVAL'
} finally {
    $Webhook=$null;$Hmac=$null;$GithubToken=$null;$AppToken=$null
    # A private script project retains its secrets for its own deployment.
    # Never git-add this folder or expose it through the public repo.
    Write-Host ('PRIVATE_APPS_SCRIPT_PROJECT=' + $Project)
}
