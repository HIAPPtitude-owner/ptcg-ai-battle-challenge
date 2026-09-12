# Register (or remove) the continuous factory cycle as a Windows Scheduled Task.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\register_factory_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\register_factory_task.ps1 -IntervalMinutes 15
#   powershell -ExecutionPolicy Bypass -File scripts\register_factory_task.ps1 -Unregister
#   powershell -ExecutionPolicy Bypass -File scripts\register_factory_task.ps1 -DryRun
#
# Idempotent: Register-ScheduledTask -Force updates an existing task of the
# same name in place rather than creating a duplicate, so re-running this
# script (e.g. to change -IntervalMinutes) is always safe.
#
# -DryRun prints the task name, action, triggers, and settings that WOULD be
# registered/unregistered without ever calling Register-ScheduledTask or
# Unregister-ScheduledTask. Real registration is deferred to T10 (run once,
# by Brad/the orchestrator, against the finished factory) -- this script
# must NEVER touch the real Task Scheduler as a side effect of running the
# test suite or any -DryRun invocation.
param(
    [int]$IntervalMinutes = 15,
    [switch]$Unregister,
    [switch]$DryRun,
    [string]$UvPath = ""
)

$TaskName = "ptcg-factory-continuous"
$OldTaskName = "ptcg-factory-nightly"
$MatrixTaskName = "ptcg-factory-matrix"
$TrainerTaskName = "ptcg-factory-trainer"
# Workers hold single-instance locks (see factory_matrix_worker.py /
# factory_trainer_worker.py): a firing while the worker is already alive
# exits "busy" instantly. Their AtStartup + repetition trigger is therefore
# a WATCHDOG, not a work-cadence knob -- fixed independently of
# -IntervalMinutes (which only tunes the main watch task's own cadence).
$WorkerWatchdogMinutes = 15
$Repo = Split-Path -Parent $PSScriptRoot

