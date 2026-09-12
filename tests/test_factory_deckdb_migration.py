"""Migration tests for the decisions-table v2 rebuild (ui-remove-any-deck T1)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ptcg.factory import deckdb
from tests.fixtures.race import race_two

_OLD_DECISIONS_DDL = """
    CREATE TABLE decisions(
      id INTEGER PRIMARY KEY AUTOINCREMENT, deck_id TEXT NOT NULL,
      action TEXT NOT NULL CHECK(action IN ('remove','pass')),
      actor TEXT NOT NULL, timestamp TEXT NOT NULL)
"""


def _old_schema_db(tmp_path: Path) -> sqlite3.Connection:
    """A DB whose decisions table has the PRE-migration shape, with one row."""
    conn = deckdb.connect(tmp_path / "t.db")
    conn.execute(_OLD_DECISIONS_DDL)
    conn.execute(
        "INSERT INTO decisions(deck_id, action, actor, timestamp) "
        "VALUES('d0','remove','brad','2026-08-01T00:00:00+00:00')"
    )
    return conn


def test_migrate_rebuilds_old_table_preserving_rows(tmp_path):
    conn = _old_schema_db(tmp_path)
    deckdb.migrate_decisions(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(decisions)")}
    assert {"deck_id", "concept_id", "action", "actor", "timestamp", "prior_status"} <= cols
    row = conn.execute("SELECT deck_id, action, actor FROM decisions").fetchone()
    assert (row["deck_id"], row["action"], row["actor"]) == ("d0", "remove", "brad")
    # new action values and NULL deck_id are now legal
    conn.execute(
        "INSERT INTO decisions(concept_id, action, actor, timestamp, prior_status) "
        "VALUES('c1','restore','brad','2026-08-05T00:00:00+00:00','untested')"
    )
    # old-shape insert (strict-superset requirement) still legal
    conn.execute(
        "INSERT INTO decisions(deck_id, action, actor, timestamp) "
        "VALUES('d1','pass','brad','2026-08-05T00:00:00+00:00')"
    )


def test_migrate_is_idempotent(tmp_path):
    conn = _old_schema_db(tmp_path)
    deckdb.migrate_decisions(conn)
    before = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    deckdb.migrate_decisions(conn)  # second call: no-op, no data change
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == before


def test_init_db_fresh_creates_v2_and_runs_migration(tmp_path):
    conn = deckdb.connect(tmp_path / "fresh.db")
    deckdb.init_db(conn)
    conn.execute(
        "INSERT INTO decisions(concept_id, action, actor, timestamp, prior_status) "
        "VALUES('c1','bulk-remove','brad','2026-08-05T00:00:00+00:00','untested')"
    )


def test_init_db_on_old_db_migrates(tmp_path):
    conn = _old_schema_db(tmp_path)
    deckdb.init_db(conn)  # init_db calls migrate_decisions at the end
    conn.execute(
        "INSERT INTO decisions(concept_id, action, actor, timestamp, prior_status) "
        "VALUES('c1','bulk-restore','brad','2026-08-05T00:00:00+00:00','culled')"
    )


def test_decisions_ddl_index_resolves_to_table_not_index_statement():
    """FIX FINDING 2 (Pass-2, 2026-08-05): `_DECISIONS_DDL_INDEX` must
    resolve to the `decisions` TABLE statement specifically, not merely to
    any statement CONTAINING the substring "decisions(" -- the new
    `ix_decisions_concept` index statement (`...ON decisions(concept_id)`)
    also contains that substring. A bare substring matcher only "works"
    today by accident of list order (the table entry comes first); this
    pins the resolution against a future reorder of `_DDL_STATEMENTS`."""
    stmt = deckdb._DDL_STATEMENTS[deckdb._DECISIONS_DDL_INDEX]
    assert stmt.lstrip().startswith("CREATE TABLE IF NOT EXISTS decisions(")
    # the new index statement must actually exist in the DDL list somewhere
    assert any(
        "CREATE INDEX" in s.upper() and "ix_decisions_concept" in s
        for s in deckdb._DDL_STATEMENTS
    )


def test_init_db_on_old_db_creates_decisions_index(tmp_path):
    """FIX FINDING 1 (Pass-2, 2026-08-05), rebuild-path coverage: an old
    (v1) DB that migrates via init_db()->migrate_decisions() must end up
    with `ix_decisions_concept` too -- the rebuild recreates `decisions`
    from scratch (any index on the old table is gone with it), so the
    rebuild's own `_apply` must recreate the index in the SAME transaction
    rather than relying on some LATER init_db() call to backfill it."""
    conn = _old_schema_db(tmp_path)
    deckdb.init_db(conn)
    idx = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ix_decisions_concept" in idx


def test_migrate_decisions_toctou_guard_survives_concurrent_callers(tmp_path):
    """FIX FINDING 1 (Critical, TOCTOU): two callers racing migrate_decisions()
    on the same v1 table must produce exactly ONE rebuild. A return-value
    assertion is tautological here (migrate_decisions returns None either
    way -- see tests/fixtures/race.py's docstring on the tautological-
    concurrency-receipt lesson), so this discriminates on the MUTATING
    STATEMENT itself via a trace-callback statement counter: without the
    inner re-check under BEGIN IMMEDIATE, the loser unconditionally
    re-executes `ALTER TABLE decisions RENAME` on the winner's already-v2
    table, which is exactly the data-loss path (its 5-column copy SELECT
    silently drops any concept_id/prior_status values written in the gap).
    """
    db_path = tmp_path / "race.db"
    setup_conn = deckdb.connect(db_path)
    setup_conn.execute(_OLD_DECISIONS_DDL)
    setup_conn.execute(
        "INSERT INTO decisions(deck_id, action, actor, timestamp) "
        "VALUES('d0','remove','brad','2026-08-01T00:00:00+00:00')"
    )
    setup_conn.close()

    _results, alter_count = race_two(
        db_path,
        lambda c: deckdb.migrate_decisions(c),
        lambda sql: sql.startswith("ALTER TABLE DECISIONS RENAME"),
    )
    assert alter_count == 1, (
        f"expected exactly one rebuild (ALTER TABLE RENAME fired once), "
        f"got {alter_count} -- the loser re-ran the rebuild on the winner's "
        f"already-migrated table"
    )

    conn = deckdb.connect(db_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(decisions)")}
    assert {"deck_id", "concept_id", "action", "actor", "timestamp", "prior_status"} <= cols
    row = conn.execute("SELECT deck_id, action FROM decisions").fetchone()
    assert (row["deck_id"], row["action"]) == ("d0", "remove")


def test_migrate_preserves_autoincrement_sequence_after_deletes(tmp_path):
    """FIX FINDING 2 (Important, AUTOINCREMENT continuity): the copy step
    only transfers EXISTING rows, so if the highest id ever assigned in the
    v1 table (tracked by sqlite_sequence, not by any surviving row) exceeds
    the highest COPIED id -- e.g. because the highest-id rows were deleted
    before migration -- a post-migration insert must not reuse that
    previously-used, now-deleted id.
    """
    conn = deckdb.connect(tmp_path / "seq.db")
    conn.execute(_OLD_DECISIONS_DDL)
    for i in range(5):
        conn.execute(
            "INSERT INTO decisions(deck_id, action, actor, timestamp) "
            "VALUES(?, 'remove', 'brad', ?)",
            (f"d{i}", f"2026-08-0{i + 1}T00:00:00+00:00"),
        )
    conn.execute("DELETE FROM decisions WHERE id IN (4, 5)")

    deckdb.migrate_decisions(conn)

    conn.execute(
        "INSERT INTO decisions(deck_id, action, actor, timestamp) "
        "VALUES('d5','pass','brad','2026-08-05T00:00:00+00:00')"
    )
    new_id = conn.execute(
        "SELECT id FROM decisions WHERE deck_id='d5'"
    ).fetchone()[0]
    assert new_id > 5, (
        f"expected a fresh id > 5 (no reuse of deleted id 4), got {new_id}"
    )
