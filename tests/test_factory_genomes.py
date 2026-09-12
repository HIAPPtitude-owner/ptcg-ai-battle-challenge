"""Tests for genome models + pool ledgers (evolutionary agent population)."""
from __future__ import annotations

import json

import pytest

from ptcg.factory.genomes import (
    AgentGenome,
    DeckGenome,
    agent_genome_id,
    cell_id,
    deck_genome_id,
    load_pool,
    pool_merge_save,
    save_pool,
    split_cell_id,
)


def _agent(config: dict | None = None, **kw) -> AgentGenome:
    config = config or {"search_budget_ms": 200, "rollout_depth": 12}
    return AgentGenome(id=agent_genome_id(config), kind="search", config=config, **kw)


def _deck(cards: list | None = None, **kw) -> DeckGenome:
    cards = cards or [3] * 60
    return DeckGenome(id=deck_genome_id(cards), cards=cards, **kw)


def test_pool_round_trip_agent_and_deck_genomes(tmp_path):
    path = tmp_path / "pool.json"
    agent = _agent(notes="unicode ok: ⚠")
    agent.rating = 12.5
    agent.games = 4
    deck = _deck(csv="src/ptcg/decks/candidates/mega-lucario-fighting.csv")
    deck.rating = -3.2
    save_pool(path, [agent, deck])
    assert not path.with_suffix(".json.tmp").exists()  # temp+rename cleaned up

    loaded = load_pool(path)

    assert len(loaded) == 2
    by_id = {g.id: g for g in loaded}
    got_agent = by_id[agent.id]
    got_deck = by_id[deck.id]
    assert isinstance(got_agent, AgentGenome)
    assert isinstance(got_deck, DeckGenome)
    assert got_agent.config == agent.config
    assert got_agent.rating == 12.5
    assert got_agent.games == 4
    assert got_agent.notes.endswith("⚠")
    assert got_deck.cards == deck.cards
    assert got_deck.rating == -3.2
    assert got_deck.csv == deck.csv


def test_load_pool_missing_file_returns_empty_list(tmp_path):
    assert load_pool(tmp_path / "nope.json") == []


def test_load_pool_back_compat_unknown_key_dropped_and_missing_rating_defaulted(tmp_path):
    doc = {"version": 1, "genomes": [{
        "id": "ag-deadbeef00", "kind": "search",
        "config": {"search_budget_ms": 200}, "status": "live",
        "zzz": "some-future-field-we-dont-know-about"}]}
    path = tmp_path / "pool.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    loaded = load_pool(path)

    assert len(loaded) == 1
    assert loaded[0].rating is None  # defaulted, not KeyError
    assert loaded[0].config == {"search_budget_ms": 200}
    assert not hasattr(loaded[0], "zzz")  # unknown key dropped, not stored


def test_save_pool_creates_missing_parent_directory_virgin_dir(tmp_path):
    """Virgin-dir rule: the parent chain does NOT exist before this call and
    must not be pre-created by the test — save_pool must mkdir it itself."""
    path = tmp_path / "never" / "made" / "agent_pool.json"
    assert not path.parent.exists()

    save_pool(path, [_agent()])

    assert path.exists()
    loaded = load_pool(path)
    assert len(loaded) == 1


def test_cell_id_round_trip_and_split_raises_on_malformed_input():
    cid = cell_id("ag-abc123", "dk-def456")
    assert cid == "cell~ag-abc123~dk-def456"
    assert split_cell_id(cid) == ("ag-abc123", "dk-def456")
    with pytest.raises(ValueError):
        split_cell_id("garbage")


def test_agent_genome_id_is_order_insensitive_over_dict_key_insertion():
    config_a = {"search_budget_ms": 200, "rollout_depth": 12}
    config_b = {"rollout_depth": 12, "search_budget_ms": 200}
    assert agent_genome_id(config_a) == agent_genome_id(config_b)


def test_pool_merge_save_preserves_interleaved_concurrent_write(tmp_path):
    """Mirrors test_worker_tick_reloads_candidates_under_lock_survives_concurrent_submission
    (tests/test_factory_matrix_worker.py) for the genome pool: pool_merge_save
    must fresh-load under the lock so a concurrent actor's write landing
    during our own slow in-memory mutation isn't clobbered by a stale-
    snapshot full-row replace.

    Sequence: load pool, mutate genome A in memory (the slow operation);
    meanwhile a concurrent actor does its own fresh load + save_pool call
    that appends genome B to disk; then pool_merge_save(path, [A]) must
    leave BOTH the updated A and the concurrently-written B on disk.
    """
    path = tmp_path / "pool.json"
    agent_a = _agent(config={"search_budget_ms": 200})
    save_pool(path, [agent_a])

    # Load pool (our "stale snapshot" going forward), then start a slow
    # in-memory mutation of A.
    loaded = load_pool(path)
    a = loaded[0]
    a.games += 1

    # Meanwhile, a concurrent actor appends genome B via its OWN fresh
    # load + save_pool call — simulating a second process writing to the
    # same pool file while we're still mutating A in memory.
    agent_b = _agent(config={"search_budget_ms": 400})
    concurrent_view = load_pool(path)
    save_pool(path, concurrent_view + [agent_b])

    merged = pool_merge_save(path, [a])

    merged_by_id = {g.id: g for g in merged}
    assert merged_by_id[a.id].games == 1  # our update landed
    assert agent_b.id in merged_by_id     # B's concurrent write survived

    on_disk = {g.id: g for g in load_pool(path)}
    assert on_disk[a.id].games == 1
    assert agent_b.id in on_disk
