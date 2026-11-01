# Offline mapping acceptance only. No webhook prompt, CRM REST, live writes or sources.
[CmdletBinding()]
param([string]$PythonPath = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) { $PythonPath = Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'SERVICEIT_MAPPING_EXISTING_VENV_REQUIRED'
}
Push-Location -LiteralPath $Repo
try {
    Write-Output 'SERVICEIT_1062_MAPPING_ACCEPTANCE=START; CRM_WRITES=NO'
    & $PythonPath -m pytest -q -p no:cacheprovider tests/test_bitrix_handoff_dryrun.py tests/test_bitrix_1062_mapping.py
    if ($LASTEXITCODE -ne 0) { throw 'SERVICEIT_1062_MAPPING_TESTS_FAILED' }
    & $PythonPath -m py_compile leadpilot/bitrix_1062_mapping.py
    if ($LASTEXITCODE -ne 0) { throw 'SERVICEIT_1062_MAPPING_COMPILE_FAILED' }
    $Check = @'
from datetime import date
from leadpilot.bitrix_handoff_dryrun import build_offline_handoff, synthetic_fixture
from leadpilot.bitrix_1062_mapping import build_synthetic_1062_plan
now = date.today()
result = build_synthetic_1062_plan(build_offline_handoff(synthetic_fixture(now), today=now), today=now)
if result["operation_count"] != 2 or result["field_count"] != 16 or result["crm_writes"] != "NO":
    raise RuntimeError("MAPPING_ACCEPTANCE_INVARIANT_FAILED")
print("SERVICEIT_1062_MAPPING_PREVIEW=PASS; SYNTHETIC_SIGNALS=2; UF_FIELDS=16; REST_CALLS=0")
'@
    $Check | & $PythonPath -
    if ($LASTEXITCODE -ne 0) { throw 'SERVICEIT_1062_MAPPING_PREVIEW_FAILED' }
    Write-Output 'SERVICEIT_1062_MAPPING_ACCEPTANCE=PASS_OFFLINE_ONLY; CRM_WRITES=NO; SOURCES=NO'
} finally {
    Pop-Location
}
