"""Stamping receipts for the materialized composition columns (spec §1):
every live deck-insert site writes energy_count/pokemon_count at birth.

Live insert sites (verified 2026-08-13): census._insert_deck_row (both
census sites — seed_census census.py:271 and activate_pair_concepts
census.py:428 — route through it), anchor.ensure_anchor_deck
(anchor.py:115), and scripts/reseed_tournament_pool.py:129 (one-shot,
swept by tests/test_reseed_tournament_pool.py).
"""
from __future__ import annotations

from ptcg.factory import anchor, census, deckdb
from ptcg.factory.builder import build_deck, composition_counts, enumerate_concepts


def _conn(tmp_path):
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    return conn


def test_virgin_ddl_has_composition_columns_and_index(tmp_path):
    conn = _conn(tmp_path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(decks)")}
    assert {"energy_count", "pokemon_count"} <= cols
    idx = {r["name"] for r in conn.execute("PRAGMA index_list(decks)")}
    assert "ix_decks_concept_comp" in idx


def test_ensure_anchor_deck_stamps_counts(tmp_path):
    conn = _conn(tmp_path)
    anchor.ensure_anchor_deck(conn)
    row = conn.execute(
        "SELECT energy_count, pokemon_count FROM decks WHERE id=?",
        (anchor.ANCHOR_DECK_ID,),
    ).fetchone()
    # Golden, verified against the engine DB at plan time: anchor-min8.csv
    # = 22 energy / 12 Pokémon.
    assert (row["energy_count"], row["pokemon_count"]) == (22, 12)


def test_census_insert_deck_row_stamps_counts(tmp_path):
    conn = _conn(tmp_path)
    concept = enumerate_concepts()[0]
    result = build_deck(concept)
    assert result.cards is not None, "first enumerated concept must be buildable"
    conn.execute("INSERT INTO concepts(id, cores) VALUES('c-stamp', '[\"x\"]')")
    census._insert_deck_row(conn, "d-stamp", "c-stamp", result.cards)
    row = conn.execute(
        "SELECT energy_count, pokemon_count FROM decks WHERE id='d-stamp'"
    ).fetchone()
    assert row["energy_count"] is not None and row["pokemon_count"] is not None
    assert (row["energy_count"], row["pokemon_count"]) == composition_counts(result.cards)
