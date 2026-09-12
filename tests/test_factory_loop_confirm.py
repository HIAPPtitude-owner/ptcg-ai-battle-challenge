"""Tests for the CONFIRM step -- 200-game series vs baseline, TRASHED/SURVIVOR
verdict (tournament T14).

`enqueue_confirm_series` enqueues `purpose='confirm'` games of the
offspring's optimal deck+agent (`offspring.deck_id`, set by T13's
`select_optimal_deck`) vs the current baseline, mirrored (same deck on each
side, same fixed-side convention as MATCH: `agent_version_a` is always the
offspring). It is resumable -- it counts EXISTING `confirm` games already
enqueued for the offspring and enqueues only the shortfall against
`n_games`, rather than restarting or double-enqueuing the series (required
for T16's crash-mid-series resume behavior; see the JUDGMENT CALL docstring
in `loop.py`). `resolve_confirm` reads `done` `confirm` games for the
offspring and, once `n_games` of them are `done`, resolves the verdict: win
rate `< 0.50` -> `trashed`, `>= 0.50` -> `survivor`.

Arithmetic hand-verified (`.claude/rules/plan-test-arithmetic-sanity.md`):
`99 / 200 = 0.495` (< 0.50 -> trashed), `100 / 200 = 0.500` (>= 0.50 ->
survivor), `200 - 100 = 100` (resume shortfall).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from ptcg.factory import deckdb, loop, loop_state


def _seed(tmp_path: Path, offspring_status: str = "matching", set_deck: bool = True):
    """A deckdb with a founding `v0.1` baseline on a deck OUTSIDE the field
    (`cBase`/`dBase`) and one offspring row parented to it on its own deck
    (`cOff`/`dOff`, the deck `select_optimal_deck` would have picked in
    T13), `status=offspring_status`. Mirrors the `_seed` fixture convention
    already established in `tests/test_factory_loop_match.py`.
    """
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)

    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cBase','[\"Z\"]','untested')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dBase','cBase','[1]')")
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cOff','[\"Y\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dOff','cOff','[1]')")

    deckdb._write(db, _s)
    loop_state.set_founding_baseline(db, "dBase", loop.FOUNDING_AGENT_CONFIG)
    offspring_id = loop_state.next_offspring_version(db)
    loop_state.insert_offspring(db, offspring_id, "{}", "w.json")
    if set_deck:
        loop.set_offspring_deck(db, offspring_id, "dOff")
    loop_state.set_offspring_status(db, offspring_id, offspring_status)
    return db, offspring_id


def _play_confirm_series(db, offspring_id, baseline_version, deck_id, offspring_wins, total):
    """Enqueue + claim + record `total` `purpose='confirm'` games on
    `deck_id` (offspring as `agent_version_a`, baseline as
    `agent_version_b`), with the first `offspring_wins` decided for the
    offspring (`winner=0`) and the rest for the baseline (`winner=1`).
    Mirrors `test_factory_loop_match.py`'s `_play_match_series`."""
    for i in range(total):
        deckdb.enqueue_game(db, deck_id, deck_id, offspring_id, baseline_version, purpose="confirm")
        row = deckdb.claim_next_game(db, worker_pid=1)
        winner = 0 if i < offspring_wins else 1
        deckdb.record_result(db, row["id"], winner)


def _pass_floor(conn, offspring_id, deck_id="dOff"):
    """Seed a passing `floor_checks` row directly (bypassing
    `floor.enqueue_floor_series`/`resolve_floor`'s own game-playing path --
    CONFIRM-layer tests only need the RESOLVED verdict `enqueue_confirm_series`
    reads, not the floor series itself)."""
    from ptcg.factory import floor

    floor._ensure_schema(conn)

    def _apply(c):
        c.execute(
            "INSERT INTO floor_checks(offspring_id, deck_id, games_planned, "
            "games_done, wins, wr, verdict, created_at, resolved_at) "
            "VALUES(?,?,50,50,25,0.5,'pass',?,?)",
            (offspring_id, deck_id, "2026-08-03T00:00:00+00:00", "2026-08-03T00:00:00+00:00"),
        )

    deckdb._write(conn, _apply)


