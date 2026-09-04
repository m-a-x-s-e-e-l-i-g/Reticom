$ErrorActionPreference = 'Stop'
$saved = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime\pids.json') -Raw | ConvertFrom-Json
$fieldUri = "http://127.0.0.1:$($saved.fieldPort)"
$commandUri = "http://127.0.0.1:$($saved.commandPort)"
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$body = @{
    type = 'chat.message'
    callsign = 'CODEX-SMOKE'
    message = "Live Reticulum packet $stamp"
} | ConvertTo-Json

$sent = Invoke-RestMethod -Uri "$fieldUri/api/send" -Method Post -ContentType 'application/json' -Body $body
$deadline = (Get-Date).AddSeconds(15)
$received = $null
while ((Get-Date) -lt $deadline) {
    $state = Invoke-RestMethod -Uri "$commandUri/api/state"
    $received = $state.events | Where-Object { $_.id -eq $sent.event.id } | Select-Object -First 1
    if ($received) { break }
    Start-Sleep -Milliseconds 250
}
if (-not $received) { throw 'Command did not receive the Reticulum event.' }

[pscustomobject]@{
    eventId = $sent.event.id
    proof = $sent.delivery.status
    roundTripMs = $sent.delivery.rtt_ms
    bytes = $sent.delivery.packet_bytes
    senderIdentity = $received.network.sender_hash
    packetHash = $received.network.packet_hash
    receivingInterface = $received.network.interface
    persistedByCommand = $true
} | ConvertTo-Json
