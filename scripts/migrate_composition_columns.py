"""One-shot composition-columns migration for the EXISTING production
tournament DB (spec §1, go-live step 1). Adds nullable INTEGER columns
`energy_count`/`pokemon_count` to `decks` (idempotent — PRAGMA
table_info checked first), backfills every NULL row from the deck's own
cards via builder.composition_counts, and creates the supporting index
`ix_decks_concept_comp` — all inside ONE `BEGIN IMMEDIATE` transaction
(single read-decide-act, .claude/rules/single-actor-worker-tests.md).

Deliberately NOT wired into deckdb.init_db(): the watch loop calls
init_db every ~15 minutes; the backfill runs exactly once, at go-live,
with the runner/scheduler tasks held (see the plan's Go-Live section).

Unknown-card-id decks are left NULL (counted in the receipt): NULL sorts
LAST in the census ORDER BY, so an unstampable deck never jumps the
queue. Exit is nonzero if null_remaining != skipped_unknown_ids (an
unexplained NULL means the backfill did not do its job — fail loud).

Usage:
    uv run python scripts/migrate_composition_columns.py --db <path> [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ptcg.factory import deckdb  # noqa: E402
from ptcg.factory.builder import composition_counts  # noqa: E402

_IX_SQL = (
    "CREATE INDEX IF NOT EXISTS ix_decks_concept_comp "
    "ON decks(concept_id, shell_variant, energy_count, pokemon_count)"
)


def run_migration(conn, dry_run: bool = False) -> dict:
    """Held-lock sizing (fix round 1, spec §1 review): `cards` is immutable
    input, so the SELECT + json.loads + composition_counts pass is pure
    read/compute and does NOT need the write lock. Measured on a live-DB
    snapshot at real production N (140,318 decks): that pass alone took
    13.24s: holding it inside BEGIN IMMEDIATE left only ~9s of headroom
    against the runner/scheduler workers' 30s busy_timeout (the
    factory-db-lock-contention failure class, see
    .claude/rules/single-actor-worker-tests.md's access-path-analysis
    clause). Precomputing the (deck_id -> energy_count, pokemon_count)
    mapping BEFORE opening the transaction, then doing only the guarded
    ALTERs + a single batched `executemany` UPDATE + index creation
    inside BEGIN IMMEDIATE, sizes the held lock at ~5.87s instead.
    """
    # Outside the write transaction (no lock held): decide which rows need
    # backfill and compute their counts. If the columns don't exist yet,
    # every row needs it; if they do, only current NULL rows do.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(decks)")}
    has_cols = "energy_count" in cols and "pokemon_count" in cols
    if has_cols:
        rows = conn.execute(
            "SELECT id, cards FROM decks "
            "WHERE energy_count IS NULL OR pokemon_count IS NULL"
        ).fetchall()
    else:
        rows = conn.execute("SELECT id, cards FROM decks").fetchall()

    updates: list[tuple[int, int, str]] = []
    skipped_unknown = 0
    for row in rows:
        try:
            en, pk = composition_counts(json.loads(row["cards"]))
        except ValueError:
            skipped_unknown += 1  # stays NULL: sorts last, never jumps queue
            continue
        updates.append((en, pk, row["id"]))

    conn.execute("BEGIN IMMEDIATE")
    try:
        cols_now = {r["name"] for r in conn.execute("PRAGMA table_info(decks)")}
        added: list[str] = []
        for col in ("energy_count", "pokemon_count"):
            if col not in cols_now:
                conn.execute(f"ALTER TABLE decks ADD COLUMN {col} INTEGER")
                added.append(col)
        conn.executemany(
            "UPDATE decks SET energy_count=?, pokemon_count=? WHERE id=?",
            updates,
        )
        conn.execute(_IX_SQL)
        null_remaining = conn.execute(
            "SELECT COUNT(*) FROM decks "
            "WHERE energy_count IS NULL OR pokemon_count IS NULL"
        ).fetchone()[0]
        result = {
            "columns_added": added,
            "rows_needing_backfill": len(rows),
            "backfilled": len(updates),
            "skipped_unknown_ids": skipped_unknown,
            "null_remaining": null_remaining,
            "dry_run": dry_run,
        }
        conn.execute("ROLLBACK" if dry_run else "COMMIT")
        return result
    except BaseException:
        conn.execute("ROLLBACK")
        raise


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
    result = run_migration(conn, args.dry_run)
    print(json.dumps(result, indent=2))
    if result["null_remaining"] != result["skipped_unknown_ids"]:
        raise SystemExit(1)  # unexplained NULLs — fail loud, never exit 0
    return result


if __name__ == "__main__":
    main()