def _offspring_with_confirm(tmp_path: Path, wins: int, games: int):
    """Seed an offspring already at `status='confirming'` with `games`
    `done` `purpose='confirm'` games, `wins` of them decided for the
    offspring. Returns `(db, offspring_id)` -- diverges from the plan's
    pseudocode hardcoded `"off-1"` id (real ids come from
    `loop_state.next_offspring_version`, e.g. `v0.1.1`); plan code is
    reference, not gospel (global CLAUDE.md -> Plan Writing & Dispatch).
    """
    db, off_id = _seed(tmp_path, offspring_status="confirming", set_deck=True)
    baseline = loop_state.current_baseline(db)
    _play_confirm_series(db, off_id, baseline["version"], "dOff", offspring_wins=wins, total=games)
    return db, off_id


# --- resolve_confirm ---------------------------------------------------------


def test_confirm_below_half_trashes(tmp_path):
    db, off_id = _offspring_with_confirm(tmp_path, wins=99, games=200)  # 99/200 = 0.495 < 0.50

    assert loop.resolve_confirm(db, off_id) == "trashed"
    row = db.execute("SELECT status FROM offspring WHERE id=?", (off_id,)).fetchone()
    assert row["status"] == "trashed"


def test_confirm_at_half_survives(tmp_path):
    db, off_id = _offspring_with_confirm(tmp_path, wins=100, games=200)  # 100/200 = 0.500 >= 0.50

    assert loop.resolve_confirm(db, off_id) == "survivor"
    row = db.execute("SELECT status FROM offspring WHERE id=?", (off_id,)).fetchone()
    assert row["status"] == "survivor"


def test_resolve_confirm_not_done_returns_current_status_unchanged(tmp_path):
    db, off_id = _offspring_with_confirm(tmp_path, wins=80, games=150)  # only 150 of 200 done

    result = loop.resolve_confirm(db, off_id)

    assert result == "confirming"
    row = db.execute("SELECT status FROM offspring WHERE id=?", (off_id,)).fetchone()
    assert row["status"] == "confirming"


def test_resolve_confirm_idempotent_once_resolved(tmp_path):
    db, off_id = _offspring_with_confirm(tmp_path, wins=100, games=200)
    first = loop.resolve_confirm(db, off_id)

    second = loop.resolve_confirm(db, off_id)  # already resolved -- must not error or flip

    assert first == "survivor"
    assert second == "survivor"


