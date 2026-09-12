# Design Spec: Compute-Saturation — Continuous Matrix Tournament + Unlocked Per-Deck Training

**Date:** 2026-07-20
**Status:** APPROVED
**Feature branch target:** `feature/compute-saturation`

## 1. Goal & Motivation

Saturate Brad's idle CPU+GPU 24/7 to (a) find better decks via a continuous
round-robin tournament and (b) train per-deck value nets for top decks.

The Kaggle 5/day submission cap and the ladder-as-evaluator-of-record
principle are **unchanged**. Extra compute buys better local **RANKING**
before ladder slots are spent — it does not buy more ladder slots.

**Motivating defect:** ladder-vs-local inversion. The pooled 2-baseline
`local_wr` is a weak predictor of ladder score (documented across the
2026-07-14 and 2026-07-20 weekly reviews: starmie-searchnet and density20
outscored candidates with higher local_wr). A dense all-play-all matrix
with a proper strength fit is a strictly richer local signal than 150
games against two fixed baselines.

**Naming note (disambiguation):** "matrix" in this spec means the
**matrix tournament** (pairwise game matrix + Bradley-Terry fit, stored in
`experiments/factory/matrix.json`). It is unrelated to the existing
`src/ptcg/factory/deck_matrix.py` (deck-matrix **queue refill** — seed
decks x mutation rules). The new module MUST NOT be named `matrix.py`;
use `src/ptcg/factory/tournament.py` (worker loop + pair scheduler) and
`src/ptcg/factory/bt.py` (Bradley-Terry solver) to avoid collision.

## 2. Architecture — three processes, one ledger pair

Three OS processes cooperate through two locked, atomically-written JSON
ledgers: the existing `experiments/factory/candidates.json` and the new
`experiments/factory/matrix.json`.

### 2.1 Matrix worker (NEW)

- Continuous Windows Scheduled Task. CPU-bound. BelowNormal process
  priority (set on itself at startup, same mechanism as the watch loop's
  cycle throttling).
- **The ONLY process that plays evaluation games.** Continuous
  round-robin among active candidates.
- Loop body:
  1. Check PAUSE kill-switch file → if present, exit this iteration
     without playing.
  2. Refresh the active pool from `candidates.json` (see §5 pool policy).
  3. Pick the pair with the fewest recorded games (uniform coverage
     first; see §4 pair scheduling).
  4. Play a block of **10 games** between the pair (reusing the existing
     factory eval-runner game machinery; each candidate plays under its
     own versioned agent config + deck + net weights).
  5. Record the block result into `experiments/factory/matrix.json`
     under `matrix.lock` (atomic tmp + `os.replace`, same pattern as
     `candidates.json`).
  6. Refit Bradley-Terry ratings from the full matrix (§4).
  7. Write `matrix_rating` and `matrix_games` back onto candidate
     records in `candidates.json` via the existing `merge_save` path.
  8. Touch its heartbeat file (§7).

### 2.2 Trainer worker (NEW)

- Continuous Windows Scheduled Task. GPU+CPU. BelowNormal priority.
- Loop body:
  1. Check PAUSE kill-switch → skip iteration if present.
  2. Pick the **highest-matrix-rated deck that has no net-tier
     candidate** (no `*-searchnet` candidate registered for that deck).
  3. Run `PerDeckNetTrainer` (existing producer-consumer
     CPU-data-gen/GPU-train overlap, behind the `daemon.py` `Trainer`
     protocol — no protocol changes).
  4. Register the new `*-searchnet` candidate into the queue via
     `candidates.json`. The matrix worker picks it up automatically on
     its next pool refresh — no cross-process signalling needed.
  5. Touch its heartbeat file (§7).
- **The 1/day training stamp gate is REMOVED for this worker.** It
  trains back-to-back as fast as the hardware allows.
- After the top-N (**default N=5**) matrix-rated decks all have nets,
  continue down the ranking in matrix-rating order.
- **Disk hygiene:** delete training data files after a successful train
  (keep exported weights). A **~20 GB disk cap** on the training-data
  directory is the backstop: before starting data generation, if the
  directory exceeds the cap, delete oldest data files first and log the
  deletion; never silently fill the disk.

