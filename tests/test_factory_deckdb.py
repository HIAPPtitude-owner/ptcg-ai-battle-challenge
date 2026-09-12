"""Tests for the SQLite deck database module (tournament T1).

Covers connection discipline (WAL + busy_timeout), idempotent schema
creation, CHECK-constraint enforcement, index presence, and the
virgin-directory first-write path (Pattern VIRGIN-DIR-TEST).
"""

from __future__ import annotations

import sqlite3

import pytest

from ptcg.factory import deckdb


def test_init_db_creates_all_tables(tmp_path):
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)
    names = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"concepts", "decks", "games", "decisions", "coverage", "offspring", "baselines"} <= names


def test_wal_mode_and_busy_timeout(tmp_path):
    db = deckdb.connect(tmp_path / "t.db")
    assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 30000


def test_init_db_is_idempotent(tmp_path):
    p = tmp_path / "t.db"
    db = deckdb.connect(p)
    deckdb.init_db(db)
    deckdb.init_db(db)  # second call must not raise
    assert db.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 0


def test_status_check_constraint_rejects_bad_concept_status(tmp_path):
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO concepts(id,cores,status,builder_version) VALUES('c1','[1]','bogus',1)")


def test_concepts_status_index_exists(tmp_path):
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)
    idx = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ix_concepts_status" in idx and "ix_games_claim" in idx


def test_decks_concept_index_exists_and_is_used(tmp_path):
    """`ix_decks_concept` is load-bearing, not cosmetic: without it
    `census.schedule_screening_games`' concept_id join/filter is a full SCAN
    of `decks` (95,907 rows live) inside a BEGIN IMMEDIATE txn -- measured
    >180s vs 1.16s, i.e. past the runners' 30s busy_timeout. Asserting the
    PLAN uses the index (not just that the index exists) is what actually
    pins the behaviour."""
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)
    idx = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ix_decks_concept" in idx

    plan = " ".join(
        row[3] for row in db.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM decks WHERE concept_id = 'c1'"
        )
    )
    assert "ix_decks_concept" in plan and "SCAN decks" not in plan


def test_connect_creates_missing_parent(tmp_path):  # Pattern VIRGIN-DIR-TEST
    fresh = tmp_path / "never" / "here" / "t.db"
    assert not fresh.parent.exists()
    deckdb.init_db(deckdb.connect(fresh))
    assert fresh.exists()
