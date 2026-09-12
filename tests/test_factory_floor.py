"""Tests for the early anchor floor gate (anchor-pressure design 2).

Spec: docs/superpowers/specs/2026-08-03-tournament-breeding-anchor-pressure-design.md
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from ptcg.factory import anchor, deckdb, floor
from tests.fixtures.race import race_two


def _connect(tmp_path: Path) -> sqlite3.Connection:
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    return conn


def _seed_world(conn, *, oid="v0.1.1", status="matching", deck="dOff"):
    """Anchor deck row (fake cards, no CSV read), one offspring with a
    MATCH-picked deck. Founding baseline not needed by floor.py itself."""
    def _apply(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES(?,?,?)",
                  (anchor.ANCHOR_CONCEPT_ID, '["anchor"]', "finalist"))
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES(?,?,?)",
                  (anchor.ANCHOR_DECK_ID, anchor.ANCHOR_CONCEPT_ID, json.dumps([3] * 60)))
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cOff','[\"cOff\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES(?, 'cOff', ?)",
                  (deck, json.dumps([3] * 60)))
        c.execute(
            "INSERT INTO offspring(id,parent_baseline_version,search_config_json,"
            "value_net_ref,deck_id,status,created_at) VALUES(?,?,?,?,?,?,?)",
            (oid, "v0.1", "{}", "w.json", deck, status, "2026-08-03T00:00:00+00:00"))
    deckdb._write(conn, _apply)


def _seed_match_results(conn, oid, deck_shares):
    """Done `match` games for `oid` so the re-pick ranking has a field:
    `deck_shares` is `{deck_id: (wins, n)}`. Also creates each deck row."""
    def _apply(c):
        for i, (deck_id, (wins, n)) in enumerate(sorted(deck_shares.items())):
            c.execute("INSERT OR IGNORE INTO concepts(id,cores,status) "
                      "VALUES(?,?, 'active')", (f"c{deck_id}", f'["c{deck_id}"]'))
            c.execute("INSERT OR IGNORE INTO decks(id,concept_id,cards) VALUES(?,?,?)",
                      (deck_id, f"c{deck_id}", json.dumps([3] * 60)))
            for g in range(n):
                c.execute(
                    "INSERT INTO games(deck_a_id,deck_b_id,agent_version_a,"
                    "agent_version_b,purpose,winner,status) "
                    "VALUES(?,?,?, 'v0.1','match',?, 'done')",
                    (deck_id, deck_id, oid, 0 if g < wins else 1))
    deckdb._write(conn, _apply)


def _finish_floor_games(conn, oid, wins, total=floor.FLOOR_GAMES, attempt=0):
    """Mark the first `total` pending floor games done: `wins` candidate wins,
    the rest anchor wins."""
    version = floor.floor_version(oid, attempt)
    def _apply(c):
        rows = c.execute(
            "SELECT id FROM games WHERE purpose='floor' AND agent_version_a=? "
            "ORDER BY id LIMIT ?", (version, total)).fetchall()
        assert len(rows) == total
        for i, row in enumerate(rows):
            c.execute("UPDATE games SET status='done', winner=? WHERE id=?",
                      (0 if i < wins else 1, row["id"]))
    deckdb._write(conn, _apply)


def test_constants_exact_values():
    assert floor.FLOOR_GAMES == 50
    assert floor.FLOOR_BAR == 0.46  # recalibrated 2026-08-13 (T6 gate), was 0.40
    assert floor.FLOOR_VERSION_PREFIX == "floor:"
    assert floor.FLOOR_MAX_ATTEMPTS == 3  # 1 initial pick + 2 re-picks


def test_floor_version_is_attempt_scoped_and_attempt0_is_unsuffixed():
    # Attempt 0 keeps the pre-re-pick string exactly (live rows/games).
    assert floor.floor_version("v0.1.1") == "floor:v0.1.1"
    assert floor.floor_version("v0.1.1", 0) == "floor:v0.1.1"
    # Later attempts get their OWN key so their games are never pooled with
    # the failed attempt's games in `_FLOOR_RESULTS_QUERY`.
    assert floor.floor_version("v0.1.1", 1) == "floor:v0.1.1#1"
    assert floor.floor_version("v0.1.1", 2) == "floor:v0.1.1#2"
    assert len({floor.floor_version("v0.1.1", a) for a in range(3)}) == 3
    # runner_pool resolves floor games by PREFIX, so every attempt still
    # resolves to HeuristicAgent.
    for attempt in range(3):
        assert floor.floor_version("v0.1.1", attempt).startswith(
            floor.FLOOR_VERSION_PREFIX)


def test_init_db_creates_floor_checks_on_virgin_db(tmp_path):
    conn = _connect(tmp_path)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "floor_checks" in names


def test_ensure_schema_upgrades_legacy_db(tmp_path):
    conn = deckdb.connect(tmp_path / "legacy.db")
    def _apply(c):
        for ddl in deckdb._DDL_STATEMENTS:
            if "floor_checks" not in ddl:
                c.execute(ddl)
    deckdb._write(conn, _apply)
    floor._ensure_schema(conn)
    floor._ensure_schema(conn)  # idempotent
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "floor_checks" in names


def test_enqueue_creates_row_and_50_heuristic_games(tmp_path):
    conn = _connect(tmp_path)
    _seed_world(conn)
    assert floor.enqueue_floor_series(conn, "v0.1.1") == 50
    row = conn.execute(
        "SELECT deck_id, games_planned, verdict FROM floor_checks "
        "WHERE offspring_id='v0.1.1'").fetchone()
    assert row["deck_id"] == "dOff"
    assert row["games_planned"] == 50
    assert row["verdict"] == "pending"
    game = conn.execute(
        "SELECT * FROM games WHERE purpose='floor' LIMIT 1").fetchone()
    assert game["deck_a_id"] == "dOff"
    assert game["deck_b_id"] == anchor.ANCHOR_DECK_ID
    assert game["agent_version_a"] == "floor:v0.1.1"
    assert game["agent_version_b"] == anchor.ANCHOR_VERSION
    assert game["priority"] == floor.FLOOR_PRIORITY
    # resumable top-up: a second call enqueues only the shortfall (0 here)
    assert floor.enqueue_floor_series(conn, "v0.1.1") == 0


def test_enqueue_guards(tmp_path):
    conn = _connect(tmp_path)
    _seed_world(conn, status="confirming")
    assert floor.enqueue_floor_series(conn, "v0.1.1") == 0  # wrong status -> no-op
    with pytest.raises(ValueError):
        floor.enqueue_floor_series(conn, "nope")  # missing offspring -> loud


def test_enqueue_requires_deck_id(tmp_path):
    conn = _connect(tmp_path)
    _seed_world(conn, deck="dOff")
    def _apply(c):
        c.execute("UPDATE offspring SET deck_id=NULL WHERE id='v0.1.1'")
    deckdb._write(conn, _apply)
    with pytest.raises(RuntimeError):
        floor.enqueue_floor_series(conn, "v0.1.1")


def test_resolve_pass_at_exact_bar_23_wins(tmp_path):
    """EXACT-BAR case: at the calibrated bar 0.46, n=50 lands on the bar
    precisely (23/50 = 0.460) and the comparison is `>=`, so it PASSES.
    Hand-verified: 23/50 = 0.460 == FLOOR_BAR -> pass."""
    conn = _connect(tmp_path)
    _seed_world(conn)
    floor.enqueue_floor_series(conn, "v0.1.1")
    _finish_floor_games(conn, "v0.1.1", wins=23)  # 23/50 = 0.460 >= 0.46 -> pass
    assert floor.resolve_floor(conn, "v0.1.1") == "pass"
    off = conn.execute("SELECT status FROM offspring WHERE id='v0.1.1'").fetchone()
    assert off["status"] == "matching"  # pass does NOT advance status itself


def test_resolve_fail_at_22_wins_trashes_offspring(tmp_path):
    """One win BELOW the exact bar -- fail with NO untried MATCH deck
    available (no match games seeded), so the verdict is terminal on attempt
    0. Hand-verified: 22/50 = 0.44 < 0.46 -> fail."""
    conn = _connect(tmp_path)
    _seed_world(conn)
    floor.enqueue_floor_series(conn, "v0.1.1")
    _finish_floor_games(conn, "v0.1.1", wins=22)  # 22/50 = 0.44 < 0.46 -> fail
    assert floor.resolve_floor(conn, "v0.1.1") == "fail"
    off = conn.execute("SELECT status FROM offspring WHERE id='v0.1.1'").fetchone()
    assert off["status"] == "trashed"
    # settled verdict is never recomputed/flipped
    assert floor.resolve_floor(conn, "v0.1.1") == "fail"


def test_resolve_pending_and_absent_shapes(tmp_path):
    conn = _connect(tmp_path)
    _seed_world(conn)
    assert floor.resolve_floor(conn, "v0.1.1") == "absent"
    assert floor.floor_status(conn, "v0.1.1") == ("absent", 0, floor.FLOOR_GAMES, None)
    floor.enqueue_floor_series(conn, "v0.1.1")
    _finish_floor_games(conn, "v0.1.1", wins=10, total=20)  # only 20 of 50 done
    assert floor.resolve_floor(conn, "v0.1.1") == "pending"
    verdict, done, planned, wr = floor.floor_status(conn, "v0.1.1")
    assert (verdict, done, planned, wr) == ("pending", 20, 50, None)


def test_draws_count_as_losses(tmp_path):
    conn = _connect(tmp_path)
    _seed_world(conn)
    floor.enqueue_floor_series(conn, "v0.1.1")
    version = floor.floor_version("v0.1.1")
    def _apply(c):
        rows = c.execute(
            "SELECT id FROM games WHERE purpose='floor' AND agent_version_a=? "
            "ORDER BY id", (version,)).fetchall()
        for i, row in enumerate(rows):
            # 19 wins, 31 draws -> wr 0.38 -> fail (draw is not a win).
            # If draws counted as wins this would read 50/50 = 1.00.
            c.execute("UPDATE games SET status='done', winner=? WHERE id=?",
                      (0 if i < 19 else 2, row["id"]))
    deckdb._write(conn, _apply)
    assert floor.resolve_floor(conn, "v0.1.1") == "fail"


def test_enqueue_survives_concurrent_calls(tmp_path):
    """Interleaved-mutation receipt (.claude/rules/single-actor-worker-tests.md):
    two threads on two connections both enqueue; the loser's count-read runs
    after the winner's COMMIT, so the series is never doubled."""
    db = tmp_path / "t.db"
    conn = deckdb.connect(db)
    deckdb.init_db(conn)
    _seed_world(conn)
    results = []
    def _call():
        c = deckdb.connect(db)
        results.append(floor.enqueue_floor_series(c, "v0.1.1"))
    t1 = threading.Thread(target=_call)
    t2 = threading.Thread(target=_call)
    t1.start(); t2.start(); t1.join(); t2.join()
    total = conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='floor'").fetchone()[0]
    assert total == 50
    # BEGIN IMMEDIATE serializes: winner enqueues 50, loser reads the
    # committed 50 and tops up 0 -- never 100 from two 50-game passes.
    assert sorted(results) == [0, 50]
    # exactly one floor_checks row either way
    n = conn.execute("SELECT COUNT(*) FROM floor_checks").fetchone()[0]
    assert n == 1


