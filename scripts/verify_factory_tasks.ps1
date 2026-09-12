<#
.SYNOPSIS
  Read-only state verdict for the ptcg factory Scheduled Tasks
  (post-deadline wind-down roster, 2026-08-18).

.DESCRIPTION
  Prints State / LastRunTime / LastTaskResult / NextRunTime for each task and
  ends with a single loud verdict line. Purely read-only: it queries the Task
  Scheduler and never registers, enables, disables, stops or starts anything,
  so it needs no elevation and is safe to run at any time.

  Exists because the pre-2026-08-10 liveness check queried Triggers /
  LastRunTime / LastTaskResult only, and was therefore blind to a task left in
  State=Disabled by an unlifted inertness hold — the factory-db-lock-contention
  incident (2026-08-08/10) sat at zero throughput for ~2 days in exactly that
  state, with no crash, no Event 322, and no error line anywhere.
  See .claude/rules/factory-task-scheduler-liveness.md (2026-08-10 addendum).

  2026-08-18 wind-down: the 2026-08-16 final-submission deadline passed, so
  ptcg-factory-runner and ptcg-factory-scheduler were PERMANENTLY disabled
  (transcript at experiments/factory/logs/winddown_disable.transcript.txt).

  2026-09-01 FULL SHUTDOWN (Brad decision): leaderboard convergence has
  passed, so ptcg-factory-continuous and ptcg-factory-ui were disabled too
  (kept registered, not unregistered) via
  scripts\factory_shutdown_disable.ps1; transcript at
  experiments/factory/logs/winddown_disable_2026-09-01.transcript.txt.
  ALL FOUR tasks are now expected Disabled; a task found Ready or Running
  after 2026-09-01 is the anomaly this script flags. See the dated addenda in
  .claude/rules/factory-task-scheduler-liveness.md and factory-resume-probe.md.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\verify_factory_tasks.ps1
#>

$ErrorActionPreference = 'Continue'

# 2026-09-01: full shutdown - all four tasks Disabled by design (Brad decision);
# a task found Ready/Running after 2026-09-01 is the anomaly.
$expected = [ordered]@{
    'ptcg-factory-continuous' = @('Disabled')
    'ptcg-factory-runner'     = @('Disabled')
    'ptcg-factory-scheduler'  = @('Disabled')
    'ptcg-factory-ui'         = @('Disabled')
}

$problems = @()
$rows = @()

foreach ($name in $expected.Keys) {
    $task = $null
    try { $task = Get-ScheduledTask -TaskName $name -ErrorAction Stop } catch { $task = $null }

    if ($null -eq $task) {
        $problems += "$name : NOT REGISTERED"
        $rows += [pscustomobject]@{
            Task           = $name
            State          = 'MISSING'
            Expected       = ($expected[$name] -join '/')
            LastRunTime    = $null
            LastTaskResult = $null
            NextRunTime    = $null
        }
        continue
    }

    $info = $null
    try { $info = Get-ScheduledTaskInfo -TaskName $name -ErrorAction Stop } catch { $info = $null }

    $state = [string]$task.State
    $rows += [pscustomobject]@{
        Task           = $name
        State          = $state
        Expected       = ($expected[$name] -join '/')
        LastRunTime    = if ($info) { $info.LastRunTime } else { $null }
        LastTaskResult = if ($info) { ('0x{0:X}' -f $info.LastTaskResult) } else { $null }
        NextRunTime    = if ($info) { $info.NextRunTime } else { $null }
    }

    if ($expected[$name] -notcontains $state) {
        $problems += "$name : State=$state (expected Disabled since the 2026-09-01 full shutdown - a re-enabled factory task is itself an anomaly)"
    }
}

$rows | Format-Table -AutoSize | Out-String | Write-Output

if ($problems.Count -eq 0) {
    Write-Output "VERIFY: PASS - all $($expected.Count) factory tasks match the post-2026-09-01 full-shutdown roster (all four Disabled)."
    Write-Output "NOTE: runner/scheduler were PERMANENTLY disabled 2026-08-18 (post-deadline"
    Write-Output "      wind-down); continuous/ui were disabled 2026-09-01 (full shutdown)."
    Write-Output "      Flat tournament.db game counts, no new watch.log firings and no new"
    Write-Output "      ladder_snapshots.jsonl rows are ALL EXPECTED - none is a fault signal."
    Write-Output "NOTE: State only proves the tasks will not fire. Also confirm no orphan UI"
    Write-Output "      process survives: Get-NetTCPConnection -LocalPort 8765 should return"
    Write-Output "      nothing (see scripts\factory_shutdown_disable.ps1, which checks both)."
    Write-Output "NOTE: LastTaskResult=0x800710E0 on ptcg-factory-ui is a BENIGN historical"
    Write-Output "      value - it was the 15-min watchdog trigger being refused because a"
    Write-Output "      healthy long-lived instance already held the MultipleInstances=IgnoreNew"
    Write-Output "      slot. Post-shutdown it is a frozen last-run record, not a live event."
    exit 0
}

Write-Output "VERIFY: FAIL - $($problems.Count) factory task(s) not in their expected state:"
foreach ($p in $problems) { Write-Output "  - $p" }
Write-Output ""
Write-Output "Expected roster since the 2026-09-01 full shutdown: ALL FOUR tasks Disabled"
Write-Output "(deliberate, permanent - runner/scheduler disabled 2026-08-18, continuous/ui"
Write-Output "disabled 2026-09-01; see the dated addenda in"
Write-Output ".claude/rules/factory-task-scheduler-liveness.md)."
Write-Output "A Ready/Running task means a factory task was RE-ENABLED after the shutdown -"
Write-Output "investigate who/what enabled it, then disable it via"
Write-Output "scripts\factory_shutdown_disable.ps1 (elevated). A MISSING task means it was"
Write-Output "unregistered rather than disabled - benign for a shut-down factory, but note it."
exit 1
