# Compute-Saturation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Saturate the machine 24/7 with a continuous round-robin deck tournament (CPU) and unlocked per-deck net training (GPU), so the factory's 5/day Kaggle slots are spent on matrix-ranked candidates instead of weakly-ranked ones.

**Architecture:** Two new always-on worker processes (matrix worker = sole game-runner writing Bradley-Terry ratings to `experiments/factory/matrix.json`; trainer worker = sole training owner) coordinate with the existing 15-min watch loop purely through the candidates ledger (`merge_save`) and the new matrix ledger. The watch loop's own eval and daily-training steps are retired; the gate is re-keyed to `matrix_rating` with a coverage fallback to `local_wr`.

**Tech Stack:** Python 3.11 + uv, pure-stdlib runtime (torch dev-only for training), pytest, PowerShell scheduled tasks.

**Spec:** `docs/superpowers/specs/2026-07-20-compute-saturation-design.md` (commit 8d59fbb). The spec's 14 invariants bind every task.

## Global Constraints

- Windows host; path contains a space — always quote. `uv run pytest` (slow marker deselected by default via `addopts = "-m 'not slow'"`).
- Every text-file write uses `encoding="utf-8"` explicitly (Windows cp1252 lesson).
- All ledger writes atomic (per-PID tmp + `os.replace`) under their lock; `matrix.lock` guards `matrix.json`, `candidates.json.lock` (via `ledger_lock`) guards the candidates ledger. Workers NEVER take `watch.lock`.
- PAUSE file (`experiments/factory/PAUSE`) stops all workers' work loops.
- Constants (single source, defined in Task 2 `tournament.py`): `POOL_CAP = 24`, `BLOCK_GAMES = 10`, `MIN_COVERAGE_GAMES = 15`, `MIN_COVERAGE_OPPONENTS = 8`, `RETIRE_FLOOR_GAMES = 15`, `INCUMBENT_MARGIN_P = 0.55`, `WORKER_STALE_MIN = 30`, `DISK_CAP_GB = 20`.
- Stage by explicit path only; NEVER `git add .` (live factory output sits uncommitted in this tree). On `index.lock` contention wait 2s, retry up to 3×.
- Ladder identity files `src/ptcg/submission_main.py` and `src/ptcg/agents/current.py` must be untouched by this feature (verify at Task 12).
- New module names are `tournament.py` and `bt.py` — never `matrix.py` (collides conceptually with existing `deck_matrix.py`).

---

### Task 1: Bradley-Terry solver (`bt.py`)

**Files:**
- Create: `src/ptcg/factory/bt.py`
- Test: `tests/test_factory_bt.py`

**Interfaces:**
- Consumes: nothing (pure stdlib).
- Produces: `fit_ratings(wins: dict[tuple[str, str], int]) -> dict[str, float]` — keys of `wins` are ordered pairs `(winner_id, loser_id)` → win count; returns strength per id, normalized so the geometric mean is 1.0. Also `head_to_head_p(ratings, a, b) -> float` = `ratings[a] / (ratings[a] + ratings[b])`.

Algorithm: Zermelo/MM iteration with +0.1 pseudo-win smoothing on every observed pairing (both directions) so undefeated/never-winning candidates stay finite. Iterate until max relative rating change < 1e-9 or 500 iterations. Complexity O(iters × pairs); at pool cap 24 → ≤276 pairs → trivially fast.

- [ ] **Step 1: Write failing tests** — golden vectors chosen to be analytically checkable (hand-verify the arithmetic before transcribing; do NOT trust these prose values blindly — derive them):

