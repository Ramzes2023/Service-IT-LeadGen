# Synthetic Windows PowerShell 5.1 acceptance for nonfatal Codex stderr.
# NO Codex requests, GitHub writes, corporate credentials, or Bitrix calls.
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$TempLog = Join-Path ([IO.Path]::GetTempPath()) ('serviceit-stderr-' + [guid]::NewGuid().ToString('N') + '.log')

function Invoke-SyntheticNative([int]$ExitWanted) {
    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        'synthetic-stdin-prompt' | & $env:ComSpec /d /c "echo SYNTHETIC_CODEX_MODEL_CATALOG_TIMEOUT 1>&2 & exit /b $ExitWanted" 2>&1 |
            ForEach-Object { $_.ToString() } |
            Tee-Object -FilePath $TempLog -Append | Out-Null
        $ExitObserved = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    return $ExitObserved
}

try {
    $Success = Invoke-SyntheticNative 0
    if ($Success -ne 0) { throw 'STDERR_WARNING_FALSIFIED_SUCCESS_EXIT' }
    $Failure = Invoke-SyntheticNative 23
    if ($Failure -ne 23) { throw 'STDERR_WARNING_MASKED_REAL_NONZERO_EXIT' }
    if ($ErrorActionPreference -cne 'Stop') { throw 'ERR_ACTION_NOT_RESTORED' }
    $Logged = @(Select-String -LiteralPath $TempLog -SimpleMatch -Pattern 'SYNTHETIC_CODEX_MODEL_CATALOG_TIMEOUT')
    if ($Logged.Count -lt 2) { throw 'STDERR_DIAGNOSTIC_NOT_LOGGED' }
    # The preceding intentional synthetic exit 23 must not become this CI step's exit.
    $global:LASTEXITCODE = 0
    Write-Output 'SERVICEIT_CODEX_NATIVE_STDERR_PS51=PASS'
}
finally {
    Remove-Item -LiteralPath $TempLog -Force -ErrorAction SilentlyContinue
}
