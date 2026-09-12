# Verify the factory's Windows Scheduled Task fired before trusting its output as schedule-driven

## 2026-08-18 addendum: post-deadline wind-down — expected states changed for the mandatory check

`ptcg-factory-runner` and `ptcg-factory-scheduler` were PERMANENTLY disabled
on 2026-08-18 (deliberate wind-down after the 2026-08-16 final-submission
deadline; elevated `Disable-ScheduledTask`, transcript at
`experiments/factory/logs/winddown_disable.transcript.txt`). The mandatory
pre-review check now expects:

- `ptcg-factory-continuous` — `Ready`/`Running` (carries the ladder-snapshot
  logger through leaderboard convergence, ~Aug 31)
- `ptcg-factory-ui` — `Ready`/`Running`
- `ptcg-factory-runner` — `Disabled` (EXPECTED; a re-enabled game worker
  after 2026-08-18 is itself an anomaly worth flagging)
- `ptcg-factory-scheduler` — `Disabled` (EXPECTED; same)

`scripts/verify_factory_tasks.ps1` encodes these expectations and PASSes
against them. The 2026-08-10 addendum below ("a DISABLED task is a fourth
pathology") is **superseded FOR THESE TWO TASKS ONLY** by this wind-down —
it still applies in full to `ptcg-factory-continuous` and `ptcg-factory-ui`,
and to any future task roster. Flat `tournament.db` game counts are expected,
not a starvation signal. A full-shutdown decision for the remaining two tasks
is pending (Brad-owned) after ~Aug 31 convergence. **Convergence CONFIRMED
REACHED 2026-09-01** (both counted refs flat across their final three
snapshots — `ladder_snapshots.jsonl:951`/`:952`, newest `utc_ts
2026-09-01T18:15:05Z`), so that decision is no longer waiting on anything;
see `.claude/rules/factory-resume-probe.md`'s wind-down addendum.

## 2026-09-01 addendum: full shutdown — expected state is Disabled ×4

The full-shutdown decision named above as pending was made and executed
2026-09-01: `scripts/factory_shutdown_disable.ps1` ran elevated for real
and disabled `ptcg-factory-continuous` + `ptcg-factory-ui` (transcript
`experiments/factory/logs/winddown_disable_2026-09-01.transcript.txt`).
Combined with `ptcg-factory-runner`/`ptcg-factory-scheduler` (already
`Disabled` since 2026-08-18 above), **all four tasks are now Disabled**.
`scripts/verify_factory_tasks.ps1` was re-keyed to expect `Disabled` on
all four and PASSes against that roster.

- The 2026-08-10 addendum below ("a DISABLED task is a fourth pathology")
  is now **superseded for ALL FOUR tasks**, not just runner/scheduler —
  `State=Disabled` is the expected, correct state for the entire roster.
  It remains valid diagnostic guidance for any future roster this repo
  might register.
- A fifth transient signature to distinguish from the pathologies above:
  `Disable-ScheduledTask` returns success immediately, but `State` can
  still read `Running` for up to roughly a firing's duration afterward if
  an instance was already in flight when the disable was issued — this
  is benign (the task simply never fires again once that instance exits),
  not a wedge and not a failed disable. See
  `.claude/rules/factory-resume-probe.md`'s matching 2026-09-01 addendum
  for the full receipt (the disable script's own verdict printed
  `SHUTDOWN: FAIL` on the first check for exactly this reason, and read
  clean 80 seconds later with zero factory python processes alive).


Any weekly review (or any session) that reasons about the agent factory's
recent activity — candidates registered, submissions uploaded, digests
written — as evidence the **continuous schedule** is working must first
confirm the scheduled task itself actually fired, not just that output
exists. Output can also come from a manual run
(`uv run python scripts/factory_watch_once.py` or `factory_cycle.py`
invoked by hand) or a stale prior run; neither proves the schedule is live.

**Updated for the T21 tournament go-live (executed 2026-07-30): there are
now FOUR scheduled tasks to check.** The current roster:

- `ptcg-factory-continuous` — narrowed watch loop: submission-scheduler
  ticks + episode harvester only (T20).
- `ptcg-factory-runner` — game-runner pool for the tournament pipeline,
  writing to `experiments/factory/tournament.db`.
- `ptcg-factory-scheduler` — the crash-safe tournament loop
  (`loop_scheduler.py`/`factory_tournament_scheduler.py`), which also owns
  offspring breeding via the R1 faucet (`--pipeline-target 4`).
- `ptcg-factory-ui` — localhost review/status server (port 8765), the sole
  status surface (`dashboard.html` retired 2026-07-24).

(Historical: from the 2026-07-20 compute-saturation slice until T21, the
roster was `ptcg-factory-continuous` / `ptcg-factory-matrix` /
`ptcg-factory-trainer`; matrix/trainer were retired 2026-07-30 and their
output files — `matrix.json`, `matrix_blocks.jsonl`, `agent_pool.json`,
`deck_pool.json` — frozen.)

Run the liveness check against all four task names below, not just
`ptcg-factory-continuous`. A future review that checks only the watch loop
and finds it healthy could still be blind to the runner/scheduler/ui
workers having silently stopped.

**Datapoint (2026-07-14, weekly factory review):** the session reviewed 3
nights of autonomous Kaggle-submission activity (refs
54585057/54608104/54608114) and treated it as confirmation the nightly
`ptcg-factory-nightly` Task Scheduler job was running correctly — without
ever running `Get-ScheduledTask` to check the trigger/last-run-result. If a
future week's scheduled cycle silently stops firing (task deleted, trigger
disabled, credentials expired), there is no baseline to distinguish "the
task didn't run" from "the task ran and produced nothing interesting" unless
this check becomes routine.

