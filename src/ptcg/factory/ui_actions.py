"""Query + write actions for the pool/search/restore UI (ui-remove-any-deck).

Write functions all go through `deckdb._write` (BEGIN IMMEDIATE) — the split
read->decide->UPDATE shape is a REJECTED pattern in this repo (TOCTOU class,
commit 178043b). Read functions are plain SELECTs.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

from ptcg.factory import deckdb, loop_state

_TABS = ("untested", "culled", "active", "all")
SEARCH_ROW_CAP = 200

#: Canonical-deck sub-select, mirroring ui_server._BOTTOM_TEN_QUERY's
#: MIN(shell_variant) rationale (one row per concept).
_POOL_QUERY = (
    "SELECT c.id AS concept_id, d.id AS deck_id, d.cards AS cards, "
    "co.rating AS rating, co.games_played AS games_played "
    "FROM concepts c "
    "JOIN decks d ON d.concept_id = c.id AND d.shell_variant = ("
    "SELECT MIN(d2.shell_variant) FROM decks d2 WHERE d2.concept_id = c.id) "
    "LEFT JOIN coverage co ON co.concept_id = c.id "
    "WHERE c.status = 'active' "
    "ORDER BY co.rating DESC, c.id ASC"
)


def search_concepts(
    conn: sqlite3.Connection, q: str, tab: str = "untested", limit: int = SEARCH_ROW_CAP
) -> tuple[list[dict], int]:
    if tab not in _TABS:
        raise ValueError(f"search_concepts: tab must be one of {_TABS}, got {tab!r}")
    like = f"%{q}%"
    where = "(c.id LIKE ? OR c.cores LIKE ?)"
    params: list = [like, like]
    if tab != "all":
        where += " AND c.status = ?"
        params.append(tab)
    total = conn.execute(
        f"SELECT COUNT(*) FROM concepts c WHERE {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT c.id AS concept_id, c.cores AS cores, c.status AS status, "
        "c.reason AS reason, co.rating AS rating, co.games_played AS games_played "
        f"FROM concepts c LEFT JOIN coverage co ON co.concept_id = c.id WHERE {where} "
        "ORDER BY c.id ASC LIMIT ?",
        (*params, limit),
    ).fetchall()
    return [dict(r) for r in rows], total


def pool_decks(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(_POOL_QUERY).fetchall()]


def canonical_decks(
    conn: sqlite3.Connection, concept_ids: list[str]
) -> dict[str, list[int]]:
    """Canonical (MIN shell_variant, same convention as _POOL_QUERY) deck
    card-ids for the given concepts. Concepts with no built deck are absent.

    Access path (pinned by test): indexed via ix_decks_concept, read-only,
    outside any write transaction; callers pass at most SEARCH_ROW_CAP (200)
    ids, so the IN list stays far under SQLite's default 999-variable limit.
    """
    if not concept_ids:
        return {}
    placeholders = ",".join("?" * len(concept_ids))
    rows = conn.execute(
        f"SELECT concept_id, cards, shell_variant FROM decks "
        f"WHERE concept_id IN ({placeholders}) "
        "ORDER BY concept_id ASC, shell_variant ASC",
        concept_ids,
    ).fetchall()
    out: dict[str, list[int]] = {}
    for r in rows:
        cid = r["concept_id"]
        if cid not in out:  # first row per concept == MIN shell_variant
            out[cid] = json.loads(r["cards"])
    return out


def anchor_concept(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT id AS concept_id, cores FROM concepts WHERE status='finalist'"
    ).fetchone()
    return dict(row) if row is not None else None


def champion_concept_id(conn: sqlite3.Connection) -> str | None:
    baseline = loop_state.current_baseline(conn)
    if baseline is None:
        return None
    row = conn.execute(
        "SELECT concept_id FROM decks WHERE id=?", (baseline["deck_id"],)
    ).fetchone()
    return row["concept_id"] if row is not None else None


# --- apply_concept_decision ---------------------------------------------

class FinalistProtectedError(ValueError):
    """Culling the anchor (status='finalist') is refused."""


class UnknownConceptError(ValueError):
    """No concept exists with the given id (MINOR-7 fix, 2026-08-05: the
    spec's Error handling section promises 404 for this case; a plain
    ValueError is indistinguishable from every other validation failure
    ui_server maps to 400, so it needs its own subclass to route on)."""


def _restore_target(c: sqlite3.Connection, concept_id: str) -> str:
    """Two-tier restore: back to 'active' ONLY when the most recent UI cull
    of this concept recorded prior_status='active'; every other case —
    no audit row (reseed-culled), v1 rows (NULL prior_status), culled-while-
    untested — restores to 'untested'.

    Restoring to 'untested' is NOT re-gated by a rating bar of any kind:
    `census.promote_proven_singles` promotes on `games_played >= floor AND
    rating IS NOT NULL` with no rating bar at all, so a stale-but-decisive
    coverage row would insta-promote a restored junk concept back to
    'active' with zero new games played. The actual, load-bearing mitigation
    lives in the CALLER (`_decide_one`, below): when `_restore_target`
    returns 'untested', `_decide_one` zeroes the concept's `coverage` row in
    the SAME transaction, forcing a genuine re-screen from scratch before
    the concept can be promoted again. Do not delete that reset as
    "redundant" with this function — this function only decides the target
    TIER; the reset is what actually re-gates it."""
    last = c.execute(
        "SELECT prior_status FROM decisions "
        "WHERE concept_id=? AND action IN ('remove','bulk-remove') "
        "ORDER BY id DESC LIMIT 1",
        (concept_id,),
    ).fetchone()
    return "active" if last is not None and last["prior_status"] == "active" else "untested"


def _decide_one(c: sqlite3.Connection, concept_id: str, action: str, actor: str) -> str:
    """Shared per-concept core for single + bulk paths. Runs INSIDE a
    deckdb._write transaction owned by the caller. Returns the new status."""
    row = c.execute("SELECT status FROM concepts WHERE id=?", (concept_id,)).fetchone()
    if row is None:
        raise UnknownConceptError(f"no concept with id={concept_id!r}")
    status = row["status"]
    if action in ("remove", "bulk-remove"):
        if status == "finalist":
            raise FinalistProtectedError("anchor is protected")
        if status not in ("untested", "active"):
            raise ValueError(f"cannot remove a {status!r} concept")
        new_status = "culled"
        reason = f"ui-cull: {dt.datetime.now(dt.timezone.utc).isoformat()}"
    elif action in ("restore", "bulk-restore"):
        if status != "culled":
            raise ValueError(f"restore requires a culled concept, got {status!r}")
        new_status = _restore_target(c, concept_id)
        reason = f"ui-restore: {dt.datetime.now(dt.timezone.utc).isoformat()}"
        if new_status == "untested":
            # CRITICAL fix (2026-08-05, Brad-decided remedy): a concept
            # restored to the untested tier must have its coverage record
            # ZEROED in this SAME transaction, not just its status flipped.
            # Without this, a concept culled with a stale but decisive
            # rating (e.g. games_played=60, rating=0.03 from before it was
            # culled) is invisible to nothing: census._CANDIDATES_QUERY only
            # re-schedules concepts with games_played < floor, and
            # census.promote_proven_singles promotes on
            # `games_played >= floor AND rating IS NOT NULL` with NO rating
            # bar at all -- so the restored junk concept insta-promotes
            # straight back to 'active' on the very next census tick with
            # ZERO new games played (reviewer receipt: a wr-0.03 lineage
            # went culled -> untested -> active with 0 games). Zeroing here
            # forces a genuine re-screen from scratch before the concept can
            # ever be promoted again. A concept with no coverage row at all
            # needs nothing -- rowcount 0 on this UPDATE is a safe no-op.
            c.execute(
                "UPDATE coverage SET games_played=0, distinct_opponents=0, rating=NULL "
                "WHERE concept_id=?",
                (concept_id,),
            )
    else:
        raise ValueError(f"action must be remove/restore, got {action!r}")
    c.execute(
        "INSERT INTO decisions(concept_id, action, actor, timestamp, prior_status) "
        "VALUES(?,?,?,?,?)",
        (concept_id, action, actor, dt.datetime.now(dt.timezone.utc).isoformat(), status),
    )
    c.execute("UPDATE concepts SET status=?, reason=? WHERE id=?", (new_status, reason, concept_id))
    return new_status


def apply_concept_decision(
    conn: sqlite3.Connection, concept_id: str, action: str, actor: str = "brad"
) -> str:
    if action not in ("remove", "restore"):
        raise ValueError(f"action must be 'remove' or 'restore', got {action!r}")
    return deckdb._write(conn, lambda c: _decide_one(c, concept_id, action, actor))


# --- apply_bulk_decision --------------------------------------------------

_BULK_TAB_FOR_ACTION = {"bulk-remove": "untested", "bulk-restore": "culled"}


def apply_bulk_decision(
    conn: sqlite3.Connection, q: str, tab: str, action: str, actor: str = "brad"
) -> int:
    """Cull/restore EVERY concept matching (q, tab) in ONE BEGIN IMMEDIATE
    transaction: the match list is computed INSIDE the txn (never trusted
    from a preview page), then each concept goes through the same
    `_decide_one` core as the single-row path. Returns the actual count."""
    required_tab = _BULK_TAB_FOR_ACTION.get(action)
    if required_tab is None:
        raise ValueError(f"bulk action must be bulk-remove/bulk-restore, got {action!r}")
    if tab != required_tab:
        raise ValueError(f"{action} requires tab={required_tab!r}, got {tab!r}")
    if not q.strip():
        raise ValueError("bulk actions require a non-empty search query")

    def _apply(c: sqlite3.Connection) -> int:
        like = f"%{q}%"
        ids = [
            r["id"]
            for r in c.execute(
                "SELECT id FROM concepts WHERE status=? AND (id LIKE ? OR cores LIKE ?) "
                "ORDER BY id",
                (required_tab, like, like),
            ).fetchall()
        ]
        for cid in ids:
            _decide_one(c, cid, action, actor)
        return len(ids)

    return deckdb._write(conn, _apply)
