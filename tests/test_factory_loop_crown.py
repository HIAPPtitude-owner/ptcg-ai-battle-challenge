"""Tests for the CROWN step -- survivor round-robin -> champion-elect
NOMINATION (tournament T15; design-3 revision, `.superpowers/sdd/
2026-08-03-tournament-breeding-anchor-pressure/`: CROWN nominates, the
anchor verdict promotes).

`enqueue_crown_round_robin` enqueues a `purpose='crown'` round-robin among
every currently-`'survivor'` offspring that is not already crowned or
nominated (the eligibility exclusion prevents a past champion, OR a
currently-pending/resolved champion-elect, from re-entering a future
round-robin). Every unordered eligible pair plays `n_games_per_pair` games,
each side on its own MATCH-picked optimal deck. `resolve_crown` then reads
the AGGREGATE win% (both as `agent_version_a` and `agent_version_b` -- CROWN
has no fixed side, unlike MATCH/CONFIRM) once every pair is fully `done`,
and NOMINATES the best as a champion-elect -- a pending `anchor_checks` row
keyed by the offspring id on BOTH `version` and `offspring_id` (the
elect-row signature Task 7's promotion keys on) -- rather than inserting a
new `baselines` row or bumping `meta['baseline_version']` immediately. Every
other eligible offspring is marked `'trashed'` (the only available terminal
non-survivor status, per the locked Phase-1 CHECK constraint -- see
`resolve_crown`'s own docstring for the JUDGMENT CALL); the winner's own
status is left untouched at `'survivor'`. At most one champion-elect may be
pending at a time (a new guard blocks a second nomination while one is
already awaiting its anchor verdict).

Arithmetic hand-verified (`.claude/rules/plan-test-arithmetic-sanity.md`):
`C(3,2) = 3*2/2 = 3` pairs * `100` games/pair = `300` games enqueued for 3
eligible survivors (`CROWN_GAMES_PER_PAIR` rescoped 200->100, Task 3,
2026-08-08 -- see `test_enqueue_crown_round_robin_arithmetic`). The
aggregate-win scenario below still uses 200 games/pair as its own explicit,
self-contained dataset (`_play_crown_pair(..., total=200)`, independent of
the `CROWN_GAMES_PER_PAIR` default) -- 200 >= the new 100 bar either way, so
`resolve_crown`'s "fully done" gate is unaffected. Aggregate-win scenario
(3 survivors A/B/C, 200 games per
pair): A wins 120/200 vs B and 80/200 vs C -> A total 200/400 = 0.500; B
wins 80/200 vs A and 150/200 vs C -> B total 230/400 = 0.575; C wins
120/200 vs A and 50/200 vs C -> C total 170/400 = 0.425. Best is B at
0.575, hand-verified: 80+150=230, 200+200=400, 230/400=0.575 > 200/400=0.500
> 170/400=0.425.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from ptcg.factory import anchor, deckdb, loop, loop_scheduler, loop_state


def _seed_survivors(tmp_path: Path, n: int, set_decks: bool = True) -> tuple:
    """A deckdb with a founding `v0.1` baseline on a deck OUTSIDE the
    survivor pool (`cBase`/`dBase`) and `n` offspring rows, each parented to
    it on its own deck (`cS{i}`/`dS{i}`), `status='survivor'`. Returns
    `(db, [offspring_ids])` in insertion order (`v0.1.1 .. v0.1.n`, already
    ascending -- matches `_ELIGIBLE_CROWN_SURVIVORS_QUERY`'s `ORDER BY id`).
    Mirrors the `_seed` fixture convention established in
    `tests/test_factory_loop_confirm.py` / `tests/test_factory_loop_match.py`.
    """
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)

    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cBase','[\"Z\"]','untested')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dBase','cBase','[1]')")
        for i in range(n):
            cid, did = f"cS{i}", f"dS{i}"
            c.execute("INSERT INTO concepts(id,cores,status) VALUES(?,?,'active')", (cid, '["X"]'))
            c.execute("INSERT INTO decks(id,concept_id,cards) VALUES(?,?,?)", (did, cid, "[1]"))

    deckdb._write(db, _s)
    loop_state.set_founding_baseline(db, "dBase", loop.FOUNDING_AGENT_CONFIG)

    ids = []
    for i in range(n):
        off_id = loop_state.next_offspring_version(db)
        loop_state.insert_offspring(db, off_id, "{}", "w.json")
        if set_decks:
            loop.set_offspring_deck(db, off_id, f"dS{i}")
        loop_state.set_offspring_status(db, off_id, "survivor")
        ids.append(off_id)
    return db, ids


def _play_crown_pair(db, a_id, deck_a, b_id, deck_b, a_wins, total):
    """Enqueue + claim + record `total` `purpose='crown'` games between
    `a_id` (`agent_version_a`, `deck_a`) and `b_id` (`agent_version_b`,
    `deck_b`), the first `a_wins` decided for `a` (`winner=0`), the rest for
    `b` (`winner=1`). Mirrors `_play_match_series`/`_play_confirm_series`.
    Caller must pass `a_id < b_id` (ascending) to match the fixed
    `(lower_id, higher_id)` insertion order `enqueue_crown_round_robin` and
    `resolve_crown` both assume."""
    assert a_id < b_id, "a_id must sort before b_id -- matches CROWN's pairing convention"
    for i in range(total):
        deckdb.enqueue_game(db, deck_a, deck_b, a_id, b_id, purpose="crown")
        row = deckdb.claim_next_game(db, worker_pid=1)
        winner = 0 if i < a_wins else 1
        deckdb.record_result(db, row["id"], winner)


def _mk_floor_row(db, offspring_id: str, wr: float) -> None:
    """Insert a `floor_checks` row carrying the given `wr`, for `crown_field`
    ranking tests. `floor_checks` has no FK (see `deckdb.py`'s DDL), so any
    `offspring_id`/`deck_id` text is accepted -- mirrors the INSERT shape in
    `tests/test_factory_floor.py`'s own legacy-row seeding. The brief's
    `_mk_floor_row(db, k, wr=wr)` sketch is adapted 1:1 here; only the column
    set differs slightly (a fixed placeholder `deck_id`/`verdict`, since
    `crown_field`'s ranking only reads `wr`)."""

    def _apply(c):
        c.execute(
            "INSERT INTO floor_checks(offspring_id, deck_id, games_planned, wr, "
            "verdict, created_at) VALUES(?, 'dFloor', 50, ?, 'pass', "
            "'2026-01-01T00:00:00+00:00')",
            (offspring_id, wr),
        )

    deckdb._write(db, _apply)


# --- enqueue_crown_round_robin -----------------------------------------------


def test_enqueue_crown_round_robin_arithmetic(tmp_path):
    db, ids = _seed_survivors(tmp_path, 3)

    enqueued = loop.enqueue_crown_round_robin(db)

    # hand-verified: C(3,2)=3 pairs * 100 games/pair = 300 (CROWN_GAMES_PER_PAIR
    # rescoped 200->100, Task 3, 2026-08-08)
    assert enqueued == 300
    assert deckdb.pending_count(db, purpose="crown") == 300


def test_enqueue_crown_round_robin_noop_with_fewer_than_two_survivors(tmp_path):
    db, ids = _seed_survivors(tmp_path, 1)

    enqueued = loop.enqueue_crown_round_robin(db)

    assert enqueued == 0
    assert deckdb.pending_count(db, purpose="crown") == 0


def test_enqueue_crown_round_robin_noop_with_zero_survivors(tmp_path):
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)

    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cBase','[\"Z\"]','untested')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dBase','cBase','[1]')")

    deckdb._write(db, _s)
    loop_state.set_founding_baseline(db, "dBase", loop.FOUNDING_AGENT_CONFIG)

    enqueued = loop.enqueue_crown_round_robin(db)

    assert enqueued == 0


def test_enqueue_crown_round_robin_resumes_partial_shortfall(tmp_path):
    db, ids = _seed_survivors(tmp_path, 2)
    # Simulate a crash after only 50 of the eventual 100 crown games for the
    # single pair were ever enqueued (CROWN_GAMES_PER_PAIR rescoped
    # 200->100, Task 3, 2026-08-08).
    for _ in range(50):
        deckdb.enqueue_game(db, "dS0", "dS1", ids[0], ids[1], purpose="crown")

    enqueued = loop.enqueue_crown_round_robin(db)

    assert enqueued == 50  # hand-verified: 100 - 50 = 50, tops up rather than restarting
    assert deckdb.pending_count(db, purpose="crown") == 100  # total across both calls


def test_enqueue_crown_round_robin_guarded_against_double_call(tmp_path):
    db, ids = _seed_survivors(tmp_path, 2)

    first = loop.enqueue_crown_round_robin(db)
    second = loop.enqueue_crown_round_robin(db)  # already topped up to 100

    assert first == 100
    assert second == 0
    assert deckdb.pending_count(db, purpose="crown") == 100  # not doubled


def test_enqueue_crown_round_robin_missing_deck_id_raises(tmp_path):
    db, ids = _seed_survivors(tmp_path, 2, set_decks=False)

    with pytest.raises(RuntimeError):
        loop.enqueue_crown_round_robin(db)


def test_enqueue_crown_round_robin_excludes_already_crowned_offspring(tmp_path):
    # off0 was crowned in a PRIOR round -- a `baselines` row already
    # references it as offspring_id, even though its own offspring.status
    # is still 'survivor' (crown_baseline never touches offspring.status --
    # see loop_state.py). off1/off2 are fresh, never-crowned survivors.
    db, ids = _seed_survivors(tmp_path, 3)
    off0, off1, off2 = ids

    def _crown_off0(c):
        c.execute(
            "INSERT INTO baselines(version, offspring_id, deck_id, crowned_at) "
            "VALUES ('v0.2', ?, 'dS0', '2026-01-01T00:00:00+00:00')",
            (off0,),
        )

    deckdb._write(db, _crown_off0)

    enqueued = loop.enqueue_crown_round_robin(db)

    assert enqueued == 100  # only 1 eligible pair (off1, off2) once off0 is excluded: 1 * 100
    games = db.execute("SELECT agent_version_a, agent_version_b FROM games").fetchall()
    for g in games:
        assert off0 not in (g["agent_version_a"], g["agent_version_b"])
        assert {g["agent_version_a"], g["agent_version_b"]} == {off1, off2}


def test_enqueue_crown_round_robin_survives_concurrent_calls(tmp_path):  # INTERLEAVED
    # Same TOCTOU class as `enqueue_confirm_series`'s / `enqueue_match_games`'s
    # concurrent tests (`.claude/rules/single-actor-worker-tests.md`): two
    # concurrent callers racing the same per-pair existing-count read must
    # not both compute the same shortfall and double-enqueue.
    db, ids = _seed_survivors(tmp_path, 2)
    db_path = tmp_path / "t.db"
    barrier = threading.Barrier(2)
    results: dict[str, int] = {}

    def _call(name):
        conn = deckdb.connect(db_path)
        barrier.wait()
        results[name] = loop.enqueue_crown_round_robin(conn, n_games_per_pair=50)

    t1 = threading.Thread(target=_call, args=("t1",))
    t2 = threading.Thread(target=_call, args=("t2",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    # Exactly one caller fills the whole shortfall (50 games); the other's
    # own existing-count read runs strictly after the winner's commit and
    # sees the shortfall already filled -- 0 more. Never 100.
    assert sorted(results.values()) == [0, 50]
    assert deckdb.pending_count(db, purpose="crown") == 50


def _eqp_text(conn, sql, params=()):
    return " | ".join(str(r[3]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params))


def test_crown_pair_count_query_uses_covering_index(tmp_path):
    """Access-path guard (.claude/rules/single-actor-worker-tests.md
    access-path-analysis): the per-pair crown COUNT must never SCAN games
    inside the scheduler's BEGIN IMMEDIATE. RED before ix_games_crown_pair
    exists; GREEN after."""
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)
    plan = _eqp_text(
        db,
        "SELECT COUNT(*) FROM games WHERE purpose = 'crown' "
        "AND agent_version_a = ? AND agent_version_b = ?",
        ("v0.13.1", "v0.13.2"),
    )
    assert "ix_games_crown_pair" in plan
    assert "SCAN games" not in plan


def test_enqueue_prunes_pending_for_stale_pairs(tmp_path):
    """A pending crown game whose pair is no longer eligible (offspring
    trashed) is DELETEd on the next enqueue call; done/claimed rows survive.
    Adapted to this file's own `_seed_survivors` helper (the brief's
    `_mk_survivor(db, "v0.13.1")` sketch does not exist here) -- three fresh
    survivors via `_seed_survivors(tmp_path, 3)` stand in for the brief's
    v0.13.1/v0.13.2/v0.13.9 IDs."""
    db, ids = _seed_survivors(tmp_path, 3)
    off0, off1, off2 = ids

    loop.enqueue_crown_round_robin(db, n_games_per_pair=4)
    # 3 pairs x 4 = 12 pending
    assert db.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='crown' AND status='pending'"
    ).fetchone()[0] == 12
    # off2 drops out of eligibility
    loop_state.set_offspring_status(db, off2, "trashed")
    loop.enqueue_crown_round_robin(db, n_games_per_pair=4)
    rows = db.execute(
        "SELECT DISTINCT agent_version_a, agent_version_b FROM games "
        "WHERE purpose='crown' AND status='pending'"
    ).fetchall()
    assert {(r[0], r[1]) for r in rows} == {(off0, off1)}


def test_enqueue_prunes_pending_when_eligible_field_collapses_below_two(tmp_path):
    """Reviewer-caught gap (fix round 1): the `len(eligible) < 2` early
    return must still run the self-healing prune. Trashing all survivors
    (field collapses to 0 eligible) must not leave stale pending crown rows
    claimable forever -- Task 3's dynamic top-K makes sub-2 eligibility far
    more plausible than a one-off. RED before the fix: 12 pending rows
    survive the early return; GREEN after: all pruned, 0 pending."""
    db, ids = _seed_survivors(tmp_path, 3)

    loop.enqueue_crown_round_robin(db, n_games_per_pair=4)
    assert deckdb.pending_count(db, purpose="crown") == 12  # 3 pairs x 4

    for off_id in ids:
        loop_state.set_offspring_status(db, off_id, "trashed")

    enqueued = loop.enqueue_crown_round_robin(db, n_games_per_pair=4)

    assert enqueued == 0
    assert deckdb.pending_count(db, purpose="crown") == 0


def test_enqueue_prunes_excess_pending_when_target_shrinks(tmp_path):
    """Lowering n_games_per_pair deletes excess PENDING rows only; done rows
    are untouched and still credit toward the target."""
    db, ids = _seed_survivors(tmp_path, 2)

    loop.enqueue_crown_round_robin(db, n_games_per_pair=10)
    # simulate 3 played: mark 3 pending rows done directly (test setup only,
    # bypassing claim_next_game/record_result -- mirrors the brief's sketch)
    played_ids = [
        r[0]
        for r in db.execute(
            "SELECT id FROM games WHERE purpose='crown' AND status='pending' LIMIT 3"
        )
    ]
    for gid in played_ids:
        db.execute("UPDATE games SET status='done', winner=0 WHERE id=?", (gid,))
    loop.enqueue_crown_round_robin(db, n_games_per_pair=5)
    pending = db.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='crown' AND status='pending'"
    ).fetchone()[0]
    done = db.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='crown' AND status='done'"
    ).fetchone()[0]
    assert done == 3
    assert pending == 2  # 5 target - 3 done = 2 needed; excess 7 deleted