def test_resolve_confirm_survives_concurrent_calls(tmp_path):
    # INTERLEAVED (Pattern INTERLEAVED-TEST / .claude/rules/single-actor-worker-tests.md).
    # Mid-task correction (T14): the guard-check + status-transition in
    # `resolve_confirm` must be race-safe -- two concurrent calls on the SAME
    # fully-done offspring must not both perform the transition UPDATE. This
    # mirrors T13's `enqueue_match_games` review finding (unlocked read ->
    # act -> unconditional write is this repo's TOCTOU class) and reuses the
    # exact threading.Barrier idiom already established in
    # `test_factory_census.py::test_promote_proven_singles_survives_concurrent_calls`.
    # Discriminates: the pre-fix (unlocked-read) shape lets both threads pass
    # the `status not in (trashed, survivor)` guard before either commits its
    # UPDATE, producing 2 UPDATE statements; the fixed shape (whole guard +
    # read + write inside one `deckdb._write` BEGIN IMMEDIATE txn) forces
    # thread B's own guard-read to happen strictly after thread A's commit,
    # so thread B observes the already-resolved status and issues 0 UPDATEs
    # -- exactly 1 UPDATE total.
    db, off_id = _offspring_with_confirm(tmp_path, wins=100, games=200)
    db_path = tmp_path / "t.db"
    barrier = threading.Barrier(2)
    update_count = 0
    count_lock = threading.Lock()

    def _trace(sql: str) -> None:
        nonlocal update_count
        if sql.strip().upper().startswith("UPDATE OFFSPRING SET STATUS"):
            with count_lock:
                update_count += 1

    results: dict[str, str] = {}

    def _call(name: str) -> None:
        conn = deckdb.connect(db_path)
        conn.set_trace_callback(_trace)
        barrier.wait()
        results[name] = loop.resolve_confirm(conn, off_id)

    t1 = threading.Thread(target=_call, args=("t1",))
    t2 = threading.Thread(target=_call, args=("t2",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert results["t1"] == "survivor"
    assert results["t2"] == "survivor"
    assert update_count == 1  # exactly one real transition across both racers
    row = db.execute("SELECT status FROM offspring WHERE id=?", (off_id,)).fetchone()
    assert row["status"] == "survivor"


def test_resolve_confirm_no_offspring_row_raises(tmp_path):
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)

    with pytest.raises(ValueError):
        loop.resolve_confirm(db, "nonexistent")


# --- enqueue_confirm_series ---------------------------------------------------


def test_enqueue_confirm_series_arithmetic_and_status_transition(tmp_path):
    db, off_id = _seed(tmp_path, offspring_status="matching", set_deck=True)
    _pass_floor(db, off_id)

    enqueued = loop.enqueue_confirm_series(db, off_id)

    assert enqueued == 200  # hand-verified: n_games default (CONFIRM_GAMES) - 0 existing = 200
    assert deckdb.pending_count(db, purpose="confirm") == 200
    row = db.execute("SELECT status FROM offspring WHERE id=?", (off_id,)).fetchone()
    assert row["status"] == "confirming"


def test_enqueue_confirm_series_resumes_partial_shortfall(tmp_path):
    db, off_id = _seed(tmp_path, offspring_status="confirming", set_deck=True)
    _pass_floor(db, off_id)
    baseline = loop_state.current_baseline(db)
    # Simulate a crash after only 100 of the eventual 200 confirm games were
    # ever enqueued (mirrors T16's resume-test scenario in the plan).
    for _ in range(100):
        deckdb.enqueue_game(db, "dOff", "dOff", off_id, baseline["version"], purpose="confirm")

    enqueued = loop.enqueue_confirm_series(db, off_id)

    assert enqueued == 100  # hand-verified: 200 - 100 = 100, tops up rather than restarting
    assert deckdb.pending_count(db, purpose="confirm") == 200  # total across both calls


def test_enqueue_confirm_series_guarded_against_double_call(tmp_path):
    db, off_id = _seed(tmp_path, offspring_status="matching", set_deck=True)
    _pass_floor(db, off_id)

    first = loop.enqueue_confirm_series(db, off_id)
    second = loop.enqueue_confirm_series(db, off_id)  # already topped up to 200

    assert first == 200
    assert second == 0
    assert deckdb.pending_count(db, purpose="confirm") == 200  # not doubled


def test_enqueue_confirm_series_noop_before_matching(tmp_path):
    db, off_id = _seed(tmp_path, offspring_status="queued_for_match", set_deck=False)

    enqueued = loop.enqueue_confirm_series(db, off_id)

    assert enqueued == 0
    assert deckdb.pending_count(db, purpose="confirm") == 0


def test_enqueue_confirm_series_noop_once_resolved(tmp_path):
    db, off_id = _seed(tmp_path, offspring_status="survivor", set_deck=True)

    enqueued = loop.enqueue_confirm_series(db, off_id)

    assert enqueued == 0
    assert deckdb.pending_count(db, purpose="confirm") == 0


def test_enqueue_confirm_series_survives_concurrent_calls(tmp_path):  # INTERLEAVED
    # Same TOCTOU class as `enqueue_match_games`'s concurrent test (T13
    # review finding, sibling occurrence flagged in this function's own
    # docstring -- `.claude/rules/single-actor-worker-tests.md`): two
    # concurrent callers racing the existing-count read must not both
    # compute the same `remaining` shortfall and double-enqueue. Mirrors
    # `census.py`'s own pending-aware concurrent test
    # (`test_schedule_concurrent_calls_no_over_enqueue`) -- resumability
    # (topping up only the true residual shortfall) must survive the same
    # fix that makes concurrent calls race-safe.
    db, off_id = _seed(tmp_path, offspring_status="matching", set_deck=True)
    _pass_floor(db, off_id)
    db_path = tmp_path / "t.db"
    barrier = threading.Barrier(2)
    results: dict[str, int] = {}

    def _call(name):
        conn = deckdb.connect(db_path)
        barrier.wait()
        results[name] = loop.enqueue_confirm_series(conn, off_id, n_games=50)

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
    assert deckdb.pending_count(db, purpose="confirm") == 50
    row = db.execute("SELECT status FROM offspring WHERE id=?", (off_id,)).fetchone()
    assert row["status"] == "confirming"


def test_confirm_refused_without_floor_pass(tmp_path):
    # No floor_checks row at all -- the invariant "candidate may not enter
    # CONFIRM without the floor" (`.claude/rules/single-actor-worker-tests.md`
    # toctou-guard-in-step-functions class) enforced INSIDE the step
    # function's own transaction, not only at the scheduler.
    db, off_id = _seed(tmp_path, offspring_status="matching", set_deck=True)

    enqueued = loop.enqueue_confirm_series(db, off_id)

    assert enqueued == 0
    assert deckdb.pending_count(db, purpose="confirm") == 0
    row = db.execute("SELECT status FROM offspring WHERE id=?", (off_id,)).fetchone()
    assert row["status"] == "matching"  # never advanced to 'confirming'


def test_confirm_refused_on_floor_fail(tmp_path):
    db, off_id = _seed(tmp_path, offspring_status="matching", set_deck=True)
    from ptcg.factory import floor

    floor._ensure_schema(db)

    def _apply(c):
        c.execute(
            "INSERT INTO floor_checks(offspring_id, deck_id, games_planned, "
            "games_done, wins, wr, verdict, created_at, resolved_at) "
            "VALUES(?,?,50,50,20,0.4,'fail',?,?)",
            (off_id, "dOff", "2026-08-03T00:00:00+00:00", "2026-08-03T00:00:00+00:00"),
        )

    deckdb._write(db, _apply)

    enqueued = loop.enqueue_confirm_series(db, off_id)

    assert enqueued == 0
    assert deckdb.pending_count(db, purpose="confirm") == 0
    row = db.execute("SELECT status FROM offspring WHERE id=?", (off_id,)).fetchone()
    assert row["status"] == "matching"


def test_enqueue_confirm_series_missing_deck_id_raises(tmp_path):
    db, off_id = _seed(tmp_path, offspring_status="matching", set_deck=False)

    with pytest.raises(RuntimeError):
        loop.enqueue_confirm_series(db, off_id)


def test_enqueue_confirm_series_no_offspring_row_raises(tmp_path):
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)

    with pytest.raises(ValueError):
        loop.enqueue_confirm_series(db, "nonexistent")