**Mandatory pre-review check** (before drawing any conclusion from a week's
worth of unattended factory output):

One command (read-only, prints a loud PASS/FAIL verdict line):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\verify_factory_tasks.ps1
```

Or inline — note `State` is part of the check, not optional (see the
2026-08-10 addendum on disabled-task blindness):

```powershell
foreach ($t in "ptcg-factory-continuous", "ptcg-factory-runner", "ptcg-factory-scheduler", "ptcg-factory-ui") {
  Get-ScheduledTask -TaskName $t | Select-Object -ExpandProperty Triggers
  Get-ScheduledTask -TaskName $t | Select-Object TaskName, State
  Get-ScheduledTaskInfo -TaskName $t |
    Select-Object @{n="Task";e={$t}}, LastRunTime, LastTaskResult, NextRunTime
}
```

- `LastRunTime` for `ptcg-factory-continuous` should be within the last
  ~30 minutes (two firing intervals at the default 15-minute
  `-IntervalMinutes` repetition, plus the `AtStartup` trigger) at the start
  of any review — a much tighter window than the retired once-nightly
  task's ~24-36h, since a healthy continuous loop should never go long
  without ticking. `ptcg-factory-runner`, `ptcg-factory-scheduler`, and
  `ptcg-factory-ui` are loop-forever processes under 15-minute
  `MultipleInstances=IgnoreNew` watchdog triggers — a recent `LastRunTime`
  mostly proves the watchdog fired, not that the worker is healthy.
  Cross-check the `ptcg-factory-ui` `/status` page (localhost:8765) and
  games-played growth in `experiments/factory/tournament.db` instead of a
  fixed LastRunTime window (~337 games/hour observed at go-live).
- `LastTaskResult` of `0` means success; a nonzero value means the task
  fired but `factory_watch_once.py` itself crashed outright (the script is
  designed to exit 0 even for routine busy/paused/no-op firings, so nonzero
  means something worse) — check `experiments/factory/logs/watch.log` for
  the last line and the matching `experiments/factory/logs/` cycle log.
- If a task is missing entirely, re-register it — the watch loop via
  `scripts\register_factory_task.ps1` (per `docs/factory-operations.md`;
  it auto-retires any lingering `ptcg-factory-nightly` task from the prior
  once-per-night design), the runner/scheduler/ui trio via
  `scripts\register_tournament_tasks.ps1` (elevated). Do not assume "no
  output recently" means "nothing to review."

This generalizes the global CLAUDE.md rule on unattended-automation liveness
checks (Workflow → Verification before done) to this repo's specific task
name and log locations. See also `.claude/rules/factory-resume-probe.md`
(the broader session-resume contract for this repo's autonomous factory) —
this rule is the specific scheduler-liveness sub-check that probe should
include going forward.

## Terminal marker liveness — watch-loop processes hiding crashes behind missing log lines

Watch-loop processes that return exit 0 by design (exception-safe, no crash
on error) but rely on optional terminal markers (e.g., `cycle:` or
`cycle-error:` line in watch.log) can hide silent crashes behind the
**ABSENCE of that marker**. A watch.log showing regular firing timestamps but
MISSING terminal markers on a majority of them (not just one firing here and
there — a persistent pattern) is the crash signature; distinguish it from a
healthy loop by the **marker pattern, not just the timestamp pattern.**

**Datapoint (factory-submit-crash-fix, 2026-07-22):** `factory_watch_once.py`'s
top-level try-catch returned exit 0 even on exceptions, and wrote a terminal
`cycle:` line only on success. A submit-gate None-safe bug crashed the loop
~96 times over 36 hours (~every 15 minutes). Each crash: watch.log showed a
firing timestamp (the process launched), but NO terminal `cycle:` line (the
crash happened mid-cycle). Two concurrent masking layers hid this crash
pattern: (1) no fixture tested the None-field provenance that triggered it
(see `.claude/rules/provenance-shaped-optional-fields.md`), so the crash was
invisible to CI; (2) a concurrent auth outage and dashboard staleness
prevented visibility of the unattended factory's state degradation. Total
impact: 36 hours, ~96 silent failures, ladder submissions accepted by Kaggle
but factory's internal state never recorded them, corrupting future decisions.

**Rule:** any watch-loop-style process with optional terminal markers must
**GUARANTEE a terminal log line (success OR failure indicator) on every
firing**, not just on success. Wrap the entire loop in try-catch and emit a
terminal `cycle-error:` marker on any unhandled exception. Then, when
reviewing a week's worth of unattended output: grep watch.log for the
terminal marker pattern (expect one per ~15-min firing, none missing across a
week except for explicit pause-window windows) — don't just check that
timestamps exist. Missing markers on even a few% of firings are worth
investigating; missing markers on >50% of firings is a crash cascade.

## A `Running` task is not proof it is running CURRENT code — check block provenance against HEAD after merges

`Get-ScheduledTaskInfo`'s `LastRunTime`/`LastTaskResult` only prove the
task **fired** (per the rest of this file). They say nothing about whether
`ptcg-factory-matrix` or `ptcg-factory-trainer` — both long-lived
processes that stay running across many firings, unlike the watch loop
which spawns a fresh process every ~15 minutes — are executing code from
BEFORE or AFTER the most recent merge that touched their code paths. A
task can be `Running` with `LastTaskResult = 0` while the process itself
is a stale pre-merge instance.

**Datapoint (evolutionary-agent-population go-live, 2026-07-21):** both
worker tasks reported healthy/`Running` post-merge, but were still
executing pre-merge code (started 2026-07-20 19:30, merge landed
2026-07-21 ~11:42) — caught only by comparing the commit stamped in
`experiments/factory/matrix_blocks.jsonl`'s per-block provenance against
`git log` HEAD, not by the Scheduled Task's own reported state. Fixed via
`Stop-ScheduledTask`/`Start-ScheduledTask` on both tasks (see the
long-lived-worker-code-staleness section of `.claude/rules/factory-resume-probe.md`
for the full incident and the generalized rule).

**Add this check whenever a review or resume reasons about matrix/trainer
worker output across a merge boundary:** pull the most recent
`matrix_blocks.jsonl` entry's commit stamp (or the trainer's equivalent log
line) and confirm it is at or after the last merge touching
`src/ptcg/factory/` evolution/breeding/bt/genomes/training code — a
`Running` status alone is insufficient evidence.

## 2026-07-29 addendum: post-T20 task roster, strict terminal-marker baseline, and Event-322 wedged-instance starvation

**Post-T20 state (generational-champion-tournament, merged 61a3148,
2026-07-24):** `ptcg-factory-continuous` was narrowed to submission-scheduler
+ episode-harvester ticks only; `dashboard.html` retired (PD-B override).
**T21 (EXECUTED 2026-07-30)** additionally registered
`ptcg-factory-runner` / `ptcg-factory-scheduler` / `ptcg-factory-ui` and
retired `ptcg-factory-matrix` / `ptcg-factory-trainer` — see
`scripts/register_tournament_tasks.ps1` for the exact task names,
`.claude/rules/factory-resume-probe.md`'s matching addenda for the full
roster-change narrative, and the 2026-07-30 addendum below for the go-live
datapoint. The four-task roster documented at the top of this file is now
the live set.

**Terminal-marker check is now STRICT, not "majority."** Since the
terminal-marker-liveness fix above (factory-submit-crash-fix, 2026-07-22)
guarantees a marker on every exit path including hold/no-op/error, a
healthy `ptcg-factory-continuous` should show a PAIRED terminal marker on
literally every firing in `watch.log`. Any gap — even a single missing
pair, not just "missing on >50% of firings" — is worth investigating
immediately rather than waiting for a pattern to accumulate.

**New failure signature: `LastTaskResult = 0x80070420` with a wedged
(not crashed) instance.** A fresh-process-per-firing task under
`MultipleInstances=IgnoreNew` (this is `ptcg-factory-continuous`'s
configuration) can get one instance that hangs instead of exiting. Every
later scheduled firing is then silently skipped — no new process, no new
terminal marker, `LastRunTime` simply stops advancing, and
`LastTaskResult` shows `0x80070420` ("already running") rather than a
normal 0/nonzero result. This looks superficially like "the task just
hasn't fired recently," but is actually total starvation. Check for it
explicitly, not just via `LastRunTime`/`LastTaskResult`:

```powershell
Get-WinEvent -LogName Microsoft-Windows-TaskScheduler/Operational |
  Where-Object { $_.Id -eq 322 -and $_.TimeCreated -gt (Get-Date).AddHours(-6) }
