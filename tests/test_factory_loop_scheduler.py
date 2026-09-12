"""Tests for the loop scheduler + crash-safe resume (tournament T16).

`loop_scheduler.loop_tick` is ONE idempotent advance of the Baseline-Challenge
Loop driven purely by DB state (no in-memory state -- spec Resumability), so a
fresh-connection call resumes exactly where the last committed transaction left
off. This module pins:

- the census-incomplete branch (schedule + throttled refresh, no promote/activate);
- the carry-forward-mandated post-census order refresh -> promote_proven_singles
  -> activate_pair_concepts (`.claude/rules/...` carry-forward #1 -- without
  promote, pair activation is a production no-op);
- BOOTSTRAP founding of `v0.1` on the best census deck;
- the plan's Step-1 RESUME test (mid-CONFIRM 100/200 done -> tops up only the
  remaining 100 on a FRESH connection, never restarts) and the plan's Step-1
  INTERLEAVED test (two ticks racing never double-enqueue a series);
- orphaned-`claimed` reclaim + the poison-game requeue cap / dead-letter with a
  DB-persisted error (carry-forward #2), incl. an interleaved two-actor receipt;
- the T15-review survivor-exclusion carry-forward (a crowned ex-survivor is
  invisible to the scheduler's CROWN logic).

Arithmetic hand-verified (`.claude/rules/plan-test-arithmetic-sanity.md`):
`200 - 100 = 100` (resume shortfall), `min(30, 3) * 15 = 45` (match field).
"""
from __future__ import annotations

import datetime as dt
import random
import threading
from pathlib import Path

import pytest

from ptcg.factory import (
    anchor,
    census,
    deckdb,
    floor,
    loop,
    loop_scheduler,
    loop_state,
    netcheck,
    rating,
)

_NOW = dt.datetime(2026, 7, 24, 12, 0, 0, tzinfo=dt.timezone.utc)
_SEED = 20260724


# --- fixtures ---------------------------------------------------------------


def _fresh_db(tmp_path: Path):
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)
    return db


def _seed_singles(db, n: int, games_played: int, rated: bool, status: str = "untested"):
    """Insert `n` single-core concepts (`s000`..) + `shell_variant=0` decks +
    coverage rows (`games_played`, `rating` descending so `s000` is best when
    rated). Distinct core NAMES so pair activation has real 2-core combos."""

    def _s(c):
        for i in range(n):
            cid, did, core = f"s{i:03d}", f"ds{i:03d}", f"CORE{i:03d}"
            c.execute(
                "INSERT INTO concepts(id,cores,status) VALUES(?,?,?)",
                (cid, f'["{core}"]', status),
            )
            c.execute("INSERT INTO decks(id,concept_id,cards) VALUES(?,?,'[1]')", (did, cid))
            c.execute(
                "INSERT INTO coverage(concept_id,games_played,distinct_opponents,rating) "
                "VALUES(?,?,1,?)",
                (cid, games_played, float(1000 - i) if rated else None),
            )

    deckdb._write(db, _s)


def _seed_baseline_and_offspring(tmp_path: Path, offspring_status: str, set_deck: bool = True):
    """A founding `v0.1` baseline on a deck OUTSIDE the field (`dBase`) + one
    offspring on `dOff`. Mirrors `tests/test_factory_loop_confirm.py::_seed`.
    The concepts carry NO coverage rows, so `census_complete` is vacuously
    True (no single-core concept sits below the floor) -- puts `loop_tick`
    straight into the post-census drive branch."""
    db = _fresh_db(tmp_path)

    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cBase','[\"Z\"]','untested')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dBase','cBase','[1]')")
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cOff','[\"Y\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dOff','cOff','[1]')")

    deckdb._write(db, _s)
    loop_state.set_founding_baseline(db, "dBase", loop.FOUNDING_AGENT_CONFIG)
    off_id = loop_state.next_offspring_version(db)
    loop_state.insert_offspring(db, off_id, "{}", "w.json")
    if set_deck:
        loop.set_offspring_deck(db, off_id, "dOff")
    loop_state.set_offspring_status(db, off_id, offspring_status)
    return db, off_id


def _play_confirm(db, off_id, baseline_version, deck_id, offspring_wins, total):
    """Enqueue+claim+record `total` done `confirm` games (offspring=a)."""
    for i in range(total):
        deckdb.enqueue_game(db, deck_id, deck_id, off_id, baseline_version, purpose="confirm")
        row = deckdb.claim_next_game(db, worker_pid=1)
        deckdb.record_result(db, row["id"], 0 if i < offspring_wins else 1)


def _backdate_claim(db, game_id, now, seconds_ago):
    stale = (now - dt.timedelta(seconds=seconds_ago)).isoformat()
    deckdb._write(
        db, lambda c: c.execute("UPDATE games SET claimed_at=? WHERE id=?", (stale, game_id))
    )


def _seed_floor_pass(db, off_id, deck_id, now=_NOW):
    """Directly seed a PASSED `floor_checks` row (design 2), bypassing
    `floor.enqueue_floor_series`'s own 50-game series. CONFIRM's floor gate
    (`loop.enqueue_confirm_series`, wired T8) only checks
    `floor_checks.verdict='pass'`, so tests that seed offspring straight at
    'confirming'/'matching' (skipping the MATCH->floor pipeline stages) must
    satisfy this gate directly -- mirrors `_play_confirm`'s direct-write
    convention above."""
    floor._ensure_schema(db)
    deckdb._write(
        db,
        lambda c: c.execute(
            "INSERT INTO floor_checks(offspring_id, deck_id, games_planned, games_done, "
            "wins, wr, verdict, created_at, resolved_at) VALUES (?,?,?,?,?,?,'pass',?,?)",
            (off_id, deck_id, floor.FLOOR_GAMES, floor.FLOOR_GAMES, 30, 0.60,
             now.isoformat(), now.isoformat()),
        ),
    )


def _finish_games_for(db, purpose, version_a, wins, total):
    """Mark the first `total` pending `purpose` games for `agent_version_a=
    version_a` done via direct UPDATE (`wins` a-side wins, the rest b-side).
    Purpose+version-TARGETED, unlike `deckdb.claim_next_game` (claims the
    single highest-priority pending game across ALL purposes -- the anchor
    stage's own always-pending baseline series (priority 1.0) outranks
    floor (0.8) and netcheck (0.6) games, so a naive claim loop after
    `loop_tick` has already run would silently drain the wrong series).
    Mirrors `tests/test_factory_floor.py::_finish_floor_games`."""

    def _apply(c):
        rows = c.execute(
            "SELECT id FROM games WHERE purpose=? AND agent_version_a=? "
            "ORDER BY id LIMIT ?",
            (purpose, version_a, total),
        ).fetchall()
        assert len(rows) == total, (
            f"expected {total} pending {purpose!r} games for agent_version_a="
            f"{version_a!r}, found {len(rows)}"
        )
        for i, row in enumerate(rows):
            c.execute(
                "UPDATE games SET status='done', winner=? WHERE id=?",
                (0 if i < wins else 1, row["id"]),
            )

    deckdb._write(db, _apply)


