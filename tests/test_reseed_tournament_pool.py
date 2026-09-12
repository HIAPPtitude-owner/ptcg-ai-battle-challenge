"""Tests for the one-time pool reseed migration (tournament-breeding-anchor-
pressure Task 10, spec design 5). Every test operates on a `tmp_path` DB or
an explicit `shutil.copy` of one -- this migration must NEVER touch the live
`experiments/factory/tournament.db` from a test run.
"""
from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

import pytest

import ptcg.decks.validate as validate_module
from ptcg.decks.validate import validate_deck
from ptcg.factory import anchor, census, deckdb

import scripts.reseed_tournament_pool as reseed_mod
from scripts.reseed_tournament_pool import (
    DEFAULT_SEED,
    MUTATIONS_PER_TEMPLATE,
    TEMPLATES,
    main,
    run_reseed,
)


@pytest.fixture(autouse=True)
def _min_basics_exempt_for_historical_reseed(monkeypatch):
    """Pool rule (2026-08-11, MIN_BASIC_CARDS=8): this one-time, ALREADY-
    EXECUTED migration (module docstring: "Never invoked by any worker")
    seeds and mutates the 2026-08-04 templates, which predate the rule and
    run only 3-4 basics each. Its own internal oracle (`_seed_concept`,
    hard-raises on any `validate_deck` problem) and `mutate_deck`'s legality
    gate (`deck_matrix.apply_rule`, which lazy-imports `validate_deck` fresh
    on every call) both now reject that historical content outright,
    collapsing the reachable mutation set to zero -- not just failing the
    line-129/151 test assertions below, but raising/short-circuiting BEFORE
    those assertions are ever reached (verified via a real pre-fix run).
    Patching scripts/reseed_tournament_pool.py or ptcg/factory/breeding.py
    is out of this task's declared scope (Files: Modify:
    src/ptcg/decks/validate.py only) -- so `validate_deck` is monkeypatched
    at the MODULE level, test-local via `monkeypatch` (auto-reverted after
    each test), to strip only the min-basics problem before this historical
    migration's own oracle and legality gate see it. The line-129/151
    assertions below use the file's directly-imported, UNPATCHED
    `validate_deck` plus the same exempt-filter, so they still confirm the
    seeded/mutated content is engine-legal in every OTHER respect.
    """
    real_validate_deck = validate_module.validate_deck

    def _exempt(cards):
        return [p for p in real_validate_deck(cards) if "fewer than 8 Basic" not in p]

    monkeypatch.setattr(validate_module, "validate_deck", _exempt)
    monkeypatch.setattr(reseed_mod, "validate_deck", _exempt)

# Hand-verified pool arithmetic (plan-test-arithmetic-sanity rule). The
# NOMINAL ceiling is 3 templates + 3 * 5 mutation slots = 18 decks, but the
# mutation OPERATOR caps it lower and that cap is not a seed artifact:
# `breeding.mutate_deck` draws from 4 deterministic rules (each a pure
# function of the parent deck -- the rng only permutes rule order), so a
# template can yield at most 4 distinct children ever. Measured 2026-08-03
# against the real template CSVs, unchanged at attempt budgets 8/12/20/40:
#   mega-lucario-fighting        3 legal children (attacker-up1 fails)
#   mega-starmie-water           3 legal children (attacker-up1 fails)
#   mega-starmie-water-density20 2 legal children (attacker-up1, energy-down2)
# -> 8 distinct mutations, 11 total decks, 11 * SCREENING_FLOOR(15) = 165
# census games. `EXPECTED_*` below is the REACHABLE pool, not the nominal one.
assert len(TEMPLATES) == 3
assert 3 + 3 * MUTATIONS_PER_TEMPLATE == 18  # nominal ceiling (unreachable)
EXPECTED_MUTATIONS = 8
EXPECTED_DECKS = len(TEMPLATES) + EXPECTED_MUTATIONS
assert EXPECTED_DECKS == 11
assert EXPECTED_DECKS * census.SCREENING_FLOOR == 165


