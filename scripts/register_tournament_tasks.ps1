# Register (or remove) the GENERATIONAL TOURNAMENT Windows Scheduled Tasks
# (tournament T20 cutover). Run ELEVATED, once, post-merge (T21 go-live) --
# NEVER as a side effect of the test suite or any -DryRun invocation.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\register_tournament_tasks.ps1 -DryRun
#   powershell -ExecutionPolicy Bypass -File scripts\register_tournament_tasks.ps1 -UvPath "<uv.exe>"
#   powershell -ExecutionPolicy Bypass -File scripts\register_tournament_tasks.ps1 -Unregister
#
# Pattern PS1-REGISTER (models scripts/register_factory_task.ps1 verbatim):
# register ALL new tasks FIRST (verifying each with Get-ScheduledTask right
# after its own Register-ScheduledTask), and only THEN retire the old ones --
# register-before-retire + loud try/catch + exit 1 on every real registration
# cmdlet are non-negotiable per the go-live incident (commits 72bc0f8 /
# 225686c): a mid-loop failure must never leave the machine with zero factory
# tasks. uv is resolved by explicit -UvPath / probe (per-user PATH is invisible
# in an elevated shell). Real runs are Start-Transcript-captured (the elevated
# console closes on exit, taking its output with it).
#
# ============================ POST-CUTOVER TASK MAP ============================
#   REGISTER (new):
#     ptcg-factory-runner    -> factory_runner_pool.py (4-8 game-runner procs)
#     ptcg-factory-scheduler -> factory_tournament_scheduler.py (loop + faucet)
#     ptcg-factory-ui        -> factory_ui.py (SOLE status/review surface, PD-B)
#   RETIRE (unregister): ptcg-factory-matrix, ptcg-factory-trainer
#   LEAVE ALONE: ptcg-factory-continuous (narrowed watch loop, T20; it spawns a
#     fresh process every ~15 min so it already runs the narrowed code -- this
#     script does NOT touch it).
#
# ===================== FAUCET OWNERSHIP -- PLAN-DRIFT FLAG =====================
# The plan defers "faucet ownership" to T20 (loop_scheduler.loop_tick docstring:
# "T20 settles faucet ownership"; factory_tournament_scheduler.py docstring:
# "the separate ptcg-factory-trainer worker ... owns the TRAIN faucet"). The
# plan's LITERAL wiring (scheduler at --pipeline-target 0 AND a retargeted
# trainer ALSO running factory_tournament_scheduler.py) is a VERIFIED DEAD
# FAUCET: both share the hard-coded SCHEDULER_LOCK (watch.instance_lock,
# fail-fast 0.5s; no --lock-path CLI override exists), so whichever process
# wins the lock runs and the other exits "busy" forever. If the pipeline_target=0
# scheduler wins, NOTHING ever breeds -> no challengers -> the whole tournament
# stalls after BOOTSTRAP.
#
#   R1 (IMPLEMENTED here, in T20's file scope, guaranteed-live, ZERO code
#       edits): the SINGLE ptcg-factory-scheduler process owns the loop AND the
#       faucet via -PipelineTarget 4 (train_offspring's in-flight gate bounds
#       over-breeding). ptcg-factory-trainer is RETIRED -- offspring net training
#       happens INLINE in train_offspring's trainer_factory (PerDeckNetTrainer),
#       so a separate trainer task is redundant. Cost: an offspring GPU-train
#       briefly blocks the scheduler tick (a few times/day; crash-safe/resumable).
#   R2 (PREFERRED architecture, but OUT OF T20's declared file scope): keep the
#       trainer as a SEPARATE faucet (training off the orchestration critical
#       path, matching the plan's stated intent). Requires adding a --lock-path
#       CLI seam to factory_tournament_scheduler.py (run_scheduler already takes
#       lock_path; only the CLI needs it) so scheduler(pipeline 0)+trainer(pipeline
#       4) use distinct locks. Then: scheduler -PipelineTarget 0, and register
#       ptcg-factory-trainer -> factory_tournament_scheduler.py --pipeline-target 4
#       --lock-path <faucet.lock>.
#
# >>> ORCHESTRATOR: CONFIRM R1 vs R2 BEFORE T21 REGISTERS THIS. Flipping to R2 is
#     -PipelineTarget 0 here + the trainer registration + the scheduler CLI seam.
#     This script is INERT until run elevated at T21, so R1 is a safe default. <<<
# ==============================================================================
param(
    [int]$PipelineTarget = 4,    # scheduler TRAIN faucet (R1). Set 0 for R2.
    [int]$UiPort = 8765,
    [int]$Workers = 4,           # game-runner subprocesses (spec: 4-8)
    [int]$WatchdogMinutes = 15,
    [switch]$Unregister,
    [switch]$DryRun,
    [string]$UvPath = ""
)

