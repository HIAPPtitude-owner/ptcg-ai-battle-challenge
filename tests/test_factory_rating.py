"""Tests for `coverage.rating` = win-rate-vs-anchor (tournament T5, Anchor
Pressure slice). Bradley-Terry over mirror games is dropped: after T4's
anchor-opponent rework, every `screening` game already has `deck_b_id ==
ANCHOR_DECK_ID`, so `refresh_field_ratings` simply tallies decisive wins
over total anchor-screening games per challenger concept
(`.claude/rules/single-actor-worker-tests.md` for the column-scoped-write
shape preserved from the pre-slice BT version).
"""

from __future__ import annotations

import threading

from ptcg.factory import deckdb, rating
from ptcg.factory.anchor import ANCHOR_CONCEPT_ID, ANCHOR_DECK_ID


def _seed_concept(c, cid: str, deck_id: str, cards: str = "[]") -> None:
    c.execute("INSERT INTO concepts(id,cores,status) VALUES(?,?, 'active')", (cid, '["X"]'))
    c.execute(
        "INSERT INTO coverage(concept_id,games_played,distinct_opponents) VALUES(?,0,0)",
        (cid,),
    )
    c.execute("INSERT INTO decks(id,concept_id,cards) VALUES(?,?,?)", (deck_id, cid, cards))


def _seed_with_anchor_games(tmp_path, cid: str, deck_id: str, wins: int, losses: int, draws: int):
    """Seed challenger concept `cid` (+ its deck + coverage row) and the
    anchor concept/deck (raw rows -- no need for the real 60-card ladder
    identity CSV in a unit test; the query only needs `deck_b_id ==
    ANCHOR_DECK_ID`, no FK join on deck_b), then play `wins` decisive games
    won by the challenger, `losses` won by the anchor, and `draws` draws --
    all `purpose='screening'`, `deck_a_id=deck_id` (challenger),
    `deck_b_id=ANCHOR_DECK_ID` -- via the real T4 queue functions (enqueue ->
    claim -> record) so `coverage.games_played` is bumped exactly as it
    would be by a real runner.
    """
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)

    def _s(c):
        _seed_concept(c, cid, deck_id)
        # Anchor: no coverage row, mirroring anchor.ensure_anchor_deck's own
        # documented omission (record_result's coverage bump on a missing
        # anchor coverage row is a silent no-op).
        c.execute(
            "INSERT INTO concepts(id,cores,status) VALUES(?, '[\"anchor\"]', 'finalist')",
            (ANCHOR_CONCEPT_ID,),
        )
        c.execute(
            "INSERT INTO decks(id,concept_id,cards) VALUES(?,?,'[]')",
            (ANCHOR_DECK_ID, ANCHOR_CONCEPT_ID),
        )

    deckdb._write(db, _s)

    for winner, count in ((0, wins), (1, losses), (2, draws)):
        for _ in range(count):
            gid = deckdb.enqueue_game(
                db, deck_id, ANCHOR_DECK_ID, "v0.1", "anchor-heuristic-v0", "screening", 0.0
            )
            deckdb.claim_next_game(db, worker_pid=1)
            deckdb.record_result(db, gid, winner=winner)

    return db


def test_rating_is_wr_vs_anchor(tmp_path):
    db = _seed_with_anchor_games(tmp_path, "cA", "dA", wins=7, losses=2, draws=1)
    n = rating.refresh_field_ratings(db)
    assert n == 1
    row = db.execute(
        "SELECT rating, distinct_opponents FROM coverage WHERE concept_id='cA'"
    ).fetchone()
    assert row["rating"] == 0.7  # 7 wins / 10 total (draws in denominator, not numerator)
    assert row["distinct_opponents"] == 1


