# Weekly Factory Review Checklist (spec S1: the weekly review is the brain)

Run this as a Claude session in the repo, roughly weekly.

1. Read state: latest digests (experiments/factory/digests/), experiments/LADDER.md
   score trajectories, experiments/factory/candidates.json queue.
2. Reconcile: does the local-WR ordering match the ladder-score ordering? Divergence
   is signal - the ladder is the evaluator of record; note it in writeup-notes.md.
3. Re-prioritize: adjust queue priorities from ladder evidence (hand-edit the ledger
   or bump via a short script; record the reasoning in writeup-notes.md). Also verify
   the `is_incumbent` gate pin (candidates.json) still points at the ladder-best
   converged line and re-pin it if it has drifted - the pin is manual and never
   auto-reassigns (see the retired-flag ignore-fallback in `incumbent()`,
   src/ptcg/factory/gate.py).
4. Author candidates: deck tweaks (new version of an existing name) and/or agent
   config variants; add via ptcg.factory.candidates + save_ledger; novel axes get
   novel_axis=true.
5. Curate the deck matrix: review `SEEDS` (src/ptcg/factory/deck_matrix.py) - retire
   seeds that are clearly dead ends (drop the tuple, note why in writeup-notes.md) and
   add new archetype seeds worth exploring; review how the auto-generated mutation
   variants under src/ptcg/decks/candidates/generated/ (surfaced continuously by
   `refill_queue()` whenever the queue runs dry, up to 10 new cells per firing) have
   actually performed in the ledger - a seed whose variants all underperform is a
   stronger retirement signal than the seed's own single result.
6. Retire dead ends: mark clearly-dominated candidates retired with a notes reason.
7. Daemon check: experiments/factory/daemon_state.json last_error null? Registered
   candidates flowing into the queue? Recall evaluation and training no longer run
   on the watch loop at all - as of the T21 tournament go-live (2026-07-30) the
   game-runner pool (ptcg-factory-runner) and the crash-safe tournament loop
   (ptcg-factory-scheduler, which also owns offspring breeding under the R1
   faucet) own all game-play and training, writing to
   experiments/factory/tournament.db (the retired matrix/trainer workers'
   ledgers - matrix.json, matrix_blocks.jsonl, agent_pool.json, deck_pool.json -
   are frozen) - a quiet daemon_state.json between visits is expected, not a
   sign anything stalled; check watch.log
   (experiments/factory/logs/watch.log) for watch-loop firing-by-firing activity,
   and step 9 below for worker liveness.
8. Journal: one strategic-prose entry in docs/writeup-notes.md (what the ladder
   taught us this week, what we change in response).
9. Housekeeping: counter file sane vs kaggle submissions list; PAUSE file absent
   (one PAUSE file now stops the watch loop AND both continuous workers - see
   docs/factory-operations.md "Pausing the watch loop AND both continuous
   workers"); continuous watch task last-run result and recency
   (`Get-ScheduledTaskInfo ptcg-factory-continuous` - `LastRunTime` should be within
   ~30 minutes of the review start, not 24-36h, since the loop fires every 15
   minutes by default; see .claude/rules/factory-task-scheduler-liveness.md).
   Also check the tournament workers' scheduled tasks (T21 roster, live since
   2026-07-30): `Get-ScheduledTaskInfo ptcg-factory-runner`,
   `Get-ScheduledTaskInfo ptcg-factory-scheduler`, and
   `Get-ScheduledTaskInfo ptcg-factory-ui` - all three are loop-forever
   processes under a 15-minute AtStartup+repetition *watchdog*
   (MultipleInstances=IgnoreNew), not a work-cadence knob, so `LastRunTime`
   itself isn't the primary signal (a long-alive worker just holds the slot and
   the watchdog firing finds it "busy," which still counts as a recent
   `LastRunTime`). Primary liveness signals instead: games-played growth in
   `experiments/factory/tournament.db` for runner/scheduler, and the
   `ptcg-factory-ui` `/status` page (localhost:8765) responding - see item 11
   below for the at-a-glance version of this check. Watchdog-respawn corollary
   (2026-07-30): a stopped-but-still-registered watchdog task relaunches its
   worker within ~15 minutes - to keep a worker down, unregister or disable
   the task, don't just stop it.
10. Pre-final convergence freeze: each champion re-upload restarts its Gaussian
    rating, so stop the champion-pairing churn far enough before the 2026-08-16
    final-submission deadline for the last counted pair to rating-converge on the
    ladder. Plan (and record) the freeze date at the early-August weekly review.
11. Tournament-health glance: open the ptcg-factory-ui `/status` page
    (localhost:8765 - dashboard.html is retired, mtime frozen 2026-07-24, no
    longer regenerated) and confirm the pipeline is advancing. Then check
    experiments/factory/tournament.db - total recorded games should be growing
    week-over-week (~337 games/hour observed at go-live); a flat or shrinking
    total (outside an intentional PAUSE window) is a signal the runner/scheduler
    workers stopped ticking even if their scheduled tasks look fine on paper.
    Known open issue (2026-07-30): the /status throughput line stamps +10h vs
    real UTC (suspected HST/UTC time-seam) - don't treat its timestamps as
    ground truth until fixed.
12. Episode review: open the dashboard loss panel; check timeout-loss % (>5% =
    investigate budget genes), our WR vs each top meta cluster, and whether
    current meta-anchors still match the observed top-3.