$RunnerTask = "ptcg-factory-runner"
$SchedulerTask = "ptcg-factory-scheduler"
$UiTask = "ptcg-factory-ui"
# Retired at cutover: the compute-saturation matrix worker and (per R1 above)
# the standalone trainer worker.
$RetireTasks = @("ptcg-factory-matrix", "ptcg-factory-trainer")
$Db = "experiments/factory/tournament.db"
$Repo = Split-Path -Parent $PSScriptRoot

if ($Unregister) {
    if ($DryRun) {
        foreach ($name in @($RunnerTask, $SchedulerTask, $UiTask)) {
            Write-Output "DRYRUN unregister task=$name"
        }
        exit 0
    }
    foreach ($name in @($RunnerTask, $SchedulerTask, $UiTask)) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            try {
                Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction Stop
                Write-Output "unregistered $name"
            }
            catch {
                Write-Warning "failed to unregister $name`: $($_.Exception.Message)"
            }
        }
    }
    exit 0
}

# Resolve uv.exe robustly -- an ELEVATED shell's PATH may not include the
# per-user install location, so this must not depend on the invoking context
# (commit 225686c lesson). Identical probe to register_factory_task.ps1.
$uv = ""
if ($UvPath -and (Test-Path $UvPath)) {
    $uv = $UvPath
}
else {
    $uvCmd = Get-Command uv.exe -ErrorAction SilentlyContinue
    if ($uvCmd) {
        $uv = $uvCmd.Source
    }
    else {
        $uvCandidates = @(
            "$env:LOCALAPPDATA\Programs\Python\Python*\Scripts\uv.exe",
            "$env:USERPROFILE\AppData\Local\Programs\Python\Python*\Scripts\uv.exe",
            "$env:USERPROFILE\.local\bin\uv.exe",
            "$env:USERPROFILE\.cargo\bin\uv.exe"
        )
        foreach ($pattern in $uvCandidates) {
            $found = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue |
                Select-Object -First 1 -ExpandProperty FullName
            if ($found) {
                $uv = $found
                break
            }
        }
    }
}

if (-not $uv) {
    Write-Output "ERROR: uv.exe not found (pass -UvPath or add uv to PATH)"
    exit 1
}

# Action argument strings (all relative to -WorkingDirectory $Repo). The
# scheduler carries --seed so it creates + seeds the Founding Census on first
# run (idempotent: seed_if_empty no-ops on a warm DB) and --pipeline-target
# $PipelineTarget wires the TRAIN faucet ON (R1).
$runnerArgs = "run python scripts/factory_runner_pool.py --db $Db --workers $Workers --loop-forever"
$schedulerArgs = "run python scripts/factory_tournament_scheduler.py --db $Db --seed --pipeline-target $PipelineTarget"
$uiArgs = "run python scripts/factory_ui.py --db $Db --port $UiPort"

if ($DryRun) {
    Write-Output "DRYRUN register task=$RunnerTask"
    Write-Output "DRYRUN exec=$uv"
    Write-Output "DRYRUN args=$runnerArgs"
    Write-Output "DRYRUN workingdir=$Repo"
    Write-Output "DRYRUN trigger1=AtStartup"
    Write-Output "DRYRUN trigger2=Once+Repetition every $WatchdogMinutes minute(s) (watchdog), duration 3650 days"
    Write-Output "DRYRUN executiontimelimit=no execution time limit"
    Write-Output "DRYRUN multipleinstances=IgnoreNew"
    Write-Output "DRYRUN register task=$SchedulerTask"
    Write-Output "DRYRUN exec=$uv"
    Write-Output "DRYRUN args=$schedulerArgs"
    Write-Output "DRYRUN workingdir=$Repo"
    Write-Output "DRYRUN trigger1=AtStartup"
    Write-Output "DRYRUN trigger2=Once+Repetition every $WatchdogMinutes minute(s) (watchdog), duration 3650 days"
    Write-Output "DRYRUN executiontimelimit=no execution time limit"
    Write-Output "DRYRUN multipleinstances=IgnoreNew"
    Write-Output "DRYRUN faucet=scheduler pipeline-target=$PipelineTarget (R1; 0 => R2, see header)"
    Write-Output "DRYRUN register task=$UiTask"
    Write-Output "DRYRUN exec=$uv"
    Write-Output "DRYRUN args=$uiArgs"
    Write-Output "DRYRUN workingdir=$Repo"
    Write-Output "DRYRUN trigger1=AtStartup"
    Write-Output "DRYRUN trigger2=Once+Repetition every $WatchdogMinutes minute(s) (watchdog), duration 3650 days"
    Write-Output "DRYRUN executiontimelimit=no execution time limit"
    Write-Output "DRYRUN multipleinstances=IgnoreNew"
    foreach ($old in $RetireTasks) {
        Write-Output "DRYRUN retire task=$old (AFTER new tasks confirmed registered)"
    }
    exit 0
}

