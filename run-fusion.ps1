$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ReportAgentRoot = Join-Path (Split-Path -Parent $ProjectRoot) "video-report-agent"

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# Credential precedence is: current shell > project .env > Hermes fallback.
# Settings.load() reads .env inside Python; this check prevents the fallback
# from occupying the process environment first and silently masking that file.
$ProjectEnvPath = Join-Path $ProjectRoot ".env"
$ProjectEnvValues = @{}
if (Test-Path -LiteralPath $ProjectEnvPath) {
    foreach ($line in Get-Content -LiteralPath $ProjectEnvPath) {
        if ($line -match '^\s*([^#=]+)\s*=\s*(.*?)\s*$') {
            $ProjectEnvValues[$Matches[1].Trim()] = $Matches[2].Trim().Trim('"').Trim("'")
        }
    }
}

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
$NeedsNoteKey = -not $env:NOTE_API_KEY -and -not $ProjectEnvValues["NOTE_API_KEY"]
$NeedsReportKey = -not $env:REPORT_API_KEY -and -not $ProjectEnvValues["REPORT_API_KEY"]
if (($NeedsNoteKey -or $NeedsReportKey) -and $HermesEnv -and (Test-Path -LiteralPath $HermesEnv)) {
    $KeyLine = Get-Content -LiteralPath $HermesEnv | Where-Object { $_ -match '^DEEPSEEK_API_KEY=' } | Select-Object -First 1
    if ($KeyLine) {
        $DeepSeekKey = $KeyLine.Substring($KeyLine.IndexOf('=') + 1).Trim().Trim('"').Trim("'")
        if ($NeedsNoteKey) { $env:NOTE_API_KEY = $DeepSeekKey }
        if ($NeedsReportKey) { $env:REPORT_API_KEY = $DeepSeekKey }
    }
}

& (Join-Path $ProjectRoot ".venv\Scripts\fusion-video.exe") @args
exit $LASTEXITCODE