def test_non_anchor_games_excluded(tmp_path):
    # A legacy mirror-style game (deck_b is NOT the anchor) must not move
    # the rating at all -- the entire pre-slice mirror history is excluded
    # by the deck_b_id == ANCHOR_DECK_ID filter.
    db = _seed_with_anchor_games(tmp_path, "cA", "dA", wins=0, losses=0, draws=0)

    def _s(c):
        _seed_concept(c, "cB", "dB")

    deckdb._write(db, _s)

    gid = deckdb.enqueue_game(db, "dA", "dB", "v0.1", "v0.1", "screening", 0.0)
    deckdb.claim_next_game(db, worker_pid=1)
    deckdb.record_result(db, gid, winner=0)  # mirror game, deck_b != anchor

    n = rating.refresh_field_ratings(db)
    assert n == 0
    row = db.execute("SELECT rating FROM coverage WHERE concept_id='cA'").fetchone()
    assert row["rating"] is None


def test_scope_filter_respected(tmp_path):
    db = _seed_with_anchor_games(tmp_path, "cA", "dA", wins=5, losses=5, draws=0)

    def _s(c):
        _seed_concept(c, "cB", "dB")

    deckdb._write(db, _s)
    for _ in range(3):
        gid = deckdb.enqueue_game(
            db, "dB", ANCHOR_DECK_ID, "v0.1", "anchor-heuristic-v0", "screening", 0.0
        )
        deckdb.claim_next_game(db, worker_pid=1)
        deckdb.record_result(db, gid, winner=0)

    n = rating.refresh_field_ratings(db, scope_concept_ids=["cA"])
    assert n == 1
    assert db.execute("SELECT rating FROM coverage WHERE concept_id='cA'").fetchone()[0] == 0.5
    assert db.execute("SELECT rating FROM coverage WHERE concept_id='cB'").fetchone()[0] is None


def test_draw_only_concept_rates_zero(tmp_path):
    # A concept whose entire done anchor-screening history is draws must
    # rate 0.0 (a real, decisive "does not beat the anchor" measurement),
    # NOT NULL -- unlike the dropped BT model, wr semantics make a wr of
    # zero meaningful even with no decisive game.
    db = _seed_with_anchor_games(tmp_path, "cA", "dA", wins=0, losses=0, draws=3)
    n = rating.refresh_field_ratings(db)
    assert n == 1
    row = db.execute(
        "SELECT games_played, distinct_opponents, rating FROM coverage WHERE concept_id='cA'"
    ).fetchone()
    assert row["distinct_opponents"] == 1
    assert row["rating"] == 0.0
    assert row["games_played"] == 3  # record_result's count untouched by refresh


def test_rating_write_is_column_scoped(tmp_path):  # INTERLEAVED
    # A rating refresh (writes coverage.rating/distinct_opponents) must not
    # clobber a concurrent record_result's games_played bump on the same
    # coverage row, and vice versa -- both are column-scoped UPDATEs, so
    # regardless of which write wins the race for SQLite's write lock first,
    # BOTH effects must be visible once both threads have completed.
    db = _seed_with_anchor_games(tmp_path, "cA", "dA", wins=5, losses=5, draws=0)
    gid = deckdb.enqueue_game(
        db, "dA", ANCHOR_DECK_ID, "v0.1", "anchor-heuristic-v0", "screening", 0.0
    )
    deckdb.claim_next_game(db, worker_pid=999)
    before_played = db.execute(
        "SELECT games_played FROM coverage WHERE concept_id='cA'"
    ).fetchone()[0]

    db_path = tmp_path / "t.db"
    barrier = threading.Barrier(2)

    def _refresh_worker():
        conn = deckdb.connect(db_path)
        barrier.wait()
        rating.refresh_field_ratings(conn)

    def _record_worker():
        conn = deckdb.connect(db_path)
        barrier.wait()
        deckdb.record_result(conn, gid, winner=0)

    t1 = threading.Thread(target=_refresh_worker)
    t2 = threading.Thread(target=_record_worker)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    row = db.execute(
        "SELECT games_played, rating FROM coverage WHERE concept_id='cA'"
    ).fetchone()
    assert row["games_played"] == before_played + 1  # record_result's bump survived
    assert row["rating"] is not None  # refresh's write survived

    game = db.execute("SELECT status, winner FROM games WHERE id=?", (gid,)).fetchone()
    assert game["status"] == "done" and game["winner"] == 0