13. Wedged-instance staleness check (closes generational-champion-tournament
    T21 carry-forward item vii, 2026-07-29): a fresh-process-per-firing task
    (`ptcg-factory-continuous`, both pre- and post-T21) can get silently
    wedged instead of crashing. Under its `MultipleInstances=IgnoreNew`
    setting, one stuck instance causes every later scheduled firing to be
    skipped rather than run — `LastRunTime` stalls and NO new terminal
    marker appears at all (not even an error one), which looks like mere
    staleness rather than total starvation. Check for it explicitly:
    `Get-WinEvent -LogName Microsoft-Windows-TaskScheduler/Operational |
    Where-Object { $_.Id -eq 322 -and $_.TimeCreated -gt (Get-Date).AddHours(-6) }`
    (repeated Event ID 322 = a fresh instance was skipped because the prior
    one is still "running") and the age of
    `experiments/factory/logs/watch.lock` (older than ~15-30 minutes with no
    matching terminal marker = wedged, not slow). See
    `.claude/rules/factory-task-scheduler-liveness.md`'s 2026-07-29
    addendum for the full incident (a legacy instance wedged at 11:15 on
    2026-07-24 silently starved all firings until found and cleared) and
    recovery steps. As of the T21 go-live (2026-07-30) this check targets
    `ptcg-factory-continuous` (still fresh-process-per-firing) via
    `watch.lock`. The `ptcg-factory-runner`/`ptcg-factory-scheduler`/
    `ptcg-factory-ui` trio also runs under `MultipleInstances=IgnoreNew`
    15-minute watchdogs, but for those loop-forever workers repeated
    Event-322 entries are BENIGN (the watchdog finding a healthy long-lived
    instance holding the slot) - the Event-322 starvation fingerprint
    applies only to the fresh-process watch loop.
14. Strength-gate check (submission strength gate, spec 2026-08-01): check
    `anchor_checks` verdicts for recent champions (SELECT against
    `experiments/factory/tournament.db`, or the `anchor-gate:`/
    `submit-scheduler: GATE=anchor-*` lines in `watch.log`) - a string of
    `fail` verdicts is EXPECTED under the 0.55 `ANCHOR_BAR` (Slices 4-7B
    repeatedly measured search-based agents at 0.42-0.54 against this same
    anchor deck/agent) and is the gate doing its job, not a sign anything is
    broken. Investigate only if a version's verdict is stuck `pending` far
    beyond ~1h of pool throughput without resolving (see
    `docs/factory-operations.md` "Submission strength gate (anchor check)"
    for the full mechanics and log-line reference).
15. Task-state check (run this FIRST, before anything else in this list):
    `powershell -ExecutionPolicy Bypass -File scripts\verify_factory_tasks.ps1`
    - Read-only; prints State/LastRunTime/LastTaskResult/NextRunTime for all
      four factory tasks plus a loud `VERIFY: PASS` / `VERIFY: FAIL` verdict.
    - A `State = Disabled` task is the signature of an inertness hold that was
      never lifted: no crash, no Event 322, no error line, `LastTaskResult`
      still 0 - it just silently produces nothing. The
      factory-db-lock-contention slice (2026-08-08/10) left the runner and
      scheduler disabled for ~2 days this way, on an already-merged fix, and
      nothing between sessions surfaced it. See the 2026-08-10 addendum in
      `.claude/rules/factory-task-scheduler-liveness.md`.
    - PASS only means "enabled and scheduled" - still do step 11's throughput
      cross-check (games-played growth in tournament.db) before concluding the
      pipeline is actually healthy.
    - **2026-09-01: the factory is fully shut down.** `verify_factory_tasks.ps1`
      now expects `State=Disabled` on all four tasks and PASSes against that
      by design - a Disabled result here is no longer the inertness-hold
      pathology described above. See the 2026-09-01 addenda in
      `.claude/rules/factory-resume-probe.md` and
      `.claude/rules/factory-task-scheduler-liveness.md`.
16. Bar recalibration against the NEW anchor (one-time, opened 2026-08-11 by
    the min-basics-pool-rule slice — HIGH priority, close it and delete this
    step once done):
    - `FLOOR_BAR` (0.40) and `ANCHOR_BAR` (0.55) were both calibrated against
      the RETIRED anchor. The factory anchor is now
      `src/ptcg/decks/candidates/anchor-min8.csv` (concept
      `anchor-lucario-min8`, see `src/ptcg/factory/anchor.py`), which is a
      materially stronger opponent — so both bars now measure something
      different from what they were tuned on.
    - Post-migration measurements: the baseline lineage scores **0.545**
      against the new anchor (sitting ON the 0.55 `ANCHOR_BAR`); typical pool
      decks read **0.01-0.05**. Both bars therefore err RESTRICTIVE, which is
      the safe direction — the pair-gate (`src/ptcg/factory/pairgate.py`)
      remains the hard upload-protection layer meanwhile — but a bar that
      nothing can clear stops being a filter and starts being a freeze.
    - Recalibrate against the new regime, not against historical
      pre-migration win rates (see `empirical-check-against-wrong-regime` in
      `.claude/rules/single-actor-worker-tests.md` — the identical mistake was
      a Pass-2 Critical on 2026-08-04). In-process heuristic games run at
      ~0.022s/game, so a 1000+ game calibration is a ~30-second local script,
      not a production-rate wait.
    - `scripts/measure_floor_distribution.py` needs re-pointing first: its
      reseed templates are themselves `<8` basics and now fail loud against
      the `MIN_BASIC_CARDS = 8` check in `src/ptcg/decks/validate.py`.