def test_resolve_survives_concurrent_calls(tmp_path):
    """INTERLEAVED (`.claude/rules/single-actor-worker-tests.md`): two racing
    `resolve_floor` calls must perform the verdict write EXACTLY ONCE.

    Discriminates on the mutating statement, not the return value. The
    lost-race branch (`floor.py`, `cur.rowcount != 1`) deliberately reports
    the winner's settled verdict, so `verdicts == ['pass','pass']` is a
    TAUTOLOGY -- true under a correct `BEGIN IMMEDIATE` implementation and
    equally true under a fully non-atomic one, i.e. zero fail-power. Counting
    `UPDATE floor_checks` executions does discriminate: atomic -> the loser's
    own `verdict != 'pending'` read runs after the winner COMMITs and returns
    early (1 execution); non-atomic -> both racers pass the pending-read and
    both execute the guarded UPDATE (2 executions). RED receipt in this
    slice's pass2-fixwave-report.md."""
    db = tmp_path / "t.db"
    conn = deckdb.connect(db)
    deckdb.init_db(conn)
    _seed_world(conn)
    floor.enqueue_floor_series(conn, "v0.1.1")
    _finish_floor_games(conn, "v0.1.1", wins=26)  # 26/50 = 0.52 >= 0.46 -> pass

    verdicts, updates = race_two(
        db,
        lambda c: floor.resolve_floor(c, "v0.1.1"),
        lambda s: s.startswith("UPDATE FLOOR_CHECKS"),
    )

    assert updates == 1, f"verdict write must execute exactly once, saw {updates}"
    assert verdicts == ["pass", "pass"]  # loser reports the settled verdict
    row = conn.execute(
        "SELECT resolved_at, verdict FROM floor_checks WHERE offspring_id='v0.1.1'"
    ).fetchone()
    assert row["resolved_at"] is not None and row["verdict"] == "pass"


