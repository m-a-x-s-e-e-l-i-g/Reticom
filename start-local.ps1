param(
    [int]$CommandPort = 8780,
    [int]$FieldPort = 8781
)

$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$python = Join-Path $project '.venv\Scripts\python.exe'
$runtime = Join-Path $project 'runtime'
$logs = Join-Path $runtime 'logs'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Virtual environment missing. Run: py -m venv .venv; .\.venv\Scripts\python -m pip install -e .'
}

New-Item -ItemType Directory -Force -Path $logs | Out-Null

foreach ($port in @($CommandPort, $FieldPort)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use. Stop the existing process or select another port."
    }
}
if (Get-NetTCPConnection -State Listen -LocalAddress '127.0.0.1' -LocalPort 4242 -ErrorAction SilentlyContinue) {
    throw 'Local Reticulum carrier port 127.0.0.1:4242 is already in use.'
}

$gatewayArgs = @(
    '-m', 'retium.server', '--role', 'gateway',
    '--config', (Join-Path $project 'configs\gateway'),
    '--data', (Join-Path $runtime 'gateway'),
    '--port', "$CommandPort"
)
$gateway = Start-Process -FilePath $python -ArgumentList $gatewayArgs -WorkingDirectory $project -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logs 'gateway.stdout.log') -RedirectStandardError (Join-Path $logs 'gateway.stderr.log')

$gatewayState = Join-Path $runtime 'gateway\gateway.json'
$deadline = (Get-Date).AddSeconds(25)
while ((Get-Date) -lt $deadline) {
    if ((Test-Path -LiteralPath $gatewayState) -and (Get-NetTCPConnection -State Listen -LocalPort $CommandPort -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 250
}
if (-not (Test-Path -LiteralPath $gatewayState)) {
    throw "Gateway did not start. See $logs\gateway.stderr.log"
}

$fieldArgs = @(
    '-m', 'retium.server', '--role', 'field',
    '--config', (Join-Path $project 'configs\field'),
    '--data', (Join-Path $runtime 'field'),
    '--gateway-file', $gatewayState,
    '--port', "$FieldPort"
)
$field = Start-Process -FilePath $python -ArgumentList $fieldArgs -WorkingDirectory $project -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logs 'field.stdout.log') -RedirectStandardError (Join-Path $logs 'field.stderr.log')

$deadline = (Get-Date).AddSeconds(25)
while ((Get-Date) -lt $deadline) {
    if (Get-NetTCPConnection -State Listen -LocalPort $FieldPort -ErrorAction SilentlyContinue) { break }
    Start-Sleep -Milliseconds 250
}
if (-not (Get-NetTCPConnection -State Listen -LocalPort $FieldPort -ErrorAction SilentlyContinue)) {
    throw "Field node did not start. See $logs\field.stderr.log"
}

[pscustomobject]@{
    gateway = (Get-NetTCPConnection -State Listen -LocalPort $CommandPort).OwningProcess
    field = (Get-NetTCPConnection -State Listen -LocalPort $FieldPort).OwningProcess
    gatewayLauncher = $gateway.Id
    fieldLauncher = $field.Id
    commandPort = $CommandPort
    fieldPort = $FieldPort
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtime 'pids.json') -Encoding utf8

Write-Output "Command: http://127.0.0.1:$CommandPort"
Write-Output "Field:   http://127.0.0.1:$FieldPort"
Write-Output "Reticulum TCP carrier: 127.0.0.1:4242"
