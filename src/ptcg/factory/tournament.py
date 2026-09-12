"""Matrix ledger: durable candidate-vs-candidate win/loss tallies (spec
compute-saturation).

The matrix ledger is the raw pairwise game-count record that `bt.py`'s
`fit_ratings` consumes to produce Bradley-Terry ratings. Storage is a flat
JSON file keyed by lexically-sorted candidate-id pairs so the on-disk shape
never depends on which side happened to be recorded as "winner" for a given
`record()` call.

Global compute-saturation constants live here as the single source of
truth; later tasks (pool-cap eviction, coverage gating, gate re-keying,
worker staleness) import them from this module rather than re-literaling.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from typing import Callable

from ptcg.factory.bt import fit_ratings
from ptcg.factory.candidates import Candidate, Status, ledger_lock, load_ledger, merge_save
from ptcg.factory.evaluate import ROOT, build_agent

# --- Global constants (single source; imported by later compute-saturation
# tasks - do not re-literal these values elsewhere). ---
POOL_CAP = 24
BLOCK_GAMES = 10
MIN_COVERAGE_GAMES = 15
MIN_COVERAGE_OPPONENTS = 8
RETIRE_FLOOR_GAMES = 15
INCUMBENT_MARGIN_P = 0.55
WORKER_STALE_MIN = 30
# Trainer heartbeat-stale threshold is deliberately much longer than the
# matrix worker's: a single training run is normal and can legitimately run
# for hours between heartbeat-worthy progress ticks (spec compute-saturation
# design doc, 2026-07-20). Single source of truth - dashboard.py imports
# this rather than re-literaling it.
TRAINER_STALE_MIN = 360
DISK_CAP_GB = 20


def _pair_key(a: str, b: str) -> str:
    lo, hi = sorted((a, b))
    return f"{lo}|{hi}"


@dataclass
class MatrixLedger:
    """In-memory view of the matrix ledger JSON.

    `pairs` maps `"idA|idB"` (idA < idB lexically) -> `{"a": idA, "b": idB,
    "wins_a": int, "wins_b": int}`. `meta` is a free-form dict; the only key
    currently defined is `last_snapshot_date` (owned by a later task's daily
    digest writer).
    """

    pairs: dict[str, dict] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "MatrixLedger":
        path = Path(path)
        if not path.exists():
            return cls()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(pairs=payload.get("pairs", {}), meta=payload.get("meta", {}))

    def record(self, winner_id: str, loser_id: str, wins: int, games: int) -> None:
        """Add `wins` (for `winner_id`) and `games - wins` (for `loser_id`)
        to the pair's running tally. Despite the parameter names, this is
        NOT restricted to "the side that won the block overall" - it is
        simply "the side whose win count is `wins`" - so a single block's
        result can be recorded with either candidate named first.
        """
        a, b = sorted((winner_id, loser_id))
        key = _pair_key(a, b)
        entry = self.pairs.setdefault(key, {"a": a, "b": b, "wins_a": 0, "wins_b": 0})
        losses = games - wins
        if winner_id == a:
            entry["wins_a"] += wins
            entry["wins_b"] += losses
        else:
            entry["wins_b"] += wins
            entry["wins_a"] += losses

    def wins_dict(self) -> dict[tuple[str, str], int]:
        out: dict[tuple[str, str], int] = {}
        for entry in self.pairs.values():
            a, b = entry["a"], entry["b"]
            out[(a, b)] = entry["wins_a"]
            out[(b, a)] = entry["wins_b"]
        return out

    def games_between(self, a: str, b: str) -> int:
        entry = self.pairs.get(_pair_key(a, b))
        if entry is None:
            return 0
        return entry["wins_a"] + entry["wins_b"]

    def opponents_of(self, id: str) -> set[str]:
        out: set[str] = set()
        for entry in self.pairs.values():
            if entry["a"] == id:
                out.add(entry["b"])
            elif entry["b"] == id:
                out.add(entry["a"])
        return out

    def total_games(self, id: str) -> int:
        total = 0
        for entry in self.pairs.values():
            if entry["a"] == id or entry["b"] == id:
                total += entry["wins_a"] + entry["wins_b"]
        return total


def save(path: Path, ledger: MatrixLedger) -> None:
    """Atomic per-PID tmp + `os.replace` write, mirroring
    `candidates.save_ledger`'s convention. `mkdir(parents=True)` first so a
    virgin (never-created) parent directory does not raise on first write.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": ledger.meta, "pairs": ledger.pairs}
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def active_pool(candidates: list[Candidate]) -> list[Candidate]:
    """All non-RETIRED candidates, in the order given."""
    return [c for c in candidates if c.status != Status.RETIRED]