# --- REAL (elevated) path ---------------------------------------------------
# Capture the elevated console to a transcript (it closes on exit). Non-fatal
# if transcription cannot start -- it is diagnostics, not correctness.
$logDir = Join-Path $Repo "experiments\factory\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
$transcript = Join-Path $logDir "register_tournament_tasks.transcript.txt"
try { Start-Transcript -Path $transcript -Append -ErrorAction Stop | Out-Null } catch {}

# All three tasks are LONG-LIVED workers (like the retired matrix/trainer):
# AtStartup + a WATCHDOG repetition (their own single-instance locks / the UI's
# tournament_ui.lock / IgnoreNew make a firing-while-alive exit instantly), no
# execution time limit.
$watchdogRepetition = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes $WatchdogMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$startupTrigger = New-ScheduledTaskTrigger -AtStartup
$workerTriggers = @($startupTrigger, $watchdogRepetition)
$workerSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -MultipleInstances IgnoreNew

# $uv resolved once above and reused for every action (elevated-shell PATH
# lesson applies identically to all three).
$runnerAction = New-ScheduledTaskAction -Execute $uv -Argument $runnerArgs -WorkingDirectory $Repo
$schedulerAction = New-ScheduledTaskAction -Execute $uv -Argument $schedulerArgs -WorkingDirectory $Repo
$uiAction = New-ScheduledTaskAction -Execute $uv -Argument $uiArgs -WorkingDirectory $Repo

$newTasks = @(
    @{ Name = $RunnerTask; Action = $runnerAction; Triggers = $workerTriggers; Settings = $workerSettings;
       Description = "PTCG tournament game-runner pool ($Workers workers, loop-forever)" },
    @{ Name = $SchedulerTask; Action = $schedulerAction; Triggers = $workerTriggers; Settings = $workerSettings;
       Description = "PTCG tournament loop scheduler + TRAIN faucet (pipeline-target $PipelineTarget)" },
    @{ Name = $UiTask; Action = $uiAction; Triggers = $workerTriggers; Settings = $workerSettings;
       Description = "PTCG tournament review + status UI (sole surface, port $UiPort)" }
)

foreach ($t in $newTasks) {
    try {
        Register-ScheduledTask -TaskName $t.Name -Action $t.Action -Trigger $t.Triggers `
            -Settings $t.Settings -Description $t.Description -Force `
            -ErrorAction Stop | Out-Null
    }
    catch {
        Write-Output "ERROR: registration failed for $($t.Name): $($_.Exception.Message)"
        try { Stop-Transcript -ErrorAction SilentlyContinue | Out-Null } catch {}
        exit 1
    }

    if (-not (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue)) {
        Write-Output "ERROR: Register-ScheduledTask reported success but $($t.Name) is not present"
        try { Stop-Transcript -ErrorAction SilentlyContinue | Out-Null } catch {}
        exit 1
    }
    Write-Output "registered $($t.Name)"
}

# Retire the superseded tasks ONLY after every new task is confirmed present.
# A retire failure is a WARNING, not exit 1: the new tasks are already live, so
# a lingering old task is cosmetic (never leaves the machine with zero tasks).
foreach ($old in $RetireTasks) {
    if (Get-ScheduledTask -TaskName $old -ErrorAction SilentlyContinue) {
        try {
            Unregister-ScheduledTask -TaskName $old -Confirm:$false -ErrorAction Stop
            Write-Output "retired $old"
        }
        catch {
            Write-Warning "failed to retire $old (new tasks already registered): $($_.Exception.Message)"
        }
    }
}

Write-Output "registered $RunnerTask + $SchedulerTask + $UiTask (AtStartup + watchdog every $WatchdogMinutes min, repo: $Repo)"
Write-Output "faucet: $SchedulerTask --pipeline-target $PipelineTarget (R1 -- confirm vs R2, see script header)"
Write-Output "left alone: ptcg-factory-continuous (narrowed watch loop, T20)"
Write-Output "logs: experiments\factory\logs\  transcript: $transcript"
try { Stop-Transcript -ErrorAction SilentlyContinue | Out-Null } catch {}
