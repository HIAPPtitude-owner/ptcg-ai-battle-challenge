# Operating the Agent Factory

## Commands

- Continuous watch firing (what the scheduled task actually runs, every 15 min + at startup):
                             uv run python scripts/factory_watch_once.py
- Dry-run a single firing (never uploads): uv run python scripts/factory_watch_once.py --no-submit
- One manual cycle now:      uv run python scripts/factory_cycle.py
- Dry-run a cycle (never uploads):  uv run python scripts/factory_cycle.py --no-submit
- Small/fast cycle:          uv run python scripts/factory_cycle.py --games 50 --max-candidates 2
- Custom budget/cadence:     uv run python scripts/factory_cycle.py --budget-minutes 60 --cadence 1
- Seed/refresh candidates:   uv run python scripts/seed_candidates.py
- Training daemon:           uv run python scripts/factory_daemon.py --deck src/ptcg/decks/candidates/<deck>.csv --cycles 2 [--games N] [--epochs N] [--stop-file <path>]
- Matrix worker (manual):    uv run python scripts/factory_matrix_worker.py --max-ticks N [--block-games N]
- Trainer worker (manual):   uv run python scripts/factory_trainer_worker.py --max-ticks N
- Register all three scheduled tasks: powershell -ExecutionPolicy Bypass -File scripts\register_factory_task.ps1
- Change the watch task's repetition interval: powershell -ExecutionPolicy Bypass -File scripts\register_factory_task.ps1 -IntervalMinutes 30
- Remove all four tasks (incl. any lingering old one): powershell -ExecutionPolicy Bypass -File scripts\register_factory_task.ps1 -Unregister
- Preview registration:      powershell -ExecutionPolicy Bypass -File scripts\register_factory_task.ps1 -DryRun (prints the task name/action/triggers/settings it would register for all three tasks -- never touches Task Scheduler)

`register_factory_task.ps1` now registers **three** scheduled tasks in one
run: the watch task (`ptcg-factory-continuous`) plus the two continuous
workers (`ptcg-factory-matrix`, `ptcg-factory-trainer` -- see "Continuous
workers" below). It is idempotent: re-running it (e.g. to change
`-IntervalMinutes`, which only affects the watch task's own cadence, not the
workers' fixed watchdog interval) updates each existing task in place rather
than creating a duplicate (`Register-ScheduledTask -Force`). Every
non-DryRun register/unregister call also auto-retires any lingering
`ptcg-factory-nightly` task left over from the prior once-per-night design,
so re-running this script is the correct way to migrate a machine off the
old schedule. Registration is **elevated** (Task Scheduler registration
requires it) and **register-first-then-retire**: all three new/updated tasks
are registered and individually verified with `Get-ScheduledTask` before the
old task is ever touched, so a mid-run failure never leaves the machine with
zero factory tasks (see the go-live incident, commits `72bc0f8`/`225686c`).
Real registration against Brad's machine is deliberately NOT part of any
implementation task -- it happens once, at the acceptance task's rung-3 step,
run directly by the orchestrator (per the safety-classifier split-dispatch
lesson) -- do not register the tasks against a half-built factory.

**Go-live default: orchestrator-driven UAC elevation, not a manual elevated
shell.** The harness PowerShell is non-elevated, so the orchestrator elevates
itself for the one registration call:
`Start-Process powershell -Verb RunAs -Wait -ArgumentList '-ExecutionPolicy','Bypass','-File','scripts\register_factory_task.ps1'`.
This is the proven path (compute-saturation go-live, 2026-07-20): a first
attempt that handed the elevated command to Brad to run manually reported
"Done" while nothing was actually registered -- the orchestrator-UAC retry
registered cleanly and is now the recommended default. Because the elevated
console closes on completion (taking its output with it), wrap the script in
`Start-Transcript`/`Stop-Transcript` OR verify AFTER with a non-elevated
`Get-ScheduledTask -TaskName ptcg-factory-continuous` -- never trust the
elevated window's self-report (per the exit-code-laundering and
elevated-context-PATH lessons). Only fall back to handing Brad a manual
elevated shell if the UAC prompt itself cannot be driven.

## Where things live

- Candidate queue ledger:   experiments/factory/candidates.json (git-tracked)
- Ledger lockfile:          experiments/factory/candidates.json.lock (see "Concurrent access" below -- do NOT hand-delete a live one)
- Matrix tournament ledger: experiments/factory/matrix.json (distinct file/lock from the candidate ledger -- BT ratings + per-pair game counts, see "Continuous workers" below)
- Matrix worker lock:       experiments/factory/matrix.worker.lock (single-instance guard, distinct from candidates.json.lock and watch.lock)
- Trainer worker lock:      experiments/factory/trainer.worker.lock (single-instance guard)
- Worker heartbeats:        experiments/factory/matrix_heartbeat.json / trainer_heartbeat.json (written on every tick, including paused/idle/error ticks -- see "Continuous workers" below)
- Trainer worker data dir:  experiments/factory/ (per-cycle self-play JSONL + daemon state; 20GB disk cap, see "Continuous workers" below)
- Daily submission counter: experiments/factory/submission_counter.json (hard cap 5/day, UTC-anchored)
- Cycle digests:            experiments/factory/digests/cycle-<timestamp>.md (read these -- only written for firings that did something, see "Continuous watch loop" below)
- Cycle logs:               experiments/factory/logs/cycle-<timestamp>.log
- Watch log (no-op trail):  experiments/factory/logs/watch.log -- one line per watch firing, including no-ops; see "Continuous watch loop" below
- Watch instance lock:      experiments/factory/watch.lock (distinct from candidates.json.lock -- see "Continuous watch loop" below)
- Training-once-per-day stamp: experiments/factory/training_stamp.json (legacy path kept in `watch_paths()` but no longer read/written by the watch loop -- training moved to the trainer worker, see "Continuous workers" below)
- Value-net daemon state:   experiments/factory/daemon_state.json
- Policy-net daemon state:  experiments/factory/policy_daemon_state.json
- Ladder log:               experiments/LADDER.md (auto-regenerated every harvest)
- Methodology journal:      docs/writeup-notes.md (append-only)
- Bundles are staged under: build/factory/ (not git-tracked)
- Generated deck variants:  src/ptcg/decks/candidates/generated/ (written by `matrix_decks()`/`refill_queue()`, `src/ptcg/factory/deck_matrix.py`) -- like all other factory output these files are session-commit drift: they land on disk as a side effect of a watch firing but are NOT auto-committed (no-autocommit convention unchanged, see commit `04f9fd0`); a development or weekly-review session is responsible for `git add`-ing new variants alongside ledger/digest/log drift.

## Continuous watch loop (factory_watch_once.py)

The scheduled task (`ptcg-factory-continuous`) does not call
`factory_cycle.py` directly -- it calls `scripts/factory_watch_once.py`
(`watch_once()` in `src/ptcg/factory/watch.py`) once per firing, every
`-IntervalMinutes` (default 15) plus once at machine startup. Each firing:

1. **Throttles itself.** `throttle_below_normal()` sets the process to
   Windows `BELOW_NORMAL_PRIORITY_CLASS` (child processes inherit it) so an
   always-on loop firing every 15 minutes doesn't starve interactive use of
   the machine. Windows-only; a no-op elsewhere.
2. **Checks PAUSE first.** If `experiments/factory/PAUSE` exists, the firing
   exits immediately -- no harvest, no evaluate, no submit -- and records a
   `"paused"` line in `watch.log`. Same PAUSE file as the old nightly cycle
   (see below); nothing about pausing changed except how often a paused
   firing gets skipped (every 15 min instead of once a night).
