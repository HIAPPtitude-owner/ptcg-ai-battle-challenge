<#
.SYNOPSIS
  Final factory shutdown (2026-09-01): disable ptcg-factory-continuous and
  ptcg-factory-ui, stop the running UI process, and verify all four factory
  Scheduled Tasks are Disabled with no orphan UI listener.

.DESCRIPTION
  Brad decision, 2026-09-01: full shutdown. ptcg-factory-runner and
  ptcg-factory-scheduler were already permanently disabled 2026-08-18
  (post-deadline wind-down; transcript at
  experiments/factory/logs/winddown_disable.transcript.txt). This script
  closes out the remaining two: continuous (ladder-snapshot logger, no longer
  needed now that leaderboard convergence has passed) and ui.

  Tasks are DISABLED, not unregistered — the roster stays visible to
  Get-ScheduledTask so a future session can still audit it.

  Ordering: disable BEFORE stopping the UI process. Stopping first is not
  durable — a still-enabled 15-minute MultipleInstances=IgnoreNew watchdog
  trigger relaunches the worker within one interval (see the watchdog-respawn
  corollary in .claude/rules/factory-resume-probe.md, confirmed 5x).

  Fails loud: any Disable-ScheduledTask error prints FAIL and exits 1. It
  never prints a success verdict it did not verify (see the
  dryrun-is-not-the-real-thing lesson in global CLAUDE.md — the 2026-07-17
  register_factory_task.ps1 incident printed "registered" and exited 0 on an
  Access Denied failure).

  Uses only built-in cmdlets — nothing resolved via per-user PATH, which an
  elevated shell does not inherit.

  REQUIRES ELEVATION for the real (non -DryRun) run: Disable-ScheduledTask on
  these tasks needs administrator rights. -DryRun is read-only and needs none.

.PARAMETER DryRun
  Print current states and what would be done, change nothing.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\factory_shutdown_disable.ps1 -DryRun

.EXAMPLE
  # Elevated, from a NON-elevated PowerShell (triggers the UAC prompt):
  Start-Process powershell -Verb RunAs -ArgumentList '-ExecutionPolicy','Bypass','-NoProfile','-File','"C:\Users\Brad\Dev Folder\Product\ptcg-ai-battle-challenge-strategy\scripts\factory_shutdown_disable.ps1"' -Wait
#>

