"""CLI: one-shot founder-population seeding for the evolutionary agent
population (Task 8, evolutionary-agent-population slice).

Reads the pinned incumbent (`gate.incumbent()` -- the exact same "beat-this
bar" selector `gate.decide()`/`evolution.evo_gate_step` already use, per
`evolution._incumbent_anchor_cell`'s own precedent) out of `candidates.json`
and the curated seed decks out of `deck_matrix.SEEDS`, then writes the two
founder genome pools (`agent_pool.json` / `deck_pool.json`) the evolutionary
tournament tick (evolution.py Task 5) plays forever after.

This is a ONE-SHOT operation: re-seeding a live, already-rated population
would silently fork its lineage, so the script refuses outright (loud exit
1, NO writes) if EITHER pool file already exists -- both files are checked
BEFORE either is written, so a refusal can never leave one pool seeded and
the other not.

Founder agent pool (~`evolution.TARGET_AGENTS`, usually 12):
  1. one **anchor**: kind="heuristic", config={} (scale bridge + fast games).
  2. one **anchor**: the pinned incumbent's agent_kind/agent_config VERBATIM.
  3. `TARGET_AGENTS - 2` **live** search genomes: the incumbent's config
     completed to the full GENE_SPEC/CATEGORICAL_GENES surface (missing
     genes filled with the documented SearchConfig defaults), plus jitters
     of that completed config via `breeding.mutate_agent` seeded
     `random.Random(1)..Random(9)`.
  A collision between any two of the above (most notably: the incumbent IS
  heuristic, so its verbatim config equals the heuristic anchor's `{}`) is
  resolved by a single keep-first dedup pass over the whole ordered list --
  see `_dedup_agents_keep_first`.

Founder deck pool (~`evolution.TARGET_DECKS`, usually 12): every distinct
non-retired candidate's deck (by content hash), ranked by the best
`matrix_rating` among candidates sharing that deck; the incumbent's own
deck is forced first and flagged **anchor**. Each deck's `net_weights` is
carried over from its best-rated candidate's `agent_config["net_weights"]`
ONLY when that file actually exists on disk. Remaining slots (if fewer than
`TARGET_DECKS` distinct decks exist) are filled from `deck_matrix.SEEDS`.

`--dry-run` builds and prints the full roster (ids, kind/status, notes) but
writes NOTHING. Because a dry-run flag means the real (write) path is
untested by dry-run invocations alone, the real path here is exercised
directly by tests/test_seed_evolution_pools.py against tmp paths, not just
previewed -- see `.claude/rules/` on dry-run-only testing gaps.
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ptcg.arena.runner import load_deck  # noqa: E402
from ptcg.factory.breeding import mutate_agent  # noqa: E402
from ptcg.factory.candidates import Candidate, load_ledger  # noqa: E402
from ptcg.factory.cycle import FactoryPaths  # noqa: E402
from ptcg.factory.deck_matrix import SEEDS, deck_hash  # noqa: E402
from ptcg.factory.evolution import (  # noqa: E402
    TARGET_AGENTS, TARGET_DECKS, _agent_pool_path, _deck_pool_path,
)
from ptcg.factory.gate import incumbent as gate_incumbent  # noqa: E402
from ptcg.factory.genomes import (  # noqa: E402
    AgentGenome, CATEGORICAL_GENES, DeckGenome, GENE_SPEC,
    agent_genome_id, deck_genome_id, save_pool,
)
from ptcg.factory.tournament import active_pool  # noqa: E402

# SearchConfig defaults (brief's founder-roster note) for every GENE_SPEC +
# CATEGORICAL_GENES key. Must cover exactly those keys -- checked below so a
# future GENE_SPEC change fails loudly here instead of silently seeding
# incomplete configs.
DEFAULT_GENE_VALUES: dict = {
    "search_budget_ms": 200,
    "rollout_depth": 12,
    "deviate_min_visits": 20,
    "deviate_value_edge": 0.12,
    "c_uct": 1.4,
    "c_puct": 1.5,
    "prior_tau": 0.1,
    "max_depth": 40,
    "robust_min_visits": 5,
    "deviate_min_visit_frac": 0.0,
    "final_move_rule": "most_visited",
    "use_root_prior": False,
}
assert set(DEFAULT_GENE_VALUES) == set(GENE_SPEC) | set(CATEGORICAL_GENES), (
    "DEFAULT_GENE_VALUES drifted from genomes.GENE_SPEC/CATEGORICAL_GENES")


class AlreadySeededError(RuntimeError):
    """Raised when either pool file already exists -- seeding is one-shot."""


def complete_gene_surface(config: dict) -> dict:
    """Fill any GENE_SPEC/CATEGORICAL_GENES key missing from `config` with
    its documented SearchConfig default, leaving every key already present
    in `config` (including non-gene keys such as `net_weights`) untouched."""
    out = dict(config)
    for key, default in DEFAULT_GENE_VALUES.items():
        out.setdefault(key, default)
    return out


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _agent_kind_for(candidate_agent_kind: str) -> str:
    """candidates.py's `Candidate.agent_kind` is "heuristic" | "search-net";
    genomes.py's `AgentGenome.kind` is "search" | "heuristic" -- the reverse
    of `evolution.cell_candidate_view`'s own mapping."""
    return "heuristic" if candidate_agent_kind == "heuristic" else "search"