def next_pair(pool: list[Candidate], ledger: MatrixLedger) -> tuple[Candidate, Candidate] | None:
    """Pick the pool pair with the fewest recorded games between them.

    Ties are broken by lexical (id, id) order for determinism. Returns
    `None` iff the pool has fewer than 2 candidates.
    """
    if len(pool) < 2:
        return None
    pairs = itertools.combinations(pool, 2)
    return min(
        pairs,
        key=lambda p: (ledger.games_between(p[0].id, p[1].id), p[0].id, p[1].id),
    )


def is_protected(c: Candidate) -> bool:
    """True if `c` must not be evicted from the active pool: it is the
    manually-pinned incumbent, or it is (or has ever been) on the ladder.
    """
    return c.is_incumbent or c.submitted_at is not None


def _default_series_fn(cand_a: Candidate, cand_b: Candidate, n_games: int) -> tuple[int, int]:
    """Mirrors `evaluate.py`'s `_real_series` injection pattern: each
    candidate is piloted by its OWN agent (via `build_agent`), not a fixed
    heuristic baseline. Imports `load_deck`/`run_series` lazily, matching
    `_real_series`'s own lazy-import style.
    """
    from ptcg.arena.runner import load_deck, run_series

    deck_a = load_deck(ROOT / cand_a.deck)
    deck_b = load_deck(ROOT / cand_b.deck)
    stats = run_series(build_agent(cand_a, deck_a), build_agent(cand_b, deck_b),
                        deck_a, deck_b, n_games)
    return stats.wins_a, stats.wins_b


def make_series_fn(n_games: int) -> "Callable[[Candidate, Candidate, int], tuple[int, int]]":
    """Returns a `series_fn` that ignores whatever game count `play_block`
    forwards to it and always plays `n_games` real games via
    `_default_series_fn` instead. `play_block`/`worker_tick` always request
    the module default `BLOCK_GAMES` (there is no size-override parameter on
    that path); this is the public factory the CLI worker (`scripts/
    factory_matrix_worker.py`) uses to thread `--block-games` through
    without reaching into this module's private `_default_series_fn` - the
    same shape tests use to inject a fake series_fn.
    """
    def fn(cand_a: Candidate, cand_b: Candidate, _requested_games: int) -> tuple[int, int]:
        return _default_series_fn(cand_a, cand_b, n_games)
    return fn


def play_block(cand_a: Candidate, cand_b: Candidate, n_games: int = BLOCK_GAMES,
               series_fn: "Callable[[Candidate, Candidate, int], tuple[int, int]] | None" = None,
               ) -> tuple[int, int]:
    """Play one candidate-vs-candidate block. Returns (wins_a, wins_b) for
    `cand_a`/`cand_b` respectively - does NOT write to the matrix ledger;
    callers record the result via `MatrixLedger.record` themselves.

    The returned tuple EXCLUDES draws (the engine's `SeriesStats.draws`,
    `winner == 2` in `ptcg.arena.runner`): `wins_a + wins_b` can be less
    than the requested `n_games` whenever the block produced draws. The
    matrix ledger is a win/loss Bradley-Terry model, so draws are dropped
    rather than credited to either side. Callers MUST record with
    `games = wins_a + wins_b` (the actual decided-game count), NEVER the
    requested `n_games` - `MatrixLedger.record(winner_id, loser_id, wins,
    games)` credits `games - wins` to the other side, so passing the
    requested `n_games` when draws occurred would silently count those
    draws as losses for the other side, corrupting the matrix.

    `series_fn` defaults to `_default_series_fn` (real games via
    `run_series`); tests inject a fake `(cand_a, cand_b, n_games) ->
    (wins_a, wins_b)` callable to avoid playing real games.
    """
    fn = series_fn if series_fn is not None else _default_series_fn
    return fn(cand_a, cand_b, n_games)