# --- census-incomplete branch -----------------------------------------------


def test_census_incomplete_schedules_and_skips_promote_activate(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path)
    _seed_singles(db, n=3, games_played=0, rated=False)  # below floor -> incomplete
    calls: list[str] = []
    monkeypatch.setattr(census, "promote_proven_singles", lambda *a, **k: calls.append("promote"))
    monkeypatch.setattr(census, "activate_pair_concepts", lambda *a, **k: calls.append("activate"))

    result = loop_scheduler.loop_tick(db, random.Random(_SEED), None, _NOW, pipeline_target=0)

    assert result["phase"] == "census"
    assert result["screening_enqueued"] > 0
    assert deckdb.pending_count(db, purpose="screening") > 0
    assert "promote" not in calls and "activate" not in calls  # pre-census must NOT touch pairs


# --- post-census carry-forward ordering (#1) --------------------------------


def test_post_census_calls_refresh_then_promote_then_activate_in_order(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path)  # empty -> census_complete vacuously True
    order: list[str] = []

    def _rec(tag):
        return lambda *a, **k: order.append(tag) or 0

    monkeypatch.setattr(rating, "refresh_field_ratings", _rec("refresh"))
    monkeypatch.setattr(census, "promote_proven_singles", _rec("promote"))
    monkeypatch.setattr(census, "activate_pair_concepts", _rec("activate"))

    loop_scheduler.loop_tick(db, random.Random(_SEED), None, _NOW, pipeline_target=0)

    # carry-forward #1: refresh BEFORE promote BEFORE activate, or pair
    # activation is a production no-op (singles never leave 'untested').
    assert order == ["refresh", "promote", "activate"]


def test_promote_uses_screening_floor(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path)
    seen: dict[str, int] = {}
    monkeypatch.setattr(
        census, "promote_proven_singles", lambda conn, floor=None: seen.setdefault("floor", floor)
    )
    monkeypatch.setattr(census, "activate_pair_concepts", lambda *a, **k: 0)

    loop_scheduler.loop_tick(db, random.Random(_SEED), None, _NOW, pipeline_target=0)

    assert seen["floor"] == census.SCREENING_FLOOR == 15


# --- BOOTSTRAP --------------------------------------------------------------


def test_bootstrap_founds_v01_on_best_census_deck(tmp_path):
    db = _fresh_db(tmp_path)
    # proven single-core concepts (untested, cleared floor, rated) but no
    # baseline yet -> loop_tick promotes then founds on the best deck.
    _seed_singles(db, n=3, games_played=census.SCREENING_FLOOR, rated=True)
    assert loop_state.current_baseline(db) is None

    result = loop_scheduler.loop_tick(db, random.Random(_SEED), None, _NOW, pipeline_target=0)

    assert result["phase"] == "bootstrap"
    assert result["founded"] == "v0.1"
    baseline = loop_state.current_baseline(db)
    assert baseline is not None
    assert baseline["version"] == "v0.1"
    assert baseline["deck_id"] == "ds000"  # s000 is the highest-rated single (rating 1000)
    # founding agent config persisted for later breeding.
    cfg = db.execute("SELECT value FROM meta WHERE key='founding_agent_config'").fetchone()
    assert cfg is not None


def test_bootstrap_waits_when_no_rated_active_deck(tmp_path):
    db = _fresh_db(tmp_path)  # nothing seeded -> census vacuously complete, no deck to found on
    result = loop_scheduler.loop_tick(db, random.Random(_SEED), None, _NOW, pipeline_target=0)
    assert result["phase"] == "bootstrap_wait"
    assert loop_state.current_baseline(db) is None


# --- Step-1 RESUME test (crash mid-CONFIRM) ---------------------------------


def test_loop_tick_resumes_confirm_from_game_100_on_fresh_connection(tmp_path):
    db, off_id = _seed_baseline_and_offspring(tmp_path, "confirming", set_deck=True)
    baseline = loop_state.current_baseline(db)
    _seed_floor_pass(db, off_id, "dOff")  # CONFIRM's floor gate (T8) requires this
    _play_confirm(db, off_id, baseline["version"], "dOff", offspring_wins=55, total=100)  # 100/200
    db.close()

    # FRESH connection, NO in-memory state -- pure DB-driven resume.
    fresh = deckdb.connect(tmp_path / "t.db")
    result = loop_scheduler.loop_tick(fresh, random.Random(_SEED), None, _NOW, pipeline_target=0)

    assert result["phase"] == "loop"
    # tops up ONLY the remaining 100, never restarts the 200-game series.
    assert deckdb.pending_count(fresh, purpose="confirm") == 100
    total = fresh.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='confirm' AND agent_version_a=?", (off_id,)
    ).fetchone()[0]
    assert total == 200
    status = fresh.execute(
        "SELECT status FROM offspring WHERE id=?", (off_id,)
    ).fetchone()["status"]
    assert status == "confirming"  # 100 done < 200 -> not resolved yet


# --- Step-1 INTERLEAVED test (two ticks racing) -----------------------------


def test_two_ticks_racing_do_not_double_enqueue_series(tmp_path):  # INTERLEAVED
    # Two scheduler ticks racing must not double-enqueue a CONFIRM series
    # (Pattern SQLITE-TXN). The committed `enqueue_confirm_series` guard makes
    # the loser top up 0; this proves loop_tick's orchestration inherits that.
    db, off_id = _seed_baseline_and_offspring(tmp_path, "confirming", set_deck=True)
    _seed_floor_pass(db, off_id, "dOff")  # CONFIRM's floor gate (T8) requires this
    db.close()
    db_path = tmp_path / "t.db"
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def _call():
        conn = deckdb.connect(db_path)
        try:
            barrier.wait()
            loop_scheduler.loop_tick(conn, random.Random(_SEED), None, _NOW, pipeline_target=0)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=_call)
    t2 = threading.Thread(target=_call)
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    assert not errors, errors
    check = deckdb.connect(db_path)
    total = check.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='confirm' AND agent_version_a=?", (off_id,)
    ).fetchone()[0]
    assert total == 200  # enqueued once, never 400


# --- drive: status-driven advance -------------------------------------------


def _seed_active_field(tmp_path: Path, n_active: int, offspring_status: str):
    """Founding baseline on `dBase` (outside the field) + `n_active` active
    rated field decks (`d000`..) + one offspring at `offspring_status`."""
    db = _fresh_db(tmp_path)

    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cBase','[\"Z\"]','untested')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dBase','cBase','[1]')")
        for i in range(n_active):
            cid, did = f"c{i:03d}", f"d{i:03d}"
            c.execute("INSERT INTO concepts(id,cores,status) VALUES(?,?,'active')", (cid, '["X"]'))
            c.execute("INSERT INTO decks(id,concept_id,cards) VALUES(?,?,'[1]')", (did, cid))
            c.execute(
                "INSERT INTO coverage(concept_id,games_played,distinct_opponents,rating) "
                "VALUES(?,15,1,?)",
                (cid, float(1000 - i)),
            )

    deckdb._write(db, _s)
    loop_state.set_founding_baseline(db, "dBase", loop.FOUNDING_AGENT_CONFIG)
    off_id = loop_state.next_offspring_version(db)
    loop_state.insert_offspring(db, off_id, "{}", "w.json")
    loop_state.set_offspring_status(db, off_id, offspring_status)
    return db, off_id


