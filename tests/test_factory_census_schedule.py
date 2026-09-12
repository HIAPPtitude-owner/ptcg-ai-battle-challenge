"""Tests for census scheduling (tournament T7; anchor-opponent rework Task 4):
pending-aware priority scheduling (worst-rated / least-covered first) against
a FIXED anchor opponent + completion detection, plus the adversarial
concurrent-scheduling test required by
`.claude/rules/single-actor-worker-tests.md` (the whole schedule pass is one
BEGIN IMMEDIATE transaction, so concurrent callers serialize and never
over-enqueue).

Task 4 replaced the round-robin opponent-rotation cursor with a fixed
opponent (the anchor deck, `anchor.ANCHOR_DECK_ID`): every screening game is
now candidate-vs-anchor, never candidate-vs-candidate. This means each
under-floor candidate accumulates its OWN deficit independently -- games
scheduled for one candidate never count toward another candidate's in-flight
tally the way opponent-side games used to under the old rotation.
"""

from __future__ import annotations

import json
import threading

from ptcg.factory import anchor, census, deckdb


def _db(tmp_path):
    d = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(d)
    return d


def _seed_concepts(db, specs):
    """`specs`: iterable of (concept_id, games_played, rating, cores_json).
    Inserts `concepts` (status='active') / `coverage` / `decks` rows
    directly, matching the T4/T5 sibling tests' fixture style (bypasses
    `census.seed_census` for speed and full control over coverage state).
    """

    def _apply(c):
        for cid, games_played, rating_value, cores_json in specs:
            c.execute(
                "INSERT INTO concepts(id,cores,status) VALUES(?,?, 'active')",
                (cid, cores_json),
            )
            c.execute(
                "INSERT INTO coverage(concept_id,games_played,distinct_opponents,rating) "
                "VALUES(?,?,0,?)",
                (cid, games_played, rating_value),
            )
            c.execute(
                "INSERT INTO decks(id,concept_id,cards) VALUES(?,?,'[]')",
                (f"d-{cid}", cid),
            )

    deckdb._write(db, _apply)


def _seed_concept_with_status(db, cid, games_played, rating_value, cores_json, status):
    """Same shape as `_seed_concepts` but with an explicit `concepts.status`
    (e.g. `'culled'`), for the status-filter test."""

    def _apply(c):
        c.execute(
            "INSERT INTO concepts(id,cores,status) VALUES(?,?,?)",
            (cid, cores_json, status),
        )
        c.execute(
            "INSERT INTO coverage(concept_id,games_played,distinct_opponents,rating) "
            "VALUES(?,?,0,?)",
            (cid, games_played, rating_value),
        )
        c.execute(
            "INSERT INTO decks(id,concept_id,cards) VALUES(?,?,'[]')",
            (f"d-{cid}", cid),
        )

    deckdb._write(db, _apply)


def _seed_anchor_deck(db):
    """Directly insert the anchor's `concepts`/`decks` rows -- same shape as
    `anchor.ensure_anchor_deck` (status='finalist', cores=`["anchor"]`, NO
    coverage row) but without depending on the real ladder-deck CSV, so
    these tests stay hermetic. `status='finalist'` matters: it is what
    keeps the anchor concept out of `_CANDIDATES_QUERY`'s `status IN
    ('untested','active')` filter, so the anchor is never itself a
    scheduling subject."""

    def _apply(c):
        c.execute(
            "INSERT OR IGNORE INTO concepts(id, cores, status, reason) "
            "VALUES(?, '[\"anchor\"]', 'finalist', 'test anchor stub')",
            (anchor.ANCHOR_CONCEPT_ID,),
        )
        c.execute(
            "INSERT OR IGNORE INTO decks(id, concept_id, cards, shell_variant) "
            "VALUES(?, ?, '[]', 0)",
            (anchor.ANCHOR_DECK_ID, anchor.ANCHOR_CONCEPT_ID),
        )

    deckdb._write(db, _apply)


def _single(cid: str) -> str:
    return json.dumps([cid])


def _pair(a: str, b: str) -> str:
    return json.dumps(sorted([a, b]))


def _drain_screening_games(db, count: int, winner: int = 0) -> None:
    """Claim + record `count` screening games through the real T4 queue
    functions, bumping `coverage.games_played` exactly as a runner would."""
    for _ in range(count):
        row = deckdb.claim_next_game(db, worker_pid=1)
        assert row is not None, "expected a pending game to drain"
        deckdb.record_result(db, row["id"], winner=winner)


