"""Continuous trainer worker (spec compute-saturation, Task 7).

Picks the highest-`matrix_rating` candidate's deck that does NOT already
have a `search-net` candidate, runs exactly one `PerDeckNetTrainer` cycle
through `daemon.run_daemon`, and deletes that cycle's self-play data on
success (the trained weights are kept - `export_and_register` has already
written them into the candidate ledger by that point). This spreads
distribution coverage across decks by priority rather than continuously
retraining an already-covered deck.

The 1/day training stamp (`watch.training_due`/`record_training`) is NOT
consulted here - continuous cadence is the point, mirroring the matrix
worker's (Task 6) always-on design.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Callable

from ptcg.factory.candidates import Candidate, load_ledger
from ptcg.factory.cycle import FactoryPaths
from ptcg.factory.daemon import run_daemon
from ptcg.factory.evolution import _deck_pool_path
from ptcg.factory.genomes import DeckGenome, load_pool, pool_merge_save
from ptcg.factory.tournament import DISK_CAP_GB
from ptcg.factory.trainers import PerDeckNetTrainer


def _pick_from_deck_pool(pool_path: Path) -> str | None:
    """Highest-`rating` non-retired deck genome with `net_weights is None`.
    None-ratings sort last, ties broken by id for determinism. Returns
    `None` if the pool has no netless-and-eligible genome (the caller then
    falls back to the legacy candidate-based path -- see `pick_next_deck`).

    Only `status == "retired"` is excluded; "anchor"/"meta-anchor" genomes
    are eligible for training, mirroring `evolution.active_cells`'s existing
    "only retired is excluded" convention (JUDGMENT CALL -- the brief only
    specifies LIVE explicitly and is silent on anchor/meta-anchor)."""
    decks = [g for g in load_pool(pool_path) if isinstance(g, DeckGenome)]
    eligible = [d for d in decks if d.status != "retired" and d.net_weights is None]
    if not eligible:
        return None
    eligible.sort(key=lambda d: (
        d.rating is None,
        -(d.rating if d.rating is not None else 0.0),
        d.id,
    ))
    return eligible[0].csv


def pick_next_deck(candidates: list[Candidate], pool_path: Path | None = None) -> str | None:
    """Highest-`matrix_rating` candidate's deck, skipping any deck that
    already has a `search-net` candidate (continuous coverage spreads
    across decks rather than retraining an already-covered one).
    None-ratings sort last. Returns `None` if no deck is eligible.

    Pool-aware (Task 7): when `pool_path` is given and the deck-pool file
    exists, prefer the highest-rating netless deck GENOME from it (see
    `_pick_from_deck_pool`). If the pool exists but every genome already has
    net_weights (or the pool is empty), falls through to the legacy
    candidate-based path below. When `pool_path` is `None` or the file
    doesn't exist, behavior is byte-identical to the pre-Task-7 legacy path.
    """
    if pool_path is not None and Path(pool_path).exists():
        from_pool = _pick_from_deck_pool(Path(pool_path))
        if from_pool is not None:
            return from_pool

    covered = {c.deck for c in candidates if c.agent_kind == "search-net"}
    eligible = [c for c in candidates if c.deck not in covered]
    if not eligible:
        return None
    eligible.sort(key=lambda c: (
        c.matrix_rating is None,
        -(c.matrix_rating if c.matrix_rating is not None else 0.0),
        c.id,
    ))
    return eligible[0].deck


def _dir_size_gb(path: Path) -> float:
    path = Path(path)
    if not path.exists():
        return 0.0
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / (1024 ** 3)


def _write_heartbeat(path: Path, detail: str, now: dt.datetime) -> None:
    """Atomic per-PID tmp + `os.replace` write, mirroring `tournament.py`'s
    `_write_heartbeat`/`save()` convention. `mkdir(parents=True)` first so a
    virgin (never-created) parent directory does not raise on the worker's
    very first tick."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ts": now.isoformat(), "detail": detail}
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def _default_trainer_factory(paths: FactoryPaths) -> "Callable[[str], PerDeckNetTrainer]":
    def factory(deck: str) -> PerDeckNetTrainer:
        deck_path = Path(deck)
        if not deck_path.is_absolute():
            deck_path = Path(paths.root) / deck_path
        return PerDeckNetTrainer(deck=deck_path, data_dir=paths.trainer_data_dir)
    return factory