```python
"""Tests for the pure-stdlib Bradley-Terry solver."""
import math
from ptcg.factory.bt import fit_ratings, head_to_head_p


def test_two_player_analytic():
    # A beats B 3, B beats A 1 -> with +0.1 smoothing both ways:
    # effective wins A=3.1, B=1.1 -> P(A beats B) = 3.1/4.2
    ratings = fit_ratings({("A", "B"): 3, ("B", "A"): 1})
    p = head_to_head_p(ratings, "A", "B")
    assert math.isclose(p, 3.1 / 4.2, rel_tol=1e-6)


def test_symmetric_round_robin_all_equal():
    wins = {}
    ids = ["A", "B", "C"]
    for i in ids:
        for j in ids:
            if i != j:
                wins[(i, j)] = 5  # everyone 5-5 vs everyone
    ratings = fit_ratings(wins)
    vals = list(ratings.values())
    assert max(vals) / min(vals) < 1.0001


def test_dominance_ordering():
    # A dominates B dominates C -> rating order must follow
    wins = {("A", "B"): 8, ("B", "A"): 2, ("B", "C"): 8, ("C", "B"): 2,
            ("A", "C"): 9, ("C", "A"): 1}
    r = fit_ratings(wins)
    assert r["A"] > r["B"] > r["C"]


def test_undefeated_candidate_finite():
    r = fit_ratings({("A", "B"): 10})  # B never wins
    assert 0 < r["B"] < r["A"] < float("inf")


def test_normalization_geometric_mean_one():
    r = fit_ratings({("A", "B"): 3, ("B", "A"): 1, ("B", "C"): 2, ("C", "B"): 2})
    gm = math.exp(sum(math.log(v) for v in r.values()) / len(r))
    assert math.isclose(gm, 1.0, rel_tol=1e-6)
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_factory_bt.py -v` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
"""Pure-stdlib Bradley-Terry rating fit (Zermelo/MM iteration).

Smoothing: +0.1 pseudo-wins are added in BOTH directions for every pairing
that has at least one recorded game, keeping ratings finite for undefeated
or winless candidates without materially distorting well-sampled pairs.
"""
from __future__ import annotations

import math

SMOOTH = 0.1
MAX_ITERS = 500
TOL = 1e-9


def _smoothed(wins: dict[tuple[str, str], int]) -> dict[tuple[str, str], float]:
    pairs = {frozenset(k) for k in wins if k[0] != k[1]}
    out: dict[tuple[str, str], float] = {}
    for pair in pairs:
        a, b = sorted(pair)
        out[(a, b)] = wins.get((a, b), 0) + SMOOTH
        out[(b, a)] = wins.get((b, a), 0) + SMOOTH
    return out


def fit_ratings(wins: dict[tuple[str, str], int]) -> dict[str, float]:
    w = _smoothed(wins)
    ids = sorted({i for pair in w for i in pair})
    if not ids:
        return {}
    r = {i: 1.0 for i in ids}
    games: dict[tuple[str, str], float] = {}
    for (a, b), wa in w.items():
        key = (a, b) if a < b else (b, a)
        games[key] = games.get(key, 0.0) + wa
    for _ in range(MAX_ITERS):
        max_delta = 0.0
        new_r = {}
        for i in ids:
            num = sum(wa for (a, _b), wa in w.items() if a == i)
            den = 0.0
            for (a, b), n in games.items():
                if i == a:
                    den += n / (r[a] + r[b])
                elif i == b:
                    den += n / (r[a] + r[b])
            new_r[i] = num / den if den > 0 else r[i]
        gm = math.exp(sum(math.log(v) for v in new_r.values()) / len(new_r))
        for i in ids:
            new_r[i] /= gm
            max_delta = max(max_delta, abs(new_r[i] - r[i]) / r[i])
        r = new_r
        if max_delta < TOL:
            break
    return r