def test_fail_that_cannot_trash_records_an_anomaly(tmp_path):
    """A terminal 'fail' whose guarded trash UPDATE matches NO row must not be
    silent. `UPDATE offspring SET status='trashed' WHERE id=? AND
    status='matching'` no-ops whenever the offspring's status moved out from
    under the resolver -- leaving a floor-FAILED candidate un-trashed and
    still advancing, the exact state the floor gate exists to prevent. The
    verdict is still 'fail' (contract unchanged, callers unaffected), but the
    discrepancy is now recorded on the check row."""
    conn = _connect(tmp_path)
    _seed_world(conn, deck="dOff")
    # No MATCH results seeded -> no untried deck -> the fail is TERMINAL
    # (`_next_match_deck` returns None), so the trash UPDATE is reached.
    assert floor.enqueue_floor_series(conn, "v0.1.1") == 50
    _finish_floor_games(conn, "v0.1.1", wins=10)  # 0.20 -> fail

    # Move the status out from under the guard, exactly as a concurrent writer
    # (or a caller-ordering bug) would.
    def _move(c):
        c.execute("UPDATE offspring SET status='confirming' WHERE id='v0.1.1'")
    deckdb._write(conn, _move)

    assert floor.resolve_floor(conn, "v0.1.1") == "fail"

    anomaly = floor.floor_anomaly(conn, "v0.1.1")
    assert anomaly is not None, "un-trashable fail must be observable, not silent"
    assert "fail-not-trashed" in anomaly
    assert "confirming" in anomaly  # the status actually observed
    # The offspring really was left un-trashed -- that IS the anomaly.
    assert conn.execute(
        "SELECT status FROM offspring WHERE id='v0.1.1'").fetchone()["status"] == "confirming"


