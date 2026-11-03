# Test owner-CLI variable lookup on Windows PowerShell 5.1, WITHOUT GitHub,
# network access, CRM access, token reads or changes to host settings.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$CodePath = Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts\ENABLE_SERVICEIT_BITRIX_POLL_FALLBACK.ps1'
$tokens=$null; $parseErrors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile(
    $CodePath,[ref]$tokens,[ref]$parseErrors)
if($parseErrors.Count -gt 0){throw 'POLL_PS51_PARSE_FAILED'}
$found=@($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -ceq 'Get-GhVarValue'
},$true))
if($found.Count -ne 1){throw 'POLL_GH_LOOKUP_FUNCTION_NOT_UNIQUE'}
$script:RepositoryName='Ramzes2023/Service-IT-LeadGen'
$script:FixturesNames='[]'
$script:FixtureValue=''
$script:MockError=''
$script:CliCalls=@()
function Guard([bool]$Condition,[string]$Code) {
    if(-not $Condition){throw $Code}
}
function SafeGh([string[]]$Arguments,[string]$Code) {
    $script:CliCalls+=,(@($Arguments))
    if($Arguments[0] -ne 'variable'){throw 'UNEXPECTED_CLI_CATEGORY'}
    if($script:MockError -ceq 'LIST' -and $Arguments[1] -ceq 'list'){
        throw 'MOCK_LIST_DENIED'
    }
    if($script:MockError -ceq 'GET' -and $Arguments[1] -ceq 'get'){
        throw 'MOCK_GET_DENIED'
    }
    if($Arguments[1] -ceq 'list'){
        if($Arguments -contains '--jq'){throw 'JQ_PATH_FORBIDDEN'}
        if($Arguments -contains 'name,value'){throw 'RAW_VALUES_ENUM_FORBIDDEN'}
        if(-not ($Arguments -contains 'name')){throw 'NAME_ONLY_LIST_REQUIRED'}
        return $script:FixturesNames
    }
    if($Arguments[1] -ceq 'get'){
        if($Arguments[2] -cne 'SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED'){throw 'UNEXPECTED_VARIABLE'}
        if($Arguments -contains '--jq'){throw 'JQ_PATH_FORBIDDEN'}
        return $script:FixtureValue
    }
    throw 'UNEXPECTED_GH_SUBCOMMAND'
}
# Load only the audited function node: never dot-source the installer.
Invoke-Expression $found[0].Extent.Text
$script:FixturesNames='[]'
if((Get-GhVarValue 'SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED') -cne ''){
    throw 'MISSING_VARIABLE_NOT_HANDLED'
}
if($script:CliCalls.Count -ne 1){throw 'MISSING_SHOULD_NOT_CALL_GET'}
$script:FixturesNames='[{"name":"SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED"}]'
foreach($expected in @('YES','NO','MALFORMED_IGNORED')){
    $script:FixtureValue=$expected
    $script:CliCalls=@()
    $actual=Get-GhVarValue 'SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED'
    if($actual -cne $expected){throw 'VALUE_NOT_PRESERVED'}
    if($script:CliCalls.Count -ne 2){throw 'EXPECTED_LIST_THEN_GET'}
}
$script:FixturesNames='[{"name":"SOME_OTHER_VARIABLE"}]'
$script:CliCalls=@()
if((Get-GhVarValue 'SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED') -cne ''){
    throw 'OTHER_NAME_SHOULD_BE_MISSING'
}
if($script:CliCalls.Count -ne 1){throw 'OTHER_NAME_SHOULD_NOT_GET'}
$script:FixturesNames='[{"name":"SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED"},{"name":"SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED"}]'
try{
    [void](Get-GhVarValue 'SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED')
    throw 'DUPLICATES_NOT_BLOCKED'
}catch {
    if($_.Exception.Message -cne 'SERVICEIT_GH_VARIABLE_NAMES_AMBIGUOUS'){throw}
}
$script:FixturesNames='[{"name":"SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED"}]'
$script:MockError='LIST'
try{
    [void](Get-GhVarValue 'SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED')
    throw 'LIST_DENIAL_NOT_BLOCKED'
}catch{
    if($_.Exception.Message -cne 'MOCK_LIST_DENIED'){throw}
}
$script:MockError='GET'
try{
    [void](Get-GhVarValue 'SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED')
    throw 'GET_DENIAL_NOT_BLOCKED'
}catch{
    if($_.Exception.Message -cne 'MOCK_GET_DENIED'){throw}
}
Write-Output 'SERVICEIT_POLL_PRIVACY_POWERSHELL51_MOCK=PASS'
