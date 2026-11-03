# Shared ServiceIT Node.js bootstrap for Windows PowerShell 5.1.
# Official portable LTS is SHA256 checked and needs no administrator.
function Test-ServiceITNodeDir([string]$Directory) {
    if ([string]::IsNullOrWhiteSpace($Directory)) { return $null }
    $Executable = Join-Path $Directory 'node.exe'
    $Npm = Join-Path $Directory 'npm.cmd'
    $Npx = Join-Path $Directory 'npx.cmd'
    if (-not ((Test-Path -LiteralPath $Executable -PathType Leaf) -and
              (Test-Path -LiteralPath $Npm -PathType Leaf) -and
              (Test-Path -LiteralPath $Npx -PathType Leaf))) { return $null }
    $Version = (& $Executable --version 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $Version -notmatch '^v([0-9]+)\.[0-9]+\.[0-9]+$') { return $null }
    if ([int]$Matches[1] -lt 22) { return $null }
    $env:Path = $Directory + [IO.Path]::PathSeparator + $env:Path
    return (Get-Command node.exe -ErrorAction Stop)
}

function Ensure-ServiceITNode {
    $Already = Get-Command node.exe -ErrorAction SilentlyContinue
    $Npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    $Npx = Get-Command npx.cmd -ErrorAction SilentlyContinue
    if ($Already -and $Npm -and $Npx) {
        $Existing = Test-ServiceITNodeDir (Split-Path -Parent $Already.Source)
        if ($Existing) { Write-Host 'SERVICEIT_NODE=EXISTING_LTS'; return $Existing }
    }
    $Root = Join-Path $env:LOCALAPPDATA 'ServiceIT\Tools\NodeJS-LTS'
    $Candidates = @((Join-Path $env:ProgramFiles 'nodejs'), (Join-Path $env:LOCALAPPDATA 'Programs\nodejs'))
    $ProgramFiles86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
    if ($ProgramFiles86) { $Candidates += (Join-Path $ProgramFiles86 'nodejs') }
    if (Test-Path -LiteralPath $Root -PathType Container) {
        $Candidates += @(Get-ChildItem -LiteralPath $Root -Directory -ErrorAction SilentlyContinue |
                         Sort-Object Name -Descending | ForEach-Object { $_.FullName })
    }
    foreach ($Dir in $Candidates) {
        $Found = Test-ServiceITNodeDir $Dir
        if ($Found) { Write-Host 'SERVICEIT_NODE=FOUND_INSTALLED_OR_CACHED'; return $Found }
    }
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw 'SERVICEIT_NODE_BOOTSTRAP_REQUIRES_64BIT_WINDOWS'
    }
    $Temporary = Join-Path ([IO.Path]::GetTempPath()) ('serviceit-node-' + [guid]::NewGuid().ToString('N'))
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $Releases = @(Invoke-RestMethod -Uri 'https://nodejs.org/dist/index.json' -UseBasicParsing -TimeoutSec 35 -ErrorAction Stop)
        $Chosen = @($Releases | Where-Object {
            $_.version -match '^v[0-9]+\.[0-9]+\.[0-9]+$' -and
            $_.lts -is [string] -and
            @($_.files) -contains 'win-x64-zip'
        } | Select-Object -First 1)
        if ($Chosen.Count -ne 1) { throw 'OFFICIAL_LTS_RELEASE_NOT_FOUND' }
        $Version = [string]$Chosen[0].version
        $Base = 'https://nodejs.org/dist/' + $Version + '/'
        $ArchiveName = 'node-' + $Version + '-win-x64.zip'
        $DirectoryName = 'node-' + $Version + '-win-x64'
        $Target = Join-Path $Root $DirectoryName
        if (Test-Path -LiteralPath $Target -PathType Container) {
            $Cached = Test-ServiceITNodeDir $Target
            if ($Cached) { Write-Host 'SERVICEIT_NODE=CACHED_OFFICIAL_LTS'; return $Cached }
            throw 'SERVICEIT_NODE_CACHED_DIRECTORY_INVALID'
        }
        New-Item -ItemType Directory -Path $Temporary -Force | Out-Null
        $Archive = Join-Path $Temporary $ArchiveName
        $Hashes = Join-Path $Temporary 'SHASUMS256.txt'
        Invoke-WebRequest -Uri ($Base + 'SHASUMS256.txt') -OutFile $Hashes -UseBasicParsing -TimeoutSec 45 -ErrorAction Stop
        Invoke-WebRequest -Uri ($Base + $ArchiveName) -OutFile $Archive -UseBasicParsing -TimeoutSec 180 -ErrorAction Stop
        $Pattern = '^([A-Fa-f0-9]{64})\s+\*?' + [regex]::Escape($ArchiveName) + '$'
        $Matched = @(Get-Content -LiteralPath $Hashes -Encoding UTF8 | Where-Object { $_ -match $Pattern })
        if ($Matched.Count -ne 1) { throw 'OFFICIAL_CHECKSUM_ENTRY_INVALID' }
        $Expected = [regex]::Match($Matched[0], $Pattern).Groups[1].Value.ToLowerInvariant()
        $Actual = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($Actual -cne $Expected) { throw 'OFFICIAL_NODE_SHA256_MISMATCH' }
        $Expanded = Join-Path $Temporary 'expanded'
        Expand-Archive -LiteralPath $Archive -DestinationPath $Expanded -Force -ErrorAction Stop
        $Source = Join-Path $Expanded $DirectoryName
        if (-not ((Test-Path -LiteralPath (Join-Path $Source 'node.exe') -PathType Leaf) -and
                  (Test-Path -LiteralPath (Join-Path $Source 'npm.cmd') -PathType Leaf) -and
                  (Test-Path -LiteralPath (Join-Path $Source 'npx.cmd') -PathType Leaf))) {
            throw 'OFFICIAL_PORTABLE_NODE_INCOMPLETE'
        }
        New-Item -ItemType Directory -Path $Root -Force | Out-Null
        Move-Item -LiteralPath $Source -Destination $Target -ErrorAction Stop
        $Installed = Test-ServiceITNodeDir $Target
        if (-not $Installed) { throw 'OFFICIAL_PORTABLE_NODE_VALIDATION_FAILED' }
        Write-Host 'SERVICEIT_NODE=OFFICIAL_PORTABLE_LTS_SHA256_OK'
        return $Installed
    }
    catch {
        Write-Host 'SERVICEIT_NODE_OFFICIAL_PORTABLE=UNAVAILABLE_TRY_WINGET'
    }
    finally {
        if (Test-Path -LiteralPath $Temporary) {
            Remove-Item -LiteralPath $Temporary -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($Winget) {
        & $Winget.Source install --id OpenJS.NodeJS.LTS --exact --source winget --accept-source-agreements --accept-package-agreements --silent
        if ($LASTEXITCODE -eq 0) {
            foreach ($Dir in @((Join-Path $env:ProgramFiles 'nodejs'), (Join-Path $env:LOCALAPPDATA 'Programs\nodejs'))) {
                $Installed = Test-ServiceITNodeDir $Dir
                if ($Installed) { Write-Host 'SERVICEIT_NODE=WINGET_LTS'; return $Installed }
            }
        }
    }
    throw 'SERVICEIT_NODE_BOOTSTRAP_FAILED_OFFICIAL_AND_WINGET'
}