def refresh_ratings(ledger: MatrixLedger, candidates: list[Candidate]) -> list[Candidate]:
    """Fit Bradley-Terry ratings from `ledger.wins_dict()` and write
    `matrix_rating`/`matrix_games`/`matrix_opponents` onto every active
    (non-RETIRED) candidate. Promotes `status` QUEUED -> EVALUATED exactly
    when coverage is reached (`matrix_games >= MIN_COVERAGE_GAMES and
    matrix_opponents >= MIN_COVERAGE_OPPONENTS`) - candidates in any other
    status (EVALUATING, EVALUATED, BELOW_INCUMBENT, SUBMITTED, SCORED) keep
    their status untouched even if their rating/coverage fields change.

    Returns only the candidates whose fields actually changed, for
    `merge_save`.
    """
    ratings = fit_ratings(ledger.wins_dict())
    modified: list[Candidate] = []
    for c in active_pool(candidates):
        new_rating = ratings.get(c.id)
        new_games = ledger.total_games(c.id)
        new_opponents = len(ledger.opponents_of(c.id))
        new_status = c.status
        if (c.status == Status.QUEUED
                and new_games >= MIN_COVERAGE_GAMES
                and new_opponents >= MIN_COVERAGE_OPPONENTS):
            new_status = Status.EVALUATED

        if (new_rating, new_games, new_opponents, new_status) == (
                c.matrix_rating, c.matrix_games, c.matrix_opponents, c.status):
            continue

        c.matrix_rating = new_rating
        c.matrix_games = new_games
        c.matrix_opponents = new_opponents
        c.status = new_status
        modified.append(c)

    return modified


def enforce_pool_cap(candidates: list[Candidate], ledger: MatrixLedger) -> list[Candidate]:
    """Retire candidates from the active pool until it is at or under
    `POOL_CAP`.

    Each iteration retires the eligible candidate (`matrix_games >=
    RETIRE_FLOOR_GAMES` and not `is_protected(c)`) with the lowest
    `matrix_rating` - ties (and unrated candidates, whose `matrix_rating`
    is `None` and sort as lowest) broken by `id` for determinism. Stops as
    soon as no eligible victim remains, even if the pool is still over cap:
    under-sampled and protected (incumbent / ever-on-ladder) candidates are
    never force-retired.

    `ledger` is accepted to mirror this module's other functions' shape
    (eligibility is read entirely from the candidates' own
    `matrix_games`/`matrix_rating` fields, which `refresh_ratings` keeps in
    sync) - it is not read directly here.

    Returns only the candidates whose status changed to `Status.RETIRED`,
    for `merge_save`.
    """
    modified: list[Candidate] = []
    while len(active_pool(candidates)) > POOL_CAP:
        eligible = [c for c in active_pool(candidates)
                    if c.matrix_games >= RETIRE_FLOOR_GAMES and not is_protected(c)]
        if not eligible:
            break
        victim = min(
            eligible,
            key=lambda c: (c.matrix_rating if c.matrix_rating is not None
                            else float("-inf"), c.id),
        )
        victim.status = Status.RETIRED
        modified.append(victim)

    return modified


