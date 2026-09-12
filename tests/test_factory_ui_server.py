"""Tests for the localhost bottom-10 Remove/Pass review UI (tournament T18).

Includes the decision-write atomicity test required by
`.claude/rules/single-actor-worker-tests.md` (Pattern INTERLEAVED-TEST): a
concurrent reader racing `apply_decision` must never observe a half-applied
decision (decision row present but the concept's status not yet flipped).
"""

from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import HTTPServer

import pytest

from ptcg.factory import deckdb, ui_server

_WATER_ENERGY_ID = 3  # "Basic {W} Energy" -- stable real card id (see CLAUDE.md)


def _seed_concept(c, concept_id: str, deck_id: str, *, status: str = "active",
                   rating: float | None = 1.0, games_played: int = 20,
                   cards: list[int] | None = None) -> None:
    cards = cards if cards is not None else [_WATER_ENERGY_ID] * 3
    c.execute(
        "INSERT INTO concepts(id,cores,status) VALUES(?,?,?)",
        (concept_id, json.dumps([concept_id]), status),
    )
    c.execute(
        "INSERT INTO decks(id,concept_id,cards,shell_variant) VALUES(?,?,?,0)",
        (deck_id, concept_id, json.dumps(cards)),
    )
    c.execute(
        "INSERT INTO coverage(concept_id,games_played,distinct_opponents,rating) "
        "VALUES(?,?,3,?)",
        (concept_id, games_played, rating),
    )


def _seeded_db(tmp_path):
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)
    return db


# --- bottom_ten --------------------------------------------------------


def test_bottom_ten_lowest_rated_with_min_games(tmp_path):
    db = _seeded_db(tmp_path)

    def _seed(c):
        # 12 active, decisively-rated concepts at increasing rating.
        for i in range(12):
            _seed_concept(c, f"c{i}", f"d{i}", rating=float(i), games_played=20)
        # Below the min-games floor -- must be excluded even though its
        # rating (-5.0) would otherwise sort first.
        _seed_concept(c, "cLow", "dLow", rating=-5.0, games_played=5)
        # Not active -- must be excluded regardless of rating.
        _seed_concept(c, "cUntested", "dUntested", status="untested", rating=-9.0,
                       games_played=20)
        # No decisive rating yet -- must be excluded.
        _seed_concept(c, "cNull", "dNull", rating=None, games_played=20)

    deckdb._write(db, _seed)

    rows = ui_server.bottom_ten(db, min_games=15)

    assert [r["deck_id"] for r in rows] == [f"d{i}" for i in range(10)]
    assert [r["rating"] for r in rows] == [float(i) for i in range(10)]
    assert rows[0]["cards"] == [_WATER_ENERGY_ID] * 3
    assert rows[0]["games_played"] == 20
    assert rows[0]["concept_id"] == "c0"


def test_bottom_ten_dedupes_to_canonical_shell_variant(tmp_path):
    db = _seeded_db(tmp_path)

    def _seed(c):
        _seed_concept(c, "c0", "d0-sv0", rating=1.0, games_played=20)
        # A second shell-variant deck for the SAME concept must not produce
        # a second bottom-10 row (coverage/rating is per-concept).
        c.execute(
            "INSERT INTO decks(id,concept_id,cards,shell_variant) VALUES('d0-sv1','c0',?,1)",
            (json.dumps([_WATER_ENERGY_ID]),),
        )

    deckdb._write(db, _seed)

    rows = ui_server.bottom_ten(db, min_games=15)
    assert [r["deck_id"] for r in rows] == ["d0-sv0"]  # canonical (lowest shell_variant) only


# --- apply_decision ------------------------------------------------------


def test_apply_remove_culls_concept_and_logs_decision(tmp_path):
    db = _seeded_db(tmp_path)
    deckdb._write(db, lambda c: _seed_concept(c, "c1", "d1"))

    ui_server.apply_decision(db, "d1", "remove", actor="brad")

    status = db.execute("SELECT status FROM concepts WHERE id='c1'").fetchone()[0]
    assert status == "culled"
    decision = db.execute(
        "SELECT deck_id, action, actor FROM decisions WHERE deck_id='d1'"
    ).fetchone()
    assert decision["deck_id"] == "d1"
    assert decision["action"] == "remove"
    assert decision["actor"] == "brad"


def test_apply_pass_keeps_active_and_logs(tmp_path):
    db = _seeded_db(tmp_path)
    deckdb._write(db, lambda c: _seed_concept(c, "c1", "d1"))

    ui_server.apply_decision(db, "d1", "pass", actor="brad")

    status = db.execute("SELECT status FROM concepts WHERE id='c1'").fetchone()[0]
    assert status == "active"
    decision = db.execute(
        "SELECT action FROM decisions WHERE deck_id='d1'"
    ).fetchone()
    assert decision["action"] == "pass"


