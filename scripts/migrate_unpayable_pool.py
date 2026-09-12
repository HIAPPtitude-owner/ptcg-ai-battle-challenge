"""One-shot migration: enforce the unpayable-attack pool rule (`spec
2026-08-12, `.superpowers/sdd/2026-08-12-unpayable-attack-pool-rule`)
against every buildable, non-`culled` deck in the tournament pool --
REPAIR (not rebuild) every deck `attack_payability_problems` flags via
`deck_repair.repair_deck`, or cull the concept when a deck can't be made
payable+legal under the repair algorithm's conservative bounds.

Semantics:
  * Scope: every `decks` row whose concept is not `culled` (any other
    status -- `untested`/`active`/`unbuildable`/`finalist`).
  * EXCLUSIONS (never touched, counted in receipts, checked in this
    priority order so a deck is never double-counted): deck ids referenced
    by ANY `baselines` row (pair-gate evictee reconstruction depends on
    these surviving byte-identical -- see `.claude/rules/
    platform-mechanics-model.md`); the anchor concept's decks
    (`concept_id == anchor.ANCHOR_CONCEPT_ID`); `finalist` concepts' decks.
  * Non-excluded, already-payable decks (`attack_payability_problems(cards)
    == []`): untouched, counted `already_payable`.
  * Non-excluded, violating decks: `deck_repair.repair_deck(cards)` ->
    on success, `UPDATE decks SET cards=? WHERE id=?` (SAME id -- repair,
    never rebuild) + a coverage reset scoped to that deck's concept (zero
    `games_played`/`distinct_opponents`, `rating` -> NULL -- same column
    semantics as `migrate_min_basics_pool`'s coverage reset, scoped per-
    concept here rather than globally since only the repaired concepts'
    decks actually changed); on `None`, cull the concept
    (`reason='unpayable-rule: <iso>'`, status-guarded, idempotent).
  * `chain_delta`: of the violating decks, how many have at least one
    CHAIN-BOUND offender (an evolved form, or a basic something else in
    the deck evolves from -- never a swappable filler), per
    `deck_repair`'s own offender classification. Quantifies the
    core-only-measurement gap: a core-only flag would only ever see the
    swappable-filler case, never the chain-bound case that needs an actual
    energy-type conversion.

All the compute-heavy classification (`attack_payability_problems`,
`repair_deck` -- both pure, deterministic, DLL-backed but read-only) runs
in a READ-ONLY pre-pass OUTSIDE the write lock, mirroring
`migrate_min_basics_pool.py:126-184`. The write lock (ONE `BEGIN IMMEDIATE`)
is held only for the DRAIN GUARD re-check, the EXCLUSION re-verification,
and the guarded UPDATEs themselves.

DRAIN GUARD: identical convention and query shape to
`migrate_min_basics_pool.py` -- the runner pool has no PAUSE check of its
own, so this script must never run concurrently with in-flight games. A
FAST, non-authoritative pre-flight check runs BEFORE the (potentially
long, DLL-backed) pre-pass, so an obviously-undrained queue aborts in
seconds rather than after the full pre-pass has run. The AUTHORITATIVE
check re-runs the same query INSIDE the transaction (closing the TOCTOU
window the fast check can't): if the queue is not drained at that point,
the transaction raises and rolls back WITHOUT writing anything. Operator
protocol: hold `experiments/factory/PAUSE`, wait for the queue to reach 0,
then run this script.

STALE-EXCLUSION RE-VERIFICATION (fix round 1, review finding): the
pre-pass's `baselines`/anchor/finalist exclusion snapshot can go stale
during the pre-pass's own (potentially long) DLL-backed classification
work -- a concurrent writer can INSERT a new `baselines` row, or promote a
concept to `finalist`/`culled`, between the pre-pass read and this
script's own write. Before applying any planned repair/cull, EVERY
`to_repair`/`to_cull` entry is re-checked against a FRESH read of the
exclusion criteria taken INSIDE the transaction; any entry that has become
excluded since the pre-pass is skipped and counted `excluded_late` rather
than acted on -- this is what keeps a baselines-referenced deck
byte-identical even under this race, not just in the common case.

`--dry-run` runs the full pre-pass + transaction, then ROLLBACKs instead of
COMMITting -- the DB is left byte-identical. The post-commit canary
(read-only re-count of the payability flag over the `active` pool) only
runs for a real (non-dry-run) commit.

Idempotency: a second run finds `violating=0` -- repaired decks now pass
`attack_payability_problems` (reclassified `already_payable`), and culled
concepts are excluded from the pre-pass scan entirely (`WHERE status !=
'culled'`), so `scanned` also drops. No separate guard clauses are needed
for the `decks.cards` UPDATE beyond that natural reclassification; the
concept cull UPDATE is still status-guarded (`AND status != 'culled'`) for
safety against a concept with more than one deck.

`coverage_reset` is counted PER CONCEPT (deduplicated), not per repaired
deck row -- a concept with more than one repaired deck resets its coverage
once, not once per deck.

Never invoked by any worker -- this is a one-time, by-hand migration at
the post-merge go-live rung. Tests operate on `tmp_path` DBs only -- NEVER
the live `experiments/factory/tournament.db` from a test run.
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

from ptcg.decks.validate import attack_payability_problems  # noqa: E402
from ptcg.factory import anchor, deck_repair, deckdb  # noqa: E402

_RECEIPT_KEYS = (
    "scanned", "violating", "repaired", "culled_unrepairable", "coverage_reset",
    "excluded_baselines", "excluded_anchor", "excluded_finalist", "already_payable",
    "chain_delta", "excluded_late",
)


def _chain_bound(cards: list[int]) -> bool:
    """True if `cards` (a payability-VIOLATING deck) has >=1 CHAIN-BOUND
    offender per `deck_repair`'s own classification -- an evolved form, or
    a basic something else in the deck evolves from, never a swappable
    filler. Reuses `deck_repair`'s private classification helpers directly
    (the same convention `deck_repair` itself uses for `builder`'s private
    helpers) rather than re-deriving the offender-partition logic; this is
    a receipt-only measurement (`chain_delta`), not part of the repair
    algorithm itself."""
    db = deck_repair._card_db()
    if any(cid not in db for cid in cards):
        return False
    attacks_by_id = deck_repair._attacks_by_id()
    energy_types = deck_repair._deck_energy_types(cards, db)
    offenders = deck_repair._offending_pokemon(cards, db, attacks_by_id, energy_types)
    deck_cids = set(cards)
    return any(not deck_repair._is_swappable(cid, deck_cids, db) for cid in offenders)


def _undrained_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM games WHERE status IN ('pending','claimed')"
    ).fetchone()[0]


def run_migration(conn: sqlite3.Connection, dry_run: bool) -> dict:
    """Run the migration once. Returns the printed receipt-counts dict.

    Raises `RuntimeError` (leaving the DB untouched) if the games queue is
    not drained -- see the DRAIN GUARD note in the module docstring.
    """
    # Fast, non-authoritative pre-flight: abort in seconds rather than
    # after the potentially-long pre-pass below if the queue is obviously
    # not drained. The in-txn check further down is the AUTHORITATIVE one.
    undrained = _undrained_count(conn)
    if undrained:
        raise RuntimeError(
            f"{undrained} pending/claimed games -- the runner has not drained. "
            "Hold PAUSE, wait for the queue to reach 0, re-run."
        )

    # Pre-pass (read-only, NO write lock held): classify every non-culled
    # deck. Deliberately outside the transaction so the write lock is held
    # only for the guarded UPDATEs below.
    baseline_deck_ids = {r[0] for r in conn.execute("SELECT deck_id FROM baselines")}
    to_repair: list[tuple[str, str, list[int]]] = []  # (deck_id, concept_id, new_cards)
    to_cull: list[tuple[str, str]] = []  # (concept_id, deck_id)
    receipts: dict[str, int] = {k: 0 for k in _RECEIPT_KEYS}

    for r in conn.execute(
        "SELECT d.id AS deck_id, d.cards AS cards, d.concept_id AS concept_id, "
        "c.status AS status FROM decks d JOIN concepts c ON c.id = d.concept_id "
        "WHERE c.status != 'culled'"
    ):
        receipts["scanned"] += 1
        if r["deck_id"] in baseline_deck_ids:
            receipts["excluded_baselines"] += 1
            continue
        if r["concept_id"] == anchor.ANCHOR_CONCEPT_ID:
            receipts["excluded_anchor"] += 1
            continue
        if r["status"] == "finalist":
            receipts["excluded_finalist"] += 1
            continue

        cards = json.loads(r["cards"])
        if attack_payability_problems(cards) == []:
            receipts["already_payable"] += 1
            continue

        receipts["violating"] += 1
        if _chain_bound(cards):
            receipts["chain_delta"] += 1
        result = deck_repair.repair_deck(cards)
        if result is None:
            to_cull.append((r["concept_id"], r["deck_id"]))
        else:
            repaired_cards, _summary = result
            to_repair.append((r["deck_id"], r["concept_id"], repaired_cards))

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn.execute("BEGIN IMMEDIATE")
    try:
        undrained = _undrained_count(conn)  # AUTHORITATIVE re-check, closes the race window
        if undrained:
            raise RuntimeError(
                f"{undrained} pending/claimed games -- the runner has not drained. "
                "Hold PAUSE, wait for the queue to reach 0, re-run."
            )

        # Re-verify exclusions INSIDE the lock (fix round 1, review
        # finding): a concurrent writer could have inserted a new
        # `baselines` row, or promoted/culled a concept, during the
        # pre-pass above. `fresh_baseline_ids` is re-read here, not reused
        # from the pre-pass snapshot.
        fresh_baseline_ids = {r[0] for r in conn.execute("SELECT deck_id FROM baselines")}

        def _now_excluded(concept_id: str, deck_id: str) -> bool:
            if deck_id in fresh_baseline_ids:
                return True
            if concept_id == anchor.ANCHOR_CONCEPT_ID:
                return True
            row = conn.execute(
                "SELECT status FROM concepts WHERE id=?", (concept_id,)
            ).fetchone()
            return row is None or row["status"] in ("culled", "finalist")

        live_to_repair = [
            entry for entry in to_repair if not _now_excluded(entry[1], entry[0])
        ]
        receipts["excluded_late"] += len(to_repair) - len(live_to_repair)
        live_to_cull = [
            entry for entry in to_cull if not _now_excluded(entry[0], entry[1])
        ]
        receipts["excluded_late"] += len(to_cull) - len(live_to_cull)

        coverage_concepts: set[str] = set()
        for deck_id, cid, new_cards in live_to_repair:
            conn.execute(
                "UPDATE decks SET cards=? WHERE id=?", (json.dumps(new_cards), deck_id),
            )
            receipts["repaired"] += 1
            coverage_concepts.add(cid)
        for cid in coverage_concepts:
            receipts["coverage_reset"] += conn.execute(
                "UPDATE coverage SET games_played=0, distinct_opponents=0, rating=NULL "
                "WHERE concept_id=?", (cid,),
            ).rowcount
        for cid, _deck_id in live_to_cull:
            receipts["culled_unrepairable"] += conn.execute(
                "UPDATE concepts SET status='culled', reason=? "
                "WHERE id=? AND status != 'culled'",
                (f"unpayable-rule: {now}", cid),
            ).rowcount
        conn.execute("ROLLBACK" if dry_run else "COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    if not dry_run:
        # Canary (post-commit, read-only): recount the payability flag over
        # the `active` pool only -- excluded (baseline/anchor/finalist) rows
        # are reported separately above and deliberately not part of this
        # recount.
        active_rows = conn.execute(
            "SELECT d.cards AS cards FROM decks d JOIN concepts c ON c.id = d.concept_id "
            "WHERE c.status = 'active'"
        ).fetchall()
        canary_violating = sum(
            1 for row in active_rows
            if attack_payability_problems(json.loads(row["cards"])) != []
        )
        print(f"migrate_unpayable_pool: canary_violating_active = {canary_violating}")

    for k, v in receipts.items():
        print(f"migrate_unpayable_pool: {k} = {v}")
    return receipts


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
