"""Anchor-check stage: absolute strength evidence for crowned champions
(submission strength gate, spec 2026-08-01).

CROWN is purely RELATIVE (best aggregate win% among sibling survivors), so a
uniformly weak cohort still crowns a "champion" -- that is how the 325.1 junk
submission (Kaggle ref 55125891) shipped. This module wires the factory's
known-strength anchor -- `HeuristicAgent` piloting `ANCHOR_DECK_PATH`
(DECOUPLED from the ladder identity as of the min-basics pool rule, spec
2026-08-11: the ladder still plays `ptcg.agents.current.CURRENT_DECK_PATH`,
frozen at 4 basics) -- into the tournament DB as a
post-crown series and persists an absolute pass/fail verdict that
`subscheduler` reads before ANY upload (champion mark-trigger and daily-floor
probe alike -- both ship the current baseline's agent to the ladder).

The champion is ALWAYS `agent_version_a` in anchor games (MATCH/CONFIRM
fixed-side convention -- crown-style side mixing buys nothing here and would
complicate win counting). Draws (`winner == 2`) count as champion losses:
only `winner == 0` is a champion win, the conservative reading.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from ptcg.decks.validate import validate_deck
from ptcg.factory import deckdb, loop_state
from ptcg.factory.candidates import bump_minor

_REPO_ROOT = Path(__file__).resolve().parents[3]
#: The anchor deck is DECOUPLED from the ladder identity as of the min-basics
#: pool rule (spec 2026-08-11): the ladder still plays mega-lucario-fighting
#: (4 basics, frozen), while the census/floor/anchor-gate opponent is this
#: committed >=8-basic mini-tournament winner.
ANCHOR_DECK_PATH = _REPO_ROOT / "src" / "ptcg" / "decks" / "candidates" / "anchor-min8.csv"

ANCHOR_VERSION = "anchor-heuristic-v0"          # unchanged: same heuristic agent
ANCHOR_GAMES = 200
#: ANCHOR_BAR recalibrated 2026-08-13 (census/screening-regime slice, spec
#: 2026-08-13 Sec5, T6 Brad gate): set AT the baseline lineage's measured
#: WR-vs-anchor point estimate (pooled across both measurement runs:
#: 118/200 + 121/200 = 239/400 = 0.5975), rounded UP to the nearest
#: 1/ANCHOR_GAMES multiple (0.60) rather than padded with a margin above
#: it -- a same-strength champion-elect therefore passes only ~50% of the
#: time (0.5019 recomputed at p=0.5975, n=200); the bar filters
#: weaker-than-baseline candidates, not same-strength ones. Source receipt:
#: experiments/screening-regime-measurement-2026-08-14.md. (Previous: 0.55,
#: calibrated against the retired anchor.)
ANCHOR_BAR = 0.60
ANCHOR_CONCEPT_ID = "anchor-lucario-min8"
ANCHOR_DECK_ID = ANCHOR_CONCEPT_ID + "-d0"

_ANCHOR_CHECKS_DDL = (
    "CREATE TABLE IF NOT EXISTS anchor_checks("
    "version TEXT PRIMARY KEY, offspring_id TEXT, deck_id TEXT NOT NULL, "
    "games_planned INTEGER NOT NULL, games_done INTEGER NOT NULL DEFAULT 0, "
    "wins INTEGER NOT NULL DEFAULT 0, wr REAL, "
    "verdict TEXT NOT NULL DEFAULT 'pending' "
    "CHECK(verdict IN ('pending','pass','fail')), "
    "created_at TEXT NOT NULL, resolved_at TEXT)"
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Additive upgrade for a LIVE pre-slice DB (production tournament.db
    predates the `deckdb._DDL_STATEMENTS` addition and nothing re-runs
    `init_db` on it). Idempotent; virgin DBs get the table from `init_db`
    and this is a no-op. Autocommit single statement -- no `_write` needed."""
    conn.execute(_ANCHOR_CHECKS_DDL)


