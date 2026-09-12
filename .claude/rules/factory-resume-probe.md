---
paths:
  - "**"
---

# On ANY session resume, probe autonomous factory activity FIRST — this project now acts between sessions

As of Slice 7A (2026-07-14) this repo runs an **autonomous agent factory**: a continuous-training daemon plus scheduled tasks that generate candidates, evaluate/gate them, and upload real Kaggle submissions **unattended between sessions** (first real automated submissions live: refs 54585057 / 54608104 / 54608114, validated across 3 unattended production nights).

**Consequence — the resume contract changed for every future session.** The working tree, the experiment ledger, the Kaggle submission history, and the value-net/candidate state can all have moved since the last interactive session **without any human or in-session agent action**. Stale-session assumptions ("nothing happened since I paused") are now *guaranteed wrong over any multi-day gap*, not incidentally wrong. The Slice-7A session was itself interrupted 3 days mid-gate and resumed only to discover 3 nights of autonomous activity that a naive resume would have overwritten or misread.

## 2026-08-18 addendum: post-deadline wind-down — runner/scheduler PERMANENTLY disabled; live roster is now TWO tasks

The 2026-08-16 final-submission deadline has passed, so no further Kaggle
submissions are possible and game-play compute is pure waste. On 2026-08-18
`ptcg-factory-runner` and `ptcg-factory-scheduler` were **deliberately and
permanently disabled** (elevated `Disable-ScheduledTask`, verified
`State=Disabled`; receipt transcript at
`experiments/factory/logs/winddown_disable.transcript.txt`). This is a
**wind-down, NOT an inertness hold** — no re-enable is owed, no lift is
pending, and it is NOT the 2026-08-10 unanswered-UAC pathology.

Consequences for every check in this file:

- **Live roster is now TWO tasks:** `ptcg-factory-continuous` (carries the
  ladder-snapshot logger through leaderboard convergence, ~Aug 31) and
  `ptcg-factory-ui`. Step 1's four-task roster below is historical for the
  runner/scheduler entries.
- **`State=Disabled` on runner/scheduler is the EXPECTED steady state.** Do
  not investigate it, do not surface a UAC prompt, do not treat it as an
  unlifted hold. The inverse is now the anomaly: a runner/scheduler found
  RE-ENABLED after 2026-08-18 is itself worth flagging.
- **`tournament.db` game-count growth is expected to be FLAT** — a flat
  count is no longer a starvation signal; it is the wind-down working.
- **Convergence is CONFIRMED REACHED as of 2026-09-01, so the full-shutdown
  decision (Brad-owned) for the remaining two tasks is now UNBLOCKED — surface
  it rather than re-deferring it.** Receipt: both counted refs read flat across
  their final three snapshots (`ladder_snapshots.jsonl:951` ref 55512672 @501.8
  and `:952` ref 55512669 @529.4, newest `utc_ts 2026-09-01T18:15:05Z`, 96
  observations per ref, last-3 spread 0.0 each). The snapshot logger was the
  only reason `ptcg-factory-continuous` was still wanted, and it has served its
  purpose. Until Brad decides, both tasks keep firing normally and that is not
  an anomaly — but a resume probe should no longer describe the decision as
  waiting on convergence. Also still open: the competition-license deletion
  of `pokemon-tcg-ai-battle/`, due at Strategy-competition end (after
  2026-09-13) — not yet due (corrected below; a prior version of this
  bullet wrongly called it "overdue by date").

## 2026-09-01 addendum: FULL SHUTDOWN executed — all four tasks Disabled; the resume probe's expected state is now "nothing fires"

Brad's full-shutdown decision (unblocked by the convergence confirmation
above) was executed the same day. `scripts/factory_shutdown_disable.ps1`
ran elevated for real (Brad clicked the UAC prompt): `OK: disabled
ptcg-factory-continuous`, `OK: disabled ptcg-factory-ui`,
`Stop-ScheduledTask -TaskName ptcg-factory-ui` issued, and a post-check
found zero listeners on port 8765. Transcript:
`experiments/factory/logs/winddown_disable_2026-09-01.transcript.txt`.
`ptcg-factory-runner`/`ptcg-factory-scheduler` were already `Disabled`
from the 2026-08-18 wind-down above — this action disabled the two
survivors, so **all four tasks are now Disabled** (kept registered, not
unregistered). `scripts/verify_factory_tasks.ps1` was re-keyed to expect
`Disabled` on all four and PASSes against that.

Consequences for any future resume probe:

- **Expected roster state is now "nothing fires."** A resume probe that
  finds all four tasks `Disabled`, a flat `watch.log` (no new firings
  after 2026-09-01), a flat `ladder_snapshots.jsonl` (no new rows), and a
  flat `tournament.db` game count is seeing the shutdown working exactly
  as designed — none of that is an anomaly to investigate.
- **The only anomaly is the opposite**: any task found `Ready`/`Running`,
  or any `factory_watch_once.py`/`factory_ui.py` process alive, after
  2026-09-01.
- **In-flight-firing transient, confirmed this run.** The disable command
  for `ptcg-factory-continuous` succeeded immediately, but `State` still
  read `Running` for roughly 80 seconds afterward while the firing already
  in progress (two `factory_watch_once.py` PIDs) finished — the script's
  own verdict printed `SHUTDOWN: FAIL` on that first check for exactly this
  reason. Don't kill the in-flight instance on a first `State != Disabled`
  read; poll again after the current firing has had time to exit (a firing
  is normally seconds, not minutes). This is a new variant of the
  wedged-instance/watchdog-respawn transient signatures already documented
  in `.claude/rules/factory-task-scheduler-liveness.md` — "still Running
  right after Disable" is benign here, distinct from a wedge, because the
  task never fires again once the in-flight instance exits.
- **`SUBMIT_HOLD` was left in place** (harmless/inert now that nothing can
  submit) — no cleanup owed.
- **No re-enable is owed and no lift is pending.** Same as the 2026-08-18
  wind-down: this is a deliberate, permanent shutdown, not an inertness
  hold with debt attached.

**Mandatory first step on ANY resume of this project** (before reasoning about state, before dispatching work, before assuming the tree is where you left it):

1. **Scheduled-task activity** — `Get-ScheduledTask`, `Get-ScheduledTaskInfo` for **all four** factory tasks as of the T21 tournament go-live (executed 2026-07-30): `ptcg-factory-continuous` (narrowed watch loop: submission-scheduler + episode harvester), `ptcg-factory-runner` (game-runner pool), `ptcg-factory-scheduler` (crash-safe tournament loop + R1 offspring faucet), and `ptcg-factory-ui` (localhost review/status server, port 8765). Check LastRunTime / LastTaskResult for each and the logs since your last session timestamp. (Historical: `ptcg-factory-matrix` / `ptcg-factory-trainer` were the live workers from the 2026-07-20 compute-saturation slice until T21 retired them on 2026-07-30 — dated datapoints below still cite them.)
2. **Kaggle submissions** — list recent submissions (`kaggle competitions submissions -c <comp>`); reconcile against the ledger to see what the factory shipped autonomously.
3. **Repo/ledger drift** — `git log --since=<last-session>` and read `experiments/EXPERIMENTS.md` + the persistent ledger for autonomously-appended rows; the factory follows a **no-autocommit convention** (its output may sit uncommitted in the tree — see commit `04f9fd0`), so also check `git status` for unstaged watch-loop output before doing anything destructive. As of the T21 tournament go-live (2026-07-30) the live high-frequency-write files are `experiments/factory/tournament.db` (persistent SQLite tournament DB, written by the runner/scheduler workers), the episode-harvester's `extracts.jsonl`/`harvest_stamp.json`, and `experiments/factory/logs/watch.log`. FROZEN (writers retired with the matrix/trainer workers; final state committed 2026-07-30): `experiments/factory/agent_pool.json`, `experiments/factory/deck_pool.json`, `experiments/factory/matrix.json`, `experiments/factory/matrix_blocks.jsonl` — new drift in any of these is anomalous, not routine.
4. **Worker restart-after-merge (evolutionary-agent-population, 2026-07-21; roster re-keyed by T21, 2026-07-30)** — if the resuming session is about to reason about long-lived worker output, or is about to merge/branch off a change touching worker code, confirm the affected worker tasks were RESTARTED (not just left `Running`) since the last merge that touched their code. As of T21 this applies to `ptcg-factory-runner`, `ptcg-factory-scheduler`, and `ptcg-factory-ui` (loop-forever processes under 15-minute `MultipleInstances=IgnoreNew` watchdog triggers; formerly `ptcg-factory-matrix`/`ptcg-factory-trainer`). See the long-lived-worker-code-staleness section below — a task showing `Running` since before a merge is not evidence it loaded that merge. Watchdog-respawn corollary (2026-07-30): a stopped-but-still-registered watchdog task relaunches its worker within one ~15-minute interval — `Stop-ScheduledTask` is not durable; only unregister or disable keeps a worker down.