def test_healthy_fail_trashes_and_records_no_anomaly(tmp_path):
    """The control for the test above: when the guard DOES match, the
    offspring is trashed and `anomaly` stays NULL -- so a non-NULL anomaly is
    a real signal, not noise every fail emits."""
    conn = _connect(tmp_path)
    _seed_world(conn, deck="dOff")
    floor.enqueue_floor_series(conn, "v0.1.1")
    _finish_floor_games(conn, "v0.1.1", wins=10)  # 0.20 -> fail

    assert floor.resolve_floor(conn, "v0.1.1") == "fail"

    assert floor.floor_anomaly(conn, "v0.1.1") is None
    assert conn.execute(
        "SELECT status FROM offspring WHERE id='v0.1.1'").fetchone()["status"] == "trashed"


# --- re-pick on floor fail (a fail blames the DECK, not the agent) ---------


def _floor_row(conn, oid="v0.1.1"):
    return conn.execute(
        "SELECT deck_id, attempt, tried_decks, verdict, games_planned "
        "FROM floor_checks WHERE offspring_id=?", (oid,)).fetchone()


def test_false_fail_repicks_next_best_deck_then_passes(tmp_path):
    """A deck that fails the floor is swapped for the offspring's next-best
    MATCH deck and re-floored; passing on the re-pick leaves the offspring
    alive with `deck_id` pinned to the deck that PASSED."""
    conn = _connect(tmp_path)
    _seed_world(conn, deck="dOff")
    # Ranking: dOff 0.90 (picked first) > dB 0.60 > dC 0.20.
    _seed_match_results(conn, "v0.1.1", {"dOff": (9, 10), "dB": (6, 10), "dC": (2, 10)})

    assert floor.enqueue_floor_series(conn, "v0.1.1") == 50
    _finish_floor_games(conn, "v0.1.1", wins=19, attempt=0)  # 0.38 -> fail
    assert floor.resolve_floor(conn, "v0.1.1") == "repick"

    off = conn.execute("SELECT status, deck_id FROM offspring WHERE id='v0.1.1'").fetchone()
    assert off["status"] == "matching"  # NOT trashed
    assert off["deck_id"] == "dB"       # next-best untried deck
    row = _floor_row(conn)
    assert (row["deck_id"], row["attempt"], row["verdict"]) == ("dB", 1, "pending")
    assert json.loads(row["tried_decks"]) == ["dOff", "dB"]
    # the re-pick's own full series is already enqueued (no wedge: there is
    # always an advancing step), and it is keyed to attempt 1 only
    assert conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='floor' AND status='pending' "
        "AND agent_version_a=?", (floor.floor_version("v0.1.1", 1),)).fetchone()[0] == 50
    assert conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='floor' AND deck_a_id='dB'"
    ).fetchone()[0] == 50
    # a repeat scheduler tick tops up 0 rather than double-enqueuing
    assert floor.enqueue_floor_series(conn, "v0.1.1") == 0
    assert floor.resolve_floor(conn, "v0.1.1") == "pending"

    _finish_floor_games(conn, "v0.1.1", wins=23, attempt=1)  # 0.46 -> pass
    assert floor.resolve_floor(conn, "v0.1.1") == "pass"
    off = conn.execute("SELECT status, deck_id FROM offspring WHERE id='v0.1.1'").fetchone()
    assert (off["status"], off["deck_id"]) == ("matching", "dB")
    assert _floor_row(conn)["deck_id"] == "dB"  # the deck that PASSED


