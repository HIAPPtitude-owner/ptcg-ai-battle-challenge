"""Tests for Founding Census seeding (tournament T3).

Covers: single-core concepts + their shell_variant=0 decks land in the DB,
seeding is idempotent (content-addressed ids + INSERT OR IGNORE), and the
virgin-directory first-write path (Pattern VIRGIN-DIR-TEST).
"""

from __future__ import annotations

import threading

from ptcg.factory import census, deckdb
from ptcg.factory.builder import Concept


def _db(tmp):
    d = deckdb.connect(tmp / "t.db")
    deckdb.init_db(d)
    return d


def test_seed_census_inserts_concepts_and_decks(tmp_path):
    db = _db(tmp_path)
    res = census.seed_census(db)
    n_concepts = db.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
    n_decks = db.execute("SELECT COUNT(*) FROM decks").fetchone()[0]
    assert n_concepts == res["concepts"] and n_concepts >= 800
    assert n_decks == res["buildable"] >= 1
    # unbuildable concepts are recorded, never dropped
    assert res["unbuildable"] == n_concepts - res["buildable"]


def test_seed_census_is_idempotent(tmp_path):
    db = _db(tmp_path)
    a = census.seed_census(db)
    b = census.seed_census(db)
    assert a["concepts"] == b["concepts"]
    assert db.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == a["concepts"]
    assert db.execute("SELECT COUNT(*) FROM decks").fetchone()[0] == a["buildable"]


def test_seed_census_virgin_dir(tmp_path):  # Pattern VIRGIN-DIR-TEST
    fresh = tmp_path / "no" / "dir" / "t.db"
    assert not fresh.parent.exists()
    db = deckdb.connect(fresh)
    deckdb.init_db(db)
    assert census.seed_census(db)["concepts"] >= 800


def test_deck_id_is_deterministic_and_shell_variant_scoped():
    assert census.deck_id("c-abc123", 0) == census.deck_id("c-abc123", 0)
    assert census.deck_id("c-abc123", 0) != census.deck_id("c-abc123", 1)


def test_seed_census_unbuildable_concept_recorded_with_reason_never_dropped(tmp_path, monkeypatch):
    """A concept the builder can't build must still land in `concepts` with
    status='unbuildable' + a reason, never silently dropped — and must NOT
    get a `decks`/`coverage` row."""
    from ptcg.factory import builder as builder_module
    from ptcg.factory.builder import BuildResult, Concept

    real_build_deck = builder_module.build_deck

    def _fake_build_deck(concept):
        if concept.cores == ("Pikachu",):
            return BuildResult(cards=None, unbuildable_reason="forced-unbuildable-for-test")
        return real_build_deck(concept)

    monkeypatch.setattr(census, "enumerate_concepts", lambda: [Concept(cores=("Pikachu",))])
    monkeypatch.setattr(census, "build_deck", _fake_build_deck)

    db = _db(tmp_path)
    res = census.seed_census(db)
    assert res == {"concepts": 1, "buildable": 0, "unbuildable": 1}
    row = db.execute("SELECT status, reason FROM concepts").fetchone()
    assert row["status"] == "unbuildable"
    assert row["reason"] == "forced-unbuildable-for-test"
    assert db.execute("SELECT COUNT(*) FROM decks").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM coverage").fetchone()[0] == 0


# --- promote_proven_singles (T10 plan-gap fix) ------------------------------
#
# Confirmed gap: no task in the plan (T1-T21) ever promotes a single-core
# concept out of 'untested' -- only pair rows reach 'active', and only
# inside activate_pair_concepts. Without this writer, activate_pair_concepts's
# own _ACTIVE_SINGLES_QUERY (status='active' AND rating IS NOT NULL) matches
# zero rows forever, making pair activation a production no-op. Reproduced
# executably (RED, base commit 9a3f330) before this fix: seeding 3 real
# singles, forcing coverage to games_played=15/rating=1.0 for all three, then
# calling activate_pair_concepts(db, max_new=10) returned n_activated == 0
# even though every single had "proven out" by every other measure.

_PROMOTE_CORES = ("Abomasnow", "Abra", "Absol")  # same verified-buildable
# names as tests/test_factory_census_pairs.py's `_FIVE_CORES` prefix.


