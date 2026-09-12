"""Early anchor floor gate (anchor-pressure design 2).

A candidate offspring must reach FLOOR_BAR win rate over FLOOR_GAMES games
against the anchor deck BEFORE it may enter CONFIRM (and therefore playoffs/
CROWN). Floor games are heuristic-vs-heuristic -- `agent_version_a` is the
sentinel `floor:<offspring_id>` and `agent_version_b` is the anchor version,
BOTH resolved to `HeuristicAgent` by `runner_pool._resolve_agent_entry` --
so the 50-game check costs heuristic-game time, not search-game time, and
measures the DECK (D3: deck quality is the primary failure mode).

Draws (`winner == 2`) count as candidate losses (anchor.py convention).
Boundary: 23/50 = 0.46 passes (the bar is `>=`, and n=50 hits it exactly),
22/50 = 0.44 fails.

A floor FAIL blames the DECK, not the agent (D3), so a failing attempt does
not terminally trash the offspring: the offspring RE-PICKS its next-best MATCH
deck (same ranking `loop.select_optimal_deck` uses) and re-runs the floor at
`FLOOR_BAR`/`FLOOR_GAMES` again, up to `FLOOR_MAX_ATTEMPTS` decks total (2
re-picks). Only when every attempt fails -- or the MATCH field has no untried
deck left -- is the offspring trashed. Each attempt owns its own sentinel
version (`floor_version(oid, attempt)`), so one attempt's games never pollute
another's aggregate, and `floor_checks.deck_id`/`.attempt`/`.tried_decks`
record which deck each attempt checked (auditable + idempotent on resume).

Every read-decide-act sequence here is ONE `deckdb._write` (`BEGIN
IMMEDIATE`) transaction (Pattern SQLITE-TXN,
`.claude/rules/single-actor-worker-tests.md`) -- including the re-pick, whose
verdict write, deck re-pick, offspring `deck_id` update and next series'
INSERTs all land atomically with the failing verdict itself.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

from ptcg.factory import anchor, deckdb

#: SINGLE SOURCE OF TRUTH for the floor gate -- grep-verified (Pass 2) to have
#: no literal duplicates anywhere in production code, so the bar can be
#: re-set by editing these two lines and nothing else. (The `0.45` in
#: `evaluate.py` and `deck_matrix.py` are unrelated constants in other
#: subsystems, NOT copies of this bar.) Boundary examples in the docstrings
#: below are illustrative of these values -- refresh that prose if they change.
#:
#: FLOOR_BAR recalibrated 2026-08-13 (census/screening-regime slice, spec
#: 2026-08-13 Sec5, T6 Brad gate): justified by the binomial null, not the
#: pool measurement -- a true-0.50 offspring (Binomial(50, 0.5)) fails
#: P=0.2399 of 50-game checks at this bar (W_f=23), softened by the
#: 3-attempt re-pick. -- every measured deck (max per-deck WR 0.22, n=20)
#: fails at both 0.40 and 0.46, so the pool measurement is not what set
#: this value.
#: Brad chose 0.45 as the target; 0.45 is equivalent to 0.46 at n=50 under
#: the exact-multiple-of-1/FLOOR_GAMES guarantee (both round to W_f=23/50),
#: so 0.46 is recorded here. Source receipt:
#: experiments/screening-regime-measurement-2026-08-14.md (both runs).
#: (Previous: 0.40, calibrated 2026-08-04 against the pre-anchor-swap,
#: pre-repair pool -- invalidated by the two regime shifts since.)
FLOOR_GAMES = 50
FLOOR_BAR = 0.46
FLOOR_PRIORITY = 0.8  # below anchor's 1.0, above match/confirm/crown's 0.0
FLOOR_VERSION_PREFIX = "floor:"
#: Distinct decks an offspring may float through the floor before it is
#: trashed (1 initial pick + 2 re-picks). Each attempt is a full
#: `FLOOR_GAMES`-game series at `FLOOR_BAR`.
FLOOR_MAX_ATTEMPTS = 3

_FLOOR_CHECKS_DDL = (
    "CREATE TABLE IF NOT EXISTS floor_checks("
    "offspring_id TEXT PRIMARY KEY, deck_id TEXT NOT NULL, "
    "games_planned INTEGER NOT NULL, games_done INTEGER NOT NULL DEFAULT 0, "
    "wins INTEGER NOT NULL DEFAULT 0, wr REAL, "
    "verdict TEXT NOT NULL DEFAULT 'pending' "
    "CHECK(verdict IN ('pending','pass','fail')), "
    "created_at TEXT NOT NULL, resolved_at TEXT, "
    "attempt INTEGER NOT NULL DEFAULT 0, "
    "tried_decks TEXT NOT NULL DEFAULT '[]', "
    "anomaly TEXT)"
)

#: Additive column upgrade for a floor_checks table created before the
#: re-pick design landed (a live DB that already ran the pre-re-pick code).
#: `ALTER TABLE ... ADD COLUMN` is the only additive shape SQLite offers --
#: `CREATE TABLE IF NOT EXISTS` silently no-ops on an existing table.
_FLOOR_ADDITIVE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("attempt", "INTEGER NOT NULL DEFAULT 0"),
    ("tried_decks", "TEXT NOT NULL DEFAULT '[]'"),
    # Set only when a terminal 'fail' could NOT trash the offspring (its
    # status moved out from under the guarded UPDATE). NULL on every healthy
    # row -- see `resolve_floor`'s fail branch and `floor_anomaly`.
    ("anomaly", "TEXT"),
)

_FLOOR_RESULTS_QUERY = (
    "SELECT SUM(CASE WHEN winner = 0 THEN 1 ELSE 0 END) AS wins, "
    "COUNT(*) AS n FROM games "
    "WHERE purpose='floor' AND status='done' AND agent_version_a = ?"
)

#: Deck ranking for a re-pick: MIRRORS `loop._OFFSPRING_MATCH_RESULTS_QUERY`
#: verbatim (same purpose/status/side convention). Inlined rather than
#: imported because `loop.py` imports THIS module -- importing it back would
#: be circular. `test_repick_ranking_matches_select_optimal_deck` pins the two
#: together so the copy cannot drift silently.
_MATCH_RESULTS_QUERY = (
    "SELECT deck_a_id AS deck_id, "
    "SUM(CASE WHEN winner = 0 THEN 1 ELSE 0 END) AS wins, "
    "COUNT(*) AS n "
    "FROM games "
    "WHERE purpose = 'match' AND status = 'done' AND agent_version_a = ? "
    "GROUP BY deck_a_id"
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Additive upgrade for a live pre-slice DB (mirrors anchor._ensure_schema).
    Idempotent; virgin DBs get the table (with every column) from init_db."""
    conn.execute(_FLOOR_CHECKS_DDL)
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(floor_checks)")}
    for name, decl in _FLOOR_ADDITIVE_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE floor_checks ADD COLUMN {name} {decl}")