def test_attempt_series_are_scored_independently(tmp_path):
    """Attempt 1's verdict must be computed from attempt 1's games ALONE --
    pooling the failed attempt's games with the re-pick's would make both
    verdicts meaningless."""
    conn = _connect(tmp_path)
    _seed_world(conn, deck="dOff")
    _seed_match_results(conn, "v0.1.1", {"dOff": (9, 10), "dB": (6, 10)})
    floor.enqueue_floor_series(conn, "v0.1.1")
    _finish_floor_games(conn, "v0.1.1", wins=0, attempt=0)  # 0.00 -> fail
    assert floor.resolve_floor(conn, "v0.1.1") == "repick"
    _finish_floor_games(conn, "v0.1.1", wins=23, attempt=1)  # 0.46 in isolation
    assert floor.resolve_floor(conn, "v0.1.1") == "pass"
    row = conn.execute(
        "SELECT wins, games_done, wr FROM floor_checks WHERE offspring_id='v0.1.1'"
    ).fetchone()
    # 23/50 = 0.46, not 23/100 = 0.23 (pooled with the 0-win failed attempt)
    assert (row["wins"], row["games_done"], row["wr"]) == (23, 50, 0.46)


def test_exhaustion_after_three_decks_trashes(tmp_path):
    """FLOOR_MAX_ATTEMPTS decks, all failing -> trashed on the 3rd."""
    conn = _connect(tmp_path)
    _seed_world(conn, deck="dOff")
    _seed_match_results(conn, "v0.1.1", {"dOff": (9, 10), "dB": (6, 10),
                                         "dC": (5, 10), "dD": (4, 10)})
    floor.enqueue_floor_series(conn, "v0.1.1")

    _finish_floor_games(conn, "v0.1.1", wins=10, attempt=0)
    assert floor.resolve_floor(conn, "v0.1.1") == "repick"
    _finish_floor_games(conn, "v0.1.1", wins=10, attempt=1)
    assert floor.resolve_floor(conn, "v0.1.1") == "repick"
    assert _floor_row(conn)["deck_id"] == "dC"
    _finish_floor_games(conn, "v0.1.1", wins=10, attempt=2)
    # 3 decks tried == FLOOR_MAX_ATTEMPTS: dD is untried but the budget is out
    assert floor.resolve_floor(conn, "v0.1.1") == "fail"

    off = conn.execute("SELECT status FROM offspring WHERE id='v0.1.1'").fetchone()
    assert off["status"] == "trashed"
    row = _floor_row(conn)
    assert (row["verdict"], row["attempt"]) == ("fail", 2)
    assert json.loads(row["tried_decks"]) == ["dOff", "dB", "dC"]
    assert floor.resolve_floor(conn, "v0.1.1") == "fail"  # settled, never reopened
    assert conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='floor' AND deck_a_id='dD'"
    ).fetchone()[0] == 0


def test_field_exhaustion_trashes_before_attempt_budget(tmp_path):
    """Only ONE deck in the MATCH field: nothing to re-pick, trash at once."""
    conn = _connect(tmp_path)
    _seed_world(conn, deck="dOff")
    _seed_match_results(conn, "v0.1.1", {"dOff": (9, 10)})
    floor.enqueue_floor_series(conn, "v0.1.1")
    _finish_floor_games(conn, "v0.1.1", wins=19, attempt=0)  # 0.38 -> fail
    assert floor.resolve_floor(conn, "v0.1.1") == "fail"
    off = conn.execute("SELECT status FROM offspring WHERE id='v0.1.1'").fetchone()
    assert off["status"] == "trashed"


