"""Tests for pair-concept enumeration + lazy activation + DB scale checks
(tournament T8).

Covers: the real, full-scale `C(815,2)=331,705` pair enumeration stays
within its time budget and an indexed status query stays sub-second at that
scale (`.claude/rules/single-actor-worker-tests.md`-adjacent scale
discipline — no code path may scan all 332,520 rows per tick); idempotent
re-seeding; pairs are enumerated WITHOUT deck rows (dormant); activation is
gated on both single cores having proven out and builds a deck lazily; and
the adversarial concurrent-activation race required by
`.claude/rules/single-actor-worker-tests.md` (two callers racing the same
dormant pair must never double-activate or double-count).
"""

from __future__ import annotations

import json
import threading
import time

from ptcg.factory import census, deckdb
from ptcg.factory.builder import Concept

#: Five real, engine-verified buildable single-core names (sorted
#: alphabetically among the ~815 real single-core concepts; confirmed
#: buildable via `builder.build_deck` at implementation time — avoids
#: fabricating names the engine can't actually build, per
#: `.claude/rules/verify-game-data-claims.md`).
#:
#: unpayable-attack-pool-rule (2026-08-12) T2 fix round 1: the original set
#: included "Abomasnow", whose own best attack needs a DIFFERENT energy
#: type than another attack in its own chain -- it is a genuine 2-type-cost
#: single core, so pairing it with ANY second core (here, "Abra") pushed
#: the combined pair to 3 distinct energy types, tripping the new R2
#: 2-type cap and making `test_activate_pairs_gated_on_both_cores_proven`'s
#: expected pair build fail. Re-derived executably (mono-payable-chain
#: definition from src/ptcg/factory/builder.py's `_deck_energy_plan`): all
#: 5 names below share the SAME single primary energy type (Psychic, type
#: 5) both individually and pairwise -- verified via `build_deck` for every
#: name AND every one of the C(5,2)=10 pairs among them (all `cards is not
#: None` and `validate_deck(cards) == []`), so no future 2-core test in
#: this file can hit the cap regardless of which two indices it picks.
_FIVE_CORES = ["Abra", "Alakazam", "Aromatisse", "Azelf", "Azumarill"]


def _db(tmp_path):
    d = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(d)
    return d


def _small_seeded(tmp_path, monkeypatch):
    """5 real, verified-buildable single-core concepts seeded (helper:
    monkeypatched enumerate -> 5 cores)."""
    db = _db(tmp_path)
    monkeypatch.setattr(
        census, "enumerate_concepts", lambda: [Concept(cores=(n,)) for n in _FIVE_CORES]
    )
    census.seed_census(db)
    return db


def _small_seeded_with_ratings(tmp_path, monkeypatch):
    """5 singles seeded + all C(5,2)=10 pairs seeded (dormant); the first 2
    singles are promoted to `status='active'` with a decisive rating (proven
    out); the other 3 stay `'untested'` (never proven) — matches exactly
    ONE pair (the 2 proven singles) as activation-eligible."""
    db = _small_seeded(tmp_path, monkeypatch)
    census.seed_pair_concepts(db)
    proven = _FIVE_CORES[:2]

    def _apply(c):
        for name in proven:
            cid = census.concept_id((name,))
            c.execute("UPDATE concepts SET status='active' WHERE id=?", (cid,))
            c.execute(
                "UPDATE coverage SET rating=1.0, games_played=15 WHERE concept_id=?", (cid,)
            )

    deckdb._write(db, _apply)
    return db


def test_seed_pair_concepts_full_scale_and_indexed_query(tmp_path):
    # FULL-SCALE check (global CLAUDE.md scale-blind-plan-code lesson): run the
    # real 331,705-row enumeration against a temp DB, not a toy subset.
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)
    census.seed_census(db)  # singles first (pair gating reads them)
    t0 = time.perf_counter()
    n = census.seed_pair_concepts(db)
    elapsed = time.perf_counter() - t0
    total = db.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
    singles = db.execute(
        "SELECT COUNT(*) FROM concepts WHERE json_array_length(cores)=1"
    ).fetchone()[0]
    assert n == total - singles == singles * (singles - 1) // 2  # C(n,2) exactly
    assert elapsed < 120, f"pair seeding took {elapsed:.1f}s (budget 120s)"
    t0 = time.perf_counter()
    db.execute("SELECT COUNT(*) FROM concepts WHERE status='untested'").fetchone()
    assert time.perf_counter() - t0 < 1.0  # indexed status scan stays sub-second


