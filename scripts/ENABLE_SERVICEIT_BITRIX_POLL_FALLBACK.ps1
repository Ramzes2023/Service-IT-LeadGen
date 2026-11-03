# Bitrix 1062/21 WITHOUT outbound-webhook creation permission.
# Uses an ALREADY AUTHORIZED local incoming REST webhook and GitHub Actions.
# No email/Gmail/B2B source integrations; no historical CRM cards changed.
[CmdletBinding()]
param([switch]$Apply)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$RepositoryName = 'Ramzes2023/Service-IT-LeadGen'
$Repository = Split-Path $PSScriptRoot -Parent
$RestFile = Join-Path $env:LOCALAPPDATA 'ServiceIT\AI-Autofill\bitrix-rest.dpapi'
$GhCmd = Get-Command gh.exe -ErrorAction SilentlyContinue
function Guard([bool]$Condition,[string]$Code) {
    if (-not $Condition) { throw $Code }
}
function SafeGh([string[]]$Arguments,[string]$Code) {
    $Saved = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $Text = (& $GhCmd.Source @Arguments 2>$null | Out-String)
        $Exit = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $Saved }
    Guard ($Exit -eq 0) $Code
    return $Text.Trim()
}
function Set-GhVar([string]$Name,[string]$Value) {
    [void](SafeGh @('variable', 'set', $Name, '--repo', $RepositoryName,
                   '--body', $Value) ('SERVICEIT_SET_VAR_FAILED_' + $Name))
}
function Read-Dpapi([string]$File) {
    $secure = Get-Content -LiteralPath $File -Raw -Encoding UTF8 | ConvertTo-SecureString
    $ptr = [IntPtr]::Zero
    try {
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    }
    finally {
        if ($ptr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        }
        $secure.Dispose()
    }
}
Guard (Test-Path -LiteralPath (Join-Path $Repository '.git') -PathType Container) 'SERVICEIT_REPO_MISSING'
Guard (Test-Path -LiteralPath $RestFile -PathType Leaf) 'SERVICEIT_EXISTING_REST_DPAPI_MISSING'
Guard ($null -ne $GhCmd) 'SERVICEIT_GH_NOT_INSTALLED'
$Origin = (& git -C $Repository remote get-url origin | Out-String).Trim()
Guard ($LASTEXITCODE -eq 0 -and
       $Origin -match '^(https://github\.com/|git@github\.com:)Ramzes2023/Service-IT-LeadGen(\.git)?$') 'SERVICEIT_GIT_ORIGIN_MISMATCH'
$Branch = (& git -C $Repository branch --show-current | Out-String).Trim()
Guard ($LASTEXITCODE -eq 0 -and $Branch -ceq 'main') 'SERVICEIT_MAIN_BRANCH_REQUIRED'
$Source = Join-Path $Repository 'leadpilot\serviceit_bitrix_poll_fallback.py'
$Workflow = Join-Path $Repository '.github\workflows\serviceit-bitrix-poll-fallback.yml'
Guard ((Test-Path -LiteralPath $Source -PathType Leaf) -and
       (Test-Path -LiteralPath $Workflow -PathType Leaf)) 'SERVICEIT_POLLING_REVIEWED_CODE_NOT_IN_MAIN'
$RemoteRepo = SafeGh @('api', ('repos/' + $RepositoryName), '--jq', '.full_name') 'SERVICEIT_GH_REPO_CHECK_FAILED'
Guard ($RemoteRepo -ceq $RepositoryName) 'SERVICEIT_GH_REPO_MISMATCH'