def head_to_head_p(ratings: dict[str, float], a: str, b: str) -> float:
    return ratings[a] / (ratings[a] + ratings[b])
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_factory_bt.py -v` → 5 PASS. If `test_two_player_analytic` fails, re-derive the expected value from the MM fixed point before touching the solver (2-player MM converges to the smoothed win ratio; verify by hand).
- [ ] **Step 5: Commit** — `git add src/ptcg/factory/bt.py tests/test_factory_bt.py && git commit -m "feat: pure-stdlib Bradley-Terry solver for matrix tournament"`

---

### Task 2: Matrix ledger + Candidate fields (`tournament.py` part 1)

**Files:**
- Create: `src/ptcg/factory/tournament.py`
- Modify: `src/ptcg/factory/candidates.py` (add 3 fields to `Candidate`, after `local_breakdown`)
- Test: `tests/test_factory_tournament_ledger.py`

**Interfaces:**
- Consumes: `ledger_lock` from `candidates.py:123` (reused for `matrix.lock`).
- Produces: constants listed in Global Constraints; `MatrixLedger` with `load(path) -> MatrixLedger`, `.record(winner_id, loser_id, wins: int, games: int)`, `.wins_dict() -> dict[tuple[str,str],int]`, `.games_between(a, b) -> int`, `.opponents_of(id) -> set[str]`, `.total_games(id) -> int`, `.meta: dict` (holds `last_snapshot_date`), `save(path, ledger)` (atomic per-PID tmp + `os.replace`, `mkdir(parents=True, exist_ok=True)` first, `encoding="utf-8"`). New `Candidate` fields: `matrix_rating: float | None = None`, `matrix_games: int = 0`, `matrix_opponents: int = 0`.

JSON shape: `{"meta": {...}, "pairs": {"idA|idB": {"a": idA, "b": idB, "wins_a": int, "wins_b": int}}}` with `idA < idB` lexically in the key.

- [ ] **Step 1: Write failing tests.** Cover: round-trip; record accumulates; **virgin-directory first write** (point at `tmp_path / "never" / "created" / "matrix.json"` — do NOT pre-create parents; the save must succeed); **adversarial 2-process concurrency**: two `multiprocessing.Process` workers each acquire `matrix.lock` via `ledger_lock`, read-modify-write 50 `record` calls into the same file; final total games must equal exactly 100 × games-per-record (no lost update). Also: `Candidate` new fields default correctly and `merge_save` round-trips them (construct via `Candidate.create(...)`, set `matrix_rating=1.5`, `merge_save`, reload, assert).
- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_factory_tournament_ledger.py -v` → FAIL.
- [ ] **Step 3: Implement.** `MatrixLedger` as a `@dataclass` wrapping `pairs: dict[str, dict]` + `meta: dict`. `record()` normalizes ordering (`a, b = sorted((winner_id, loser_id))` then increments `wins_a`/`wins_b` accordingly; `games` param increments implicit total = wins_a+wins_b, so `record(w, l, wins, games)` adds `wins` to winner and `games - wins` to loser). All mutation helpers pure-Python; locking is the CALLER's job (worker loop wraps read-modify-write in `ledger_lock(matrix_lock_path, timeout_s=30.0)`). The Candidate field additions are 3 dataclass lines — no other candidates.py change.
- [ ] **Step 4: Run full suite** — `uv run pytest` → all green (baseline 415 + new). The Candidate change is additive; `test_factory_*` suites must stay green untouched.
- [ ] **Step 5: Commit** — `git add src/ptcg/factory/tournament.py src/ptcg/factory/candidates.py tests/test_factory_tournament_ledger.py && git commit -m "feat: matrix ledger with atomic writes + Candidate matrix fields"`

---

### Task 3: Active pool + pair scheduler (`tournament.py` part 2)

**Files:**
- Modify: `src/ptcg/factory/tournament.py`
- Test: `tests/test_factory_tournament_scheduler.py`

**Interfaces:**
- Consumes: `Candidate`, `Status` from `candidates.py` (Status values: QUEUED/EVALUATING/EVALUATED/BELOW_INCUMBENT/SUBMITTED/SCORED/RETIRED).
- Produces: `active_pool(candidates: list[Candidate]) -> list[Candidate]` (all non-RETIRED); `next_pair(pool: list[Candidate], ledger: MatrixLedger) -> tuple[Candidate, Candidate] | None` (fewest `games_between` first; ties broken by lexical id pair for determinism; `None` iff pool < 2); `is_protected(c: Candidate) -> bool` (True if `c.is_incumbent` or `c.submitted_at` is not None — on-ladder).

- [ ] **Step 1: Write failing tests.** Properties, not vectors: (a) with a fresh ledger, repeated `next_pair` + fake `record` of `BLOCK_GAMES` per pick visits every distinct pair once before any pair repeats (uniform coverage; simulate 5 candidates → 10 pairs → first 10 picks are all distinct); (b) `next_pair` returns None for pool of 0 or 1; (c) RETIRED candidates never appear in `active_pool`; (d) `is_protected` true for incumbent and for `submitted_at` set, false otherwise.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** (straightforward min-by with deterministic tie-break: `min(pairs, key=lambda p: (ledger.games_between(p[0].id, p[1].id), p[0].id, p[1].id))`). Complexity O(pool²) per pick = ≤276 comparisons — fine.
- [ ] **Step 4: Run to verify pass**, then full suite.
- [ ] **Step 5: Commit** — `git add src/ptcg/factory/tournament.py tests/test_factory_tournament_scheduler.py && git commit -m "feat: active pool + fewest-games-first pair scheduler"`

---

### Task 4: Block runner + rating write-back (`tournament.py` part 3)

**Files:**
- Modify: `src/ptcg/factory/tournament.py`
- Test: `tests/test_factory_tournament_runner.py`

