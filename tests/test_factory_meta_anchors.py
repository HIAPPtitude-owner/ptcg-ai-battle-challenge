"""Tests for meta-anchor deck injection from harvested episodes (Task 11):
`top_meta_decks` (rolling-window opponent-deck aggregation) and
`inject_meta_anchors` (deck-pool injection with dedup/cap/interleaved-write
safety). Loss-panel dashboard tests live in tests/test_factory_dashboard.py
(same convention as the evolution panel's tests)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ptcg.factory import episodes
from ptcg.factory.cycle import FactoryPaths
from ptcg.factory.genomes import DeckGenome, deck_genome_id, load_pool, save_pool


@pytest.fixture(autouse=True)
def _isolate_generated(monkeypatch, tmp_path):
    """Never write meta-anchor deck csvs into the real repo generated dir
    (mirrors tests/test_factory_evolution_tick.py's identical fixture, but
    monkeypatches episodes.GENERATED_DIR -- the name episodes.py's own
    inject_meta_anchors looks up at call time -- rather than
    evolution.GENERATED_DIR)."""
    gen = tmp_path / "generated"
    gen.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(episodes, "GENERATED_DIR", gen)


# --- fixture builders --------------------------------------------------


def _oh(deck: list) -> str:
    """Same opponent_deck_hash convention as episodes.extract_record."""
    key = ",".join(str(c) for c in sorted(deck))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _rec(day, opponent_deck, our_result, *, timeout=False, n_turns=10,
         episode_id="e"):
    return {
        "episode_id": episode_id, "day": day, "opponent_deck": list(opponent_deck),
        "our_result": our_result, "timeout": timeout, "n_turns": n_turns,
        "opponent_deck_hash": _oh(opponent_deck),
    }


def _write_extracts(path: Path, records: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n",
                    encoding="utf-8")


def _deck(cards: list, **kw) -> DeckGenome:
    return DeckGenome(id=deck_genome_id(cards), cards=list(cards), **kw)


DECK_X = [101] * 60
DECK_Y = [202] * 60
DECK_NEWEST = [301] * 60
DECK_BOUNDARY = [302] * 60
DECK_OLD = [303] * 60


# --- top_meta_decks: weighting (item a) --------------------------------


def test_top_meta_decks_weighting_ranks_deck_that_beats_us_higher(tmp_path):
    """Hand-verified fixture from the brief: deck X beats us twice
    (weight 2+2=4), deck Y seen 3x all losses-FOR-THEM (we won, weight
    1+1+1=3) -> 4 > 3, X ranks first."""
    extracts = tmp_path / "extracts.jsonl"
    _write_extracts(extracts, [
        _rec("2026-07-20", DECK_X, "loss"),   # X beat us: weight 2
        _rec("2026-07-20", DECK_X, "loss"),   # X beat us: weight 2 (total 4)
        _rec("2026-07-20", DECK_Y, "win"),    # we beat Y: weight 1
        _rec("2026-07-20", DECK_Y, "win"),    # weight 1
        _rec("2026-07-20", DECK_Y, "win"),    # weight 1 (total 3)
    ])
    result = episodes.top_meta_decks(extracts, k=2)
    assert result == [DECK_X, DECK_Y]


def test_top_meta_decks_distinct_hashes_only(tmp_path):
    extracts = tmp_path / "extracts.jsonl"
    _write_extracts(extracts, [
        _rec("2026-07-20", DECK_X, "loss"),
        _rec("2026-07-20", DECK_X, "win"),
        _rec("2026-07-20", DECK_Y, "win"),
    ])
    result = episodes.top_meta_decks(extracts, k=10)
    assert len(result) == 2  # distinct by hash, not by episode count


# --- top_meta_decks: window (item b) -----------------------------------


def test_top_meta_decks_window_excludes_old_extracts(tmp_path):
    """window_days=7 (default): newest day anchors the window. Cutoff =
    newest - 6 days = 2026-07-15 (inclusive). A record one day before the
    cutoff must be excluded; one exactly on the cutoff must be included."""
    extracts = tmp_path / "extracts.jsonl"
    _write_extracts(extracts, [
        _rec("2026-07-21", DECK_NEWEST, "win"),
        _rec("2026-07-15", DECK_BOUNDARY, "win"),   # cutoff day: included
        _rec("2026-07-14", DECK_OLD, "win"),        # one day too old: excluded
    ])
    result = episodes.top_meta_decks(extracts, window_days=7, k=10)
    assert DECK_NEWEST in result
    assert DECK_BOUNDARY in result
    assert DECK_OLD not in result
    assert len(result) == 2


# --- top_meta_decks: degenerate inputs ----------------------------------


def test_top_meta_decks_missing_file_returns_empty(tmp_path):
    assert episodes.top_meta_decks(tmp_path / "nope.jsonl") == []


def test_top_meta_decks_empty_file_returns_empty(tmp_path):
    extracts = tmp_path / "extracts.jsonl"
    extracts.write_text("", encoding="utf-8")
    assert episodes.top_meta_decks(extracts) == []


def test_top_meta_decks_malformed_lines_skipped_not_raised(tmp_path):
    extracts = tmp_path / "extracts.jsonl"
    extracts.write_text(
        "{not valid json\n" + json.dumps(_rec("2026-07-20", DECK_X, "win")) + "\n",
        encoding="utf-8")
    result = episodes.top_meta_decks(extracts)
    assert result == [DECK_X]


# --- inject_meta_anchors: dedup (item c) --------------------------------


def test_inject_meta_anchors_dedup_same_hash_no_duplicate(tmp_path):
    root = tmp_path
    paths = FactoryPaths(root)
    pool_path = root / "experiments" / "factory" / "deck_pool.json"
    save_pool(pool_path, [_deck(DECK_X, status="live")])  # already in pool

    injected = episodes.inject_meta_anchors(paths, [DECK_X])

    assert injected == 0
    pool = load_pool(pool_path)
    assert len(pool) == 1  # no duplicate row added
    assert pool[0].status == "live"  # untouched -- not promoted to meta-anchor


# --- inject_meta_anchors: cap-3 replacement (item c) --------------------


def test_inject_meta_anchors_cap_replacement_evicts_fewest_observed(tmp_path):
    root = tmp_path
    paths = FactoryPaths(root)
    pool_path = root / "experiments" / "factory" / "deck_pool.json"
    ma1 = _deck(DECK_X, status="meta-anchor", born_at="2026-07-01T00:00:00")
    ma2 = _deck(DECK_Y, status="meta-anchor", born_at="2026-07-02T00:00:00")
    ma3 = _deck(DECK_OLD, status="meta-anchor", born_at="2026-07-03T00:00:00")
    save_pool(pool_path, [ma1, ma2, ma3])

    new_deck = [909] * 60
    # DECK_OLD (ma3) never appears in `decks` -> lowest observation-rank
    # proxy (fewest recent-window observations) -> evicted to make room.
    decks = [DECK_Y, DECK_X, new_deck]

    injected = episodes.inject_meta_anchors(paths, decks)

    assert injected == 1  # only new_deck is actually new; DECK_X/DECK_Y dedup-skip
    pool = load_pool(pool_path)
    by_id = {g.id: g for g in pool}
    assert by_id[ma1.id].status == "meta-anchor"
    assert by_id[ma2.id].status == "meta-anchor"
    assert by_id[ma3.id].status == "retired"          # evicted
    new_id = deck_genome_id(new_deck)
    assert by_id[new_id].status == "meta-anchor"
    assert by_id[new_id].cards == new_deck
    assert Path(by_id[new_id].csv).exists() or (root / by_id[new_id].csv).exists()


def test_inject_meta_anchors_pool_not_full_no_eviction(tmp_path):
    root = tmp_path
    paths = FactoryPaths(root)
    pool_path = root / "experiments" / "factory" / "deck_pool.json"
    ma1 = _deck(DECK_X, status="meta-anchor")
    save_pool(pool_path, [ma1])

    new_deck = [909] * 60
    injected = episodes.inject_meta_anchors(paths, [new_deck])

    assert injected == 1
    pool = load_pool(pool_path)
    assert len(pool) == 2
    assert all(g.status == "meta-anchor" for g in pool)  # nothing evicted


# --- inject_meta_anchors: interleaved-mutation (item d, MANDATORY) ------


def test_inject_meta_anchors_survives_interleaved_matrix_worker_write(tmp_path):
    """The matrix worker's continuous tournament tick writes deck_pool.json
    concurrently (rating/games updates via its own load+save cycle). This
    reproduces exactly that: a concurrent write lands on the SAME row
    inject_meta_anchors is about to retire, landing in the window between
    inject_meta_anchors's planning read and its final save. The concurrent
    write's fields (games, rating) must survive; only `status` should flip
    to "retired" -- a stale full-row replace via pool_merge_save's by-id
    merge would otherwise clobber them (the exact bug class fixed in
    matrix_worker's merge_save, commit 6009b4d;
    .claude/rules/single-actor-worker-tests.md)."""
    root = tmp_path
    paths = FactoryPaths(root)
    pool_path = root / "experiments" / "factory" / "deck_pool.json"
    ma1 = _deck(DECK_X, status="meta-anchor", born_at="2026-07-01T00:00:00")
    ma2 = _deck(DECK_Y, status="meta-anchor", born_at="2026-07-02T00:00:00")
    ma3 = _deck(DECK_OLD, status="meta-anchor", born_at="2026-07-03T00:00:00",
               games=0, rating=0.0)
    save_pool(pool_path, [ma1, ma2, ma3])

    new_deck = [909] * 60
    decks = [DECK_Y, DECK_X, new_deck]  # DECK_OLD (ma3) unobserved -> evicted

    def _concurrent_matrix_worker_write():
        # Simulates the matrix worker's OWN independent load->mutate->save
        # cycle landing on ma3's row while inject_meta_anchors is mid-flight.
        fresh = load_pool(pool_path)
        for g in fresh:
            if g.id == ma3.id:
                g.games = 99
                g.rating = 2.5
        save_pool(pool_path, fresh)

    injected = episodes.inject_meta_anchors(
        paths, decks, _before_save=_concurrent_matrix_worker_write)

    assert injected == 1
    pool = load_pool(pool_path)
    by_id = {g.id: g for g in pool}
    retired = by_id[ma3.id]
    assert retired.status == "retired"     # our mutation applied
    assert retired.games == 99             # concurrent write survived
    assert retired.rating == 2.5           # concurrent write survived
    assert by_id[ma1.id].status == "meta-anchor"
    assert by_id[ma2.id].status == "meta-anchor"


def test_inject_meta_anchors_no_qualifying_decks_returns_zero_no_write(tmp_path):
    """All candidates already in the pool -> no write at all (not even a
    no-op save), so a concurrent writer's file is never touched."""
    root = tmp_path
    paths = FactoryPaths(root)
    pool_path = root / "experiments" / "factory" / "deck_pool.json"
    save_pool(pool_path, [_deck(DECK_X, status="live")])
    before = pool_path.read_text(encoding="utf-8")

    injected = episodes.inject_meta_anchors(paths, [DECK_X])

    assert injected == 0
    assert pool_path.read_text(encoding="utf-8") == before