As a quick supplement to (not a replacement for) the checks above, open the `ptcg-factory-ui` `/status` page (localhost:8765) for an at-a-glance view — `dashboard.html` is retired (mtime frozen 2026-07-24) and no longer regenerated. Known open issue (2026-07-30): the `/status` throughput line stamps +10h vs real UTC (suspected HST/UTC time-seam) — don't treat its timestamp as ground truth until fixed.

**The same UTC-vs-HST seam bites FILENAMES, not just displayed timestamps
(2026-08-13/14, census-screening-regime).** This host runs HST (UTC-10), so
any date derived from UTC during the HST evening lands on **tomorrow's
date**. A measurement receipt produced by a 2026-08-13 HST run was written
with a `2026-08-14` filename — cosmetic in itself, but these receipts are
cited as provenance for calibrated constants (`FLOOR_BAR`/`ANCHOR_BAR`), so
a future session reconciling "which measurement justified this bar?" against
plan.md's HST-stamped Progress Log will find the dates disagree by one day
and may conclude a receipt is missing or stale. **Rule:** when a script
writes a dated artifact that will be cited as provenance, derive the date
from local (HST) time to match the plan/Progress-Log convention, or embed
the full timestamp with an explicit offset — do not use a bare UTC date. When
reading such receipts, treat a ±1-day filename mismatch as the known seam,
not as evidence of a missing artifact.

## During-slice exposure: watch-loop-reachable code goes LIVE on the next firing, BEFORE its review completes — the working tree IS production for `ptcg-factory-continuous`

The long-lived-worker-code-staleness section (below) covers the *post-merge*
hazard: matrix/trainer workers don't hot-reload, so a merge is invisible to
them until restart. The **inverse hazard applies during a slice** and is the
sharper of the two for the watch loop. `ptcg-factory-continuous` spawns a
**fresh Python process every ~15 minutes** that imports whatever is in the
repo working tree *right now* — and factory development happens on a feature
branch checked out in that same main working tree (Python project, no
worktree per the Windows-pnpm precheck). So the moment an implementer writes
a new or changed watch-loop-reachable step (gate/submit/harvest/dashboard/
queue-refill) to disk, that code is **executed in production on the next
firing — before the per-task reviewer, let alone Pass 2, has seen it.** The
review is racing a 15-minute clock it doesn't know it's in.

**Datapoint (evolutionary-agent-population, 2026-07-21, T10):** a new
episode-harvester step reachable from the watch loop would have triggered a
742MB live Kaggle download on the next unattended firing, ~1h before its
review completed. The orchestrator caught it and manually seeded
`experiments/factory/harvest_stamp.json` to defer the download until the code
was reviewed — the right call, but an ad-hoc save, not a rule-driven one; the
exposure was unmanaged until the implementer happened to flag it.

**Rule — treat any watch-loop-reachable change as live-on-write during a
slice, and gate it deliberately:**
1. When a slice adds or changes code reachable by `factory_watch_once.py`
   (the gate/submit/harvest/dashboard/queue-refill path), decide UP FRONT
   how it stays inert until reviewed. Cheapest levers, in order: touch the
   `PAUSE` file for the duration of the risky implementation window; or
   pre-seed the step's stamp/ledger guard (as with `harvest_stamp.json`) so
   the first live firing no-ops; or land the new step behind a default-off
   flag the watch loop reads, flipped on only post-review.
2. Name that inertness mechanism in the plan for the task, so it's a
   reviewed decision — not an orchestrator reflex discovered mid-slice.
3. At the task's review, verify the guard actually holds against a real
   firing (check `watch.log` for the no-op line at the expected firing
   time), not just that the code "would" no-op. A watch-loop step is in
   production the instant it's on disk; "not merged yet" is NOT "not live."

**Worked example — the rule executed as designed (counted-pair-protection,
2026-08-05).** First slice where this hazard was managed by PLAN DESIGN
rather than orchestrator reflex, and it is the shape to copy. The spec named
`SUBMIT_HOLD` as the inertness mechanism up front (clause 1); it was executed
as plan **Task 1**, before any subscheduler-reachable line was written, with
a timestamp-ordered receipt proving hold-set preceded first-write (clause 2);
the hold was verified against real firings — 100% of firings 10:04→11:31
no-opped in `watch.log` — rather than reasoned about (clause 3); and it was
lifted only at the post-merge go-live rung, with a same-day
"first unheld firing" receipt (`[2026-08-05T11:45] submit: no-op`) closing
the loop. Cost: one plan task. Compare against the 2026-07-21 datapoint
above, where the same exposure was caught only because an implementer
happened to flag it. Treat the hold-as-Task-1 + timestamp-ordered receipt +
verified-lift triple as the settled template for any future
watch-loop-reachable slice.