def ensure_anchor_deck(conn: sqlite3.Connection) -> None:
    """Register the anchor's concept/deck rows (idempotent).

    Status is `'finalist'`, NOT `'active'`: the daily-floor probe query
    (`subscheduler._BEST_ACTIVE_DECKS_QUERY`) and census culling both key on
    `'active'`, so the anchor deck can never be probed, culled, or bred.
    Cards come from `ANCHOR_DECK_PATH` (the min-basics pool-rule anchor
    source, decoupled from the ladder identity as of spec 2026-08-11), one
    card id per line -- NOT the exact deck the ladder plays on Kaggle
    (`ptcg.agents.current.CURRENT_DECK_PATH`, still frozen at 4 basics).

    Deliberately NO `coverage` row: `census.census_complete` (census.py:225)
    treats every concept that HAS a coverage row and `json_array_length(
    cores)=1` as counting toward the floor gate, and the anchor's `cores`
    is `'["anchor"]'` (length 1) -- a fresh zeroed coverage row would dip
    `census_complete` to False at every go-live until ~15 anchor games play.
    Omitting the row is safe: `deckdb.record_result`'s coverage bump is
    `UPDATE ... WHERE concept_id=(SELECT ...)`, a silent no-op against a
    missing row, so anchor games still record fine; every other coverage
    consumer filters `status='active'` (anchor is `'finalist'`, never
    matches) or `purpose='screening'` (`rating.py`), so nothing else
    depends on this row existing."""
    _ensure_schema(conn)
    cards = [
        int(line)
        for line in ANCHOR_DECK_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(cards) != 60:
        raise RuntimeError(
            f"ensure_anchor_deck: {ANCHOR_DECK_PATH} has {len(cards)} cards, "
            "expected exactly 60 -- refusing to register a malformed anchor deck"
        )
    problems = validate_deck(cards)
    if problems:
        raise RuntimeError(
            f"ensure_anchor_deck: {ANCHOR_DECK_PATH} fails validate_deck: {problems} "
            "-- refusing to register a non-compliant anchor deck"
        )

    def _apply(c: sqlite3.Connection) -> None:
        c.execute(
            "INSERT OR IGNORE INTO concepts(id, cores, status, reason) "
            "VALUES(?, '[\"anchor\"]', 'finalist', "
            "'min-basics anchor deck (strength gate, spec 2026-08-11); never cull')",
            (ANCHOR_CONCEPT_ID,),
        )
        # Local import (NOT module-level): `anchor` is imported by
        # `subscheduler`, a leaf the narrowed watch loop legitimately
        # imports (test_watch_loop_import_graph_pins_cutover_boundary). A
        # module-level `builder` import here would make `builder` reachable
        # from every watch-loop firing even though `ensure_anchor_deck` is
        # only ever called from the scheduler process — deferring the
        # import to call time keeps the watch loop's import graph clean.
        from ptcg.factory.builder import composition_counts

        en, pk = composition_counts(cards)
        c.execute(
            "INSERT OR IGNORE INTO decks(id, concept_id, cards, shell_variant, "
            "energy_count, pokemon_count) VALUES(?, ?, ?, 0, ?, ?)",
            (ANCHOR_DECK_ID, ANCHOR_CONCEPT_ID, json.dumps(cards), en, pk),
        )

    deckdb._write(conn, _apply)


def enqueue_anchor_series(conn: sqlite3.Connection) -> int:
    """Ensure EVERY pending `anchor_checks` row (the current baseline's, plus
    at most one champion-elect's -- design 3, `resolve_crown` nominates
    before this gate resolves it) has a full `ANCHOR_GAMES` series enqueued;
    returns total games enqueued this call across all pending rows.

    Three steps in one code path:
    1. Backfill: if the CURRENT baseline has no `anchor_checks` row yet,
       insert one keyed by its version -- covers go-live/founding baselines
       exactly as before this generalization (fresh crown, backfill,
       resumable top-up all still apply per-row).
    2. Supersede DELETE: stale `pending` anchor games belonging to a version
       that is no longer ANY pending check's version are deleted --
       `games.status` has no 'cancelled' value (CHECK constraint,
       deckdb.py:83), and a never-claimed pending row is pure queue waste;
       `claimed` stale games are left to finish harmlessly. Elect and
       baseline series are NEVER collateral damage here (both are pending
       checks, both are exempt).
    3. Top-up: every pending row's series is topped up to its own
       `games_planned` (mirrors `enqueue_confirm_series`'s
       count-the-shortfall design). Pending rows are <= 2 in practice (one
       baseline backfill + one elect), so this stays cheap.

    Champion is ALWAYS `agent_version_a`; priority 1.0 jumps the claim queue
    (`ORDER BY priority DESC`) because a pending verdict blocks uploads.

    Race-safe (Pattern SQLITE-TXN): the backfill read/INSERT, supersede
    DELETE, and every shortfall INSERT run inside ONE `deckdb._write`
    transaction; a concurrent caller's count read runs strictly after the
    winner's COMMIT and computes shortfall 0 (mirrors
    `enqueue_confirm_series`, verified by
    `test_enqueue_survives_concurrent_calls`)."""
    _ensure_schema(conn)
    ensure_anchor_deck(conn)

    def _apply(c: sqlite3.Connection) -> int:
        baseline = loop_state.current_baseline(c)
        if baseline is not None:
            row = c.execute(
                "SELECT verdict FROM anchor_checks WHERE version=?",
                (baseline["version"],),
            ).fetchone()
            if row is None:
                c.execute(
                    "INSERT INTO anchor_checks(version, offspring_id, deck_id, "
                    "games_planned, created_at) VALUES(?,?,?,?,?)",
                    (baseline["version"], baseline["offspring_id"],
                     baseline["deck_id"], ANCHOR_GAMES, _now()),
                )
        c.execute(
            "DELETE FROM games WHERE purpose='anchor' AND status='pending' "
            "AND agent_version_a NOT IN "
            "(SELECT version FROM anchor_checks WHERE verdict='pending')"
        )
        pending = c.execute(
            "SELECT version, deck_id, games_planned FROM anchor_checks "
            "WHERE verdict='pending'"
        ).fetchall()
        total = 0
        for row in pending:
            existing = c.execute(
                "SELECT COUNT(*) FROM games WHERE purpose='anchor' "
                "AND agent_version_a=?",
                (row["version"],),
            ).fetchone()[0]
            remaining = max(0, row["games_planned"] - existing)
            for _ in range(remaining):
                c.execute(
                    "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, "
                    "agent_version_b, purpose, priority, status) "
                    "VALUES (?, ?, ?, ?, 'anchor', 1.0, 'pending')",
                    (row["deck_id"], ANCHOR_DECK_ID, row["version"],
                     ANCHOR_VERSION),
                )
            total += remaining
        return total

    return deckdb._write(conn, _apply)


_ANCHOR_RESULTS_QUERY = (
    "SELECT SUM(CASE WHEN winner = 0 THEN 1 ELSE 0 END) AS wins, "
    "COUNT(*) AS n FROM games "
    "WHERE purpose='anchor' AND status='done' AND agent_version_a = ?"
)


def resolve_anchor_check(conn: sqlite3.Connection) -> str | None:
    """Resolve any pending anchor check whose series is fully done
    (>= games_planned completed games): wr >= ANCHOR_BAR -> 'pass', else
    'fail' (boundary: 120/200 = 0.600 -> pass). Draws already count as
    losses via the winner=0-only wins aggregate. Returns the verdict
    resolved this call (the last one resolved, if more than one row settles
    in the same call), else None.

    The anchor verdict now OWNS promotion (design 3) -- CROWN only
    nominates a champion-elect (a pending `anchor_checks` row with
    `version == offspring_id`, see `loop.resolve_crown`). A row is an
    "elect" row iff that identity holds; a baseline-keyed row (backfill,
    `offspring_id` NULL or != `version`) never promotes, matching pre-slice
    behavior exactly:
    - elect + pass: the SAME transaction inserts the new `baselines` row,
      bumps `meta['baseline_version']` (`bump_minor`), and RE-KEYS this
      check row's `version` to the new baseline version -- so
      `subscheduler.anchor_status(conn, baseline["version"])` (the upload
      gate) reads `'pass'` for the freshly crowned baseline with zero
      `subscheduler` changes. The offspring's `status` stays `'survivor'`
      (it IS the new champion now).
    - elect + fail: verdict recorded, row stays keyed by the offspring id,
      no baseline row, no version bump, no `meta` change -- breeding keeps
      producing offspring against the incumbent baseline. The offspring is
      marked `'trashed'`.
    - baseline-keyed (backfill): verdict recorded, no promotion side
      effects either way -- exactly as before this generalization.

    Race-safe (Pattern SQLITE-TXN): the pending-row read, per-version
    aggregate, the promotion INSERTs (elect+pass only), and the verdict
    UPDATE all run in ONE `deckdb._write` transaction; the UPDATE is
    guarded `AND verdict='pending'` so a concurrent resolver that lost the
    lock race observes the settled row and no-ops
    (`test_resolve_survives_concurrent_calls`). A settled verdict is never
    recomputed or flipped."""
    _ensure_schema(conn)

    def _apply(c: sqlite3.Connection) -> str | None:
        resolved: str | None = None
        pending = c.execute(
            "SELECT version, offspring_id, deck_id, games_planned "
            "FROM anchor_checks WHERE verdict='pending'"
        ).fetchall()
        for row in pending:
            agg = c.execute(_ANCHOR_RESULTS_QUERY, (row["version"],)).fetchone()
            n = agg["n"] or 0
            if n < row["games_planned"]:
                continue
            wins = agg["wins"] or 0
            wr = wins / n
            verdict = "pass" if wr >= ANCHOR_BAR else "fail"
            elect = (
                row["offspring_id"] is not None
                and row["version"] == row["offspring_id"]
            )
            if elect and verdict == "pass":
                current = loop_state.current_baseline(c)
                if current is None:
                    raise RuntimeError(
                        "resolve_anchor_check: no baseline founded yet"
                    )
                new_version = bump_minor(current["version"])
                c.execute(
                    "INSERT INTO baselines(version, offspring_id, deck_id, "
                    "crowned_at) VALUES (?, ?, ?, ?)",
                    (new_version, row["offspring_id"], row["deck_id"], _now()),
                )
                c.execute(
                    "INSERT OR REPLACE INTO meta(key, value) "
                    "VALUES ('baseline_version', ?)",
                    (new_version,),
                )
                cur = c.execute(
                    "UPDATE anchor_checks SET games_done=?, wins=?, wr=?, "
                    "verdict='pass', resolved_at=?, version=? "
                    "WHERE version=? AND verdict='pending'",
                    (n, wins, wr, _now(), new_version, row["version"]),
                )
            else:
                cur = c.execute(
                    "UPDATE anchor_checks SET games_done=?, wins=?, wr=?, "
                    "verdict=?, resolved_at=? WHERE version=? AND verdict='pending'",
                    (n, wins, wr, verdict, _now(), row["version"]),
                )
                if cur.rowcount == 1 and elect and verdict == "fail":
                    c.execute(
                        "UPDATE offspring SET status='trashed' WHERE id=?",
                        (row["offspring_id"],),
                    )
            if cur.rowcount == 1:
                resolved = verdict
        return resolved

    return deckdb._write(conn, _apply)


def anchor_status(
    conn: sqlite3.Connection, version: str
) -> tuple[str, int, int, float | None]:
    """Read-only gate evidence for `version`: (verdict, done_games, planned,
    wr). verdict is 'absent' when no check row exists (treated as pending by
    the gate -- the scheduler's backfill will create it). All four evidence
    shapes are first-class (`provenance-shaped-optional-fields` rule)."""
    _ensure_schema(conn)
    row = conn.execute(
        "SELECT games_planned, games_done, wr, verdict FROM anchor_checks "
        "WHERE version=?",
        (version,),
    ).fetchone()
    if row is None:
        return ("absent", 0, ANCHOR_GAMES, None)
    if row["verdict"] != "pending":
        return (row["verdict"], row["games_done"], row["games_planned"], row["wr"])
    done = conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='anchor' AND status='done' "
        "AND agent_version_a=?",
        (version,),
    ).fetchone()[0]
    return ("pending", done, row["games_planned"], None)
