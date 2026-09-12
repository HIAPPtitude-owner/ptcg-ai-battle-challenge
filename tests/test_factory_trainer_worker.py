"""Tests for the continuous trainer worker + entry script (spec
compute-saturation, Task 7): `pick_next_deck`'s top-rated / no-search-net /
None-rating ordering, `trainer_tick`'s paused/idle/disk-capped/trained
ticks, the always-on heartbeat, the delete-on-success/keep-on-failure data
file contract, and the script's single-instance "busy" exit.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ptcg.factory.candidates import Candidate, load_ledger, merge_save
from ptcg.factory.cycle import FactoryPaths
from ptcg.factory.evolution import _deck_pool_path
from ptcg.factory.genomes import DeckGenome, deck_genome_id, load_pool, pool_merge_save
from ptcg.factory.trainer_worker import pick_next_deck, trainer_tick
from ptcg.factory.watch import instance_lock
from scripts.factory_trainer_worker import run_worker


def _make_deck_genome(key: int, csv: str, *, rating: float | None = None,
                      net_weights: str | None = None, status: str = "live") -> DeckGenome:
    """`key` seeds a distinct 60-card list so distinct test decks get
    distinct content-addressed ids (deck_genome_id hashes `cards`)."""
    cards = [key] * 60
    return DeckGenome(id=deck_genome_id(cards), cards=cards, csv=csv,
                      rating=rating, net_weights=net_weights, status=status)


def _make(name: str, *, deck: str = "d.csv", agent_kind: str = "heuristic",
          matrix_rating: float | None = None) -> Candidate:
    c = Candidate.create(name=name, version="v0.1", deck=deck, agent_kind=agent_kind)
    c.matrix_rating = matrix_rating
    return c


# ---------- pick_next_deck ----------

def test_pick_next_deck_highest_rating_first():
    candidates = [
        _make("a", deck="a.csv", matrix_rating=1.0),
        _make("b", deck="b.csv", matrix_rating=2.5),
        _make("c", deck="c.csv", matrix_rating=1.8),
    ]
    assert pick_next_deck(candidates) == "b.csv"


def test_pick_next_deck_skips_deck_with_existing_search_net():
    candidates = [
        _make("a", deck="a.csv", matrix_rating=2.5),
        _make("a-searchnet", deck="a.csv", agent_kind="search-net", matrix_rating=2.9),
        _make("b", deck="b.csv", matrix_rating=1.0),
    ]
    assert pick_next_deck(candidates) == "b.csv"


def test_pick_next_deck_none_ratings_sort_last():
    candidates = [
        _make("a", deck="a.csv", matrix_rating=None),
        _make("b", deck="b.csv", matrix_rating=0.1),
    ]
    assert pick_next_deck(candidates) == "b.csv"


def test_pick_next_deck_none_when_nothing_eligible():
    candidates = [_make("a", deck="a.csv", agent_kind="search-net", matrix_rating=2.0)]
    assert pick_next_deck(candidates) is None


def test_pick_next_deck_empty_list():
    assert pick_next_deck([]) is None


# ---------- pick_next_deck: deck-pool coupling (Task 7) ----------

def test_pick_next_deck_pool_present_picks_netless_top_rated(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    pool_path = _deck_pool_path(paths)
    pool_merge_save(pool_path, [
        _make_deck_genome(1, "decks/low.csv", rating=1.0),
        _make_deck_genome(2, "decks/high.csv", rating=5.0),
        _make_deck_genome(3, "decks/netted.csv", rating=9.0, net_weights="w.json"),
    ])

    assert pick_next_deck([], pool_path) == "decks/high.csv"


def test_pick_next_deck_pool_all_netted_falls_back_to_legacy(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    pool_path = _deck_pool_path(paths)
    pool_merge_save(pool_path, [
        _make_deck_genome(1, "decks/only.csv", rating=1.0, net_weights="w.json"),
    ])
    candidates = [_make("legacy", deck="legacy.csv", matrix_rating=3.0)]

    assert pick_next_deck(candidates, pool_path) == "legacy.csv"


def test_pick_next_deck_pool_retired_deck_skipped(tmp_path):
    """Only `status == "retired"` is excluded; "anchor"/"meta-anchor" stay
    eligible (see the next test), mirroring `evolution.active_cells`'s
    existing convention (JUDGMENT CALL -- the brief only names LIVE
    explicitly)."""
    paths = FactoryPaths(root=tmp_path)
    pool_path = _deck_pool_path(paths)
    pool_merge_save(pool_path, [
        _make_deck_genome(1, "decks/retired.csv", rating=9.0, status="retired"),
        _make_deck_genome(2, "decks/live.csv", rating=1.0),
    ])

    assert pick_next_deck([], pool_path) == "decks/live.csv"


def test_pick_next_deck_pool_includes_meta_anchor(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    pool_path = _deck_pool_path(paths)
    pool_merge_save(pool_path, [
        _make_deck_genome(1, "decks/anchor.csv", rating=9.0, status="meta-anchor"),
    ])

    assert pick_next_deck([], pool_path) == "decks/anchor.csv"


def test_pick_next_deck_pool_none_rating_sorts_last(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    pool_path = _deck_pool_path(paths)
    pool_merge_save(pool_path, [
        _make_deck_genome(1, "decks/unrated.csv", rating=None),
        _make_deck_genome(2, "decks/rated.csv", rating=0.1),
    ])

    assert pick_next_deck([], pool_path) == "decks/rated.csv"


def test_pick_next_deck_pool_absent_matches_legacy_call_byte_identical(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    pool_path = _deck_pool_path(paths)
    assert not pool_path.exists()
    candidates = [_make("legacy", deck="legacy.csv", matrix_rating=3.0)]

    assert pick_next_deck(candidates, pool_path) == "legacy.csv"
    assert pick_next_deck(candidates) == "legacy.csv"


# ---------- trainer_tick ----------

class _FakeTrainer:
    """Records calls; never touches torch/real training. Mirrors the shape
    of `PerDeckNetTrainer` closely enough (real `prepare_data`/`train`
    output files under `data_dir`) to exercise `trainer_tick`'s
    delete-on-success / keep-on-failure contract for real."""

    def __init__(self, deck: str, data_dir: Path, calls: list,
                fail_train: bool = False):
        self.deck = deck
        self.data_dir = Path(data_dir)
        self.name = "fake"
        self.calls = calls
        self.fail_train = fail_train

    def prepare_data(self, cycle: int) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / f"{Path(self.deck).stem}-c{cycle}.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        self.calls.append(("prepare_data", cycle))
        return path

    def train(self, data_path: Path, cycle: int) -> Path:
        self.calls.append(("train", cycle))
        if self.fail_train:
            raise RuntimeError("boom")
        weights = self.data_dir / f"weights-c{cycle}.json"
        weights.write_text("{}", encoding="utf-8")
        return weights

    def export_and_register(self, weights_path: Path, cycle: int,
                            candidates: list) -> Candidate:
        self.calls.append(("export", cycle))
        return Candidate.create(
            name=f"{Path(self.deck).stem}-searchnet", version="v0.1",
            deck=str(self.deck), agent_kind="search-net",
            agent_config={"net_weights": str(weights_path)})


def _fake_factory(paths: FactoryPaths, calls: list, fail_train: bool = False):
    def factory(deck: str) -> _FakeTrainer:
        return _FakeTrainer(deck, paths.trainer_data_dir, calls, fail_train=fail_train)
    return factory


def test_trainer_tick_paused_when_pause_file_present(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    paths.pause_file.parent.mkdir(parents=True, exist_ok=True)
    paths.pause_file.write_text("", encoding="utf-8")
    merge_save(paths.ledger, [_make("a", deck="a.csv", matrix_rating=1.0)])
    calls: list = []

    result = trainer_tick(paths, trainer_factory=_fake_factory(paths, calls))

    assert result == "paused"
    assert calls == []
    assert paths.trainer_heartbeat.exists()


def test_trainer_tick_idle_when_no_eligible_deck(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    merge_save(paths.ledger, [
        _make("a-searchnet", deck="a.csv", agent_kind="search-net", matrix_rating=2.0),
    ])
    calls: list = []

    result = trainer_tick(paths, trainer_factory=_fake_factory(paths, calls))

    assert result == "idle"
    assert calls == []
    assert paths.trainer_heartbeat.exists()


def test_trainer_tick_idle_on_virgin_ledger(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    calls: list = []

    result = trainer_tick(paths, trainer_factory=_fake_factory(paths, calls))

    assert result == "idle"
    assert paths.trainer_heartbeat.exists()


def test_trainer_tick_disk_capped_short_circuits(tmp_path, monkeypatch):
    paths = FactoryPaths(root=tmp_path)
    merge_save(paths.ledger, [_make("a", deck="a.csv", matrix_rating=1.0)])
    paths.trainer_data_dir.mkdir(parents=True, exist_ok=True)
    (paths.trainer_data_dir / "existing.jsonl").write_text("x" * 1024, encoding="utf-8")
    monkeypatch.setattr("ptcg.factory.trainer_worker.DISK_CAP_GB", 0.0)
    calls: list = []
    logs: list = []

    result = trainer_tick(paths, trainer_factory=_fake_factory(paths, calls), log=logs.append)

    assert result == "disk-capped"
    assert calls == []  # never picked a deck or spent any compute
    assert any("disk cap" in m.lower() for m in logs)  # loud log
    assert paths.trainer_heartbeat.exists()


def test_trainer_tick_trained_deletes_data_keeps_weights(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    merge_save(paths.ledger, [_make("a", deck="a.csv", matrix_rating=1.0)])
    calls: list = []

    result = trainer_tick(paths, trainer_factory=_fake_factory(paths, calls))

    assert result == "trained"
    assert ("prepare_data", 0) in calls
    assert ("train", 0) in calls
    assert ("export", 0) in calls

    data_file = paths.trainer_data_dir / "a-c0.jsonl"
    weights_file = paths.trainer_data_dir / "weights-c0.json"
    assert not data_file.exists()   # deleted on success
    assert weights_file.exists()    # weights kept

    saved = {c.id: c for c in load_ledger(paths.ledger)}
    assert "a-searchnet-v0.1" in saved


# ---------- trainer_tick: deck-pool coupling (Task 7) ----------

def test_trainer_tick_pool_present_attaches_net_weights_post_train(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    pool_path = _deck_pool_path(paths)
    genome = _make_deck_genome(1, "decks/pool-deck.csv", rating=2.0)
    pool_merge_save(pool_path, [genome])
    calls: list = []

    result = trainer_tick(paths, trainer_factory=_fake_factory(paths, calls))

    assert result == "trained"
    assert ("prepare_data", 0) in calls
    assert ("train", 0) in calls
    assert ("export", 0) in calls

    saved = {g.id: g for g in load_pool(pool_path)}
    assert saved[genome.id].net_weights is not None
    # attach never touches the legacy candidate ledger for a pool-sourced pick
    saved_candidates = {c.id: c for c in load_ledger(paths.ledger)}
    assert "pool-deck-searchnet-v0.1" in saved_candidates  # legacy export still recorded


def test_trainer_tick_pool_attach_survives_concurrent_breeding_write(tmp_path):
    """Interleaved-mutation test (mandatory per the brief and
    `.claude/rules/single-actor-worker-tests.md`): a concurrent pool write
    (a freshly-bred deck genome) lands WHILE this cycle's fake training is
    running. The trainer's post-train attach must not clobber it -- both
    the concurrent write and the trainer's own attach must survive, because
    `_attach_net_weights` fresh-loads the pool immediately before its single
    `pool_merge_save`, never holding a stale row across the training
    window."""
    paths = FactoryPaths(root=tmp_path)
    pool_path = _deck_pool_path(paths)
    genome = _make_deck_genome(1, "decks/pool-deck.csv", rating=2.0)
    pool_merge_save(pool_path, [genome])
    calls: list = []
    base_factory = _fake_factory(paths, calls)

    def wrapped_factory(deck: str):
        trainer = base_factory(deck)
        real_train = trainer.train

        def _train_with_concurrent_breeding_write(data_path, cycle):
            # Simulate a concurrent breeding tick landing mid-training-window:
            # a brand-new bred deck genome appears in the pool while this
            # trainer cycle is still "running" (train() is the slow phase).
            bred = _make_deck_genome(2, "decks/bred-child.csv")
            bred.lineage = [genome.id]
            pool_merge_save(pool_path, [bred])
            return real_train(data_path, cycle)

        trainer.train = _train_with_concurrent_breeding_write
        return trainer

    result = trainer_tick(paths, trainer_factory=wrapped_factory)

    assert result == "trained"
    saved = {g.id: g for g in load_pool(pool_path)}
    assert saved[genome.id].net_weights is not None          # trainer's attach survived
    assert "dk-" in genome.id  # sanity: genome ids use the documented prefix
    bred_ids = [g.id for g in saved.values()
               if isinstance(g, DeckGenome) and g.csv == "decks/bred-child.csv"]
    assert len(bred_ids) == 1                                 # concurrent write survived too


def test_trainer_tick_pool_absent_no_pool_attach_attempted(tmp_path):
    """Pool absent -> legacy candidate-based path only, no pool file is
    ever created as a side effect of a legacy-mode training tick."""
    paths = FactoryPaths(root=tmp_path)
    merge_save(paths.ledger, [_make("a", deck="a.csv", matrix_rating=1.0)])
    calls: list = []

    result = trainer_tick(paths, trainer_factory=_fake_factory(paths, calls))

    assert result == "trained"
    assert not _deck_pool_path(paths).exists()


def test_trainer_tick_failure_keeps_data_file(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    merge_save(paths.ledger, [_make("a", deck="a.csv", matrix_rating=1.0)])
    calls: list = []

    with pytest.raises(RuntimeError):
        trainer_tick(paths, trainer_factory=_fake_factory(paths, calls, fail_train=True))

    data_file = paths.trainer_data_dir / "a-c0.jsonl"
    assert data_file.exists()  # kept after failure - never reached the delete step


def test_trainer_tick_heartbeat_written_every_tick(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    calls: list = []

    # idle tick, virgin experiments/factory dir
    result = trainer_tick(paths, trainer_factory=_fake_factory(paths, calls))
    assert result == "idle"
    payload = json.loads(paths.trainer_heartbeat.read_text(encoding="utf-8"))
    assert set(payload) == {"ts", "detail"}
    assert isinstance(payload["ts"], str) and isinstance(payload["detail"], str)

    # trained tick
    merge_save(paths.ledger, [_make("a", deck="a.csv", matrix_rating=1.0)])
    result = trainer_tick(paths, trainer_factory=_fake_factory(paths, calls))
    assert result == "trained"
    payload = json.loads(paths.trainer_heartbeat.read_text(encoding="utf-8"))
    assert set(payload) == {"ts", "detail"}


# ---------- script: single-instance ----------

def test_run_worker_second_instance_returns_busy_without_training(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    merge_save(paths.ledger, [_make("a", deck="a.csv", matrix_rating=1.0)])
    calls: list = []

    with instance_lock(paths.trainer_worker_lock):
        result = run_worker(paths, max_ticks=1, trainer_factory=_fake_factory(paths, calls),
                            log=lambda m: None)

    assert result == "busy"
    assert calls == []  # never got to train

    # lock is released afterward -> a fresh worker can proceed normally
    result2 = run_worker(paths, max_ticks=1, trainer_factory=_fake_factory(paths, calls),
                         log=lambda m: None)
    assert result2 == "trained"
    assert calls