**Counter-datapoint — an inertness claim can still be wrong even when no
hold/gate mechanism was needed, because "touched files" undercounts
"reachable code" (ui-remove-any-deck, 2026-08-05).** The plan's Go-live
section asserted all new code stayed inert during the slice pending the
post-merge `ptcg-factory-ui` restart. That was true for the UI server's own
routes (nothing calls them but a browser hitting the not-yet-restarted
task), but FALSE for `deckdb.py`: the slice's `decisions` table v2 migration
(`migrate_decisions`) runs automatically inside `deckdb.init_db()`, which
`scripts/factory_watch_once.py` calls on every ~15-minute firing —
completely independent of whether `ptcg-factory-ui` itself had restarted.
The migration executed against production `tournament.db` mid-slice.
Harmless in this instance (additive-only, idempotent, the table was
effectively empty of the new columns), but the inertness claim itself was
false and had to be corrected in-slice (`476614a`) rather than caught by
review.

**Generalized rule:** an inertness/hold analysis (per clauses 1-3 above)
must walk the **import graph of the watch loop's entrypoint**
(`grep -n "^import\|^from" scripts/factory_watch_once.py` and follow one
level into anything it calls, e.g. `deckdb.init_db`, `subscheduler`,
`episodes`), not just the list of files the task touched. A file can be
"new work for this slice" and simultaneously "already imported and called
by code that already runs every firing" — those are independent questions,
and only the import-graph walk answers the second one. Do this check at
plan-write time for any slice that touches a module the watch loop already
imports (`deckdb`, `subscheduler`, `episodes`, `census`), even when the
slice's own routes/entrypoints are genuinely new and unreachable.

Only after this probe is the session state known. Skipping it risks clobbering autonomous output, double-submitting, or building on a stale picture.

## Disk-drain triage order: check pagefile allocation BEFORE file-level sweeps when the drain is step-wise, not a smooth slope

The 2026-08-12 disk-full incident (see `check_auth`-conflates-any-CLI-failure
addendum above) was traced to episodic app bursts. A SECOND, structurally
different disk-drain incident on 2026-08-14 showed a different root cause
with a different diagnostic signature, worth checking for explicitly before
repeating a file-level sweep (WAL files, temp dirs, generated output) that
won't find it.

**Datapoint (2026-08-14, freeze-pair-probe-and-finalize).** Free space fell
6.7GB → 3.13GB → 1.1GB across roughly an hour, in visible STEPS rather than
a smooth drain — the signature that flagged it as commit-pressure-driven
rather than a file being written. A targeted probe found `C:\pagefile.sys`
had expanded from ~30GB to 41.7GB allocated, while actual pagefile USAGE was
only 4.2GB (peak 4.5GB) — i.e., Windows had grown the system-managed
pagefile far ahead of what was actually being used, consuming real disk
space to do it. WAL files were ruled out (56MB, far too small to explain the
drain) and no file-level consumer was ever found — the pagefile allocation
WAS the consumer. Closing memory-heavy applications (no reboot) halted
further growth and recovered ~2.2GB of the allocated-but-unused pagefile
space — confirming pagefile growth, once triggered, can partially reverse
under reduced commit pressure without a reboot; it does not require a full
reboot to see ANY recovery, though a full return to baseline size still
does. Root cause was the same "system-managed pagefile" mechanism flagged as
dead capacity in the 2026-08-13/14 census-screening-regime session (a
separate 30GB pagefile finding from a prior session) — this is now the
SECOND session where pagefile behavior, not a file-level consumer, was the
actual driver of a disk emergency.

**Rule — triage order for any future disk-drain incident on this host:**
1. Check the DRAIN SHAPE first (`Get-PSDrive C` sampled a few times a
   minute, or equivalent): a smooth slope suggests a file being actively
   written (log, WAL, cache); a step-wise/bursty pattern suggests either an
   application burst (per the 2026-08-12 datapoint) or pagefile
   reallocation under commit pressure (this datapoint).
