"""Concurrency receipt for the pool-pruning slice's ThreadingHTTPServer swap.

The import below is the RED driver: scripts/factory_ui.py currently imports
HTTPServer (single-threaded), so importing ThreadingHTTPServer FROM IT fails
until the swap lands. The behavioral test then proves a slow request no
longer blocks a concurrent GET -- with a genuine overlap receipt (the fast
request completes WHILE the slow one is provably held open by an Event
gate), per the barrier+overlap discipline in
.claude/rules/single-actor-worker-tests.md.
"""
from __future__ import annotations

import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

from ptcg.factory import deck_quality, deckdb, ui_pages, ui_server

# RED until scripts/factory_ui.py:34 swaps its import (this is the pin that
# the PRODUCTION entrypoint -- not just this test -- serves threaded).
from scripts.factory_ui import ThreadingHTTPServer as ProductionServerClass


def test_production_server_class_is_threading_and_daemon():
    assert ProductionServerClass is ThreadingHTTPServer
    assert ThreadingHTTPServer.daemon_threads is True  # stdlib class default


def test_slow_request_does_not_block_concurrent_get(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    db.close()

    slow_started = threading.Event()   # slow request is genuinely in-flight
    release_slow = threading.Event()   # gate holding the slow request open
    first_call = threading.Lock()
    seen_first = []

    def conn_factory():
        # The lock must guard ONLY the check-and-set of seen_first, not the
        # wait itself -- holding it across release_slow.wait() would
        # serialize every subsequent conn_factory() call behind the first
        # one (defeating the concurrency receipt this test exists to make).
        with first_call:
            is_first = not seen_first
            if is_first:
                seen_first.append(1)
        if is_first:
            slow_started.set()
            release_slow.wait(timeout=10)  # hold ONLY the first request
        return deckdb.connect(db_path)

    handler = ui_server.make_app(conn_factory)
    httpd = ProductionServerClass(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    slow_result: dict = {}
    try:
        port = httpd.server_address[1]

        def _slow():
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/", timeout=30
            ) as r:
                slow_result["status"] = r.status

        slow_thread = threading.Thread(target=_slow, daemon=True)
        slow_thread.start()
        assert slow_started.wait(timeout=5)

        # Overlap receipt: this GET completes while the slow request is
        # still held open (release_slow not yet set). Under the old
        # single-threaded HTTPServer this urlopen times out instead.
        # /pool is DB-only (no repo-root filesystem reads, unlike /status),
        # so the test has no environmental dependency beyond tmp_path.
        start = time.monotonic()
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/pool", timeout=5
        ) as r:
            assert r.status == 200
        assert time.monotonic() - start < 4.0
        assert not release_slow.is_set()  # the overlap was real

        release_slow.set()
        slow_thread.join(timeout=10)
        assert slow_result.get("status") == 200  # slow request also finished
    finally:
        release_slow.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_cold_cache_engine_dll_lookup_is_thread_safe():
    """Whole-branch review Finding 1 (CRITICAL): `all_card_data()`/
    `all_attack()` share a native buffer that is NOT thread-safe. An
    unguarded 8-thread barrier probe against the raw `cg.api` calls
    reproduced the reviewer's exact failure signature (6/8 threads raising
    `json.JSONDecodeError`, the 2 survivors disagreeing -- (1267, 1556) vs
    a torn read) -- confirmed manually against this branch before the fix
    landed, not just asserted from the review prose.

    This test is the GREEN receipt for the fix: `deck_quality._DLL_LOCK`
    serializes every DLL-calling cache-builder (`_card_db`/`_attack_db`),
    and `ui_pages._card_id_to_name` now funnels through `_card_db()`
    instead of calling `all_card_data()` itself, so all three caches share
    one choke point. `ThreadingHTTPServer` (this module's own subject,
    tested above) is exactly the caller shape that exposed the race in
    production -- a real request thread per connection, all racing a cold
    cache on the first firing after process start.

    Barrier + overlap discipline per `.claude/rules/single-actor-worker-
    tests.md`: N threads are released simultaneously against CLEARED
    caches (a genuine cold-cache stampede, not N calls that happen to run
    back-to-back), and the assertion is on OUTCOME EQUALITY across all N
    threads (not a tautological return-value check) -- a torn/interleaved
    read would show up as differing dict lengths between threads, exactly
    as the raw-`cg.api` RED probe demonstrated.
    """
    N = 8
    deck_quality._card_db.cache_clear()
    deck_quality._attack_db.cache_clear()
    ui_pages._card_id_to_name.cache_clear()

    barrier = threading.Barrier(N)
    lock = threading.Lock()
    results: list[tuple[int, int, int]] = []
    errors: list[BaseException] = []
    entered: list[int] = []

    def _worker() -> None:
        try:
            barrier.wait(timeout=10)
            with lock:
                entered.append(1)
            cards = deck_quality._card_db()
            attacks = deck_quality._attack_db()
            names = ui_pages._card_id_to_name()
            with lock:
                results.append((len(cards), len(attacks), len(names)))
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not any(t.is_alive() for t in threads), "racer thread hung"
    assert not barrier.broken, "barrier broke -- the N calls never overlapped"
    assert len(entered) == N, f"expected {N} racers past the barrier, got {len(entered)}"
    assert not errors, f"cold-cache DLL lookup raised under concurrency: {errors!r}"
    assert len(results) == N
    assert len(set(results)) == 1, f"threads disagreed on card/attack counts: {set(results)}"


def test_run_server_warms_engine_caches_before_serving(tmp_path, monkeypatch):
    """Warm-up half of Finding 1: `run_server` must populate every
    engine-DLL-backed cache BEFORE `serve_forever` hands out connections to
    request threads, so the very first concurrent requests after process
    start never race a cold cache. Captures the real `ThreadingHTTPServer`
    instance (via a subclass swapped in for the module's own reference) so
    the test can assert warm-up happened, then shut the server down
    cleanly -- `run_server` itself only returns after `serve_forever` exits,
    so there is no other seam to inspect its setup path from outside."""
    import scripts.factory_ui as factory_ui_mod

    deck_quality._card_db.cache_clear()
    deck_quality._attack_db.cache_clear()
    ui_pages._card_id_to_name.cache_clear()

    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    db.close()

    captured: dict = {}
    real_cls = factory_ui_mod.ThreadingHTTPServer

    class _CapturingServer(real_cls):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            captured["httpd"] = self

    monkeypatch.setattr(factory_ui_mod, "ThreadingHTTPServer", _CapturingServer)

    result: dict = {}

    def _run():
        result["value"] = factory_ui_mod.run_server(
            db_path, port=0, lock_path=tmp_path / "ui.lock")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while "httpd" not in captured and time.monotonic() < deadline:
            time.sleep(0.05)
        assert "httpd" in captured, "server never started"

        # The warm-up ran synchronously inside run_server, BEFORE
        # serve_forever -- by the time the socket is bound, all three
        # caches must already be populated (currsize == 1), independent of
        # whether any request has actually been served yet.
        assert deck_quality._card_db.cache_info().currsize == 1
        assert deck_quality._attack_db.cache_info().currsize == 1
        assert ui_pages._card_id_to_name.cache_info().currsize == 1

        port = captured["httpd"].server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=5) as r:
            assert r.status == 200
    finally:
        captured["httpd"].shutdown()
        thread.join(timeout=5)
    assert result.get("value") == "stopped"
