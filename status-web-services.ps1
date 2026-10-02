$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$StatePath = Join-Path $ProjectRoot ".runtime\services.json"
$state = if (Test-Path -LiteralPath $StatePath) {
    Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
} else {
    [pscustomobject]@{ tunnel_url = "" }
}

$rows = foreach ($service in @(
    @{ name = "fusion"; port = 8766 },
    @{ name = "benchmark"; port = 8765 }
)) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $service.port -ErrorAction SilentlyContinue |
        Select-Object -First 1
    [pscustomobject]@{
        service = $service.name
        port = $service.port
        running = [bool]$listener
        pid = if ($listener) { $listener.OwningProcess } else { $null }
    }
}
$rows | Format-Table -AutoSize

$tunnel = Get-CimInstance Win32_Process -Filter "Name = 'cloudflared.exe'" |
    Where-Object { $_.CommandLine -match '127\.0\.0\.1:8766' } |
    Select-Object -First 1
Write-Output "tunnel_running=$([bool]$tunnel)"
if ($state.tunnel_url) { Write-Output "tunnel_url=$($state.tunnel_url)" }
