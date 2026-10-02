$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeRoot = Join-Path $ProjectRoot ".runtime"
New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null

function Get-ListenerProcessId([int]$Port) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($listener) { return [int]$listener.OwningProcess }
    return $null
}

function Start-HiddenPowerShell(
    [string]$Name,
    [string]$Script,
    [string[]]$Arguments,
    [int]$Port
) {
    $existing = Get-ListenerProcessId $Port
    if ($existing) { return $existing }
    $stdout = Join-Path $RuntimeRoot "$Name.stdout.log"
    $stderr = Join-Path $RuntimeRoot "$Name.stderr.log"
    $pwsh = (Get-Command pwsh -ErrorAction Stop).Source
    $argumentList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $Script) + $Arguments
    $process = Start-Process -FilePath $pwsh -ArgumentList $argumentList `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    foreach ($attempt in 1..40) {
        Start-Sleep -Milliseconds 250
        $listener = Get-ListenerProcessId $Port
        if ($listener) { return $listener }
        if ($process.HasExited) {
            $detail = Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue
            throw "$Name 启动失败：$detail"
        }
    }
    throw "$Name 未在端口 $Port 开始监听"
}

$fusionPid = Start-HiddenPowerShell -Name "fusion" -Script (Join-Path $ProjectRoot "run-fusion.ps1") `
    -Arguments @("serve", "--host", "127.0.0.1", "--port", "8766", "--no-browser", "--public-mode") `
    -Port 8766
$benchmarkPid = Start-HiddenPowerShell -Name "benchmark" -Script (Join-Path $ProjectRoot "run-benchmark.ps1") `
    -Arguments @("serve", "--host", "127.0.0.1", "--port", "8765", "--no-open") -Port 8765

$cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cloudflared) {
    $fallback = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
    if (Test-Path -LiteralPath $fallback) { $cloudflared = Get-Item -LiteralPath $fallback }
}
if (-not $cloudflared) { throw "未找到 cloudflared；请先安装 Cloudflare.cloudflared" }
$cloudflaredPath = if ($cloudflared.Source) { $cloudflared.Source } else { $cloudflared.FullName }

$tunnelProcess = Get-CimInstance Win32_Process -Filter "Name = 'cloudflared.exe'" |
    Where-Object { $_.CommandLine -match '127\.0\.0\.1:8766' } |
    Select-Object -First 1
$tunnelStdout = Join-Path $RuntimeRoot "tunnel.stdout.log"
$tunnelStderr = Join-Path $RuntimeRoot "tunnel.stderr.log"
if (-not $tunnelProcess) {
    $startedTunnel = Start-Process -FilePath $cloudflaredPath `
        -ArgumentList @("tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8766") `
        -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $tunnelStdout -RedirectStandardError $tunnelStderr
    $tunnelPid = $startedTunnel.Id
} else {
    $tunnelPid = [int]$tunnelProcess.ProcessId
}

$url = ""
foreach ($attempt in 1..60) {
    $logs = @()
    foreach ($log in ($tunnelStdout, $tunnelStderr)) {
        if (Test-Path -LiteralPath $log) {
            $logs += Get-Content -LiteralPath $log -Raw -ErrorAction SilentlyContinue
        }
    }
    $match = [regex]::Match(($logs -join "`n"), 'https://[a-z0-9-]+\.trycloudflare\.com')
    if ($match.Success) { $url = $match.Value; break }
    Start-Sleep -Milliseconds 250
}

$state = [ordered]@{
    started_at = [DateTimeOffset]::Now.ToString("o")
    fusion_pid = $fusionPid
    benchmark_pid = $benchmarkPid
    tunnel_pid = $tunnelPid
    tunnel_url = $url
}
$state | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $RuntimeRoot "services.json") -Encoding utf8

Write-Output "管道本机：http://127.0.0.1:8766/"
Write-Output "评测本机：http://127.0.0.1:8765/"
if ($url) { Write-Output "外网预览：$url" } else { Write-Output "Tunnel 已启动；地址正在生成，请稍后运行 .\status-web-services.ps1" }