def test_apply_decision_rejects_unknown_action(tmp_path):
    db = _seeded_db(tmp_path)
    deckdb._write(db, lambda c: _seed_concept(c, "c1", "d1"))

    with pytest.raises(ValueError, match="remove.*pass"):
        ui_server.apply_decision(db, "d1", "cull")


def test_apply_decision_unknown_deck_raises(tmp_path):
    db = _seeded_db(tmp_path)
    with pytest.raises(ValueError, match="no deck"):
        ui_server.apply_decision(db, "does-not-exist", "remove")


def test_apply_decision_remove_on_finalist_refused(tmp_path):
    # IMPORTANT-2 fix (2026-08-05): the legacy bottom-10 `/decision` path
    # was the one remaining route with no finalist guard at all.
    db = _seeded_db(tmp_path)
    deckdb._write(
        db, lambda c: _seed_concept(c, "cAnchorLegacy", "dAnchorLegacy", status="finalist")
    )
    with pytest.raises(ui_server.ui_actions.FinalistProtectedError):
        ui_server.apply_decision(db, "dAnchorLegacy", "remove")
    status = db.execute(
        "SELECT status FROM concepts WHERE id='cAnchorLegacy'"
    ).fetchone()[0]
    assert status == "finalist"
    assert db.execute(
        "SELECT COUNT(*) FROM decisions WHERE deck_id='dAnchorLegacy'"
    ).fetchone()[0] == 0


def test_apply_decision_pass_on_finalist_is_unaffected(tmp_path):
    # `pass` never changes status, so the finalist guard only fires for
    # `remove` -- confirm `pass` still logs normally against a finalist.
    db = _seeded_db(tmp_path)
    deckdb._write(
        db, lambda c: _seed_concept(c, "cAnchorPass", "dAnchorPass", status="finalist")
    )
    ui_server.apply_decision(db, "dAnchorPass", "pass")
    status = db.execute("SELECT status FROM concepts WHERE id='cAnchorPass'").fetchone()[0]
    assert status == "finalist"
    assert db.execute(
        "SELECT COUNT(*) FROM decisions WHERE deck_id='dAnchorPass'"
    ).fetchone()[0] == 1


# --- decision-write atomicity (INTERLEAVED) ------------------------------


def test_decision_write_atomic_under_concurrent_scheduler_read(  # INTERLEAVED
    tmp_path, monkeypatch
):
    """A concurrent reader (standing in for the scheduler's own SELECT of
    active decks) must never observe a half-applied `apply_decision`:
    either the decision row is ABSENT and the concept is still `active`
    (pre-commit snapshot), or the decision row is PRESENT and the concept
    is `culled` (post-commit snapshot) -- never a mix. `apply_decision`'s
    real transaction commits too fast to race naturally, so `deckdb._write`
    is monkeypatched to widen the window with a sleep between the write and
    the COMMIT -- the widened window still runs the REAL `apply_decision`
    code end to end (Pattern INTERLEAVED-TEST).
    """
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    deckdb._write(db, lambda c: _seed_concept(c, "c1", "d1"))
    db.close()

    def _slow_write(conn, fn):
        conn.execute("BEGIN IMMEDIATE")
        try:
            result = fn(conn)
            time.sleep(0.2)  # widen the race window for the reader thread below
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise

    monkeypatch.setattr(deckdb, "_write", _slow_write)

    seen: list[tuple[int, str]] = []
    stop = threading.Event()

    def _reader():
        b = deckdb.connect(db_path)
        while not stop.is_set():
            row = b.execute(
                "SELECT (SELECT COUNT(*) FROM decisions WHERE deck_id='d1') AS n, "
                "(SELECT status FROM concepts WHERE id='c1') AS status"
            ).fetchone()
            seen.append((row["n"], row["status"]))

    reader = threading.Thread(target=_reader)
    reader.start()
    time.sleep(0.05)  # let the reader observe the pre-write snapshot at least once

    writer_conn = deckdb.connect(db_path)
    ui_server.apply_decision(writer_conn, "d1", "remove", actor="brad")

    time.sleep(0.05)
    stop.set()
    reader.join(timeout=5)

    half_applied = [pair for pair in seen if pair[0] > 0 and pair[1] == "active"]
    assert half_applied == [], f"reader observed a half-applied decision: {half_applied}"
    assert (0, "active") in seen  # the pre-write snapshot was actually observed
    assert (1, "culled") in seen  # the post-commit snapshot was actually observed