def _dedup_agents_keep_first(genomes: list) -> list:
    """Keep-first dedup by agent_genome_id: a genome built earlier in the
    list always survives a collision with anything built later, so an
    anchor can never be silently displaced by a later-built search genome.
    Handles the brief's named case (the incumbent IS heuristic, so its
    verbatim `{}` config collides with the heuristic anchor's `{}`) plus any
    other exact-config coincidence generically."""
    seen: set = set()
    out = []
    for g in genomes:
        if g.id in seen:
            continue
        seen.add(g.id)
        out.append(g)
    return out


def _build_agent_pool(inc: Candidate, *, born_at: str) -> list:
    heuristic_anchor = AgentGenome(
        id=agent_genome_id({}), kind="heuristic", config={}, status="anchor",
        born_at=born_at, notes="founder: heuristic anchor (scale bridge)")

    # `net_weights` is a DECK-side gene (see DeckGenome/`cell_candidate_view`,
    # which only injects it back in when the CELL's deck carries one) -- an
    # agent_config that retains a deck-specific `net_weights` verbatim would
    # silently follow every founder agent genome onto every deck it's later
    # paired with, including netless decks, where `cell_candidate_view` never
    # overrides it and a wrong-deck ValueNetEvaluator gets loaded. Strip it
    # before building ANY agent genome.
    inc_config = dict(inc.agent_config)
    inc_config.pop("net_weights", None)
    incumbent_anchor = AgentGenome(
        id=agent_genome_id(inc_config), kind=_agent_kind_for(inc.agent_kind),
        config=inc_config, status="anchor", born_at=born_at,
        notes=f"founder: incumbent agent anchor (from candidate {inc.id!r})")

    completed = complete_gene_surface(inc_config)
    n_search = TARGET_AGENTS - 2  # e.g. 10: 1 base (completed) + 9 jitters
    search_genomes = [AgentGenome(
        id=agent_genome_id(completed), kind="search", config=completed,
        status="live", lineage=[incumbent_anchor.id], seed=0, born_at=born_at,
        notes="founder: incumbent config completed to full gene surface")]
    for i in range(1, n_search):
        child_config = mutate_agent(random.Random(i), completed)
        search_genomes.append(AgentGenome(
            id=agent_genome_id(child_config), kind="search", config=child_config,
            status="live", lineage=[incumbent_anchor.id], seed=i, born_at=born_at,
            notes=f"founder: mutate_agent jitter seed={i}"))

    ordered = [heuristic_anchor, incumbent_anchor] + search_genomes
    return _dedup_agents_keep_first(ordered)


def _rank_active_decks(candidates: list, root: Path) -> list:
    """Group non-retired candidates by deck content hash; return
    [(deck_hash, {"cards", "csv", "best", "rating"}), ...] sorted descending
    by the group's best matrix_rating (None -> -inf), ties broken by
    deck_hash for determinism. Candidates whose deck csv fails to load
    (missing/malformed file) are skipped -- mirrors evolution.py's
    `_incumbent_anchor_cell` OSError/ValueError tolerance."""
    groups: dict = {}
    for c in active_pool(candidates):
        try:
            cards = load_deck(root / c.deck)
        except (OSError, ValueError):
            continue
        h = deck_hash(cards)
        rating = c.matrix_rating if c.matrix_rating is not None else float("-inf")
        group = groups.get(h)
        if group is None or rating > group["rating"]:
            groups[h] = {"cards": cards, "csv": c.deck, "best": c, "rating": rating}
    return sorted(groups.items(), key=lambda kv: (-kv[1]["rating"], kv[0]))


