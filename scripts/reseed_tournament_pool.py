"""One-time migration: reseed the tournament pool after the anchor-pressure
redesign collapses the field (spec design 5, `.superpowers/sdd/
2026-08-03-tournament-breeding-anchor-pressure`). Culls -- never deletes --
every old buildable concept (actives AND played-but-unpromoted singles),
preserving provenance, then seeds 3 proven templates plus up to
`MUTATIONS_PER_TEMPLATE` validated mutations each.

LEGACY IN-FLIGHT OFFSPRING (Pass-2 addition, `_reconcile_legacy_offspring`):
the deck pool is not the only pre-slice state that survives go-live. Offspring
rows created BEFORE the floor/netcheck gates existed carry no evidence rows,
so the new gates would either wave them through or skip them permanently. Two
shapes are reconciled, both idempotently and both status-guarded:
  * `'survivor'` with no `floor_checks` row -> `'trashed'` (known-collapsed
    lineage; leaving it costs ~400 games through CROWN + the anchor series
    before it is rejected anyway).
  * `'queued_for_match'` with no `net_checks` row -> back to `'training'`
    (the live `v0.10.1` case), the only status from which
    `netcheck.enqueue_net_check` will actually run for it.
Every other offspring row is left untouched.

Never invoked by any worker -- this is a one-time, idempotent migration run
by hand at the post-merge go-live rung
(`.superpowers/sdd/2026-08-03-tournament-breeding-anchor-pressure/
task-10-brief.md`), and it must NEVER be pointed at a live/shared DB from a
test (tests operate on `tmp_path` copies only).

Idempotency: re-running with the SAME `--seed` culls 0 rows (prior runs'
culled rows no longer match `status IN ('active','untested')`) and inserts 0
new rows (template ids are fixed; mutation ids are content-addressed off the
child deck's card list, so `random.Random(seed)` replaying the identical
sequence of `mutate_deck` calls reproduces the identical mutation set, which
`INSERT OR IGNORE` then no-ops against).

ONE-WAY DOOR (accepted, deliberate -- Brad's decision 2026-08-03 for the
2026-08-16 window): this reseed permanently FREEZES the deck pool to the set
it seeds. There is no mechanism anywhere in the factory that adds a deck
afterwards -- culled concepts are never reactivated, `census.
activate_pair_concepts` can only pair cores that predate the reseed (it
cannot match the synthetic `reseed-`/`reseed-mut-` concept ids), and no
worker authors new decks. Exploration therefore ends here: whatever this
script seeds is the entire deck search space for the rest of the competition
window. Re-opening it means a NEW migration, not a config change.

MUTATION YIELD CEILING (measured 2026-08-03, not a seed artifact):
`breeding.mutate_deck` draws from exactly 4 deterministic rules
(`deck_matrix.MUTATION_RULES`), each a PURE function of the parent deck --
the rng only permutes which rule is tried first. A template can therefore
yield AT MOST 4 distinct children no matter how many attempts are spent.
Measured for these three templates: 3 / 3 / 2 legal children (`attacker-up1`
fails on all three; `energy-down2` additionally fails on density20), i.e. 8
distinct mutations and 11 total decks -- `MUTATIONS_PER_TEMPLATE = 5` and
`MUTATION_ATTEMPTS = 8` are ceilings the operator cannot reach, verified
unchanged at attempt budgets of 8/12/20/40.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ptcg.decks.validate import validate_deck  # noqa: E402
from ptcg.factory import anchor, deckdb  # noqa: E402
from ptcg.factory.breeding import mutate_deck  # noqa: E402
from ptcg.factory.builder import composition_counts  # noqa: E402

RESEED_PREFIX = "reseed-"
RESEED_MUT_PREFIX = f"{RESEED_PREFIX}mut-"
CULL_REASON = "reseed-2026-08-03: collapsed lineage culled (D1-D3 anchor diagnostics)"

TEMPLATES: list[tuple[str, Path]] = [
    (
        f"{RESEED_PREFIX}mega-lucario-fighting",
        ROOT / "src/ptcg/decks/candidates/mega-lucario-fighting.csv",
    ),
    (
        f"{RESEED_PREFIX}mega-starmie-water",
        ROOT / "src/ptcg/decks/candidates/mega-starmie-water.csv",
    ),
    (
        f"{RESEED_PREFIX}mega-starmie-water-density20",
        ROOT / "src/ptcg/decks/candidates/mega-starmie-water-density20.csv",
    ),
]
MUTATIONS_PER_TEMPLATE = 5
MUTATION_ATTEMPTS = 8
DEFAULT_SEED = 20260803


def _read_deck_csv(path: Path) -> list[int]:
    """One card id per line, no header (matches `CURRENT_DECK_PATH`'s own
    reader in `ptcg.factory.anchor.ensure_anchor_deck`)."""
    cards = [
        int(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if len(cards) != 60:
        raise RuntimeError(f"_read_deck_csv: {path} has {len(cards)} cards, expected exactly 60")
    return cards


def _mutation_concept_id(cards: list[int]) -> str:
    """Content-addressed: same card multiset -> same id, so a same-seed
    re-run's identical `mutate_deck` output collides via `INSERT OR IGNORE`
    rather than duplicating."""
    digest = hashlib.sha1(",".join(str(c) for c in sorted(cards)).encode("utf-8")).hexdigest()
    return f"{RESEED_MUT_PREFIX}{digest[:12]}"


def _seed_concept(c, concept_id: str, cards: list[int]) -> bool:
    """Validate (loud raise on failure -- landmark: `validate_deck` is the
    oracle) then `INSERT OR IGNORE` concept + deck + zeroed coverage.
    Returns True iff the concept row was newly inserted this call (the
    concepts INSERT's own rowcount is the idempotency signal `run_reseed`
    reports)."""
    problems = validate_deck(cards)
    if problems:
        raise RuntimeError(f"_seed_concept: {concept_id} failed validate_deck: {problems}")
    cur = c.execute(
        "INSERT OR IGNORE INTO concepts(id, cores, status) VALUES(?, ?, 'untested')",
        (concept_id, json.dumps([concept_id])),
    )
    inserted = cur.rowcount == 1
    en, pk = composition_counts(cards)
    c.execute(
        "INSERT OR IGNORE INTO decks(id, concept_id, cards, shell_variant, "
        "energy_count, pokemon_count) VALUES(?, ?, ?, 0, ?, ?)",
        (f"{concept_id}-sv0", concept_id, json.dumps(cards), en, pk),
    )
    c.execute(
        "INSERT OR IGNORE INTO coverage(concept_id, games_played, distinct_opponents, rating) "
        "VALUES(?, 0, 0, NULL)",
        (concept_id,),
    )
    return inserted


def _reconcile_legacy_offspring(c, log=print) -> dict:
    """Bring PRE-SLICE in-flight offspring into a state the new gates can
    actually validate. Two legacy shapes exist in the live DB at go-live,
    both produced by code that ran BEFORE the floor/netcheck gates existed;
    neither can be retro-validated, and both would otherwise sail straight
    past a gate that was supposed to judge them:

    (a) `status='survivor'` with NO `floor_checks` row -- a candidate that
        reached CONFIRM/CROWN eligibility without ever facing the anchor
        floor. Its lineage is the collapsed one the D1-D3 diagnostics
        condemned, and CROWN is purely relative, so leaving it in place
        spends a full CROWN round-robin plus a 200-game anchor series
        (~400 games) re-discovering that it is junk. Trashed.

    (b) `status='queued_for_match'` with NO `net_checks` row -- an offspring
        that left TRAIN before the validated-net-swap gate existed (the live
        `v0.10.1` case). Reset to `'training'`, which is exactly where
        `enqueue_net_check`'s own `status != 'training'` guard requires it to
        be, so the netcheck runs for it on the next scheduler tick instead of
        being skipped forever.

    Everything else is left untouched -- in particular a `'survivor'` that
    DOES carry a floor row (it was floored under the new rules and is
    legitimately eligible), and any `'training'`/`'matching'`/`'confirming'`/
    `'trashed'` row.

    Idempotent: both UPDATEs are guarded on the status they are moving away
    from AND on the absent-evidence-row condition, so a second run matches
    zero rows. Runs inside the caller's `deckdb._write` transaction (never
    its own -- `_write` cannot nest)."""
    trashed = c.execute(
        "UPDATE offspring SET status='trashed' WHERE status='survivor' "
        "AND id NOT IN (SELECT offspring_id FROM floor_checks)"
    ).rowcount
    reset = c.execute(
        "UPDATE offspring SET status='training' WHERE status='queued_for_match' "
        "AND id NOT IN (SELECT offspring_id FROM net_checks)"
    ).rowcount
    if trashed or reset:
        log(
            f"reseed_tournament_pool: legacy reconcile -- trashed {trashed} "
            f"unfloored survivor(s), reset {reset} un-netchecked "
            "queued_for_match offspring to 'training'"
        )
    return {"survivors_trashed": trashed, "reset_to_training": reset}


def run_reseed(conn, rng: random.Random, log=print) -> dict:
    """Cull the collapsed lineage, reconcile legacy in-flight offspring, then
    seed 3 proven templates + <=15 validated mutations, all inside ONE
    `deckdb._write` transaction after `init_db` + `ensure_anchor_deck` (both
    idempotent, run first and separately -- never nested inside the cull/seed
    transaction). Returns `{"culled": n, "templates_seeded": n,
    "mutations_seeded": n, "survivors_trashed": n, "reset_to_training": n}`.
    """
    deckdb.init_db(conn)
    anchor.ensure_anchor_deck(conn)

    def _apply(c):
        legacy = _reconcile_legacy_offspring(c, log=log)
        # Dead key from the retired round-robin screening design (screening
        # now always plays the FIXED anchor deck, so there is no opponent
        # cursor). Idempotent: a no-op once it is gone / on a virgin DB.
        c.execute("DELETE FROM meta WHERE key='screening_opponent_cursor'")

        cur = c.execute(
            "UPDATE concepts SET status='culled', reason=? "
            "WHERE status IN ('active','untested') "
            "AND id IN (SELECT concept_id FROM coverage) "
            "AND id NOT LIKE ?",
            (CULL_REASON, f"{RESEED_PREFIX}%"),
        )
        culled = cur.rowcount

        templates_seeded = 0
        mutations_seeded = 0
        # Every mutation id produced THIS run, across all templates. A repeat
        # id is a wasted slot (the `INSERT OR IGNORE` would silently no-op and
        # the pool would come up short), so it counts as a FAILED attempt and
        # the slot keeps retrying for a genuinely new child.
        seeded_this_run: set[str] = set()
        for template_id, csv_path in TEMPLATES:
            cards = _read_deck_csv(csv_path)
            if _seed_concept(c, template_id, cards):
                templates_seeded += 1

            for slot in range(MUTATIONS_PER_TEMPLATE):
                child: list[int] | None = None
                mutation_id: str | None = None
                for _attempt in range(MUTATION_ATTEMPTS):
                    candidate = mutate_deck(rng, list(cards))
                    if candidate is None or validate_deck(candidate):
                        continue
                    candidate_id = _mutation_concept_id(candidate)
                    if candidate_id in seeded_this_run:
                        continue  # duplicate child -> failed attempt, retry
                    child, mutation_id = candidate, candidate_id
                    break
                if child is None or mutation_id is None:
                    # Expected on the later slots: the rule set's reachable
                    # child set is exhausted (see the module docstring's
                    # MUTATION YIELD CEILING), not a legality accident.
                    log(
                        f"reseed_tournament_pool: no NEW legal mutation found for "
                        f"{template_id} slot {slot} after {MUTATION_ATTEMPTS} attempts"
                    )
                    continue
                seeded_this_run.add(mutation_id)
                if _seed_concept(c, mutation_id, child):
                    mutations_seeded += 1

        return {
            "culled": culled,
            "templates_seeded": templates_seeded,
            "mutations_seeded": mutations_seeded,
            **legacy,
        }

    result = deckdb._write(conn, _apply)
    log(f"reseed_tournament_pool: {result}")
    return result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db", required=True,
        help="Path to the tournament DB. Never the production tournament.db in tests.",
    )
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> dict:
    args = _parse_args(argv)
    conn = deckdb.connect(Path(args.db))
    return run_reseed(conn, random.Random(args.seed))


if __name__ == "__main__":
    main()