def test_drive_advances_queued_for_match_to_matching(tmp_path):
    db, off_id = _seed_active_field(tmp_path, n_active=3, offspring_status="queued_for_match")

    loop_scheduler.loop_tick(db, random.Random(_SEED), None, _NOW, pipeline_target=0)

    status = db.execute("SELECT status FROM offspring WHERE id=?", (off_id,)).fetchone()["status"]
    assert status == "matching"
    assert deckdb.pending_count(db, purpose="match") == 45  # min(30,3)*15


def test_drive_completed_match_selects_deck_and_enqueues_floor(tmp_path):
    db, off_id = _seed_active_field(tmp_path, n_active=2, offspring_status="matching")
    baseline = loop_state.current_baseline(db)
    # play a completed MATCH series: d000 best win share so it is the pick.
    for deck, wins in (("d000", 12), ("d001", 4)):
        for i in range(15):
            deckdb.enqueue_game(db, deck, deck, off_id, baseline["version"], purpose="match")
            row = deckdb.claim_next_game(db, worker_pid=1)
            deckdb.record_result(db, row["id"], 0 if i < wins else 1)

    loop_scheduler.loop_tick(db, random.Random(_SEED), None, _NOW, pipeline_target=0)

    off = db.execute("SELECT status, deck_id FROM offspring WHERE id=?", (off_id,)).fetchone()
    # early anchor floor gate (design 2, T9 wiring): MATCH-complete picks the
    # optimal deck immediately, but CONFIRM does NOT start in the same tick
    # -- a separate floor series against the anchor deck must pass first.
    assert off["status"] == "matching"
    assert off["deck_id"] == "d000"
    assert deckdb.pending_count(db, purpose="floor") == floor.FLOOR_GAMES
    verdict, done, planned, wr = floor.floor_status(db, off_id)
    assert (verdict, done, planned) == ("pending", 0, floor.FLOOR_GAMES)


def test_drive_starts_confirm_once_floor_passes(tmp_path):
    db, off_id = _seed_active_field(tmp_path, n_active=2, offspring_status="matching")
    baseline = loop_state.current_baseline(db)
    for deck, wins in (("d000", 12), ("d001", 4)):
        for i in range(15):
            deckdb.enqueue_game(db, deck, deck, off_id, baseline["version"], purpose="match")
            row = deckdb.claim_next_game(db, worker_pid=1)
            deckdb.record_result(db, row["id"], 0 if i < wins else 1)
    loop_scheduler.loop_tick(db, random.Random(_SEED), None, _NOW, pipeline_target=0)  # enqueues floor

    # pass the floor series (26/50 = 0.52 >= FLOOR_BAR 0.46) -- targeted by
    # purpose+version, since a plain claim_next_game would grab the anchor
    # stage's own higher-priority (always-pending) baseline series instead.
    _finish_games_for(db, "floor", floor.floor_version(off_id), wins=26, total=floor.FLOOR_GAMES)

    result = loop_scheduler.loop_tick(db, random.Random(_SEED), None, _NOW, pipeline_target=0)

    assert result["floor_failed"] == 0
    assert result["confirm_started"] == 1
    off = db.execute("SELECT status, deck_id FROM offspring WHERE id=?", (off_id,)).fetchone()
    assert off["status"] == "confirming"
    assert off["deck_id"] == "d000"
    assert deckdb.pending_count(db, purpose="confirm") == loop.CONFIRM_GAMES


def test_drive_repicks_deck_on_floor_fail_then_confirms(tmp_path):
    """A floor FAIL blames the DECK: the scheduler must re-point the offspring
    at its next-best MATCH deck and keep driving it, never wedge it. No-wedge
    trace, tick by tick: fail -> 'repick' leaves the offspring at 'matching'
    with a full pending floor series (an advancing step exists) -> that series
    completes -> pass -> CONFIRM. No tick leaves the offspring in a state with
    nothing pending and no transition available."""
    db, off_id = _seed_active_field(tmp_path, n_active=2, offspring_status="matching")
    baseline = loop_state.current_baseline(db)
    for deck, wins in (("d000", 12), ("d001", 4)):
        for i in range(15):
            deckdb.enqueue_game(db, deck, deck, off_id, baseline["version"], purpose="match")
            row = deckdb.claim_next_game(db, worker_pid=1)
            deckdb.record_result(db, row["id"], 0 if i < wins else 1)
    loop_scheduler.loop_tick(db, random.Random(_SEED), None, _NOW, pipeline_target=0)
    assert db.execute(
        "SELECT deck_id FROM offspring WHERE id=?", (off_id,)).fetchone()["deck_id"] == "d000"

    # d000 fails its floor (10/50 = 0.20 < 0.46)
    _finish_games_for(db, "floor", floor.floor_version(off_id, 0), wins=10,
                      total=floor.FLOOR_GAMES)
    result = loop_scheduler.loop_tick(db, random.Random(_SEED), None, _NOW, pipeline_target=0)

    assert (result["floor_repicked"], result["floor_failed"], result["confirm_started"]) == (1, 0, 0)
    off = db.execute("SELECT status, deck_id FROM offspring WHERE id=?", (off_id,)).fetchone()
    assert off["status"] == "matching"  # alive, not trashed
    assert off["deck_id"] == "d001"     # next-best MATCH deck
    # advancing step exists: attempt 1's full series is pending
    assert deckdb.pending_count(db, purpose="floor") == floor.FLOOR_GAMES

    # d001 passes its own floor (26/50 = 0.52 >= 0.46) -> CONFIRM on the PASSING deck
    _finish_games_for(db, "floor", floor.floor_version(off_id, 1), wins=26,
                      total=floor.FLOOR_GAMES)
    result = loop_scheduler.loop_tick(db, random.Random(_SEED), None, _NOW, pipeline_target=0)

    assert (result["floor_repicked"], result["floor_failed"], result["confirm_started"]) == (0, 0, 1)
    off = db.execute("SELECT status, deck_id FROM offspring WHERE id=?", (off_id,)).fetchone()
    assert (off["status"], off["deck_id"]) == ("confirming", "d001")
    assert deckdb.pending_count(db, purpose="confirm") == loop.CONFIRM_GAMES


def test_drive_resolves_completed_confirm_to_survivor(tmp_path):
    db, off_id = _seed_baseline_and_offspring(tmp_path, "confirming", set_deck=True)
    baseline = loop_state.current_baseline(db)
    _play_confirm(db, off_id, baseline["version"], "dOff", offspring_wins=100, total=200)  # 0.50

    loop_scheduler.loop_tick(db, random.Random(_SEED), None, _NOW, pipeline_target=0)

    status = db.execute("SELECT status FROM offspring WHERE id=?", (off_id,)).fetchone()["status"]
    assert status == "survivor"