def test_enqueue_crown_round_robin_pairs_id_ascending_regardless_of_wr_order(tmp_path):
    """Reviewer-caught gap (fix round 1, factory-db-lock-contention,
    2026-08-08): the `sorted(..., key=lambda r: r["id"])` re-sort right
    after fetching `_CROWN_FIELD_QUERY` is load-bearing, not cosmetic.
    `crown_field`'s own field is wr-DESC ranked, not id-ranked; without the
    re-sort, a field whose wr order differs from its id order would make
    this pairing loop insert `(higher_id, lower_id)` games for some pairs,
    which `resolve_crown`'s `agent_version_a=?/agent_version_b=?` lookup
    (id-ascending by construction) could never find -- a permanent
    nomination stall. Seeds floor wr in the OPPOSITE order from id (lowest
    id gets the lowest wr, so the wr-DESC field is the exact reverse of the
    id-ASC order) and asserts every inserted pair still satisfies `a < b`."""
    db, ids = _seed_survivors(tmp_path, 4)
    for i, oid in enumerate(ids):
        _mk_floor_row(db, oid, wr=0.10 + i * 0.10)  # wr ASC with id -> field is id-DESC

    loop.enqueue_crown_round_robin(db, n_games_per_pair=1)

    rows = db.execute(
        "SELECT agent_version_a, agent_version_b FROM games WHERE purpose='crown'"
    ).fetchall()
    assert len(rows) == 6  # C(4,2) = 6 pairs, sanity check the field wasn't capped/short
    for r in rows:
        assert r["agent_version_a"] < r["agent_version_b"]