2. For a step-wise pattern, check `C:\pagefile.sys` size
   (`(Get-Item C:\pagefile.sys -Force).Length`) and compare against actual
   commit usage (Task Manager Performance tab or
   `Get-Counter '\Memory\% Committed Bytes In Use'`) BEFORE doing a
   file-level sweep of WAL/temp/cache directories — a mismatch between
   pagefile ALLOCATION and pagefile USAGE is itself the finding, and no
   amount of file-level searching will locate a "missing" consumer that is
   actually the pagefile.
3. Do not assume recovery requires a reboot — try closing memory-heavy
   applications first and re-measure; partial recovery without a reboot is
   possible and was observed directly this session. A capped pagefile size
   (post-Aug-16 carry-forward, tracked across the 2026-08-13/14 and
   2026-08-14 sessions) plus a scheduled reboot remain the durable fix; both
   are deferred, Brad-owned, post-deadline tasks — do not attempt either
   mid-slice on a live production host.

## Holding workers for a migration can be the RISKIER choice, not the safer one, when the working tree IS production (2026-08-13, census-screening-regime)

The obvious-looking safe pattern for a live schema migration is "stop the
workers, migrate, restart the workers" — avoid any window where a worker
might touch a half-migrated table. On this repo that pattern has a hidden
cost the during-slice-exposure section above already implies but never
named explicitly: **the workers don't hot-reload, so restarting them
during a slice forces them onto whatever code is currently on disk in the
main working tree — which, mid-slice, is unreviewed branch code, not just
the migration's own change.** Stopping-then-restarting doesn't just apply
the migration; it also force-activates every other in-flight, unreviewed
edit the slice has made so far.

**Datapoint.** The T6 gate for census-screening-regime needed an early
additive migration (`scripts/migrate_composition_columns.py`) run against
the live `tournament.db` before merge, to close a scheduler-respawn crash
window. The instinctive safe path was to hold (stop) the runner/scheduler
workers for the migration's duration. Instead, the migration was measured
first (~6s hold, well under the 30s `busy_timeout`) and run **without**
stopping the workers at all — a deliberate, explicitly-logged deviation.
This was the better call specifically because a stop/restart would have
force-activated the rest of the mid-slice branch's unreviewed code the
moment the workers came back up, not just the reviewed migration.

**Rule:** before defaulting to "hold workers during a migration," check
two things: (1) is the migration's own lock-hold time short relative to
`busy_timeout` (per `.claude/rules/single-actor-worker-tests.md`'s
access-path-analysis discipline) — if so, an unheld migration may be
strictly safer than a held one; (2) if workers WOULD need to be held,
would restarting them mid-slice pull in unreviewed branch code beyond the
migration itself? If yes, either scope the migration to run from a clean
checkout/detached state (not the live working tree), or defer the hold
until the branch is fully reviewed and ready to go live, rather than
treating "stop the workers" as automatically the conservative choice. Log
whichever path is taken as an explicit deviation with the reasoning, per
the diagnose-before-dispatch discipline — don't let either the hold or the
no-hold path be a silent default.

## Continuous firing means re-probe before ANY git-state-dependent action in long sessions, not just ones crossing a nightly boundary

The start-of-session probe above only proves the tree's state *at that
moment*. Under the continuous-factory design the watch loop
(`ptcg-factory-continuous`) fires roughly every 15 minutes (plus once at
machine startup) rather than once a night, so ANY session left open for
more than a firing interval or two — not only a multi-hour or overnight
session crossing one specific clock boundary — can have a fresh autonomous
firing land: new ledger rows, a new digest or `watch.log` line, uncommitted
generated-deck output, *while the session is still running*. A start-only
probe now goes stale far sooner than it did under the old once-nightly
design. **Datapoint (2026-07-15/16 factory-polish slice, under the prior
once-nightly design):** one session spanned two midnight boundaries and two
autonomous cycles (7/15 and 7/16 02:00); both were probed and committed
ad-hoc without disruption, but nothing in this rule mandated it — the same
gap under the current 15-minute cadence would be far more likely to bite,
since uncommitted drift can now accumulate many times an hour rather than
once a night. **Rule, tightened for continuous firing:** treat "the loop
has been running for a while since I last checked" — not "the session
crossed ~02:00" — as the trigger. Any long session should re-run the probe
(at minimum `git status` + the digest/ledger drift check, plus a tail of
`experiments/factory/logs/watch.log`) **before the next git-state-dependent
action** — branching, merging, or committing — so a mid-session firing's
uncommitted output is reconciled, not clobbered or double-committed. The
old "~02:00 boundary" framing under-counts risk now: at 15-minute cadence,
almost any session that does git-state-dependent work after a stretch of
other work should re-probe first, regardless of what time it is.

