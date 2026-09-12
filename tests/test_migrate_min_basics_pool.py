"""Tests for the min-basics pool-rule migration (spec 2026-08-11,
`.superpowers/sdd/2026-08-11-min-basics-pool-rule`). Every test operates on
a `tmp_path` DB -- this migration must NEVER touch the live
`experiments/factory/tournament.db` from a test run.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from cg.api import CardType, all_card_data

from ptcg.decks.validate import MIN_BASIC_CARDS
from ptcg.factory import anchor, deckdb

from scripts.migrate_min_basics_pool import main, run_migration

_NOW = dt.datetime.now(dt.timezone.utc).isoformat()


def _card_ids() -> tuple[int, int, int]:
    """Two DISTINCT real Basic-Pokemon card ids + one real basic-energy id."""
    cards = all_card_data()
    basics = [c for c in cards if c.cardType == CardType.POKEMON and c.basic]
    a = basics[0]
    b = next(c for c in basics if c.name != a.name)
    energy = next(c for c in cards if c.cardType == CardType.BASIC_ENERGY)
    return a.cardId, b.cardId, energy.cardId


BASIC_A, BASIC_B, ENERGY = _card_ids()


def _cards(n_basics: int) -> list[int]:
    """A `decks.cards`-shaped list with exactly `n_basics` Basic Pokemon
    copies (split across 2 distinct lines when >=5, to stay under the
    4-copies-per-name cap -- legality doesn't matter to `run_migration`,
    only the basics COUNT, but staying realistic costs nothing)."""
    if n_basics <= 4:
        return [BASIC_A] * n_basics + [ENERGY] * 6
    half = n_basics // 2
    return [BASIC_A] * half + [BASIC_B] * (n_basics - half) + [ENERGY] * 6


def _db(tmp_path: Path):
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    return conn


def _seed_concept(conn, cid: str, n_basics: int, status: str, cores: str = '["x"]',
                   reason: str = "") -> None:
    conn.execute(
        "INSERT INTO concepts(id, cores, status, reason) VALUES(?,?,?,?)",
        (cid, cores, status, reason),
    )
    conn.execute(
        "INSERT INTO decks(id, concept_id, cards, shell_variant) VALUES(?,?,?,0)",
        (cid + "-d0", cid, json.dumps(_cards(n_basics))),
    )


def _seed_decision(conn, cid: str, action: str, ts: str = _NOW) -> None:
    conn.execute(
        "INSERT INTO decisions(concept_id, action, actor, timestamp) VALUES(?,?,?,?)",
        (cid, action, "brad", ts),
    )


# ---------------------------------------------------------------------------
# Cull semantics
# ---------------------------------------------------------------------------


def test_culls_active_below_min_basics_and_retains_deck_row(tmp_path):
    conn = _db(tmp_path)
    _seed_concept(conn, "c-active-4basics", 4, status="active")

    run_migration(conn, dry_run=False)

    row = conn.execute("SELECT status, reason FROM concepts WHERE id=?",
                        ("c-active-4basics",)).fetchone()
    assert row["status"] == "culled"
    assert row["reason"] == f"min-basics-rule: 4 basics < {MIN_BASIC_CARDS} (2026-08-11)"
    # deck row RETAINED -- cull is a status change, never a delete.
    assert conn.execute(
        "SELECT COUNT(*) FROM decks WHERE id='c-active-4basics-d0'"
    ).fetchone()[0] == 1


def test_culls_finalist_old_anchor_shape(tmp_path):
    conn = _db(tmp_path)
    _seed_concept(conn, "anchor-old-shape", 4, status="finalist",
                  reason="ladder-identity anchor deck (strength gate); never cull")

    run_migration(conn, dry_run=False)

    status = conn.execute(
        "SELECT status FROM concepts WHERE id='anchor-old-shape'"
    ).fetchone()["status"]
    assert status == "culled"


# ---------------------------------------------------------------------------
# Restore semantics (R2 scope: singles only, human culls respected)
# ---------------------------------------------------------------------------


def test_restores_ge8_culled_single(tmp_path):
    conn = _db(tmp_path)
    _seed_concept(conn, "c-single-restore", 8, status="culled",
                  cores='["single-a"]', reason="reseed-2026-08-03: some old reason")

    run_migration(conn, dry_run=False)

    row = conn.execute("SELECT status, reason FROM concepts WHERE id=?",
                        ("c-single-restore",)).fetchone()
    assert row["status"] == "untested"
    assert row["reason"] == ""


def test_restore_skips_concept_whose_latest_decision_is_human_remove(tmp_path):
    conn = _db(tmp_path)
    _seed_concept(conn, "c-human-removed", 8, status="culled", cores='["single-b"]')
    _seed_decision(conn, "c-human-removed", "remove")

    receipts = run_migration(conn, dry_run=False)

    status = conn.execute(
        "SELECT status FROM concepts WHERE id='c-human-removed'"
    ).fetchone()["status"]
    assert status == "culled"
    assert receipts["restore_skipped_human_cull"] == 1


def test_restore_skips_concept_whose_latest_decision_is_bulk_remove(tmp_path):
    conn = _db(tmp_path)
    _seed_concept(conn, "c-bulk-removed", 8, status="culled", cores='["single-c"]')
    _seed_decision(conn, "c-bulk-removed", "bulk-remove")

    run_migration(conn, dry_run=False)

    status = conn.execute(
        "SELECT status FROM concepts WHERE id='c-bulk-removed'"
    ).fetchone()["status"]
    assert status == "culled"


def test_restore_uses_latest_decision_not_just_any_decision(tmp_path):
    """An OLDER remove followed by a NEWER pass must still restore -- the
    lookup is `ORDER BY id DESC LIMIT 1` (latest), not "any human cull ever"."""
    conn = _db(tmp_path)
    _seed_concept(conn, "c-un-removed", 8, status="culled", cores='["single-d"]')
    _seed_decision(conn, "c-un-removed", "remove")
    _seed_decision(conn, "c-un-removed", "pass")  # supersedes the remove above

    run_migration(conn, dry_run=False)

    status = conn.execute(
        "SELECT status FROM concepts WHERE id='c-un-removed'"
    ).fetchone()["status"]
    assert status == "untested"


def test_ge8_culled_pair_stays_culled(tmp_path):
    conn = _db(tmp_path)
    _seed_concept(conn, "c-pair", 8, status="culled", cores='["pair-a","pair-b"]',
                  reason="some prior pair cull reason")

    run_migration(conn, dry_run=False)

    row = conn.execute("SELECT status, reason FROM concepts WHERE id='c-pair'").fetchone()
    assert row["status"] == "culled"
    assert row["reason"] == "some prior pair cull reason"  # untouched


# ---------------------------------------------------------------------------
# Coverage reset / offspring trash / pending-check failure
# ---------------------------------------------------------------------------


def test_coverage_reset(tmp_path):
    conn = _db(tmp_path)
    _seed_concept(conn, "c-cov", 8, status="active")
    conn.execute(
        "INSERT INTO coverage(concept_id, games_played, distinct_opponents, rating) "
        "VALUES('c-cov', 42, 3, 0.55)"
    )

    run_migration(conn, dry_run=False)

    row = conn.execute(
        "SELECT games_played, distinct_opponents, rating FROM coverage WHERE concept_id='c-cov'"
    ).fetchone()
    assert row["games_played"] == 0
    assert row["distinct_opponents"] == 0
    assert row["rating"] is None


def test_all_non_trashed_offspring_become_trashed(tmp_path):
    conn = _db(tmp_path)
    for oid, status in (("o-survivor", "survivor"), ("o-training", "training"),
                         ("o-already", "trashed")):
        conn.execute(
            "INSERT INTO offspring(id, parent_baseline_version, search_config_json, "
            "status, created_at) VALUES(?,?,?,?,?)",
            (oid, "v0.1", "{}", status, _NOW),
        )

    receipts = run_migration(conn, dry_run=False)

    statuses = {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM offspring")}
    assert statuses == {"o-survivor": "trashed", "o-training": "trashed", "o-already": "trashed"}
    assert receipts["offspring_trashed"] == 2  # the already-trashed row doesn't count


def test_pending_anchor_and_floor_checks_fail_but_resolved_left_alone(tmp_path):
    conn = _db(tmp_path)
    conn.execute(
        "INSERT INTO anchor_checks(version, deck_id, games_planned, created_at) "
        "VALUES('v0.5','some-deck-d0',200,?)", (_NOW,),
    )
    conn.execute(
        "INSERT INTO anchor_checks(version, deck_id, games_planned, verdict, resolved_at, "
        "created_at) VALUES('v0.4','other-deck-d0',200,'pass',?,?)", (_NOW, _NOW),
    )
    conn.execute(
        "INSERT INTO floor_checks(offspring_id, deck_id, games_planned, created_at) "
        "VALUES('o-1','some-deck-d0',50,?)", (_NOW,),
    )

    run_migration(conn, dry_run=False)

    pending_row = conn.execute(
        "SELECT verdict, resolved_at FROM anchor_checks WHERE version='v0.5'"
    ).fetchone()
    assert pending_row["verdict"] == "fail"
    assert pending_row["resolved_at"] is not None

    already_resolved = conn.execute(
        "SELECT verdict, resolved_at FROM anchor_checks WHERE version='v0.4'"
    ).fetchone()
    assert already_resolved["verdict"] == "pass"  # untouched

    floor_row = conn.execute(
        "SELECT verdict FROM floor_checks WHERE offspring_id='o-1'"
    ).fetchone()
    assert floor_row["verdict"] == "fail"


# ---------------------------------------------------------------------------
# Drain guard (R4)
# ---------------------------------------------------------------------------


def test_drain_guard_blocks_and_writes_nothing(tmp_path):
    conn = _db(tmp_path)
    _seed_concept(conn, "c-active-4basics", 4, status="active")
    conn.execute(
        "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, agent_version_b, "
        "purpose, status) VALUES('a','b','v0','v0','screening','pending')"
    )

    with pytest.raises(RuntimeError, match="pending/claimed"):
        run_migration(conn, dry_run=False)

    # nothing written -- the concept that WOULD have been culled is untouched.
    status = conn.execute(
        "SELECT status FROM concepts WHERE id='c-active-4basics'"
    ).fetchone()["status"]
    assert status == "active"


def test_drain_guard_counts_claimed_games_too(tmp_path):
    conn = _db(tmp_path)
    conn.execute(
        "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, agent_version_b, "
        "purpose, status) VALUES('a','b','v0','v0','screening','claimed')"
    )

    with pytest.raises(RuntimeError, match="pending/claimed"):
        run_migration(conn, dry_run=False)


# ---------------------------------------------------------------------------
# Dry-run mechanism
# ---------------------------------------------------------------------------


def test_dry_run_prints_same_counts_and_writes_nothing(tmp_path):
    conn = _db(tmp_path)
    _seed_concept(conn, "c-active-4basics", 4, status="active")
    _seed_concept(conn, "c-single-restore", 8, status="culled", cores='["single-a"]')
    conn.execute(
        "INSERT INTO coverage(concept_id, games_played, distinct_opponents, rating) "
        "VALUES('c-single-restore', 5, 1, 0.5)"
    )

    dry_receipts = run_migration(conn, dry_run=True)

    # nothing written: both concepts still in their pre-migration state.
    statuses = {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM concepts")}
    assert statuses == {"c-active-4basics": "active", "c-single-restore": "culled"}
    cov = conn.execute(
        "SELECT games_played FROM coverage WHERE concept_id='c-single-restore'"
    ).fetchone()
    assert cov["games_played"] == 5  # unchanged
    assert "anchor_installed" not in dry_receipts
    assert conn.execute("SELECT COUNT(*) FROM concepts WHERE id LIKE 'anchor-%'").fetchone()[0] == 0

    real_receipts = run_migration(conn, dry_run=False)
    for key in ("culled", "restored", "restore_skipped_human_cull", "coverage_reset",
                "offspring_trashed", "anchor_checks_failed", "floor_checks_failed"):
        assert dry_receipts[key] == real_receipts[key], key


def test_dry_run_installs_no_anchor_row(tmp_path):
    conn = _db(tmp_path)
    run_migration(conn, dry_run=True)
    assert conn.execute(
        "SELECT COUNT(*) FROM concepts WHERE id=?", (anchor.ANCHOR_CONCEPT_ID,)
    ).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------


def test_second_run_is_a_noop(tmp_path):
    conn = _db(tmp_path)
    _seed_concept(conn, "c-active-4basics", 4, status="active")
    _seed_concept(conn, "c-single-restore", 8, status="culled", cores='["single-a"]')
    conn.execute(
        "INSERT INTO coverage(concept_id, games_played, distinct_opponents, rating) "
        "VALUES('c-single-restore', 5, 1, 0.5)"
    )
    conn.execute(
        "INSERT INTO offspring(id, parent_baseline_version, search_config_json, status, "
        "created_at) VALUES('o-1','v0.1','{}','survivor',?)", (_NOW,),
    )
    conn.execute(
        "INSERT INTO anchor_checks(version, deck_id, games_planned, created_at) "
        "VALUES('v0.5','some-deck-d0',200,?)", (_NOW,),
    )

    first = run_migration(conn, dry_run=False)
    assert first["culled"] == 1
    assert first["restored"] == 1

    snapshot_before = {
        "concepts": [dict(r) for r in conn.execute("SELECT * FROM concepts ORDER BY id")],
        "coverage": [dict(r) for r in conn.execute("SELECT * FROM coverage ORDER BY concept_id")],
        "offspring": [dict(r) for r in conn.execute("SELECT * FROM offspring ORDER BY id")],
        "anchor_checks": [dict(r) for r in conn.execute("SELECT * FROM anchor_checks ORDER BY version")],
    }

    second = run_migration(conn, dry_run=False)

    assert second["culled"] == 0
    assert second["restored"] == 0
    assert second["offspring_trashed"] == 0
    assert second["anchor_checks_failed"] == 0
    assert second["floor_checks_failed"] == 0

    snapshot_after = {
        "concepts": [dict(r) for r in conn.execute("SELECT * FROM concepts ORDER BY id")],
        "coverage": [dict(r) for r in conn.execute("SELECT * FROM coverage ORDER BY concept_id")],
        "offspring": [dict(r) for r in conn.execute("SELECT * FROM offspring ORDER BY id")],
        "anchor_checks": [dict(r) for r in conn.execute("SELECT * FROM anchor_checks ORDER BY version")],
    }
    assert snapshot_before == snapshot_after


# ---------------------------------------------------------------------------
# Anchor installation
# ---------------------------------------------------------------------------


def test_real_run_installs_new_anchor_as_finalist(tmp_path):
    conn = _db(tmp_path)
    receipts = run_migration(conn, dry_run=False)
    assert receipts["anchor_installed"] == anchor.ANCHOR_DECK_ID
    row = conn.execute(
        "SELECT status, reason FROM concepts WHERE id=?", (anchor.ANCHOR_CONCEPT_ID,)
    ).fetchone()
    assert row["status"] == "finalist"
    assert "min-basics anchor deck" in row["reason"]


# ---------------------------------------------------------------------------
# Anchor-source validation runs FIRST, before any transaction (fix round 1,
# Pass-1 review Important finding: a corrupted/short anchor CSV must not be
# caught only post-COMMIT by `ensure_anchor_deck` -- it must block the WHOLE
# run, dry-run included, before any DB write.
# ---------------------------------------------------------------------------


def test_short_anchor_csv_blocks_both_modes_with_zero_mutation(tmp_path, monkeypatch):
    bad_csv = tmp_path / "bad-anchor.csv"
    bad_csv.write_text("\n".join(str(ENERGY) for _ in range(5)), encoding="utf-8")
    monkeypatch.setattr(anchor, "ANCHOR_DECK_PATH", bad_csv)

    for label, dry_run in (("dry", True), ("real", False)):
        conn = _db(tmp_path / label)
        _seed_concept(conn, "c-would-be-culled", 4, status="active")
        with pytest.raises(RuntimeError, match="has 5 cards"):
            run_migration(conn, dry_run=dry_run)
        # zero mutation: the pre-pass never even ran, so the seeded
        # would-be-culled concept is untouched.
        status = conn.execute(
            "SELECT status FROM concepts WHERE id='c-would-be-culled'"
        ).fetchone()["status"]
        assert status == "active"
        conn.close()


def test_anchor_csv_failing_validate_deck_blocks_both_modes_with_zero_mutation(
    tmp_path, monkeypatch
):
    bad_csv = tmp_path / "no-basics-anchor.csv"
    bad_csv.write_text("\n".join(str(ENERGY) for _ in range(60)), encoding="utf-8")
    monkeypatch.setattr(anchor, "ANCHOR_DECK_PATH", bad_csv)

    for label, dry_run in (("dry", True), ("real", False)):
        conn = _db(tmp_path / label)
        _seed_concept(conn, "c-would-be-culled", 4, status="active")
        with pytest.raises(RuntimeError, match="fails validate_deck"):
            run_migration(conn, dry_run=dry_run)
        status = conn.execute(
            "SELECT status FROM concepts WHERE id='c-would-be-culled'"
        ).fetchone()["status"]
        assert status == "active"
        conn.close()


# ---------------------------------------------------------------------------
# CLI / virgin-directory
# ---------------------------------------------------------------------------


def test_main_virgin_db_path_installs_schema_and_anchor(tmp_path):
    db_path = tmp_path / "deep" / "never" / "t.db"
    assert not db_path.parent.exists()

    result = main(["--db", str(db_path)])

    assert db_path.exists()
    conn = deckdb.connect(db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"concepts", "decks", "coverage", "anchor_checks", "games"}.issubset(tables)
    assert result["anchor_installed"] == anchor.ANCHOR_DECK_ID
    conn.close()


def test_main_dry_run_flag_writes_nothing(tmp_path):
    db_path = tmp_path / "t.db"
    result = main(["--db", str(db_path), "--dry-run"])
    assert "anchor_installed" not in result
    conn = deckdb.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 0
    conn.close()


def test_db_flag_is_required():
    with pytest.raises(SystemExit):
        main(["--dry-run"])