# --- TRAIN faucet gating ----------------------------------------------------


class _FakeTrainer:
    def __init__(self, deck, calls, data_dir):
        self.deck, self.calls, self.data_dir = deck, calls, Path(data_dir)

    def prepare_data(self, cycle: int) -> Path:
        out = self.data_dir / f"{Path(self.deck).stem}-c{cycle}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("{}\n", encoding="utf-8")
        return out

    def train(self, data_path: Path, cycle: int) -> Path:
        w = self.data_dir / f"weights-c{cycle}.json"
        w.write_text("{}", encoding="utf-8")
        return w


def _fake_factory(tmp_path):
    return lambda deck: _FakeTrainer(deck, [], tmp_path / "gen")


def test_train_faucet_breeds_when_pipeline_below_target(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "GENERATED_DIR", tmp_path / "generated")
    db, off_id = _seed_active_field(tmp_path, n_active=2, offspring_status="survivor")
    # one 'survivor' is terminal (not in-flight); in-flight count is 0 < target.

    result = loop_scheduler.loop_tick(
        db, random.Random(_SEED), _fake_factory(tmp_path), _NOW, pipeline_target=1
    )

    assert result["bred"] is not None
    new = db.execute("SELECT status FROM offspring WHERE id=?", (result["bred"],)).fetchone()
    # design 4 (netcheck, T9 wiring): the fake trainer's weights differ from
    # the founding net, so `train_offspring`'s own `netcheck.enqueue_net_check`
    # call plans a real check series rather than short-circuiting to 'auto'
    # -- the offspring rests at 'training' until that check resolves, not
    # 'queued_for_match' immediately.
    assert new["status"] == "training"


def test_train_faucet_skips_when_pipeline_at_target(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "GENERATED_DIR", tmp_path / "generated")
    db, _ = _seed_active_field(tmp_path, n_active=2, offspring_status="matching")  # 1 in-flight

    result = loop_scheduler.loop_tick(
        db, random.Random(_SEED), _fake_factory(tmp_path), _NOW, pipeline_target=1
    )

    assert result["bred"] is None  # in-flight (1) not < target (1)


# --- orphaned-claim reclaim + poison cap (carry-forward #2) ------------------