**Interfaces:**
- Consumes: `build_agent(candidate, deck)` from `evaluate.py:53`; `load_deck`, `run_series` from `ptcg.arena.runner` (same imports `_real_series` uses at `evaluate.py:147-156`); `fit_ratings` from Task 1; `merge_save` from `candidates.py:185`.
- Produces: `play_block(cand_a, cand_b, n_games=BLOCK_GAMES, series_fn=None) -> tuple[int, int]` (wins_a, wins_b — candidate-vs-candidate, each piloted by its OWN agent per `build_agent`); `refresh_ratings(ledger, candidates) -> list[Candidate]` — fits BT on `ledger.wins_dict()`, then for each active candidate sets `matrix_rating`, `matrix_games = ledger.total_games(id)`, `matrix_opponents = len(ledger.opponents_of(id))`, and promotes `status` QUEUED→EVALUATED when coverage reached (`matrix_games >= MIN_COVERAGE_GAMES and matrix_opponents >= MIN_COVERAGE_OPPONENTS`); returns ONLY the modified candidates (for `merge_save`).

`play_block` signature mirrors `_real_series`'s injection pattern: `series_fn` defaults to a thin wrapper over `run_series(build_agent(a, deck_a), build_agent(b, deck_b), deck_a, deck_b, n)`; tests inject a fake. IMPORTANT landmark check before coding: grep `evaluate.py` for the exact `run_series` call shape and mirror argument order; if it differs from this plan's description, report plan-drift.

- [ ] **Step 1: Write failing tests** (fake `series_fn` returning fixed splits): coverage promotion happens exactly at the threshold (14 games/8 opp → still QUEUED; 15/8 → EVALUATED); ratings written to modified candidates only; SUBMITTED/SCORED candidates keep their status (only QUEUED promotes — never overwrite ladder statuses).
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, then full suite.
- [ ] **Step 5: Commit** — `git add src/ptcg/factory/tournament.py tests/test_factory_tournament_runner.py && git commit -m "feat: candidate-vs-candidate block runner + BT rating write-back"`

---

### Task 5: Pool cap, auto-retire, daily snapshot (`tournament.py` part 4)

**Files:**
- Modify: `src/ptcg/factory/tournament.py`
- Test: `tests/test_factory_tournament_pool.py`

**Interfaces:**
- Produces: `enforce_pool_cap(candidates, ledger) -> list[Candidate]` — while non-RETIRED count > `POOL_CAP`: retire the lowest-`matrix_rating` candidate that has `matrix_games >= RETIRE_FLOOR_GAMES` and NOT `is_protected(c)`; if no eligible victim, stop (never force-retire under-sampled or protected candidates); returns modified. `append_daily_snapshot(ledger, candidates, experiments_md_path, today: str) -> bool` — appends ONE markdown row per UTC day (guard: `ledger.meta["last_snapshot_date"]`) with pool size, total games, top-5 by rating; `encoding="utf-8"`; returns whether it wrote.

- [ ] **Step 1: Write failing tests:** cap enforcement retires exactly the right victim; incumbent and on-ladder never retired even when lowest; under-floor candidates never retired; snapshot writes once per day (second call same `today` → False, file unchanged); snapshot appends (never truncates — read file back and assert prior content still present).
- [ ] **Step 2–4: RED → implement → GREEN + full suite.**
- [ ] **Step 5: Commit** — `git add src/ptcg/factory/tournament.py tests/test_factory_tournament_pool.py && git commit -m "feat: pool cap with protected auto-retire + daily matrix snapshot"`

---

### Task 6: Matrix worker loop + entry script

**Files:**
- Create: `scripts/factory_matrix_worker.py`
- Modify: `src/ptcg/factory/tournament.py` (add `worker_tick` + heartbeat)
- Test: `tests/test_factory_matrix_worker.py`

