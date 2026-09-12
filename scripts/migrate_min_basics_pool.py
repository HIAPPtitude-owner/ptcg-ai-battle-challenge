"""One-shot migration: enforce the min-basics pool rule (`MIN_BASIC_CARDS=8`,
spec 2026-08-11, `.superpowers/sdd/2026-08-11-min-basics-pool-rule`) against
every buildable concept/deck in the tournament pool, reset coverage for a
clean re-screen, trash in-flight offspring bred under the old
(<8-basic-tolerant) rules, fail every pending anchor/floor check, and install
the new min-basics anchor deck as the `finalist` opponent.

Semantics:
  * CULL every buildable concept (`untested`/`active`/`finalist` -- i.e.
    every status except `unbuildable` and already-`culled`) whose deck has
    fewer than `MIN_BASIC_CARDS` basics. This is ALL current built decks,
    champions and the OLD ladder-identity anchor included. Cull is a STATUS
    CHANGE (`concepts.status='culled'`, `reason` recorded) -- concept and
    deck rows are NEVER deleted.
  * RESTORE to `untested` every SINGLE concept (`json_array_length(cores)=1`)
    that is currently `culled` but now has `>=MIN_BASIC_CARDS` basics --
    UNLESS its most recent `decisions` row is a human `remove`/`bulk-remove`
    cull, which is respected and left culled. PAIR concepts (`cores` arity
    2) that are `culled` stay `culled` (R2 scope decision -- no automatic
    pair re-activation).
  * RESET coverage: every `coverage` row's `games_played`/`distinct_opponents`
    -> 0, `rating` -> NULL, forcing a clean re-screen under the new pool.
  * TRASH every non-`trashed` `offspring` row -- in-flight breeding lineages
    were bred/evaluated against the old (<8-basic-tolerant) deck pool and
    carry no valid provenance under the new rule.
  * FAIL every `pending` `anchor_checks`/`floor_checks` row (`verdict='fail'`,
    `resolved_at` stamped) -- their target decks/baselines are being culled
    out from under them.
  * INSTALL the new min-basics anchor deck as `finalist`
    (`anchor.ensure_anchor_deck`) -- run in a SEPARATE transaction AFTER
    commit, because `deckdb._write` cannot nest inside this script's own
    `BEGIN IMMEDIATE`.

`pair_gate_checks` is DELIBERATELY untouched: the settled `pass` row is
history, and `enqueue_pair_gate`'s supersede-DELETE already handles version
turnover on its own. The `games` queue is NOT deleted -- the drain guard
below makes an EMPTY queue a precondition instead (R4). Champion/old-anchor
DECK ROWS are never written by this script (pair-gate reconstruction
invariant, R6) -- only their CONCEPT status changes.

DRAIN GUARD (R4): the runner pool has no PAUSE check of its own, so this
script must never run concurrently with in-flight games. The pending/claimed
COUNT is checked INSIDE the same `BEGIN IMMEDIATE` transaction that performs
every write -- if the queue is not drained, the transaction raises and rolls
back WITHOUT writing anything. Operator protocol: hold `experiments/factory/
PAUSE`, wait for the queue to reach 0, then run this script.

Never invoked by any worker -- this is a one-time, by-hand migration at the
post-merge go-live rung. Tests operate on `tmp_path` DBs (or an explicit
`shutil.copy` of a production-scale copy) only -- NEVER the live
`experiments/factory/tournament.db` from a test run.

Idempotency: every write is gated by a `WHERE status != 'culled'` /
`WHERE status='culled'` (etc.) row-count guard, so re-running after a
successful migration culls/restores/resets/trashes/fails zero additional
rows (the coverage reset and offspring-trash UPDATEs are unconditional but
already-zeroed/already-trashed rows simply produce a matching, no-change
UPDATE).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ptcg.decks.validate import MIN_BASIC_CARDS, is_basic_pokemon, validate_deck  # noqa: E402
from ptcg.factory import anchor, deckdb  # noqa: E402


def _basics(cards_json: str) -> int:
    """Count Basic Pokemon CARD COPIES in a `decks.cards` JSON blob (a list
    of card ids, one entry per copy -- see `ptcg.factory.builder`)."""
    return sum(1 for cid in json.loads(cards_json) if is_basic_pokemon(cid))


def _validate_anchor_source() -> None:
    """Read + validate `anchor.ANCHOR_DECK_PATH` -- the SAME refusal guard
    `anchor.ensure_anchor_deck` runs (60-card count + `validate_deck` clean),
    duplicated here (not imported -- `ensure_anchor_deck` couples the check
    to its own DB-writing `_apply`) so it can run BEFORE any transaction
    begins, in BOTH dry-run and real modes.

    Fix for Pass-1 review Important finding: without this, a corrupted/
    short `anchor-min8.csv` was only caught by `ensure_anchor_deck` AFTER
    this script's own migration transaction had already COMMITted --
    leaving a fully migrated pool (culled/restored/reset) with NO finalist
    anchor row, discovered only post-hoc. Failing loud here, first, means a
    bad CSV blocks the ENTIRE run (dry-run included) before any DB write.
    """
    cards = [
        int(line)
        for line in anchor.ANCHOR_DECK_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(cards) != 60:
        raise RuntimeError(
            f"migrate_min_basics_pool: {anchor.ANCHOR_DECK_PATH} has {len(cards)} cards, "
            "expected exactly 60 -- refusing to run the migration with a malformed "
            "anchor deck (checked before any transaction begins)"
        )
    problems = validate_deck(cards)
    if problems:
        raise RuntimeError(
            f"migrate_min_basics_pool: {anchor.ANCHOR_DECK_PATH} fails validate_deck: "
            f"{problems} -- refusing to run the migration with a non-compliant anchor "
            "deck (checked before any transaction begins)"
        )


def run_migration(conn: sqlite3.Connection, dry_run: bool) -> dict:
    """Run the migration once. Returns the printed receipt-counts dict.

    Raises `RuntimeError` (leaving the DB untouched) if the games queue is
    not drained -- see the DRAIN GUARD note in the module docstring -- or if
    the anchor source CSV is missing/malformed/non-compliant (checked FIRST,
    in both dry-run and real modes, before any transaction begins).
    """
    _validate_anchor_source()
    # Pre-pass (read-only, NO write lock held): classify every deck. ~95,907
    # rows x 60 DLL-backed dict lookups (the DLL call is cached after the
    # first `is_basic_pokemon` call -- see validate.py's `_card_db`
    # `lru_cache`), a few seconds, deliberately outside the transaction so
    # the write lock is held only for the indexed UPDATEs below.
    to_cull: list[tuple[str, int]] = []  # (concept_id, basics)
    to_restore: list[str] = []
    for r in conn.execute(
        "SELECT d.cards, c.id AS cid, c.status, json_array_length(c.cores) AS arity "
        "FROM decks d JOIN concepts c ON c.id = d.concept_id "
        "WHERE c.status != 'unbuildable'"
    ):
        b = _basics(r["cards"])
        if b < MIN_BASIC_CARDS and r["status"] in ("untested", "active", "finalist"):
            to_cull.append((r["cid"], b))
        elif b >= MIN_BASIC_CARDS and r["status"] == "culled" and r["arity"] == 1:
            to_restore.append(r["cid"])

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn.execute("BEGIN IMMEDIATE")
    try:
        undrained = conn.execute(
            "SELECT COUNT(*) FROM games WHERE status IN ('pending','claimed')"
        ).fetchone()[0]
        if undrained:
            raise RuntimeError(
                f"{undrained} pending/claimed games -- the runner has not drained. "
                "Hold PAUSE, wait for the queue to reach 0, re-run."
            )
        receipts: dict[str, int] = {
            "culled": 0, "restored": 0, "restore_skipped_human_cull": 0,
        }
        for cid, b in to_cull:
            receipts["culled"] += conn.execute(
                "UPDATE concepts SET status='culled', reason=? "
                "WHERE id=? AND status != 'culled'",
                (f"min-basics-rule: {b} basics < {MIN_BASIC_CARDS} (2026-08-11)", cid),
            ).rowcount
        for cid in to_restore:
            last = conn.execute(
                "SELECT action FROM decisions WHERE concept_id=? "
                "ORDER BY id DESC LIMIT 1", (cid,)  # uses ix_decisions_concept
            ).fetchone()
            if last is not None and last["action"] in ("remove", "bulk-remove"):
                receipts["restore_skipped_human_cull"] += 1
                continue
            receipts["restored"] += conn.execute(
                "UPDATE concepts SET status='untested', reason='' "
                "WHERE id=? AND status='culled'", (cid,),
            ).rowcount
        receipts["coverage_reset"] = conn.execute(
            "UPDATE coverage SET games_played=0, distinct_opponents=0, rating=NULL"
        ).rowcount
        receipts["offspring_trashed"] = conn.execute(
            "UPDATE offspring SET status='trashed' WHERE status != 'trashed'"
        ).rowcount
        for table in ("anchor_checks", "floor_checks"):
            receipts[f"{table}_failed"] = conn.execute(
                f"UPDATE {table} SET verdict='fail', resolved_at=? "
                "WHERE verdict='pending'", (now,),
            ).rowcount
        conn.execute("ROLLBACK" if dry_run else "COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    result: dict[str, int | str] = dict(receipts)
    if not dry_run:
        # Separate transaction by design: `deckdb._write` cannot nest inside
        # the `BEGIN IMMEDIATE` above.
        anchor.ensure_anchor_deck(conn)
        result["anchor_installed"] = anchor.ANCHOR_DECK_ID
    for k, v in result.items():
        print(f"migrate_min_basics_pool: {k} = {v}")
    return result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db", required=True,
        help="Path to the tournament DB. Never the production tournament.db in tests.",
    )
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> dict:
    args = _parse_args(argv)
    conn = deckdb.connect(Path(args.db))
    deckdb.init_db(conn)
    return run_migration(conn, args.dry_run)


if __name__ == "__main__":
    main()
