"""One-shot composition-columns migration (spec §1): idempotent ALTER +
backfill + index, single BEGIN IMMEDIATE, --db required, virgin-path
covered (never-created parent), unknown-id rows stay NULL."""
import json
import time

import pytest

from ptcg.factory import deckdb
from scripts.migrate_composition_columns import main, run_migration

# Golden anchor deck (verified against the engine DB at plan time:
# 22 energy / 12 Pokémon). Loaded from the committed CSV, not hardcoded ids.
from ptcg.factory import anchor as _anchor


def _anchor_cards() -> list[int]:
    return [
        int(line)
        for line in _anchor.ANCHOR_DECK_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _legacy_db(path):
    """Reproduce the PRE-migration production shape: decks WITHOUT the
    composition columns. Built by hand because deckdb's current DDL now
    includes them (virgin DBs are already migrated by construction)."""
    conn = deckdb.connect(path)
    conn.execute(
        "CREATE TABLE concepts(id TEXT PRIMARY KEY, cores TEXT NOT NULL, "
        "builder_version INTEGER NOT NULL DEFAULT 1, "
        "status TEXT NOT NULL DEFAULT 'untested', reason TEXT)"
    )
    conn.execute(
        "CREATE TABLE decks(id TEXT PRIMARY KEY, "
        "concept_id TEXT NOT NULL REFERENCES concepts(id), "
        "cards TEXT NOT NULL, shell_variant INTEGER NOT NULL DEFAULT 0)"
    )
    return conn


def _seed(conn, deck_id, cards):
    cid = "c-" + deck_id
    conn.execute("INSERT INTO concepts(id, cores) VALUES(?, '[\"x\"]')", (cid,))
    conn.execute(
        "INSERT INTO decks(id, concept_id, cards) VALUES(?, ?, ?)",
        (deck_id, cid, json.dumps(cards)),
    )


def test_adds_columns_backfills_and_indexes(tmp_path):
    conn = _legacy_db(tmp_path / "t.db")
    _seed(conn, "dA", _anchor_cards())
    result = run_migration(conn)
    assert sorted(result["columns_added"]) == ["energy_count", "pokemon_count"]
    assert result["backfilled"] == 1 and result["null_remaining"] == 0
    row = conn.execute(
        "SELECT energy_count, pokemon_count FROM decks WHERE id='dA'"
    ).fetchone()
    assert (row["energy_count"], row["pokemon_count"]) == (22, 12)
    idx = {r["name"] for r in conn.execute("PRAGMA index_list(decks)")}
    assert "ix_decks_concept_comp" in idx


def test_idempotent_rerun_receipt(tmp_path):
    conn = _legacy_db(tmp_path / "t.db")
    _seed(conn, "dA", _anchor_cards())
    run_migration(conn)
    second = run_migration(conn)
    assert second["columns_added"] == []
    assert second["backfilled"] == 0
    assert second["null_remaining"] == 0  # the violating/NULL-remaining=0 receipt


def test_unknown_id_rows_stay_null_and_are_counted(tmp_path):
    conn = _legacy_db(tmp_path / "t.db")
    _seed(conn, "dBad", [999_999] * 60)
    result = run_migration(conn)
    assert result["skipped_unknown_ids"] == 1
    assert result["null_remaining"] == 1  # NULL = sorts last, never jumps queue
    row = conn.execute("SELECT energy_count FROM decks WHERE id='dBad'").fetchone()
    assert row["energy_count"] is None


def test_dry_run_persists_nothing(tmp_path):
    conn = _legacy_db(tmp_path / "t.db")
    _seed(conn, "dA", _anchor_cards())
    result = run_migration(conn, dry_run=True)
    assert result["dry_run"] is True and result["backfilled"] == 1
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(decks)")}
    assert "energy_count" not in cols  # ALTER rolled back


def test_main_virgin_db_path(tmp_path):
    """Never-created nested parent (the virgin-directory class): do NOT
    rely on tmp_path pre-creation."""
    db_path = tmp_path / "deep" / "never" / "t.db"
    assert not db_path.parent.exists()
    result = main(["--db", str(db_path)])
    assert db_path.exists()
    # Virgin DBs already carry the columns from deckdb's DDL:
    assert result["columns_added"] == []
    assert result["backfilled"] == 0 and result["null_remaining"] == 0


def test_backfill_scale_receipt(tmp_path):
    """Production-scale-N timing receipt (~95k decks live; 20k here). Soft
    bound only -- this host runs loaded 24/7. Prints the measured wall time
    for the go-live sizing note."""
    conn = _legacy_db(tmp_path / "t.db")
    cards_json = json.dumps(_anchor_cards())
    conn.execute("INSERT INTO concepts(id, cores) VALUES('cS', '[\"x\"]')")
    conn.executemany(
        "INSERT INTO decks(id, concept_id, cards) VALUES(?, 'cS', ?)",
        ((f"d{i}", cards_json) for i in range(20_000)),
    )
    t0 = time.perf_counter()
    result = run_migration(conn)
    elapsed = time.perf_counter() - t0
    print(f"backfill 20k rows: {elapsed:.2f}s")
    assert result["backfilled"] == 20_000 and result["null_remaining"] == 0
    assert elapsed < 60.0
