"""Tests for the SQLite game queue (tournament T4): enqueue, ATOMIC claim,
and result recording.

Includes the adversarial two-worker double-claim race required by
`.claude/rules/single-actor-worker-tests.md` — a single pending game, two
real threads with independent connections racing `claim_next_game` via a
`threading.Barrier` to maximize contention, asserting exactly one winner.
"""

from __future__ import annotations

import threading

import pytest

from ptcg.factory import deckdb


def _seed(tmp_path):
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)

    def _s(c):
        for cid in ("cA", "cB"):
            c.execute(
                "INSERT INTO concepts(id,cores,status) VALUES(?,?, 'active')", (cid, '["X"]')
            )
            c.execute(
                "INSERT INTO coverage(concept_id,games_played,distinct_opponents) VALUES(?,0,0)",
                (cid,),
            )
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dA','cA','[]')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dB','cB','[]')")

    deckdb._write(db, _s)
    return db


def test_enqueue_and_claim_and_record(tmp_path):
    db = _seed(tmp_path)
    gid = deckdb.enqueue_game(db, "dA", "dB", "v0.1", "v0.1", "screening", 0.9)

    row = deckdb.claim_next_game(db, worker_pid=111)
    assert row is not None
    assert row["id"] == gid
    assert row["status"] == "claimed"
    assert row["worker_pid"] == 111

    assert deckdb.claim_next_game(db, worker_pid=222) is None  # nothing left pending

    deckdb.record_result(db, gid, winner=0)
    done = db.execute("SELECT status,winner FROM games WHERE id=?", (gid,)).fetchone()
    assert done["status"] == "done"
    assert done["winner"] == 0
    assert db.execute("SELECT games_played FROM coverage WHERE concept_id='cA'").fetchone()[0] == 1
    assert db.execute("SELECT games_played FROM coverage WHERE concept_id='cB'").fetchone()[0] == 1


def test_pending_count_filters_by_purpose(tmp_path):
    db = _seed(tmp_path)
    deckdb.enqueue_game(db, "dA", "dB", "v0.1", "v0.1", "screening", 0.5)
    deckdb.enqueue_game(db, "dA", "dB", "v0.1", "v0.1", "confirm", 0.5)
    assert deckdb.pending_count(db) == 2
    assert deckdb.pending_count(db, purpose="screening") == 1
    assert deckdb.pending_count(db, purpose="confirm") == 1
    assert deckdb.pending_count(db, purpose="crown") == 0
    deckdb.claim_next_game(db, worker_pid=1)
    assert deckdb.pending_count(db) == 1


def test_record_result_never_claimed_raises_and_no_bump(tmp_path):
    """A pending (never-claimed) game must NOT be recordable — loud raise,
    coverage untouched, status/winner untouched (txn rollback)."""
    db = _seed(tmp_path)
    gid = deckdb.enqueue_game(db, "dA", "dB", "v0.1", "v0.1", "screening", 0.5)
    with pytest.raises(ValueError, match="not 'claimed'"):
        deckdb.record_result(db, gid, winner=0)
    row = db.execute("SELECT status,winner FROM games WHERE id=?", (gid,)).fetchone()
    assert row["status"] == "pending"
    assert row["winner"] is None
    assert db.execute("SELECT games_played FROM coverage WHERE concept_id='cA'").fetchone()[0] == 0
    assert db.execute("SELECT games_played FROM coverage WHERE concept_id='cB'").fetchone()[0] == 0


def test_record_result_already_done_raises_and_coverage_stays(tmp_path):
    """Happy path claim->record works exactly once; re-recording a done game
    raises, does NOT double-bump coverage, and does NOT overwrite winner."""
    db = _seed(tmp_path)
    gid = deckdb.enqueue_game(db, "dA", "dB", "v0.1", "v0.1", "screening", 0.5)
    assert deckdb.claim_next_game(db, worker_pid=7) is not None
    deckdb.record_result(db, gid, winner=1)  # happy path: claimed -> done
    assert db.execute("SELECT games_played FROM coverage WHERE concept_id='cA'").fetchone()[0] == 1
    with pytest.raises(ValueError, match="not 'claimed'"):
        deckdb.record_result(db, gid, winner=0)
    row = db.execute("SELECT status,winner FROM games WHERE id=?", (gid,)).fetchone()
    assert row["status"] == "done"
    assert row["winner"] == 1  # first result stands; not silently overwritten
    assert db.execute("SELECT games_played FROM coverage WHERE concept_id='cA'").fetchone()[0] == 1
    assert db.execute("SELECT games_played FROM coverage WHERE concept_id='cB'").fetchone()[0] == 1


def test_two_workers_never_double_claim(tmp_path):  # ADVERSARIAL multi-actor
    db = _seed(tmp_path)
    deckdb.enqueue_game(db, "dA", "dB", "v0.1", "v0.1", "screening", 0.5)  # exactly one pending game

    results: dict[int, object] = {}
    barrier = threading.Barrier(2)

    def worker(pid):
        conn = deckdb.connect(tmp_path / "t.db")
        barrier.wait()
        results[pid] = deckdb.claim_next_game(conn, worker_pid=pid)

    t1 = threading.Thread(target=worker, args=(1,))
    t2 = threading.Thread(target=worker, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    claimed = [r for r in results.values() if r is not None]
    assert len(claimed) == 1, "exactly one worker may claim the single pending game"
