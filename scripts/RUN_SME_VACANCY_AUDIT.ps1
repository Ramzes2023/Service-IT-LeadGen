#Requires -Version 5.1
# ASCII-only for Windows PowerShell 5.1 (UTF-8 file may be read as ANSI).
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
try {
    $repo = Join-Path $env:USERPROFILE 'Service_IT_GitHub_Repo\Service-IT-LeadGen'
    Set-Location -LiteralPath $repo
    $requiredBranch = 'feat/sme-vacancy-demand-audit-20261031'
    $current = (& git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $current -ne $requiredBranch) {
        throw 'WRONG_BRANCH'
    }
    $python = Join-Path $repo '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw 'VENV_PYTHON_MISSING' }

    $fns = 'exports/sme_first_moscow_50.json'
    $page0 = 'exports/round2_offset0'
    $page1 = 'exports/round2_offset1'
    $output = 'exports/sme_moscow_50_demand_audit.json'
    $csv = 'exports/sme_moscow_50_demand_audit.csv'

    if (-not (Test-Path -LiteralPath $fns)) { throw 'VERIFIED_SME_50_FILE_MISSING' }
    foreach ($folder in @($page0, $page1)) {
        foreach ($code in @('77', '50')) {
            if (-not (Test-Path -LiteralPath "$folder/trudvsem_$code.json")) {
                throw "VACANCY_SNAPSHOT_MISSING=$folder/$code"
            }
        }
    }
    if ((Test-Path -LiteralPath $output) -or (Test-Path -LiteralPath $csv)) {
        throw 'OUTPUT_ALREADY_EXISTS: do not overwrite prior audit'
    }

    & $python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'PYTEST_FAILED' }

    & $python -m leadpilot.sme_vacancy_audit --fns $fns --folders $page0 $page1 --offsets 0 1 --output $output --csv $csv
    if ($LASTEXITCODE -ne 0) { throw 'SME_VACANCY_AUDIT_FAILED' }
    if (-not ((Test-Path -LiteralPath $output) -and
              (Test-Path -LiteralPath $csv))) {
        throw 'EXPECTED_REPORT_FILES_MISSING'
    }
    $report = Get-Content -LiteralPath $output -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($report.mode -ne 'SME_FIRST_OFFLINE_VACANCY_AUDIT' -or
        $report.counts.fns_size_verified_companies -ne 50 -or
        $report.counts.crm_ready -ne 0 -or
        $report.crm_writes -ne 'NO') {
        throw 'INVALID_OR_UNSAFE_AUDIT_RESULT'
    }
    Write-Host "SME_VACANCY_AUDIT=SUCCESS; VERIFIED_SME=50; VACANCY_MATCHED=$($report.counts.companies_with_1c_vacancy_signal); CRM_WRITES=NO" -ForegroundColor Green
    exit 0
}
catch {
    Write-Host "SME_VACANCY_AUDIT=FAILED; DETAILS=$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
