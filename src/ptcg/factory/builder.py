"""Deterministic concept enumeration + deck builder for the generational
champion tournament (spec Locked Decisions; tournament plan Task 2).

A *concept* is one or two attacker Pokémon names ("cores"). `enumerate_concepts`
lists every single-core concept in the card pool (one per distinct attacker
NAME, deduped, sorted — ~815 as of this dataset: 442 basic + 373 evolved
attackers). `build_deck` is a pure function `Concept -> BuildResult` with zero
randomness: the same concept always produces byte-identical output. It
handles both single-core (`len(cores) == 1`) and two-core (`len(cores) == 2`)
concepts, satisfying engine legality (`validate_deck(...) == []`) AND Brad's
construction bounds (`brad_bounds_problems(...) == []`): <=20 energy cards,
>=8 basic Pokémon, >=4 supporters, >=4 items, exactly 1 ACE SPEC.

Build recipe (single-core): the core's own evolution chain (Basic through the
core's stage, 4 copies per stage — legality caps 4 copies per name), a fixed
trainer skeleton (`TRAINER_SKELETON`, engine-verified real cards from the
confirmed field-best `mega-lucario-fighting` deck: 8 supporters + 9 items
incl. 1 ACE SPEC), cost-matched basic energy for the core's best attack
(`SINGLE_ENERGY_TOTAL` cards, well under the 20-energy cap), and the
REMAINING deck slots padded entirely with filler basic Pokémon (deterministic
cardId order, skipping names already in the deck) — this padding is what
guarantees the >=8 basic Pokémon bound regardless of the core's own chain
length, since a chain's own Basic stage alone only ever contributes up to 4
copies.

Two-core concepts use the same recipe with fewer copies per stage
(`PAIR_COPIES_PER_STAGE`) and a larger shared energy allocation
(`PAIR_ENERGY_TOTAL`) split cost-matched across BOTH cores' attack costs, so
two full evolution lines can coexist within the 60-card / 20-energy budget.
If the combined demands genuinely cannot fit (or an evolution chain / attack
/ energy type can't be resolved), `build_deck` returns a reasoned
`BuildResult(cards=None, unbuildable_reason=...)` — it never raises or
silently drops a card.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from cg.api import Attack, CardData, CardType, EnergyType, all_attack, all_card_data

from ptcg.decks.analysis import pool_summary
from ptcg.decks.validate import MIN_BASIC_CARDS

BUILDER_VERSION: int = 1

#: Fixed trainer package: (card name, copies). Engine-verified real cards
#: (SUPPORTER/ITEM, one ACE SPEC) reused from the confirmed field-best
#: `mega-lucario-fighting` deck (`.claude/rules/verify-game-data-claims.md`).
#: 8 supporters + 9 items (incl. 1 ACE SPEC) = 17 cards total, satisfying
#: Brad's >=4 supporters / >=4 items / ==1 ACE SPEC bounds with a compact
#: footprint that leaves most of the 60-card budget for Pokémon + energy.
TRAINER_SKELETON: list[tuple[str, int]] = [
    ("Cheren", 4),
    ("Judge", 4),
    ("Ultra Ball", 4),
    ("Night Stretcher", 4),
    ("Hyper Aroma", 1),
]

SINGLE_COPIES_PER_STAGE = 4
PAIR_COPIES_PER_STAGE = 3  # "fewer copies each" so two full lines can coexist
SINGLE_ENERGY_TOTAL = 12
PAIR_ENERGY_TOTAL = 16  # shared across both cores' costs, still well under 20
#: Single source of truth is ptcg.decks.validate.MIN_BASIC_CARDS (pool rule,
#: spec 2026-08-11). The local name is kept — every internal use and the
#: unbuildable-reason strings reference MIN_BASIC_POKEMON.
MIN_BASIC_POKEMON = MIN_BASIC_CARDS

#: Used only when an attack's cost is entirely Colorless (payable by ANY
#: basic energy — no dedicated Colorless basic energy card exists in the
#: pool). Falling back to the attacker's own Pokémon `energyType` instead
#: would be wrong: several real types (COLORLESS, DRAGON, RAINBOW,
#: TEAM_ROCKET) have no basic energy card either, and Colorless-cost
#: attacks don't require the attacker's own type anyway. Arbitrary but
#: deterministic and guaranteed to exist (`EnergyType.FIGHTING` is one of
#: the 8 basic-energy-backed types).
DEFAULT_FILLER_ENERGY_TYPE = EnergyType.FIGHTING


@dataclass(frozen=True)
class Concept:
    """One or two attacker Pokémon NAMES ("cores"). `concept_id` is
    order-independent (sorts internally), so callers need not pre-sort."""

    cores: tuple[str, ...]


@dataclass
class BuildResult:
    cards: list[int] | None
    unbuildable_reason: str | None


def concept_id(cores: tuple[str, ...]) -> str:
    """Stable content-addressed id, order-independent over `cores`."""
    digest = hashlib.sha1("|".join(sorted(cores)).encode("utf-8")).hexdigest()
    return "c-" + digest[:12]


@lru_cache(maxsize=1)
def _attacks_by_id() -> dict[int, Attack]:
    return {a.attackId: a for a in all_attack()}


@lru_cache(maxsize=1)
def _card_by_name() -> dict[str, CardData]:
    """Every card in the pool, deduped by name. For each name, prefers the
    lowest-cardId reprint that has >=1 damaging attack over one that
    doesn't — `enumerate_concepts` counts a name as an attacker if ANY
    reprint has a damaging attack, so the chosen representative must be an
    attacking one when one exists, or `build_deck` would wrongly resolve to
    a non-attacking reprint and report "no damaging attack". Falls back to
    lowest cardId when no reprint of that name attacks (trainers/basics)."""
    attacks_by_id = _attacks_by_id()

    def _attacks(card: CardData) -> bool:
        return any(
            (atk := attacks_by_id.get(attack_id)) is not None and atk.damage > 0
            for attack_id in card.attacks
        )

    by_name: dict[str, CardData] = {}
    for c in all_card_data():
        cur = by_name.get(c.name)
        if cur is None or (_attacks(c) and not _attacks(cur)) or (
            _attacks(c) == _attacks(cur) and c.cardId < cur.cardId
        ):
            by_name[c.name] = c
    return by_name


@lru_cache(maxsize=1)
def _filler_basic_order() -> list[CardData]:
    """All basic-Pokémon names in the pool (deduped, lowest cardId per name),
    ordered by cardId ascending — the deterministic fallback fill order."""
    by_name: dict[str, CardData] = {}
    for c in pool_summary().pokemon:
        if not c.basic:
            continue
        if c.name not in by_name or c.cardId < by_name[c.name].cardId:
            by_name[c.name] = c
    return sorted(by_name.values(), key=lambda c: c.cardId)


@lru_cache(maxsize=1)
def _energy_by_type() -> dict[int, CardData]:
    return {
        int(c.energyType): c
        for c in pool_summary().energies
        if c.cardType == CardType.BASIC_ENERGY
    }


def enumerate_concepts() -> list[Concept]:
    """All single-core concepts: one per distinct attacker NAME (a Pokémon
    with >=1 attack whose damage > 0, looked up via the attackId dict — never
    by indexing `all_attack()` directly, per the 1-based/0-based convention),
    deduped by name, sorted for determinism."""
    attacks_by_id = _attacks_by_id()
    by_name: dict[str, list[CardData]] = {}
    for c in pool_summary().pokemon:
        by_name.setdefault(c.name, []).append(c)

    names: list[str] = []
    for name in sorted(by_name):
        cards = by_name[name]
        is_attacker = any(
            (atk := attacks_by_id.get(attack_id)) is not None and atk.damage > 0
            for c in cards
            for attack_id in c.attacks
        )
        if is_attacker:
            names.append(name)
    return [Concept(cores=(name,)) for name in names]


def _best_attack_for(card: CardData) -> Attack | None:
    """Highest damage/cost attack for `card`; ties broken by lowest attackId
    (deterministic — mirrors `pool_summary`'s efficiency ranking)."""
    attacks_by_id = _attacks_by_id()
    best: Attack | None = None
    best_eff = -1.0
    for attack_id in sorted(card.attacks):
        atk = attacks_by_id.get(attack_id)
        if atk is None or atk.damage <= 0:
            continue
        cost = max(1, len(atk.energies))
        eff = atk.damage / cost
        if eff > best_eff:
            best_eff = eff
            best = atk
    return best


def _stage_chain(core_name: str) -> list[CardData] | None:
    """Ordered Basic -> ... -> core evolution chain, deduped by name. `None`
    if any evolvesFrom link can't be resolved in the pool (e.g. a stage that
    evolves from a non-Pokémon Item like an "... Fossil" card)."""
    name_to_card = _card_by_name()
    chain: list[CardData] = []
    seen: set[str] = set()
    cur = core_name
    while True:
        card = name_to_card.get(cur)
        if card is None or cur in seen:
            return None
        seen.add(cur)
        chain.append(card)
        if card.basic:
            break
        if not card.evolvesFrom:
            return None
        cur = card.evolvesFrom
    chain.reverse()
    return chain


def _energy_allocation(types_needed: list[int], total: int) -> dict[int, int]:
    """Deterministic cost-matched split of `total` energy cards across
    `types_needed` (proportional to how often each type appears in the
    attack cost(s)), floor-divided then remainder distributed one-by-one in
    a fixed (count desc, type asc) order — always sums to exactly `total`."""
    counts = Counter(types_needed)
    ordered = sorted(counts, key=lambda t: (-counts[t], t))
    weight_sum = sum(counts.values())
    alloc = {t: (total * counts[t]) // weight_sum for t in ordered}
    remainder = total - sum(alloc.values())
    i = 0
    while remainder > 0:
        alloc[ordered[i % len(ordered)]] += 1
        remainder -= 1
        i += 1
    return alloc


def _deck_energy_plan(
    chains: list[list[CardData]],
) -> tuple[list[int], dict[int, int]] | str:
    """(R2) Computes `(primary_needs, secondary_max)` for `build_deck`'s
    energy allocation, or returns an unbuildable reason string.

    `primary_needs` is unchanged from today: per core (`chain[-1]` is the
    core card, since `_stage_chain` returns Basic..core order), the best
    attack's typed cost (falling back to `DEFAULT_FILLER_ENERGY_TYPE` when
    the best attack is Colorless-only), one list entry accumulated per core.

    `secondary_max[t]` is the R2 correction: the max single-attack count of
    each non-COLORLESS type `t` NOT already in `primary_needs`, seen across
    EVERY card in EVERY chain (all stages, all attacks via `_attacks_by_id`)
    — not just the core's own best attack. Empty when every chain-wide
    attack cost is already covered by `primary_needs` (the mono-payable
    case: R2(d) — energy allocation stays bit-identical to today).

    Cap: `len(primary_types | secondary_types) > 2` is unbuildable (spec
    2-type cap)."""
    colorless = int(EnergyType.COLORLESS)
    attacks_by_id = _attacks_by_id()

    primary_needs: list[int] = []
    for chain in chains:
        core_card = chain[-1]
        best = _best_attack_for(core_card)
        if best is None:
            return f"'{core_card.name}' has no damaging attack"
        needed = [int(e) for e in best.energies if int(e) != colorless]
        primary_needs.extend(needed or [int(DEFAULT_FILLER_ENERGY_TYPE)])

    primary_set = set(primary_needs)
    secondary_max: dict[int, int] = {}
    for chain in chains:
        for card in chain:
            for attack_id in card.attacks:
                atk = attacks_by_id.get(attack_id)
                if atk is None:
                    continue
                counts: dict[int, int] = {}
                for e in atk.energies:
                    t = int(e)
                    if t == colorless or t in primary_set:
                        continue
                    counts[t] = counts.get(t, 0) + 1
                for t, n in counts.items():
                    secondary_max[t] = max(secondary_max.get(t, 0), n)

    n_types = len(primary_set | set(secondary_max))
    if n_types > 2:
        return f"needs {n_types} energy types (cap 2)"
    return primary_needs, secondary_max


def _filler_payable(card: CardData, allowed_types: set[int]) -> bool:
    """(R3) True iff every attack of `card` (an aspiring filler basic) has
    its non-COLORLESS cost types entirely within `allowed_types` — mirrors
    `attack_payability_problems`'s per-attack check (validate.py), scoped to
    one candidate card rather than a whole deck. A Colorless-only-cost
    attack always qualifies (empty required-type set is a subset of any
    `allowed_types`, even the empty set). Unknown attack ids are skipped,
    same convention as `attack_payability_problems`."""
    attacks_by_id = _attacks_by_id()
    colorless = int(EnergyType.COLORLESS)
    return all(
        {int(e) for e in atk.energies if int(e) != colorless} <= allowed_types
        for attack_id in card.attacks
        if (atk := attacks_by_id.get(attack_id)) is not None
    )


def _pad_with_basics(
    cards: list[int], used_names: set[str], target: int, allowed_types: set[int]
) -> bool:
    """Append `target` filler basic-Pokémon copies (deterministic cardId
    order, skipping names already in the deck or failing `_filler_payable`
    against `allowed_types` (R3), <=4 copies per name). Returns False (and
    leaves `cards`/`used_names` unmodified beyond partial progress) if the
    pool ran out of distinct PAYABLE filler names before reaching `target`
    — callers must treat that as unbuildable."""
    remaining = target
    for card in _filler_basic_order():
        if remaining <= 0:
            break
        if card.name in used_names:
            continue
        if not _filler_payable(card, allowed_types):
            continue
        n = min(4, remaining)
        cards.extend([card.cardId] * n)
        used_names.add(card.name)
        remaining -= n
    return remaining <= 0


def build_deck(concept: Concept) -> BuildResult:
    """Pure, deterministic `Concept -> BuildResult`. Handles both
    `len(cores) == 1` and `len(cores) == 2`. Never raises on a legitimately
    unbuildable concept — returns a reasoned `unbuildable_reason` instead."""
    n_cores = len(concept.cores)
    if n_cores == 1:
        copies_per_stage = SINGLE_COPIES_PER_STAGE
        energy_total = SINGLE_ENERGY_TOTAL
    elif n_cores == 2:
        copies_per_stage = PAIR_COPIES_PER_STAGE
        energy_total = PAIR_ENERGY_TOTAL
    else:
        return BuildResult(None, f"unsupported core count {n_cores}")

    chains: list[list[CardData]] = []
    for core_name in concept.cores:
        chain = _stage_chain(core_name)
        if chain is None:
            return BuildResult(None, f"could not resolve evolution chain for '{core_name}'")
        chains.append(chain)

    plan = _deck_energy_plan(chains)
    if isinstance(plan, str):
        return BuildResult(None, plan)
    primary_needs, secondary_max = plan

    cards: list[int] = []
    used_names: set[str] = set()
    basic_count = 0
    for chain in chains:
        for stage_card in chain:
            if stage_card.name in used_names:
                continue  # overlapping chains (shared ancestor) — no double count
            cards.extend([stage_card.cardId] * copies_per_stage)
            used_names.add(stage_card.name)
            if stage_card.basic:
                basic_count += copies_per_stage

    trainers_by_name = _card_by_name()
    for name, n in TRAINER_SKELETON:
        card = trainers_by_name.get(name)
        if card is None:
            return BuildResult(None, f"trainer skeleton card '{name}' missing from pool")
        cards.extend([card.cardId] * n)

    secondary_total = sum(secondary_max.values())
    primary_types = set(primary_needs)
    if energy_total - secondary_total < len(primary_types):
        return BuildResult(
            None,
            f"energy budget {energy_total} too small for {len(primary_types)} primary "
            f"type(s) plus {secondary_total} secondary splash",
        )
    energy_by_type = _energy_by_type()
    alloc = _energy_allocation(primary_needs, energy_total - secondary_total)
    for t, n in secondary_max.items():
        alloc[t] = alloc.get(t, 0) + n  # disjoint by construction: t not in primary_types
    for etype, n in alloc.items():
        if n <= 0:
            continue
        energy_card = energy_by_type.get(etype)
        if energy_card is None:
            return BuildResult(None, f"no basic energy card for type {etype}")
        cards.extend([energy_card.cardId] * n)

    allowed_types = primary_types | set(secondary_max)
    remaining = 60 - len(cards)
    if remaining < 0:
        return BuildResult(None, f"core+trainer+energy already exceeds 60 cards ({len(cards)})")
    need_more_basics = max(0, MIN_BASIC_POKEMON - basic_count)
    if remaining < need_more_basics:
        return BuildResult(
            None,
            f"only {remaining} deck slots remain but {need_more_basics} more basic "
            "Pokémon are needed to satisfy the >=8 basic Pokémon bound",
        )
    if not _pad_with_basics(cards, used_names, remaining, allowed_types):
        return BuildResult(None, "insufficient distinct filler basic Pokémon available")

    if len(cards) != 60:
        return BuildResult(None, f"internal sizing error: built {len(cards)} cards, expected 60")

    cards.sort()
    return BuildResult(cards=cards, unbuildable_reason=None)


def brad_bounds_problems(deck: list[int]) -> list[str]:
    """Construction bounds not covered by `validate_deck`: <=20 energy
    cards, >=8 basic Pokémon, >=4 supporters, >=4 items, exactly 1 ACE SPEC.
    Empty list means no violations."""
    db = {c.cardId: c for c in all_card_data()}
    problems: list[str] = []
    unknown = sorted({cid for cid in deck if cid not in db})
    if unknown:
        return [f"unknown card ids: {unknown}"]

    energy_count = sum(
        1 for cid in deck if db[cid].cardType in (CardType.BASIC_ENERGY, CardType.SPECIAL_ENERGY)
    )
    basic_pokemon_count = sum(
        1 for cid in deck if db[cid].cardType == CardType.POKEMON and db[cid].basic
    )
    supporter_count = sum(1 for cid in deck if db[cid].cardType == CardType.SUPPORTER)
    item_count = sum(1 for cid in deck if db[cid].cardType == CardType.ITEM)
    ace_spec_count = sum(1 for cid in deck if db[cid].aceSpec)

    if energy_count > 20:
        problems.append(f"energy cards {energy_count} > 20")
    if basic_pokemon_count < MIN_BASIC_POKEMON:
        problems.append(f"basic Pokémon {basic_pokemon_count} < {MIN_BASIC_POKEMON}")
    if supporter_count < 4:
        problems.append(f"supporters {supporter_count} < 4")
    if item_count < 4:
        problems.append(f"items {item_count} < 4")
    if ace_spec_count != 1:
        problems.append(f"ACE SPEC count {ace_spec_count} != 1")
    return problems


@lru_cache(maxsize=1)
def _card_by_id() -> dict[int, CardData]:
    """Every card in the pool keyed by cardId. Cached once per process —
    the composition backfill (scripts/migrate_composition_columns.py)
    visits ~95k deck rows and must not rebuild this dict per deck (the
    deck_repair._card_db lru_cache lesson, Pass 2 2026-08-13: 65.6x).
    Scheduler-side/single-threaded consumers only; the UI side keeps its
    own DLL-locked cache in deck_quality.py — do not import this from UI
    code (all_card_data is not thread-safe on a cold cache)."""
    return {c.cardId: c for c in all_card_data()}


def composition_counts(deck: list[int]) -> tuple[int, int]:
    """(energy_count, pokemon_count) for a deck card-id list (spec §1).

    Classification is EXACTLY brad_bounds_problems' (builder.py:431-436):
    energy = CardType.BASIC_ENERGY + CardType.SPECIAL_ENERGY; pokemon =
    CardType.POKEMON (all stages — `basic` is not consulted here). The
    trainer count is derivable (len(deck) - energy - pokemon) and is
    deliberately not returned/stored (spec §1).

    Raises ValueError on unknown card ids: every live insert site inserts
    validated decks, so an unknown id is corruption, not data. The T3
    backfill catches the ValueError and leaves that row's counts NULL —
    an unstamped deck sorts LAST and never jumps the queue (spec §2).
    """
    db = _card_by_id()
    unknown = sorted({cid for cid in deck if cid not in db})
    if unknown:
        raise ValueError(f"composition_counts: unknown card ids {unknown}")
    energy = sum(
        1
        for cid in deck
        if db[cid].cardType in (CardType.BASIC_ENERGY, CardType.SPECIAL_ENERGY)
    )
    pokemon = sum(1 for cid in deck if db[cid].cardType == CardType.POKEMON)
    return energy, pokemon
