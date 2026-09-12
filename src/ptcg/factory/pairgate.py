"""Counted-pair protection: pair-gate head-to-head vs the to-be-evicted
counted Kaggle submission (counted-pair-protection spec 2026-08-05,
designs 1-3).

Kaggle counts only the TWO MOST RECENT submissions and evicts by RECENCY
(`.claude/rules/platform-mechanics-model.md`): a new upload evicts the
OLDER of the counted pair regardless of score. This module makes every
automated upload prove it beats the submission it would evict
(wr >= PAIR_GATE_BAR over PAIR_GATE_GAMES head-to-head games, played by
the runner workers as purpose='pair_gate' rows), with a FAIL-CLOSED
reconstruction rule and an upload-time TOCTOU re-verify (invariants
I1/I4).

Evictee identity comes from the live Kaggle submissions list (the 2 most
recent non-ERROR rows; older = evictee) parsed back through
`submit.submission_description`'s own format, then reconstructed against
the tournament DB. Agent versions are validated by MIRRORING
`runner_pool._resolve_agent_entry`'s DB chain (offspring row -> baselines
row -> founding meta) rather than importing runner_pool -- runner_pool
imports `ptcg.arena.runner` (the native engine), which the watch loop
must never load; `test_pair_gate_versions_resolve_via_runner_pool` pins
the mirror against the real resolver.

The champion is ALWAYS `agent_version_a` (anchor.py fixed-side
convention); draws (`winner == 2`) count as champion losses. Boundary:
110/200 = 0.550 -> pass (bar is `>=`), 109/200 = 0.545 -> fail. Every
read-decide-act sequence is ONE `deckdb._write` (`BEGIN IMMEDIATE`)
transaction (`.claude/rules/single-actor-worker-tests.md`).
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass

from ptcg.factory import deckdb

PAIR_GATE_GAMES = 200
PAIR_GATE_BAR = 0.55
#: Between floor's 0.8 and anchor's 1.0: a pending pair-gate verdict blocks
#: uploads (like anchor) so it outranks breeding series, but the anchor
#: series (which gates promotion itself) stays first in the claim queue.
PAIR_GATE_PRIORITY = 0.9

_PAIR_GATE_CHECKS_DDL = (
    "CREATE TABLE IF NOT EXISTS pair_gate_checks("
    "version TEXT PRIMARY KEY, "
    "opp_version TEXT NOT NULL, opp_deck_id TEXT NOT NULL, "
    "opp_submitted_at TEXT NOT NULL, "
    "games_planned INTEGER NOT NULL, games_done INTEGER NOT NULL DEFAULT 0, "
    "wins INTEGER NOT NULL DEFAULT 0, wr REAL, "
    "verdict TEXT NOT NULL DEFAULT 'pending' "
    "CHECK(verdict IN ('pending','pass','fail')), "
    "created_at TEXT NOT NULL, resolved_at TEXT)"
)


class EvicteeUnreconstructable(RuntimeError):
    """The to-be-evicted counted submission cannot be reconstructed locally
    (legacy/rescue bundle, malformed description, version or deck missing
    from the tournament DB). FAIL-CLOSED: the caller must block the upload
    and log loudly -- never upload ungated (spec design 2)."""


@dataclass(frozen=True)
class EvicteeRef:
    """The to-be-evicted counted submission, reconstructed locally.
    `submitted_at` is the Kaggle row date -- it identifies WHICH submission
    the verdict is about (I4), while (`version`, `deck_id`) identify the
    CONFIG the head-to-head evidence is against."""
    name: str
    version: str
    deck_id: str
    submitted_at: str


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Additive upgrade for a live pre-slice DB (mirrors
    anchor._ensure_schema). Idempotent; virgin DBs get the table from
    init_db."""
    conn.execute(_PAIR_GATE_CHECKS_DDL)


def parse_description(description: str) -> tuple[str, str, str] | None:
    """(name, version, deck_stem) from a `submission_description`-format
    string (`submit.py:80-84`), or None when the shape doesn't match.
    Pipeline names never contain spaces (tournament-champion /
    tournament-probe-<concept>); version is the last token of segment 0."""
    segments = [s.strip() for s in description.split(" - ")]
    if len(segments) < 2 or not segments[1].startswith("deck "):
        return None
    head = segments[0].rsplit(" ", 1)
    if len(head) != 2 or not head[0] or not head[1]:
        return None
    name, version = head
    deck_stem = segments[1][len("deck "):].strip()
    if not deck_stem:
        return None
    return (name, version, deck_stem)


