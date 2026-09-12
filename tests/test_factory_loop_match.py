"""Tests for the MATCH step -- offspring vs top-30 field, pick optimal deck
(tournament T13).

`enqueue_match_games` enqueues `games_per_deck` `purpose='match'` games of
the offspring agent vs the current baseline agent, mirrored on each of the
top-`top_n` `active` decks by `coverage.rating` (best-rated first).
`select_optimal_deck` then reads the `done` `match` games for one offspring
and records the deck with the highest offspring win share on the offspring
row.

Arithmetic hand-verified (`.claude/rules/plan-test-arithmetic-sanity.md`):
`min(30, n_active) * 15` -- `min(30, 35) * 15 = 450`, `min(30, 5) * 15 = 75`.
Win-share picks: `6/15 = 0.400`, `10/15 = 0.6666...`, `9/15 = 0.600` -- max is
the `10/15` deck. Tie-break case: two decks both at `10/15 = 0.6666...`.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from ptcg.factory import deckdb, loop, loop_state


def _seed(tmp_path: Path, n_active: int) -> tuple:
    """A deckdb with a founding `v0.1` baseline on a deck OUTSIDE the rated
    field (`cBase`/`dBase`, `status='untested'`, no `coverage` row -- so it
    is never itself eligible as a MATCH field deck), plus `n_active` active,
    rated field concepts/decks (`c000`.. descending rating, `c000` best) and
    one offspring row parented to that baseline, `status='queued_for_match'`
    (T12's own end state) -- mirrors the `_seed_founding`/`_seed_with_games`
    fixture conventions already established in `tests/test_factory_loop_train.py`
    / `tests/test_factory_rating.py` (tiny single-card `cards` lists -- deck
    legality is not under test here).
    """
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)

    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cBase','[\"Z\"]','untested')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dBase','cBase','[1]')")
        for i in range(n_active):
            cid, did = f"c{i:03d}", f"d{i:03d}"
            c.execute("INSERT INTO concepts(id,cores,status) VALUES(?,?,'active')", (cid, '["X"]'))
            c.execute("INSERT INTO decks(id,concept_id,cards) VALUES(?,?,?)", (did, cid, "[1]"))
            c.execute(
                "INSERT INTO coverage(concept_id,games_played,distinct_opponents,rating) "
                "VALUES(?,15,1,?)",
                (cid, float(1000 - i)),  # descending: c000 best-rated, c001 next, ...
            )

    deckdb._write(db, _s)
    loop_state.set_founding_baseline(db, "dBase", loop.FOUNDING_AGENT_CONFIG)
    offspring_id = loop_state.next_offspring_version(db)
    loop_state.insert_offspring(db, offspring_id, "{}", "w.json")
    loop_state.set_offspring_status(db, offspring_id, "queued_for_match")
    return db, offspring_id


def _play_match_series(db, offspring_id, baseline_version, deck_id, offspring_wins, total):
    """Enqueue + claim + record `total` `purpose='match'` games on `deck_id`
    (offspring as `agent_version_a`, baseline as `agent_version_b`), with the
    first `offspring_wins` decided for the offspring (`winner=0`) and the
    rest for the baseline (`winner=1`). Nothing else is pending in these
    tests, so `claim_next_game` always claims the game just enqueued."""
    for i in range(total):
        deckdb.enqueue_game(db, deck_id, deck_id, offspring_id, baseline_version, purpose="match")
        row = deckdb.claim_next_game(db, worker_pid=1)
        winner = 0 if i < offspring_wins else 1
        deckdb.record_result(db, row["id"], winner)


def test_enqueue_match_games_top30_arithmetic(tmp_path):
    db, off_id = _seed(tmp_path, 35)

    enqueued = loop.enqueue_match_games(db, off_id)

    assert enqueued == 450  # hand-verified: min(30, 35) * 15 = 450
    assert deckdb.pending_count(db, purpose="match") == 450
    row = db.execute("SELECT status FROM offspring WHERE id=?", (off_id,)).fetchone()
    assert row["status"] == "matching"


def test_enqueue_match_games_fewer_than_top_n(tmp_path):
    db, off_id = _seed(tmp_path, 5)

    enqueued = loop.enqueue_match_games(db, off_id)

    assert enqueued == 75  # hand-verified: min(30, 5) * 15 = 75
    assert deckdb.pending_count(db, purpose="match") == 75


def test_enqueue_match_games_selects_best_rated_decks(tmp_path):
    db, off_id = _seed(tmp_path, 35)

    loop.enqueue_match_games(db, off_id)

    deck_ids = {
        r["deck_a_id"]
        for r in db.execute("SELECT DISTINCT deck_a_id FROM games WHERE purpose='match'").fetchall()
    }
    expected = {f"d{i:03d}" for i in range(30)}  # c000..c029 hold the 30 highest ratings
    assert deck_ids == expected


def test_enqueue_match_games_guarded_against_double_call(tmp_path):
    db, off_id = _seed(tmp_path, 5)

    first = loop.enqueue_match_games(db, off_id)
    second = loop.enqueue_match_games(db, off_id)  # status already advanced to 'matching'

    assert first == 75
    assert second == 0
    assert deckdb.pending_count(db, purpose="match") == 75  # not doubled


def test_enqueue_match_games_survives_concurrent_calls(tmp_path):  # INTERLEAVED
    # Two concurrent `enqueue_match_games` callers race the same
    # status-guard-read -> field-select -> bulk-insert -> status-transition
    # window (T13 review finding, `.claude/rules/single-actor-worker-tests.md`).
    # Pre-fix, both threads can pass the `status == 'queued_for_match'` guard
    # before either commits its status-transition UPDATE, producing 150
    # enqueued games (2x75) instead of 75 -- the same TOCTOU class as the
    # fixed 5/day submission-cap race (commit 178043b) and mirrors
    # `census.py`'s own adversarial test
    # (`test_schedule_concurrent_calls_no_over_enqueue`).
    db, off_id = _seed(tmp_path, 5)
    db_path = tmp_path / "t.db"
    barrier = threading.Barrier(2)
    results: dict[str, int] = {}

    def _call(name):
        conn = deckdb.connect(db_path)
        barrier.wait()
        results[name] = loop.enqueue_match_games(conn, off_id)

    t1 = threading.Thread(target=_call, args=("t1",))
    t2 = threading.Thread(target=_call, args=("t2",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    # Exactly one caller fills the whole field (75 games: min(30,5)*15,
    # hand-verified above); the other's guard-read observes the
    # already-advanced 'matching' status and enqueues 0. Never 150.
    assert sorted(results.values()) == [0, 75]
    assert deckdb.pending_count(db, purpose="match") == 75
    row = db.execute("SELECT status FROM offspring WHERE id=?", (off_id,)).fetchone()
    assert row["status"] == "matching"


def test_enqueue_match_games_no_offspring_row_raises(tmp_path):
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)

    with pytest.raises(ValueError):
        loop.enqueue_match_games(db, "nonexistent")


def test_select_optimal_deck_picks_highest_win_share(tmp_path):
    db, off_id = _seed(tmp_path, 3)
    baseline = loop_state.current_baseline(db)
    # win shares: d000=6/15=0.400, d001=10/15=0.667 (best), d002=9/15=0.600
    _play_match_series(db, off_id, baseline["version"], "d000", offspring_wins=6, total=15)
    _play_match_series(db, off_id, baseline["version"], "d001", offspring_wins=10, total=15)
    _play_match_series(db, off_id, baseline["version"], "d002", offspring_wins=9, total=15)

    chosen = loop.select_optimal_deck(db, off_id)

    assert chosen == "d001"
    row = db.execute("SELECT deck_id FROM offspring WHERE id=?", (off_id,)).fetchone()
    assert row["deck_id"] == "d001"


def test_select_optimal_deck_tie_break_lowest_deck_id(tmp_path):
    db, off_id = _seed(tmp_path, 3)
    baseline = loop_state.current_baseline(db)
    # win shares: d002=10/15=0.667, d000=10/15=0.667 (tied with d002), d001=5/15=0.333
    _play_match_series(db, off_id, baseline["version"], "d002", offspring_wins=10, total=15)
    _play_match_series(db, off_id, baseline["version"], "d000", offspring_wins=10, total=15)
    _play_match_series(db, off_id, baseline["version"], "d001", offspring_wins=5, total=15)

    chosen = loop.select_optimal_deck(db, off_id)

    assert chosen == "d000"  # tie broken by lowest deck_id


def test_select_optimal_deck_raises_when_no_match_games(tmp_path):
    db, off_id = _seed(tmp_path, 3)

    with pytest.raises(ValueError):
        loop.select_optimal_deck(db, off_id)