### 2.3 Watch loop (EXISTING, 15-min cadence — reduced scope)

- Retains **exclusive ownership** of gate / submit / harvest / dashboard.
- Its own 150-game eval step is **RETIRED**: it reads matrix ratings
  from candidate records instead of playing games. This dissolves the
  QUEUED→EVALUATING double-eval gap — only the matrix worker runs games.
- Its at-most-1/day `PerDeckNetTrainer` step is **also retired**:
  training ownership moves wholly to the Trainer worker (stated
  interpretation of the approved design — the watch loop's retained
  responsibilities are enumerated as gate/submit/harvest/dashboard, and
  two concurrent `PerDeckNetTrainer` owners would contend for the GPU).
- Cadence, single-instance lock, PAUSE handling, deck-matrix queue
  refill, digest suppression: all unchanged.

## 3. Ledger: `experiments/factory/matrix.json`

- Guarded by its own `matrix.lock`; written atomically (per-PID tmp file
  + `os.replace`), exactly the `candidates.json` pattern.
- Contents: for every ordered-into-canonical-form candidate pair, the
  cumulative `games` and `wins_a` counts (candidate identity =
  durable name + semantic version, matching `candidates.json`
  identities); plus the latest fitted ratings, fit timestamp, and the
  UTC date stamp of the last EXPERIMENTS.md daily snapshot (§7).
- `matrix.json` is the full-reproducibility record: every block appends
  enough (pair, block size, wins, timestamp, git commit of the worker)
  to re-derive the ratings from scratch.
- First write must handle a **virgin directory** (no pre-created parent)
  — see Testing (§9).

## 4. Ranking: Bradley-Terry

- Bradley-Terry strength fit over the pairwise win/loss matrix.
- **Simple iterative solver** (standard MM fixed-point iteration), pure
  stdlib — no numpy/scipy at runtime, consistent with the serve-time
  stdlib-only convention (`value_net.py` precedent).
- **Golden-vector tested:** hardcoded input matrix → hardcoded expected
  ratings (non-circular, computed once from an independent reference
  implementation), plus convergence and invariance-under-relabeling
  checks.
- **Degenerate-case regularization:** add a small pseudo-count prior
  (0.5 virtual win + 0.5 virtual loss against a virtual average
  opponent, per candidate) so candidates with 0 wins or 0 losses and
  freshly-added candidates get finite, stable ratings.
- Output: **one scalar `matrix_rating` per candidate** (normalized so
  the pool mean is 0 on the log-strength scale), plus `matrix_games`
  (total games that candidate has in the matrix).
- **Pair scheduling equalizes coverage:** always pick the active pair
  with the fewest cumulative games (ties broken deterministically by
  candidate-id order) so ratings aren't skewed by uneven sampling. New
  candidates therefore get games fastest — every pair involving a
  0-game newcomer sorts first.

## 5. Pool policy

- Active pool = **all non-retired candidates** in `candidates.json`.
- **Pool cap: 24.** When over cap, auto-retire the lowest-`matrix_rating`
  candidate that has at least the minimum game count
  (**default 15 games** — the same floor as gate coverage in §6, chosen
  so no candidate is retired on less evidence than the gate itself
  would trust; a single tunable constant shared by both).
- **NEVER auto-retire:** (a) the `is_incumbent`-pinned candidate, or
  (b) any candidate currently **on the ladder** — i.e. submitted and
  occupying one of Kaggle's two counted (most-recent) submission slots,
  as reconciled from the submission ledger/harvest records.
- If everything over-cap is protected or under the minimum game count,
  do not retire anything this iteration (soft cap; log it).

## 6. Gate re-keying + migration

- `matrix_rating` becomes **authoritative** for a candidate only at
  minimum coverage: **≥15 games against ≥8 distinct opponents**.
- Below that coverage, the gate **falls back to existing `local_wr`
  semantics** unchanged (dual-baseline breakdown, incumbent bar).