# Only the owner's DPAPI webhook is opened; never print or store the URL.
$Webhook = $null
try {
    $Webhook = Read-Dpapi $RestFile
    Guard ($Webhook -match '^https://serviseit\.bitrix24\.ru/rest/[0-9]+/[A-Za-z0-9_-]+/?$') 'SERVICEIT_DPAPI_REST_URL_SCOPE_INVALID'
    $Cutoff = [DateTime]::UtcNow.AddMinutes(-10).ToString('yyyy-MM-ddTHH:mm:ss+00:00')
    $Payload = @{
        entityTypeId = 1062
        useOriginalUfNames = 'N'
        filter = @{ categoryId = 21; '>=createdTime' = $Cutoff }
        order = @{ id = 'ASC' }
        select = @('id','categoryId','createdTime')
        start = 0
    } | ConvertTo-Json -Depth 6 -Compress
    try {
        $ApiResult = Invoke-RestMethod -Method Post -Uri ($Webhook.TrimEnd('/') + '/crm.item.list.json') -Body $Payload -ContentType 'application/json; charset=utf-8' -TimeoutSec 30
    }
    catch {
        # Do not print the original exception: it can include the REST secret URL.
        throw 'SERVICEIT_BITRIX_READONLY_CRM_ITEM_LIST_DENIED_OR_UNAVAILABLE'
    }
    Guard ($null -ne $ApiResult -and $null -ne $ApiResult.result) 'SERVICEIT_BITRIX_LIST_RESPONSE_MISSING'
    Guard ($null -ne $ApiResult.result.items) 'SERVICEIT_BITRIX_LIST_ITEMS_SHAPE_INVALID'
    $Items = @($ApiResult.result.items)
    Guard ($Items.Count -le 50) 'SERVICEIT_BITRIX_LIST_PAGE_TOO_LARGE'
    foreach ($Item in $Items) {
        Guard ($null -ne $Item -and ($null -ne $Item.id) -and
               [string]$Item.categoryId -ceq '21' -and
               (-not $Item.PSObject.Properties['entityTypeId'] -or
                [string]$Item.entityTypeId -ceq '1062')) 'SERVICEIT_BITRIX_LIST_SCOPE_UNVERIFIED'
        $Created = [DateTimeOffset]::MinValue
        Guard ([DateTimeOffset]::TryParse([string]$Item.createdTime,[ref]$Created)) 'SERVICEIT_BITRIX_LIST_DATE_UNVERIFIED'
        Guard ($Created.UtcDateTime -ge [DateTimeOffset]::Parse($Cutoff).UtcDateTime.AddMinutes(-1)) 'SERVICEIT_BITRIX_LIST_FILTER_NOT_OBEYED'
    }
    Write-Host 'SERVICEIT_BITRIX_LIST_READONLY=PASS'
    Write-Host 'CRM_WRITES=0'
    Write-Host 'OUTGOING_WEBHOOK=NOT_REQUIRED'
}
finally {
    $Webhook = $null
    $Payload = $null
    $ApiResult = $null
    $Items = $null
}

if (-not $Apply) {
    Write-Host 'POLLING_PREVIEW=PASS; LIVE_ENABLE=NO'
    Write-Host 'NEXT=Apply only after confirming authorized limited future-card AI fields'
    exit 0
}
# Only repository secret NAMES are requested. No secret bytes are read.
$PresentSecrets = @(SafeGh @('secret', 'list', '--repo', $RepositoryName,
                            '--json', 'name') 'SERVICEIT_GH_SECRET_NAMES_FAILED' | ConvertFrom-Json |
                    ForEach-Object { $_.name })
foreach ($Needed in @('SERVICEIT_BITRIX_WEBHOOK',
                       'SERVICEIT_BITRIX_EVENT_BRIDGE_KEY',
                       'SERVICEIT_GH_LEDGER_KEY')) {
    Guard ($PresentSecrets -contains $Needed) ('SERVICEIT_REQUIRED_GH_SECRET_MISSING_' + $Needed)
}
# Let gh/jq select a single variable rather than piping an ambiguous JSON
# array through PowerShell 5.1 ConvertFrom-Json/@() enumeration semantics.
# Always redact values: console sees only vetted YES/NO or safe enum codes.
function Get-GhVarValue([string]$Name) {
    Guard ($Name -match '^[A-Z0-9_]{8,100}$') 'SERVICEIT_GITHUB_VARIABLE_NAME_INVALID'
    $Filter = '.[] | select(.name == "' + $Name + '") | .value'
    return (SafeGh @('variable', 'list', '--repo', $RepositoryName,
                      '--json', 'name,value', '--jq', $Filter)
                   ('SERVICEIT_GH_VARIABLE_LOOKUP_FAILED_' + $Name))
}
$RawPrivacy = Get-GhVarValue 'SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED'
$PrivacyNeedsDefault = $false
if ($RawPrivacy -ceq 'YES' -or $RawPrivacy -ceq 'NO') {
    $PrivacyValue = $RawPrivacy
    Write-Host ('SERVICEIT_GIGACHAT_PRIVACY=EXISTING_' + $PrivacyValue)
} else {
    # Unknown/blank/ambiguous value is NEVER treated as consent.
    # The user already authorizes only public tender titles via an independent
    # B2B source gate; that is not blanket approval for arbitrary CRM titles.
    $PrivacyValue = 'NO'
    $PrivacyNeedsDefault = $true
    Write-Host 'SERVICEIT_GIGACHAT_PRIVACY=UNVERIFIED_SAFE_NO_EXTERNAL_MODEL'
}
$RawPublicTitleAI = Get-GhVarValue 'SERVICEIT_PRELIMINARY_PUBLIC_TITLE_AI_ENABLED'
$PublicTitleNeedsOff = ($PrivacyValue -ceq 'NO' -and
                        $RawPublicTitleAI -ceq 'YES')