# --- virgin-dir + binding -------------------------------------------------


def test_ui_decision_log_virgin_dir(tmp_path):  # VIRGIN-DIR-TEST for any UI-owned file
    fresh = tmp_path / "never" / "created" / "here" / "t.db"
    assert not fresh.parent.exists()

    conn = deckdb.connect(fresh)  # must not raise FileNotFoundError
    deckdb.init_db(conn)
    deckdb._write(conn, lambda c: _seed_concept(c, "c1", "d1"))

    ui_server.apply_decision(conn, "d1", "remove")

    assert fresh.exists()
    status = conn.execute("SELECT status FROM concepts WHERE id='c1'").fetchone()[0]
    assert status == "culled"


def test_server_binds_localhost_only():
    handler = ui_server.make_app(lambda: None)
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    try:
        assert httpd.server_address[0] == "127.0.0.1"
    finally:
        httpd.server_close()


# --- HTTP routing (real ephemeral server) --------------------------------


def _start_server(conn_factory):
    handler = ui_server.make_app(conn_factory)
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _stop_server(httpd, thread):
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def test_get_root_renders_bottom_ten_with_card_names(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    deckdb._write(db, lambda c: _seed_concept(c, "c1", "d1"))
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
        assert "d1" in body
        assert "Basic {W} Energy" in body
    finally:
        _stop_server(httpd, thread)


def test_get_unknown_path_404(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=5)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        _stop_server(httpd, thread)


def test_post_decision_applies_and_redirects(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    deckdb._write(db, lambda c: _seed_concept(c, "c1", "d1"))
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        body = urllib.parse.urlencode({"deck_id": "d1", "action": "remove"}).encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST", "/decision", body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = conn.getresponse()
        assert resp.status == 303
        location = resp.getheader("Location") or ""
        split = urllib.parse.urlsplit(location)
        assert split.path == "/"
        assert urllib.parse.parse_qs(split.query)["flash"] == ["Removed d1"]
        resp.read()
        conn.close()

        checker = deckdb.connect(db_path)
        status = checker.execute("SELECT status FROM concepts WHERE id='c1'").fetchone()[0]
        assert status == "culled"
    finally:
        _stop_server(httpd, thread)


def test_post_decision_missing_fields_is_bad_request(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST", "/decision", body=b"",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = conn.getresponse()
        assert resp.status == 400
        resp.read()
        conn.close()
    finally:
        _stop_server(httpd, thread)


def test_post_decision_remove_finalist_409(tmp_path):
    # IMPORTANT-2 fix (2026-08-05): the legacy `/decision` endpoint (bottom-
    # 10 review) had NO finalist guard at all before this fix -- an operator
    # (or a malformed form post) could cull the anchor through it.
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    deckdb._write(db, lambda c: _seed_concept(c, "cAnchorHttp", "dAnchorHttp", status="finalist"))
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        body = urllib.parse.urlencode({"deck_id": "dAnchorHttp", "action": "remove"}).encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST", "/decision", body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = conn.getresponse()
        assert resp.status == 409
        resp.read()
        conn.close()

        checker = deckdb.connect(db_path)
        status = checker.execute(
            "SELECT status FROM concepts WHERE id='cAnchorHttp'"
        ).fetchone()[0]
        assert status == "finalist"
        assert checker.execute(
            "SELECT COUNT(*) FROM decisions WHERE deck_id='dAnchorHttp'"
        ).fetchone()[0] == 0
    finally:
        _stop_server(httpd, thread)


# --- pool/search/concept-decision/bulk routing (task 6) -------------------


def _post(port, path, fields):
    body = urllib.parse.urlencode(fields).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(
        "POST", path, body=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp = conn.getresponse()
    read_body = resp.read().decode("utf-8")
    conn.close()
    return resp, read_body


def test_pool_route_lists_active_and_protects_anchor(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    deckdb._write(
        db,
        lambda c: (
            _seed_concept(c, "cAnchor", "dAnchor", status="finalist"),
            _seed_concept(c, "cActive", "dActive", status="active"),
        ),
    )
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/pool", timeout=5) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
        assert "cActive" in body
        assert "cAnchor" in body
        assert "protected" in body  # anchor badge
        # Only the non-anchor row gets a /concept-decision Remove form.
        assert body.count('action="/concept-decision"') == 1
    finally:
        _stop_server(httpd, thread)


def test_search_route_defaults_and_tab(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    deckdb._write(db, lambda c: _seed_concept(c, "cAbomasnow", "dAbom", status="untested"))
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/search?q=abom", timeout=5
        ) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
        assert "cAbomasnow" in body

        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/search?q=x&status=bogus", timeout=5
            )
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
    finally:
        _stop_server(httpd, thread)


def test_concept_decision_remove_redirects_to_origin(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    deckdb._write(db, lambda c: _seed_concept(c, "cX", "dX", status="untested"))
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        resp, _ = _post(
            port, "/concept-decision",
            {"concept_id": "cX", "action": "remove", "from": "search",
             "q": "a", "status": "untested"},
        )
        assert resp.status == 303
        location = resp.getheader("Location") or ""
        split = urllib.parse.urlsplit(location)
        assert split.path == "/search"
        qs = urllib.parse.parse_qs(split.query)
        assert qs["q"] == ["a"]
        assert qs["status"] == ["untested"]
        assert qs["flash"] == ["Removed cX"]

        checker = deckdb.connect(db_path)
        status = checker.execute("SELECT status FROM concepts WHERE id='cX'").fetchone()[0]
        assert status == "culled"
    finally:
        _stop_server(httpd, thread)


def test_concept_decision_pool_origin_carries_problems_filter(tmp_path):
    # Whole-branch review MEDIUM finding: cull from the filtered pool view
    # (/pool?problems=1) must redirect back to /pool?problems=1, not the
    # unfiltered /pool -- otherwise the reviewer's filter silently resets
    # on every action.
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    deckdb._write(db, lambda c: _seed_concept(c, "cY", "dY", status="untested"))
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        resp, _ = _post(
            port, "/concept-decision",
            {"concept_id": "cY", "action": "remove", "from": "pool", "problems": "1"},
        )
        assert resp.status == 303
        location = resp.getheader("Location") or ""
        split = urllib.parse.urlsplit(location)
        assert split.path == "/pool"
        qs = urllib.parse.parse_qs(split.query)
        assert qs["problems"] == ["1"]
        assert qs["flash"] == ["Removed cY"]
    finally:
        _stop_server(httpd, thread)


def test_concept_decision_pool_origin_without_filter_omits_problems_param(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    deckdb._write(db, lambda c: _seed_concept(c, "cZ", "dZ", status="untested"))
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        resp, _ = _post(
            port, "/concept-decision",
            {"concept_id": "cZ", "action": "remove", "from": "pool"},
        )
        location = resp.getheader("Location") or ""
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
        assert "problems" not in qs
    finally:
        _stop_server(httpd, thread)


def test_concept_decision_finalist_409(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    deckdb._write(db, lambda c: _seed_concept(c, "cAnchor2", "dAnchor2", status="finalist"))
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        resp, _ = _post(
            port, "/concept-decision",
            {"concept_id": "cAnchor2", "action": "remove", "from": "pool"},
        )
        assert resp.status == 409

        checker = deckdb.connect(db_path)
        status = checker.execute(
            "SELECT status FROM concepts WHERE id='cAnchor2'"
        ).fetchone()[0]
        assert status == "finalist"
    finally:
        _stop_server(httpd, thread)


def test_concept_decision_unknown_concept_404(tmp_path):
    # MINOR-7 fix (2026-08-05): spec's Error handling section promises 404
    # for an unknown concept id, distinct from the generic 400 every other
    # bad-input case gets. Was 400 (pinning test renamed from _400 to _404).
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        resp, _ = _post(
            port, "/concept-decision",
            {"concept_id": "does-not-exist", "action": "remove", "from": "pool"},
        )
        assert resp.status == 404
    finally:
        _stop_server(httpd, thread)


def test_bulk_confirm_shows_preview_count(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)

    def _seed(c):
        for i in range(3):
            _seed_concept(c, f"bq{i}", f"dbq{i}", status="untested")

    deckdb._write(db, _seed)
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        resp, body = _post(
            port, "/bulk-confirm",
            {"q": "bq", "tab": "untested", "action": "bulk-remove"},
        )
        assert resp.status == 200
        assert "3" in body
        assert 'value="bq"' in body
        assert 'value="untested"' in body
        assert 'value="bulk-remove"' in body
    finally:
        _stop_server(httpd, thread)


def test_bulk_decision_executes_and_reports_actual(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)

    def _seed(c):
        for i in range(3):
            _seed_concept(c, f"bq{i}", f"dbq{i}", status="untested")

    deckdb._write(db, _seed)
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        # /bulk-decision now 303-redirects to /search (PRG) instead of
        # rendering a 200 result page -- follow it (urllib auto-follows a
        # 303 for POST, converting to GET, per RFC 7231) to inspect the
        # final page body for the flash-carried count.
        data = urllib.parse.urlencode(
            {"q": "bq", "tab": "untested", "action": "bulk-remove"}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/bulk-decision", data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
        assert "Removed 3 concepts" in body

        checker = deckdb.connect(db_path)
        statuses = [
            row[0]
            for row in checker.execute(
                "SELECT status FROM concepts WHERE id IN ('bq0','bq1','bq2')"
            ).fetchall()
        ]
        assert statuses == ["culled", "culled", "culled"]
    finally:
        _stop_server(httpd, thread)


def test_bulk_decision_rejects_bad_combo(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    deckdb._write(db, lambda c: _seed_concept(c, "cCulled", "dCulled", status="culled"))
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        # bulk-remove requires tab='untested', not 'culled'.
        resp, _ = _post(
            port, "/bulk-decision",
            {"q": "cCulled", "tab": "culled", "action": "bulk-remove"},
        )
        assert resp.status == 400
    finally:
        _stop_server(httpd, thread)


_JUNK_CARDS = [3] * 35 + [721] * 2 + [722] * 4 + [723] * 4 + [1145] * 4 \
    + [1158] * 1 + [1205] * 2 + [1227] * 4 + [1235] * 4  # energy-heavy red

_CLEAN_CARDS = (
    [1031] * 4 + [1030] * 4 + [3] * 20 + [1121] * 4 + [1102] * 4
    + [1086] * 4 + [1224] * 4 + [1213] * 4 + [1182] * 4 + [1097] * 4
    + [43] * 4
)  # zero-flag fixture, rebuilt for the 2026-08-11 min-basics pool rule: the
# old champion-shaped fixture had basics_count=4 (< MIN_BASIC_CARDS=8), which
# now fires mulligan-risk red under the same rule the problems=1 filter is
# supposed to be testing. Swapped the 3x Pokegear 3.0 (1122) + 1x Hyper
# Aroma (1082) trainer lines for 4x Eevee (43, basic, colorless-only
# attacks, does not evolve and evolves from nothing) to reach basics_count=8
# without disturbing pokemon/trainer/energy counts or the Mega Starmie
# ex/Staryu evolution line. Verified via analyze_deck(_CLEAN_CARDS) ->
# flags=() (deck_quality.py, 2026-08-11).


def test_pool_problems_filter_shows_only_flagged(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)

    def _seed(c):
        _seed_concept(c, "cClean", "dClean", cards=list(_CLEAN_CARDS))
        _seed_concept(c, "cJunk", "dJunk", cards=list(_JUNK_CARDS))

    deckdb._write(db, _seed)
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/pool", timeout=5) as r:
            all_body = r.read().decode("utf-8")
        assert "cClean" in all_body and "cJunk" in all_body
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/pool?problems=1", timeout=5
        ) as r:
            filtered = r.read().decode("utf-8")
        assert "cJunk" in filtered
        assert "cClean" not in filtered
    finally:
        _stop_server(httpd, thread)


# --- PRG flash banners (task 9) --------------------------------------------


def _location_of(port: int, path: str, data: dict) -> str:
    """POST without following the redirect; return the Location header."""
    body = urllib.parse.urlencode(data).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST", path, body,
                     {"Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        assert resp.status == 303, f"expected 303, got {resp.status}"
        return resp.getheader("Location") or ""
    finally:
        conn.close()


def test_decision_redirect_carries_flash_and_page_renders_banner(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    deckdb._write(db, lambda c: _seed_concept(c, "c1", "d1"))
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        loc = _location_of(port, "/decision", {"deck_id": "d1", "action": "pass"})
        split = urllib.parse.urlsplit(loc)
        assert split.path == "/"
        flash = urllib.parse.parse_qs(split.query)["flash"][0]
        assert flash == "Passed d1"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{loc}", timeout=5) as r:
            body = r.read().decode("utf-8")
        assert '<p class="flash">Passed d1</p>' in body
    finally:
        _stop_server(httpd, thread)


def test_bulk_decision_redirects_to_search_with_count_flash(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)

    def _seed(c):
        for i in range(3):
            _seed_concept(c, f"fq{i}", f"dfq{i}", status="untested")

    deckdb._write(db, _seed)
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        loc = _location_of(port, "/bulk-decision",
                           {"q": "fq", "tab": "untested", "action": "bulk-remove"})
        split = urllib.parse.urlsplit(loc)
        assert split.path == "/search"
        qs = urllib.parse.parse_qs(split.query)
        assert qs["q"] == ["fq"]
        assert qs["status"] == ["untested"]     # status= key, never tab=
        assert "tab" not in qs
        assert qs["flash"] == ["Removed 3 concepts"]
    finally:
        _stop_server(httpd, thread)
