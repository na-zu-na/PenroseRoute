[CmdletBinding()]
param([string]$BaseUrl = 'http://localhost:5000')

$ErrorActionPreference = 'Stop'
$BaseUrl = $BaseUrl.TrimEnd('/')
$RouteUrl = "$BaseUrl/route/v1/driving/103.6819,1.3483;103.8590,1.2830?overview=full&geometries=geojson"
$TableUrl = "$BaseUrl/table/v1/driving/103.6819,1.3483;103.8000,1.3000;103.8590,1.2830?annotations=distance,duration"

$Route = Invoke-RestMethod -Uri $RouteUrl -TimeoutSec 20
if ($Route.code -ne 'Ok' -or -not $Route.routes -or $null -eq $Route.routes[0].distance -or
    $null -eq $Route.routes[0].duration -or -not $Route.routes[0].geometry) {
    throw 'OSRM Route API returned an incomplete response.'
}
Write-Output '[PASS] OSRM Route API'
Write-Output "Distance: $([Math]::Round($Route.routes[0].distance / 1000, 2)) km"
Write-Output "Duration: $([Math]::Round($Route.routes[0].duration / 60, 2)) min"

$Table = Invoke-RestMethod -Uri $TableUrl -TimeoutSec 20
if ($Table.code -ne 'Ok' -or $Table.durations.Count -ne 3 -or $Table.distances.Count -ne 3 -or
    @($Table.durations | Where-Object { $_.Count -ne 3 }).Count -gt 0 -or
    @($Table.distances | Where-Object { $_.Count -ne 3 }).Count -gt 0) {
    throw 'OSRM Table API returned an incomplete 3 x 3 matrix.'
}
Write-Output '[PASS] OSRM Table API'
Write-Output 'Matrix size: 3 x 3'