- A candidate with **neither** matrix coverage **nor** a legacy
  `local_wr` (possible post-migration, since the watch loop no longer
  runs evals) is **not gate-eligible**: it stays queued until the matrix
  worker's fewest-games-first scheduling gives it coverage. This is the
  stated resolution of the migration gap — no games are ever played by
  the watch loop to fill it.
- **`is_incumbent` manual pin preserved** (2026-07-20 gate-recal
  semantics; manual re-pin remains a weekly-review checklist step).
- Challenger rule under matrix authority: challenger must beat the
  incumbent's `matrix_rating` **with a margin**. Margin is expressed as
  the BT-implied head-to-head win probability: challenger qualifies
  when `P(challenger beats incumbent | ratings) ≥ 0.55`
  (**default, configurable**) — chosen to mirror the 0.55 arena bars
  used since Slice 4 rather than an arbitrary rating-scale delta.
- **Untouched:** champion-pairing guard, 5/day hard cap + atomic
  reserve-token-before-upload counter, submit locks, description
  standard, pre-submit `check_auth()` guard.

## 7. Safety / ops

- Both new workers:
  - honor the **PAUSE kill-switch** file (checked every loop iteration,
    before any games/training start);
  - run at **BelowNormal** priority;
  - take their own **single-instance locks** (`matrix.lock`,
    `trainer.lock`) so a scheduler re-fire while a previous instance is
    alive exits immediately as a no-op; `matrix.lock` additionally
    guards all `matrix.json` writes (one lock, both duties);
  - write **heartbeat files** (`experiments/factory/matrix.heartbeat`,
    `trainer.heartbeat`) — touched at least once per block (matrix) /
    once per data-gen-or-train chunk (trainer). The dashboard status
    strip surfaces each as a **worker-stale badge** exactly like the
    existing scheduler-stale badge; stale thresholds: **30 min** for the
    matrix worker, **6 h** for the trainer (a single long train is
    normal; a silent half-day is not). Thresholds are constants, not
    config sprawl.
- **Scheduler registration** extends the `register_factory_task.ps1`
  conventions (new task names `ptcg-factory-matrix` and
  `ptcg-factory-trainer`, 15-min repetition + `AtStartup`, instance
  locks make re-fires cheap):
  - register-first-then-retire ordering (never leave zero tasks);
  - loud failure: `try/catch` with `-ErrorAction Stop`, nonzero exit on
    any registration error — no exit-code laundering;
  - explicit `uv` path resolution for elevated shells (no per-user PATH
    reliance);
  - post-registration `Get-ScheduledTask` verification of every task
    the script claims to have registered.
- **EXPERIMENTS.md** gets **ONE daily matrix-snapshot summary row**
  (top ratings, pool size, total games; written by the matrix worker on
  the first block completed after UTC date rollover, stamped in
  `matrix.json` so it cannot double-write). Full reproducibility lives
  in `matrix.json`, not EXPERIMENTS.md.

## 8. Invariants

1. **Single game-player.** Only the matrix worker plays evaluation
   games. The watch loop plays zero games; the trainer plays games only
   inside `PerDeckNetTrainer` data generation (training data, never
   recorded as evaluation results).
2. **Ladder is still the evaluator of record.** `matrix_rating` ranks
   candidates locally to choose what to submit; it never overrides
   ladder evidence in weekly-review decisions.
3. **Submission invariants untouched.** 5/day hard cap (atomic
   reserve-token counter), champion-pairing guard, submit-phase lock,
   and `is_incumbent` manual pin behave bit-identically to the
   2026-07-17/20 implementations.
4. **All `matrix.json` writes are lock-guarded and atomic** (per-PID
   tmp + `os.replace` under `matrix.lock`). Two concurrent writers must
   never produce a torn file or a lost update — enforced by an
   adversarial 2-process test (§9).
5. **Coverage before authority.** A candidate's `matrix_rating` is
   gate-authoritative only at ≥15 games against ≥8 distinct opponents;
   below that the gate uses legacy `local_wr`; with neither, the
   candidate is not gate-eligible.
6. **Protected candidates are never auto-retired**: the incumbent pin
   and any candidate in a counted ladder slot are exempt from pool-cap
   retirement unconditionally.