def _build_production_shaped_db(path: Path) -> None:
    """A "production-shaped" DB: 2 active concepts w/ coverage+decks, 1
    played-but-unpromoted `untested` single (has a coverage row), 1 dormant
    pair (no coverage/deck row -- the T8 not-yet-activated shape), plus the
    real anchor concept/deck via `anchor.ensure_anchor_deck`.
    """
    db = deckdb.connect(path)
    deckdb.init_db(db)
    anchor.ensure_anchor_deck(db)

    def _apply(c):
        for cid in ("old-active-1", "old-active-2"):
            c.execute(
                "INSERT INTO concepts(id,cores,status) VALUES(?,?, 'active')",
                (cid, json.dumps([cid])),
            )
            c.execute(
                "INSERT INTO coverage(concept_id,games_played,distinct_opponents,rating) "
                "VALUES(?,20,3,0.55)",
                (cid,),
            )
            c.execute(
                "INSERT INTO decks(id,concept_id,cards,shell_variant) VALUES(?,?,?,0)",
                (f"{cid}-sv0", cid, json.dumps([3] * 60)),
            )
        c.execute(
            "INSERT INTO concepts(id,cores,status) VALUES(?,?, 'untested')",
            ("old-untested-played", json.dumps(["old-untested-played"])),
        )
        c.execute(
            "INSERT INTO coverage(concept_id,games_played,distinct_opponents,rating) "
            "VALUES(?,10,2,NULL)",
            ("old-untested-played",),
        )
        c.execute(
            "INSERT INTO decks(id,concept_id,cards,shell_variant) VALUES(?,?,?,0)",
            ("old-untested-played-sv0", "old-untested-played", json.dumps([3] * 60)),
        )
        # Dormant pair: no coverage row, no deck row -- never buildable/played.
        c.execute(
            "INSERT INTO concepts(id,cores,status) VALUES(?,?, 'untested')",
            ("old-dormant-pair", json.dumps(["old-active-1", "old-active-2"])),
        )

    deckdb._write(db, _apply)
    db.execute("PRAGMA wal_checkpoint(FULL)")
    db.close()


def test_reseed_on_copy_of_populated_db(tmp_path):
    original = tmp_path / "orig.db"
    _build_production_shaped_db(original)
    original_bytes = original.read_bytes()

    copy_path = tmp_path / "copy.db"
    shutil.copy(original, copy_path)

    conn = deckdb.connect(copy_path)
    result = run_reseed(conn, random.Random(DEFAULT_SEED))
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    conn.close()

    # Smoke-again-rule receipt: the ORIGINAL file's bytes are unchanged --
    # only the copy was ever touched.
    assert original.read_bytes() == original_bytes

    check = deckdb.connect(copy_path)
    rows = {r["id"]: r for r in check.execute("SELECT id, status, reason FROM concepts")}

    assert rows["old-active-1"]["status"] == "culled"
    assert rows["old-active-1"]["reason"] == reseed_mod.CULL_REASON
    assert rows["old-active-2"]["status"] == "culled"
    assert rows["old-untested-played"]["status"] == "culled"
    # Dormant pair (no coverage row) is untouched -- stays 'untested'.
    assert rows["old-dormant-pair"]["status"] == "untested"

    template_ids = [cid for cid, _ in TEMPLATES]
    for cid in template_ids:
        assert rows[cid]["status"] == "untested"
        deck = check.execute(
            "SELECT cards FROM decks WHERE concept_id=?", (cid,)
        ).fetchone()
        cards = json.loads(deck["cards"])
        assert len(cards) == 60
        # Historical pre-rule template decks (2026-08-04 reseed): 4 basics
        # each, min-basics exempt. Otherwise engine-legal.
        problems = [p for p in validate_deck(cards) if "fewer than 8 Basic" not in p]
        assert problems == []

    mutation_ids = [cid for cid in rows if cid.startswith(reseed_mod.RESEED_MUT_PREFIX)]
    # A duplicate child counts as a FAILED attempt and the slot retries, so
    # the run delivers every mutation the operator can reach (see the
    # EXPECTED_MUTATIONS receipt at the top of this module) -- not 5 slots'
    # worth of collisions silently swallowed by `INSERT OR IGNORE`.
    assert len(mutation_ids) == EXPECTED_MUTATIONS
    assert len(set(mutation_ids)) == len(mutation_ids)
    # every seeded deck is a DISTINCT card multiset (ids are content-addressed)
    seeded_decks = {
        tuple(sorted(json.loads(r["cards"])))
        for r in check.execute("SELECT cards FROM decks WHERE concept_id LIKE 'reseed-%'")
    }
    assert len(seeded_decks) == EXPECTED_DECKS
    for cid in mutation_ids:
        assert rows[cid]["status"] == "untested"
        deck = check.execute(
            "SELECT cards FROM decks WHERE concept_id=?", (cid,)
        ).fetchone()
        cards = json.loads(deck["cards"])
        assert len(cards) == 60
        # Historical pre-rule mutation decks (2026-08-04 reseed): descend
        # from 4-basic templates, min-basics exempt. Otherwise engine-legal.
        problems = [p for p in validate_deck(cards) if "fewer than 8 Basic" not in p]
        assert problems == []

    assert result["culled"] == 3
    assert result["templates_seeded"] == 3
    assert result["mutations_seeded"] == len(mutation_ids)

    check.close()


