<#
.SYNOPSIS
    Detect and auto-recover qwen3.6 llama-server zombie state.
.DESCRIPTION
    Background loop. Every PROBE_INTERVAL_S, probes both /health and /slots on
    127.0.0.1:7870. If /slots is unresponsive (timeout / non-200) for
    FAIL_THRESHOLD consecutive checks while /health stays ok, the dispatcher
    inside llama-server has hung (verified failure mode 2026-05-25/26: process
    alive, model in VRAM, accepts TCP, /health 200, but no log writes for 24h
    and every inference request hangs until 30-min subprocess timeout).
    Auto-runs scripts/restart_qwen36.bat to recover. Logs every probe + every
    restart to .qwen36_watchdog.log. Designed to run under pm2 so it survives
    reboot via pm2 startup + pm2 save.
.PARAMETER ProbeIntervalS
    Seconds between probes. Default 90.
.PARAMETER FailThreshold
    Consecutive /slots failures before triggering restart. Default 3.
    (90s * 3 = ~5 min of badness before action. Tight enough to keep distill
    requests from waiting 30 min on a corpse; loose enough not to bounce on
    legitimate long inference where /slots returns busy:true normally.)
#>
param(
    [int]$ProbeIntervalS = 90,
    [int]$FailThreshold  = 3
)
$ErrorActionPreference = 'Continue'

$repoRoot   = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$logPath    = Join-Path $repoRoot '.qwen36_watchdog.log'
$restartBat = 'C:\Projects\visual-llm\scripts\restart_qwen36.bat'
$qwenBase   = 'http://127.0.0.1:7870'

function Write-WatchdogLog([string]$msg) {
    $line = ('{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
    Write-Host $line
    Add-Content -Path $logPath -Value $line -Encoding utf8
}

function Probe-Endpoint([string]$path, [int]$timeoutS) {
    try {
        $r = Invoke-WebRequest -Uri "$qwenBase$path" -TimeoutSec $timeoutS `
                -UseBasicParsing -ErrorAction Stop
        return @{ ok = ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300); detail = "http $($r.StatusCode)" }
    } catch {
        return @{ ok = $false; detail = $_.Exception.Message }
    }
}

Write-WatchdogLog "qwen36_watchdog starting (interval=${ProbeIntervalS}s, threshold=$FailThreshold)"
$consecutiveSlotsFail = 0

while ($true) {
    Start-Sleep -Seconds $ProbeIntervalS

    $health = Probe-Endpoint '/health' 5
    $slots  = Probe-Endpoint '/slots' 5

    if ($slots.ok) {
        if ($consecutiveSlotsFail -gt 0) {
            Write-WatchdogLog "recovered: /slots ok after $consecutiveSlotsFail failure(s)"
        }
        $consecutiveSlotsFail = 0
        continue
    }

    $consecutiveSlotsFail++
    Write-WatchdogLog ("/slots FAIL ${consecutiveSlotsFail}/$FailThreshold (health: $($health.detail) | slots: $($slots.detail))")

    if ($consecutiveSlotsFail -lt $FailThreshold) { continue }

    # Don't restart if even /health is dead — the process may be load-bouncing
    # legitimately (boot/model-reload). Only auto-heal the documented zombie
    # pattern: health 200, slots dead.
    if (-not $health.ok) {
        Write-WatchdogLog "skip restart: /health also down ($($health.detail)) - likely model reload, not zombie"
        continue
    }

    Write-WatchdogLog "ZOMBIE DETECTED - restarting llama-server via $restartBat"
    try {
        $p = Start-Process -FilePath $restartBat -Wait -PassThru -WindowStyle Hidden
        Write-WatchdogLog "restart_qwen36.bat exit=$($p.ExitCode)"
    } catch {
        Write-WatchdogLog "restart failed: $($_.Exception.Message)"
    }

    # Give the new server 60s grace to load before next probe so we don't
    # immediately tag the reload as a failure.
    Start-Sleep -Seconds 60
    $consecutiveSlotsFail = 0
}
