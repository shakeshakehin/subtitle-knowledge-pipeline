$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$HermesEnv = if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA "hermes\profiles\manager\.env" } else { $null }
if (-not $env:NOTE_API_KEY -and $HermesEnv -and (Test-Path -LiteralPath $HermesEnv)) {
    $KeyLine = Get-Content -LiteralPath $HermesEnv | Where-Object { $_ -match '^DEEPSEEK_API_KEY=' } | Select-Object -First 1
    if ($KeyLine) {
        $env:NOTE_API_KEY = $KeyLine.Substring($KeyLine.IndexOf('=') + 1).Trim().Trim('"').Trim("'")
    }
}

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
& $Python -m fusion_video_pipeline.benchmark_cli @args
exit $LASTEXITCODE