def test_enqueue_crown_round_robin_tops_up_after_dead_letter(tmp_path):
    """Whole-branch review finding (factory-db-lock-contention, 2026-08-08):
    a dead-lettered game (loop_scheduler's reclaim path, loop_scheduler.py:
    118-254) is LEFT `status='claimed'` forever, flagged `dead=1` in
    `game_recovery` -- but before this fix it still counted toward this
    function's per-pair `counts` GROUP BY, so no replacement game was ever
    enqueued and `resolve_crown`'s per-pair `done >= CROWN_GAMES_PER_PAIR`
    check could never be satisfied for that pair (a permanent, silent
    nomination stall). RED pre-fix: enqueued == 0 (dead game still counts
    toward the target). GREEN post-fix: the dead game is excluded, so a
    shortfall of 1 is topped up with a fresh `pending` replacement."""
    db, ids = _seed_survivors(tmp_path, 2)
    a_id, b_id = ids

    game_ids = []
    for _ in range(loop.CROWN_GAMES_PER_PAIR):
        game_ids.append(deckdb.enqueue_game(db, "dS0", "dS1", a_id, b_id, purpose="crown"))
    for _ in range(loop.CROWN_GAMES_PER_PAIR):
        deckdb.claim_next_game(db, worker_pid=1)  # all claimed, none done

    # Dead-letter ONE claimed game, mirroring loop_scheduler's reclaim path
    # (reclaim_orphans inserts/updates game_recovery with dead=1 and never
    # touches the game row's own status -- see loop_scheduler.py:220-231).
    loop_scheduler.ensure_recovery_schema(db)
    deckdb._write(
        db,
        lambda c: c.execute(
            "INSERT INTO game_recovery(game_id, reclaims, dead, last_error, updated_at) "
            "VALUES (?, 3, 1, 'poison', '2026-01-01T00:00:00+00:00')",
            (game_ids[0],),
        ),
    )

    enqueued = loop.enqueue_crown_round_robin(db)

    assert enqueued == 1
    assert deckdb.pending_count(db, purpose="crown") == 1


