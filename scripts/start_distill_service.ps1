<#
.SYNOPSIS
    Launch the host YouTube-distill HTTP service (tools/distill_service.py).
.DESCRIPTION
    Idempotent: skips if port already serving. Loads KCS_CASCADE_TOKEN from the
    kimiclaw .env so the service shares the federation cascade token (the clawfish
    bridge presents the same token). Background launch via pythonw (no console).
    Mirrors kimiclaw/scripts/start_oauth_proxy.ps1.
.PARAMETER Port
    Override KCS_DISTILL_PORT (default 9789).
#>
param(
    [int]$Port = 9789,
    [switch]$Background
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
Set-Location -Path $repoRoot

# Idempotency: if the port is already serving, do nothing.
$alreadyUp = $false
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $iar = $tcp.BeginConnect('127.0.0.1', $Port, $null, $null)
    if ($iar.AsyncWaitHandle.WaitOne(1000, $false) -and $tcp.Connected) { $alreadyUp = $true }
    $tcp.Close()
} catch { $alreadyUp = $false }
if ($alreadyUp) {
    Write-Host "distill_service already listening on 127.0.0.1:$Port - nothing to do." -ForegroundColor DarkGray
    return
}

# Share the cascade token with the service (it also falls back to reading this file).
$envFile = 'C:\Projects\_Jobs\Collaborations\Andrew\kimiclaw\repo\.env'
if (Test-Path $envFile) {
    $line = (Get-Content $envFile | Where-Object { $_ -match '^KCS_CASCADE_TOKEN=' }) | Select-Object -First 1
    if ($line) { $env:KCS_CASCADE_TOKEN = ($line -replace '^KCS_CASCADE_TOKEN=', '').Trim('"', "'", ' ') }
}
$env:KCS_DISTILL_PORT = $Port

Write-Host "Starting distill_service on 127.0.0.1:$Port" -ForegroundColor Green
Write-Host "  Health: curl http://127.0.0.1:$Port/healthz" -ForegroundColor DarkGray

$logPath = Join-Path $repoRoot '.distill_service.log'
$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonw) { $pythonw = 'python' }
Start-Process -FilePath $pythonw `
    -ArgumentList 'tools/distill_service.py' `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $logPath `
    -RedirectStandardError "$logPath.err" `
    -WindowStyle Hidden
Write-Host "  Background PID started (pythonw, hidden); log: $logPath" -ForegroundColor Green
