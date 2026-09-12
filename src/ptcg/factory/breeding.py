"""Mutation + crossover breeding operators for both evolutionary populations
(search-agent hyperparameter genomes and 60-card deck genomes).

Every function here is deterministic given a seeded `random.Random` instance
(never the bare `random` module) - reproducibility across breeding runs
depends on callers threading the same rng object through.

Deck-side legality is never assumed: `crossover_deck`/`mutate_deck`/
`breed_deck` all defer final acceptance to `ptcg.decks.validate.validate_deck`
(the oracle), retrying or falling back to `None` rather than ever returning
an illegal deck.
"""
from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
from typing import Union

from cg.api import CardType

from ptcg.factory.deck_matrix import MUTATION_RULES, _card_db, apply_rule, write_deck_csv
from ptcg.factory.genomes import (
    AgentGenome,
    CATEGORICAL_GENES,
    DeckGenome,
    GENE_SPEC,
    agent_genome_id,
    deck_genome_id,
)

MUTATE_P_NUMERIC = 0.35      # per-gene chance of jitter
MUTATE_P_CATEGORICAL = 0.15  # per-gene chance of flip
CROSSOVER_RETRIES = 8        # deck crossover legality retries before fallback

# Repo root: src/ptcg/factory/breeding.py -> factory -> ptcg -> src -> ROOT.
# Same depth/convention as deck_matrix.ROOT.
_ROOT = Path(__file__).resolve().parents[3]


def _repo_rel(p: Path) -> str:
    """Repo-relative posix path when possible, else the absolute path
    unchanged (tests write outside ROOT via tmp_path). Mirrors
    deck_matrix._rel's fallback semantics."""
    p = Path(p)
    if p.is_absolute():
        try:
            p = p.relative_to(_ROOT)
        except ValueError:
            pass
    return p.as_posix()


def _best_rated(genomes: list) -> DeckGenome:
    """Highest-`rating` genome; `rating is None` sorts as worst (never
    picked over any genome that has a real rating).

    Typed as `DeckGenome` (not `AgentGenome | DeckGenome`) because every
    call site in this module passes deck genomes and immediately reads a
    deck-only attribute (`.net_weights`, `.cards`) off the result."""
    return max(
        genomes,
        key=lambda g: (g.rating is not None, g.rating if g.rating is not None else float("-inf")),
    )


def _pick_net_weights(parents: list) -> Union[str, None]:
    """net_weights inherited from the higher-rated parent that HAS one;
    None if no parent has net_weights set."""
    with_weights = [p for p in parents if p.net_weights]
    if not with_weights:
        return None
    return _best_rated(with_weights).net_weights


# --- Agent-genome operators --------------------------------------------


def mutate_agent(rng, config: dict) -> dict:
    child = dict(config)
    for gene, (lo, hi, sigma, cast) in GENE_SPEC.items():
        if rng.random() < MUTATE_P_NUMERIC:
            child[gene] = cast(min(hi, max(lo, child.get(gene, lo) + rng.gauss(0, sigma))))
    for gene, choices in CATEGORICAL_GENES.items():
        if rng.random() < MUTATE_P_CATEGORICAL:
            child[gene] = rng.choice([c for c in choices if c != child.get(gene, choices[0])])
    return child


def crossover_agent(rng, pa: dict, pb: dict) -> dict:
    """Uniform per-gene inheritance: for each gene independently, inherit
    from a coin-flip-chosen parent that HAS the key; if only one parent has
    it, inherit from that one; if NEITHER has it, fall back to GENE_SPEC's
    lo bound (numeric genes) or the first categorical choice. Then apply
    mutate_agent on top.

    NOTE: the brief's illustrative one-line dict-comprehension for this
    function always picks parent A (its `if True else pb` short-circuits),
    so it is written here as the explicit loop the brief's own note
    prescribes - see .claude/rules/plan-test-arithmetic-sanity.md.
    """
    keys = list(GENE_SPEC) + list(CATEGORICAL_GENES)
    child: dict = {}
    for key in keys:
        has_a = key in pa
        has_b = key in pb
        if has_a and has_b:
            child[key] = pa[key] if rng.random() < 0.5 else pb[key]
        elif has_a:
            child[key] = pa[key]
        elif has_b:
            child[key] = pb[key]
        elif key in GENE_SPEC:
            child[key] = GENE_SPEC[key][0]  # lo
        else:
            child[key] = CATEGORICAL_GENES[key][0]  # first choice
    return mutate_agent(rng, child)


def breed_agent(rng, parents: list) -> AgentGenome:
    if not parents:
        raise ValueError("breed_agent requires at least one parent")
    seed = rng.getrandbits(32)
    if len(parents) >= 2:
        child_config = crossover_agent(rng, parents[0].config, parents[1].config)
    else:
        child_config = mutate_agent(rng, parents[0].config)
    return AgentGenome(
        id=agent_genome_id(child_config),
        kind="search",
        config=child_config,
        lineage=[p.id for p in parents],
        seed=seed,
    )


# --- Deck-genome operators ----------------------------------------------