7. **Auto-retire requires evidence**: a candidate may be auto-retired
   only with ≥ the minimum game count (15); never on a sparse rating.
8. **PAUSE stops everything.** One kill-switch file halts new games,
   new training runs, and new submissions across all three processes
   within one loop iteration each.
9. **Fail-safe scheduler changes.** Task registration is
   register-first-then-retire and fails loudly; at no point during
   registration/unregistration can the machine be left with zero live
   factory tasks without a nonzero exit code saying so.
10. **Pure-stdlib serve path.** The BT solver and everything the
    matrix/gate read path imports run with no torch/numpy dependency;
    torch remains dev/training-only.
11. **Coverage-uniform sampling.** Pair selection is
    fewest-games-first; no rating-weighted or self-reinforcing sampling
    that would bias the BT fit.
12. **Disk backstop.** The trainer never lets the training-data
    directory exceed ~20 GB; data files are deleted after successful
    trains, weights are kept.
13. **Reproducibility.** Every matrix block record carries enough
    (pair identities+versions, counts, timestamp, worker git commit) to
    refit ratings from scratch; EXPERIMENTS.md carries only the daily
    summary row.
14. **No new autocommit.** Both workers follow the factory's
    no-autocommit convention: outputs land in the working tree and are
    committed by interactive sessions, per
    `.claude/rules/factory-resume-probe.md`.

## 9. Testing

**Unit:**
- BT solver: golden vectors (independent reference values, hardcoded),
  convergence, relabeling invariance, degenerate 0-win/0-loss
  regularization.
- Pair scheduler: coverage properties (fewest-games-first, deterministic
  tie-break, newcomer-priority emergent behavior).
- Pool cap / auto-retire rules, including **never-retire-incumbent** and
  never-retire-on-ladder, and the soft-cap no-op path.
- Trainer queue policy: highest-rated-deck-without-net selection,
  top-N-then-continue ordering, stamp-gate removal.
- Gate coverage-fallback logic: matrix-authoritative vs local_wr
  fallback vs not-gate-eligible, and the ≥0.55 BT-implied margin rule.

**Lesson-derived mandatory tests** (each cites its precedent):
- **Adversarial 2-process concurrent-write test on `matrix.json`** —
  two real processes racing block writes must not lose an update or
  tear the file (TOCTOU lesson, submission-counter fix `178043b`).
- **Virgin-directory first-write test for `matrix.json`** — the test
  must NOT pre-create the parent directory (`tmp_path` lesson, 2 prior
  recurrences: `ledger_lock()` slice 7A, `make_random_policy_weights`
  slice 7B).
- **Scheduler-registration script real path reviewed as-if-it-will-fail**
  — the non-`-DryRun` branch is untested by any dry-run by
  construction; review must confirm loud failure and
  register-first-then-retire ordering (dryrun-is-not-the-real-thing
  lesson, commits `72bc0f8`/`225686c`).

**Acceptance (Smoke Test Ladder rung 3):** a **real short-duration run
of BOTH workers on the actual machine** — matrix worker completes at
least one full block→record→refit→merge_save cycle against the live
`candidates.json`, trainer completes at least a truncated
data-gen→train→register cycle — with the dashboard showing both
heartbeat badges healthy, **before** scheduler go-live.

**Go-live:** actual scheduled-task registration is an explicit
**POST-MERGE rung** (master = production; the elevated registration run
happens after merge, per the continuous-factory precedent), followed by
`Get-ScheduledTask`/`Get-ScheduledTaskInfo` liveness verification per
`.claude/rules/factory-task-scheduler-liveness.md`.

## 10. Out of scope

- Any change to ladder identity files (`src/ptcg/submission_main.py`,
  `src/ptcg/agents/current.py`).
- Any change to Kaggle submission cadence, cap, or counted-slot
  semantics.
- New search-architecture or policy-improvement experiments (Slices 4–7B
  closed those hypotheses; this feature is ranking + throughput
  infrastructure only).
- Static-key Kaggle auth swap (separate carry-forward from the
  2026-07-20 auth-expiry bugfix).
