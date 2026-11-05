# Runs "saihatsu weekly" and appends the output to a local log.
# Used by Windows Task Scheduler. Keep this file ASCII only (Windows PowerShell 5.1 misreads UTF-8 without BOM).
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$logDir = Join-Path $env:LOCALAPPDATA "saihatsu\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ((Get-Date).ToString("yyyyMMdd-HHmm") + ".log")
$env:PYTHONIOENCODING = "utf-8"
# cmd writes the raw UTF-8 bytes of python to the file as they are
cmd /c "python -m saihatsu.cli weekly >> `"$log`" 2>&1"
$code = $LASTEXITCODE
"exit code: $code" | Add-Content -Path $log -Encoding ascii
exit $code