## Weekly-review sessions specifically: open the checklist file, don't reconstruct it from memory

`docs/weekly-review-checklist.md` already exists and already lists the exact
scheduled-task liveness check (`Get-ScheduledTaskInfo ptcg-factory-continuous`,
its step 9 as of the continuous-factory slice; it was step 8 under the
`ptcg-factory-nightly` task name at the time of the datapoint below) plus
the pre-final convergence-freeze reminder (step 10, was step 9). **Datapoint
(2026-07-14, weekly factory review):** the session ran a full weekly review —
re-prioritizing the queue, authoring candidates, journaling — without ever
opening `docs/weekly-review-checklist.md`, so the liveness check was
skipped even though it was already written down. The gap was not missing
knowledge; it was an unread runbook. Any session that identifies itself as
(or is asked to run) the weekly factory review MUST `Read
docs/weekly-review-checklist.md` as an explicit first step and work the
numbered list in order, rather than reconstructing "what a weekly review
should cover" from context/memory. If the checklist is stale or incomplete
relative to what actually shipped (e.g., it predates a mechanism like the
champion-pairing guard), update the checklist file itself in the same
session rather than silently working around the gap.

## Long-lived-worker-code-staleness: a merge does not update a process that is already running

`ptcg-factory-matrix` and `ptcg-factory-trainer` are **long-lived
processes** — once a Scheduled Task starts them, they keep running
(playing tournament blocks, training nets) across an arbitrarily long
span, unlike `ptcg-factory-continuous`, which spawns a **fresh process
every ~15-minute firing**. This asymmetry means a merge to master that
changes matrix-worker or trainer-worker code is **invisible to the running
processes** until they are explicitly restarted — Python does not
hot-reload. A Scheduled Task reporting `Running` proves the process is
alive, not that it is running current code. And critically, the watch
loop showing new post-merge behavior is NOT transferable evidence that the
long-lived workers picked up the same merge — the two processes have
completely different reload semantics.

**Datapoint (evolutionary-agent-population go-live, 2026-07-21):** the
first 30-minute post-merge poll (Rung 2: "verify evolution live") timed
out because `ptcg-factory-matrix` and `ptcg-factory-trainer` (both started
2026-07-20 19:30, pre-merge) were still playing candidate blocks stamped
with the pre-merge commit `1a245a8` in `matrix_blocks.jsonl` provenance —
merged code `45ee1d5` had landed ~30 minutes earlier and the workers had
simply never restarted. Root-caused by comparing block provenance stamps
against `git log` HEAD. Fix: `Stop-ScheduledTask` + `Start-ScheduledTask`
on both tasks (non-elevated — no UAC needed for stop/start, unlike
registration); the first real post-restart cell block landed 150 seconds
later, confirming the merge was finally live.

**Rule:** any merge that changes code under `src/ptcg/factory/` reachable
by the matrix or trainer worker (evolution/breeding/bt/genomes/training
code, not just the watch-loop-only gate/submit/harvest/dashboard path)
must include, as an explicit post-merge go-live step — not an assumption —
`Stop-ScheduledTask -TaskName ptcg-factory-matrix` /
`Start-ScheduledTask -TaskName ptcg-factory-matrix` and the same pair for
`ptcg-factory-trainer`. Verify liveness afterward by checking that the
NEXT block/training-run provenance stamp in `matrix_blocks.jsonl` /
`experiments/factory/logs/` carries a commit at or after the merge, not
just that the task state is `Running`.

**2026-07-30 update (T21 executed):** `ptcg-factory-matrix` /
`ptcg-factory-trainer` are retired; this rule now applies verbatim to
`ptcg-factory-runner`, `ptcg-factory-scheduler`, and `ptcg-factory-ui` —
all three are loop-forever processes kept alive by 15-minute
`MultipleInstances=IgnoreNew` watchdog triggers, so a merge touching their
code still requires an explicit `Stop-ScheduledTask`/`Start-ScheduledTask`
restart to load. **New corollary — watchdog-respawn (2026-07-30 go-live
incident):** stopping a watchdog-repeating task does NOT keep it stopped.
During the T21 cutover, the legacy matrix/trainer watchdogs refired at
~08:00 — in the window between the Rung-A `Stop-ScheduledTask` and the
registration script's retire step — relaunching 6 stale (pre-cutover
commit `9004d1b`) worker processes that had to be killed by PID
(`taskkill /T /F`; verified zero remained). `Stop-ScheduledTask` only
holds until the next watchdog interval; to rely on a worker staying down,
unregister or disable the task first.

