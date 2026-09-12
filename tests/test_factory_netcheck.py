"""Tests for the validated net-swap gate (anchor-pressure design 4).

Spec: docs/superpowers/specs/2026-08-03-tournament-breeding-anchor-pressure-design.md
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from ptcg.factory import deckdb, netcheck
from tests.fixtures.race import race_two


def _connect(tmp_path: Path) -> sqlite3.Connection:
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    return conn


def _seed_world(
    conn,
    *,
    oid="v0.1.1",
    status="training",
    value_net_ref="w_new.json",
    incumbent_net_ref="w_inc.json",
    baseline_deck="dBase",
):
    """FOUNDING baseline (offspring_id NULL) on `baseline_deck`, with
    `meta['founding_agent_config']` carrying `net_weights=incumbent_net_ref`
    (key omitted entirely when incumbent_net_ref is None), plus one offspring
    at `status` with `value_net_ref`. `meta['baseline_version']` points
    `loop_state.current_baseline` at the founding row."""
    def _apply(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cBase','[\"cBase\"]','finalist')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES(?, 'cBase', ?)",
                  (baseline_deck, json.dumps([3] * 60)))
        c.execute(
            "INSERT INTO baselines(version,offspring_id,deck_id,crowned_at) "
            "VALUES('v0.1', NULL, ?, ?)",
            (baseline_deck, "2026-08-03T00:00:00+00:00"))
        c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('baseline_version','v0.1')")
        cfg = {"search_budget_ms": 200}
        if incumbent_net_ref is not None:
            cfg["net_weights"] = incumbent_net_ref
        c.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES('founding_agent_config',?)",
            (json.dumps(cfg),))
        c.execute(
            "INSERT INTO offspring(id,parent_baseline_version,search_config_json,"
            "value_net_ref,deck_id,status,created_at) VALUES(?,?,?,?,?,?,?)",
            (oid, "v0.1", "{}", value_net_ref, None, status, "2026-08-03T00:00:00+00:00"))
    deckdb._write(conn, _apply)


def _finish_netcheck_games(conn, oid, wins, total=netcheck.NETCHECK_GAMES):
    """Mark the first `total` pending netcheck games done: `wins` candidate
    wins, the rest incumbent wins."""
    version = netcheck.cand_version(oid)
    def _apply(c):
        rows = c.execute(
            "SELECT id FROM games WHERE purpose='netcheck' AND agent_version_a=? "
            "ORDER BY id LIMIT ?", (version, total)).fetchall()
        assert len(rows) == total
        for i, row in enumerate(rows):
            c.execute("UPDATE games SET status='done', winner=? WHERE id=?",
                      (0 if i < wins else 1, row["id"]))
    deckdb._write(conn, _apply)


def test_constants_exact_values():
    assert netcheck.NETCHECK_GAMES == 100
    assert netcheck.NETCHECK_BAR == 0.55
    assert netcheck.NETCHECK_CAND_PREFIX == "netcheck-cand:"
    assert netcheck.NETCHECK_INC_PREFIX == "netcheck-inc:"


def test_init_db_creates_net_checks_on_virgin_db(tmp_path):
    conn = _connect(tmp_path)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "net_checks" in names


def test_ensure_schema_upgrades_legacy_db(tmp_path):
    conn = deckdb.connect(tmp_path / "legacy.db")
    def _apply(c):
        for ddl in deckdb._DDL_STATEMENTS:
            if "net_checks" not in ddl:
                c.execute(ddl)
    deckdb._write(conn, _apply)
    netcheck._ensure_schema(conn)
    netcheck._ensure_schema(conn)  # idempotent
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "net_checks" in names


def test_enqueue_creates_row_and_100_mirror_games(tmp_path):
    conn = _connect(tmp_path)
    _seed_world(conn)
    assert netcheck.enqueue_net_check(conn, "v0.1.1") == 100
    row = conn.execute(
        "SELECT candidate_net_ref, incumbent_net_ref, deck_id, games_planned, verdict "
        "FROM net_checks WHERE offspring_id='v0.1.1'").fetchone()
    assert row["candidate_net_ref"] == "w_new.json"
    assert row["incumbent_net_ref"] == "w_inc.json"
    assert row["deck_id"] == "dBase"
    assert row["games_planned"] == 100
    assert row["verdict"] == "pending"
    game = conn.execute("SELECT * FROM games WHERE purpose='netcheck' LIMIT 1").fetchone()
    assert game["deck_a_id"] == "dBase"
    assert game["deck_b_id"] == "dBase"
    assert game["agent_version_a"] == "netcheck-cand:v0.1.1"
    assert game["agent_version_b"] == "netcheck-inc:v0.1.1"
    assert game["priority"] == netcheck.NETCHECK_PRIORITY
    # resumable top-up: a second call enqueues only the shortfall (0 here)
    assert netcheck.enqueue_net_check(conn, "v0.1.1") == 0


def test_enqueue_noop_unless_training(tmp_path):
    conn = _connect(tmp_path)
    _seed_world(conn, status="matching")
    assert netcheck.enqueue_net_check(conn, "v0.1.1") == 0
    row = conn.execute("SELECT * FROM net_checks WHERE offspring_id='v0.1.1'").fetchone()
    assert row is None


def test_auto_adopt_when_incumbent_missing(tmp_path):
    conn = _connect(tmp_path)
    _seed_world(conn, incumbent_net_ref=None)
    assert netcheck.enqueue_net_check(conn, "v0.1.1") == 0
    row = conn.execute(
        "SELECT verdict, candidate_net_ref, incumbent_net_ref FROM net_checks "
        "WHERE offspring_id='v0.1.1'").fetchone()
    assert row["verdict"] == "auto"
    assert row["candidate_net_ref"] == "w_new.json"
    assert row["incumbent_net_ref"] is None
    off = conn.execute(
        "SELECT status, value_net_ref FROM offspring WHERE id='v0.1.1'").fetchone()
    assert off["status"] == "queued_for_match"
    assert off["value_net_ref"] == "w_new.json"


def test_auto_adopt_when_refs_equal(tmp_path):
    conn = _connect(tmp_path)
    _seed_world(conn, value_net_ref="w_inc.json", incumbent_net_ref="w_inc.json")
    assert netcheck.enqueue_net_check(conn, "v0.1.1") == 0
    row = conn.execute(
        "SELECT verdict FROM net_checks WHERE offspring_id='v0.1.1'").fetchone()
    assert row["verdict"] == "auto"
    off = conn.execute(
        "SELECT status, value_net_ref FROM offspring WHERE id='v0.1.1'").fetchone()
    assert off["status"] == "queued_for_match"
    assert off["value_net_ref"] == "w_inc.json"


def test_resolve_adopt_at_55_wins(tmp_path):
    conn = _connect(tmp_path)
    _seed_world(conn)
    netcheck.enqueue_net_check(conn, "v0.1.1")
    _finish_netcheck_games(conn, "v0.1.1", wins=55)  # 55/100 = 0.55 >= 0.55
    assert netcheck.resolve_net_check(conn, "v0.1.1") == "adopt"
    off = conn.execute(
        "SELECT status, value_net_ref FROM offspring WHERE id='v0.1.1'").fetchone()
    assert off["status"] == "queued_for_match"
    assert off["value_net_ref"] == "w_new.json"


def test_resolve_reject_at_54_wins_keeps_incumbent(tmp_path):
    conn = _connect(tmp_path)
    _seed_world(conn)
    netcheck.enqueue_net_check(conn, "v0.1.1")
    _finish_netcheck_games(conn, "v0.1.1", wins=54)  # 54/100 = 0.54 < 0.55
    assert netcheck.resolve_net_check(conn, "v0.1.1") == "reject"
    off = conn.execute(
        "SELECT status, value_net_ref FROM offspring WHERE id='v0.1.1'").fetchone()
    assert off["status"] == "queued_for_match"
    assert off["value_net_ref"] == "w_inc.json"  # swapped back in the same txn
    # settled verdict is never recomputed/flipped
    assert netcheck.resolve_net_check(conn, "v0.1.1") == "reject"


def test_resolve_pending_and_absent_shapes(tmp_path):
    conn = _connect(tmp_path)
    _seed_world(conn)
    assert netcheck.resolve_net_check(conn, "v0.1.1") == "absent"
    assert netcheck.net_status(conn, "v0.1.1") == ("absent", 0, netcheck.NETCHECK_GAMES, None)
    netcheck.enqueue_net_check(conn, "v0.1.1")
    _finish_netcheck_games(conn, "v0.1.1", wins=10, total=40)  # only 40 of 100 done
    assert netcheck.resolve_net_check(conn, "v0.1.1") == "pending"
    verdict, done, planned, wr = netcheck.net_status(conn, "v0.1.1")
    assert (verdict, done, planned, wr) == ("pending", 40, 100, None)


def test_draws_count_as_losses(tmp_path):
    conn = _connect(tmp_path)
    _seed_world(conn)
    netcheck.enqueue_net_check(conn, "v0.1.1")
    version = netcheck.cand_version("v0.1.1")
    def _apply(c):
        rows = c.execute(
            "SELECT id FROM games WHERE purpose='netcheck' AND agent_version_a=? "
            "ORDER BY id", (version,)).fetchall()
        for i, row in enumerate(rows):
            # 54 wins, 46 draws -> wr 0.54 -> reject (a draw is not a win)
            c.execute("UPDATE games SET status='done', winner=? WHERE id=?",
                      (0 if i < 54 else 2, row["id"]))
    deckdb._write(conn, _apply)
    assert netcheck.resolve_net_check(conn, "v0.1.1") == "reject"


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
        results.append(netcheck.enqueue_net_check(c, "v0.1.1"))
    t1 = threading.Thread(target=_call)
    t2 = threading.Thread(target=_call)
    t1.start(); t2.start(); t1.join(); t2.join()
    total = conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='netcheck'").fetchone()[0]
    assert total == 100
    # BEGIN IMMEDIATE serializes: winner enqueues 100, loser reads the
    # committed 100 and tops up 0 -- never 200 from two 100-game passes.
    assert sorted(results) == [0, 100]
    n = conn.execute("SELECT COUNT(*) FROM net_checks").fetchone()[0]
    assert n == 1


def test_resolve_survives_concurrent_calls(tmp_path):
    """INTERLEAVED (`.claude/rules/single-actor-worker-tests.md`): two racing
    `resolve_net_check` calls must perform the verdict write EXACTLY ONCE.

    Discriminates on the mutating statement, not the return value: the
    lost-race branch returns the settled verdict, so `verdicts ==
    ['adopt','adopt']` holds under ANY implementation (including a fully
    non-atomic one) and pins nothing. Counting `UPDATE net_checks` executions
    does discriminate -- under `BEGIN IMMEDIATE` the loser's own
    `verdict != 'pending'` read runs strictly after the winner COMMITs, so it
    returns early and never reaches the UPDATE (1 execution); de-transactionalize
    `resolve_net_check` and both racers pass the pending-read and both execute
    the UPDATE (2 executions) -- see the RED receipt in this slice's
    pass2-fixwave-report.md."""
    db = tmp_path / "t.db"
    conn = deckdb.connect(db)
    deckdb.init_db(conn)
    _seed_world(conn)
    netcheck.enqueue_net_check(conn, "v0.1.1")
    _finish_netcheck_games(conn, "v0.1.1", wins=55)  # 0.55 -> adopt (bar is >=)

    verdicts, updates = race_two(
        db,
        lambda c: netcheck.resolve_net_check(c, "v0.1.1"),
        lambda s: s.startswith("UPDATE NET_CHECKS"),
    )

    assert updates == 1, f"verdict write must execute exactly once, saw {updates}"
    assert verdicts == ["adopt", "adopt"]  # loser reports the settled verdict
    row = conn.execute(
        "SELECT resolved_at, verdict FROM net_checks WHERE offspring_id='v0.1.1'").fetchone()
    assert row["resolved_at"] is not None and row["verdict"] == "adopt"
    off = conn.execute(
        "SELECT value_net_ref FROM offspring WHERE id='v0.1.1'").fetchone()
    assert off["value_net_ref"] == "w_new.json"  # adopt keeps the new net


def test_resolve_reject_swap_back_happens_exactly_once(tmp_path):
    """INTERLEAVED, REJECT path -- the only NON-IDEMPOTENT side effect in this
    module. `adopt` just advances status (replaying it is harmless); `reject`
    OVERWRITES `offspring.value_net_ref` with the incumbent ref. If two racers
    both reached that swap-back the second would write over whatever the first
    left, and a third party's interleaved write would be lost outright.

    Counts `UPDATE offspring SET value_net_ref` executions: exactly one."""
    db = tmp_path / "t.db"
    conn = deckdb.connect(db)
    deckdb.init_db(conn)
    _seed_world(conn)
    netcheck.enqueue_net_check(conn, "v0.1.1")
    _finish_netcheck_games(conn, "v0.1.1", wins=54)  # 0.54 < 0.55 -> reject

    verdicts, swaps = race_two(
        db,
        lambda c: netcheck.resolve_net_check(c, "v0.1.1"),
        lambda s: s.startswith("UPDATE OFFSPRING SET VALUE_NET_REF"),
    )

    assert swaps == 1, f"net swap-back must execute exactly once, saw {swaps}"
    assert verdicts == ["reject", "reject"]
    off = conn.execute(
        "SELECT value_net_ref, status FROM offspring WHERE id='v0.1.1'").fetchone()
    assert off["value_net_ref"] == "w_inc.json"  # reverted to the incumbent net
    assert off["status"] == "queued_for_match"