def append_daily_snapshot(ledger: MatrixLedger, candidates: list[Candidate],
                           experiments_md_path: Path, today: str) -> bool:
    """Append one markdown row to `experiments_md_path` summarizing the
    matrix ledger for UTC day `today`: active pool size, total games
    recorded across the active pool, and the top-5 active candidates by
    `matrix_rating`.

    Guarded by `ledger.meta["last_snapshot_date"]` so at most one row is
    written per UTC day - a second call with the same `today` is a no-op
    (file left byte-identical) and returns `False`. APPEND-only: existing
    file content is read back and preserved, never truncated.

    On a successful write this mutates `ledger.meta["last_snapshot_date"]`
    but does NOT save the ledger itself - the caller (Task 6's
    `worker_tick`) is responsible for persisting the ledger afterward.
    """
    if ledger.meta.get("last_snapshot_date") == today:
        return False

    pool = active_pool(candidates)
    total_games = sum(ledger.total_games(c.id) for c in pool)
    ranked = sorted(
        (c for c in pool if c.matrix_rating is not None),
        key=lambda c: c.matrix_rating,
        reverse=True,
    )
    top5 = ", ".join(f"{c.id} ({c.matrix_rating:.3f})" for c in ranked[:5])
    row = (f"- **{today}** matrix snapshot: pool={len(pool)}, "
           f"games={total_games}, top5: {top5 or '(none rated)'}\n")

    path = Path(experiments_md_path)
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = ""
    path.write_text(text + row, encoding="utf-8")

    ledger.meta["last_snapshot_date"] = today
    return True


_worker_commit_cache: str | None = None


def _worker_commit() -> str:
    """This worker process's short git commit hash, resolved once and
    cached for the process lifetime (spec Invariant 13: reproducibility -
    every matrix block needs the worker's commit to refit ratings from
    scratch). Never raises: any failure (git missing, not a repo,
    subprocess error) falls back to the literal string "unknown" so a
    provenance-logging problem can never break a tick.
    """
    global _worker_commit_cache
    if _worker_commit_cache is None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(ROOT), capture_output=True, text=True, timeout=5,
            )
            out = result.stdout.strip()
            _worker_commit_cache = out if result.returncode == 0 and out else "unknown"
        except Exception:
            _worker_commit_cache = "unknown"
    return _worker_commit_cache


def _append_block_provenance(matrix_path: Path, cand_a_id: str, cand_b_id: str,
                              wins_a: int, wins_b: int, now: dt.datetime) -> None:
    """Append-only JSONL sidecar (`matrix_blocks.jsonl`, next to matrix.json)
    recording this block's raw per-block provenance (spec Invariant 13):
    pair identities, decided-game wins, timestamp, and the worker's git
    commit - enough to refit ratings from scratch, unlike matrix.json's
    cumulative-only tallies. UTF-8, append mode, never truncates.

    Failure-isolated like `dashboard.safe_render`: any exception (disk
    full, permissions, a failing `_worker_commit()`) is swallowed here so a
    provenance-logging problem never breaks a tick - this is best-effort
    auxiliary record-keeping, not the tick's primary contract.
    """
    try:
        path = matrix_path.with_name("matrix_blocks.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({
            "ts": now.isoformat(),
            "a": cand_a_id,
            "b": cand_b_id,
            "wins_a": wins_a,
            "wins_b": wins_b,
            "commit": _worker_commit(),
        })
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _matrix_path(paths) -> Path:
    return paths.matrix