## 2026-07-29 addendum: generational-champion-tournament narrows the roster again — T21 task-name changes are PENDING, and a new wedged-instance starvation mode

- **T20 (code shipped 2026-07-24, merged 61a3148):** the narrowed
  `ptcg-factory-continuous` watch loop now does ONLY submission-scheduler
  ticks (`subscheduler.py`) plus the episode harvester; eval/gate/train/
  dashboard-render are gone from it entirely. `dashboard.html` itself is
  retired per Brad's PD-B override — the localhost `ptcg-factory-ui` server
  (T18/T19) is now the sole review/status surface, not a fallback pair.
- **T21 (post-merge go-live) EXECUTED 2026-07-30 (~07:56 machine time).**
  `scripts/register_tournament_tasks.ps1` ran elevated for real (its
  first-ever real run; exit 0; transcript at
  `experiments/factory/logs/register_tournament_tasks.transcript.txt`):
  REGISTERED `ptcg-factory-runner` (game-runner pool),
  `ptcg-factory-scheduler` (the crash-safe tournament loop —
  `loop_scheduler.py`/`factory_tournament_scheduler.py`, `--pipeline-target 4`
  = R1 faucet), and `ptcg-factory-ui` (port 8765); RETIRED
  `ptcg-factory-matrix` and `ptcg-factory-trainer`; LEFT
  `ptcg-factory-continuous` registered in its narrowed T20 role.
  Independently verified via `Get-ScheduledTask`. The resume probe's live
  roster is now the FOUR tasks named in step 1 at the top of this file.
  See `experiments/EXPERIMENTS.md` (2026-07-30 entry) for the full go-live
  record, including the watchdog-respawn incident and the census-seed
  numbers.
- Because terminal markers on the narrowed loop are now guaranteed on
  EVERY exit path (see the terminal-marker-liveness section of
  `.claude/rules/factory-task-scheduler-liveness.md`), the old
  "missing markers on >50% of firings = crash cascade" heuristic is now a
  strict baseline for `ptcg-factory-continuous`: expect a paired terminal
  marker on every single firing, full stop — not just "most" of them.
