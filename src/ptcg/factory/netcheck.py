"""Validated net swaps (anchor-pressure design 4).

A freshly trained value net is adopted for an offspring only if it beats the
incumbent net (the current baseline's net) head-to-head: wr >= NETCHECK_BAR
over NETCHECK_GAMES mirrored games on the baseline's deck, same SearchConfig
on both sides, only `net_weights` differing. Otherwise the incumbent net is
kept and the rejection is recorded in `net_checks` (candidate identity,
head-to-head result, verdict) so the training pipeline stays auditable.

Head-to-head isolates the NET: both sides play the offspring's OWN
`search_config_json` on the BASELINE's deck (the deck the net was trained
for), mirrored; only `net_weights` differs (candidate = offspring
`value_net_ref`, incumbent = current baseline's net).

Sentinel versions `netcheck-cand:<oid>` / `netcheck-inc:<oid>` keep the
offspring id itself un-resolved (hence un-cached by runner workers) until the
adoption decision has settled -- see runner_pool._resolve_agent_entry.

Draws count as candidate losses. Boundary: 55/100 = 0.55 adopts, 54/100
rejects. Every read-decide-act sequence is one `deckdb._write` transaction.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

from ptcg.factory import deckdb, loop_state

NETCHECK_GAMES = 100
NETCHECK_BAR = 0.55
NETCHECK_PRIORITY = 0.6
NETCHECK_CAND_PREFIX = "netcheck-cand:"
NETCHECK_INC_PREFIX = "netcheck-inc:"

_NET_CHECKS_DDL = (
    "CREATE TABLE IF NOT EXISTS net_checks("
    "offspring_id TEXT PRIMARY KEY, deck_id TEXT NOT NULL, "
    "candidate_net_ref TEXT, incumbent_net_ref TEXT, "
    "games_planned INTEGER NOT NULL, games_done INTEGER NOT NULL DEFAULT 0, "
    "wins INTEGER NOT NULL DEFAULT 0, wr REAL, "
    "verdict TEXT NOT NULL DEFAULT 'pending' "
    "CHECK(verdict IN ('pending','adopt','reject','auto')), "
    "created_at TEXT NOT NULL, resolved_at TEXT)"
)

_NETCHECK_RESULTS_QUERY = (
    "SELECT SUM(CASE WHEN winner = 0 THEN 1 ELSE 0 END) AS wins, "
    "COUNT(*) AS n FROM games "
    "WHERE purpose='netcheck' AND status='done' AND agent_version_a = ?"
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_NET_CHECKS_DDL)


def cand_version(offspring_id: str) -> str:
    return f"{NETCHECK_CAND_PREFIX}{offspring_id}"


def inc_version(offspring_id: str) -> str:
    return f"{NETCHECK_INC_PREFIX}{offspring_id}"


def _incumbent_net_ref(c: sqlite3.Connection, baseline: sqlite3.Row) -> str | None:
    """Current baseline's net, covering both provenances
    (provenance-shaped-optional-fields): founding -> founding_agent_config's
    net_weights key (may be absent -> None); crowned -> winning offspring's
    value_net_ref (nullable column -> None)."""
    if baseline["offspring_id"] is None:
        row = c.execute(
            "SELECT value FROM meta WHERE key='founding_agent_config'"
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["value"]).get("net_weights")
    off = c.execute(
        "SELECT value_net_ref FROM offspring WHERE id=?", (baseline["offspring_id"],)
    ).fetchone()
    return off["value_net_ref"] if off is not None else None


def enqueue_net_check(
    conn: sqlite3.Connection, offspring_id: str, n_games: int = NETCHECK_GAMES
) -> int:
    """Ensure a net_checks row + series exists for a 'training' offspring;
    returns games enqueued this call (resumable top-up). Degenerate shapes
    (no incumbent net, no candidate net, identical refs) short-circuit to
    verdict 'auto' and advance the offspring -- no games played."""
    _ensure_schema(conn)

    def _apply(c: sqlite3.Connection) -> int:
        off = c.execute(
            "SELECT status, value_net_ref FROM offspring WHERE id=?", (offspring_id,)
        ).fetchone()
        if off is None:
            raise ValueError(
                f"enqueue_net_check: no offspring row with id={offspring_id!r}"
            )
        if off["status"] != "training":
            return 0
        row = c.execute(
            "SELECT verdict, deck_id FROM net_checks WHERE offspring_id=?",
            (offspring_id,),
        ).fetchone()
        if row is not None and row["verdict"] != "pending":
            return 0
        if row is None:
            baseline = loop_state.current_baseline(c)
            if baseline is None:
                raise RuntimeError("enqueue_net_check: no baseline founded yet")
            incumbent = _incumbent_net_ref(c, baseline)
            candidate = off["value_net_ref"]
            if incumbent is None or candidate is None or incumbent == candidate:
                c.execute(
                    "INSERT INTO net_checks(offspring_id, deck_id, candidate_net_ref, "
                    "incumbent_net_ref, games_planned, verdict, created_at, resolved_at) "
                    "VALUES(?,?,?,?,0,'auto',?,?)",
                    (offspring_id, baseline["deck_id"], candidate, incumbent,
                     _now(), _now()),
                )
                c.execute(
                    "UPDATE offspring SET status='queued_for_match' "
                    "WHERE id=? AND status='training'",
                    (offspring_id,),
                )
                return 0
            c.execute(
                "INSERT INTO net_checks(offspring_id, deck_id, candidate_net_ref, "
                "incumbent_net_ref, games_planned, created_at) VALUES(?,?,?,?,?,?)",
                (offspring_id, baseline["deck_id"], candidate, incumbent,
                 n_games, _now()),
            )
            deck_id = baseline["deck_id"]
            planned = n_games
        else:
            deck_id = row["deck_id"]
            planned = c.execute(
                "SELECT games_planned FROM net_checks WHERE offspring_id=?",
                (offspring_id,),
            ).fetchone()[0]
        version_a = cand_version(offspring_id)
        existing = c.execute(
            "SELECT COUNT(*) FROM games WHERE purpose='netcheck' AND agent_version_a=?",
            (version_a,),
        ).fetchone()[0]
        remaining = max(0, planned - existing)
        for _ in range(remaining):
            c.execute(
                "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, "
                "agent_version_b, purpose, priority, status) "
                "VALUES (?, ?, ?, ?, 'netcheck', ?, 'pending')",
                (deck_id, deck_id, version_a, inc_version(offspring_id),
                 NETCHECK_PRIORITY),
            )
        return remaining

    return deckdb._write(conn, _apply)


def resolve_net_check(conn: sqlite3.Connection, offspring_id: str) -> str:
    """Resolve once the series is fully done: wr >= NETCHECK_BAR -> 'adopt'
    (keep the new net), else 'reject' (swap value_net_ref back to the
    incumbent). Either way the offspring advances to 'queued_for_match' in
    the SAME transaction. Settled verdicts never flip."""
    _ensure_schema(conn)

    def _apply(c: sqlite3.Connection) -> str:
        row = c.execute(
            "SELECT games_planned, verdict, incumbent_net_ref FROM net_checks "
            "WHERE offspring_id=?",
            (offspring_id,),
        ).fetchone()
        if row is None:
            return "absent"
        if row["verdict"] != "pending":
            return row["verdict"]
        agg = c.execute(_NETCHECK_RESULTS_QUERY, (cand_version(offspring_id),)).fetchone()
        n = agg["n"] or 0
        if n < row["games_planned"]:
            return "pending"
        wins = agg["wins"] or 0
        wr = wins / n
        verdict = "adopt" if wr >= NETCHECK_BAR else "reject"
        cur = c.execute(
            "UPDATE net_checks SET games_done=?, wins=?, wr=?, verdict=?, "
            "resolved_at=? WHERE offspring_id=? AND verdict='pending'",
            (n, wins, wr, verdict, _now(), offspring_id),
        )
        if cur.rowcount == 1:
            if verdict == "reject":
                c.execute(
                    "UPDATE offspring SET value_net_ref=?, status='queued_for_match' "
                    "WHERE id=? AND status='training'",
                    (row["incumbent_net_ref"], offspring_id),
                )
            else:
                c.execute(
                    "UPDATE offspring SET status='queued_for_match' "
                    "WHERE id=? AND status='training'",
                    (offspring_id,),
                )
        return verdict

    return deckdb._write(conn, _apply)


def net_status(
    conn: sqlite3.Connection, offspring_id: str
) -> tuple[str, int, int, float | None]:
    """Read-only evidence: (verdict, done, planned, wr); 'absent' if no row."""
    _ensure_schema(conn)
    row = conn.execute(
        "SELECT games_planned, games_done, wr, verdict FROM net_checks "
        "WHERE offspring_id=?",
        (offspring_id,),
    ).fetchone()
    if row is None:
        return ("absent", 0, NETCHECK_GAMES, None)
    if row["verdict"] != "pending":
        return (row["verdict"], row["games_done"], row["games_planned"], row["wr"])
    done = conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='netcheck' AND status='done' "
        "AND agent_version_a=?",
        (cand_version(offspring_id),),
    ).fetchone()[0]
    return ("pending", done, row["games_planned"], None)
