[CmdletBinding()]
param([string]$DataDirectory)

$ErrorActionPreference = 'Stop'
$OsrmRoot = Split-Path -Parent $PSScriptRoot
$DataDir = if ($DataDirectory) { $DataDirectory } else { Join-Path $OsrmRoot 'data' }
$Python = Join-Path $OsrmRoot '.venv\Scripts\python.exe'
$Dataset = Join-Path $DataDir 'malaysia-singapore-brunei-latest.osrm'
$ReadyFiles = @("$Dataset.properties", "$Dataset.partition", "$Dataset.cells", "$Dataset.mldgr", "$Dataset.cell_metrics")
if (@($ReadyFiles | Where-Object { Test-Path -LiteralPath $_ }).Count -ne $ReadyFiles.Count) {
    Write-Output 'OSRM dataset not found.'
    Write-Output 'Run setup-osrm.ps1 first.'
    exit 1
}
if (-not (Test-Path -LiteralPath $Python)) { throw 'OSRM Python environment not found. Run setup-osrm.ps1 first.' }

Push-Location $DataDir
try {
    & $Python -m osrm routed 'malaysia-singapore-brunei-latest.osrm' --algorithm mld --ip 127.0.0.1 --port 5000
    if ($LASTEXITCODE -ne 0) { throw 'OSRM routed service exited with an error.' }
} finally {
    Pop-Location
}