def _version_resolvable(conn: sqlite3.Connection, version: str) -> bool:
    """Mirrors `runner_pool._resolve_agent_entry`'s DB fallback chain
    (`runner_pool.py:230-266`): offspring row -> baselines row [crowned ->
    winning offspring row must exist; founding -> meta
    'founding_agent_config' must exist]. NOT imported from runner_pool (it
    loads the game engine); pinned against the real resolver by
    test_pair_gate_versions_resolve_via_runner_pool."""
    if conn.execute("SELECT 1 FROM offspring WHERE id=?", (version,)).fetchone():
        return True
    base = conn.execute(
        "SELECT offspring_id FROM baselines WHERE version=?", (version,)
    ).fetchone()
    if base is None:
        return False
    if base["offspring_id"] is None:
        return conn.execute(
            "SELECT 1 FROM meta WHERE key='founding_agent_config'"
        ).fetchone() is not None
    return conn.execute(
        "SELECT 1 FROM offspring WHERE id=?", (base["offspring_id"],)
    ).fetchone() is not None


def resolve_evictee(conn: sqlite3.Connection, rows: list) -> EvicteeRef | None:
    """The submission the next upload would evict: the OLDER of the two
    most recent non-ERROR Kaggle rows (recency eviction). Returns None
    when fewer than 2 counted rows exist (nothing to protect). Raises
    EvicteeUnreconstructable when the evictee cannot be rebuilt locally.

    ERROR-status rows are excluded (they never become counted leaderboard
    submissions). Kaggle dates are fixed-width "YYYY-MM-DD HH:MM:SS", so
    lexicographic order IS chronological (harvest._authoritative_row
    precedent, harvest.py:44-47).
    """
    counted = sorted(
        (r for r in rows if (r.status or "").upper() != "ERROR"),
        key=lambda r: r.date, reverse=True)[:2]
    if len(counted) < 2:
        return None
    evictee_row = counted[1]  # the OLDER of the counted pair
    parsed = parse_description(evictee_row.description)
    if parsed is None:
        raise EvicteeUnreconstructable(
            f"unparseable description {evictee_row.description!r}")
    name, version, deck_stem = parsed
    if name == "tournament-champion":
        base = conn.execute(
            "SELECT deck_id FROM baselines WHERE version=?", (version,)
        ).fetchone()
        if base is None:
            raise EvicteeUnreconstructable(
                f"champion version {version!r} has no baselines row")
        deck_id = base["deck_id"]
    else:
        deck_id = deck_stem
    if not _version_resolvable(conn, version):
        raise EvicteeUnreconstructable(
            f"agent version {version!r} not reconstructable from the "
            "tournament DB (legacy/rescue bundle or missing genome)")
    if not conn.execute("SELECT 1 FROM decks WHERE id=?", (deck_id,)).fetchone():
        raise EvicteeUnreconstructable(
            f"deck {deck_id!r} not found in the tournament DB")
    return EvicteeRef(name=name, version=version, deck_id=deck_id,
                      submitted_at=evictee_row.date)


_PAIR_GATE_RESULTS_QUERY = (
    "SELECT SUM(CASE WHEN winner = 0 THEN 1 ELSE 0 END) AS wins, "
    "COUNT(*) AS n FROM games "
    "WHERE purpose='pair_gate' AND status='done' "
    "AND agent_version_a = ? AND agent_version_b = ? AND deck_b_id = ?"
)


def _enqueue_series(c: sqlite3.Connection, version: str, champ_deck: str,
                    opp_version: str, opp_deck: str, planned: int) -> int:
    """Top the (version vs opp_version-on-opp_deck) matchup's series up to
    `planned` and return rows inserted. Caller supplies the open BEGIN
    IMMEDIATE connection (deckdb._write cannot nest -- floor.
    _enqueue_attempt_games precedent). Games are identified by the matchup
    CONFIG triple (agent_version_a, agent_version_b, deck_b_id), so a
    re-key back to an identical config legitimately pools its evidence."""
    existing = c.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='pair_gate' "
        "AND agent_version_a=? AND agent_version_b=? AND deck_b_id=?",
        (version, opp_version, opp_deck)).fetchone()[0]
    remaining = max(0, planned - existing)
    for _ in range(remaining):
        c.execute(
            "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, "
            "agent_version_b, purpose, priority, status) "
            "VALUES (?, ?, ?, ?, 'pair_gate', ?, 'pending')",
            (champ_deck, opp_deck, version, opp_version, PAIR_GATE_PRIORITY))
    return remaining


