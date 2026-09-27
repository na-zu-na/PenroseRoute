[CmdletBinding()]
param([switch]$Force)

$ErrorActionPreference = 'Stop'
$OsrmRoot = Split-Path -Parent $PSScriptRoot
$DataDir = Join-Path $OsrmRoot 'data'
$VenvDir = Join-Path $OsrmRoot '.venv'
$Python = Join-Path $VenvDir 'Scripts\python.exe'
$BaseName = 'malaysia-singapore-brunei-latest'
$Pbf = Join-Path $DataDir "$BaseName.osm.pbf"
$Dataset = Join-Path $DataDir "$BaseName.osrm"
$ReadyFiles = @("$Dataset.properties", "$Dataset.partition", "$Dataset.cells", "$Dataset.mldgr", "$Dataset.cell_metrics")

if (-not (Test-Path -LiteralPath $Python)) {
    $UseLauncher = $false
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $AvailablePython = & py -0p
        $UseLauncher = ($LASTEXITCODE -eq 0 -and ($AvailablePython -match '-V:3\.12'))
    }
    if (-not $UseLauncher) {
        if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
            throw "OSRM Windows wheel requires Python 3.12+ x86_64. Please install Python 3.12 or newer first."
        }
        $PythonSupported = & python -c "import sys,platform; print(sys.version_info >= (3,12) and platform.machine().lower() in ('amd64','x86_64'))"
        if ($LASTEXITCODE -ne 0 -or $PythonSupported -ne 'True') {
            throw "OSRM Windows wheel requires Python 3.12+ x86_64. Please install Python 3.12 or newer first."
        }
    }
    if ($UseLauncher) { & py -3.12 -m venv $VenvDir }
    else { & python -m venv $VenvDir }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the independent OSRM Python environment.' }
}

$VenvPythonSupported = & $Python -c "import sys,platform; print(sys.version_info >= (3,12) and platform.machine().lower() in ('amd64','x86_64'))"
if ($LASTEXITCODE -ne 0 -or $VenvPythonSupported -ne 'True') { throw 'The existing OSRM environment is not 64-bit Python 3.12+.' }
$HasOsrm = & $Python -c "import importlib.util; print(importlib.util.find_spec('osrm') is not None)"
if ($LASTEXITCODE -ne 0 -or $HasOsrm -ne 'True') {
    & $Python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'Could not upgrade pip in the OSRM environment.' }
    # Binary-only prevents an accidental OSRM C++ source build on Windows.
    & $Python -m pip install --only-binary=:all: 'osrm-bindings==0.3.0'
    if ($LASTEXITCODE -ne 0) { throw 'No compatible prebuilt osrm-bindings wheel could be installed.' }
}

& $Python -m osrm extract --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'The installed osrm-bindings package has no working OSRM CLI.' }
$ProfileRelative = (& $Python -c "import osrm,pathlib,sys; p=pathlib.Path(osrm.__file__).parent; profiles=(p/'share'/'profiles'/'car.lua',p.parent/'share'/'osrm'/'profiles'/'car.lua'); print(next(c.relative_to(sys.prefix) for c in profiles if c.is_file()))").Trim()
$CarProfile = Join-Path $VenvDir $ProfileRelative
if (-not (Test-Path -LiteralPath $CarProfile)) { throw "OSRM car.lua profile not found: $CarProfile" }

if (-not (Test-Path -LiteralPath $DataDir)) { New-Item -ItemType Directory -Path $DataDir | Out-Null }
$Prepared = @($ReadyFiles | Where-Object { Test-Path -LiteralPath $_ }).Count -eq $ReadyFiles.Count
if ($Prepared -and -not $Force) {
    Write-Output 'OSRM dataset already prepared. Skipping preprocessing.'
    exit 0
}

if (-not (Test-Path -LiteralPath $Pbf) -or (Get-Item -LiteralPath $Pbf).Length -eq 0) {
    $Partial = "$Pbf.part"
    & curl.exe --fail --location --retry 2 'https://download.geofabrik.de/asia/malaysia-singapore-brunei-latest.osm.pbf' --output $Partial
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Partial) -or (Get-Item -LiteralPath $Partial).Length -eq 0) {
        throw 'OSM download failed; a partial file may remain in the data directory.'
    }
    Move-Item -LiteralPath $Partial -Destination $Pbf -Force
}

Push-Location $DataDir
try {
    & $Python -m osrm extract "$BaseName.osm.pbf" -p $CarProfile
    if ($LASTEXITCODE -ne 0) { throw 'OSRM extract failed.' }
    & $Python -m osrm partition "$BaseName.osrm"
    if ($LASTEXITCODE -ne 0) { throw 'OSRM partition failed.' }
    & $Python -m osrm customize "$BaseName.osrm"
    if ($LASTEXITCODE -ne 0) { throw 'OSRM customize failed.' }
} finally {
    Pop-Location
}
if (@($ReadyFiles | Where-Object { Test-Path -LiteralPath $_ }).Count -ne $ReadyFiles.Count) {
    throw 'OSRM preprocessing finished without all required MLD files.'
}
Write-Output 'OSRM dataset prepared for MLD routing.'