def test_repick_ranking_matches_select_optimal_deck(tmp_path):
    """`floor._next_match_deck` inlines `loop._OFFSPRING_MATCH_RESULTS_QUERY`
    (floor.py cannot import loop.py -- loop.py imports floor). Pin the two
    rankings together so the copy cannot drift: floor's FIRST pick (nothing
    tried yet) must be exactly the deck `loop.select_optimal_deck` picks,
    including its deck-id-ascending tie-break."""
    from ptcg.factory import loop

    conn = _connect(tmp_path)
    _seed_world(conn, deck="dOff")
    # dA and dB tie at 0.60 -> deck-id ascending wins; dOff is lower.
    _seed_match_results(conn, "v0.1.1",
                        {"dOff": (3, 10), "dB": (6, 10), "dA": (6, 10)})
    picked = loop.select_optimal_deck(conn, "v0.1.1")
    assert picked == "dA"
    assert deckdb._write(conn, lambda c: floor._next_match_deck(c, "v0.1.1", [])) == picked


def test_repick_survives_concurrent_resolvers(tmp_path):
    """Interleaved-mutation receipt (.claude/rules/single-actor-worker-tests.md):
    two resolvers racing a failing verdict must produce exactly ONE re-pick --
    never two attempts, never a doubled series."""
    db = tmp_path / "t.db"
    conn = deckdb.connect(db)
    deckdb.init_db(conn)
    _seed_world(conn, deck="dOff")
    _seed_match_results(conn, "v0.1.1", {"dOff": (9, 10), "dB": (6, 10)})
    floor.enqueue_floor_series(conn, "v0.1.1")
    _finish_floor_games(conn, "v0.1.1", wins=19, attempt=0)  # 0.38 -> fail

    verdicts = []

    def _call():
        c = deckdb.connect(db)
        verdicts.append(floor.resolve_floor(c, "v0.1.1"))

    t1 = threading.Thread(target=_call)
    t2 = threading.Thread(target=_call)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # the loser observes the winner's freshly-reset 'pending' row and returns
    # 'pending' (attempt 1's series is not played yet), never a 2nd 'repick'
    assert sorted(verdicts) == ["pending", "repick"]
    row = _floor_row(conn)
    assert (row["attempt"], row["deck_id"]) == (1, "dB")
    assert conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='floor' AND agent_version_a=?",
        (floor.floor_version("v0.1.1", 1),)).fetchone()[0] == 50


def test_ensure_schema_backfills_repick_columns_on_legacy_row(tmp_path):
    """A live DB whose floor_checks predates the re-pick design: the additive
    ALTERs must land and an existing PENDING row must keep resolving (attempt
    0, unsuffixed sentinel), then re-pick normally."""
    conn = deckdb.connect(tmp_path / "legacy.db")

    def _legacy(c):
        for ddl in deckdb._DDL_STATEMENTS:
            if "floor_checks" not in ddl:
                c.execute(ddl)
        # the PRE-re-pick floor_checks shape, verbatim
        c.execute(
            "CREATE TABLE floor_checks("
            "offspring_id TEXT PRIMARY KEY, deck_id TEXT NOT NULL, "
            "games_planned INTEGER NOT NULL, games_done INTEGER NOT NULL DEFAULT 0, "
            "wins INTEGER NOT NULL DEFAULT 0, wr REAL, "
            "verdict TEXT NOT NULL DEFAULT 'pending' "
            "CHECK(verdict IN ('pending','pass','fail')), "
            "created_at TEXT NOT NULL, resolved_at TEXT)")
    deckdb._write(conn, _legacy)
    _seed_world(conn, deck="dOff")
    _seed_match_results(conn, "v0.1.1", {"dOff": (9, 10), "dB": (6, 10)})

    def _legacy_row(c):
        c.execute("INSERT INTO floor_checks(offspring_id, deck_id, games_planned, "
                  "created_at) VALUES('v0.1.1','dOff',50,'2026-08-03T00:00:00+00:00')")
    deckdb._write(conn, _legacy_row)

    floor._ensure_schema(conn)
    floor._ensure_schema(conn)  # idempotent
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(floor_checks)")}
    assert {"attempt", "tried_decks"} <= cols
    row = _floor_row(conn)
    assert (row["attempt"], row["tried_decks"]) == (0, "[]")

    # legacy row resolves on the unsuffixed attempt-0 sentinel, then re-picks
    # (tried_decks backfills from its own deck_id, so dOff is not re-tried)
    assert floor.enqueue_floor_series(conn, "v0.1.1") == 50
    _finish_floor_games(conn, "v0.1.1", wins=19, attempt=0)  # 0.38 -> fail
    assert floor.resolve_floor(conn, "v0.1.1") == "repick"
    assert json.loads(_floor_row(conn)["tried_decks"]) == ["dOff", "dB"]
