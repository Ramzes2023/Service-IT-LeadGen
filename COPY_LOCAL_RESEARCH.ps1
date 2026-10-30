# Copy the already collected public-source research database locally. Never stage or push it.
$ErrorActionPreference = 'Stop'
$source = Join-Path $HOME 'Service_IT_LeadGen_V03\Service_IT_LeadGen_V03'
$dest = $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $source 'data\research.sqlite3'))) { throw 'OLD_RESEARCH_DB_NOT_FOUND' }
New-Item -ItemType Directory -Path (Join-Path $dest 'data'),(Join-Path $dest 'exports') -Force | Out-Null
if (Test-Path (Join-Path $dest 'data\research.sqlite3')) { throw 'DEST_RESEARCH_DB_EXISTS: no overwrite allowed' }
Copy-Item -LiteralPath (Join-Path $source 'data\research.sqlite3') -Destination (Join-Path $dest 'data\research.sqlite3')
foreach ($region in @('77','50')) {
    $candidate = Join-Path $source ('exports\trudvsem_'+$region+'.json')
    if (Test-Path -LiteralPath $candidate) { Copy-Item -LiteralPath $candidate -Destination (Join-Path $dest 'exports') }
}
Write-Host 'LOCAL_RESEARCH_COPIED=YES; GITHUB_UPLOAD=NO; VALYQON_CHANGES=NONE' -ForegroundColor Green