3. **Takes the instance lock.** `instance_lock(experiments/factory/watch.lock)`
   fails FAST (0.5s timeout) if another firing is already live, logging
   `"busy: another firing holds the lock"` and exiting rather than queuing
   or blocking -- the next firing 15 minutes later is the retry. This lock
   is a distinct file from `candidates.json.lock` (see "Concurrent access"
   below) and has a much longer stale-break window (8h, matching the
   longest legitimate holder: a training run) versus the ledger lock's 120s.
4. **Refills the active pool only when it sits below `POOL_CAP` (24).**
   `refill_queue()` (`src/ptcg/factory/deck_matrix.py`) enqueues up to
   `min(--max-refill (default 10), POOL_CAP - len(active_pool))` new cells
   from the deck matrix (`SEEDS` + generated mutation variants, deduped by
   content hash) and/or net-eligible top-N heuristic decks; the firing logs
   `"refill: enqueued N"`. This is keyed to the pool cap, not queue
   emptiness -- a pool sitting at or above 24 active (non-retired)
   candidates skips refill entirely regardless of how many are `QUEUED`.
5. **No longer evaluates or trains candidates.** As of the compute-saturation
   slice, `QUEUED -> EVALUATED` promotion and net training have moved off
   this loop entirely: the matrix worker (`ptcg-factory-matrix`) owns
   evaluation via its own continuous tournament scheduling, and the trainer
   worker (`ptcg-factory-trainer`) owns training on its own cadence -- see
   "Continuous workers" below. `factory_watch_once.py`'s per-firing cycle
   call runs with `skip_eval=True`; there is no more once-per-day training
   tier, `training_stamp.json`/`training_due()` gate, or `--skip-training`
   flag on this loop.
