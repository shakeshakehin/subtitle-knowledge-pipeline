$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ReportAgentRoot = Join-Path (Split-Path -Parent $ProjectRoot) "video-report-agent"

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# Reuse optional local runtimes installed by video-report-agent while still
# allowing globally installed Node.js and Pi to work.
$RuntimePaths = @()
$PiRoot = Join-Path $ReportAgentRoot ".local\pi"
if (Test-Path -LiteralPath $PiRoot) {
    $RuntimePaths += $PiRoot
}
$NodeRoot = Join-Path $ReportAgentRoot ".local\node"
if (Test-Path -LiteralPath $NodeRoot) {
    $NodeRuntime = Get-ChildItem -LiteralPath $NodeRoot -Directory |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "node.exe") } |
        Select-Object -First 1
    if ($NodeRuntime) {
        $RuntimePaths += $NodeRuntime.FullName
    }
}
if ($RuntimePaths.Count -gt 0) {
    $env:PATH = (($RuntimePaths + $env:PATH) -join ";")
}

$HermesEnv = if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA "hermes\profiles\manager\.env" } else { $null }
if ((-not $env:NOTE_API_KEY -or -not $env:REPORT_API_KEY) -and $HermesEnv -and (Test-Path -LiteralPath $HermesEnv)) {
    $KeyLine = Get-Content -LiteralPath $HermesEnv | Where-Object { $_ -match '^DEEPSEEK_API_KEY=' } | Select-Object -First 1
    if ($KeyLine) {
        $DeepSeekKey = $KeyLine.Substring($KeyLine.IndexOf('=') + 1).Trim().Trim('"').Trim("'")
        if (-not $env:NOTE_API_KEY) { $env:NOTE_API_KEY = $DeepSeekKey }
        if (-not $env:REPORT_API_KEY) { $env:REPORT_API_KEY = $DeepSeekKey }
    }
}

& (Join-Path $ProjectRoot ".venv\Scripts\fusion-video.exe") @args
exit $LASTEXITCODE