def enqueue_pair_gate(conn: sqlite3.Connection, version: str,
                      evictee: EvicteeRef,
                      n_games: int = PAIR_GATE_GAMES) -> int:
    """Ensure a pair_gate_checks row + a full series exists for `version`
    vs `evictee`; returns games enqueued this call (resumable top-up,
    anchor.enqueue_anchor_series precedent). No-op on a settled verdict.
    One BEGIN IMMEDIATE transaction (Pattern SQLITE-TXN).

    Supersede-DELETE (Fix round 1, Task 6 opus review, real-DB probe):
    unlike anchor's baseline+elect model, pair_gate has exactly ONE
    version that can legitimately be gating uploads at a time -- the
    caller always passes the CURRENT baseline's version
    (`subscheduler.py`'s `version = baseline["version"]`). When a new
    baseline crowns before the prior one's series finishes, its stale
    `pending` games would otherwise sit in the runner queue tied on
    priority with the fresh series and win the (priority DESC, id ASC)
    claim order by lower id, delaying the live champion's upload by a
    full dead series. Sweep them unconditionally, before the idempotence
    check below, keyed on version identity alone (NOT on the abandoned
    row's verdict, which stays 'pending' forever once superseded --
    there is no 'superseded' value in the CHECK constraint, so the row is
    left as schema-valid history and only its `pending` games are culled;
    `done` games remain, mirroring `ensure_current_opponent`'s rekey
    comment). Mirrors anchor.py's supersede-DELETE (anchor.py:154-158)."""
    _ensure_schema(conn)

    def _apply(c: sqlite3.Connection) -> int:
        c.execute(
            "DELETE FROM games WHERE purpose='pair_gate' AND "
            "status='pending' AND agent_version_a != ?", (version,))
        row = c.execute(
            "SELECT opp_version, opp_deck_id, games_planned, verdict "
            "FROM pair_gate_checks WHERE version=?", (version,)).fetchone()
        if row is not None and row["verdict"] != "pending":
            return 0
        base = c.execute(
            "SELECT deck_id FROM baselines WHERE version=?", (version,)
        ).fetchone()
        if base is None:
            raise ValueError(
                f"enqueue_pair_gate: no baselines row for {version!r}")
        if row is None:
            c.execute(
                "INSERT INTO pair_gate_checks(version, opp_version, "
                "opp_deck_id, opp_submitted_at, games_planned, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (version, evictee.version, evictee.deck_id,
                 evictee.submitted_at, n_games, _now()))
            opp_version, opp_deck, planned = (
                evictee.version, evictee.deck_id, n_games)
        else:
            opp_version, opp_deck, planned = (
                row["opp_version"], row["opp_deck_id"], row["games_planned"])
        return _enqueue_series(c, version, base["deck_id"], opp_version,
                               opp_deck, planned)

    return deckdb._write(conn, _apply)


def pair_gate_status(conn: sqlite3.Connection, version: str
                     ) -> tuple[str, int, int, float | None]:
    """Read-only gate evidence (mirrors anchor.anchor_status): (verdict,
    done, planned, wr). 'absent' when no row exists. All shapes
    first-class (provenance-shaped-optional-fields)."""
    _ensure_schema(conn)
    row = conn.execute(
        "SELECT opp_version, opp_deck_id, games_planned, games_done, wr, "
        "verdict FROM pair_gate_checks WHERE version=?", (version,)
    ).fetchone()
    if row is None:
        return ("absent", 0, PAIR_GATE_GAMES, None)
    if row["verdict"] != "pending":
        return (row["verdict"], row["games_done"], row["games_planned"],
                row["wr"])
    done = conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='pair_gate' "
        "AND status='done' AND agent_version_a=? AND agent_version_b=? "
        "AND deck_b_id=?",
        (version, row["opp_version"], row["opp_deck_id"])).fetchone()[0]
    return ("pending", done, row["games_planned"], None)