(Get-Item "experiments/factory/logs/watch.lock" -ErrorAction SilentlyContinue).LastWriteTime
```

Repeated Event ID 322 entries ("the last instance of the task is still
running, so this instance will be skipped") in the recent window is the
fingerprint; a `watch.lock` age far older than one firing interval
(>15-30 minutes) with no matching terminal marker confirms it. Recovery:
kill the wedged process, then confirm the very next firing produces a
fresh, paired terminal marker. **Datapoint (2026-07-24, Pass 2 for
generational-champion-tournament):** a legacy watch-loop instance wedged
at 11:15 silently starved every firing until it was found and cleared;
Pass 2 held the gate open (BLOCKED) until 5 clean days of firings (418/418
paired markers) confirmed the narrowed loop was healthy again on
resume (2026-07-29). This incident is now promoted into
`docs/weekly-review-checklist.md` step 13 as a routine check, not just a
one-off recovery.

## 2026-07-30 addendum: T21 executed — four-task roster live; watchdog-respawn datapoint

**T21 go-live executed 2026-07-30 (~07:56 machine time):**
`scripts/register_tournament_tasks.ps1` ran elevated for real (exit 0;
transcript at
`experiments/factory/logs/register_tournament_tasks.transcript.txt`);
`ptcg-factory-runner` / `ptcg-factory-scheduler` / `ptcg-factory-ui`
registered, `ptcg-factory-matrix` / `ptcg-factory-trainer` retired,
`ptcg-factory-continuous` untouched — independently verified via
`Get-ScheduledTask`. The mandatory pre-review check above now targets the
four-task roster. Full go-live record (census seed, holds lifted, firing
verification): `experiments/EXPERIMENTS.md`, 2026-07-30 entry.

**Watchdog-respawn datapoint (2026-07-30):** stopping a watchdog-repeating
task is not durable. During the T21 cutover the legacy matrix/trainer
15-minute watchdog triggers refired at ~08:00 — in the window between the
Rung-A `Stop-ScheduledTask` and the registration script's retire step —
relaunching 6 stale (pre-cutover commit `9004d1b`) worker processes,
killed by PID (`taskkill /T /F`; verified zero remained). Lesson: a
stopped-but-still-registered watchdog task relaunches itself within one
~15-minute interval; only unregister/disable keeps a worker down.

The wedged-instance Event-322 section above stands unchanged for
`ptcg-factory-continuous` (fresh-process-per-firing). Note that
`MultipleInstances=IgnoreNew` now also applies to the runner/scheduler/ui
trio — for those loop-forever workers, repeated Event-322 entries are
BENIGN (the watchdog finding a healthy long-lived instance already holding
the slot), so the Event-322 fingerprint indicates starvation only for the
fresh-process watch loop.

## 2026-08-10 addendum: a DISABLED task is a fourth pathology, and the pre-2026-08-10 check block was blind to it

Every failure signature above (crashed, wedged/Event-322, stale-code,
watchdog-respawned) assumes the task is at least *registered and enabled*.
A task that is **`Disabled`** — typically because a slice took an
inertness hold via `Disable-ScheduledTask` and the paired re-enable never
completed — produces none of those fingerprints: no crash result, no
Event 322, no wedged `watch.lock`, no error line anywhere. It looks
exactly like "nothing interesting happened," which is why the check block
above (which queried only `Triggers` / `LastRunTime` / `LastTaskResult` /
`NextRunTime`) could not see it. `State` has now been added to that block
for exactly this reason.

**Disabled-task signature:** `State = Disabled`; `LastRunTime` frozen at
the moment the hold was taken; `LastTaskResult` still `0` (the last real
run succeeded); `NextRunTime` blank/absent. Total throughput zero, with
the DB's game count flat — the cross-check in
`docs/weekly-review-checklist.md` step 11.

**Datapoint (factory-db-lock-contention, 2026-08-08/10).** The slice
disabled `ptcg-factory-runner` and `ptcg-factory-scheduler` as its
inertness hold (correctly — the fix targeted the very lock those workers
contend on). The merged fix went live 2026-08-08, but the paired
re-enable needed UAC elevation and the prompt went unanswered; the two
workers sat `Disabled` until 2026-08-10 07:51 HST — **~2 days of zero
factory throughput on an already-merged fix**, six days before the
2026-08-16 final-submission deadline. Nothing between sessions surfaced
it. The human-handoff half of this lesson is in
`.claude/rules/factory-resume-probe.md`'s 2026-08-10 addendum; this half
is the detection gap: *the standing liveness check itself could not have
caught it.*

**Two mitigations, both applied 2026-08-10:**
1. `State` added to the mandatory check block above, and
   `scripts/verify_factory_tasks.ps1` created as the one-command form
   (read-only; prints a loud `VERIFY: FAIL` line naming any task that is
   not `Ready`/`Running`).
2. `docs/weekly-review-checklist.md` step 15 — run that script first, so
   a between-session disable surfaces at the next weekly review rather
   than at the next slice's start-of-session probe.

**Standing rule for any slice that takes a hold:** a `PAUSE` file or a
`Disable-ScheduledTask` taken during a slice is DEBT owned by that slice.
Do not close Finish (or enter Wrap-Up) until the lift is independently
verified — `scripts/verify_factory_tasks.ps1` returning PASS plus a real
post-lift firing/throughput receipt — not merely issued. "The enable
command was run" is not evidence; `State = Ready` is.

## 2026-08-13 addendum: `unverified-automation-claim` — never cite prior-session automation as existing without a receipt

This file's whole liveness-check discipline exists because a Scheduled
Task (or a script) can be described in prose without actually running —
or, the sharper version confirmed this session, without existing at all.
A prior session's notes claimed a "scratch DB cleanup cron" ran
successfully between sessions. During the 2026-08-13 census-screening-regime
disk-fill triage, this claim was checked directly (`Get-ScheduledTask`
plus a repo-wide grep for the script it would have called) and **refuted
— no such Scheduled Task or script exists anywhere in the codebase or Task
Scheduler.** The claimed automation was never real; something else
(ultimately a dead 30GB system-managed pagefile plus episodic VSCode/torch
bursts, unrelated to any cron) was reclaiming or consuming the disk space
the prior note attributed to it.

**Rule, generalized beyond this file's own scheduled-task roster:** any
claim that automation — a cron, a Scheduled Task, a background script —
"ran" or "exists" between sessions, made in plan.md, a memory file, or
prose recall, must be checked against a live receipt
(`Get-ScheduledTask -TaskName <name>`, or a repo grep confirming the
claimed script file actually exists) before being treated as fact in the
current session's reasoning. Do not propagate a prior session's
description of automation forward as ground truth — verify first,
assume second. This generalizes the existing terminal-marker-liveness and
long-lived-worker-code-staleness checks in this file (which assume the
task IS real and ask whether it's healthy) to the prior, cheaper question:
does the claimed automation exist at all. See memory
`census-screening-regime-2026-08-13.md` for the full incident.
