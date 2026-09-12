"""Localhost UI server for the generational champion tournament (tournament
plan Task 18): Brad reviews the bottom-10 rated `active` decks and issues
Remove/Pass decisions.

Bound to `127.0.0.1` ONLY -- no authentication (spec Risks accepts this
because the process is unreachable off the local machine). `apply_decision`
writes the `decisions` row and the `remove` -> `culled` concept-status flip
inside ONE `deckdb._write` `BEGIN IMMEDIATE` transaction (Pattern
SQLITE-TXN), so a concurrent reader (e.g. the loop scheduler's own SELECT of
active decks) never observes a half-applied state -- see
`test_decision_write_atomic_under_concurrent_scheduler_read` in
`tests/test_factory_ui_server.py` for the interleaved-actor discrimination
receipt (Pattern INTERLEAVED-TEST, `.claude/rules/single-actor-worker-tests.md`).
The split unlocked-read -> act -> unconditional-UPDATE shape is a REJECTED
pattern in this repo (TOCTOU class, commit `178043b`).

T19 (UI status page) adds a second route (`GET /status`) to the SAME
`make_app`-produced handler class -- routing below is a flat `if self.path
== ...` dispatch so a new route is one more branch, no restructuring
required.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import sqlite3
import urllib.parse
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Callable

from ptcg.factory import deck_quality, deckdb, loop_state, ui_actions, ui_pages
from ptcg.factory.census import SCREENING_FLOOR
from ptcg.factory.cycle import FactoryPaths
from ptcg.factory.gate import HARD_DAILY_CAP, SubmissionCounter
from ptcg.factory.loop_state import _VALID_OFFSPRING_STATUSES
from ptcg.factory.ui_pages import (  # back-compat aliases (tests/import sweep)
    render_review_page as _render_page,
    render_status_page as _render_status_page,
)

#: src/ptcg/factory/ui_server.py -> parents[3] == repo root (same convention
#: as subscheduler.py/bundles.py/deck_matrix.py/evaluate.py/loop.py).
ROOT = Path(__file__).resolve().parents[3]

#: Bottom-10 review query: lowest-rated ACTIVE decks with a decisive
#: Bradley-Terry rating that have cleared `min_games`. `rating IS NOT NULL`
#: mirrors the decisive-rating gate already established by
#: `census._ACTIVE_SINGLES_QUERY`/`_PROMOTABLE_SINGLES_QUERY` -- a NULL
#: rating cannot be meaningfully "lowest". The join picks exactly the
#: canonical (lowest `shell_variant`) deck per concept, mirroring
#: `census._CANONICAL_DECK_JOIN_SQL`'s own rationale: coverage/rating is
#: keyed per-CONCEPT, so without this a concept with multiple shell
#: variants could crowd several near-duplicate rows into the bottom-10
#: instead of surfacing 10 distinct concepts. No task in this plan creates
#: additional shell variants yet, so this is a no-op today and a forward
#: safety net once one does.
_BOTTOM_TEN_QUERY = (
    "SELECT d.id AS deck_id, d.concept_id AS concept_id, d.cards AS cards, "
    "co.rating AS rating, co.games_played AS games_played "
    "FROM coverage co "
    "JOIN concepts c ON c.id = co.concept_id "
    "JOIN decks d ON d.concept_id = co.concept_id "
    "AND d.shell_variant = ("
    "SELECT MIN(d2.shell_variant) FROM decks d2 WHERE d2.concept_id = co.concept_id"
    ") "
    "WHERE c.status = 'active' AND co.rating IS NOT NULL AND co.games_played >= ? "
    "ORDER BY co.rating ASC, co.games_played ASC, d.id ASC "
    "LIMIT 10"
)


def bottom_ten(conn: sqlite3.Connection, min_games: int = SCREENING_FLOOR) -> list[dict]:
    """The 10 lowest-rated `active` decks meeting the `min_games` floor,
    each with its full card list (card IDs -- `_card_names` resolves display
    names). Read-only, no write-lock transaction needed."""
    rows = conn.execute(_BOTTOM_TEN_QUERY, (min_games,)).fetchall()
    return [
        {
            "deck_id": row["deck_id"],
            "concept_id": row["concept_id"],
            "cards": json.loads(row["cards"]),
            "rating": row["rating"],
            "games_played": row["games_played"],
        }
        for row in rows
    ]


def apply_decision(
    conn: sqlite3.Connection, deck_id: str, action: str, actor: str = "brad"
) -> None:
    """Atomically record a Remove/Pass decision (Pattern SQLITE-TXN): INSERT
    the `decisions` row and, on `remove`, flip the deck's concept to
    `status='culled'` -- both inside ONE `BEGIN IMMEDIATE` transaction, so a
    concurrent reader never observes a half-applied state (decision logged
    but status still `active`). `pass` logs the decision and leaves the
    concept `active`.

    IMPORTANT fix (2026-08-05): this legacy bottom-10 endpoint was the ONE
    remaining path that could cull the anchor -- `render_review_page`/T18
    predates the finalist guard `ui_actions._decide_one` gained for
    `/concept-decision`, and nothing here re-checked `status='finalist'`
    before writing. `raise ui_actions.FinalistProtectedError` BEFORE any
    write, mirroring `_decide_one`'s own guard placement exactly.
    """
    if action not in ("remove", "pass"):
        raise ValueError(f"apply_decision: action must be 'remove' or 'pass', got {action!r}")

    def _apply(c: sqlite3.Connection) -> None:
        row = c.execute("SELECT concept_id FROM decks WHERE id=?", (deck_id,)).fetchone()
        if row is None:
            raise ValueError(f"apply_decision: no deck with id={deck_id!r}")
        if action == "remove":
            concept = c.execute(
                "SELECT status FROM concepts WHERE id=?", (row["concept_id"],)
            ).fetchone()
            if concept is not None and concept["status"] == "finalist":
                raise ui_actions.FinalistProtectedError("anchor is protected")
        c.execute(
            "INSERT INTO decisions(deck_id, action, actor, timestamp) VALUES(?, ?, ?, ?)",
            (deck_id, action, actor, dt.datetime.now(dt.timezone.utc).isoformat()),
        )
        if action == "remove":
            c.execute("UPDATE concepts SET status='culled' WHERE id=?", (row["concept_id"],))

    deckdb._write(conn, _apply)


#: Live status page (tournament T19, PD-B override): the sole surface
#: replacing `experiments/factory/dashboard.html`. Every query below is a
#: SELECT -- `status_snapshot` sets `PRAGMA query_only=ON` on its connection
#: FIRST so a write attempted through it raises `sqlite3.OperationalError`
#: rather than silently succeeding (plan Step 1: "the handler never writes
#: to the DB").
_OFFSPRING_STATUS_QUERY = "SELECT status, COUNT(*) AS n FROM offspring GROUP BY status"

#: Matches `census.census_complete`'s population exactly (census.py:225-242):
#: single-core concepts WITH a `coverage` row. Only buildable concepts ever
#: get a coverage row (`seed_census` never inserts one for an unbuildable
#: concept); the anchor concept (`anchor.py`'s `ensure_anchor_deck`, cores
#: `'["anchor"]'`, deliberately no coverage row) is excluded the same way.
#: A bare `COUNT(*) FROM concepts` (no JOIN) previously counted both of
#: those no-coverage cases into the denominator while `_SINGLES_PLAYED_QUERY`
#: (below) could never count them -- structurally aligning the two queries'
#: populations, rather than special-casing the anchor id, keeps `/status`
#: reporting the same census the scheduler actually gates on.
_SINGLES_TOTAL_QUERY = (
    "SELECT COUNT(*) FROM coverage co JOIN concepts c ON c.id = co.concept_id "
    "WHERE json_array_length(c.cores) = 1"
)

_SINGLES_PLAYED_QUERY = (
    "SELECT COUNT(*) FROM coverage co JOIN concepts c ON c.id = co.concept_id "
    "WHERE json_array_length(c.cores) = 1 AND co.games_played >= ?"
)

_PAIRS_TOTAL_QUERY = "SELECT COUNT(*) FROM concepts WHERE json_array_length(cores) = 2"

#: A pair concept is "activated" once `census.activate_pair_concepts` flips
#: it to `status='active'` (and gives it a `decks` row) -- dormant pairs
#: (T8) stay `status='untested'` with no `decks` row, so `status='active'`
#: alone is the correct, index-friendly (`ix_concepts_status`) test, no join
#: needed.
_PAIRS_ACTIVATED_QUERY = (
    "SELECT COUNT(*) FROM concepts WHERE json_array_length(cores) = 2 AND status = 'active'"
)

_GAMES_RECENT_QUERY = "SELECT COUNT(*) FROM games WHERE status='done' AND timestamp >= ?"

#: Recent-throughput window (plan: "recent games throughput"). Not spelled
#: out numerically by the plan -- JUDGMENT CALL: 1 hour is short enough to
#: reflect the CURRENT rate (the matrix worker plays continuously) without
#: being noisy at the scale of a single 15-minute watch-loop firing.
THROUGHPUT_WINDOW = dt.timedelta(hours=1)


def _auth_state(watch_log_path: Path) -> dict:
    """Kaggle-auth state derived from TEXT ALREADY WRITTEN to `watch.log` by
    `subscheduler.maybe_submit`'s `AUTH-DEAD: ...` / terminal
    `submit-scheduler: AUTH=auth-dead` lines -- mirrors `dashboard.py`'s
    `auth_dead` digest-text-parsing convention (`assemble_state`'s digest
    scan, rendered by `render_status_strip`).

    Deliberately NEVER calls `kaggle_client.check_auth` live: Global
    Constraints scopes this page to "read-only queries against deckdb + the
    EXISTING state files" -- a live Kaggle API round-trip on every page load
    is a new external call, not a read of existing state, and would make
    the status page's latency/reliability depend on network/credentials
    that may not even be configured in dev.

    Forward line-scan (the log is append-only chronological, so a later
    line always overwrites an earlier one -- "most recent signal wins"):
    an `AUTH-DEAD:` line sets `state="dead"` and captures the detail text; a
    later `submit-scheduler:` terminal marker confirms `"dead"` (if it
    carries `AUTH=auth-dead`) or clears back to `"ok"` (any other outcome --
    a subsequent successful/no-op cycle means auth recovered). Returns
    `{"state": "unknown", ...}` when the log is missing or has no
    submit-scheduler activity yet -- expected before T20 wires the
    scheduler into the watch loop.
    """
    watch_log_path = Path(watch_log_path)
    if not watch_log_path.exists():
        return {"state": "unknown", "detail": None}
    state = "unknown"
    detail: str | None = None
    for line in watch_log_path.read_text(encoding="utf-8").splitlines():
        if "AUTH-DEAD:" in line:
            state = "dead"
            detail = line.split("AUTH-DEAD:", 1)[1].strip()
        elif "submit-scheduler:" in line:
            if "AUTH=auth-dead" in line:
                state = "dead"
            else:
                state = "ok"
                detail = None
    return {"state": state, "detail": detail}


def status_snapshot(conn: sqlite3.Connection, *, root: Path, now: dt.datetime) -> dict:
    """Read-only live status snapshot (tournament T19) -- everything the
    static `dashboard.html` covered, re-derived against the tournament's
    SQLite deck DB + its existing state files (Global Constraints: "status
    strip (paused / SUBMIT_HOLD / submission-counter / auth state), current
    baseline + version, offspring pipeline counts by status, census/coverage
    progress (singles played, pairs activated), recent games throughput").

    `PRAGMA query_only=ON` is set on `conn` FIRST, before any other
    statement -- so a write attempted through `conn` (by this function, or
    by a caller reusing the same connection afterward) raises
    `sqlite3.OperationalError` rather than silently succeeding. This makes
    the STRICT READ-ONLY contract a SQLite-enforced fact, not just a
    reviewed convention.

    `root` locates the existing state files via `cycle.FactoryPaths` -- the
    SAME path expressions the live watch loop already uses (`pause_file`,
    `submit_hold_file`, `counter`, `log_dir/watch.log`), reused rather than
    re-derived, so this page can never drift from where those files
    actually live. `now` is the injectable UTC-anchored time seam (this
    repo has shipped two UTC/local time-seam bugs already -- callers MUST
    pass a UTC-aware `datetime`, never call `dt.datetime.now()` here).
    """
    conn.execute("PRAGMA query_only=ON")
    paths = FactoryPaths(root=Path(root))

    baseline = loop_state.current_baseline(conn)

    offspring_counts = dict.fromkeys(_VALID_OFFSPRING_STATUSES, 0)
    for row in conn.execute(_OFFSPRING_STATUS_QUERY):
        offspring_counts[row["status"]] = row["n"]

    singles_total = conn.execute(_SINGLES_TOTAL_QUERY).fetchone()[0]
    singles_played = conn.execute(_SINGLES_PLAYED_QUERY, (SCREENING_FLOOR,)).fetchone()[0]
    pairs_total = conn.execute(_PAIRS_TOTAL_QUERY).fetchone()[0]
    pairs_activated = conn.execute(_PAIRS_ACTIVATED_QUERY).fetchone()[0]

    cutoff = (now - THROUGHPUT_WINDOW).isoformat()
    games_last_hour = conn.execute(_GAMES_RECENT_QUERY, (cutoff,)).fetchone()[0]

    counter = SubmissionCounter(paths.counter)
    today = now.date().isoformat()

    return {
        "paused": paths.pause_file.exists(),
        "submit_hold": paths.submit_hold_file.exists(),
        "submission_counter": {"used": counter.today_count(today), "cap": HARD_DAILY_CAP},
        "auth": _auth_state(paths.log_dir / "watch.log"),
        "baseline_version": baseline["version"] if baseline is not None else None,
        "offspring_counts": offspring_counts,
        "census": {
            "singles_total": singles_total,
            "singles_played": singles_played,
            "pairs_total": pairs_total,
            "pairs_activated": pairs_activated,
        },
        "games_last_hour": games_last_hour,
        "now": now.isoformat(),
    }


def make_app(
    conn_factory: Callable[[], sqlite3.Connection],
    *,
    root: Path = ROOT,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc),
) -> type[BaseHTTPRequestHandler]:
    """Build a `BaseHTTPRequestHandler` subclass bound to `conn_factory` via
    closure. Every request opens and closes its OWN connection (Pattern
    SQLITE-CONN) -- the handler never holds a connection across requests, so
    concurrent HTTP requests never race a single shared cursor.

    Binding to `127.0.0.1` ONLY is the CALLER's responsibility
    (`HTTPServer(("127.0.0.1", port), Handler)`) -- this factory returns the
    handler class only, no auth (spec Risks: mitigated entirely by the
    localhost-only bind).

    `root`/`now` (tournament T19) are optional keyword-only seams for the
    `GET /status` route's `status_snapshot` call -- default to the real repo
    root and a fresh UTC `datetime` per request, so existing callers
    (`scripts/factory_ui.py`, T18's own tests) that only pass `conn_factory`
    are unaffected; tests inject `tmp_path`/a fixed clock for determinism.
    """

    class Handler(BaseHTTPRequestHandler):
        server_version = "PTCGTournamentUI/1.0"

        def log_message(self, format: str, *args: object) -> None:
            pass  # quiet by default; stdlib default writes every request to stderr

        def _send_html(self, status: int, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler method name
            # urlsplit isolates the path from any query string so `/search?...`
            # matches on `/search`, not the raw `self.path` (which includes
            # the query) -- see task-6 binding conventions.
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/":
                qs = urllib.parse.parse_qs(parsed.query)
                flash = qs.get("flash", [""])[0]
                conn = conn_factory()
                try:
                    rows = bottom_ten(conn)
                finally:
                    conn.close()
                self._send_html(200, _render_page(rows, flash=flash))
            elif parsed.path == "/status":
                conn = conn_factory()
                try:
                    snap = status_snapshot(conn, root=root, now=now())
                finally:
                    conn.close()
                self._send_html(200, _render_status_page(snap))
            elif parsed.path == "/pool":
                qs = urllib.parse.parse_qs(parsed.query)
                problems_only = qs.get("problems", ["0"])[0] == "1"
                flash = qs.get("flash", [""])[0]
                conn = conn_factory()
                try:
                    rows = ui_actions.pool_decks(conn)
                    anchor = ui_actions.anchor_concept(conn)
                    champ = ui_actions.champion_concept_id(conn)
                finally:
                    conn.close()
                if problems_only:
                    # Post-fetch, zero lock impact. None (analysis failed)
                    # counts as a problem: it cannot be proven clean.
                    rows = [
                        r for r in rows
                        if (rep := deck_quality.safe_analyze(
                            json.loads(r["cards"]))) is None or rep.flags
                    ]
                page = ui_pages.render_pool_page(
                    rows, anchor, champ, problems_only=problems_only, flash=flash)
                self._send_html(200, page)
            elif parsed.path == "/search":
                qs = urllib.parse.parse_qs(parsed.query)
                q = qs.get("q", [""])[0]
                tab = qs.get("status", ["untested"])[0]
                flash = qs.get("flash", [""])[0]
                conn = conn_factory()
                try:
                    rows, total = ui_actions.search_concepts(conn, q, tab)
                    decks = ui_actions.canonical_decks(
                        conn, [r["concept_id"] for r in rows])
                except ValueError as exc:
                    self._send_html(
                        400, f"<h1>400 Bad Request</h1><p>{html.escape(str(exc))}</p>"
                    )
                    return
                finally:
                    conn.close()
                self._send_html(
                    200,
                    ui_pages.render_search_page(rows, total, q, tab, decks=decks, flash=flash))
            else:
                self._send_html(404, "<h1>404 Not Found</h1>")

        def _read_form(self) -> dict[str, list[str]]:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            return urllib.parse.parse_qs(raw.decode("utf-8"))

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler method name
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/decision":
                params = self._read_form()
                deck_id = params.get("deck_id", [None])[0]
                action = params.get("action", [None])[0]
                if not deck_id or action not in ("remove", "pass"):
                    self._send_html(400, "<h1>400 Bad Request</h1>")
                    return
                conn = conn_factory()
                try:
                    apply_decision(conn, deck_id, action)
                except ui_actions.FinalistProtectedError as exc:
                    # Must be caught BEFORE ValueError -- it subclasses it.
                    self._send_html(
                        409, f"<h1>409 Conflict</h1><p>{html.escape(str(exc))}</p>"
                    )
                    return
                except ValueError as exc:
                    self._send_html(
                        400, f"<h1>400 Bad Request</h1><p>{html.escape(str(exc))}</p>"
                    )
                    return
                finally:
                    conn.close()
                verb = "Removed" if action == "remove" else "Passed"
                self.send_response(303)
                self.send_header(
                    "Location",
                    "/?" + urllib.parse.urlencode({"flash": f"{verb} {deck_id}"}))
                self.end_headers()
            elif parsed.path == "/concept-decision":
                params = self._read_form()
                concept_id = params.get("concept_id", [None])[0]
                action = params.get("action", [None])[0]
                origin = params.get("from", ["pool"])[0]
                q = params.get("q", [""])[0]
                status = params.get("status", ["untested"])[0]
                problems_only = params.get("problems", ["0"])[0] == "1"
                if not concept_id or action not in ("remove", "restore"):
                    self._send_html(400, "<h1>400 Bad Request</h1>")
                    return
                conn = conn_factory()
                try:
                    # No pre-check of the concept's status here -- the
                    # read->decide->write happens entirely inside
                    # ui_actions.apply_concept_decision's own BEGIN IMMEDIATE
                    # transaction (TOCTOU class rejected repo-wide, commit
                    # 178043b; see .claude/rules/single-actor-worker-tests.md).
                    ui_actions.apply_concept_decision(conn, concept_id, action)
                except ui_actions.FinalistProtectedError as exc:
                    # Must be caught BEFORE ValueError -- it subclasses it.
                    self._send_html(
                        409, f"<h1>409 Conflict</h1><p>{html.escape(str(exc))}</p>"
                    )
                    return
                except ui_actions.UnknownConceptError as exc:
                    # MINOR-7 fix (2026-08-05): also a ValueError subclass --
                    # must be caught BEFORE the plain ValueError branch below.
                    # Spec's Error handling section: "Unknown concept id ->
                    # 404 message" (was previously falling into the generic
                    # 400 branch, indistinguishable from any other bad input).
                    self._send_html(
                        404, f"<h1>404 Not Found</h1><p>{html.escape(str(exc))}</p>"
                    )
                    return
                except ValueError as exc:
                    self._send_html(
                        400, f"<h1>400 Bad Request</h1><p>{html.escape(str(exc))}</p>"
                    )
                    return
                finally:
                    conn.close()
                verb = "Removed" if action == "remove" else "Restored"
                flash_msg = f"{verb} {concept_id}"
                if origin == "search":
                    location = "/search?" + urllib.parse.urlencode(
                        {"q": q, "status": status, "flash": flash_msg})
                else:
                    # Carry the `problems=1` filter back through the PRG
                    # redirect so a cull from the filtered pool view (MINOR
                    # finding, whole-branch review) doesn't silently drop
                    # the reviewer back to the unfiltered pool. The key is
                    # only added when the filter was actually active --
                    # `render_pool_page` only emits the hidden input when
                    # `problems_only=True`, so an unfiltered submission
                    # never sends `problems` at all.
                    pool_params = {"flash": flash_msg}
                    if problems_only:
                        pool_params["problems"] = "1"
                    location = "/pool?" + urllib.parse.urlencode(pool_params)
                self.send_response(303)
                self.send_header("Location", location)
                self.end_headers()
            elif parsed.path == "/bulk-confirm":
                params = self._read_form()
                q = params.get("q", [""])[0]
                tab = params.get("tab", [""])[0]
                action = params.get("action", [""])[0]
                required_tab = ui_actions._BULK_TAB_FOR_ACTION.get(action)
                if required_tab is None or tab != required_tab or not q.strip():
                    self._send_html(400, "<h1>400 Bad Request</h1>")
                    return
                conn = conn_factory()
                try:
                    _, total = ui_actions.search_concepts(conn, q, tab)
                finally:
                    conn.close()
                self._send_html(200, ui_pages.render_bulk_confirm_page(q, tab, action, total))
            elif parsed.path == "/bulk-decision":
                params = self._read_form()
                q = params.get("q", [""])[0]
                tab = params.get("tab", [""])[0]
                action = params.get("action", [""])[0]
                conn = conn_factory()
                try:
                    # apply_bulk_decision recomputes the match list INSIDE
                    # its own transaction -- the /bulk-confirm preview count
                    # is display-only and never trusted here.
                    n = ui_actions.apply_bulk_decision(conn, q, tab, action)
                except ValueError as exc:
                    self._send_html(
                        400, f"<h1>400 Bad Request</h1><p>{html.escape(str(exc))}</p>"
                    )
                    return
                finally:
                    conn.close()
                verb = "Removed" if action == "bulk-remove" else "Restored"
                noun = "concept" if n == 1 else "concepts"
                location = "/search?" + urllib.parse.urlencode(
                    {"q": q, "status": tab, "flash": f"{verb} {n} {noun}"})
                self.send_response(303)
                self.send_header("Location", location)
                self.end_headers()
            else:
                self._send_html(404, "<h1>404 Not Found</h1>")

    return Handler