def test_enqueue_crown_round_robin_without_recovery_schema(tmp_path):
    """`game_recovery` is created lazily by `loop_scheduler.ensure_recovery_
    schema` and does NOT exist in standalone/unit-test contexts (loop.py
    must not import loop_scheduler -- circular: loop_scheduler imports
    loop). The dead-letter exclusion added above must degrade gracefully
    (skip the exclusion, not crash) when the table is absent -- no recovery
    machinery means no dead rows are possible."""
    db, ids = _seed_survivors(tmp_path, 2)
    assert (
        db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='game_recovery'"
        ).fetchone()
        is None
    )

    enqueued = loop.enqueue_crown_round_robin(db)

    assert enqueued == loop.CROWN_GAMES_PER_PAIR


# --- crown_field ----------------------------------------------------------


def test_crown_games_per_pair_is_100():
    assert loop.CROWN_GAMES_PER_PAIR == 100


def test_crown_field_ranks_by_floor_wr_capped(tmp_path):
    """Adapted from the brief's `_mk_survivor(db, "v0.13.N")` sketch: this
    file's `_seed_survivors` assigns sequential real ids (`v0.1.1..v0.1.5`)
    rather than the brief's literal placeholders, so the wr map is built
    positionally against the returned `ids` list instead."""
    db, ids = _seed_survivors(tmp_path, 5)
    for oid, wr in zip(ids, [0.50, 0.90, None, 0.70, 0.70]):
        if wr is not None:
            _mk_floor_row(db, oid, wr)

    field = loop.crown_field(db)

    field_ids = [r["id"] for r in field]
    # wr DESC, tie by id ASC, NULL last -- ids[1]=0.90 highest; ids[3]/ids[4]
    # tie at 0.70, broken by id ASC (ids[3] < ids[4]); then ids[0]=0.50;
    # ids[2]=None ranks last. Field of 5 < CROWN_TOP_K=8, nothing capped away.
    assert field_ids[:4] == [ids[1], ids[3], ids[4], ids[0]]
    assert field_ids[-1] == ids[2]


