$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$StatePath = Join-Path $ProjectRoot ".runtime\services.json"
if (-not (Test-Path -LiteralPath $StatePath)) {
    Write-Output "没有服务状态文件。"
    exit 0
}
$state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
foreach ($entry in @(
    @{ name = "fusion"; pid = $state.fusion_pid; marker = "fusion-video" },
    @{ name = "benchmark"; pid = $state.benchmark_pid; marker = "benchmark_cli" },
    @{ name = "tunnel"; pid = $state.tunnel_pid; marker = "cloudflared" }
)) {
    if (-not $entry.pid) { continue }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($entry.pid)" -ErrorAction SilentlyContinue
    if (-not $process) { continue }
    $description = "$($process.Name) $($process.CommandLine)"
    if ($description -notmatch [regex]::Escape($entry.marker)) {
        Write-Warning "跳过 PID $($entry.pid)：命令与 $($entry.name) 不匹配"
        continue
    }
    Stop-Process -Id $entry.pid -Force
    Write-Output "已停止 $($entry.name) (PID $($entry.pid))"
}