def _attach_net_weights(pool_path: Path, deck_csv: str, net_weights: str,
                       log: Callable[[str], None] = print) -> None:
    """Fresh-load the pool, set `net_weights` on the deck genome matching
    `deck_csv`, `pool_merge_save`. No row is held across the training
    window -- this is called only AFTER `run_daemon` returns, so this is a
    single fresh-load-then-merge-save, not a read-modify-write spanning the
    training run (see `trainer_tick`'s docstring). Only attaches when a
    matching, still-netless genome is found -- a no-op if `deck_csv` came
    from the legacy candidate-based fallback rather than the pool, or if a
    concurrent tick already attached weights to this genome."""
    fresh = load_pool(pool_path)
    for genome in fresh:
        if isinstance(genome, DeckGenome) and genome.csv == deck_csv and genome.net_weights is None:
            genome.net_weights = net_weights
            pool_merge_save(pool_path, [genome], log=log)
            return


def trainer_tick(paths: FactoryPaths, *,
                 trainer_factory: "Callable[[str], object] | None" = None,
                 log: Callable[[str], None] = print) -> str:
    """One iteration of the continuous trainer worker loop.

    Order: write the heartbeat first (so a liveness check sees a fresh
    timestamp on EVERY tick, including a paused/idle/disk-capped one that
    trains nothing) -> honor PAUSE -> disk cap check on the trainer's own
    data dir (loud log + short-circuit at/over `DISK_CAP_GB`, BEFORE
    picking a deck or spending any compute) -> pick the highest-rated
    uncovered deck (pool-aware, Task 7 -- see `pick_next_deck`) -> run
    exactly one trainer cycle via `run_daemon(n_cycles=1)` -> on success,
    delete that cycle's self-play data file(s) (the trained weights are
    kept) and, when the deck came from the deck-pool, attach the produced
    weights path onto that deck genome's `net_weights` field.

    The pool attach holds NO pool row across the training window: the pool
    is loaded fresh only AFTER `run_daemon` returns, then a single genome's
    `net_weights` field is set and `pool_merge_save`d (a fresh-load-under-
    lock merge) -- so a concurrent pool mutation (e.g. a breeding tick
    landing mid-training) is preserved rather than clobbered by a stale
    read-modify-write spanning the whole training run.

    On a training failure, `run_daemon` re-raises per its own documented
    crash-isolation contract; this function does NOT swallow that
    exception, so execution never reaches the deletion/attach steps and the
    cycle's data file survives for a later retry/inspection.

    Returns "paused", "idle" (nothing eligible to train), "disk-capped", or
    "trained".
    """
    now = dt.datetime.now(dt.timezone.utc)
    _write_heartbeat(paths.trainer_heartbeat, "tick", now)

    if paths.pause_file.exists():
        return "paused"

    used_gb = _dir_size_gb(paths.trainer_data_dir)
    if used_gb >= DISK_CAP_GB:
        log(f"trainer: DISK CAP REACHED ({used_gb:.2f} GB >= {DISK_CAP_GB} GB) "
            f"at {paths.trainer_data_dir} - skipping this tick")
        return "disk-capped"

    candidates = load_ledger(paths.ledger)
    pool_path = _deck_pool_path(paths)
    deck = pick_next_deck(candidates, pool_path)
    if deck is None:
        return "idle"

    factory = trainer_factory if trainer_factory is not None else _default_trainer_factory(paths)
    trainer = factory(deck)

    # Capture exactly the file(s) this cycle's prepare_data produced, so
    # deletion is correct regardless of the trainer's own naming convention
    # (real PerDeckNetTrainer or a test double).
    produced: list[Path] = []
    real_prepare_data = trainer.prepare_data

    def _prepare_and_record(cycle: int) -> Path:
        path = Path(real_prepare_data(cycle))
        produced.append(path)
        return path

    trainer.prepare_data = _prepare_and_record  # type: ignore[method-assign]

    # Same recording wrapper, applied to export_and_register so the exact
    # net_weights string this cycle produced (already normalized to the
    # candidates.json convention -- repo-relative, posix, by
    # export_and_register itself) is available for the pool attach below,
    # without re-deriving that path-normalization logic here.
    produced_candidates: list[Candidate] = []
    real_export_and_register = trainer.export_and_register

    def _export_and_record(weights_path: Path, cycle: int, cands: list) -> Candidate:
        cand = real_export_and_register(weights_path, cycle, cands)
        produced_candidates.append(cand)
        return cand

    trainer.export_and_register = _export_and_record  # type: ignore[method-assign]

    slug = Path(deck).stem
    state_path = paths.trainer_data_dir / f"{slug}.daemon_state.json"
    run_daemon(trainer, n_cycles=1, ledger_path=paths.ledger,
              state_path=state_path, log=log)

    for path in produced:
        path.unlink(missing_ok=True)

    if pool_path.exists() and produced_candidates:
        net_weights = produced_candidates[-1].agent_config.get("net_weights")
        if net_weights is not None:
            _attach_net_weights(pool_path, deck, net_weights, log=log)

    return "trained"
