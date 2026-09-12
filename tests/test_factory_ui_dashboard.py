"""Tests for the UI live status page (tournament T19) -- the sole surface
replacing `experiments/factory/dashboard.html` (PD-B override, Global
Constraints / Plan-Review Decisions).

Covers: `GET /status` returns 200 with baseline version + offspring counts +
census progress; the handler NEVER writes to the DB (`PRAGMA query_only=ON`
enforced at the SQLite level, not just by convention); paused/SUBMIT_HOLD
flags read from the existing state files; submission-counter reporting;
auth-state derived from `watch.log` text (never a live Kaggle API call --
mirrors `dashboard.py`'s digest-text `auth_dead` parsing convention); and
census/offspring progress counts.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
import urllib.request
from http.server import HTTPServer

import pytest

from ptcg.factory import anchor, deckdb, loop_state, ui_server

_WATER_ENERGY_ID = 3  # "Basic {W} Energy" -- stable real card id (see CLAUDE.md)

_NOW = dt.datetime(2026, 7, 24, 12, 0, 0, tzinfo=dt.timezone.utc)


def _seeded_db(tmp_path):
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)
    return db


def _seed_single(c, name: str, *, status: str = "active",
                 games_played: int = 0, rating: float | None = None) -> None:
    """A single-core concept + its canonical deck + coverage row (mirrors
    `test_factory_ui_server.py`'s `_seed_concept`, adapted to single-core
    `cores` shape -- `[name]`, matching `builder.concept_id`/`seed_census`'s
    real encoding, verified in `census.py`)."""
    c.execute(
        "INSERT INTO concepts(id,cores,status) VALUES(?,?,?)",
        (name, json.dumps([name]), status),
    )
    c.execute(
        "INSERT INTO decks(id,concept_id,cards,shell_variant) VALUES(?,?,?,0)",
        (f"deck-{name}", name, json.dumps([_WATER_ENERGY_ID] * 3)),
    )
    c.execute(
        "INSERT INTO coverage(concept_id,games_played,distinct_opponents,rating) "
        "VALUES(?,?,1,?)",
        (name, games_played, rating),
    )


def _seed_pair(c, pair_id: str, cores: list[str], *, status: str = "untested",
              activated: bool = False) -> None:
    """A two-core concept, `activated` controlling whether it also gets a
    `decks`/`coverage` row (mirrors `census.activate_pair_concepts`'s real
    dormant-vs-activated distinction: no `decks` row == dormant)."""
    c.execute(
        "INSERT INTO concepts(id,cores,status) VALUES(?,?,?)",
        (pair_id, json.dumps(sorted(cores)), status),
    )
    if activated:
        c.execute(
            "INSERT INTO decks(id,concept_id,cards,shell_variant) VALUES(?,?,?,0)",
            (f"deck-{pair_id}", pair_id, json.dumps([_WATER_ENERGY_ID] * 3)),
        )
        c.execute(
            "INSERT INTO coverage(concept_id,games_played,distinct_opponents,rating) "
            "VALUES(?,0,0,NULL)",
            (pair_id,),
        )


# --- status_snapshot (function-level) -------------------------------------


def test_status_snapshot_no_baseline_yet(tmp_path):
    db = _seeded_db(tmp_path)

    snap = ui_server.status_snapshot(db, root=tmp_path, now=_NOW)

    assert snap["baseline_version"] is None


def test_status_snapshot_reports_founded_baseline(tmp_path):
    db = _seeded_db(tmp_path)
    deckdb._write(db, lambda c: _seed_single(c, "Pikachu"))
    loop_state.set_founding_baseline(db, "deck-Pikachu", {"kind": "search-net"})

    snap = ui_server.status_snapshot(db, root=tmp_path, now=_NOW)

    assert snap["baseline_version"] == "v0.1"


def test_status_snapshot_offspring_counts_by_status(tmp_path):
    db = _seeded_db(tmp_path)
    deckdb._write(db, lambda c: _seed_single(c, "Pikachu"))
    loop_state.set_founding_baseline(db, "deck-Pikachu", {"kind": "search-net"})
    loop_state.insert_offspring(db, "off-1", json.dumps({}), None)
    loop_state.insert_offspring(db, "off-2", json.dumps({}), None)
    loop_state.set_offspring_status(db, "off-2", "survivor")

    snap = ui_server.status_snapshot(db, root=tmp_path, now=_NOW)

    assert snap["offspring_counts"]["training"] == 1
    assert snap["offspring_counts"]["survivor"] == 1
    assert snap["offspring_counts"]["trashed"] == 0  # zero-filled, not just absent


def test_status_snapshot_census_progress(tmp_path):
    db = _seeded_db(tmp_path)

    def _seed(c):
        _seed_single(c, "Above1", games_played=ui_server.SCREENING_FLOOR)
        _seed_single(c, "Above2", games_played=ui_server.SCREENING_FLOOR + 5)
        _seed_single(c, "Below1", games_played=ui_server.SCREENING_FLOOR - 1)
        _seed_pair(c, "p-active", ["Above1", "Above2"], status="active", activated=True)
        _seed_pair(c, "p-dormant", ["Above1", "Below1"], status="untested", activated=False)

    deckdb._write(db, _seed)

    snap = ui_server.status_snapshot(db, root=tmp_path, now=_NOW)

    assert snap["census"]["singles_total"] == 3
    assert snap["census"]["singles_played"] == 2  # Above1 + Above2 cleared the floor
    assert snap["census"]["pairs_total"] == 2
    assert snap["census"]["pairs_activated"] == 1  # only p-active has a decks row


def test_status_snapshot_census_singles_total_excludes_anchor_concept(tmp_path):
    """The anchor concept (`anchor.ensure_anchor_deck`) is single-core
    (`cores = '["anchor"]'`, length 1) but deliberately gets NO `coverage`
    row (see anchor.py's docstring -- a coverage row would dip
    `census.census_complete` at every go-live). Mirrors
    `test_ensure_anchor_deck_does_not_dip_census_complete`
    (tests/test_factory_anchor.py) for the `/status` `singles_total`
    denominator: registering the anchor deck must not change it, matching
    `census_complete`'s own population (single-core concepts WITH a
    coverage row)."""
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    deckdb._write(db, lambda c: _seed_single(c, "Above1", games_played=5))

    # `status_snapshot` sets `PRAGMA query_only=ON` on the connection it's
    # given (permanent for that connection's lifetime), so the "before" read
    # and the anchor write below each need their own connection to the same
    # file rather than reusing `db` after it has been marked read-only.
    before = ui_server.status_snapshot(deckdb.connect(db_path), root=tmp_path, now=_NOW)
    assert before["census"]["singles_total"] == 1

    anchor.ensure_anchor_deck(db)

    after = ui_server.status_snapshot(deckdb.connect(db_path), root=tmp_path, now=_NOW)
    assert after["census"]["singles_total"] == 1  # unchanged -- anchor excluded


def test_status_snapshot_games_last_hour(tmp_path):
    db = _seeded_db(tmp_path)

    def _seed(c):
        _seed_single(c, "A")
        _seed_single(c, "B")
        # Inside the 1h window.
        c.execute(
            "INSERT INTO games(deck_a_id,deck_b_id,agent_version_a,agent_version_b,"
            "purpose,status,winner,timestamp) VALUES('deck-A','deck-B','v0.1','v0.1',"
            "'screening','done',0,?)",
            ((_NOW - dt.timedelta(minutes=30)).isoformat(),),
        )
        # Outside the 1h window -- must not be counted.
        c.execute(
            "INSERT INTO games(deck_a_id,deck_b_id,agent_version_a,agent_version_b,"
            "purpose,status,winner,timestamp) VALUES('deck-A','deck-B','v0.1','v0.1',"
            "'screening','done',0,?)",
            ((_NOW - dt.timedelta(hours=2)).isoformat(),),
        )
        # Still pending -- must not be counted regardless of timestamp.
        c.execute(
            "INSERT INTO games(deck_a_id,deck_b_id,agent_version_a,agent_version_b,"
            "purpose,status) VALUES('deck-A','deck-B','v0.1','v0.1','screening','pending')"
        )

    deckdb._write(db, _seed)

    snap = ui_server.status_snapshot(db, root=tmp_path, now=_NOW)

    assert snap["games_last_hour"] == 1


def test_status_snapshot_paused_and_submit_hold_flags(tmp_path):
    db = _seeded_db(tmp_path)
    factory_dir = tmp_path / "experiments" / "factory"
    factory_dir.mkdir(parents=True)
    (factory_dir / "PAUSE").write_text("", encoding="utf-8")
    (factory_dir / "SUBMIT_HOLD").write_text("", encoding="utf-8")

    snap = ui_server.status_snapshot(db, root=tmp_path, now=_NOW)

    assert snap["paused"] is True
    assert snap["submit_hold"] is True


def test_status_snapshot_paused_false_when_no_file(tmp_path):
    db = _seeded_db(tmp_path)

    snap = ui_server.status_snapshot(db, root=tmp_path, now=_NOW)

    assert snap["paused"] is False
    assert snap["submit_hold"] is False


def test_status_snapshot_submission_counter(tmp_path):
    db = _seeded_db(tmp_path)
    factory_dir = tmp_path / "experiments" / "factory"
    factory_dir.mkdir(parents=True)
    (factory_dir / "submission_counter.json").write_text(
        json.dumps({"date": "2026-07-24", "count": 3}), encoding="utf-8"
    )

    snap = ui_server.status_snapshot(db, root=tmp_path, now=_NOW)

    assert snap["submission_counter"] == {"used": 3, "cap": ui_server.HARD_DAILY_CAP}


def test_status_snapshot_submission_counter_different_day_reads_zero(tmp_path):
    db = _seeded_db(tmp_path)
    factory_dir = tmp_path / "experiments" / "factory"
    factory_dir.mkdir(parents=True)
    (factory_dir / "submission_counter.json").write_text(
        json.dumps({"date": "2026-07-23", "count": 5}), encoding="utf-8"
    )

    snap = ui_server.status_snapshot(db, root=tmp_path, now=_NOW)

    assert snap["submission_counter"]["used"] == 0  # yesterday's count doesn't carry over


# --- read-only enforcement -------------------------------------------------


def test_status_snapshot_connection_is_read_only(tmp_path):
    db = _seeded_db(tmp_path)

    ui_server.status_snapshot(db, root=tmp_path, now=_NOW)

    with pytest.raises(sqlite3.OperationalError):
        db.execute("INSERT INTO concepts(id,cores) VALUES('x','[]')")


# --- auth state (log-derived, never a live Kaggle call) --------------------


def test_auth_state_unknown_without_log(tmp_path):
    state = ui_server._auth_state(tmp_path / "watch.log")
    assert state == {"state": "unknown", "detail": None}


def test_auth_state_dead_from_log(tmp_path):
    log_path = tmp_path / "watch.log"
    log_path.write_text(
        "[2026-07-24T11:00] cycle: noop\n"
        "[2026-07-24T12:00] AUTH-DEAD: kaggle auth check failed - skipping submit "
        "phase (re-auth needed): expired token\n"
        "[2026-07-24T12:00] submit-scheduler: AUTH=auth-dead\n",
        encoding="utf-8",
    )

    state = ui_server._auth_state(log_path)

    assert state["state"] == "dead"
    assert "expired token" in state["detail"]


def test_auth_state_ok_after_recovery(tmp_path):
    log_path = tmp_path / "watch.log"
    log_path.write_text(
        "[2026-07-24T11:00] AUTH-DEAD: kaggle auth check failed - skipping submit "
        "phase (re-auth needed): expired token\n"
        "[2026-07-24T11:00] submit-scheduler: AUTH=auth-dead\n"
        "[2026-07-24T16:00] submit-scheduler: no-op\n",
        encoding="utf-8",
    )

    state = ui_server._auth_state(log_path)

    assert state == {"state": "ok", "detail": None}


# --- HTTP routing (real ephemeral server) ----------------------------------


def _start_server(conn_factory, **kwargs):
    handler = ui_server.make_app(conn_factory, **kwargs)
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _stop_server(httpd, thread):
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def test_get_status_returns_200_with_baseline_offspring_census(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)

    def _seed(c):
        _seed_single(c, "Pikachu")

    deckdb._write(db, _seed)
    loop_state.set_founding_baseline(db, "deck-Pikachu", {"kind": "search-net"})
    loop_state.insert_offspring(db, "off-1", json.dumps({}), None)
    db.close()

    httpd, thread = _start_server(
        lambda: deckdb.connect(db_path), root=tmp_path, now=lambda: _NOW
    )
    try:
        port = httpd.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=5) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
        assert "v0.1" in body          # baseline version
        assert "training: 1" in body   # offspring status name + real count
        assert "singles played" in body.lower()  # census progress
    finally:
        _stop_server(httpd, thread)


def test_get_status_root_route_still_works(tmp_path):
    """Regression guard: adding `/status` must not disturb the existing `/`
    bottom-10 route (T18) or the flat if/elif dispatch shape."""
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            assert resp.status == 200
    finally:
        _stop_server(httpd, thread)
