#Requires -Version 5.1
# ASCII-only source so Windows PowerShell 5.1 does not corrupt Cyrillic filenames.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

try {
    $repo = Join-Path $env:USERPROFILE 'Service_IT_GitHub_Repo\Service-IT-LeadGen'
    Set-Location -LiteralPath $repo

    $expectedBranch = 'feat/sme-first-fns-discovery-20261031'
    $currentBranch = (& git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $currentBranch -ne $expectedBranch) {
        throw "WRONG_GIT_BRANCH"
    }

    $py = Join-Path $repo '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $py)) { throw "LOCAL_PYTHON_MISSING" }
    $downloads = Join-Path $env:USERPROFILE 'Downloads'
    if (-not (Test-Path -LiteralPath $downloads)) { throw "DOWNLOADS_FOLDER_MISSING" }

    # A browser may append (1), (2), etc. Match the distinctive ASCII marker.
    $matches = @(
        Get-ChildItem -LiteralPath $downloads -File -Filter '*.xlsx' |
        Where-Object { $_.Name -like '*77_50_*' } |
        Sort-Object LastWriteTime -Descending
    )
    if ($matches.Count -eq 0) {
        Write-Host 'Recent XLSX files in Downloads:' -ForegroundColor Yellow
        Get-ChildItem -LiteralPath $downloads -File -Filter '*.xlsx' |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 7 Name, Length, LastWriteTime |
            Format-Table -AutoSize
        throw "FNS_XLSX_NOT_DOWNLOADED: save the small Moscow subset to Downloads, then retry"
    }

    $selected = $matches[0]
    if ($selected.Length -gt (12 * 1024 * 1024)) {
        throw "FNS_XLSX_TOO_LARGE: selected file is not the small Moscow subset"
    }
    if ($selected.Length -eq 0) { throw "FNS_XLSX_EMPTY_FILE" }
    Write-Host "FNS_XLSX_FOUND=$($selected.FullName)"
    Write-Host "FNS_XLSX_SIZE_BYTES=$($selected.Length)"

    $outputJson = 'exports/sme_first_moscow_50.json'
    $outputTxt = 'exports/sme_first_moscow_50_inns.txt'
    if ((Test-Path -LiteralPath $outputJson) -or (Test-Path -LiteralPath $outputTxt)) {
        throw 'SME_FIRST_OUTPUT_ALREADY_EXISTS: use new output names for repeat runs'
    }

    & $py -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "SME_FIRST_TESTS_FAILED" }

    & $py -m leadpilot.sme_first_discovery --xlsx $selected.FullName --limit 50 --output $outputJson --inn-list $outputTxt
    if ($LASTEXITCODE -ne 0) { throw "SME_FIRST_IMPORT_FAILED" }

    if ((Test-Path -LiteralPath $outputJson) -and (Test-Path -LiteralPath $outputTxt)) {
        $result = Get-Content -LiteralPath $outputJson -Raw -Encoding UTF8 | ConvertFrom-Json
        $count = @($result.companies).Count
        if ($count -lt 1 -or $count -gt 50) { throw "INVALID_CANDIDATE_COUNT" }
        Write-Host "SME_FIRST_IMPORT=SUCCESS; SIZE_ONLY_RESEARCH=$count; CRM_WRITES=NO" -ForegroundColor Green
    } elseif ((-not (Test-Path -LiteralPath $outputJson)) -and (-not (Test-Path -LiteralPath $outputTxt))) {
        Write-Host "SME_FIRST_IMPORT=NO_ELIGIBLE_COMPANIES; CRM_WRITES=NO" -ForegroundColor Yellow
    } else {
        throw "SME_FIRST_OUTPUT_PARTIAL"
    }
    exit 0
}
catch {
    Write-Host "SME_FIRST_IMPORT=FAILED; DETAILS=$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