def test_reseed_drops_dead_screening_cursor_meta_key(tmp_path):
    """`meta['screening_opponent_cursor']` is a leftover from the retired
    round-robin screening design (screening now always plays the FIXED anchor
    deck). The reseed drops it; dropping is idempotent and safe when absent."""
    db_path = tmp_path / "t.db"
    _build_production_shaped_db(db_path)
    conn = deckdb.connect(db_path)
    deckdb._write(conn, lambda c: c.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('screening_opponent_cursor','7')"))
    assert conn.execute(
        "SELECT COUNT(*) FROM meta WHERE key='screening_opponent_cursor'").fetchone()[0] == 1

    run_reseed(conn, random.Random(DEFAULT_SEED))
    assert conn.execute(
        "SELECT COUNT(*) FROM meta WHERE key='screening_opponent_cursor'").fetchone()[0] == 0
    # unrelated meta rows survive
    assert conn.execute(
        "SELECT COUNT(*) FROM meta WHERE key='schema_version'").fetchone()[0] == 1
    # idempotent when already absent
    run_reseed(conn, random.Random(DEFAULT_SEED))
    assert conn.execute(
        "SELECT COUNT(*) FROM meta WHERE key='screening_opponent_cursor'").fetchone()[0] == 0
    conn.close()


def test_reseed_idempotent_same_seed(tmp_path):
    db_path = tmp_path / "t.db"
    _build_production_shaped_db(db_path)
    conn = deckdb.connect(db_path)

    run_reseed(conn, random.Random(DEFAULT_SEED))
    concept_rows_after_first = conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
    deck_rows_after_first = conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0]

    second = run_reseed(conn, random.Random(DEFAULT_SEED))

    concept_rows_after_second = conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
    deck_rows_after_second = conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0]

    assert second["culled"] == 0
    assert second["templates_seeded"] == 0
    assert second["mutations_seeded"] == 0
    assert concept_rows_after_second == concept_rows_after_first
    assert deck_rows_after_second == deck_rows_after_first

    conn.close()


def test_reseed_virgin_db_path(tmp_path):
    # Nested, never-created parent -- do NOT pre-create it (first-run-bug
    # rule: `tmp_path` itself always pre-exists, but its children don't).
    db_path = tmp_path / "deep" / "never" / "t.db"
    assert not db_path.parent.exists()

    result = main(["--db", str(db_path)])

    assert db_path.exists()
    conn = deckdb.connect(db_path)
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"concepts", "decks", "coverage", "anchor_checks"}.issubset(tables)

    concept_ids = {r[0] for r in conn.execute("SELECT id FROM concepts")}
    for cid, _ in TEMPLATES:
        assert cid in concept_ids
    assert result["templates_seeded"] == 3
    conn.close()