def test_floor_gate_read_is_inside_the_transaction(tmp_path):
    """INTERLEAVED (`.claude/rules/single-actor-worker-tests.md`,
    `toctou-guard-in-step-functions`): a concurrent writer must not be able to
    flip `floor_checks.verdict` away from 'pass' INSIDE
    `enqueue_confirm_series`'s decision window (between its floor read and its
    series INSERTs), nor land a flip that leaves a PARTIAL series behind.

    The flip is fired from a second connection at the offspring SELECT -- the
    first statement INSIDE the open transaction and strictly before the floor
    read -- and the trace callback then holds there briefly so the flipper
    genuinely reaches SQLite while the window is open. (The trigger cannot be
    `BEGIN IMMEDIATE` itself: sqlite3 fires the trace callback BEFORE the
    traced statement executes, so at that point the write lock is not yet
    held, the flipper wins it outright, and the test would merely re-prove the
    `flips-before-the-window` control below.)

    Fail-power: with the read-decide-act sequence inside one `BEGIN IMMEDIATE`
    transaction the flipper blocks on the write lock (busy_timeout) until the
    enqueue COMMITs, so the enqueue reads 'pass' and writes a COMPLETE series
    (`CONFIRM_GAMES`, never a partial count). Remove the transaction and the
    flip lands before the floor read instead, the gate observes 'fail', and
    the enqueue returns 0 -- the assertions below invert. RED receipt in this
    slice's pass2-fixwave-report.md.
    """
    from ptcg.factory import floor

    db, off_id = _seed(tmp_path, offspring_status="matching", set_deck=True)
    _pass_floor(db, off_id)
    db_path = tmp_path / "t.db"

    flip_done = threading.Event()
    window_open = threading.Event()

    def _flip() -> None:
        c = deckdb.connect(db_path)
        window_open.wait(timeout=10)

        def _apply(cc):
            cc.execute(
                "UPDATE floor_checks SET verdict='fail' WHERE offspring_id=?", (off_id,)
            )

        deckdb._write(c, _apply)  # blocks on the write lock until the enqueue commits
        flip_done.set()

    enqueue_conn = deckdb.connect(db_path)
    fired = []

    def _trace(sql: str) -> None:
        # First statement INSIDE the held transaction (BEGIN IMMEDIATE has
        # already returned, so the write lock is ours) and strictly before the
        # floor read -> open the window and give the flipper real time to
        # attempt its write and block on that lock.
        if not fired and sql.strip().upper().startswith("SELECT STATUS, DECK_ID FROM OFFSPRING"):
            fired.append(1)
            window_open.set()
            time.sleep(0.25)

    flipper = threading.Thread(target=_flip)
    flipper.start()
    enqueue_conn.set_trace_callback(_trace)
    enqueued = loop.enqueue_confirm_series(enqueue_conn, off_id)
    enqueue_conn.set_trace_callback(None)
    flipper.join(timeout=20)

    assert fired, "trace never saw the transaction open -- window was not exercised"
    assert flip_done.is_set(), "flipper never completed"
    assert window_open.is_set()  # overlap receipt: flip was live during the window

    # The gate decided on the 'pass' it read, and the series is ALL-OR-NOTHING.
    assert enqueued == loop.CONFIRM_GAMES
    assert deckdb.pending_count(db, purpose="confirm") == loop.CONFIRM_GAMES
    # The flip did land -- just strictly after the decision, never inside it.
    assert floor.floor_status(db, off_id)[0] == "fail"


def test_confirm_refused_when_floor_flips_before_the_window(tmp_path):
    """The control for the test above: when the same flip commits BEFORE
    `enqueue_confirm_series` opens its transaction, the gate must observe it
    and refuse -- so the test above is pinning ordering, not an unconditional
    'the enqueue always wins'."""
    from ptcg.factory import floor

    db, off_id = _seed(tmp_path, offspring_status="matching", set_deck=True)
    _pass_floor(db, off_id)

    def _apply(c):
        c.execute("UPDATE floor_checks SET verdict='fail' WHERE offspring_id=?", (off_id,))

    deckdb._write(db, _apply)

    assert loop.enqueue_confirm_series(db, off_id) == 0
    assert deckdb.pending_count(db, purpose="confirm") == 0
    assert floor.floor_status(db, off_id)[0] == "fail"
    row = db.execute("SELECT status FROM offspring WHERE id=?", (off_id,)).fetchone()
    assert row["status"] == "matching"