def _write_heartbeat(path: Path, detail: str, now: dt.datetime) -> None:
    """Atomic per-PID tmp + `os.replace` write, mirroring `save()`'s
    convention. `mkdir(parents=True)` first so a virgin (never-created)
    parent directory does not raise on the worker's very first tick.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ts": now.isoformat(), "detail": detail}
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def worker_tick(paths, *, series_fn: "Callable[[Candidate, Candidate, int], tuple[int, int]] | None" = None,
                now: dt.datetime | None = None, log: Callable[[str], None] = print) -> str:
    """One iteration of the continuous matrix worker loop (spec
    compute-saturation, Task 6). `paths` is a `cycle.FactoryPaths`.

    Order: write the heartbeat first (so a liveness check sees a fresh
    timestamp on EVERY tick, including a paused/idle one that plays nothing)
    -> honor PAUSE -> pick the fewest-games pair -> play it OUTSIDE any lock
    (games are slow; only the matrix ledger's own read-modify-write is
    locked) -> under `ledger_lock(paths.matrix)`: reload the matrix ledger
    (another process may have written since our unlocked pair-selection
    read), record the block, save, RELOAD `candidates.json` fresh (the
    pre-play_block load is now stale - `play_block` can run for minutes,
    during which another process, e.g. the watch loop marking a submission,
    can mutate a candidate on disk), refresh ratings + enforce the pool cap
    against those FRESH rows only, persist candidate changes via
    `merge_save`, and append the daily snapshot (re-saving the matrix
    ledger only if the snapshot actually wrote, since it mutates
    `ledger.meta` without saving itself).

    The reload-under-lock is required because `merge_save` does a FULL-ROW
    replace by id (see `candidates.merge_save`'s docstring): it fresh-loads
    `candidates.json` under its own lock but then overwrites, for every id
    present in the caller's `modified` list, the ENTIRE row with the
    caller's object - not just the fields the caller changed. If
    `refresh_ratings`/`enforce_pool_cap` mutated the pre-play_block
    (stale) `Candidate` objects, and a concurrent writer had updated that
    same candidate's status/submitted_at/etc. on disk in the meantime,
    `merge_save` would silently clobber the concurrent update with the
    stale snapshot's values. Reloading `candidates.json` fresh under the
    matrix lock, immediately before mutating, closes that window (it is
    not airtight against every possible interleaving, but it collapses the
    minutes-long play_block window down to the lock hold, matching this
    project's existing TOCTOU-mitigation pattern for the submission
    counter).

    Returns "paused" (PAUSE file present, nothing played), "idle" (fewer
    than 2 active candidates, nothing to pair), or "played".
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    matrix_path = _matrix_path(paths)
    _write_heartbeat(paths.matrix_heartbeat, "tick", now)

    if paths.pause_file.exists():
        return "paused"

    candidates = load_ledger(paths.ledger)
    pool = active_pool(candidates)
    selection_ledger = MatrixLedger.load(matrix_path)
    pair = next_pair(pool, selection_ledger)
    if pair is None:
        return "idle"

    cand_a, cand_b = pair
    wins_a, wins_b = play_block(cand_a, cand_b, series_fn=series_fn)

    with ledger_lock(matrix_path):
        ledger = MatrixLedger.load(matrix_path)
        # CRITICAL: play_block's (wins_a, wins_b) EXCLUDES draws - record
        # games=wins_a+wins_b (the actual decided-game count), NEVER the
        # requested block size, or draws get silently counted as losses.
        ledger.record(cand_a.id, cand_b.id, wins_a, wins_a + wins_b)
        _append_block_provenance(matrix_path, cand_a.id, cand_b.id, wins_a, wins_b, now)
        save(matrix_path, ledger)

        # Reload fresh: the pre-play_block `candidates` load above is now
        # stale (play_block can take minutes). refresh_ratings/
        # enforce_pool_cap must mutate current rows, never the stale
        # snapshot - merge_save does a full-row replace by id, so mutating
        # stale objects here would silently clobber any concurrent update
        # (e.g. the watch loop marking a submission) that landed on disk
        # while this block was playing. See the docstring above.
        fresh_candidates = load_ledger(paths.ledger)
        refreshed = refresh_ratings(ledger, fresh_candidates)
        retired = enforce_pool_cap(fresh_candidates, ledger)
        modified = {c.id: c for c in refreshed}
        modified.update({c.id: c for c in retired})
        if modified:
            merge_save(paths.ledger, list(modified.values()), log=log)

        today = now.date().isoformat()
        wrote_snapshot = append_daily_snapshot(ledger, fresh_candidates, paths.experiments_md, today)
        if wrote_snapshot:
            save(matrix_path, ledger)

    return "played"