def test_crown_field_caps_at_top_k(tmp_path):
    db, ids = _seed_survivors(tmp_path, 12)
    for i, oid in enumerate(ids):
        _mk_floor_row(db, oid, wr=0.50 + i * 0.01)

    field = loop.crown_field(db)

    assert len(field) == loop.CROWN_TOP_K == 8
    assert field[0]["id"] == ids[-1]  # highest wr = the last-seeded survivor


# --- resolve_crown ------------------------------------------------------------


def test_resolve_crown_fewer_than_two_survivors_returns_none(tmp_path):
    db, ids = _seed_survivors(tmp_path, 1)

    assert loop.resolve_crown(db) is None


def test_resolve_crown_not_done_returns_none(tmp_path):
    db, ids = _seed_survivors(tmp_path, 2)
    off0, off1 = ids
    # only 50 of CROWN_GAMES_PER_PAIR=100 (rescoped 200->100, Task 3,
    # 2026-08-08 -- 150 would now clear the new, lower bar)
    _play_crown_pair(db, off0, "dS0", off1, "dS1", a_wins=40, total=50)

    assert loop.resolve_crown(db) is None
    row0 = db.execute("SELECT status FROM offspring WHERE id=?", (off0,)).fetchone()
    row1 = db.execute("SELECT status FROM offspring WHERE id=?", (off1,)).fetchone()
    assert row0["status"] == "survivor"
    assert row1["status"] == "survivor"


def test_resolve_crown_two_survivors_nominates_higher_win_rate(tmp_path):
    db, ids = _seed_survivors(tmp_path, 2)
    off0, off1 = ids
    _play_crown_pair(db, off0, "dS0", off1, "dS1", a_wins=120, total=200)  # off0: 120/200=0.60

    elect_id = loop.resolve_crown(db)

    assert elect_id == off0  # nominates the offspring id, not a bumped baseline version
    baseline_count = db.execute("SELECT COUNT(*) FROM baselines").fetchone()[0]
    assert baseline_count == 1  # only the founding v0.1 -- no new baseline row yet
    meta_row = db.execute("SELECT value FROM meta WHERE key='baseline_version'").fetchone()
    assert meta_row["value"] == "v0.1"  # unchanged -- promotion is Task 7's job
    anchor_row = db.execute(
        "SELECT version, offspring_id, deck_id, games_planned, verdict "
        "FROM anchor_checks WHERE offspring_id=?",
        (off0,),
    ).fetchone()
    assert anchor_row is not None
    assert anchor_row["version"] == off0  # elect-row signature: version == offspring_id
    assert anchor_row["deck_id"] == "dS0"  # winner's own optimal deck
    assert anchor_row["games_planned"] == anchor.ANCHOR_GAMES  # 200
    assert anchor_row["verdict"] == "pending"
    row0 = db.execute("SELECT status FROM offspring WHERE id=?", (off0,)).fetchone()
    row1 = db.execute("SELECT status FROM offspring WHERE id=?", (off1,)).fetchone()
    assert row0["status"] == "survivor"  # winner untouched
    assert row1["status"] == "trashed"  # loser retired


def test_resolve_crown_tie_break_lowest_id(tmp_path):
    db, ids = _seed_survivors(tmp_path, 2)
    off0, off1 = ids
    _play_crown_pair(db, off0, "dS0", off1, "dS1", a_wins=100, total=200)  # 0.500 vs 0.500

    elect_id = loop.resolve_crown(db)

    assert elect_id == off0  # tie broken by lowest id
    anchor_row = db.execute(
        "SELECT offspring_id FROM anchor_checks WHERE offspring_id=?", (off0,)
    ).fetchone()
    assert anchor_row["offspring_id"] == off0