if ($PublicTitleNeedsOff) {
    # Prevent "new-card receipt reserved then worker fails" where preexisting
    # preliminary title AI flag conflicts with the safety default of NO.
    # Do not touch any settings until one-time owner activation is confirmed.
    Write-Host 'SERVICEIT_PUBLIC_TITLE_AI=SAFETY_DISABLE_REQUIRED_ON_ACTIVATION'
}
if ($PrivacyValue -ceq 'YES' -and
    $PresentSecrets -notcontains 'GIGACHAT_CREDENTIALS') {
    # Consent alone cannot make a missing model credential work. Switch to
    # safe local HOLD processing rather than reserve each new-card receipt
    # and fail an external-model invocation. Explicit owner YES still required.
    $PrivacyValue = 'NO'
    $PrivacyNeedsDefault = $true
    $PublicTitleNeedsOff = ($RawPublicTitleAI -ceq 'YES')
    Write-Host 'SERVICEIT_GIGACHAT=CONFIG_MISSING_SAFE_HOLD_ONLY'
}
$OwnerAck = Read-Host 'After read-only N-mode check, authorize scheduling ONLY future 1062/21 card IDs to guarded AI worker (not existing cards)? Type YES'
Guard ($OwnerAck -ceq 'YES') 'SERVICEIT_OWNER_POLL_ACTIVATION_NOT_CONFIRMED'
$StartUtc = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
$Started = $false
try {
    Set-GhVar 'SERVICEIT_BITRIX_POLL_ENABLED' 'NO'
    if ($PrivacyNeedsDefault) {
        # Only after the owner's explicit activation acknowledgement.
        # Invalid/unset privacy never becomes an external-model YES.
        Set-GhVar 'SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED' 'NO'
        Write-Host 'SERVICEIT_GIGACHAT_PRIVACY=SAFE_NO_PERSISTED'
    }
    if ($PublicTitleNeedsOff) {
        Set-GhVar 'SERVICEIT_PRELIMINARY_PUBLIC_TITLE_AI_ENABLED' 'NO'
        Write-Host 'SERVICEIT_PUBLIC_TITLE_AI=OFF_UNTIL_SEPARATE_APPROVAL'
    }
    Set-GhVar 'SERVICEIT_BITRIX_POLL_START_UTC' $StartUtc
    # The original signed event processor still has an independent gate.
    Set-GhVar 'SERVICEIT_BITRIX_EVENT_AI_ENABLED' 'YES_1062_21_CLOUD'
    $Started = $true
    Set-GhVar 'SERVICEIT_BITRIX_POLL_ENABLED' 'YES_1062_21_READONLY'
    Write-Host 'SERVICEIT_POLL_FALLBACK=ACTIVATED_AFTER_READONLY_PROBE'
    Write-Host 'POLL_INTERVAL=10_MINUTES_BEST_EFFORT'
    Write-Host 'GOOGLE_WEBAPP_AND_OUTGOING_TOKEN=NOT_REQUIRED'
    Write-Host 'CRM_OLD_RECORD_WRITES=FORBIDDEN'
    Write-Host ('GIGACHAT_FOR_NEW_CARDS='+$(if($PrivacyValue -ceq 'YES'){'CONSENTED_CONFIG_PRESENT'}else{'OFF_HOLD_ONLY'}))
    Write-Host 'LIVE_CARD_END_TO_END=NOT_YET_VERIFIED'
}
catch {
    # Fail closed. On any unexpected GitHub error, verify variable states
    # manually; these calls can themselves fail if GitHub is unavailable.
    try { Set-GhVar 'SERVICEIT_BITRIX_POLL_ENABLED' 'NO' } catch {}
    if ($Started) {
        try { Set-GhVar 'SERVICEIT_BITRIX_EVENT_AI_ENABLED' 'NO' } catch {}
    }
    throw 'SERVICEIT_POLL_ACTIVATION_FAILED_RECHECK_GITHUB_VARIABLES'
}