def test_reseed_never_touches_anchor_or_finalists(tmp_path):
    db_path = tmp_path / "t.db"
    _build_production_shaped_db(db_path)
    conn = deckdb.connect(db_path)

    before = conn.execute(
        "SELECT status, reason FROM concepts WHERE id=?", (anchor.ANCHOR_CONCEPT_ID,)
    ).fetchone()
    before_deck = conn.execute(
        "SELECT cards FROM decks WHERE id=?", (anchor.ANCHOR_DECK_ID,)
    ).fetchone()
    assert before["status"] == "finalist"

    run_reseed(conn, random.Random(DEFAULT_SEED))

    after = conn.execute(
        "SELECT status, reason FROM concepts WHERE id=?", (anchor.ANCHOR_CONCEPT_ID,)
    ).fetchone()
    after_deck = conn.execute(
        "SELECT cards FROM decks WHERE id=?", (anchor.ANCHOR_DECK_ID,)
    ).fetchone()

    assert after["status"] == "finalist"
    assert after["reason"] == before["reason"]
    assert after_deck["cards"] == before_deck["cards"]

    conn.close()


def test_culled_pool_not_reschedulable(tmp_path):
    db_path = tmp_path / "t.db"
    _build_production_shaped_db(db_path)
    conn = deckdb.connect(db_path)

    run_reseed(conn, random.Random(DEFAULT_SEED))
    enqueued = census.schedule_screening_games(conn)
    assert enqueued > 0

    rows = conn.execute(
        "SELECT deck_a_id, deck_b_id FROM games WHERE purpose='screening' AND status='pending'"
    ).fetchall()
    assert rows

    concept_by_deck = {
        r["id"]: r["concept_id"] for r in conn.execute("SELECT id, concept_id FROM decks")
    }
    for row in rows:
        assert row["deck_b_id"] == anchor.ANCHOR_DECK_ID
        subject_concept = concept_by_deck[row["deck_a_id"]]
        assert subject_concept.startswith(reseed_mod.RESEED_PREFIX)

    conn.close()


# --- legacy in-flight offspring reconcile (Pass-2 addition) -----------------
#
# The deck pool is not the only pre-slice state that survives go-live.
# Offspring rows created BEFORE the floor/netcheck gates existed carry no
# evidence rows, so the new gates either wave them through (an unfloored
# 'survivor' sails into CROWN + a 200-game anchor series, ~400 wasted games)
# or skip them forever (a 'queued_for_match' with no net_checks row is past
# `enqueue_net_check`'s `status == 'training'` guard, so its net is never
# validated -- the live v0.10.1 case).