def _build_deck_pool(inc: Candidate, candidates: list, root: Path,
                     *, born_at: str) -> list:
    ranked = _rank_active_decks(candidates, root)
    by_hash = dict(ranked)

    try:
        inc_cards = load_deck(root / inc.deck)
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"cannot load incumbent deck csv {inc.deck!r}: {exc}") from exc
    inc_hash = deck_hash(inc_cards)
    inc_group = by_hash.get(inc_hash) or {
        "cards": inc_cards, "csv": inc.deck, "best": inc, "rating": float("-inf")}

    ordered: list = [(inc_hash, inc_group, True)]
    seen = {inc_hash}
    for h, g in ranked:
        if len(ordered) >= TARGET_DECKS:
            break
        if h in seen:
            continue
        ordered.append((h, g, False))
        seen.add(h)

    if len(ordered) < TARGET_DECKS:
        for csv_path, _prio in SEEDS:
            if len(ordered) >= TARGET_DECKS:
                break
            seed_abs = root / csv_path
            if not seed_abs.exists():
                continue
            cards = load_deck(seed_abs)
            h = deck_hash(cards)
            if h in seen:
                continue
            ordered.append((h, {"cards": cards, "csv": csv_path, "best": None}, False))
            seen.add(h)

    decks = []
    for h, g, is_anchor in ordered:
        best = g.get("best")
        net_weights = None
        if best is not None and isinstance(best.agent_config, dict):
            nw = best.agent_config.get("net_weights")
            if nw and (root / nw).exists():
                net_weights = nw
        if is_anchor:
            note = "founder: incumbent deck anchor"
        elif best is not None:
            note = "founder: ranked active-candidate deck"
        else:
            note = "founder: deck_matrix.SEEDS fill"
        decks.append(DeckGenome(
            id=deck_genome_id(g["cards"]), cards=g["cards"], csv=g["csv"],
            net_weights=net_weights, status="anchor" if is_anchor else "live",
            born_at=born_at, notes=note))
    return decks


def seed_pools(paths: FactoryPaths, *, ledger_path: Path | None = None,
               dry_run: bool = False, root: Path | None = None,
               log=print) -> dict:
    """Build (and, unless `dry_run`, write) the founder `agent_pool.json` /
    `deck_pool.json` under `paths.root`'s `experiments/factory/` directory.

    `ledger_path` defaults to `paths.ledger` (the real CLI path: the ledger
    lives alongside the pool files) but is independently overridable so
    tests can point `paths` at a wholly virgin output directory while
    reading a candidates.json fixture from elsewhere.

    `root` is the REPO root used to resolve every repo-relative path this
    function reads (candidate deck csvs, net_weights, deck_matrix.SEEDS
    csvs) -- distinct from `paths.root` for the same reason, mirroring
    `evolution._incumbent_anchor_cell`'s own separate `evaluate.ROOT`
    import. Defaults to this script's own repo root (the real CLI path).
    """
    root = Path(root) if root is not None else ROOT
    ledger_path = Path(ledger_path) if ledger_path is not None else paths.ledger

    agent_pool_path = _agent_pool_path(paths)
    deck_pool_path = _deck_pool_path(paths)
    if agent_pool_path.exists() or deck_pool_path.exists():
        existing = agent_pool_path if agent_pool_path.exists() else deck_pool_path
        raise AlreadySeededError(
            f"refusing to seed: {existing} already exists -- seeding is "
            "one-shot; delete both pool files manually to re-seed")

    candidates = load_ledger(ledger_path)
    inc = gate_incumbent(candidates)
    if inc is None:
        raise RuntimeError(
            "no incumbent found via gate.incumbent() -- cannot found the "
            "population without a pinned/counted beat-this-bar candidate")

    born_at = _now_iso()
    agents = _build_agent_pool(inc, born_at=born_at)
    decks = _build_deck_pool(inc, candidates, root, born_at=born_at)

    for g in agents:
        log(f"  agent {g.id} kind={g.kind} status={g.status} :: {g.notes}")
    for g in decks:
        log(f"  deck  {g.id} status={g.status} :: {g.notes}")

    if dry_run:
        log(f"DRY RUN: would write {len(agents)} agents, {len(decks)} decks "
            "-- no files written")
    else:
        save_pool(agent_pool_path, agents)
        save_pool(deck_pool_path, decks)
        log(f"wrote {agent_pool_path} ({len(agents)} agents), "
            f"{deck_pool_path} ({len(decks)} decks)")

    return {"agents": agents, "decks": decks, "incumbent": inc}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="print the founder roster; write nothing")
    p.add_argument("--root", type=Path, default=ROOT,
                   help="repo root override (testing only)")
    args = p.parse_args(argv)

    paths = FactoryPaths(root=args.root)
    try:
        seed_pools(paths, dry_run=args.dry_run, root=args.root)
    except (AlreadySeededError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