6. **Runs one throttled gate/submit/harvest cycle.** `run_cycle(...,
   cadence_per_day=5, suppress_empty_digest=True, skip_eval=True)` -- the
   gate keys off each candidate's `matrix_rating` (Bradley-Terry, from the
   matrix worker) once it has coverage, falling back to `local_wr` below
   coverage or if a rating is somehow still unset (see "Submission policy
   knobs" below for the cadence/gate details).
7. **Renders the status dashboard**, then **writes exactly one watch.log
   summary line** for the firing: `"cycle: noop"` for a no-op, or `"cycle:
   evaluated=<n> actions=<n> digest=<path or None>"` otherwise.

`factory_watch_once.py` CLI flags (watch-specific defaults, NOT the same as
`factory_cycle.py`'s): `--no-submit`, `--games` (150), `--budget-minutes`
(60.0 -- the watch default, deliberately shorter than `factory_cycle.py`'s
own 240 default so a single firing can't starve the next one), `--max-candidates`
(None), `--cadence` (5), `--max-refill` (10). (`--skip-training` was retired
along with the once-per-day training tier -- see "Continuous workers" below.)
The script
exits 0 in every non-crash outcome, including `busy`/`paused` -- Task
Scheduler treats a nonzero exit as task-failure noise, and busy/paused are
expected, routine outcomes at 15-minute cadence, not failures.

**`watch.log` is the no-op trail; digests are not.** A cycle digest
(`experiments/factory/digests/cycle-<timestamp>.md`) is written ONLY when a
firing actually did something -- the no-op test is exactly
`not evaluated and not actions and (harvest_res is None or
harvest_res.scored_updates == 0)` (`src/ptcg/factory/cycle.py`).
`submissions_today` deliberately does not factor into that test, since it
stays truthy for the rest of the UTC day after any upload and would
otherwise suppress every later firing's digest even when that firing
evaluated new candidates. At 15-minute cadence most firings across a day
ARE no-ops (empty queue, nothing new to harvest), so `watch.log` -- not the
digests directory -- is where to check "did the loop actually tick" for any
given 15-minute window; open a digest only for firings that moved the
ledger.

**Cadence-5 default.** `factory_cycle.py --cadence` / `run_cycle`'s
`cadence_per_day` default changed from 2 to 5 (`src/ptcg/factory/gate.py`
`decide(..., cadence_per_day: int = 5, ...)`), matching the Kaggle hard cap
(`HARD_DAILY_CAP = 5`, still unconfigurable). With the loop firing every 15
minutes instead of once a night, more evaluation cycles happen per day, so
the submission-rhythm knob needed the same headroom as the absolute cap
rather than throttling submissions well below it.

## Continuous workers (matrix + trainer)

The compute-saturation slice split evaluation and training off the 15-minute
watch loop into two independent, always-on worker processes, each registered
as its own Windows Scheduled Task by `register_factory_task.ps1`:
`ptcg-factory-matrix` (runs `scripts/factory_matrix_worker.py`) and
`ptcg-factory-trainer` (runs `scripts/factory_trainer_worker.py`).

**Watchdog pattern, not a work-cadence knob.** Both tasks share an
`AtStartup` trigger plus a 15-minute repetition trigger
(`$WorkerWatchdogMinutes`, fixed independently of the watch task's own
`-IntervalMinutes`), with **no execution time limit** (unlike the watch
task's 8h cap) -- a worker is meant to run forever once started. Each worker
holds its own single-instance lock for as long as it's alive
(`experiments/factory/matrix.worker.lock` /
`experiments/factory/trainer.worker.lock`), so the watchdog trigger firing
every 15 minutes while the worker is already running just finds the lock
held and exits immediately, logging `"busy"` (exit 0) -- it is not a
redundant second worker. The watchdog trigger only matters when the worker
process has actually died (crash, reboot, manual kill): the next watchdog
firing (or `AtStartup`) restarts it.

**Matrix worker** (`scripts/factory_matrix_worker.py` ->
`worker_tick`/`src/ptcg/factory/tournament.py`): loops continuously, playing
the pair of active (non-retired) candidates with the fewest games recorded
against each other -- a round-robin-style tournament scheduler, not a
one-shot batch eval. Each played block writes results into
`experiments/factory/matrix.json` (a separate ledger/lock from
`candidates.json`) and fits Bradley-Terry ratings (`matrix_rating`) over the
accumulated game history; a candidate is promoted `QUEUED -> EVALUATED` once
it clears coverage (`matrix_games >= 15` and `matrix_opponents >= 8`). The
active pool is capped at `POOL_CAP = 24`: once at or over the cap, the
worker retires the lowest-rated *eligible* candidate (`matrix_games >= 15`,
i.e. `RETIRE_FLOOR_GAMES`, and not protected) on every tick until back at or
under 24 -- the manually-pinned incumbent and any candidate that is or has
ever been on the ladder (`is_protected`) are never force-retired regardless
of rating. Sleeps 5s between ticks after a `"paused"`/`"idle"` tick, loops
immediately after a `"played"` tick.

**Trainer worker** (`scripts/factory_trainer_worker.py` -> `trainer_tick`/
`src/ptcg/factory/trainer_worker.py`): loops continuously, picking the
**highest-`matrix_rating`** candidate's deck that doesn't already have a
trained value-net weights file, generating self-play training data for it,
training, and registering the result -- top-rated-first rather than the old
once-per-day "first eligible deck" order. Training data is **deleted after
a successful run** (it is not kept around like the old Slice-7A daemon's
data) to bound disk usage; a **20GB disk cap** (`DISK_CAP_GB`, checked
against the trainer's own data directory before any generation starts) makes
a tick return `"disk-capped"` (loud log, no training) rather than filling the
disk if cleanup ever falls behind generation. A training failure inside one
tick is caught and logged as an `"error"` tick, not a crash -- the loop
survives to the next tick. Sleeps 60s between ticks after a
`"paused"`/`"idle"`/`"disk-capped"`/`"error"` tick (training cycles are
expensive, no need to hammer the ledger between them), loops immediately
after a `"trained"` tick.

**Heartbeats.** Both workers write `experiments/factory/matrix_heartbeat.json`
/ `experiments/factory/trainer_heartbeat.json` (`{"ts": iso-utc, "detail":
str}`) at the **start** of every tick -- including paused, idle,
disk-capped, and error ticks -- so a liveness check sees a fresh timestamp
even when the tick does nothing. The status dashboard (see "Status
dashboard" below) reads both files and shows a red `STALE` badge once a
worker's heartbeat is older than `WORKER_STALE_MIN` (30 minutes); a missing
heartbeat file renders as a plain grey "not registered" badge (expected
pre-go-live), never the red stale badge.

## Pausing the watch loop AND both continuous workers (single kill switch)

Create the pause file and the next firing of the watch loop, the matrix
worker, and the trainer worker all exit immediately without doing anything
else -- one file stops all three:

    echo paused > experiments/factory/PAUSE

Delete the file to resume:

    del experiments\factory\PAUSE     (PowerShell: Remove-Item experiments/factory/PAUSE)

This PAUSE-file check covers `scripts/factory_watch_once.py` (and, by
extension, the `scripts/factory_cycle.py` harvest/evaluate/gate/submit logic
it calls each firing), `scripts/factory_matrix_worker.py`, and
`scripts/factory_trainer_worker.py` -- each checks `paths.pause_file` at the
top of its own tick/firing (after writing a heartbeat, for the two workers,
so a liveness check still sees a fresh timestamp on a paused tick). It does
**not** cover the older, separately-invoked training daemon
(`scripts/factory_daemon.py`, Slice 7A) -- see the next section for that.

## Stopping the training daemon

`src/ptcg/factory/daemon.py`'s `run_daemon` accepts a `stop_path`
(wired by default in `scripts/factory_daemon.py --stop-file`, default
`experiments/factory/DAEMON_STOP`). Create the stop file and the daemon
exits cleanly at the next checkpoint:

    echo stop > experiments\factory\DAEMON_STOP     (PowerShell: New-Item experiments/factory/DAEMON_STOP)

Delete the file before the next run (it is not auto-cleared):

    del experiments\factory\DAEMON_STOP     (PowerShell: Remove-Item experiments/factory/DAEMON_STOP)

The stop file is checked at two points per cycle, not just between whole
cycles: (1) between cycles, before the next cycle's producer/consumer work
starts, and (2) between the producer and consumer stages WITHIN a cycle,
after `prepare_data` returns but before `train` is submitted. Either
checkpoint exits without raising and without registering a partial
candidate -- `state.next_cycle` reflects exactly the last fully-completed
cycle, so a rerun resumes cleanly. It does **not** interrupt a subprocess
already in flight (`prepare_data`/`train` mid-call) -- the checkpoint only
fires between stages, not inside one.

- This is distinct from `experiments/factory/PAUSE`, which only affects
  `scripts/factory_watch_once.py`/`scripts/factory_cycle.py` (the
  harvest/evaluate/gate/submit cycle, fired every 15 min by the continuous
  watch loop) -- the daemon does not look at `PAUSE`, and the cycle does not
  look at `DAEMON_STOP`.
- A wedged trainer subprocess (e.g. `trainer.train()` hanging on the GPU
  worker) still blocks the daemon until that subprocess call itself times
  out. `PerDeckNetTrainer._run` passes an explicit 24h subprocess timeout
  (`SUBPROCESS_TIMEOUT_S` in `src/ptcg/factory/trainers.py`), so a genuinely
  hung generation/training process eventually raises `TimeoutError` rather
  than blocking forever -- the daemon's crash isolation then persists state
  and re-raises, same as any other cycle failure. If a run looks stuck well
  short of 24h, check whether the underlying subprocess is still making
  progress (its own log output) before assuming the daemon is broken.
- Killing the process (Ctrl-C in the foreground, or
  `Get-Process`/`Stop-Process` if launched detached) remains a valid
  fallback and is still the only way to interrupt a subprocess call already
  in flight -- the stop file only stops the daemon from STARTING further
  work, it cannot cancel work already handed to a subprocess.
- Per-cycle state is persisted to disk before/after each cycle
  (`state.next_cycle`), so killing the daemon between cycles is always safe
  to resume from -- killing it mid-cycle loses that cycle's in-flight work
  but not the ledger (writes are lock-protected, see below).

## Concurrent access: the ledger lockfile

`scripts/factory_cycle.py` (harvest/evaluate/gate/submit) and the training
daemon (`src/ptcg/factory/daemon.py`) can both read-modify-write
`experiments/factory/candidates.json` -- the cycle when it registers
harvested results, the daemon when `export_and_register` appends a newly
trained candidate. Both paths take `ledger_lock()`
(`src/ptcg/factory/candidates.py`) before load -> mutate -> save, so a
concurrently running cycle and daemon can never interleave and silently
drop each other's write.

The lock is a sidecar file: `experiments/factory/candidates.json.lock`. It
exists only while a process holds the lock and is deleted when that process
releases it. **If you see this file while the factory is running, leave it
alone** -- it is expected during a live cycle or daemon run, not a stuck
state. A lock older than 120 seconds is presumed abandoned (a crashed
holder) and is broken automatically by the next process that tries to
acquire it; you should never need to delete it by hand. If a lock is stuck
well past 120s, that is itself a signal something crashed without cleanup
-- check the daemon/cycle logs before manually removing the lockfile.

## Submission policy knobs (scripts/factory_cycle.py flags)

- --cadence N        default 5/day rhythm (was 2/day pre-continuous-factory; now matches the Kaggle hard cap since the loop fires many more times per day); merit override bypasses it
- --games N          games per evaluation run (default 150)
- --budget-minutes N wall-clock budget for the evaluation phase (default 240)
- --max-candidates N cap how many queued candidates are evaluated this cycle
- --no-submit        full pipeline, no upload

Gate logic (`src/ptcg/factory/gate.py`): a candidate beating the incumbent
by `edge = candidate.local_wr - incumbent.local_wr > 0` submits if the daily
cadence isn't used up, or if `edge >= merit_margin` (0.05) even when cadence
is used (merit override). A candidate at parity (`edge >= -parity_band`,
0.02) on a novel strategic axis gets one exploration submission per day even
without beating the incumbent, subject to cadence. Hard cap 5/day is NOT
configurable (Kaggle rule); the counter survives restarts, is anchored to
UTC dates, and is reconciled against harvested ladder rows every cycle
(manual uploads count against it too).

## Scheduled Task Verification

**2026-09-01: the factory is fully shut down.** All four Scheduled Tasks
(`ptcg-factory-continuous`, `ptcg-factory-runner`, `ptcg-factory-scheduler`,
`ptcg-factory-ui`) are Disabled by design; `scripts/verify_factory_tasks.ps1`
now expects `State=Disabled` on all four and PASSes against that. See the
2026-09-01 addenda in `.claude/rules/factory-resume-probe.md` and
`.claude/rules/factory-task-scheduler-liveness.md`. The liveness checks
below are historical/reference for a live roster, not the current state.

The continuous watch loop runs unattended via Windows Task Scheduler as
`ptcg-factory-continuous` (registered by `scripts/register_factory_task.ps1`;
a repetition trigger firing every `-IntervalMinutes`, default 15, plus an
`AtStartup` trigger). Recent output in `experiments/factory/` or
`experiments/LADDER.md` is evidence a firing ran at some point -- it is NOT
proof the *schedule* is what triggered it (a manual
`uv run python scripts/factory_watch_once.py` or `factory_cycle.py`
invocation looks identical in the ledger). Before any weekly review draws a
conclusion from a run of unattended output, confirm the task itself is live:

    Get-ScheduledTask -TaskName "ptcg-factory-continuous" | Select-Object -ExpandProperty Triggers
    Get-ScheduledTaskInfo -TaskName "ptcg-factory-continuous" | Select-Object LastRunTime, LastTaskResult, NextRunTime

- `LastRunTime` should fall within the last ~30 minutes of the review start
  time (two firing intervals at the default 15-minute repetition) -- a much
  tighter window than the old once-a-night task, since a healthy loop should
  never go long without ticking.
- `LastTaskResult` of `0` means the task ran and the script exited cleanly;
  a nonzero value means the task fired but `factory_watch_once.py` itself
  crashed outright (recall the script is designed to exit 0 even for
  busy/paused/no-op outcomes, so a nonzero result means something worse than
  routine) -- check `experiments/factory/logs/watch.log` for the last line
  and the matching `experiments/factory/logs/cycle-<timestamp>.log` if a
  cycle was in progress.
- If the task is missing entirely, re-register it (see "Register all three
  scheduled tasks" in Commands above; the same apex applies to
  `ptcg-factory-matrix` and `ptcg-factory-trainer` -- check their own
  `Get-ScheduledTaskInfo` output and heartbeat files too, see "Continuous
  workers" above) -- do not assume "no new output recently" means "nothing
  happened," and do not assume existing output means the task is still
  registered.

See `.claude/rules/factory-task-scheduler-liveness.md` for the full rule and
the datapoint that motivated it (2026-07-14 weekly review: a session
reasoned about 3 nights of factory output as schedule-driven without ever
running `Get-ScheduledTask` to confirm it) -- the rule's task-name and
liveness-window references were updated for the continuous loop in the same
slice that added this section.

## Reading a cycle digest (audit trail)

Two audit-trail fields added in the 2026-07-15 factory-polish slice:

- **Per-baseline win-rate breakdown.** When a candidate carries
  `local_breakdown` (dual-baseline eval), its digest line shows the pooled
  `local_wr` *plus* the per-baseline split, e.g.
  `- <id>: local_wr=0.647/150 [mega-lucario-fighting 0.573 (43/75); mega-starmie-water 0.720 (54/75)] -> submitted`.
  Older ledger entries without the field print the pooled-only line as before.
  Audit dual-baseline behavior from this line — the split is no longer buried
  in `candidate.notes` free text.
- **Re-upload provenance marker.** A guard-driven champion re-upload (the
  champion-pairing recency-eviction guard) carries a `re-upload` tag in its
  Kaggle submission description alongside the `factory` tag; fresh submissions
  do not. Use this to distinguish a protective champion re-upload from a fresh
  challenger submission in Kaggle's submission list. Fresh-submission
  descriptions are byte-identical to pre-slice output (pinned by
  `test_description_byte_identical_for_normal_candidate`).

## Status dashboard

The factory renders a single self-contained HTML status page to
`experiments/factory/dashboard.html` at the end of **every** watch-loop firing
(including paused / lock-skipped / no-op firings, so its "last updated"
timestamp stays honest). The page has zero external requests (inline CSS/SVG),
no runtime dependencies beyond the standard library, and is **gitignored** as
regenerated runtime output. It shows: a RUNNING/PAUSED badge, last-firing
summary and next-expected firing (inferred from `watch.log` recency — a
`>35 min` gap raises a "scheduler stale" warning without querying Task
Scheduler), the `N/5` submission counter, a persistent AUTH-DEAD badge when the
newest digest recorded a `- AUTH: auth-dead` line, one heartbeat badge per
continuous worker (green `worker ✓ <detail>` when its heartbeat is fresh, red
`STALE <n>m` past `WORKER_STALE_MIN` (30 min), grey "not registered" if the
heartbeat file doesn't exist yet -- see "Continuous workers" above), a queue
-> evaluate -> gate -> submit -> harvest pipeline with the incumbent
highlighted and deck-matrix lineage chains, per-candidate ladder score-history
charts (with the 520-575 convergence band shaded), and the last ~5 cycle
digests. Rendering is
failure-isolated: a bug in the renderer logs one line and the cycle proceeds
untouched. Render it on demand between firings with:

    uv run python scripts/render_dashboard.py

Then open `experiments/factory/dashboard.html` in a browser (it auto-refreshes
every 60 seconds).

## Pre-submit auth guard

Added 2026-07-20 after the Kaggle OAuth token expired mid-cycle and the
submit phase crashed with a cryptic `RuntimeError` only at upload time.
`submit_candidates` (`src/ptcg/factory/submit.py`) now probes auth with a
cheap read-only `list_submissions()` call (`kaggle_client.check_auth`)
before touching the counter or any candidate; a dead/expired token logs a
loud `AUTH-DEAD: kaggle auth check failed - skipping submit phase (re-auth
needed): <detail>` line, appears in the digest's Gate actions as
`AUTH: auth-dead - <detail>`, and skips the entire phase with zero
submission-counter reservations consumed. Dry-run cycles (`no_submit=True`)
never probe, so they stay byte-identical.

**As of 2026-07-21, auth uses Kaggle's static KGAT token, not the OAuth
cache.** The 12h-expiring OAuth login cache
(`~/.kaggle/credentials.json`) that caused the 2026-07-20 incident above is
retired (renamed `credentials.json.retired-20260721`, left in place only so
it can't silently shadow anything). Live auth is now a **static,
non-expiring** Kaggle API token stored as a raw 37-byte string (no JSON
wrapper) at `~/.kaggle/access_token` — Kaggle's currently-recommended
method. The CLI also accepts the token via the `KAGGLE_API_TOKEN` env var.
This is a different, newer mechanism than the legacy `kaggle.json`
username+key file; this factory does not use `kaggle.json`. Verified live:
`uvx kaggle competitions submissions` exits 0 with only the static token
present. Zero code changes were needed — `check_auth()` and the Kaggle
client are auth-scheme agnostic, so the swap is transparent to the factory
pipeline. The AUTH-DEAD guard above remains the backstop if this token is
ever revoked.

**Known fragility — the submit path depends on `uvx` resolving `kaggle`
on demand, so ANY cache/disk problem reads as an auth outage. Drafted
backlog item, not implemented.** Every Kaggle call shells out to
`uvx kaggle ...`, which resolves and materializes the package from the uv
cache at invocation time. That makes the entire upload path (and
`check_auth`, and therefore the status page's auth badge) dependent on
free disk plus a healthy `~/.cache/uv` — neither of which has anything to
do with Kaggle credentials. Compounding it, `kaggle_client.check_auth()`
maps *any* CLI launch failure to "auth-dead," so an infrastructure fault
is reported as a credentials fault, sending triage down the wrong path.
*Datapoint (2026-08-12):* `C:` hit 0 bytes free ~08:15; `uvx` could not
write its temp files; the factory reported auth-dead for hours while the
static token was perfectly valid. Resolved by `uv cache clean` + a temp
purge (14.7GB reclaimed); the token was never touched.
**Proposed fix, when this next bites:** install `kaggle` once into a
pinned project venv (`uv add --dev kaggle`, invoked as `uv run kaggle`)
instead of `uvx`-on-demand, removing the per-invocation cache dependency;
and split `check_auth`'s return into `auth-dead` vs `cli-unavailable` so
the two failure modes stop sharing a badge. Build trigger: a 2nd
occurrence of a non-credential failure being reported as auth-dead.

**Re-auth procedure if the static token is ever revoked/rotated:**
generate a new token at kaggle.com -> Settings -> API, then replace the
contents of `~/.kaggle/access_token` with the new raw token string (no
quotes, no JSON, no trailing newline needed). No `uvx kaggle auth login`
step is required for this mechanism.

## Weekly review

Follow docs/weekly-review-checklist.md (created in T14): re-prioritize the
queue from LADDER.md trajectories, author new deck/config candidates,
record strategy decisions in docs/writeup-notes.md.

Watch-loop firings write ledgers/digests/logs/generated decks to disk but
do NOT git-commit; the weekly review (or any development session) is
responsible for committing accumulated factory output.

## Policy-improvement trainer (Slice 7B)

`PolicyImprovementTrainer` (`src/ptcg/factory/trainers.py`) implements the
same `Trainer` Protocol (`src/ptcg/factory/daemon.py:17`) that
`PerDeckNetTrainer` (value net, Slice 7A) already uses, so it plugs into the
existing daemon loop with no factory rework: `prepare_data(cycle)` runs
search-policy self-play (`--policy-targets` mode, resumable, budget-boxed per
the daemon's loop-top budget check), `train(data_path, cycle)` runs
`scripts/train_policy_net.py`, and `export_and_register(...)` registers a
versioned `Candidate` with `agent_kind="search-policy"` (deck, policy-net
weights, the frozen value-net-v2 path, commit traceability).

**Invoking a cycle by hand:**

    uv run python scripts/run_policy_cycle.py --deck src/ptcg/decks/candidates/mega-lucario-fighting.csv --cycles 1 [--games N] [--stop-file <path>]

State is tracked in its own `experiments/factory/policy_daemon_state.json`
(separate from the value-net daemon's state file, so the two trainers never
collide on the same checkpoint). The `experiments/factory/PAUSE` file is
honored the same way it is for `scripts/factory_cycle.py` and the value-net
daemon — create it to make the next policy cycle exit immediately without
generating, training, or registering anything; delete it to resume.

**Operational decision: policy-improvement cycles are NOT part of the
continuous watch loop.** Gen-1's sanity gate (argmax agreement vs. search's
chosen move) FAILED across all three featurizer generations attempted
in-session (v1 0.2874, v2 0.3156, v3 0.3067 — see
`experiments/ANALYSIS-slice7b-policy-improvement.md` for the full saga and
diagnosis). Per the pre-registered fail path, no arena probe was spent and
no candidate was queued. `run_policy_cycle.py` and `PolicyImprovementTrainer`
are real, tested infrastructure — but wiring them into `factory_watch_once.py`'s
per-firing training tier would generate data and train nets against a gate
that is already known to fail, burning compute for no decision value.
**Enabling scheduled policy cycles requires a new Brad decision, gated on a
policy net that first passes the amended gate 1** (argmax agreement ≥ 0.45
AND ≥ 0.75× the measured collision ceiling) on a fresh attempt — most likely
via a different training signal than visit imitation (see the Carry-forwards
section of the analysis doc). Until then, treat `run_policy_cycle.py` as a
manually-invoked research tool. It is not part of either continuous
scheduled worker: the trainer worker (`ptcg-factory-trainer`, see
"Continuous workers" above) trains only the value net, top-rated-deck-first,
via `PerDeckNetTrainer` — a future decision to enable scheduled policy
cycles would most likely mean pointing the trainer worker at
`PolicyImprovementTrainer` instead (or adding a third worker), not reviving
the old watch-loop training tier, which no longer exists.

## Evolutionary population

An opt-in overlay on top of the candidate-ledger pipeline described above:
instead of a flat pool of independently-authored `Candidate` rows, two
**genome pools** hold an evolving agent population and an evolving deck
population, and the matrix worker plays cross-pairings between them
(steady-state tournament) instead of the legacy fewest-games candidate
pairing. Until the pools are seeded (see "Seeding runbook" below) every
piece of this system is a documented no-op and the factory behaves exactly
as described in "Continuous workers"/"Continuous watch loop" above.

**The two pool files + genome model** (`src/ptcg/factory/genomes.py`):
`experiments/factory/agent_pool.json` and `experiments/factory/deck_pool.json`,
each a `{"version": 1, "genomes": [...]}` JSON ledger using the exact same
atomic tmp-then-`os.replace` + `ledger_lock` + refresh-under-lock-merge
conventions as `candidates.json` (`load_pool`/`save_pool`/`pool_merge_save`,
genomes.py:130-165). An `AgentGenome` (genomes.py:49-59) carries its evolvable
search hyperparameters in `config` (the ten numeric genes in `GENE_SPEC` --
`search_budget_ms`, `rollout_depth`, `deviate_min_visits`,
`deviate_value_edge`, `c_uct`, `c_puct`, `prior_tau`, `max_depth`,
`robust_min_visits`, `deviate_min_visit_frac`, each with `(lo, hi, sigma,
cast)` mutation bounds -- genomes.py:27-38 -- plus the two categorical genes
`final_move_rule` and `use_root_prior`, genomes.py:42-44) alongside
`lineage`/`born_at`/`seed`/`rating`/`games`/`notes` bookkeeping. A
`DeckGenome` (genomes.py:72-83) carries a 60-card `cards` list, the
repo-relative `csv` path it's written to, an optional `net_weights` path,
and the same lineage/rating bookkeeping. Both genome types share a
`status` vocabulary (genomes.py:48): `"live"` (counts toward the
population target and is retire/breed-eligible), `"retired"` (excluded from
pairing and from cull/breed targets, per `active_cells`), `"anchor"`
(pinned reference genome -- the heuristic scale-bridge and the incumbent's
verbatim config/deck -- counted in pairing but never retired or bred), and
`"meta-anchor"` (deck-only, harvested-opponent decks injected by Task 11,
same never-retired-by-cull treatment as `"anchor"`). Ids are
content-addressed (`agent_genome_id`/`deck_genome_id`, genomes.py:94-103) so
re-deriving the same config/decklist always yields the same id; a `Cell` is
one `(AgentGenome, DeckGenome)` pairing keyed `cell~<agent_id>~<deck_id>`
(`cell_id`, genomes.py:106-107) and is the atomic unit the tournament plays
games with (`evolution.Cell`/`active_cells`, evolution.py:63-84).

**Dispatch condition: evolution vs legacy tick.** The matrix worker
(`scripts/factory_matrix_worker.py:41-43`) checks a single file's existence
on every tick: `if (paths.root / "experiments" / "factory" /
"agent_pool.json").exists(): return evolution_tick(...)`, else
`return worker_tick(...)` (the legacy candidate-ledger tournament,
unchanged). So the switch is a one-way door -- the moment
`agent_pool.json` exists on disk (seeding, see below), every subsequent
matrix-worker tick plays the genome-pool tournament instead of the
candidate-ledger one, with no separate flag to flip back other than
deleting the pool files (which the seeding script itself refuses to run
against, see below). The trainer worker and watch loop are unaffected by
this switch -- they read `agent_pool.json` independently for their own
purposes (refill-skip and the gate feed, respectively).

**Steady-state tick mechanics + 48h throughput-review knobs**
(`src/ptcg/factory/evolution.py`). `evolution_tick` (evolution.py:237-332)
mirrors `worker_tick`'s TOCTOU discipline: write heartbeat -> honor PAUSE
-> assemble cells from the (unlocked, possibly-stale) pools -> play ONE
block outside any lock -> under `ledger_lock(matrix.json)`, reload the
matrix ledger fresh, record the block + append `matrix_blocks.jsonl`
provenance, bump a persisted `evo_tick` counter -> reload BOTH pools fresh
(another process may have written meta-anchors or ratings while the block
played) -> refit Bradley-Terry ratings (`bt.fit_two_factor`) and per-genome
game counts over the fresh pools -> run one cull/breed step per population
(`_cull_and_breed`, evolution.py:199-234) -> persist via `pool_merge_save`
only. Pairing (`next_cell_pair`, evolution.py:138-196) picks the pair whose
four involved genomes have the fewest total games recorded (coverage mode),
except every `PLAYOFF_EVERY`th tick, which restricts candidates to the top
`PLAYOFF_TOP` cells by combined agent+deck rating first (a periodic
head-to-head among current leaders). The knobs to revisit at the 48h
throughput review, all module constants in evolution.py (lines 114-118,
347-348) rather than CLI flags -- editing them means editing this file and
redeploying, there is no runtime override:

- `TARGET_AGENTS = 12`, `TARGET_DECKS = 12` -- steady-state population size
  per pool; a population under target breeds one offspring per tick without
  retiring anyone (fills gradually), at/over target retires the
  lowest-rated live genome (once it has played `GENOME_RETIRE_FLOOR` games)
  and breeds one replacement.
- `GENOME_RETIRE_FLOOR = 40` -- games a live genome must accumulate before
  it becomes retire-eligible, even if it is the pool's worst-rated member --
  protects a freshly-bred genome from being culled before it has enough
  games to be fairly rated.
- `PLAYOFF_EVERY = 5`, `PLAYOFF_TOP = 5` -- every 5th tick is a playoff tick
  (elite-subset pairing instead of coverage-balancing pairing) among the
  top 5 cells by combined rating.
- `CELL_MIN_GAMES = max(30, MIN_COVERAGE_GAMES)` (currently 30) and
  `CELL_MIN_OPPONENTS = MIN_COVERAGE_OPPONENTS` (8, imported from
  `tournament.py` rather than re-literaled -- see the plan-time constant-
  collision note in evolution.py:342-348) -- the floors a cell must clear
  before `select_best_cell`/`snapshot_cell` (evolution.py:381-441) will turn
  it into a gate-facing `Candidate` snapshot at all; set so a fresh snapshot
  always clears `gate.has_matrix_coverage`'s own floor rather than sitting
  EVALUATED-but-uncovered.
- Throughput to actually measure at the 48h mark: ticks/hour (from
  `evo_tick`'s growth in `matrix.json.meta`), games/hour (from
  `matrix_blocks.jsonl` row count over wall time), and whether the
  population is actually turning over (are any genomes reaching
  `GENOME_RETIRE_FLOOR`, or is the target size too large / games-per-tick
  too slow for real churn within the competition's remaining timeline).

**Seeding runbook** (`scripts/seed_evolution_pools.py`). One-shot founder-
population seeding: reads the pinned incumbent via `gate.incumbent()` (the
same beat-this-bar selector `gate.decide()`/`evo_gate_step` use) out of
`candidates.json`, and the curated seed decks out of `deck_matrix.SEEDS`,
then writes both founder pool files. **Refusal behavior**: `seed_pools`
checks whether EITHER `agent_pool.json` or `deck_pool.json` already exists
BEFORE writing either one (seed_evolution_pools.py:271-277) -- if so it
raises `AlreadySeededError` (CLI: loud `error: ...` to stderr, exit 1, zero
writes) rather than re-seeding a live, already-rated population and
silently forking its lineage. There is no re-seed flag; the documented
recovery is "delete both pool files manually" and accept that this starts
a brand-new founder population from whatever the incumbent/ledger look like
at that moment. **Founder roster**: the agent pool gets one heuristic
anchor (scale bridge, `config={}`), one incumbent anchor (the pinned
candidate's `agent_kind`/`agent_config` verbatim, with any deck-specific
`net_weights` key stripped -- an agent-side field must never carry a
deck-specific value across pairings), and `TARGET_AGENTS - 2` live search
genomes (the incumbent's config completed to the full gene surface via
`complete_gene_surface`/`DEFAULT_GENE_VALUES`, plus 9 `mutate_agent`
jitters seeded `Random(1)..Random(9)`); the deck pool gets the incumbent's
own deck (forced first, flagged anchor), every other distinct active
candidate's deck ranked by best `matrix_rating`, and `deck_matrix.SEEDS`
fills for any remaining slots up to `TARGET_DECKS`. **`--dry-run`** builds
and logs the full roster (every genome's id, kind/status, notes) but writes
nothing; because a dry-run flag by definition never exercises the real
write path (see `.claude/rules/` on dry-run-only testing gaps), the write
path itself is covered directly by `tests/test_seed_evolution_pools.py`
against tmp paths, not left to manual dry-run inspection.

**Post-merge go-live rung** (run once, after this branch merges to master --
not part of any implementation task's own Finish gate):

1. **Seed pools on master** with both continuous workers paused:
   `echo paused > experiments/factory/PAUSE`, then
   `uv run python scripts/seed_evolution_pools.py` (the real run, not
   `--dry-run`), then remove the PAUSE file.
2. **Verify evolution is live within ~30 minutes**: tail
   `experiments/factory/matrix_blocks.jsonl` and confirm new rows carry
   `cell~<agent_id>~<deck_id>` ids in the `"a"`/`"b"` fields (evolution.py's
   `Cell.id`/`cell_id`, not a plain candidate id -- the legacy tick's rows
   look like a bare candidate id in the same fields, so this is the
   at-a-glance signal the switch actually flipped); open the dashboard's
   "Evolutionary population" panel and confirm it has left the
   "evolution not seeded" placeholder state; re-run the
   `Get-ScheduledTask`/`Get-ScheduledTaskInfo` liveness check (see
   "Scheduled Task Verification" above) for all three tasks, unchanged
   names.
3. **First harvest** (episode-harvester phase 2, see below): confirm one
   real `check_and_harvest` firing produces either a harvested extract or a
   clean `"no-new"`, and that a harvest failure does not block that
   firing's gate/submit cycle (it's failure-isolated by construction, see
   below -- this rung is confirming that in production, not just in tests).
4. **48h throughput review**: measure games/hour and ticks/hour on the
   evolved pool per the knobs list above; adjust `TARGET_*`/floors if
   real-world throughput doesn't support the current population size or
   `GENOME_RETIRE_FLOOR`; record the measurement and any adjustment in
   `.claude/plan.md` and in `docs/weekly-review-checklist.md`.

**Episode harvester operation** (`src/ptcg/factory/episodes.py`,
`check_and_harvest`, episodes.py:237-270). Runs on **every** watch-loop
firing (`factory_watch_once.py:113-119`, before the cycle's gate step),
failure-isolated the same way `dashboard.safe_render` is -- a Kaggle outage
or malformed corpus must never block that firing's gate/submit/harvest
cycle; `check_and_harvest` itself never raises (`error:<msg>` return
string), and the watch loop wraps the call in a second try/except layer as
a hard backstop. Each firing: (1) downloads the tiny index manifest
(`manifest.csv`, ~6KB, dataset `kaggle/pokemon-tcg-ai-battle-episodes-index`)
and short-circuits to `"no-new"` when no day in it is newer than the
last-harvested stamp; (2) on a new day, downloads that day's dataset as a
raw, **unextracted** `.zip` (the corpus is ~21GB uncompressed/day --
`unzip=False`) and streams each `<id>.json` episode straight out of the zip
(`_iter_raw_episodes`), extracting only episode records that match OUR team
name, then deletes the raw zip in a `finally` block regardless of outcome.
**Stamp-file semantics**: `experiments/factory/episodes/harvest_stamp.json`
holds `{"last_day": "YYYY-MM-DD", "harvested_at": <iso>}`; the repo
currently ships this file pre-seeded to `"last_day": "2026-07-21"` with a
`"note"` field explaining it was seeded pre-review specifically to defer
the first real harvest to go-live rung 3 above -- a fresh clone/checkout
without that file would harvest the current newest day on its very first
firing instead. **Extract-then-delete**: harvested records are appended
one-JSON-object-per-line to `experiments/factory/episodes/extracts.jsonl`
(never the raw episode JSON itself, which is deleted with the zip) --
`extract_record` (episodes.py:127-154) keeps only `episode_id`, `day`,
`opponent_deck` (60 card ids), `our_result` (win/loss/draw), `timeout`
(bool), `n_turns`, and a `opponent_deck_hash`. **2GB cap**:
`EPISODES_CAP_GB = 2` (episodes.py:58); `_prune_cap` drops whole oldest
days from `extracts.jsonl` (never a partial day) once the file exceeds the
cap. **Auth dependency**: harvesting calls the same `KaggleClient._cli`
path (`dataset_download`) as every other Kaggle CLI operation in this
factory, so it depends on the same static, non-expiring KGAT
`~/.kaggle/access_token` documented in "Pre-submit auth guard" above -- a
dead/revoked token surfaces as a `check_and_harvest` `"error:..."` return
(logged to `watch.log` as `episodes: error:...`), not a crash, and does not
consume any submission-counter budget. **BSCode team-name matching
caveat**: `OUR_TEAM_NAME = "BSCode"` (episodes.py:55) is the *only* match
key -- episode metadata carries no submission id or description, so
matching is purely by the team display name (`_team_names`, checking
`info.TeamNames` with an `info.Agents[].Name` fallback) after a cheap raw-
bytes substring pre-filter. If the Kaggle-displayed team name for this
account is ever changed, matching silently stops finding our episodes
(every day harvests `0` ours, no error) rather than failing loudly --
worth a spot-check in the weekly review if harvested counts unexpectedly
flatline.

**Meta-anchor injection** (`inject_meta_anchors`, episodes.py:350-440;
`top_meta_decks`, episodes.py:290-347). Runs in the watch loop
(`factory_watch_once.py:125-134`) **immediately after a successful harvest
only** -- gated on `hres.startswith("harvested:")`, so a `"no-new"` or
`"error:..."` firing never touches the deck pool -- and is itself failure-
isolated the same way harvest is. `top_meta_decks` ranks opponent decks
harvested over a trailing 7-day window (anchored to the newest day *in the
file*, not wall-clock `now`, so a harvest outage doesn't silently empty the
window) by weighted frequency of `opponent_deck_hash`, weighting a deck 2x
for every episode where it beat us and 1x otherwise, returning up to 3
decklists highest-weight first. `inject_meta_anchors` adds any of those
not already present (by content hash, checked against the WHOLE pool
regardless of status) as `status="meta-anchor"` `DeckGenome`s. **Cap and
eviction**: live meta-anchors are capped at `META_ANCHORS = 3`
(episodes.py:65); once the cap is full, admitting a new qualifying deck
retires the existing live meta-anchor with the fewest recent-window
observations (an existing meta-anchor absent from the current `decks` list
entirely ranks last and is evicted first). The retire-and-save step
re-fetches the pool fresh immediately before writing (per
`.claude/rules/single-actor-worker-tests.md`) so a concurrent matrix-worker
rating/games update to that same row survives instead of being clobbered.

**PAUSE semantics for evolution.** The single kill switch documented above
(`experiments/factory/PAUSE`) covers every part of this system, via two
separate checks rather than one shared code path: (1) `evolution_tick`
checks `paths.pause_file.exists()` itself, after writing its heartbeat but
before assembling cells or playing anything (evolution.py:264-265) -- this
is what stops breeding/tournament play when the matrix worker is dispatched
into `evolution_tick`; (2) `watch_once` checks the same PAUSE file at the
very top, before taking its instance lock (`factory_watch_once.py:87-90`) --
because episode harvesting, meta-anchor injection, and `evo_gate_step` all
run inside that same locked block, pausing the watch loop pauses all three
of them together, not individually. There is no separate pause flag for
"breeding but not harvesting" or vice versa -- one file, one switch, for
the whole evolutionary system plus the legacy pipeline it overlays.

**Dashboard panels** (`src/ptcg/factory/dashboard.py`). Two panels render
unconditionally on every watch-loop firing (failure-isolated like the rest
of the dashboard), both degenerate-safe before seeding/harvesting has
happened: **"Evolutionary population"** (`render_evolution`,
dashboard.py:684-705) shows, once `agent_pool.json`/`deck_pool.json` exist
(`evo.seeded`), a live/retired/anchor/meta-anchor status-count breakdown
for each pool ("Agents"/"Decks" blocks), the top 5 cells by combined
`exp(agent.rating + deck.rating)` strength among cells where both sides
have a fitted rating ("Top cells"), and the 5 most-recently-born genomes
across both pools regardless of status ("Recent births") plus the 5
most-recently-born genomes currently `status=="retired"` ("Recent
retirements" -- a documented proxy for retirement recency, since neither
genome dataclass has a `retired_at` field). Before seeding it renders a
plain "evolution not seeded" placeholder. **"Loss / meta panel"**
(`render_loss`, dashboard.py:722-739) shows, once `extracts.jsonl` has at
least one parseable record, the overall timeout-loss percentage and the
top 5 opponent-deck clusters by episode count with each cluster's win rate
against us -- before any harvest it renders "no harvested episodes yet".

## Submission strength gate (anchor check)

Added 2026-08-01 after a crowned champion (Kaggle ref 55125891, scored
325.1 -- well below the historical 520-575 convergence band) exposed that
`CROWN` is purely RELATIVE: the best aggregate win% among sibling survivors
still "wins" even when the whole cohort is weak. This stage
(`src/ptcg/factory/anchor.py`) adds an ABSOLUTE strength check against the
known-good live ladder identity (`HeuristicAgent` + mega-lucario-fighting,
`ptcg.agents.current`) before any crowned baseline is allowed to upload --
both the champion mark-trigger and the daily-floor probe. Design spec:
`docs/superpowers/specs/2026-08-01-submission-strength-gate-design.md`.

**Constants** (`anchor.py`): `ANCHOR_VERSION = "anchor-heuristic-v0"`,
`ANCHOR_GAMES = 200`, `ANCHOR_BAR = 0.55`. The champion is always
`agent_version_a` in anchor games; draws (`winner == 2`) count as champion
losses -- only `winner == 0` counts as a win, the conservative reading.
Boundary: 110/200 = 0.550 passes. **Relaxing the bar means editing
`ANCHOR_BAR` in `anchor.py`** -- there is no runtime/CLI override.

**Mechanics.** Every `loop_scheduler.py` tick calls
`anchor.enqueue_anchor_series(conn)` then `anchor.resolve_anchor_check(conn)`
(both idempotent/no-op-safe on every call). The first ensures the CURRENT
baseline has an `anchor_checks` row and a full 200-game series queued
(top-up-safe after a partial loss, and self-backfills for a baseline
crowned before this gate existed); the second resolves any pending check
whose series has fully completed (>= `games_planned` done games). Anchor
games are enqueued at `priority=1.0`, jumping the claim queue ahead of
ordinary tournament play, since the verdict blocks uploads -- expect
resolution in roughly the same 35-60 min window the go-live rung uses for a
fresh 200-game series at observed pool throughput. The anchor deck row is
registered `status='finalist'` and **deliberately without a `coverage`
row** -- see the docstring on `ensure_anchor_deck` in `anchor.py` for why
(a fresh zeroed coverage row would dip `census.census_complete` to False at
every go-live until ~15 anchor games played; safe to omit because every
coverage consumer filters on `status='active'` or `purpose='screening'`,
neither of which the anchor -- `status='finalist'` -- ever matches).

**Reading `anchor_checks`.** One row per baseline version:
`version, offspring_id, deck_id, games_planned, games_done, wins, wr,
verdict (pending|pass|fail), created_at, resolved_at`. Use
`anchor.anchor_status(conn, version)` as the read-only accessor -- it
returns `'absent'` (no row yet, treated the same as pending by callers)
for a version that has never had a series enqueued.

**Log lines** (`subscheduler.maybe_submit`, checked before any other
submit logic on every tick): `anchor-gate: awaiting verdict <version>
<done>/<planned>` while the series is still running (verdict `pending` or
`absent`), or `anchor-gate: FAIL <version> wr=<wr:.3f> bar=0.55 -- upload
blocked` once it resolves fail. A `pass` verdict logs nothing extra and
falls through to the normal mark/daily-floor submit logic. Every
`maybe_submit` call also emits exactly one terminal marker line per the
terminal-marker-liveness convention (see "Scheduled Task Verification"
above) -- `submit-scheduler: <identity>=<outcome>,...` -- and when the
gate blocks the upload this reads `submit-scheduler: GATE=anchor-<verdict>`
(`<verdict>` is `pending`, `absent`, or `fail`; a passing verdict never
short-circuits, so `GATE=anchor-pass` never appears in this marker).

**Under the 0.55 bar, a run of FAIL verdicts is the gate working as
designed, not a malfunction.** Slices 4-7B repeatedly measured
search-based agents at 0.42-0.54 win rate against this same anchor
deck/agent across dozens of series. Only investigate a `pending` verdict
that sits stuck far beyond ~1h of pool throughput without resolving --
see `docs/weekly-review-checklist.md` for the routine version of this
check.

Note: this is unrelated to the genome-pool `"anchor"` **status** value
described in "Evolutionary population" above (a pinned reference genome in
the co-evolution pools, superseded by the generational champion tournament
architecture) -- same word, two different mechanisms.

## Pool curation UI

Added 2026-08-05 (`ui-remove-any-deck`). `ptcg-factory-ui` (see "Scheduled
Task Verification" above) gained three pages beyond the original bottom-10
Remove/Pass review (`/`) and the status page (`/status`):

- **`GET /pool`** -- every `status='active'` concept, one row per concept
  (canonical shell only). The anchor deck (`status='finalist'`) is pinned
  first as a form-less row badged `anchor -- protected`; the concept
  currently backing the live baseline is badged `current champion`. Each
  row's card list renders inside a collapsed `<details>` element and
  expands on click. Every non-anchor row carries its own `/concept-decision`
  Remove form.
- **`GET /search?q=&status=`** -- substring match on `concepts.id` OR
  `concepts.cores` (`LIKE '%q%'` on either column), scoped by a `status`
  tab: `untested`, `culled`, `active`, `all`. The page itself renders a
  `GET` query form (a `q` text input pre-filled with the current query,
  submitting back to `/search`) plus a 4-way tab-link strip that preserves
  the current `q` on every link (IMPORTANT-3 fix, 2026-08-05 -- previously
  the page had no in-page way to change `q`/`status` at all, only usable by
  hand-editing the URL). Results are capped at
  `ui_actions.SEARCH_ROW_CAP` (200) rows with the true total shown
  separately (`showing N of TOTAL`). Each row's action button is chosen by
  its own status -- `culled` rows get Restore, everything else gets Remove.
  A bulk-action button (`Cull all N matching` / `Restore all N matching`,
  N = the actual total match count, MINOR-9 fix 2026-08-05) renders only on
  the `untested` and `culled` tabs -- there is no bulk entry point on
  `active`/`all`, by design (`_BULK_ACTION_FOR_TAB` binding:
  `untested<->bulk-remove`, `culled<->bulk-restore`).
- **`POST /bulk-confirm` -> `POST /bulk-decision`** -- a two-step bulk
  workflow. `/bulk-confirm` renders a preview count (the same
  `search_concepts(q, tab)` query, display-only) and a confirm form;
  `/bulk-decision` re-runs the match query INSIDE its own `BEGIN IMMEDIATE`
  transaction and applies the action to every match found at that moment --
  the preview count is never trusted as the set to act on, so a concept
  that changed status between the two requests is handled correctly rather
  than silently mismatched.

**Cull/restore semantics** (`ptcg/factory/ui_actions.py`, shared core
`_decide_one`, single-row via `/concept-decision`, bulk via
`apply_bulk_decision`):

- **Remove** (`action=remove`/`bulk-remove`) flips the concept's status to
  `culled` and inserts one `decisions` audit row
  (`concept_id, action, actor, timestamp, prior_status` -- `prior_status`
  captures the status the concept had BEFORE this decision). Only
  `untested`/`active` concepts can be removed this way.
- **Restore** (`action=restore`/`bulk-restore`) is **two-tier**: it looks up
  the most recent `remove`/`bulk-remove` decision row for the concept. If
  that row's `prior_status` was `'active'`, the concept goes straight back
  to `active` with its `coverage` row untouched. In every other case -- no
  audit row at all (a concept culled by the one-time 2026-08-03 pool
  reseed, not through this UI), a legacy `decisions` v1 row (`prior_status`
  NULL), or a concept that was culled while still `untested` -- the concept
  restores to `untested` AND its `coverage` row is zeroed
  (`games_played=0, distinct_opponents=0, rating=NULL`) in the SAME
  transaction (CONFIRMED-BUG fix, 2026-08-05). The zeroing is what actually
  does the re-gating, not the status flip alone:
  `census._CANDIDATES_QUERY` only re-schedules concepts with
  `games_played < floor`, and `census.promote_proven_singles` promotes on
  `games_played >= floor AND rating IS NOT NULL` with **no rating bar at
  all** -- so a stale coverage row left in place (e.g. a wr~0.03 concept
  already sitting at `games_played=60` from before it was culled)
  insta-promotes straight back to `active` on the very next census tick
  with zero new games played. Zeroing the coverage row forces a genuine
  re-screen from scratch through the census pipeline before the concept
  can ever be promoted again. A concept with no coverage row at all needs
  nothing further (rowcount 0 on the reset UPDATE is a safe no-op).
- **Finalist guard**: culling the anchor concept (`status='finalist'`) is
  refused with `FinalistProtectedError` (a `ValueError` subclass, caught
  BEFORE the plain `ValueError` branch in `ui_server.py` since it must be
  checked first) -- surfaced to the browser as `409 Conflict`. There is no
  UI path to cull the anchor at all (`render_pool_page` never emits a form
  for the pinned anchor row), so the 409 only fires if `/concept-decision`
  is hit directly.
- **Freeze-reopening caveat**: restore deliberately has no restriction on
  which culled concepts it can reach -- including ones culled by the
  2026-08-03 pool reseed, whose whole point was a one-way exploration
  freeze (`CLAUDE.md`'s "Tournament breeding anchor pressure" entry). This
  was Brad's explicit call: reopening the freeze via manual restore is
  acceptable because every restored-to-`untested` concept has its
  `coverage` row zeroed (above) and is re-screened from scratch by the
  census scheduler, exactly like any brand-new candidate, rather than
  re-entering the active pool unvetted on stale numbers.

All writes route through `deckdb._write` (`BEGIN IMMEDIATE`) -- the
read-decide-write shape is a rejected pattern in this repo (TOCTOU class,
commit `178043b`); bulk decisions compute their match list inside the same
transaction as the writes, never off a stale preview.

**Operator note -- a cull/restore click can take ~12-20 seconds. Wait; do
not re-click.** Measured during the 2026-08-07 go-live smoke against the
live server: every UI write contends for `tournament.db`'s write lock with
the always-on runner/scheduler workers, so the browser sits on a pending
POST until those workers release it. This is expected behavior, not a hang
-- the write does land, and the `decisions` audit row is written exactly
once. Two consequences to know before touching the UI:

- **Re-clicking is not free.** The first request is still queued behind the
  lock; a second click issues a second write that will also eventually
  land, so an impatient double-click on a bulk action can apply it twice.
- **A client that times out first will drop the server.** `ui_server` is a
  single-threaded `HTTPServer`, so a browser (or `curl`) that gives up and
  closes the connection mid-write can leave the handler writing to a closed
  socket; the request itself completes against the DB, but the page never
  renders. Re-load the page to see the real post-write state rather than
  assuming the action failed. (`ThreadingHTTPServer` is the accepted-debt
  follow-up; see the "Pool curation UI" entry in `CLAUDE.md`.)

Verify any action you are unsure about by re-loading `/search` on the
relevant tab, or by reading the `decisions` table directly -- never by
re-submitting the form.