def floor_version(offspring_id: str, attempt: int = 0) -> str:
    """The sentinel agent-version string floor games carry on the candidate
    side, scoped to ONE floor attempt. Resolved to HeuristicAgent by
    runner_pool (prefix match, so the `#<attempt>` suffix rides along); also
    the key the results aggregate filters on, which is why each attempt must
    have its own string -- otherwise a re-pick's games would be pooled with
    the failed attempt's games.

    Attempt 0 is UNSUFFIXED, so rows/games written before the re-pick design
    (and every existing caller that omits `attempt`) keep their exact
    identity."""
    if attempt <= 0:
        return f"{FLOOR_VERSION_PREFIX}{offspring_id}"
    return f"{FLOOR_VERSION_PREFIX}{offspring_id}#{attempt}"


def _tried_decks(row: sqlite3.Row) -> list[str]:
    """The decks this offspring has already floored (current attempt LAST).
    Falls back to the row's own deck for a legacy row whose `tried_decks`
    column was backfilled with the `'[]'` default."""
    try:
        tried = json.loads(row["tried_decks"] or "[]")
    except (TypeError, ValueError):
        tried = []
    if not tried:
        tried = [row["deck_id"]]
    return [str(d) for d in tried]


def _next_match_deck(
    c: sqlite3.Connection, offspring_id: str, tried: list[str]
) -> str | None:
    """The best-ranked MATCH deck this offspring has not floored yet, or None
    when the field is exhausted. Ranking is `loop.select_optimal_deck`'s:
    win share (`wins / n`, draws in the denominator only) descending, deck id
    ascending as the tie-break."""
    rows = c.execute(_MATCH_RESULTS_QUERY, (offspring_id,)).fetchall()
    ranked = sorted(
        rows,
        key=lambda r: (-(r["wins"] / r["n"] if r["n"] else 0.0), r["deck_id"]),
    )
    skip = set(tried)
    for row in ranked:
        if row["deck_id"] not in skip:
            return str(row["deck_id"])
    return None