def test_schedule_enqueues_only_concepts_below_floor(tmp_path):
    db = _db(tmp_path)
    _seed_anchor_deck(db)
    _seed_concepts(
        db,
        [
            ("cA", 0, None, _single("cA")),  # under floor, deficit 15
            ("cB", 15, 1.0, _single("cB")),  # AT floor -- must not be a subject
            ("cC", 5, 0.5, _single("cC")),  # under floor, deficit 10
        ],
    )
    n = census.schedule_screening_games(db, target_per_concept=15, batch=200)
    # Opponent is always the anchor (never another candidate), so each
    # under-floor concept accumulates its OWN deficit independently:
    # cA: 15-0=15, cC: 15-5=10. cB is already at floor -- never a subject.
    assert n == 25

    subject_counts = {
        row["concept_id"]: row["n"]
        for row in db.execute(
            "SELECT d.concept_id AS concept_id, COUNT(*) AS n "
            "FROM games g JOIN decks d ON d.id = g.deck_a_id GROUP BY d.concept_id"
        ).fetchall()
    }
    assert subject_counts == {"cA": 15, "cC": 10}  # cB (at floor) never a subject


def test_schedule_priority_higher_for_lower_covered(tmp_path):
    db = _db(tmp_path)
    _seed_anchor_deck(db)
    _seed_concepts(
        db,
        [
            ("zHigh", 10, 0.2, _single("zHigh")),  # more covered, under floor
            ("zLow", 0, None, _single("zLow")),  # least covered
        ],
    )
    census.schedule_screening_games(db, target_per_concept=15, batch=200)

    def _priority_for(subject_cid):
        row = db.execute(
            "SELECT g.priority FROM games g JOIN decks d ON d.id = g.deck_a_id "
            "WHERE d.concept_id = ? LIMIT 1",
            (subject_cid,),
        ).fetchone()
        assert row is not None, f"expected subject games for {subject_cid}"
        return row["priority"]

    p_low = _priority_for("zLow")
    p_high = _priority_for("zHigh")
    # hand-checked: 1/(0+1)=1.0 > 1/(10+1)=0.0909...
    assert p_low > p_high


def test_schedule_repeated_invocation_no_over_enqueue(tmp_path):
    # THE review finding: back-to-back calls must not re-enqueue games that
    # are already pending -- the deficit is games_played + in-flight aware.
    db = _db(tmp_path)
    _seed_anchor_deck(db)
    _seed_concepts(
        db,
        [
            ("cA", 0, None, _single("cA")),
            ("cB", 0, None, _single("cB")),
        ],
    )
    first = census.schedule_screening_games(db, target_per_concept=5, batch=200)
    # Hand-traced: opponent is always the anchor (never another candidate),
    # so cA and cB accumulate their OWN 5-game deficits independently:
    # 5 + 5 = 10 (unlike the old round-robin scheme, neither's games count
    # toward the other's in-flight tally).
    assert first == 10
    assert census.schedule_screening_games(db, target_per_concept=5, batch=200) == 0
    assert census.schedule_screening_games(db, target_per_concept=5, batch=200) == 0
    assert deckdb.pending_count(db, purpose="screening") == 10  # total stays at one call's worth


def test_schedule_tops_up_only_true_deficit_after_done(tmp_path):
    db = _db(tmp_path)
    _seed_anchor_deck(db)
    _seed_concepts(db, [("cA", 0, None, _single("cA"))])

    # batch=3 caps the first call below the full deficit of 5.
    assert census.schedule_screening_games(db, target_per_concept=5, batch=3) == 3
    _drain_screening_games(db, 3)  # games_played: cA=3; 0 in flight

    # Top-up enqueues ONLY the true residual deficit: cA needs 5-3-0=2.
    assert census.schedule_screening_games(db, target_per_concept=5, batch=200) == 2
    assert deckdb.pending_count(db, purpose="screening") == 2

    _drain_screening_games(db, 2)  # games_played: cA=5
    assert census.schedule_screening_games(db, target_per_concept=5, batch=200) == 0
    assert census.census_complete(db, floor=5) is True