- **New failure mode caught at Pass 2 (2026-07-24, flip condition cleared
  2026-07-29): a wedged instance, not a crash, silently starves ALL future
  firings.** `ptcg-factory-continuous` is a fresh-process-per-firing task
  with `MultipleInstances=IgnoreNew`. A single instance that hangs
  (instead of crashing or completing) never exits, so every later
  scheduled firing is silently skipped while the wedged instance holds the
  slot — `LastRunTime` simply stops advancing and `LastTaskResult` reads
  `0x80070420` ("the service cannot be started because it is already
  running") rather than any normal success/failure code. There is no new
  terminal marker at all during the starved window — not even an error one
  — because no new process ever runs. This is invisible to `LastRunTime`
  staleness checks alone and invisible to output-based checks (there's
  simply no new output to inspect). Distinguish it from ordinary staleness
  by checking BOTH: (a) `Get-WinEvent` for Task Scheduler Event ID 322 (a
  fresh instance was skipped because the task was already running) —
  repeated 322 events in the recent window is the fingerprint; (b) the age
  of `experiments/factory/logs/watch.lock` — a lock held far longer than
  one firing interval (here, far longer than ~15 minutes) with no matching
  terminal marker means the holder is wedged, not merely slow. Recovery:
  kill the wedged process tree, then confirm the next scheduled firing
  produces a fresh, paired terminal marker before trusting the loop again.
  See `.claude/rules/factory-task-scheduler-liveness.md`'s matching
  2026-07-29 addendum, and `docs/weekly-review-checklist.md` step 13 for
  the routine check this incident promoted into the standing checklist.

## 2026-08-10 addendum: an unanswered UAC prompt froze the factory for ~2 days — elevation steps need explicit human-facing handoff, not fire-and-forget

A go-live rung that re-enables the `ptcg-factory-runner`/
`ptcg-factory-scheduler` Scheduled Tasks (disabled during an inertness
hold, per the pattern used throughout `factory-db-lock-contention`,
2026-08-08/10) requires an elevated (UAC) action on this machine, same as
`register_tournament_tasks.ps1`'s registration step documented above. That
prior datapoint (2026-07-30) was about the elevated CONSOLE losing its
output on close (`Start-Transcript` mitigation). This is a DIFFERENT
failure in the same elevation machinery: the UAC prompt itself was
launched and then never surfaced to Brad as a pending decision — nobody
said, in effect, "there is a dialog waiting for your click." It sat
unanswered from 2026-08-08 until 2026-08-10 07:51, and the factory ran at
**zero throughput for the entire gap** (~2 days), on a fix that had
already been merged and was ready to go live. Once surfaced, resolution
was a single click plus a `Get-ScheduledTask` liveness check — the
incident cost was entirely in the silent handoff, not in any technical
difficulty.

**Rule:** any go-live rung that triggers a UAC/elevation prompt must be
treated as a BLOCKING HUMAN-INPUT REQUEST at the moment it is launched —
surface it explicitly (an unambiguous loud message, or an AskUserQuestion
if the orchestration flow allows one) rather than proceeding as if the
step is "in progress." Do not mark the go-live rung complete, and do not
move on to the next phase (survival verification, autonomous outcome
review, wrap-up), until the elevation's actual completion is independently
verified (`Get-ScheduledTask` showing `Enabled`/`Ready`, not just "the
command was issued"). This generalizes global CLAUDE.md's existing
privileged-action pre-flight rule (Workflow section) from "capture the
elevated console's output" to "confirm a human actually saw and answered
the prompt" — the two are different failure modes on the same elevation
boundary, and this session is the datapoint for the second one. See the
session memory `factory-db-lock-contention-2026-08-10.md` for the full
incident record.

## 2026-08-12 addendum: `check_auth()` reports "auth-dead" for ANY CLI failure, not just a dead token — infrastructure problems masquerade as auth problems

`kaggle_client.check_auth()` treats every failure mode of the `kaggle`/
`uvx` CLI launch identically: an expired OAuth token, a missing
credentials file, and a fully unrelated infrastructure problem (disk full,
no space to write a temp file, network down) all surface as the same
"auth-dead" status on the factory dashboard/status page.

**Datapoint (2026-08-12).** `C:` hit 0 bytes free overnight. The `uvx`/
`kaggle` CLI path failed as a direct consequence, and the factory reported
"auth-dead" — which is factually what happened (the CLI call failed) but
misdirects triage toward re-authenticating a token that was never the
problem. The actual fix was `uv cache clean` + a temp-dir purge (14.7GB
reclaimed); the Kaggle token was fine the entire time.

**Rule:** when a resume probe or weekly review encounters an "auth-dead"
status, do NOT jump straight to re-authentication. Check infrastructure
first — disk space (`Get-PSDrive C` or equivalent), network reachability,
temp-dir writability — and only pursue a token/credentials fix if the
infrastructure checks come back clean. This is a standing caveat on `check_auth()`'s
reported status until the function itself is fixed to distinguish failure
causes (not done as of 2026-08-12; a real gap, out of scope for any single
session so far). See memory `freeze-curation-and-unpayable-pool-2026-08-12.md`
for the full incident.

## 2026-08-14 addendum: "which row is newest?" probes on append-only files must be dispatched as an exact command, not as a question

Several probe steps in this file ask a cheap sub-agent to answer a
newest-row question against an append-only artifact — the last firing in
`experiments/factory/logs/watch.log`, the newest entry in
`extracts.jsonl`/`matrix_blocks.jsonl`, the timestamp in a stamp file. That
question is deceptively easy to get wrong: a model handed a multi-line file
and asked "what is the latest entry?" can anchor on the most *salient* line
(a manual run, a longer row, the first one it reads) rather than the last
one, and it will report that with full confidence.

**Datapoint (2026-08-14, freeze-pair-probe-and-finalize).** A haiku-tier
verification of a jsonl tail reported a manual run's timestamp as the newest
entry; the actual newest row was a later automated one. Caught on
re-verification, cost one round-trip — but the wrong answer was
indistinguishable in shape from a right one.

**Rule:** when dispatching a newest-row/latest-timestamp probe (especially
to a cheap-tier agent), give the exact command and ask for its raw output,
not the interpretation:

```powershell
Get-Content "experiments/factory/logs/watch.log" -Tail 5
Get-Content "experiments/factory/extracts.jsonl" -Tail 3
```

Then read the output yourself. "Paste the last N lines" has no judgment
surface to get wrong; "tell me the newest entry" does. This is the
file-reading instance of the general rule that a subagent's *evidence* is
worth more than its *conclusion* — cheapest where the evidence is three
lines long.