**Interfaces:**
- Consumes: everything from Tasks 2–5; `ledger_lock`, `load_ledger`; `throttle_below_normal` from `watch.py:23`; `FactoryPaths` from `cycle.py:18`.
- Produces: `worker_tick(paths, *, series_fn=None, now=None, log=print) -> str` — one iteration: write heartbeat (`experiments/factory/matrix_heartbeat.json`, `{"ts": iso, "detail": str}`, atomic, utf-8) → if PAUSE exists return `"paused"` → load candidates → `active_pool` → `next_pair` (None → return `"idle"`) → `play_block` OUTSIDE any lock (games are slow; only ledger read-modify-write is locked) → under `ledger_lock(matrix.lock)`: reload matrix ledger, `record`, save → `refresh_ratings` + `enforce_pool_cap` → `merge_save` modified → `append_daily_snapshot` → return `"played"`. Script: argparse `--max-ticks` (default 0 = forever, used by tests/acceptance), `--block-games`; takes `matrix.worker.lock` single-instance lock via `instance_lock` pattern (`ledger_lock(path, timeout_s=0.5, stale_after_s=8*3600)`), calls `throttle_below_normal`, loops `worker_tick` with a 5-second sleep after `"paused"`/`"idle"` ticks, no sleep after `"played"`.
- [ ] **Step 1: Write failing tests** (fake series_fn, tmp FactoryPaths): tick returns "paused" when PAUSE exists and plays nothing; "idle" with <2 candidates; "played" records games and updates ratings end-to-end; heartbeat file written on EVERY tick including paused; second worker process finds `matrix.worker.lock` held → script exits 0 with "busy" (mirror `test_instance_lock_excludes_second_holder` pattern from `tests/test_factory_watch.py`).
- [ ] **Step 2–4: RED → implement → GREEN + full suite.**
- [ ] **Step 5: Rung-3-lite smoke (REAL, bounded):** `uv run python scripts/factory_matrix_worker.py --max-ticks 2 --block-games 2` against the REAL `experiments/factory/` — verify it plays 2 real blocks, `matrix.json` appears (first-ever real write — the virgin-directory case), heartbeat updates, candidates gain `matrix_games`. Then `git checkout -- experiments/factory` is NOT possible (untracked matrix.json) — instead verify the real files are sane and LEAVE them (they're legitimate factory state); note the run in the task report.
- [ ] **Step 6: Commit** — `git add scripts/factory_matrix_worker.py src/ptcg/factory/tournament.py tests/test_factory_matrix_worker.py && git commit -m "feat: continuous matrix worker loop with heartbeat + PAUSE + single-instance lock"`

---

### Task 7: Trainer worker + entry script

**Files:**
- Create: `src/ptcg/factory/trainer_worker.py`, `scripts/factory_trainer_worker.py`
- Test: `tests/test_factory_trainer_worker.py`

**Interfaces:**
- Consumes: `Trainer` protocol + `run_daemon` (`daemon.py:17/53`); `PerDeckNetTrainer` (`trainers.py:30` — fields `deck, games_per_cycle=300, epochs=30, data_dir, weights_dir, priority, runner`); `Candidate`/`merge_save`; heartbeat + PAUSE patterns from Task 6.
- Produces: `pick_next_deck(candidates) -> str | None` — highest-`matrix_rating` candidate whose `deck` has NO candidate with `agent_kind == "search-net"` sharing the same deck; ties/None-ratings sort last; returns deck filename or None. `trainer_tick(paths, *, trainer_factory=None, log=print) -> str` — heartbeat (`trainer_heartbeat.json`) → PAUSE → `disk_usage_gb(data_dir) > DISK_CAP_GB` → return `"disk-capped"` (loud log) → `pick_next_deck` (None → `"idle"`) → run one `PerDeckNetTrainer` cycle via `run_daemon(trainer, n_cycles=1, ...)` → on success DELETE the cycle's data files (keep weights) → `"trained"`. Script mirrors Task 6's: `trainer.worker.lock`, `--max-ticks`, BelowNormal, 60-second sleep on idle.
- The 1/day stamp (`training_due`) is NOT consulted here — continuous cadence is the point. Do not import it.
- [ ] **Step 1: Write failing tests** (fake trainer_factory recording calls): pick_next_deck ordering and no-net filter (a deck with an existing `search-net` candidate is skipped); disk cap short-circuits; data files deleted after success, kept after failure; PAUSE honored; heartbeat always written.
- [ ] **Step 2–4: RED → implement → GREEN + full suite.**
- [ ] **Step 5: Commit** — `git add src/ptcg/factory/trainer_worker.py scripts/factory_trainer_worker.py tests/test_factory_trainer_worker.py && git commit -m "feat: continuous trainer worker (top-rated decks first, disk-capped)"`

---

### Task 8: Gate re-key + submit ranking

**Files:**
- Modify: `src/ptcg/factory/gate.py`, `src/ptcg/factory/submit.py:165`
- Test: extend `tests/test_factory_gate.py`, `tests/test_factory_submit.py`

**Interfaces:**
- Consumes: `head_to_head_p` (Task 1), Candidate matrix fields (Task 2).
- Produces: in `gate.py`: `has_matrix_coverage(c) -> bool` (`matrix_games >= 15 and matrix_opponents >= 8` — import the Task-2 constants, do not re-literal); `effective_score(c) -> float` (matrix_rating if coverage else `local_wr or 0.0` — NOTE the two scales never compare against each other, see decide rule). `incumbent()` (gate.py:132-181) keeps its existing local_wr-based selection/ordering unchanged — the `is_incumbent` pin dominates in practice and re-keying incumbent SELECTION is out of scope; only the decide() MERIT COMPARISON is re-keyed. `decide()` change (currently `gate.py:225` computes `edge = (candidate.local_wr or 0.0) - inc.local_wr`): when BOTH challenger and incumbent have matrix coverage → challenger passes the merit bar iff `head_to_head_p({a: c.matrix_rating, b: inc.matrix_rating}, a, b) >= INCUMBENT_MARGIN_P`; when EITHER lacks coverage → existing local_wr edge/merit_margin path unchanged (never mix scales in one comparison). `novel_axis` exception (gate.py:231) parity band maps to `head_to_head_p >= 0.5 - parity_band` on the matrix path. `submit.py:164` ranking key becomes `-(effective_score(c))` via import from gate.
- [ ] **Step 1: Write failing tests:** matrix-path merit pass/fail at exactly P=0.55 boundary (construct ratings giving P=0.549 → no, 0.551 → yes; hand-verify: P = r_c/(r_c+r_inc), so r_c = 0.55, r_inc = 0.45 → P = 0.55 exactly — derive your fixtures from this identity); mixed-coverage falls back to local_wr path (existing tests must keep passing untouched — they use candidates with no matrix fields, which default to no-coverage); novel-axis parity on matrix path; submit ranking prefers covered high-rating over uncovered high-local_wr? NO — effective_score mixes scales in ranking; acceptable per spec because ranking only orders the EVALUATED set for gate consideration and decide() itself never cross-compares scales; add a test documenting this: an uncovered 0.9-local_wr candidate outranks a covered 0.8-rating one (BT ratings hover near 1.0; local_wr ≤ 1.0 — the transitional mixed period is short; assert current chosen behavior so it is explicit, with a comment pointing at spec §gate-migration).
- [ ] **Step 2–4: RED → implement → GREEN + full suite** (all pre-existing gate/submit tests must pass unmodified except any asserting the sort key line itself — grep `tests/` for `local_wr` sort assertions first and list touched ones in the report).
- [ ] **Step 5: Commit** — `git add src/ptcg/factory/gate.py src/ptcg/factory/submit.py tests/test_factory_gate.py tests/test_factory_submit.py && git commit -m "feat: gate re-keyed to matrix rating with coverage fallback (P>=0.55 margin)"`

---

### Task 9: Retire watch-loop eval + training (full test sweep in THIS task)

**Files:**
- Modify: `scripts/factory_watch_once.py` (remove training step L108-120; refill trigger L102-106), `src/ptcg/factory/cycle.py` (skip eval), `src/ptcg/factory/watch.py` (remove `training_due`/`record_training` L61-84), `src/ptcg/factory/deck_matrix.py` (refill trigger consumers if any)
- Test: `tests/test_factory_watch.py`, `tests/test_factory_cycle.py` (grep for others — see Step 1)

**Interfaces:**
- Consumes: `POOL_CAP`, `active_pool` (Tasks 2–3).
- Produces: `watch_once` drops `skip_training`/`train_fn` kwargs and the training block entirely; refill trigger becomes `len(active_pool(candidates)) < POOL_CAP` → `refill_fn(..., max_new=min(max_refill, POOL_CAP - len(pool)))`. `run_cycle` gains `skip_eval: bool = False` keyword; `watch_once` passes `skip_eval=True`; when True the `evaluate_queued` stage is skipped and gate/submit consume statuses as-is (matrix worker owns QUEUED→EVALUATED promotion per Task 4). `training_stamp.json` is no longer read or written (leave the file on disk; harmless).

- [ ] **Step 1: Enumerate breakage FIRST (assertion-sweep lesson).** Run `Grep -n "training_due\|record_training\|skip_training\|train_fn\|refills_only_when_queue_empty" tests/` and list every hit. Known from planning-time inventory (verify, don't trust): `test_watch_once_trains_only_when_refill_dry_and_stamp_due`, `test_watch_once_training_failure_still_runs_cycle`, `test_watch_once_corrupt_training_stamp_recovers`, `test_training_due_treats_corrupt_stamp_as_due`, `test_training_stamp_roundtrip`, `test_watch_once_refills_only_when_queue_empty`, possibly `test_watch_once_passes_suppress_flag_and_cadence`. DELETE the training-behavior tests (the behavior is gone), REWRITE the refill test to assert the new pool<cap trigger, and keep every other watch test green.
- [ ] **Step 2: RED** — rewrite tests to the new contract first, watch them fail against old code.
- [ ] **Step 3: Implement removals + refill change + `skip_eval`.**
- [ ] **Step 4: Full suite green.** This task is the keystone: `uv run pytest` exit 0 is the gate, not any single file.
- [ ] **Step 5: Commit** — `git add scripts/factory_watch_once.py src/ptcg/factory/cycle.py src/ptcg/factory/watch.py tests/test_factory_watch.py tests/test_factory_cycle.py && git commit -m "refactor: watch loop retires eval+training; refill keyed to pool cap"` (add any other swept test files by explicit path).

---

### Task 10: Dashboard worker heartbeat badges

**Files:**
- Modify: `src/ptcg/factory/dashboard.py` (`assemble_state` L67, `render_status_strip` L163, constants L19)
- Test: extend `tests/test_factory_dashboard.py`

**Interfaces:**
- Consumes: heartbeat JSON files from Tasks 6–7 (`matrix_heartbeat.json`, `trainer_heartbeat.json`).
- Produces: `WORKER_STALE_MIN = 30` constant; `assemble_state` adds `workers: [{"name": "matrix", "ts": ..., "detail": ..., "stale": bool, "missing": bool}, {"name": "trainer", ...}]` (missing file → `missing: True`, rendered as a grey "not registered" item, NOT a red badge — pre-go-live the workers legitimately don't exist); `render_status_strip` appends one item per worker: green `matrix ✓ <detail>` when fresh, red `badge-warn` `MATRIX STALE <age>m` when `now - ts > 30 min`.
- [ ] **Step 1: Write failing tests:** fresh/stale/missing renderings; stale threshold boundary (29 min → fresh, 31 min → stale); malformed heartbeat JSON → treated as missing + a warning in `state["warnings"]` (never crashes the render — `safe_render` isolation must hold).
- [ ] **Step 2–4: RED → implement → GREEN + full suite.**
- [ ] **Step 5: Commit** — `git add src/ptcg/factory/dashboard.py tests/test_factory_dashboard.py && git commit -m "feat: dashboard worker heartbeat badges (30-min staleness)"`

---

### Task 11: Scheduler registration for the two workers

**Files:**
- Modify: `scripts/register_factory_task.ps1` (params L19, constants L26, register flow L109, retire L124, unregister L30)
- Test: extend `tests/test_register_factory_task.py`

**Interfaces:**
- Produces: two additional tasks registered alongside `ptcg-factory-continuous`: `ptcg-factory-matrix` (action: `<uv> run python scripts/factory_matrix_worker.py`, working dir repo root) and `ptcg-factory-trainer` (`scripts/factory_trainer_worker.py`); triggers: AtStartup + Once-with-repetition every 15 min acting as a WATCHDOG (workers hold single-instance locks, so a firing while alive exits "busy" instantly — mirroring the existing watch-task pattern); `-ExecutionTimeLimit (New-TimeSpan -Hours 0)` (no limit — these run forever, unlike the 8h-limited watch task); `MultipleInstances IgnoreNew`.
- HARD RULES from the go-live incident (dryrun-is-not-the-real-thing, `72bc0f8`/`225686c`): register ALL new tasks FIRST, verify each with `Get-ScheduledTask` immediately after its `Register-ScheduledTask`, and only then touch/retire anything old; every real cmdlet in `try/catch` with `-ErrorAction Stop` and `exit 1` + loud message on failure; `$UvPath` explicit-resolution logic reused for both new actions (elevated shells don't inherit per-user PATH). `-Unregister` removes all three tasks. `-DryRun` prints the full plan without calling any real cmdlet.
- [ ] **Step 1: Enumerate output-assertion breakage FIRST:** the existing dry-run tests (`test_dry_run_register_default_time`, `test_dry_run_register_custom_interval`, `test_dry_run_unregister`, both `never_calls_real_registration_cmdlets` tests) assert current `-DryRun` output — update them in THIS task to the 3-task output, plus new tests: dry-run mentions all three task names; dry-run register mentions no-execution-time-limit for the workers; syntax parse test still passes.
- [ ] **Step 2–4: RED → implement → GREEN** (`uv run pytest tests/test_register_factory_task.py -v`, then full suite).
- [ ] **Step 5: Review the REAL path as-if-it-will-fail** (cannot sandbox scheduler registration): confirm in the diff — loud failure on every real cmdlet, register-before-retire ordering, post-register verify, UvPath resolution on all three actions. Record this review explicitly in the task report.
- [ ] **Step 6: Commit** — `git add scripts/register_factory_task.ps1 tests/test_register_factory_task.py && git commit -m "feat: register matrix + trainer worker scheduled tasks (watchdog pattern)"`
- **NOTE: actually RUNNING the registration is the POST-MERGE go-live rung (Task 12 step 6) — not part of this task.**

---

### Task 12: Acceptance — Smoke Test Ladder + docs + go-live plan

**Files:**
- Modify: `docs/factory-operations.md`, `docs/weekly-review-checklist.md` (add worker-liveness checks for the two new tasks), `experiments/EXPERIMENTS.md` (acceptance note)
- No new source.

- [ ] **Rung 1 — automated smoke:** `uv run pytest` full suite, exit 0, report exact counts (expect ≥ baseline 415 + ~35 new).
- [ ] **Rung 2 — verify the automation:** confirm right branch (`git branch --show-current` = feature/compute-saturation), test count nonzero and grew vs baseline, `git diff master...HEAD -- src/ptcg/submission_main.py src/ptcg/agents/current.py` is EMPTY (ladder identity untouched), matrix.json from Task 6's real run present + parseable, dashboard renders (`uv run python scripts/render_dashboard.py` exit 0) showing worker items.
- [ ] **Rung 3 — real dual-worker run (pre-merge, bounded):** launch BOTH workers simultaneously for a bounded real run — `uv run python scripts/factory_matrix_worker.py --max-ticks 3` and `uv run python scripts/factory_trainer_worker.py --max-ticks 1` (trainer tick may be long — data-gen 300 games; acceptable, run it in the orchestrator's background shell and wait for real exit). Verify: no lock collisions with each other or a live watch firing; heartbeats update; matrix games recorded; if trainer completed, a new searchnet candidate is registered and data files were cleaned. ORCHESTRATOR owns these processes (settled lesson) — the dispatched agent only sets up and harvests.
- [ ] **Docs:** factory-operations.md gains a "Continuous workers" section (start/stop, locks, heartbeats, PAUSE semantics, disk cap); weekly-review-checklist adds `Get-ScheduledTaskInfo ptcg-factory-matrix` / `ptcg-factory-trainer` liveness checks next to the existing continuous-task check.
- [ ] **Commit docs** — `git add docs/factory-operations.md docs/weekly-review-checklist.md experiments/EXPERIMENTS.md && git commit -m "docs: continuous-worker operations + weekly-review liveness checks"`
- [ ] **POST-MERGE GO-LIVE RUNG (explicit, after merge to master):** run `scripts\register_factory_task.ps1` (elevated; `Start-Transcript` capture per the elevated-console lesson), then verify all three tasks via `Get-ScheduledTask`/`Get-ScheduledTaskInfo`, watch one real matrix firing land in the heartbeat + dashboard. This rung runs against production master by design — mark it in plan.md as post-merge, not a Finish blocker.

---

## Self-Review (planner, completed)

- **Spec coverage:** all 6 spec sections map to tasks (1→T1-T7 architecture; 2→T1-T5 ranking/pool; 3→T8 gate; 4→T7 trainer; 5→T6/T10/T11 safety/ops; 6→lesson tests in T2/T6/T11/T12). Daily snapshot = T5. PAUSE = T6/T7. Dashboard = T10.
- **Placeholder scan:** no TBDs; T2/T5/T7 steps compress RED/GREEN into one line each but name exact behaviors to test — acceptable granularity for this codebase's established test patterns.
- **Type consistency:** `fit_ratings`/`head_to_head_p` (T1) match T8's import; `MatrixLedger` API (T2) matches T3/T4/T5 consumption; heartbeat filenames (T6/T7) match T10; constants defined once in T2.
- **Complexity glance:** BT O(iters×276), scheduler O(276)/pick — no scale traps at pool cap 24. The real cost centers are the games themselves, which is the point.