def test_resolve_crown_three_survivors_best_aggregate_win_rate(tmp_path):
    db, ids = _seed_survivors(tmp_path, 3)
    off0, off1, off2 = ids  # A, B, C
    # A vs B: A wins 120/200 (A=0.60, B=0.40)
    _play_crown_pair(db, off0, "dS0", off1, "dS1", a_wins=120, total=200)
    # A vs C: A wins 80/200 (A=0.40, C=0.60)
    _play_crown_pair(db, off0, "dS0", off2, "dS2", a_wins=80, total=200)
    # B vs C: B wins 150/200 (B=0.75, C=0.25)
    _play_crown_pair(db, off1, "dS1", off2, "dS2", a_wins=150, total=200)
    # Aggregates: A=(120+80)/400=0.500, B=(80+150)/400=0.575, C=(120+50)/400=0.425
    # Best: B at 0.575 (hand-verified in module docstring).

    elect_id = loop.resolve_crown(db)

    assert elect_id == off1
    anchor_row = db.execute(
        "SELECT offspring_id, deck_id FROM anchor_checks WHERE offspring_id=?", (off1,)
    ).fetchone()
    assert anchor_row["offspring_id"] == off1
    assert anchor_row["deck_id"] == "dS1"
    row0 = db.execute("SELECT status FROM offspring WHERE id=?", (off0,)).fetchone()
    row1 = db.execute("SELECT status FROM offspring WHERE id=?", (off1,)).fetchone()
    row2 = db.execute("SELECT status FROM offspring WHERE id=?", (off2,)).fetchone()
    assert row0["status"] == "trashed"
    assert row1["status"] == "survivor"  # winner untouched
    assert row2["status"] == "trashed"


def test_resolve_crown_idempotent_once_resolved(tmp_path):
    db, ids = _seed_survivors(tmp_path, 2)
    off0, off1 = ids
    _play_crown_pair(db, off0, "dS0", off1, "dS1", a_wins=120, total=200)
    first = loop.resolve_crown(db)

    second = loop.resolve_crown(db)  # winner excluded via anchor_checks, loser trashed -- <2 eligible

    assert first == off0
    assert second is None
    baseline_count = db.execute("SELECT COUNT(*) FROM baselines").fetchone()[0]
    assert baseline_count == 1  # still just the founding v0.1 -- no promotion happens here
    anchor_count = db.execute("SELECT COUNT(*) FROM anchor_checks").fetchone()[0]
    assert anchor_count == 1  # exactly one nomination, not a second