def test_census_complete_ignores_dormant_pair_rows(tmp_path):
    db = _db(tmp_path)
    _seed_concepts(
        db,
        [
            ("cA", 5, None, _single("cA")),  # under floor
            ("cB", 15, 1.0, _single("cB")),  # at floor
        ],
    )
    # A dormant, not-yet-activated PAIR concept (T8 shape): status='untested',
    # cores length 2, no coverage/decks row at all.
    deckdb._write(
        db,
        lambda c: c.execute(
            "INSERT INTO concepts(id,cores,status) VALUES(?,?, 'untested')",
            ("pAB", _pair("cA", "cB")),
        ),
    )

    assert census.census_complete(db, floor=15) is False  # cA still under floor

    deckdb._write(
        db, lambda c: c.execute("UPDATE coverage SET games_played=15 WHERE concept_id='cA'")
    )
    assert census.census_complete(db, floor=15) is True  # dormant pair never blocked it


def test_schedule_all_covered_enqueues_nothing(tmp_path):
    db = _db(tmp_path)
    _seed_anchor_deck(db)
    _seed_concepts(
        db,
        [
            ("cA", 15, 0.7, _single("cA")),
            ("cB", 20, 0.3, _single("cB")),
        ],
    )
    n = census.schedule_screening_games(db, target_per_concept=15, batch=200)
    assert n == 0
    assert deckdb.pending_count(db, purpose="screening") == 0
    assert census.census_complete(db, floor=15) is True


def test_screening_opponent_is_always_anchor(tmp_path):
    db = _db(tmp_path)
    _seed_anchor_deck(db)
    _seed_concepts(
        db,
        [
            ("cA", 0, None, _single("cA")),
            ("cB", 3, 0.2, _single("cB")),
            ("cC", 7, -0.1, _single("cC")),
        ],
    )
    n = census.schedule_screening_games(db, target_per_concept=15, batch=200)
    assert n > 0

    games = db.execute("SELECT deck_b_id, agent_version_a, agent_version_b FROM games").fetchall()
    assert len(games) == n
    for row in games:
        assert row["deck_b_id"] == anchor.ANCHOR_DECK_ID
        assert row["agent_version_a"] == row["agent_version_b"]


def test_screening_noop_without_anchor_deck(tmp_path):
    db = _db(tmp_path)
    # No anchor deck row seeded -- schedule_screening_games must no-op, not
    # crash or fall back to some other opponent.
    _seed_concepts(db, [("cA", 0, None, _single("cA"))])

    n = census.schedule_screening_games(db, target_per_concept=15, batch=200)
    assert n == 0
    assert deckdb.pending_count(db, purpose="screening") == 0


def test_screening_uses_baseline_version_when_founded(tmp_path):
    db = _db(tmp_path)
    _seed_anchor_deck(db)
    _seed_concepts(db, [("cA", 0, None, _single("cA"))])

    def _found_v01(c):
        c.execute(
            "INSERT INTO baselines(version, offspring_id, deck_id, crowned_at) "
            "VALUES ('v0.1', NULL, 'd-cA', '2026-01-01T00:00:00+00:00')"
        )
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('baseline_version', 'v0.1')")

    deckdb._write(db, _found_v01)

    n1 = census.schedule_screening_games(db, target_per_concept=5, batch=200)
    assert n1 > 0
    rows_v01 = db.execute("SELECT agent_version_a, agent_version_b FROM games").fetchall()
    assert all(r["agent_version_a"] == "v0.1" and r["agent_version_b"] == "v0.1" for r in rows_v01)

    # Crown a new v0.2 baseline. A freshly-seeded under-floor concept's NEW
    # games must carry the new current-baseline version.
    def _crown_v02(c):
        c.execute(
            "INSERT INTO baselines(version, offspring_id, deck_id, crowned_at) "
            "VALUES ('v0.2', 'v0.1.1', 'd-cA', '2026-01-02T00:00:00+00:00')"
        )
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('baseline_version', 'v0.2')")

    deckdb._write(db, _crown_v02)
    _seed_concepts(db, [("cD", 0, None, _single("cD"))])

    n2 = census.schedule_screening_games(db, target_per_concept=5, batch=200)
    assert n2 > 0
    new_rows = db.execute(
        "SELECT agent_version_a, agent_version_b FROM games g "
        "JOIN decks d ON d.id = g.deck_a_id WHERE d.concept_id = 'cD'"
    ).fetchall()
    assert len(new_rows) > 0
    assert all(r["agent_version_a"] == "v0.2" and r["agent_version_b"] == "v0.2" for r in new_rows)