def enqueue_floor_series(
    conn: sqlite3.Connection, offspring_id: str, n_games: int = FLOOR_GAMES
) -> int:
    """Ensure a floor_checks row + a full n_games series exists for this
    offspring's CURRENT attempt; returns games enqueued this call. Resumable
    top-up (counts the shortfall, mirrors anchor.enqueue_anchor_series). No-ops
    unless the offspring is at status 'matching' with a MATCH-picked deck_id;
    raises on a missing offspring or missing deck_id (caller-ordering bug).

    Attempt-aware: once a row exists, the series is keyed on the ROW's
    `deck_id` and `attempt` (the deck currently under test), not on the
    offspring row -- so a re-pick started by `resolve_floor` (which already
    enqueued its own full series inside the failing verdict's transaction)
    tops up 0 here rather than double-enqueuing or re-enqueuing against a
    stale deck."""
    _ensure_schema(conn)

    def _apply(c: sqlite3.Connection) -> int:
        off = c.execute(
            "SELECT status, deck_id FROM offspring WHERE id=?", (offspring_id,)
        ).fetchone()
        if off is None:
            raise ValueError(
                f"enqueue_floor_series: no offspring row with id={offspring_id!r}"
            )
        if off["status"] != "matching":
            return 0
        if off["deck_id"] is None:
            raise RuntimeError(
                f"enqueue_floor_series: offspring {offspring_id!r} has no deck_id "
                "set -- run select_optimal_deck first"
            )
        row = c.execute(
            "SELECT deck_id, verdict, attempt, games_planned FROM floor_checks "
            "WHERE offspring_id=?",
            (offspring_id,),
        ).fetchone()
        if row is not None and row["verdict"] != "pending":
            return 0
        if row is None:
            attempt = 0
            deck_id = off["deck_id"]
            planned = n_games
            c.execute(
                "INSERT INTO floor_checks(offspring_id, deck_id, games_planned, "
                "created_at, attempt, tried_decks) VALUES(?,?,?,?,0,?)",
                (offspring_id, deck_id, planned, _now(), json.dumps([deck_id])),
            )
        else:
            attempt = row["attempt"]
            deck_id = row["deck_id"]
            planned = row["games_planned"]
        return _enqueue_attempt_games(c, offspring_id, attempt, deck_id, planned)

    return deckdb._write(conn, _apply)


def _enqueue_attempt_games(
    c: sqlite3.Connection, offspring_id: str, attempt: int, deck_id: str, n_games: int
) -> int:
    """Top an attempt's series up to `n_games` and return how many rows were
    inserted. Caller supplies the open `BEGIN IMMEDIATE` connection -- this is
    never its own transaction (`deckdb._write` cannot nest)."""
    version = floor_version(offspring_id, attempt)
    existing = c.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='floor' AND agent_version_a=?",
        (version,),
    ).fetchone()[0]
    remaining = max(0, n_games - existing)
    for _ in range(remaining):
        c.execute(
            "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, "
            "agent_version_b, purpose, priority, status) "
            "VALUES (?, ?, ?, ?, 'floor', ?, 'pending')",
            (deck_id, anchor.ANCHOR_DECK_ID, version,
             anchor.ANCHOR_VERSION, FLOOR_PRIORITY),
        )
    return remaining