def crossover_deck(rng, cards_a: list, cards_b: list) -> Union[list, None]:
    from ptcg.decks.validate import validate_deck  # lazy import: mirrors
    # deck_matrix.apply_rule's local-import pattern.
    from ptcg.factory.deck_repair import repair_deck  # lazy import, same
    # convention; deck_repair.repair_deck is T3's deterministic energy-plan
    # repair primitive (spec 2026-08-12, unpayable-attack-pool-rule) --
    # applied here (Task 5) because a recombined-from-two-decks child
    # frequently inherits Pokemon from BOTH parents' type lines without
    # inheriting a payable energy split, and plain retry-the-shuffle
    # (CROSSOVER_RETRIES) does not fix that -- verified via a real
    # measurement (crossover_deck alone: 63/200 pre-fix vs baseline 200/200
    # with payability monkeypatched out; see task-5-report.md).

    db = _card_db()
    for _attempt in range(CROSSOVER_RETRIES):
        pool = list(cards_a) + list(cards_b)
        rng.shuffle(pool)
        child: list = []
        name_counts: Counter = Counter()
        ace_specs = 0
        for cid in pool:
            if len(child) >= 60:
                break
            card = db.get(cid)
            if card is None:
                continue
            is_energy = card.cardType == CardType.BASIC_ENERGY
            if not is_energy and name_counts[card.name] >= 4:
                continue
            if card.aceSpec and ace_specs >= 1:
                continue
            child.append(cid)
            if not is_energy:
                name_counts[card.name] += 1
            if card.aceSpec:
                ace_specs += 1

        if len(child) < 60:
            # Walk exhausted before reaching 60: top up with the most-common
            # basic energy id in the parents (ties -> lowest id, matching
            # deck_matrix's _top/_ranked tie-break convention).
            energy_counts: Counter = Counter()
            for cid in pool:
                card = db.get(cid)
                if card is not None and card.cardType == CardType.BASIC_ENERGY:
                    energy_counts[cid] += 1
            if energy_counts:
                top_energy_id = sorted(energy_counts.items(), key=lambda t: (-t[1], t[0]))[0][0]
                while len(child) < 60:
                    child.append(top_energy_id)

        if len(child) == 60:
            if validate_deck(child) == []:
                return child
            repaired = repair_deck(child)
            # repair_deck's early-exit "already-payable" branch returns the
            # INPUT cards unchanged (payable-but-otherwise-illegal is left
            # for the caller -- deck_repair.py only re-checks full legality
            # on the branch that actually did repair work), so a
            # payability-clean-but-basics-short (or name-cap-violating)
            # candidate can come back through unchanged and still be
            # illegal. Never trust repair_deck's non-None return alone --
            # re-run validate_deck as the final oracle, same as every other
            # acceptance point in this module (found via a real
            # crossover_deck repro during Task 5 measurement; see
            # task-5-report.md).
            if repaired is not None and validate_deck(repaired[0]) == []:
                return repaired[0]
    return None


def mutate_deck(rng, cards: list) -> Union[list, None]:
    rule_names = list(MUTATION_RULES)
    first = rng.choice(rule_names)
    result = apply_rule(cards, first)
    if result is not None:
        return result
    remaining = [name for name in rule_names if name != first]
    rng.shuffle(remaining)
    for name in remaining:
        result = apply_rule(cards, name)
        if result is not None:
            return result
    return None


def breed_deck(rng, parents: list, out_dir: Path) -> Union[DeckGenome, None]:
    """Produce one child deck via a 2-step fallback chain: try the primary
    op (crossover with probability 0.7 given 2 parents, else mutate) and,
    if it fails legality, try the OTHER op once; return None if both fail.

    There is deliberately no 3rd step re-trying the first op again:
    `mutate_deck` already exhausts every rule in `MUTATION_RULES` (trying
    the whole shuffled remainder after the first pick fails), so a second
    call to `_do_mutate()` on the same `best.cards` with the same rule set
    is guaranteed to return the identical None it returned the first time
    - it is not a fresh chance, just a repeat of an already-failed search.
    """
    if not parents:
        return None
    seed = rng.getrandbits(32)
    best = _best_rated(parents)
    two_parents = len(parents) >= 2

    def _do_crossover():
        if not two_parents:
            return None
        return crossover_deck(rng, parents[0].cards, parents[1].cards)

    def _do_mutate():
        return mutate_deck(rng, list(best.cards))

    use_crossover = two_parents and rng.random() < 0.7
    if use_crossover:
        cards = _do_crossover()
        if cards is None:
            cards = _do_mutate()  # fall back to the other op
    else:
        cards = _do_mutate()
        if cards is None:
            cards = _do_crossover()  # fall back to the other op

    if cards is None:
        return None

    deck_id = deck_genome_id(cards)
    csv_path = Path(out_dir) / f"evolved-{deck_id}.csv"
    write_deck_csv(csv_path, cards)

    return DeckGenome(
        id=deck_id,
        cards=cards,
        csv=_repo_rel(csv_path),
        net_weights=_pick_net_weights(parents),
        lineage=[p.id for p in parents],
        seed=seed,
    )


# --- Parent selection -----------------------------------------------------


def select_parents(rng, pool: list, k: int = 2, top_frac: float = 0.25) -> list:
    live = [g for g in pool if g.status == "live"]
    if not live:
        return []
    ranked = sorted(
        live,
        key=lambda g: (g.rating is None, -(g.rating if g.rating is not None else 0.0)),
    )
    head_size = max(2, math.ceil(top_frac * len(live)))
    head = ranked[:head_size]
    k = min(k, len(head))
    return rng.sample(head, k)