[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = 'Continue'

# --- Paths (derived from this script's own location; no PATH lookups) --------
$RepoRoot       = Split-Path -Parent $PSScriptRoot
$LogDir         = Join-Path $RepoRoot 'experiments\factory\logs'
$TranscriptPath = Join-Path $LogDir 'winddown_disable_2026-09-01.transcript.txt'

# Tasks this script disables (runner/scheduler were disabled 2026-08-18).
$TasksToDisable = @('ptcg-factory-continuous', 'ptcg-factory-ui')

# Full roster verified at the end — all four must read Disabled.
$AllTasks = @(
    'ptcg-factory-continuous',
    'ptcg-factory-runner',
    'ptcg-factory-scheduler',
    'ptcg-factory-ui'
)

$UiPort = 8765

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

try {
    Start-Transcript -Path $TranscriptPath -Append -ErrorAction Stop | Out-Null
} catch {
    Write-Output "FAIL: could not start transcript at $TranscriptPath : $($_.Exception.Message)"
    exit 1
}

$exitCode = 0

try {
    Set-Location -Path $RepoRoot
    Write-Output "=== ptcg factory FULL SHUTDOWN (2026-09-01) ==="
    Write-Output "Repo      : $RepoRoot"
    Write-Output "Started   : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Output "Mode      : $(if ($DryRun) { 'DRY RUN (read-only)' } else { 'REAL (disables tasks)' })"
    Write-Output ""

    # --- Current state, before any change -----------------------------------
    Write-Output "--- Current task states (before) ---"
    foreach ($name in $AllTasks) {
        $t = $null
        try { $t = Get-ScheduledTask -TaskName $name -ErrorAction Stop } catch { $t = $null }
        if ($null -eq $t) {
            Write-Output ("  {0,-26} NOT REGISTERED" -f $name)
        } else {
            Write-Output ("  {0,-26} {1}" -f $name, $t.State)
        }
    }
    Write-Output ""

    # --- Disable ------------------------------------------------------------
    if ($DryRun) {
        Write-Output "--- Actions that WOULD be taken ---"
        foreach ($name in $TasksToDisable) {
            Write-Output "  DRYRUN: Disable-ScheduledTask -TaskName $name"
        }
        Write-Output "  DRYRUN: Stop-ScheduledTask -TaskName ptcg-factory-ui   (after disable, so the watchdog cannot respawn it)"
        Write-Output "  DRYRUN: verify all 4 tasks Disabled; report orphan python/factory processes and any listener on port $UiPort"
        Write-Output "  DRYRUN: nothing is killed by this script - orphans are reported only."
        Write-Output ""
    } else {
        Write-Output "--- Disabling ---"
        foreach ($name in $TasksToDisable) {
            try {
                Disable-ScheduledTask -TaskName $name -ErrorAction Stop | Out-Null
                Write-Output "  OK: disabled $name"
            } catch {
                Write-Output "FAIL: $name $($_.Exception.Message)"
                Stop-Transcript | Out-Null
                exit 1
            }
        }
        Write-Output ""

        # Safe only AFTER disable: the watchdog trigger can no longer respawn it.
        Write-Output "--- Stopping the running UI process ---"
        Stop-ScheduledTask -TaskName 'ptcg-factory-ui' -ErrorAction SilentlyContinue
        Write-Output "  issued: Stop-ScheduledTask -TaskName ptcg-factory-ui"
        Start-Sleep -Seconds 3
        Write-Output ""
    }

    # --- Verification (read-only, same script, per privileged-action pre-flight)
    Write-Output "--- Task states (after) ---"
    $allDisabled = $true
    $rows = @()
    foreach ($name in $AllTasks) {
        $t = $null
        try { $t = Get-ScheduledTask -TaskName $name -ErrorAction Stop } catch { $t = $null }
        if ($null -eq $t) {
            $state = 'MISSING'
            $allDisabled = $false
        } else {
            $state = [string]$t.State
            if ($state -ne 'Disabled') { $allDisabled = $false }
        }
        $rows += [pscustomobject]@{ TaskName = $name; State = $state }
    }
    $rows | Format-Table -AutoSize | Out-String | Write-Output

    # --- Orphan factory processes ------------------------------------------
    # NOTE: Get-Process has no CommandLine property on PowerShell 5.1, so the
    # command line is read from CIM (Win32_Process) instead. ExecutablePath
    # covers the Path check.
    Write-Output "--- Orphan python/factory processes ---"
    $procs = @()
    try {
        $procs = @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop |
            Where-Object { $_.Name -like 'python*' -or $_.Name -like 'pythonw*' } |
            Where-Object { $_.ExecutablePath -like '*ptcg*' -or $_.CommandLine -like '*ptcg*' -or $_.CommandLine -like '*factory*' })
    } catch {
        Write-Output "  WARN: could not query Win32_Process: $($_.Exception.Message)"
    }
    if ($procs.Count -eq 0) {
        Write-Output "  none"
    } else {
        foreach ($p in $procs) {
            Write-Output "ORPHAN: $($p.ProcessId) $($p.CommandLine)"
        }
        Write-Output "  (not killed - the orchestrator decides)"
    }
    Write-Output ""

    # --- Port 8765 listener -------------------------------------------------
    Write-Output "--- Listeners on port $UiPort ---"
    $conns = @()
    try {
        $conns = @(Get-NetTCPConnection -LocalPort $UiPort -ErrorAction SilentlyContinue)
    } catch {
        $conns = @()
    }
    $listeners = @($conns | Where-Object { $_.State -eq 'Listen' })
    if ($conns.Count -eq 0) {
        Write-Output "  none"
    } else {
        $conns | Select-Object LocalAddress, LocalPort, State, OwningProcess |
            Format-Table -AutoSize | Out-String | Write-Output
        foreach ($l in $listeners) {
            $cmd = ''
            try {
                $op = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $($l.OwningProcess)" -ErrorAction Stop
                if ($op) { $cmd = $op.CommandLine }
            } catch { $cmd = '' }
            Write-Output "ORPHAN: $($l.OwningProcess) $cmd"
        }
    }
    Write-Output ""

    # --- Verdict ------------------------------------------------------------
    if ($DryRun) {
        Write-Output "DRYRUN: complete - nothing was changed."
        $exitCode = 0
    } elseif ($allDisabled -and $listeners.Count -eq 0) {
        Write-Output "SHUTDOWN: PASS"
        $exitCode = 0
    } else {
        Write-Output "SHUTDOWN: FAIL"
        if (-not $allDisabled) { Write-Output "  - not every factory task reads Disabled (see table above)" }
        if ($listeners.Count -gt 0) { Write-Output "  - a process is still listening on port $UiPort (see ORPHAN lines above)" }
        $exitCode = 1
    }
} finally {
    Stop-Transcript | Out-Null
}

exit $exitCode
