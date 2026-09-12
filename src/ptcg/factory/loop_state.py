"""Baseline + offspring state helpers over SQLite (tournament T11).

Thin primitives on top of `deckdb`'s `baselines`/`offspring`/`meta` tables
(schema already present since Phase 1 — this module adds no DDL). These are
the building blocks the Baseline-Challenge Loop (T12-T17) uses to track the
current champion (`baselines` + `meta['baseline_version']`) and the pipeline
of challengers (`offspring`, one row per bred agent moving through
`training -> queued_for_match -> matching -> confirming -> {trashed,survivor}`).

Versioning (spec Locked Decision, Global Constraints): base `v0.1`; offspring
bred while `v0.G` is the baseline are numbered `v0.G.1 .. v0.G.k` (patch
number = count of existing `offspring` rows whose `parent_baseline_version`
equals the current baseline version, plus one); each CROWN bumps the MINOR
version (`v0.1 -> v0.2`) via `candidates.bump_minor`, and offspring numbering
resets to `.1` under the new baseline.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3

from ptcg.factory import deckdb
from ptcg.factory.candidates import bump_minor

# Mirrors the `offspring.status` CHECK constraint in `deckdb.py` — kept here
# so a bad status is rejected with a clear message before it ever reaches
# sqlite's own (less legible) IntegrityError.
_VALID_OFFSPRING_STATUSES = frozenset(
    {"training", "queued_for_match", "matching", "confirming", "trashed", "survivor"}
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def set_founding_baseline(conn: sqlite3.Connection, deck_id: str, agent_config: dict) -> None:
    """Record the founding `v0.1` baseline.

    Inserts `baselines('v0.1', NULL, deck_id, now)` and sets
    `meta['baseline_version']='v0.1'`. `baselines` has no dedicated column
    for `agent_config` (the schema is locked from Phase 1 — see the T10
    additive-only containment gate), so `agent_config` is persisted as JSON
    under `meta['founding_agent_config']` for retrievability rather than
    silently discarded (JUDGMENT CALL — the plan's Produces text only names
    the two writes below; this third write reuses the existing `meta`
    key-value table and adds no schema).
    """

    def _apply(c: sqlite3.Connection) -> None:
        now = _now()
        c.execute(
            "INSERT INTO baselines(version, offspring_id, deck_id, crowned_at) "
            "VALUES ('v0.1', NULL, ?, ?)",
            (deck_id, now),
        )
        c.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('baseline_version', 'v0.1')"
        )
        c.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('founding_agent_config', ?)",
            (json.dumps(agent_config),),
        )

    deckdb._write(conn, _apply)


def current_baseline(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Return the `baselines` row for the current champion, or None if no
    baseline has been founded yet. Current-ness is tracked by
    `meta['baseline_version']`, not by MAX(crowned_at) — CROWN and future
    corrective writes update both atomically."""
    version_row = conn.execute("SELECT value FROM meta WHERE key='baseline_version'").fetchone()
    if version_row is None:
        return None
    return conn.execute(
        "SELECT * FROM baselines WHERE version=?", (version_row["value"],)
    ).fetchone()


def next_offspring_version(conn: sqlite3.Connection) -> str:
    """Return the version string the NEXT bred offspring under the current
    baseline would receive (e.g. `v0.1.3`). Does not reserve or insert
    anything — purely derived from the current baseline version and a count
    of existing `offspring` rows parented to it."""
    baseline = current_baseline(conn)
    if baseline is None:
        raise RuntimeError("next_offspring_version: no baseline founded yet")
    version = baseline["version"]
    count = conn.execute(
        "SELECT COUNT(*) FROM offspring WHERE parent_baseline_version=?", (version,)
    ).fetchone()[0]
    return f"{version}.{count + 1}"


def insert_offspring(
    conn: sqlite3.Connection,
    offspring_id: str,
    search_config_json: str,
    value_net_ref: str | None,
) -> None:
    """Insert a new `offspring` row parented to the CURRENT baseline version,
    with `status='training'` (the table default) and `created_at=now`."""
    baseline = current_baseline(conn)
    if baseline is None:
        raise RuntimeError("insert_offspring: no baseline founded yet")
    parent_version = baseline["version"]

    def _apply(c: sqlite3.Connection) -> None:
        c.execute(
            "INSERT INTO offspring(id, parent_baseline_version, search_config_json, "
            "value_net_ref, created_at) VALUES (?, ?, ?, ?, ?)",
            (offspring_id, parent_version, search_config_json, value_net_ref, _now()),
        )

    deckdb._write(conn, _apply)


def set_offspring_status(conn: sqlite3.Connection, offspring_id: str, status: str) -> None:
    """Update an `offspring` row's status. Rejects any value outside the
    `offspring.status` CHECK constraint with a clear error before hitting
    sqlite (mirrors the constraint in `deckdb.py`, does not replace it)."""
    if status not in _VALID_OFFSPRING_STATUSES:
        raise ValueError(
            f"set_offspring_status: {status!r} is not a valid offspring status "
            f"(expected one of {sorted(_VALID_OFFSPRING_STATUSES)})"
        )

    def _apply(c: sqlite3.Connection) -> None:
        cur = c.execute(
            "UPDATE offspring SET status=? WHERE id=?", (status, offspring_id)
        )
        if cur.rowcount != 1:
            raise ValueError(f"set_offspring_status: no offspring row with id={offspring_id!r}")

    deckdb._write(conn, _apply)


def list_offspring(conn: sqlite3.Connection, status: str) -> list[sqlite3.Row]:
    """Return all `offspring` rows with the given status, oldest first."""
    return conn.execute(
        "SELECT * FROM offspring WHERE status=? ORDER BY created_at", (status,)
    ).fetchall()


def crown_baseline(conn: sqlite3.Connection, offspring_id: str, deck_id: str) -> str:
    """Crown `offspring_id` (on `deck_id`) as the new baseline: bump the
    current baseline's MINOR version, insert the new `baselines` row, and
    update `meta['baseline_version']`. Returns the new version string.

    Does not touch `offspring.status` for `offspring_id` or any other
    offspring row — that belongs to the higher-level CROWN resolution logic
    (T16 `resolve_crown`), which decides how survivors/non-survivors are
    marked. This helper is a pure version-and-baseline-table primitive, and
    (per the plan-authored test) must work even when `offspring_id` does not
    correspond to an existing `offspring` row — `baselines.offspring_id` has
    no FK constraint (see `deckdb.py` DDL).
    """
    current = current_baseline(conn)
    if current is None:
        raise RuntimeError("crown_baseline: no baseline founded yet")
    new_version = bump_minor(current["version"])

    def _apply(c: sqlite3.Connection) -> None:
        c.execute(
            "INSERT INTO baselines(version, offspring_id, deck_id, crowned_at) "
            "VALUES (?, ?, ?, ?)",
            (new_version, offspring_id, deck_id, _now()),
        )
        c.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('baseline_version', ?)",
            (new_version,),
        )

    deckdb._write(conn, _apply)
    return new_version