def test_seed_pair_concepts_idempotent(tmp_path, monkeypatch):
    db = _small_seeded(tmp_path, monkeypatch)  # helper: monkeypatched enumerate -> 5 cores
    a = census.seed_pair_concepts(db)
    b = census.seed_pair_concepts(db)
    assert b == 0 and a == 10  # C(5,2)=10, second run inserts nothing


def test_pairs_seeded_without_deck_rows(tmp_path, monkeypatch):
    db = _small_seeded(tmp_path, monkeypatch)
    census.seed_pair_concepts(db)
    # dormant pairs have no decks and do not block census_complete (T7)
    assert (
        db.execute(
            "SELECT COUNT(*) FROM decks d JOIN concepts c ON d.concept_id=c.id "
            "WHERE json_array_length(c.cores)=2"
        ).fetchone()[0]
        == 0
    )


def test_activate_pairs_gated_on_both_cores_proven(tmp_path, monkeypatch):
    db = _small_seeded_with_ratings(
        tmp_path, monkeypatch
    )  # 2 singles active+rated, rest untested
    n = census.activate_pair_concepts(db, max_new=5)
    assert n == 1  # only the pair whose BOTH cores are active activates
    row = db.execute(
        "SELECT c.status, (SELECT COUNT(*) FROM decks d WHERE d.concept_id=c.id) AS nd "
        "FROM concepts c WHERE json_array_length(c.cores)=2 AND c.status='active'"
    ).fetchone()
    assert row is not None and row["nd"] == 1  # deck built lazily at activation


def test_activate_pair_concepts_survives_concurrent_activation(tmp_path, monkeypatch):
    # INTERLEAVED (Pattern INTERLEAVED-TEST / .claude/rules/single-actor-worker-tests.md):
    # two racing callers must not both activate (or double-count activating) the
    # SAME dormant pair -- the guarded UPDATE (WHERE status='untested') means the
    # loser's write affects zero rows.
    db = _small_seeded_with_ratings(tmp_path, monkeypatch)
    db_path = tmp_path / "t.db"
    barrier = threading.Barrier(2)
    results: dict[str, int] = {}

    def _call(name):
        conn = deckdb.connect(db_path)
        barrier.wait()
        results[name] = census.activate_pair_concepts(conn, max_new=5)

    t1 = threading.Thread(target=_call, args=("t1",))
    t2 = threading.Thread(target=_call, args=("t2",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    total_activated = results["t1"] + results["t2"]
    assert total_activated == 1  # exactly one real activation across both racers

    active_pairs = db.execute(
        "SELECT id FROM concepts WHERE json_array_length(cores)=2 AND status='active'"
    ).fetchall()
    assert len(active_pairs) == 1  # no double-activation
    pid = active_pairs[0]["id"]
    n_decks = db.execute("SELECT COUNT(*) FROM decks WHERE concept_id=?", (pid,)).fetchone()[0]
    assert n_decks == 1  # no duplicate deck row from the losing racer


def test_seed_pair_concepts_virgin_dir(tmp_path):  # Pattern VIRGIN-DIR-TEST
    fresh = tmp_path / "no" / "dir" / "t.db"
    assert not fresh.parent.exists()
    db = deckdb.connect(fresh)
    deckdb.init_db(db)
    census.seed_census(db)
    assert census.seed_pair_concepts(db) >= 0


def test_pair_concept_id_matches_builder_content_addressing(tmp_path, monkeypatch):
    """Pair `cores` stored as `json.dumps(sorted([a, b]))`; id via
    `concept_id((a, b))` — same content-addressing scheme as singles,
    order-independent."""
    db = _small_seeded(tmp_path, monkeypatch)
    census.seed_pair_concepts(db)
    a, b = _FIVE_CORES[0], _FIVE_CORES[1]
    expected_id = census.concept_id((a, b))
    row = db.execute("SELECT cores FROM concepts WHERE id=?", (expected_id,)).fetchone()
    assert row is not None
    assert json.loads(row["cores"]) == sorted([a, b])
