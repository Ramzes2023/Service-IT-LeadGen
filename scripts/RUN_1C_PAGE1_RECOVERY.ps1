#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

try {
    $repo = Join-Path $env:USERPROFILE 'Service_IT_GitHub_Repo\Service-IT-LeadGen'
    Set-Location -LiteralPath $repo
    $branch = 'feat/1c-multipage-dedupe-fns-20261031'
    $current = (& git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $current -ne $branch) {
        throw "WRONG_BRANCH: expected $branch"
    }

    $py = Join-Path $repo '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $py)) { throw 'LOCAL_PYTHON_MISSING' }
    & $py -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'TESTS_FAILED_NO_NETWORK_REQUESTS' }

    foreach ($code in @('77', '50')) {
        $previous = Join-Path $repo "exports\round2_offset0\trudvsem_$code.json"
        if (-not (Test-Path -LiteralPath $previous)) {
            throw "PREVIOUS_PAGE0_MISSING_FOR_REGION_$code"
        }
    }

    # ASCII-only source is required for Windows PowerShell 5.1 (UTF-8 without BOM).
    # Build Cyrillic CAPITAL ES (U+0421) by code point, not as source text.
    $term = '1' + [char]0x0421
    if ($term.Length -ne 2 -or [int][char]$term[1] -ne 0x0421) {
        throw 'QUERY_TERM_ENCODING_INVALID'
    }

    $next = 'exports/round2_offset1'
    foreach ($region in @('77','50')) {
        $file = "$next/trudvsem_$region.json"
        if (Test-Path -LiteralPath $file) {
            Write-Host "PAGE1_REUSING_LOCAL_FILE_REGION=$region" -ForegroundColor Yellow
            continue
        }
        Write-Host "PAGE1_REQUEST_REGION=$region; PAGE_INDEX=1; LIMIT=50"
        & $py -m leadpilot.cli --db data/research.sqlite3 source-scan --region $region --term $term --limit 50 --offset 1 --output $file
        if ($LASTEXITCODE -ne 0) {
            throw "SOURCE_SCAN_FAILED_REGION_$region; NO_NEW_RECORDS_CLAIMED"
        }
    }

    $outJson = 'exports/round2_combined_precheck.json'
    $outTxt = 'exports/round2_combined_inns.txt'
    if ((Test-Path -LiteralPath $outJson) -or (Test-Path -LiteralPath $outTxt)) {
        throw 'COMBINED_OUTPUT_ALREADY_EXISTS_USE_NEW_FILENAMES'
    }

    & $py -m leadpilot.multi_page_precheck --folders exports/round2_offset0 exports/round2_offset1 --offsets 0 1 --prior-fns exports/fns_official_reconciliation.json --output $outJson --inn-list $outTxt
    if ($LASTEXITCODE -ne 0) { throw 'MULTIPAGE_VALIDATION_FAILED' }

    if (Test-Path -LiteralPath $outTxt) {
        $numbers = @(Get-Content -LiteralPath $outTxt -Encoding UTF8 | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        if ($numbers.Count -eq 0) { throw 'UNEXPECTED_EMPTY_INN_LIST' }
        Get-Content -LiteralPath $outTxt -Raw -Encoding UTF8 | Set-Clipboard
        Write-Host "MULTIPAGE_FNS_BATCH=READY; NEW_INNS=$($numbers.Count); CLIPBOARD=YES" -ForegroundColor Green
    } else {
        Write-Host 'MULTIPAGE_FNS_BATCH=NO_NEW_INNS; NO_FILE_CREATED; CRM_WRITES=NO' -ForegroundColor Yellow
    }
    exit 0
}
catch {
    Write-Host "MULTIPAGE_FNS_BATCH=FAILED; DETAILS=$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