def _seed_promote_cores(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(
        census, "enumerate_concepts", lambda: [Concept(cores=(n,)) for n in _PROMOTE_CORES]
    )
    census.seed_census(db)
    return db


def test_promote_proven_singles_promotes_eligible_and_skips_others(tmp_path, monkeypatch):
    db = _seed_promote_cores(tmp_path, monkeypatch)
    cid_ready, cid_under_floor, cid_no_rating = (
        census.concept_id((n,)) for n in _PROMOTE_CORES
    )

    def _apply(c):
        c.execute(
            "UPDATE coverage SET games_played=15, rating=1.0 WHERE concept_id=?", (cid_ready,)
        )
        c.execute(
            "UPDATE coverage SET games_played=5, rating=1.0 WHERE concept_id=?",
            (cid_under_floor,),
        )
        c.execute(
            "UPDATE coverage SET games_played=15, rating=NULL WHERE concept_id=?",
            (cid_no_rating,),
        )

    deckdb._write(db, _apply)

    n = census.promote_proven_singles(db, floor=15)
    assert n == 1

    statuses = {
        row["id"]: row["status"] for row in db.execute("SELECT id, status FROM concepts").fetchall()
    }
    assert statuses[cid_ready] == "active"
    assert statuses[cid_under_floor] == "untested"  # under floor -- not eligible
    assert statuses[cid_no_rating] == "untested"  # no decisive rating yet -- not eligible


def test_promote_proven_singles_idempotent(tmp_path, monkeypatch):
    db = _seed_promote_cores(tmp_path, monkeypatch)
    cid = census.concept_id((_PROMOTE_CORES[0],))
    deckdb._write(
        db,
        lambda c: c.execute(
            "UPDATE coverage SET games_played=15, rating=1.0 WHERE concept_id=?", (cid,)
        ),
    )
    assert census.promote_proven_singles(db, floor=15) == 1
    assert census.promote_proven_singles(db, floor=15) == 0  # already active -- no re-promote
    assert db.execute("SELECT status FROM concepts WHERE id=?", (cid,)).fetchone()[0] == "active"


def test_promote_proven_singles_survives_concurrent_calls(tmp_path, monkeypatch):
    # INTERLEAVED (Pattern INTERLEAVED-TEST / .claude/rules/single-actor-worker-tests.md):
    # two callers racing promotion of the SAME concept must not double-count
    # -- the guarded UPDATE (WHERE status='untested') means the loser's write
    # affects zero rows.
    db = _seed_promote_cores(tmp_path, monkeypatch)
    cid = census.concept_id((_PROMOTE_CORES[0],))
    deckdb._write(
        db,
        lambda c: c.execute(
            "UPDATE coverage SET games_played=15, rating=1.0 WHERE concept_id=?", (cid,)
        ),
    )
    db_path = tmp_path / "t.db"
    barrier = threading.Barrier(2)
    results: dict[str, int] = {}

    def _call(name):
        conn = deckdb.connect(db_path)
        barrier.wait()
        results[name] = census.promote_proven_singles(conn, floor=15)

    t1 = threading.Thread(target=_call, args=("t1",))
    t2 = threading.Thread(target=_call, args=("t2",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert results["t1"] + results["t2"] == 1  # exactly one real promotion across both racers
    assert db.execute("SELECT status FROM concepts WHERE id=?", (cid,)).fetchone()[0] == "active"


def test_culled_concept_is_a_one_way_door(tmp_path, monkeypatch):
    """The reseed migration's ONE-WAY DOOR, pinned executably.

    `scripts/reseed_tournament_pool.py` culls the collapsed lineage by setting
    `concepts.status='culled'`, and its docstring claims that is permanent:
    "culled concepts are never reactivated". Nothing tested that claim, yet it
    is the whole basis for treating the post-reseed pool as the frozen search
    space for the rest of the competition window. If either writer that can
    reach `'active'` were ever relaxed to match `'culled'` rows, the reseed
    would silently un-cull the exact decks the D1-D3 diagnostics condemned.

    The dangerous shape specifically is a culled concept that still CARRIES A
    COVERAGE ROW (games_played + a decisive rating) -- reseed culls by
    `id IN (SELECT concept_id FROM coverage)`, so every culled row has one by
    construction, and coverage is what both promotion paths key on. Such a row
    is eligible on EVERY axis except `status`.
    """
    db = _seed_promote_cores(tmp_path, monkeypatch)
    cid_culled, cid_live, _ = (census.concept_id((n,)) for n in _PROMOTE_CORES)

    def _apply(c):
        # Culled, but fully "proven" by coverage -- the only thing holding it
        # back is its status.
        c.execute(
            "UPDATE coverage SET games_played=999, rating=99.0 WHERE concept_id=?",
            (cid_culled,),
        )
        c.execute("UPDATE concepts SET status='culled' WHERE id=?", (cid_culled,))
        # A live sibling that IS promotable, so the test proves the writers ran
        # and did work -- a green result can't be "nothing happened at all".
        c.execute(
            "UPDATE coverage SET games_played=15, rating=1.0 WHERE concept_id=?", (cid_live,)
        )

    deckdb._write(db, _apply)

    promoted = census.promote_proven_singles(db, floor=15)
    activated = census.activate_pair_concepts(db, max_new=10)

    assert promoted == 1  # the live sibling only
    status = {
        row["id"]: row["status"]
        for row in db.execute("SELECT id, status FROM concepts").fetchall()
    }
    assert status[cid_live] == "active"  # the writers really did run
    assert status[cid_culled] == "culled"  # ...and the door stayed shut
    assert activated == 0  # a culled core can never re-enter via a pair either

    # Repeated ticks must not erode it either (the scheduler calls both every
    # post-census tick, forever).
    for _ in range(3):
        census.promote_proven_singles(db, floor=15)
        census.activate_pair_concepts(db, max_new=10)
    assert db.execute(
        "SELECT status FROM concepts WHERE id=?", (cid_culled,)
    ).fetchone()["status"] == "culled"
