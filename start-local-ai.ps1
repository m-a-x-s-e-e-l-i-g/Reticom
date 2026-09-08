param([string]$Model = 'qwen3:4b-instruct')
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    docker compose up -d language-model
    if ($LASTEXITCODE -ne 0) { throw 'Could not start the local language model service.' }
    $deadline = (Get-Date).AddSeconds(60)
    do {
        try { $null = Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2; break }
        catch { if ((Get-Date) -gt $deadline) { throw }; Start-Sleep -Seconds 1 }
    } while ($true)
    Write-Output "Preparing $Model locally. The first download is approximately 2.5 GB."
    $body = @{model = $Model; stream = $false} | ConvertTo-Json
    $result = Invoke-RestMethod 'http://127.0.0.1:11434/api/pull' -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 1800
    if ($result.status -ne 'success') { throw 'The local model download did not finish.' }
    Write-Output 'Local AI is ready for Reticom Command.'
} finally { Pop-Location }