def test_reclaim_returns_orphaned_claim_to_pending(tmp_path):
    db = _fresh_db(tmp_path)

    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('c','[\"A\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('d','c','[1]')")

    deckdb._write(db, _s)
    loop_scheduler.ensure_recovery_schema(db)
    gid = deckdb.enqueue_game(db, "d", "d", "v0.1", "v0.1", purpose="screening")
    deckdb.claim_next_game(db, worker_pid=999)
    _backdate_claim(db, gid, _NOW, seconds_ago=loop_scheduler.RECLAIM_STALE_SECONDS + 60)

    result = loop_scheduler.reclaim_orphaned_games(db, _NOW)

    assert result == {"reclaimed": 1, "dead_lettered": 0}
    g = db.execute("SELECT status, worker_pid FROM games WHERE id=?", (gid,)).fetchone()
    assert g["status"] == "pending"
    assert g["worker_pid"] is None
    rec = db.execute("SELECT reclaims, dead FROM game_recovery WHERE game_id=?", (gid,)).fetchone()
    assert rec["reclaims"] == 1 and rec["dead"] == 0


def test_reclaim_leaves_fresh_claims_alone(tmp_path):
    db = _fresh_db(tmp_path)

    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('c','[\"A\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('d','c','[1]')")

    deckdb._write(db, _s)
    loop_scheduler.ensure_recovery_schema(db)
    gid = deckdb.enqueue_game(db, "d", "d", "v0.1", "v0.1", purpose="screening")
    deckdb.claim_next_game(db, worker_pid=999)  # claimed_at = now, fresh

    result = loop_scheduler.reclaim_orphaned_games(db, _NOW)

    assert result == {"reclaimed": 0, "dead_lettered": 0}
    g_status = db.execute("SELECT status FROM games WHERE id=?", (gid,)).fetchone()["status"]
    assert g_status == "claimed"


def test_poison_game_dead_lettered_after_cap_with_persisted_error(tmp_path):
    db = _fresh_db(tmp_path)

    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('c','[\"A\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('d','c','[1]')")

    deckdb._write(db, _s)
    loop_scheduler.ensure_recovery_schema(db)
    gid = deckdb.enqueue_game(db, "d", "d", "v0.1", "v0.1", purpose="screening")

    # first reclaim (attempt 1 of max 2): requeue.
    deckdb.claim_next_game(db, worker_pid=999)
    _backdate_claim(db, gid, _NOW, seconds_ago=loop_scheduler.RECLAIM_STALE_SECONDS + 60)
    r1 = loop_scheduler.reclaim_orphaned_games(db, _NOW, max_reclaims=2)
    assert r1 == {"reclaimed": 1, "dead_lettered": 0}

    # re-claim + re-orphan, second reclaim hits the cap -> dead-letter.
    deckdb.claim_next_game(db, worker_pid=1001)
    _backdate_claim(db, gid, _NOW, seconds_ago=loop_scheduler.RECLAIM_STALE_SECONDS + 60)
    r2 = loop_scheduler.reclaim_orphaned_games(db, _NOW, max_reclaims=2)
    assert r2 == {"reclaimed": 0, "dead_lettered": 1}

    rec = db.execute(
        "SELECT reclaims, dead, last_error FROM game_recovery WHERE game_id=?", (gid,)
    ).fetchone()
    assert rec["dead"] == 1
    assert rec["reclaims"] == 2
    assert "poison" in rec["last_error"]
    # dead-lettered game is quarantined: stays 'claimed' (workers only take
    # 'pending'), so no worker ever re-claims it, and a later reclaim ignores it.
    g_status = db.execute("SELECT status FROM games WHERE id=?", (gid,)).fetchone()["status"]
    assert g_status == "claimed"
    assert deckdb.claim_next_game(db, worker_pid=2) is None  # nothing claimable
    r3 = loop_scheduler.reclaim_orphaned_games(db, _NOW, max_reclaims=2)
    assert r3 == {"reclaimed": 0, "dead_lettered": 0}  # dead=1 excluded from the scan


def test_reclaim_survives_concurrent_calls(tmp_path):  # INTERLEAVED
    # Two scheduler reclaims racing the SAME orphaned game must not both
    # requeue it / both count it (Pattern SQLITE-TXN): the whole reclaim is
    # one BEGIN IMMEDIATE txn, so the loser's own claimed-scan runs strictly
    # after the winner commits and no longer sees the (now pending) game.
    db = _fresh_db(tmp_path)

    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('c','[\"A\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('d','c','[1]')")

    deckdb._write(db, _s)
    loop_scheduler.ensure_recovery_schema(db)
    gid = deckdb.enqueue_game(db, "d", "d", "v0.1", "v0.1", purpose="screening")
    deckdb.claim_next_game(db, worker_pid=999)
    _backdate_claim(db, gid, _NOW, seconds_ago=loop_scheduler.RECLAIM_STALE_SECONDS + 60)
    db.close()

    db_path = tmp_path / "t.db"
    barrier = threading.Barrier(2)
    results: dict[str, dict] = {}

    def _call(name):
        conn = deckdb.connect(db_path)
        barrier.wait()
        results[name] = loop_scheduler.reclaim_orphaned_games(conn, _NOW)

    t1 = threading.Thread(target=_call, args=("t1",))
    t2 = threading.Thread(target=_call, args=("t2",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    reclaimed_total = sum(r["reclaimed"] for r in results.values())
    assert reclaimed_total == 1  # exactly one racer reclaimed it, never two
    check = deckdb.connect(db_path)
    rec = check.execute("SELECT reclaims FROM game_recovery WHERE game_id=?", (gid,)).fetchone()
    assert rec["reclaims"] == 1  # not double-counted


# --- T15 review carry-forward: survivor exclusion ---------------------------


def test_dead_letter_survives_concurrent_calls(tmp_path):  # INTERLEAVED
    # The DEAD-LETTER path has no `WHERE status='claimed'` UPDATE guard (a
    # dead game is LEFT claimed), so unlike the requeue path it is NOT
    # protected by a guarded single-UPDATE -- only the whole-transaction
    # atomicity stops two ticks racing a game AT THE CAP from both
    # dead-lettering it (double-counting `dead_lettered`). Discriminates:
    # against a scan-outside-txn shape both racers see the not-yet-dead game
    # and both flip `dead=1`, giving `dead_lettered` total 2; the atomic shape
    # makes the loser's scan run after the winner commits, excluding the now
    # `dead=1` row -- total 1.
    db = _fresh_db(tmp_path)

    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('c','[\"A\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('d','c','[1]')")

    deckdb._write(db, _s)
    loop_scheduler.ensure_recovery_schema(db)
    gid = deckdb.enqueue_game(db, "d", "d", "v0.1", "v0.1", purpose="screening")
    deckdb.claim_next_game(db, worker_pid=999)
    _backdate_claim(db, gid, _NOW, seconds_ago=loop_scheduler.RECLAIM_STALE_SECONDS + 60)
    # already reclaimed once (reclaims=1); the NEXT reclaim (max_reclaims=2)
    # hits the cap and dead-letters.
    deckdb._write(
        db,
        lambda c: c.execute(
            "INSERT INTO game_recovery(game_id,reclaims,dead,updated_at) VALUES(?,1,0,?)",
            (gid, _NOW.isoformat()),
        ),
    )
    db.close()

    db_path = tmp_path / "t.db"
    barrier = threading.Barrier(2)
    results: dict[str, dict] = {}

    def _call(name):
        conn = deckdb.connect(db_path)
        barrier.wait()
        results[name] = loop_scheduler.reclaim_orphaned_games(conn, _NOW, max_reclaims=2)

    t1 = threading.Thread(target=_call, args=("t1",))
    t2 = threading.Thread(target=_call, args=("t2",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    dead_total = sum(r["dead_lettered"] for r in results.values())
    assert dead_total == 1  # exactly one racer dead-lettered it, never two
    check = deckdb.connect(db_path)
    rec = check.execute(
        "SELECT reclaims, dead FROM game_recovery WHERE game_id=?", (gid,)
    ).fetchone()
    assert rec["reclaims"] == 2 and rec["dead"] == 1


def test_crowned_ex_survivor_invisible_to_scheduler_crown_logic(tmp_path):
    db, off_a = _seed_baseline_and_offspring(tmp_path, "survivor", set_deck=True)
    # off_a was crowned: its status stays 'survivor' (loop_state.crown_baseline
    # never touches offspring.status), excluded only via the baselines join.
    loop_state.crown_baseline(db, off_a, "dOff")
    # a second, genuinely-fresh survivor (never crowned).
    off_b = loop_state.next_offspring_version(db)
    loop_state.insert_offspring(db, off_b, "{}", "w.json")
    loop.set_offspring_deck(db, off_b, "dOff")
    loop_state.set_offspring_status(db, off_b, "survivor")

    # eligible set excludes the crowned ex-survivor -> only off_b, so <2
    # eligible: the scheduler must NOT start a round-robin.
    eligible = [r["id"] for r in loop.eligible_crown_survivors(db)]
    assert eligible == [off_b]

    loop_scheduler.loop_tick(db, random.Random(_SEED), None, _NOW, pipeline_target=0)

    assert deckdb.pending_count(db, purpose="crown") == 0  # no redundant round-robin


# --- anchor stage wiring (T6) -------------------------------------------------


def test_tick_drives_anchor_stage(tmp_path):
    """A tick against a DB with a baseline but no anchor series must enqueue
    the full series; a later tick with the series done must resolve it."""
    db, _off_id = _seed_baseline_and_offspring(tmp_path, "training", set_deck=True)
    baseline = loop_state.current_baseline(db)
    version = baseline["version"]

    result = loop_scheduler.loop_tick(db, random.Random(_SEED), None, _NOW, pipeline_target=0)

    assert result["phase"] == "loop"
    assert result["anchor_enqueued"] == anchor.ANCHOR_GAMES
    assert result["anchor_resolved"] is None
    verdict, done, planned, wr = anchor.anchor_status(db, version)
    assert (verdict, done, planned, wr) == ("pending", 0, anchor.ANCHOR_GAMES, None)

    # mark 120 wins / 80 losses done (champion is agent_version_a: winner
    # 0=champ win, 1=anchor win). PLAN DIVERGENCE from the brief's sketch
    # ("direct SQL like test_factory_anchor.py's _finish_games"): a raw
    # `UPDATE games SET status='done'...` bypasses `deckdb.record_result`'s
    # `coverage.games_played` bump, which flips `census.census_complete`
    # back to False the moment `ensure_anchor_deck` registers the anchor as
    # a real single-core concept (its own coverage row starts at 0, below
    # `SCREENING_FLOOR`) -- the second tick would take the "census" branch
    # instead of "loop" and never reach `_drive_offspring`/the anchor
    # resolve call at all. Claim+record via `deckdb` (mirrors this file's
    # own `_play_confirm` convention) matches how the real runner_pool
    # plays anchor games in production and lets the anchor concept's
    # coverage recover past the floor exactly as it would live.
    for i in range(120 + 80):
        row = deckdb.claim_next_game(db, worker_pid=1)
        deckdb.record_result(db, row["id"], 0 if i < 120 else 1)

    result2 = loop_scheduler.loop_tick(db, random.Random(_SEED), None, _NOW, pipeline_target=0)

    assert result2["phase"] == "loop"
    assert result2["anchor_enqueued"] == 0  # already full series -> no-op
    assert result2["anchor_resolved"] == "pass"
    verdict, done, planned, wr = anchor.anchor_status(db, version)
    assert verdict == "pass" and done == 200 and wr == pytest.approx(0.60)


# --- throttle ---------------------------------------------------------------


def test_rating_refresh_is_throttled_within_interval(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path)
    _seed_singles(db, n=3, games_played=0, rated=False)  # census incomplete
    n = {"calls": 0}
    def _count(*a, **k):
        n["calls"] += 1
        return 0

    monkeypatch.setattr(rating, "refresh_field_ratings", _count)

    loop_scheduler.loop_tick(db, random.Random(_SEED), None, _NOW, pipeline_target=0)
    # immediate second tick, same interval -> throttled, no second refresh.
    loop_scheduler.loop_tick(
        db, random.Random(_SEED), None, _NOW + dt.timedelta(seconds=1), pipeline_target=0
    )
    assert n["calls"] == 1

    # well past the interval -> refresh again.
    loop_scheduler.loop_tick(
        db, random.Random(_SEED), None,
        _NOW + dt.timedelta(seconds=loop_scheduler.RATING_REFRESH_INTERVAL_S + 5),
        pipeline_target=0,
    )
    assert n["calls"] == 2


# --- entrypoint (scripts/factory_tournament_scheduler.py) -------------------


def _paths(tmp_path):
    from ptcg.factory.cycle import FactoryPaths

    return FactoryPaths(root=tmp_path)


def test_run_scheduler_advances_census_across_ticks(tmp_path):
    from scripts import factory_tournament_scheduler as sched

    db = _fresh_db(tmp_path)
    _seed_singles(db, n=3, games_played=0, rated=False)  # census incomplete
    db.close()

    last = sched.run_scheduler(
        tmp_path / "t.db", _paths(tmp_path),
        max_ticks=2, interval_s=0, pipeline_target=0,
        rng=random.Random(_SEED), now_fn=lambda: _NOW,
        lock_path=tmp_path / "sched.lock", log=lambda *a: None,
    )

    assert last == "census"
    check = deckdb.connect(tmp_path / "t.db")
    assert deckdb.pending_count(check, purpose="screening") > 0  # ticks did real work


def test_run_scheduler_honors_pause(tmp_path):
    from scripts import factory_tournament_scheduler as sched

    db = _fresh_db(tmp_path)
    _seed_singles(db, n=3, games_played=0, rated=False)
    db.close()
    paths = _paths(tmp_path)
    paths.pause_file.parent.mkdir(parents=True, exist_ok=True)
    paths.pause_file.write_text("stop", encoding="utf-8")

    last = sched.run_scheduler(
        tmp_path / "t.db", paths,
        max_ticks=1, interval_s=0, pipeline_target=0,
        rng=random.Random(_SEED), now_fn=lambda: _NOW,
        lock_path=tmp_path / "sched.lock", log=lambda *a: None,
    )

    assert last == "paused"
    check = deckdb.connect(tmp_path / "t.db")
    assert deckdb.pending_count(check, purpose="screening") == 0  # PAUSE ran no tick


def test_run_scheduler_survives_a_raising_tick(tmp_path, monkeypatch):
    """A `loop_tick` that raises must NOT kill the scheduler process.

    `ptcg-factory-scheduler` is a loop-forever worker kept alive by a
    15-minute `MultipleInstances=IgnoreNew` watchdog. If an exception
    propagated out of the tick, the process would die, the watchdog would
    respawn it, and it would die again on the same poisoned row -- an
    invisible crash-loop whose only symptom is a `Running` task making no
    progress (`.claude/rules/factory-task-scheduler-liveness.md`). So the tick
    error is caught, PERSISTED as a loud terminal marker (utf-8), and the loop
    continues.
    """
    from scripts import factory_tournament_scheduler as sched

    db = _fresh_db(tmp_path)
    _seed_singles(db, n=3, games_played=0, rated=False)
    db.close()
    log_path = tmp_path / "logs" / "scheduler.log"  # virgin dir -- never created
    calls = []

    def _boom(*a, **kw):
        calls.append(1)
        raise RuntimeError("poisoned row")

    monkeypatch.setattr(loop_scheduler, "loop_tick", _boom)

    last = sched.run_scheduler(
        tmp_path / "t.db", _paths(tmp_path),
        max_ticks=3, interval_s=0, pipeline_target=0,
        rng=random.Random(_SEED), now_fn=lambda: _NOW,
        lock_path=tmp_path / "sched.lock", log_path=log_path,
        log=lambda *a: None,
    )

    # The loop kept going: all 3 ticks ran despite every one of them raising.
    assert len(calls) == 3
    assert last == "error"
    text = log_path.read_text(encoding="utf-8")
    assert text.count(sched.TICK_ERROR_MARKER) == 3  # one marker per failed tick
    assert "RuntimeError: poisoned row" in text
    assert "Traceback" in text  # the traceback rides along, not just the message


def test_run_scheduler_recovers_after_a_raising_tick(tmp_path, monkeypatch):
    """The control: a tick that raises once must not poison later ticks --
    the next tick runs normally and its phase is what gets reported."""
    from scripts import factory_tournament_scheduler as sched

    db = _fresh_db(tmp_path)
    _seed_singles(db, n=3, games_played=0, rated=False)
    db.close()
    log_path = tmp_path / "logs" / "scheduler.log"
    real_tick = loop_scheduler.loop_tick
    calls = []

    def _boom_once(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient")
        return real_tick(*a, **kw)

    monkeypatch.setattr(loop_scheduler, "loop_tick", _boom_once)

    last = sched.run_scheduler(
        tmp_path / "t.db", _paths(tmp_path),
        max_ticks=2, interval_s=0, pipeline_target=0,
        rng=random.Random(_SEED), now_fn=lambda: _NOW,
        lock_path=tmp_path / "sched.lock", log_path=log_path,
        log=lambda *a: None,
    )

    assert len(calls) == 2
    assert last == "census"  # tick 2 ran for real and reported its own phase
    assert log_path.read_text(encoding="utf-8").count(sched.TICK_ERROR_MARKER) == 1
    check = deckdb.connect(tmp_path / "t.db")
    assert deckdb.pending_count(check, purpose="screening") > 0  # real work happened


def test_run_scheduler_still_reports_busy_on_timeout(tmp_path, monkeypatch):
    """`TimeoutError` must keep propagating to the outer busy handler -- the
    new tick-level catch explicitly re-raises it, so the instance-lock's own
    busy signal is never mistaken for a tick fault (and never spams the error
    log)."""
    from scripts import factory_tournament_scheduler as sched

    db = _fresh_db(tmp_path)
    _seed_singles(db, n=3, games_played=0, rated=False)
    db.close()
    log_path = tmp_path / "logs" / "scheduler.log"

    def _busy(*a, **kw):
        raise TimeoutError("lock held")

    monkeypatch.setattr(loop_scheduler, "loop_tick", _busy)

    last = sched.run_scheduler(
        tmp_path / "t.db", _paths(tmp_path),
        max_ticks=3, interval_s=0, pipeline_target=0,
        rng=random.Random(_SEED), now_fn=lambda: _NOW,
        lock_path=tmp_path / "sched.lock", log_path=log_path,
        log=lambda *a: None,
    )

    assert last == "busy"
    assert not log_path.exists()  # no error marker for a busy signal


def test_seed_if_empty_noops_when_concepts_exist(tmp_path):
    from scripts import factory_tournament_scheduler as sched

    db = _fresh_db(tmp_path)
    _seed_singles(db, n=1, games_played=0, rated=False)

    assert sched.seed_if_empty(db, log=lambda *a: None) is False  # already populated -> no reseed


# --- T9 END-TO-END ORDERING TEST (spec designs 2+3+4 composing) -------------


def test_full_lifecycle_ordering_train_netcheck_floor_confirm_crown_anchor(tmp_path, monkeypatch):
    """Integration receipt for the T9-wired scheduler: drives `loop_tick`
    repeatedly on a seeded post-census DB with a stub trainer, completing
    games by direct UPDATE between ticks (`_finish_games_for`, never
    `deckdb.claim_next_game` -- purpose-blind and would grab the anchor
    stage's own always-pending baseline series ahead of whatever series is
    under test), and asserts the full lifecycle in order:

    TRAIN -> 'training' + netcheck series -> reject at 54/100 (< NETCHECK_BAR
    0.55) -> incumbent net kept -> 'queued_for_match' -> MATCH -> floor
    series -> pass at 23/50 (>= FLOOR_BAR 0.46) -> CONFIRM -> 'survivor' x2
    (offspring A's own journey + a directly-seeded second survivor, whose own
    full pipeline traversal is already covered by A's -- CROWN's internals
    are its own module's concern, not this ordering test's) -> CROWN
    nominates (no `baselines` row yet) -> elect anchor series -> fail at 0.40
    (< ANCHOR_BAR 0.60) -> `meta['baseline_version']` unchanged and breeding
    continues (a subsequent tick still breeds from the OLD baseline) ->
    second elect (two more directly-seeded survivors) passes at 0.60 ->
    baseline advances exactly once (a further tick does not re-advance it).

    Arithmetic hand-verified (`.claude/rules/plan-test-arithmetic-sanity.md`):
    54/100=0.54<0.55 rejects; 23/50=0.46>=0.46 passes; 100/200=0.50>=0.50
    survives; 80/200=0.40<0.60 fails; 120/200=0.60>=0.60 passes.
    """
    monkeypatch.setattr(loop, "GENERATED_DIR", tmp_path / "generated")
    db = _fresh_db(tmp_path)

    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cBase','[\"Z\"]','untested')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dBase','cBase','[1]')")
        for i in range(2):
            cid, did = f"c{i:03d}", f"d{i:03d}"
            c.execute("INSERT INTO concepts(id,cores,status) VALUES(?,?,'active')", (cid, '["X"]'))
            c.execute("INSERT INTO decks(id,concept_id,cards) VALUES(?,?,'[1]')", (did, cid))
            c.execute(
                "INSERT INTO coverage(concept_id,games_played,distinct_opponents,rating) "
                "VALUES(?,15,1,?)",
                (cid, float(1000 - i)),
            )

    deckdb._write(db, _s)
    loop_state.set_founding_baseline(db, "dBase", loop.FOUNDING_AGENT_CONFIG)
    v01 = loop_state.current_baseline(db)["version"]

    # a second, already-'survivor' offspring so round 1's CROWN round-robin
    # has >=2 eligible without re-driving a whole second offspring through
    # the netcheck/floor/confirm gauntlet.
    off_b = loop_state.next_offspring_version(db)
    loop_state.insert_offspring(db, off_b, "{}", "w.json")
    loop.set_offspring_deck(db, off_b, "d001")
    loop_state.set_offspring_status(db, off_b, "survivor")

    rng = random.Random(_SEED)
    tick = lambda trainer=None, target=0: loop_scheduler.loop_tick(
        db, rng, trainer, _NOW, pipeline_target=target
    )

    # --- TRAIN: breed offspring A -> 'training' + a planned netcheck series
    # (the fake trainer's weights differ from the founding net, so
    # netcheck.enqueue_net_check does NOT short-circuit to 'auto'). --------
    result = tick(trainer=_fake_factory(tmp_path), target=1)
    off_a = result["bred"]
    assert off_a is not None
    assert db.execute(
        "SELECT status FROM offspring WHERE id=?", (off_a,)
    ).fetchone()["status"] == "training"
    nv, nd, nplan, nwr = netcheck.net_status(db, off_a)
    assert (nv, nd, nplan) == ("pending", 0, netcheck.NETCHECK_GAMES)

    # --- netcheck: REJECT at 54/100 -> incumbent net kept, offspring still
    # advances to 'queued_for_match' -> SAME tick's MATCH loop enqueues the
    # field series (a live DB read sees the status change from moments
    # earlier in this same `_drive_offspring` call). -------------------------
    _finish_games_for(db, "netcheck", netcheck.cand_version(off_a), wins=54, total=100)
    result = tick()
    assert result["net_resolved"] == 1
    off_row = db.execute(
        "SELECT status, value_net_ref FROM offspring WHERE id=?", (off_a,)
    ).fetchone()
    assert off_row["value_net_ref"] == loop.FOUNDING_AGENT_CONFIG["net_weights"]  # incumbent kept
    assert result["matched"] == 1
    assert off_row["status"] == "matching"
    assert deckdb.pending_count(db, purpose="match") == 30  # min(30,2)*15

    # --- floor: MATCH complete -> picks d000 (all-win) -> enqueues the
    # 50-game floor series (still pending -> 'matching', no CONFIRM yet). ---
    _finish_games_for(db, "match", off_a, wins=15, total=30)  # d000 15/15, d001 0/15
    result = tick()
    off_row = db.execute(
        "SELECT status, deck_id FROM offspring WHERE id=?", (off_a,)
    ).fetchone()
    assert off_row["deck_id"] == "d000"
    assert off_row["status"] == "matching"
    assert deckdb.pending_count(db, purpose="floor") == floor.FLOOR_GAMES

    # --- floor PASSES at 23/50 -> CONFIRM starts in the SAME tick the floor
    # verdict resolves. -------------------------------------------------------
    _finish_games_for(db, "floor", floor.floor_version(off_a), wins=23, total=floor.FLOOR_GAMES)
    result = tick()
    assert result["floor_failed"] == 0
    assert result["confirm_started"] == 1
    off_row = db.execute("SELECT status FROM offspring WHERE id=?", (off_a,)).fetchone()
    assert off_row["status"] == "confirming"
    assert deckdb.pending_count(db, purpose="confirm") == loop.CONFIRM_GAMES

    # --- CONFIRM: A survives at 100/200 = 0.50. -----------------------------
    _finish_games_for(db, "confirm", off_a, wins=loop.CONFIRM_GAMES // 2, total=loop.CONFIRM_GAMES)
    result = tick()
    assert "survivor" in result["resolved"]
    assert db.execute(
        "SELECT status FROM offspring WHERE id=?", (off_a,)
    ).fetchone()["status"] == "survivor"

    # --- CROWN: 'survivor' x2 (A + pre-seeded B) -> round-robin enqueued
    # (no nomination yet -- A only just became a survivor THIS tick, one
    # tick after CROWN's own resolve/enqueue pair already ran). -------------
    result = tick()
    assert result["crown_enqueued"] > 0
    assert off_b < off_a  # off_b bred first ("v0.1.1" < "v0.1.2") -> crown's fixed "a" side
    # off_b (the "a" side) loses everything -> off_a (the "b" side) wins 200/200.
    _finish_games_for(db, "crown", off_b, wins=0, total=loop.CROWN_GAMES_PER_PAIR)

    # --- CROWN nominates A (no `baselines` row yet -- CROWN only nominates,
    # design 3), and the SAME tick's ANCHOR stage enqueues the elect's own
    # 200-game series (the elect IS a fresh pending `anchor_checks` row). ---
    result = tick()
    assert result["elect"] == off_a
    assert db.execute(
        "SELECT COUNT(*) FROM baselines WHERE offspring_id=?", (off_a,)
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT status FROM offspring WHERE id=?", (off_b,)
    ).fetchone()["status"] == "trashed"  # CROWN's round-robin loser

    # --- elect anchor series FAILS at 80/200 = 0.40 (< ANCHOR_BAR 0.60) ->
    # no promotion; `meta['baseline_version']` unchanged; breeding continues
    # from the OLD baseline on a subsequent tick (fail-open pipeline). ------
    _finish_games_for(db, "anchor", off_a, wins=80, total=200)
    result = tick()
    assert result["anchor_resolved"] == "fail"
    assert loop_state.current_baseline(db)["version"] == v01  # unchanged
    assert db.execute(
        "SELECT status FROM offspring WHERE id=?", (off_a,)
    ).fetchone()["status"] == "trashed"

    result = tick(trainer=_fake_factory(tmp_path), target=1)
    assert result["bred"] is not None  # breeding is NOT wedged by the failed elect
    assert loop_state.current_baseline(db)["version"] == v01

    # --- a SECOND elect: two more directly-seeded survivors (the
    # netcheck/floor/confirm gauntlet is already proven end-to-end by A's own
    # journey above) -- CROWN nominates, anchor PASSES at 120/200 = 0.60 ->
    # baseline advances exactly once. -----------------------------------------
    off_d = loop_state.next_offspring_version(db)
    loop_state.insert_offspring(db, off_d, "{}", "w.json")
    loop.set_offspring_deck(db, off_d, "d000")
    loop_state.set_offspring_status(db, off_d, "survivor")
    off_e = loop_state.next_offspring_version(db)
    loop_state.insert_offspring(db, off_e, "{}", "w.json")
    loop.set_offspring_deck(db, off_e, "d001")
    loop_state.set_offspring_status(db, off_e, "survivor")
    assert off_d < off_e  # next_offspring_version is monotonically increasing

    result = tick()  # CROWN round-robin enqueued for (off_d, off_e)
    assert result["crown_enqueued"] > 0
    _finish_games_for(db, "crown", off_d, wins=loop.CROWN_GAMES_PER_PAIR, total=loop.CROWN_GAMES_PER_PAIR)

    result = tick()  # CROWN nominates off_d; SAME tick enqueues its anchor series
    assert result["elect"] == off_d
    _finish_games_for(db, "anchor", off_d, wins=120, total=200)

    result = tick()
    assert result["anchor_resolved"] == "pass"
    new_baseline = loop_state.current_baseline(db)
    assert new_baseline["version"] != v01
    assert new_baseline["offspring_id"] == off_d

    # baseline advances EXACTLY once: a further tick does not re-bump it.
    advanced_version = new_baseline["version"]
    tick()
    assert loop_state.current_baseline(db)["version"] == advanced_version


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_stage_priority_ordering_is_pinned(tmp_path):
    """Pin the claim-queue priority ladder as LITERALS.

    `deckdb.claim_next_game` orders by `priority DESC`, and `_finish_games_for`
    above exists precisely BECAUSE of this ordering (a naive claim loop drains
    the highest-priority series first). Until now nothing pinned the numbers,
    so a silent edit to any one of them -- e.g. lowering ANCHOR below FLOOR --
    would leave upload-blocking anchor verdicts starved behind floor games
    while every existing test stayed green.

    Ladder rationale: ANCHOR outranks everything because a pending anchor
    verdict BLOCKS Kaggle uploads; FLOOR outranks NETCHECK so a candidate is
    cheaply screened against the anchor deck before its net is validated; both
    outrank the bulk MATCH/CONFIRM/CROWN series, which are the queue's filler.
    """
    # Constants, as literals -- not `FLOOR_PRIORITY > NETCHECK_PRIORITY`,
    # which would still pass if both were edited in the same direction.
    assert floor.FLOOR_PRIORITY == 0.8
    assert netcheck.NETCHECK_PRIORITY == 0.6

    # ...and the values actually written to the queue, per purpose. ANCHOR's
    # 1.0 and MATCH/CONFIRM/CROWN's 0.0 are inline SQL literals with no module
    # constant to assert against, so they are read back off real rows.
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)
    deckdb.enqueue_game(db, "dA", "dB", "vA", "vB", purpose="match")
    deckdb.enqueue_game(db, "dA", "dB", "vA", "vB", purpose="confirm")
    deckdb.enqueue_game(db, "dA", "dB", "vA", "vB", purpose="crown")
    for purpose, version, priority in (
        ("anchor", "vAnchor", 1.0),
        ("floor", "vFloor", floor.FLOOR_PRIORITY),
        ("netcheck", "vNet", netcheck.NETCHECK_PRIORITY),
    ):
        def _apply(c, purpose=purpose, version=version, priority=priority):
            c.execute(
                "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, "
                "agent_version_b, purpose, priority, status) "
                "VALUES ('dA','dB',?,'vB',?,?,'pending')",
                (version, purpose, priority),
            )

        deckdb._write(db, _apply)

    seen = {
        row["purpose"]: row["priority"]
        for row in db.execute("SELECT DISTINCT purpose, priority FROM games")
    }
    assert seen["anchor"] == 1.0
    assert seen["floor"] == 0.8
    assert seen["netcheck"] == 0.6
    assert seen["match"] == 0.0
    assert seen["confirm"] == 0.0
    assert seen["crown"] == 0.0
    # Strictly descending -- the property `claim_next_game` actually relies on.
    assert seen["anchor"] > seen["floor"] > seen["netcheck"] > seen["match"]

    # And the queue really hands them back in that order.
    claimed = [deckdb.claim_next_game(db, worker_pid=1)["purpose"] for _ in range(3)]
    assert claimed == ["anchor", "floor", "netcheck"]