def test_resolve_crown_survives_concurrent_calls(tmp_path):  # INTERLEAVED
    # Same TOCTOU class as `resolve_confirm`'s concurrent test
    # (`.claude/rules/single-actor-worker-tests.md`): two concurrent calls
    # on the same fully-done round-robin must not both perform the
    # nomination write. Discriminates via a trace callback on
    # "INSERT INTO ANCHOR_CHECKS": pre-fix (unlocked-read shape) both
    # threads could pass the eligibility read before either commits,
    # producing 2 INSERTs (a duplicate/double nomination); the fixed shape
    # (whole read + write inside one `deckdb._write` BEGIN IMMEDIATE txn)
    # forces thread B's own eligibility read to happen strictly after
    # thread A's commit, so thread B observes the shrunk eligible set and
    # returns None -- exactly 1 INSERT total.
    db, ids = _seed_survivors(tmp_path, 2)
    off0, off1 = ids
    _play_crown_pair(db, off0, "dS0", off1, "dS1", a_wins=120, total=200)
    db_path = tmp_path / "t.db"
    barrier = threading.Barrier(2)
    insert_count = 0
    count_lock = threading.Lock()

    def _trace(sql: str) -> None:
        nonlocal insert_count
        if sql.strip().upper().startswith("INSERT INTO ANCHOR_CHECKS"):
            with count_lock:
                insert_count += 1

    results: dict[str, str | None] = {}

    def _call(name: str) -> None:
        conn = deckdb.connect(db_path)
        conn.set_trace_callback(_trace)
        barrier.wait()
        results[name] = loop.resolve_crown(conn)

    t1 = threading.Thread(target=_call, args=("t1",))
    t2 = threading.Thread(target=_call, args=("t2",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert sorted(results.values(), key=lambda v: (v is None, v)) == [off0, None]
    assert insert_count == 1  # exactly one real nomination across both racers
    baseline_count = db.execute("SELECT COUNT(*) FROM baselines").fetchone()[0]
    assert baseline_count == 1  # founding v0.1 only -- nomination never touches baselines
    anchor_count = db.execute("SELECT COUNT(*) FROM anchor_checks").fetchone()[0]
    assert anchor_count == 1  # exactly one nomination row


def test_nominated_elect_excluded_from_next_round_robin(tmp_path):
    # Once off0 is nominated (a pending anchor_checks elect row keyed on its
    # own id), it must never re-enter a LATER round-robin among fresh
    # survivors -- mirrors the existing `baselines`-exclusion coverage
    # (`test_enqueue_crown_round_robin_excludes_already_crowned_offspring`)
    # for the new `anchor_checks` exclusion clause.
    db, ids = _seed_survivors(tmp_path, 2)
    off0, off1 = ids
    _play_crown_pair(db, off0, "dS0", off1, "dS1", a_wins=120, total=200)  # off0: 0.60
    elect_id = loop.resolve_crown(db)
    assert elect_id == off0

    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cS2','[\"X\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dS2','cS2','[1]')")
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cS3','[\"X\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dS3','cS3','[1]')")

    deckdb._write(db, _s)
    off2 = loop_state.next_offspring_version(db)
    loop_state.insert_offspring(db, off2, "{}", "w.json")
    loop.set_offspring_deck(db, off2, "dS2")
    loop_state.set_offspring_status(db, off2, "survivor")
    off3 = loop_state.next_offspring_version(db)
    loop_state.insert_offspring(db, off3, "{}", "w.json")
    loop.set_offspring_deck(db, off3, "dS3")
    loop_state.set_offspring_status(db, off3, "survivor")

    eligible = loop.eligible_crown_survivors(db)
    eligible_ids = {row["id"] for row in eligible}

    assert off0 not in eligible_ids  # nominated elect excluded
    assert off1 not in eligible_ids  # trashed loser excluded (pre-existing behavior)
    assert eligible_ids == {off2, off3}


def test_no_second_nomination_while_elect_pending(tmp_path):
    # off0/off1's round-robin resolves first, nominating off0 (pending
    # anchor_checks). Two fresh survivors (off2/off3) then finish their OWN
    # round-robin fully -- but resolve_crown must refuse a second
    # nomination while off0's anchor verdict is still pending, and must not
    # enqueue/trash anything in the process.
    db, ids = _seed_survivors(tmp_path, 2)
    off0, off1 = ids
    _play_crown_pair(db, off0, "dS0", off1, "dS1", a_wins=120, total=200)
    elect_id = loop.resolve_crown(db)
    assert elect_id == off0

    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cS2','[\"X\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dS2','cS2','[1]')")
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cS3','[\"X\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dS3','cS3','[1]')")

    deckdb._write(db, _s)
    off2 = loop_state.next_offspring_version(db)
    loop_state.insert_offspring(db, off2, "{}", "w.json")
    loop.set_offspring_deck(db, off2, "dS2")
    loop_state.set_offspring_status(db, off2, "survivor")
    off3 = loop_state.next_offspring_version(db)
    loop_state.insert_offspring(db, off3, "{}", "w.json")
    loop.set_offspring_deck(db, off3, "dS3")
    loop_state.set_offspring_status(db, off3, "survivor")
    _play_crown_pair(db, off2, "dS2", off3, "dS3", a_wins=120, total=200)  # fully done round-robin

    result = loop.resolve_crown(db)

    assert result is None  # pending elect blocks a second nomination
    anchor_count = db.execute("SELECT COUNT(*) FROM anchor_checks").fetchone()[0]
    assert anchor_count == 1  # still just off0's pending nomination
    row2 = db.execute("SELECT status FROM offspring WHERE id=?", (off2,)).fetchone()
    row3 = db.execute("SELECT status FROM offspring WHERE id=?", (off3,)).fetchone()
    assert row2["status"] == "survivor"  # untouched -- no trashing while blocked
    assert row3["status"] == "survivor"


def test_supersede_delete_never_collaterals_a_concurrent_nomination(tmp_path):
    """INTERLEAVED (`.claude/rules/single-actor-worker-tests.md`): the exact
    interaction `enqueue_anchor_series`'s docstring promises safety for --
    "Elect and baseline series are NEVER collateral damage here" -- raced
    rather than asserted.

    `enqueue_anchor_series` step 2 issues a blind supersede DELETE:

        DELETE FROM games WHERE purpose='anchor' AND status='pending'
          AND agent_version_a NOT IN
              (SELECT version FROM anchor_checks WHERE verdict='pending')

    Its exemption set is read from `anchor_checks` -- the very table
    `resolve_crown` INSERTs a nomination into. If the two ran without
    serializing, an enqueue that snapshotted the exemption set BEFORE a
    concurrent nomination committed could delete the freshly-nominated
    elect's pending games (or, symmetrically, enqueue a series the nomination
    then orphans). Both orderings must be safe, so this asserts on the
    INVARIANT that holds either way rather than on one interleaving:
    afterwards, every pending anchor check has a complete series and nothing
    was collaterally deleted.

    The baseline's own pending series is seeded FIRST and re-counted at the
    end -- it is the row most exposed to a mis-scoped DELETE, since it is
    never the row being nominated.
    """
    db, ids = _seed_survivors(tmp_path, 2)
    off0, off1 = ids
    _play_crown_pair(db, off0, "dS0", off1, "dS1", a_wins=120, total=200)
    db_path = tmp_path / "t.db"

    # Baseline series exists and is fully pending before the race.
    anchor.enqueue_anchor_series(db)
    baseline_ids_before = [
        r["id"] for r in db.execute(
            "SELECT id FROM games WHERE purpose='anchor' AND agent_version_a='v0.1' "
            "ORDER BY id"
        ).fetchall()
    ]
    assert len(baseline_ids_before) == anchor.ANCHOR_GAMES

    barrier = threading.Barrier(2)
    deletes = 0
    lock = threading.Lock()
    entered = []

    def _trace(sql: str) -> None:
        nonlocal deletes
        if sql.strip().upper().startswith("DELETE FROM GAMES"):
            with lock:
                deletes += 1

    def _enqueue() -> None:
        c = deckdb.connect(db_path)
        c.set_trace_callback(_trace)
        barrier.wait(timeout=10)
        with lock:
            entered.append(1)
        anchor.enqueue_anchor_series(c)

    def _nominate() -> None:
        c = deckdb.connect(db_path)
        barrier.wait(timeout=10)
        with lock:
            entered.append(1)
        loop.resolve_crown(c)

    threads = [threading.Thread(target=_enqueue), threading.Thread(target=_nominate)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert not barrier.broken, "barrier broke -- the two calls never overlapped"
    assert len(entered) == 2  # overlap receipt
    assert deletes == 1  # the supersede DELETE ran (the path under test is live)

    # The nomination survived the racing DELETE's exemption-set read.
    elect = db.execute(
        "SELECT version, verdict FROM anchor_checks WHERE offspring_id=?", (off0,)
    ).fetchone()
    assert elect is not None and elect["verdict"] == "pending"

    # The baseline's pending series was NOT collateral damage -- and this is
    # asserted on the ROW IDENTITIES, not just the count. The DELETE's
    # `agent_version_a NOT IN (pending checks)` exemption is what makes the
    # existing rows survive; drop that clause and the count still reads 200
    # (the top-up in the same transaction refills it) while every id has
    # changed -- i.e. the whole series is churned and re-queued on EVERY call,
    # discarding claim progress. Counting alone cannot see that; ids can.
    baseline_ids_after = [
        r["id"] for r in db.execute(
            "SELECT id FROM games WHERE purpose='anchor' AND agent_version_a='v0.1' "
            "ORDER BY id"
        ).fetchall()
    ]
    assert baseline_ids_after == baseline_ids_before

    # Whichever order won, a follow-up enqueue leaves EVERY pending check with
    # exactly one full series -- no double-fill, no starved elect.
    anchor.enqueue_anchor_series(db)
    for row in db.execute(
        "SELECT version FROM anchor_checks WHERE verdict='pending'"
    ).fetchall():
        n = db.execute(
            "SELECT COUNT(*) FROM games WHERE purpose='anchor' AND agent_version_a=?",
            (row["version"],),
        ).fetchone()[0]
        assert n == anchor.ANCHOR_GAMES, f"{row['version']} has {n} games"


def test_resolve_crown_trashes_full_cohort_not_just_field(tmp_path):
    """Cohort-clear (Task 3, 2026-08-08): nomination happens within the
    top-CROWN_TOP_K field, but ALL other eligible survivors -- including
    sub-top-K stragglers that never played a single crown game this
    generation -- are trashed. Adapted from the brief's sketch: seeds
    K+2 survivors, gives the first K floor rows (so they rank into the
    capped field, 2 stragglers stay wr=NULL and are excluded), completes
    the field's round-robin with the highest-wr member sweeping every pair,
    then asserts the winner survives and literally everyone else --
    field losers AND stragglers alike -- is trashed."""
    k = loop.CROWN_TOP_K
    db, ids = _seed_survivors(tmp_path, k + 2)
    decks = {oid: f"dS{i}" for i, oid in enumerate(ids)}
    field_ids = ids[:k]  # get floor rows -> rank into the capped field
    for i, oid in enumerate(field_ids):
        _mk_floor_row(db, oid, wr=0.50 + i * 0.01)  # last-seeded field member = highest wr
    champion = field_ids[-1]

    field = loop.crown_field(db)
    assert {r["id"] for r in field} == set(field_ids)  # the 2 stragglers are excluded

    sorted_field = sorted(field_ids)
    for i in range(len(sorted_field)):
        for j in range(i + 1, len(sorted_field)):
            a_id, b_id = sorted_field[i], sorted_field[j]
            a_wins = loop.CROWN_GAMES_PER_PAIR if a_id == champion else 0
            _play_crown_pair(
                db,
                a_id,
                decks[a_id],
                b_id,
                decks[b_id],
                a_wins=a_wins,
                total=loop.CROWN_GAMES_PER_PAIR,
            )
    # champion sweeps 1.0 aggregate; every other field member loses at
    # least once to champion, so its aggregate is strictly < 1.0 -- champion
    # is the unambiguous nominee regardless of the non-champion-vs-
    # non-champion results above (all decided for "b" by construction).

    elect = loop.resolve_crown(db)

    assert elect == champion
    statuses = {r[0]: r[1] for r in db.execute("SELECT id, status FROM offspring")}
    assert statuses[elect] == "survivor"
    assert all(s == "trashed" for oid, s in statuses.items() if oid != elect)
