# Windows PowerShell 5.1 offline mock: no CRM traffic.
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$runner=Join-Path $PSScriptRoot 'RUN_SERVICEIT_B2B_CARD7_FORENSIC_READONLY.ps1'
$tokens=$null;$errors=$null
[Management.Automation.Language.Parser]::ParseFile($runner,[ref]$tokens,[ref]$errors)|Out-Null
if ($errors.Count -ne 0) { throw 'FORENSIC_READONLY_PS_PARSE_FAIL' }
$mock=Join-Path $env:TEMP ('serviceit-forensic-'+[guid]::NewGuid().ToString('N')+'.ps1')
@'
if ($args -notcontains '-m' -or
    $args -notcontains 'leadpilot.serviceit_b2b_card7_forensic_readonly') {
    throw 'WRONG_MODULE'
}
if ($args -contains '--apply-once' -or $args -contains '--approve-exact-item-7') {
    throw 'WRITE_FLAGS_FORBIDDEN'
}
if ($args -notcontains '--inspect-live' -or $args -notcontains '--webhook-from-env') {
    throw 'READONLY_FLAGS_MISSING'
}
if ($env:SERVICEIT_B2B_CARD7_DIAG_WEBHOOK -cne
    'https://serviseit.bitrix24.ru/rest/2/FAKEDIAGWEBHOOKONLY123456') {
    throw 'SECURE_DIAG_WEBHOOK_HANDOFF_INVALID'
}
Write-Output '{"status":"B2B_CARD7_FORENSIC_READONLY_RESULT","crm_writes":0}'
$global:LASTEXITCODE=0
'@ | Set-Content -LiteralPath $mock -Encoding UTF8
try {
    function Read-Host {
        param([string]$Prompt,[switch]$AsSecureString)
        if (-not $AsSecureString) { throw 'MUST_HIDE_SECRET' }
        ConvertTo-SecureString 'https://serviseit.bitrix24.ru/rest/2/FAKEDIAGWEBHOOKONLY123456' -AsPlainText -Force
    }
    & $runner -PythonPath $mock
    if (Test-Path Env:\SERVICEIT_B2B_CARD7_DIAG_WEBHOOK) {
        throw 'DIAGNOSTIC_SECRET_NOT_CLEARED'
    }
    Write-Output 'SERVICEIT_CARD7_FORENSIC_WINDOWS=PASS; CRM_WRITES=0'
} finally {
    if (Test-Path Env:\SERVICEIT_B2B_CARD7_DIAG_WEBHOOK) {
        Remove-Item Env:\SERVICEIT_B2B_CARD7_DIAG_WEBHOOK -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $mock -Force -ErrorAction SilentlyContinue
}