def resolve_pair_gate(conn: sqlite3.Connection, version: str) -> str:
    """Resolve the pair-gate verdict once its series is fully done:
    wr >= PAIR_GATE_BAR -> 'pass', else 'fail'. Returns
    'absent'/'pending'/'pass'/'fail'. Draws count as champion losses
    (winner=0-only wins aggregate). The verdict UPDATE is guarded
    (`AND verdict='pending'`) and the whole read-decide-act sequence is
    ONE BEGIN IMMEDIATE transaction, so a racing resolver's own
    settled-verdict read runs strictly after the winner's COMMIT and it
    never reaches the UPDATE
    (test_resolve_verdict_write_happens_exactly_once_under_race). A
    settled verdict never flips."""
    _ensure_schema(conn)

    def _apply(c: sqlite3.Connection) -> str:
        row = c.execute(
            "SELECT opp_version, opp_deck_id, games_planned, verdict "
            "FROM pair_gate_checks WHERE version=?", (version,)).fetchone()
        if row is None:
            return "absent"
        if row["verdict"] != "pending":
            return row["verdict"]
        agg = c.execute(
            _PAIR_GATE_RESULTS_QUERY,
            (version, row["opp_version"], row["opp_deck_id"])).fetchone()
        n = agg["n"] or 0
        if n < row["games_planned"]:
            return "pending"
        wins = agg["wins"] or 0
        wr = wins / n
        verdict = "pass" if wr >= PAIR_GATE_BAR else "fail"
        cur = c.execute(
            "UPDATE pair_gate_checks SET games_done=?, wins=?, wr=?, "
            "verdict=?, resolved_at=? WHERE version=? AND verdict='pending'",
            (n, wins, wr, verdict, _now(), version))
        if cur.rowcount != 1:
            # Unreachable under BEGIN IMMEDIATE; loud-guard vs regression
            # (floor.resolve_floor precedent): report the settled verdict.
            settled = c.execute(
                "SELECT verdict FROM pair_gate_checks WHERE version=?",
                (version,)).fetchone()
            return settled["verdict"]
        return verdict

    return deckdb._write(conn, _apply)


def ensure_current_opponent(conn: sqlite3.Connection, version: str,
                            evictee: EvicteeRef) -> str:
    """Upload-time TOCTOU guard (I4): inside ONE BEGIN IMMEDIATE
    transaction, verify the check row's frozen opponent still matches the
    CURRENTLY to-be-evicted submission. Returns:

    * 'current' -- same (opp_version, opp_deck_id) CONFIG: the evidence is
      against the right opponent. A changed `submitted_at` alone (the same
      identity re-uploaded) updates the stored stamp in place -- same
      config, evidence still valid for the new counted slot.
    * 'rekeyed' -- config mismatch: a stale verdict must NOT pass. The row
      re-keys onto the new evictee (counters reset, verdict back to
      'pending'), the old matchup's pending games are superseded-DELETEd
      (`games.status` has no 'cancelled' -- anchor.enqueue_anchor_series
      precedent; done games remain as history), and the new matchup's full
      series is enqueued -- all in the SAME transaction, so the upload
      gate re-blocks atomically with the re-key.
    * 'absent' -- no check row (caller-ordering bug; treat as not-current).
    """
    _ensure_schema(conn)

    def _apply(c: sqlite3.Connection) -> str:
        row = c.execute(
            "SELECT opp_version, opp_deck_id, opp_submitted_at, "
            "games_planned FROM pair_gate_checks WHERE version=?",
            (version,)).fetchone()
        if row is None:
            return "absent"
        if (row["opp_version"], row["opp_deck_id"]) == (
                evictee.version, evictee.deck_id):
            if row["opp_submitted_at"] != evictee.submitted_at:
                c.execute(
                    "UPDATE pair_gate_checks SET opp_submitted_at=? "
                    "WHERE version=?", (evictee.submitted_at, version))
            return "current"
        base = c.execute(
            "SELECT deck_id FROM baselines WHERE version=?", (version,)
        ).fetchone()
        if base is None:
            raise ValueError(
                f"ensure_current_opponent: no baselines row for {version!r}")
        c.execute(
            "UPDATE pair_gate_checks SET opp_version=?, opp_deck_id=?, "
            "opp_submitted_at=?, games_done=0, wins=0, wr=NULL, "
            "verdict='pending', created_at=?, resolved_at=NULL "
            "WHERE version=?",
            (evictee.version, evictee.deck_id, evictee.submitted_at,
             _now(), version))
        c.execute(
            "DELETE FROM games WHERE purpose='pair_gate' AND "
            "status='pending' AND agent_version_a=? AND agent_version_b=? "
            "AND deck_b_id=?",
            (version, row["opp_version"], row["opp_deck_id"]))
        _enqueue_series(c, version, base["deck_id"], evictee.version,
                        evictee.deck_id, row["games_planned"])
        return "rekeyed"

    return deckdb._write(conn, _apply)