def test_culled_concepts_never_scheduled_and_never_block_census(tmp_path):
    db = _db(tmp_path)
    _seed_anchor_deck(db)
    _seed_concept_with_status(db, "cCulled", 0, None, _single("cCulled"), "culled")
    _seed_concepts(db, [("cA", 15, 1.0, _single("cA"))])  # at floor, active

    n = census.schedule_screening_games(db, target_per_concept=15, batch=200)
    # cCulled is under floor (0 < 15) but excluded by the status filter;
    # cA is already at floor -- nothing scheduled either way.
    assert n == 0
    assert deckdb.pending_count(db, purpose="screening") == 0

    # If the culled row still counted toward completion, this would be
    # False forever (cCulled can never be scheduled to clear its deficit).
    assert census.census_complete(db, floor=15) is True


def test_schedule_concurrent_calls_no_over_enqueue(tmp_path):  # INTERLEAVED
    # Two concurrent `schedule_screening_games` callers race the same
    # read-compute-enqueue window. The whole pass runs inside one BEGIN
    # IMMEDIATE transaction, so the callers serialize: the write-lock loser
    # observes the winner's freshly-inserted pending games in its own
    # in-flight read and enqueues 0 -- the same stale-read TOCTOU class as
    # the 5/day submission-cap race (commit 178043b) must not admit twice.
    db = _db(tmp_path)
    _seed_anchor_deck(db)
    _seed_concepts(
        db,
        [
            ("cA", 0, None, _single("cA")),
            ("cB", 0, None, _single("cB")),
        ],
    )

    db_path = tmp_path / "t.db"
    barrier = threading.Barrier(2)
    results: dict[str, int] = {}

    def _call(name):
        conn = deckdb.connect(db_path)
        barrier.wait()
        results[name] = census.schedule_screening_games(conn, target_per_concept=3, batch=200)

    t1 = threading.Thread(target=_call, args=("t1",))
    t2 = threading.Thread(target=_call, args=("t2",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    # Exactly one caller fills BOTH concepts' whole deficit (cA: 3, cB: 3 --
    # independent since the opponent is always the anchor, never the other
    # candidate, so nothing is shared between them); the other enqueues
    # nothing. Never 12.
    assert sorted(results.values()) == [0, 6]
    assert deckdb.pending_count(db, purpose="screening") == 6


def test_candidates_order_composition_tiebreak_within_coverage_order(tmp_path):
    """Spec §2 ORDER BY: rating ASC NULLS FIRST, games_played ASC,
    energy_count ASC NULLS LAST, pokemon_count ASC NULLS LAST,
    concept_id ASC. Hand-verified expected sequence (all games_played=0):
      cB (e20,p8, unrated)   lowest energy, lowest pokemon, id before cE
      cE (e20,p8, unrated)   full-count tie with cB -> concept_id tiebreak
      cA (e20,p12, unrated)  energy tie, higher pokemon
      cC (e30,p8, unrated)   higher energy
      cD (NULL,NULL, unrated) unstamped sorts LAST among the unrated
      cF (e1,p1, rating 0.2) RATED sorts after ALL unrated (NULLS FIRST):
                             composition is a tie-break WITHIN coverage
                             order, never a jump over it (Locked Decision 1)
    """
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    for cid, e, p, rating in [
        ("cA", 20, 12, None),
        ("cB", 20, 8, None),
        ("cC", 30, 8, None),
        ("cD", None, None, None),
        ("cE", 20, 8, None),
        ("cF", 1, 1, 0.2),
    ]:
        conn.execute("INSERT INTO concepts(id, cores) VALUES(?, '[\"x\"]')", (cid,))
        conn.execute(
            "INSERT INTO decks(id, concept_id, cards, shell_variant, "
            "energy_count, pokemon_count) VALUES(?, ?, '[]', 0, ?, ?)",
            ("d" + cid, cid, e, p),
        )
        conn.execute(
            "INSERT INTO coverage(concept_id, games_played, distinct_opponents, "
            "rating) VALUES(?, 0, 0, ?)",
            (cid, rating),
        )
    rows = conn.execute(census._CANDIDATES_QUERY, (15, 200)).fetchall()
    assert [r["concept_id"] for r in rows] == ["cB", "cE", "cA", "cC", "cD", "cF"]