def _seed_legacy_offspring(path: Path) -> None:
    """Every offspring shape the reconcile must classify, on top of a
    production-shaped pool."""
    _build_production_shaped_db(path)
    db = deckdb.connect(path)
    from ptcg.factory import floor, netcheck

    floor._ensure_schema(db)
    netcheck._ensure_schema(db)

    def _apply(c):
        c.execute(
            "INSERT INTO decks(id,concept_id,cards,shell_variant) VALUES(?,?,?,0)",
            ("d-legacy", "old-active-1", json.dumps([3] * 60)),
        )
        for oid, status in (
            ("legacy-survivor-unfloored", "survivor"),   # (a) -> trashed
            ("legacy-survivor-floored", "survivor"),     # floored -> UNTOUCHED
            ("legacy-queued-unchecked", "queued_for_match"),  # (b) -> training
            ("legacy-queued-checked", "queued_for_match"),    # checked -> UNTOUCHED
            ("legacy-training", "training"),             # UNTOUCHED
            ("legacy-matching", "matching"),             # UNTOUCHED
            ("legacy-confirming", "confirming"),         # UNTOUCHED
            ("legacy-trashed", "trashed"),               # UNTOUCHED
        ):
            c.execute(
                "INSERT INTO offspring(id,parent_baseline_version,search_config_json,"
                "value_net_ref,deck_id,status,created_at) VALUES(?,?,?,?,?,?,?)",
                (oid, "v0.1", "{}", "w.json", "d-legacy", status,
                 "2026-08-01T00:00:00+00:00"),
            )
        # Evidence rows that make two of them legitimate.
        c.execute(
            "INSERT INTO floor_checks(offspring_id, deck_id, games_planned, "
            "verdict, created_at) VALUES('legacy-survivor-floored','d-legacy',50,"
            "'pass','2026-08-01T00:00:00+00:00')"
        )
        c.execute(
            "INSERT INTO net_checks(offspring_id, deck_id, games_planned, "
            "verdict, created_at) VALUES('legacy-queued-checked','d-legacy',100,"
            "'adopt','2026-08-01T00:00:00+00:00')"
        )

    deckdb._write(db, _apply)
    db.execute("PRAGMA wal_checkpoint(FULL)")
    db.close()


def _statuses(conn) -> dict:
    return {
        row["id"]: row["status"]
        for row in conn.execute("SELECT id, status FROM offspring").fetchall()
    }


def test_reseed_reconciles_legacy_offspring(tmp_path):
    original = tmp_path / "orig.db"
    _seed_legacy_offspring(original)
    original_bytes = original.read_bytes()
    copy_path = tmp_path / "copy.db"
    shutil.copy(original, copy_path)

    conn = deckdb.connect(copy_path)
    result = run_reseed(conn, random.Random(DEFAULT_SEED))

    assert result["survivors_trashed"] == 1
    assert result["reset_to_training"] == 1

    status = _statuses(conn)
    # (a) unfloored survivor -- known-collapsed lineage, trashed before it can
    # spend ~400 games proving it in CROWN + the anchor series.
    assert status["legacy-survivor-unfloored"] == "trashed"
    # (b) un-netchecked queued_for_match -- back to 'training', the only status
    # `netcheck.enqueue_net_check` will actually run for.
    assert status["legacy-queued-unchecked"] == "training"
    # Everything else untouched.
    assert status["legacy-survivor-floored"] == "survivor"
    assert status["legacy-queued-checked"] == "queued_for_match"
    assert status["legacy-training"] == "training"
    assert status["legacy-matching"] == "matching"
    assert status["legacy-confirming"] == "confirming"
    assert status["legacy-trashed"] == "trashed"

    conn.execute("PRAGMA wal_checkpoint(FULL)")
    conn.close()
    assert original.read_bytes() == original_bytes  # only the copy was touched


def test_reseed_legacy_reconcile_is_idempotent(tmp_path):
    original = tmp_path / "orig.db"
    _seed_legacy_offspring(original)
    copy_path = tmp_path / "copy.db"
    shutil.copy(original, copy_path)

    conn = deckdb.connect(copy_path)
    first = run_reseed(conn, random.Random(DEFAULT_SEED))
    after_first = _statuses(conn)

    second = run_reseed(conn, random.Random(DEFAULT_SEED))

    assert (first["survivors_trashed"], first["reset_to_training"]) == (1, 1)
    # Second run matches zero rows -- both UPDATEs are guarded on the status
    # they move AWAY from plus the absent-evidence-row condition.
    assert (second["survivors_trashed"], second["reset_to_training"]) == (0, 0)
    assert _statuses(conn) == after_first
    conn.close()


def test_reseed_reconcile_noops_on_db_with_no_offspring(tmp_path):
    """The go-live-on-a-virgin-DB case: no offspring table rows at all."""
    path = tmp_path / "virgin.db"
    conn = deckdb.connect(path)
    result = run_reseed(conn, random.Random(DEFAULT_SEED))
    assert result["survivors_trashed"] == 0
    assert result["reset_to_training"] == 0
    conn.close()