def resolve_floor(conn: sqlite3.Connection, offspring_id: str) -> str:
    """Resolve the current attempt's floor verdict once its series is fully
    done. Returns 'absent' (no check row), 'pending' (series incomplete),
    'pass', 'repick' (this deck failed but an untried MATCH deck remains and a
    fresh attempt was started), or 'fail' (final -- the offspring is trashed).

    Everything a verdict implies happens in the SAME transaction as the
    verdict write (the read-decide-act atomicity the spec mandates):

    * 'pass'   -> the offspring's `deck_id` is pinned to the deck that PASSED,
                  so CONFIRM/CROWN can only ever run the floored deck.
    * 'repick' -> the check row is reset onto the next-best untried MATCH deck
                  (attempt+1, verdict back to 'pending'), the offspring's
                  `deck_id` follows it, and that attempt's full series is
                  enqueued -- so the offspring always has an advancing step
                  and can never wedge at 'matching' with nothing pending.
    * 'fail'   -> attempts (or untried decks) exhausted; offspring trashed.
                  If the guarded trash UPDATE matches no row (the offspring's
                  status moved), the verdict is still 'fail' but the
                  discrepancy is recorded in `floor_checks.anomaly` rather
                  than swallowed -- read it with `floor_anomaly`.

    A settled verdict is never recomputed."""
    _ensure_schema(conn)

    def _apply(c: sqlite3.Connection) -> str:
        row = c.execute(
            "SELECT deck_id, games_planned, verdict, attempt, tried_decks "
            "FROM floor_checks WHERE offspring_id=?",
            (offspring_id,),
        ).fetchone()
        if row is None:
            return "absent"
        if row["verdict"] != "pending":
            return row["verdict"]
        attempt = row["attempt"]
        agg = c.execute(
            _FLOOR_RESULTS_QUERY, (floor_version(offspring_id, attempt),)
        ).fetchone()
        n = agg["n"] or 0
        if n < row["games_planned"]:
            return "pending"
        wins = agg["wins"] or 0
        wr = wins / n
        verdict = "pass" if wr >= FLOOR_BAR else "fail"
        cur = c.execute(
            "UPDATE floor_checks SET games_done=?, wins=?, wr=?, verdict=?, "
            "resolved_at=? WHERE offspring_id=? AND verdict='pending'",
            (n, wins, wr, verdict, _now(), offspring_id),
        )
        if cur.rowcount != 1:
            # Lost the write race to a concurrent resolver; report ITS verdict.
            settled = c.execute(
                "SELECT verdict FROM floor_checks WHERE offspring_id=?", (offspring_id,)
            ).fetchone()
            return settled["verdict"]
        if verdict == "pass":
            # Pin the passing deck (belt-and-suspenders: a re-pick already
            # keeps offspring.deck_id in step, but CONFIRM must never run a
            # deck other than the one that cleared the floor).
            c.execute(
                "UPDATE offspring SET deck_id=? WHERE id=?", (row["deck_id"], offspring_id)
            )
            return "pass"

        # FAIL: the DECK is the suspect, not the agent -- re-pick if we can.
        tried = _tried_decks(row)
        next_deck = (
            _next_match_deck(c, offspring_id, tried)
            if attempt + 1 < FLOOR_MAX_ATTEMPTS
            else None
        )
        if next_deck is not None:
            c.execute(
                "UPDATE floor_checks SET deck_id=?, attempt=?, tried_decks=?, "
                "games_planned=?, games_done=0, wins=0, wr=NULL, "
                "verdict='pending', created_at=?, resolved_at=NULL "
                "WHERE offspring_id=?",
                (next_deck, attempt + 1, json.dumps(tried + [next_deck]),
                 row["games_planned"], _now(), offspring_id),
            )
            c.execute(
                "UPDATE offspring SET deck_id=? WHERE id=?", (next_deck, offspring_id)
            )
            _enqueue_attempt_games(
                c, offspring_id, attempt + 1, next_deck, row["games_planned"]
            )
            return "repick"

        cur = c.execute(
            "UPDATE offspring SET status='trashed' WHERE id=? AND status='matching'",
            (offspring_id,),
        )
        if cur.rowcount == 0:
            # The guard did not match: the offspring's status moved out from
            # under us between this transaction's floor read and here (a
            # concurrent writer, or a caller-ordering bug). The verdict IS
            # 'fail' but the offspring was NOT trashed -- previously a silent
            # no-op, which is exactly the state that lets a floor-failed
            # candidate keep advancing. Record it on the check row so the
            # anomaly is observable in the ledger instead of invisible.
            observed = c.execute(
                "SELECT status FROM offspring WHERE id=?", (offspring_id,)
            ).fetchone()
            status = observed["status"] if observed is not None else "<missing>"
            c.execute(
                "UPDATE floor_checks SET anomaly=? WHERE offspring_id=?",
                (
                    f"fail-not-trashed: expected status 'matching', found "
                    f"{status!r} at {_now()}",
                    offspring_id,
                ),
            )
        return "fail"

    return deckdb._write(conn, _apply)


def floor_anomaly(conn: sqlite3.Connection, offspring_id: str) -> str | None:
    """The recorded anomaly note for this offspring's floor check, or None
    when the row is healthy (or absent). Currently set only by the fail
    branch's un-trashable-offspring case; a non-None value here means a
    floor-FAILED candidate was left un-trashed and needs a look."""
    _ensure_schema(conn)
    row = conn.execute(
        "SELECT anomaly FROM floor_checks WHERE offspring_id=?", (offspring_id,)
    ).fetchone()
    return row["anomaly"] if row is not None else None


def floor_status(
    conn: sqlite3.Connection, offspring_id: str
) -> tuple[str, int, int, float | None]:
    """Read-only gate evidence for the CURRENT attempt: (verdict, done,
    planned, wr). 'absent' when no row exists. All shapes first-class
    (provenance-shaped-optional-fields)."""
    _ensure_schema(conn)
    row = conn.execute(
        "SELECT games_planned, games_done, wr, verdict, attempt FROM floor_checks "
        "WHERE offspring_id=?",
        (offspring_id,),
    ).fetchone()
    if row is None:
        return ("absent", 0, FLOOR_GAMES, None)
    if row["verdict"] != "pending":
        return (row["verdict"], row["games_done"], row["games_planned"], row["wr"])
    done = conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='floor' AND status='done' "
        "AND agent_version_a=?",
        (floor_version(offspring_id, row["attempt"]),),
    ).fetchone()[0]
    return ("pending", done, row["games_planned"], None)