if ($Unregister) {
    if ($DryRun) {
        Write-Output "DRYRUN unregister task=$TaskName"
        Write-Output "DRYRUN unregister task=$MatrixTaskName"
        Write-Output "DRYRUN unregister task=$TrainerTaskName"
        Write-Output "DRYRUN unregister task=$OldTaskName"
        exit 0
    }
    foreach ($name in @($TaskName, $MatrixTaskName, $TrainerTaskName, $OldTaskName)) {
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

# Resolve uv.exe robustly. In an ELEVATED shell, PATH may not include the
# per-user install location (uv is often installed under $env:USERPROFILE),
# so Get-Command uv.exe can fail silently on PATH alone -- this must not
# depend on the invoking context.
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

if ($DryRun) {
    Write-Output "DRYRUN register task=$TaskName"
    Write-Output "DRYRUN exec=$uv"
    Write-Output "DRYRUN args=run python scripts/factory_watch_once.py"
    Write-Output "DRYRUN workingdir=$Repo"
    Write-Output "DRYRUN trigger1=Once+Repetition every $IntervalMinutes minute(s), duration 3650 days"
    Write-Output "DRYRUN trigger2=AtStartup"
    Write-Output "DRYRUN executiontimelimit=8:00:00"
    Write-Output "DRYRUN multipleinstances=IgnoreNew"
    Write-Output "DRYRUN register task=$MatrixTaskName"
    Write-Output "DRYRUN exec=$uv"
    Write-Output "DRYRUN args=run python scripts/factory_matrix_worker.py"
    Write-Output "DRYRUN workingdir=$Repo"
    Write-Output "DRYRUN trigger1=AtStartup"
    Write-Output "DRYRUN trigger2=Once+Repetition every $WorkerWatchdogMinutes minute(s) (watchdog), duration 3650 days"
    Write-Output "DRYRUN executiontimelimit=no execution time limit"
    Write-Output "DRYRUN multipleinstances=IgnoreNew"
    Write-Output "DRYRUN register task=$TrainerTaskName"
    Write-Output "DRYRUN exec=$uv"
    Write-Output "DRYRUN args=run python scripts/factory_trainer_worker.py"
    Write-Output "DRYRUN workingdir=$Repo"
    Write-Output "DRYRUN trigger1=AtStartup"
    Write-Output "DRYRUN trigger2=Once+Repetition every $WorkerWatchdogMinutes minute(s) (watchdog), duration 3650 days"
    Write-Output "DRYRUN executiontimelimit=no execution time limit"
    Write-Output "DRYRUN multipleinstances=IgnoreNew"
    Write-Output "DRYRUN retire old task=$OldTaskName (if present)"
    exit 0
}

$action = New-ScheduledTaskAction -Execute $uv `
    -Argument "run python scripts/factory_watch_once.py" `
    -WorkingDirectory $Repo
$repetitionTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$startupTrigger = New-ScheduledTaskTrigger -AtStartup
$triggers = @($repetitionTrigger, $startupTrigger)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8) `
    -MultipleInstances IgnoreNew

# $uv is resolved once above and reused for every action below -- the
# elevated-shell PATH lesson applies identically to all three tasks.
$matrixAction = New-ScheduledTaskAction -Execute $uv `
    -Argument "run python scripts/factory_matrix_worker.py" `
    -WorkingDirectory $Repo
$trainerAction = New-ScheduledTaskAction -Execute $uv `
    -Argument "run python scripts/factory_trainer_worker.py" `
    -WorkingDirectory $Repo
$watchdogRepetitionTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes $WorkerWatchdogMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$watchdogStartupTrigger = New-ScheduledTaskTrigger -AtStartup
$workerTriggers = @($watchdogStartupTrigger, $watchdogRepetitionTrigger)
# No execution time limit (New-TimeSpan -Hours 0 == PT0S == unlimited) --
# unlike the 8h-limited watch task, the workers run forever between
# watchdog firings that find them already alive.
$workerSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -MultipleInstances IgnoreNew

# Register ALL new tasks first, verifying each immediately with
# Get-ScheduledTask right after its own Register-ScheduledTask call, and
# only THEN touch/retire the old task. This ordering (register-before-retire)
# and the loud try/catch + exit 1 on every real cmdlet are non-negotiable
# per the go-live incident (commits 72bc0f8 / 225686c): a mid-loop failure
# must never leave the machine with zero factory tasks.
$newTasks = @(
    @{ Name = $TaskName; Action = $action; Triggers = $triggers; Settings = $settings;
       Description = "PTCG agent factory continuous cycle" },
    @{ Name = $MatrixTaskName; Action = $matrixAction; Triggers = $workerTriggers; Settings = $workerSettings;
       Description = "PTCG agent factory matrix worker (deck-matrix queue refill, watchdog)" },
    @{ Name = $TrainerTaskName; Action = $trainerAction; Triggers = $workerTriggers; Settings = $workerSettings;
       Description = "PTCG agent factory trainer worker (per-deck net training, watchdog)" }
)

foreach ($t in $newTasks) {
    try {
        Register-ScheduledTask -TaskName $t.Name -Action $t.Action -Trigger $t.Triggers `
            -Settings $t.Settings -Description $t.Description -Force `
            -ErrorAction Stop | Out-Null
    }
    catch {
        Write-Output "ERROR: registration failed for $($t.Name): $($_.Exception.Message)"
        exit 1
    }

    if (-not (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue)) {
        Write-Output "ERROR: Register-ScheduledTask reported success but $($t.Name) is not present"
        exit 1
    }
}

if (Get-ScheduledTask -TaskName $OldTaskName -ErrorAction SilentlyContinue) {
    try {
        Unregister-ScheduledTask -TaskName $OldTaskName -Confirm:$false -ErrorAction Stop
        Write-Output "retired old task $OldTaskName"
    }
    catch {
        Write-Warning "failed to retire old task $OldTaskName (new tasks are already registered): $($_.Exception.Message)"
    }
}

Write-Output "registered $TaskName every $IntervalMinutes minute(s) + AtStartup (repo: $Repo)"
Write-Output "registered $MatrixTaskName + $TrainerTaskName watchdog every $WorkerWatchdogMinutes minute(s) + AtStartup (repo: $Repo)"
Write-Output "logs: experiments\factory\logs\  digests: experiments\factory\digests\"
