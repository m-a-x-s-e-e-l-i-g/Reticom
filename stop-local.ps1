$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot 'runtime\pids.json'
if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Output 'No Reticom process file found.'
    exit 0
}

$saved = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
foreach ($processId in @($saved.field, $saved.gateway, $saved.fieldLauncher, $saved.gatewayLauncher)) {
    if (-not $processId) { continue }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    $isRetium = $process -and $process.CommandLine -like "*$PSScriptRoot*" -and $process.CommandLine -like '*-m retium.server*'
    if ($isRetium) {
        Stop-Process -Id $processId -ErrorAction SilentlyContinue
        Write-Output "Stopped Reticom process $processId"
    }
}
