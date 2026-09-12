"""Deterministic in-place deck repair (spec 2026-08-12, unpayable-attack pool
rule). `repair_deck` takes a 60-card deck flagged by `attack_payability_
problems` (T1) and returns a REPAIRED deck (preserving the mutated-deck's
identity as much as possible -- swaps/conversions, never a full rebuild) or
`None` when the deck can't be made payable+legal under the algorithm's
conservative bounds, in which case the caller culls it.

Pure and deterministic: no randomness, no DB writes -- reads only the
read-only engine card database (`all_card_data`/`all_attack`), same
convention as `ptcg.decks.validate` and `ptcg.factory.builder`.

Algorithm (see task-3-brief.md for the authoritative spec):
1. Already payable -> no-op.
2. Partition offending Pokemon into SWAPPABLE (a filler basic -- nothing in
   the deck evolves from it) and CHAIN-BOUND (everything else: evolved
   forms, or a basic something else in the deck evolves from).
3. Swappable offenders: replace all copies with equal copies of a payable,
   not-already-present filler basic (basic-for-basic, so MIN_BASIC_CARDS is
   preserved).
4. Chain-bound offenders: compute the missing energy type(s) across their
   attacks (max single-attack count per type). Cap at 2 total energy types;
   convert K copies of the deck's most-common current energy type to the
   single missing type, unless that would starve the deck's highest-damage
   payable attacker below its own primary-attack cost in that type.
5. Re-check payability + full legality; `None` if either still fails.
"""
from __future__ import annotations

from collections import Counter
from functools import lru_cache

from cg.api import Attack, CardData, CardType, EnergyType, all_card_data

from ptcg.decks.validate import attack_payability_problems, validate_deck
from ptcg.factory.builder import (
    _attacks_by_id,
    _energy_by_type,
    _filler_basic_order,
    _filler_payable,
)


@lru_cache(maxsize=1)
def _card_db() -> dict[int, CardData]:
    return {c.cardId: c for c in all_card_data()}


def _deck_energy_types(cards: list[int], db: dict[int, CardData]) -> set[int]:
    return {
        int(db[cid].energyType)
        for cid in cards
        if db[cid].cardType in (CardType.BASIC_ENERGY, CardType.SPECIAL_ENERGY)
    }


def _offending_pokemon(
    cards: list[int],
    db: dict[int, CardData],
    attacks_by_id: dict[int, Attack],
    energy_types: set[int],
) -> dict[int, set[int]]:
    """cardId -> set of missing (non-COLORLESS) energy types, for every
    distinct Pokemon cardId in `cards` with >=1 attack requiring a type
    `energy_types` doesn't cover. Mirrors `attack_payability_problems`'
    per-attack logic (validate.py) but keyed by cardId, not stringified."""
    colorless = int(EnergyType.COLORLESS)
    offenders: dict[int, set[int]] = {}
    for cid in sorted(set(cards)):
        card = db.get(cid)
        if card is None or card.cardType != CardType.POKEMON:
            continue
        missing_for_card: set[int] = set()
        for attack_id in card.attacks:
            atk = attacks_by_id.get(attack_id)
            if atk is None:
                continue
            missing = {int(e) for e in atk.energies if int(e) != colorless} - energy_types
            missing_for_card |= missing
        if missing_for_card:
            offenders[cid] = missing_for_card
    return offenders


def _is_swappable(cid: int, deck_cids: set[int], db: dict[int, CardData]) -> bool:
    """SWAPPABLE iff `cid` is a basic Pokemon AND no other Pokemon present in
    the deck evolves from its name -- i.e. it's a filler, not a chain
    anchor. Everything else (evolved forms, or a basic that something else
    in the deck evolves from) is CHAIN-BOUND."""
    card = db[cid]
    if not card.basic:
        return False
    return not any(
        (other := db.get(other_cid)) is not None and other.evolvesFrom == card.name
        for other_cid in deck_cids
    )


def _best_damage_attack(card: CardData, attacks_by_id: dict[int, Attack]) -> Attack | None:
    """Highest-damage single attack for `card`; ties broken by lowest
    attackId (deterministic, mirrors `builder._best_attack_for`)."""
    best: Attack | None = None
    best_damage = -1
    for attack_id in sorted(card.attacks):
        atk = attacks_by_id.get(attack_id)
        if atk is None or atk.damage <= 0:
            continue
        if atk.damage > best_damage:
            best_damage = atk.damage
            best = atk
    return best


def repair_deck(cards: list[int]) -> tuple[list[int], str] | None:
    """Repair (not rebuild) an unpayable 60-card deck. Returns
    `(repaired_cards, summary)` or `None` when the deck can't be made
    payable+legal (caller culls it)."""
    if attack_payability_problems(cards) == []:
        return cards, "already-payable"

    db = _card_db()
    if any(cid not in db for cid in cards):
        return None  # unknown ids: validate_deck's finding, not this repair's

    attacks_by_id = _attacks_by_id()
    energy_types = _deck_energy_types(cards, db)
    offenders = _offending_pokemon(cards, db, attacks_by_id, energy_types)
    if not offenders:
        # attack_payability_problems flagged the deck but our own (identical
        # logic, different keying) scan found nothing offending -- fail
        # safe rather than silently no-op on a deck we don't understand.
        return None

    deck_cids = set(cards)
    swappable = sorted(cid for cid in offenders if _is_swappable(cid, deck_cids, db))
    chain_bound = sorted(cid for cid in offenders if cid not in swappable)

    repaired = list(cards)
    summary_parts: list[str] = []

    # --- Step 3: swappable offenders -> payable fillers, basic-for-basic ---
    if swappable:
        offender_names = {db[cid].name for cid in swappable}
        used_names = {db[cid].name for cid in repaired} - offender_names
        filler_order = _filler_basic_order()
        for off_cid in swappable:
            count = repaired.count(off_cid)
            replacement = next(
                (
                    candidate
                    for candidate in filler_order
                    if candidate.name not in used_names
                    and _filler_payable(candidate, energy_types)
                ),
                None,
            )
            if replacement is None:
                return None  # pool exhausted of payable, unused fillers
            repaired = [replacement.cardId if c == off_cid else c for c in repaired]
            used_names.add(replacement.name)
            summary_parts.append(
                f"swapped {count}x '{db[off_cid].name}' -> '{replacement.name}'"
            )

    # --- Step 4: chain-bound offenders -> single energy-type conversion ---
    if chain_bound:
        colorless = int(EnergyType.COLORLESS)
        missing_types: set[int] = set()
        type_max_count: dict[int, int] = {}
        for cid in chain_bound:
            card = db[cid]
            for attack_id in card.attacks:
                atk = attacks_by_id.get(attack_id)
                if atk is None:
                    continue
                per_attack_counts: dict[int, int] = {}
                for e in atk.energies:
                    t = int(e)
                    if t == colorless:
                        continue
                    per_attack_counts[t] = per_attack_counts.get(t, 0) + 1
                for t, n in per_attack_counts.items():
                    if t in energy_types:
                        continue
                    missing_types.add(t)
                    type_max_count[t] = max(type_max_count.get(t, 0), n)

        if len(energy_types | missing_types) > 2:
            return None
        if len(missing_types) != 1:
            # 0: a chain-bound offender with no resolvable single-type
            # splash (shouldn't happen given `offenders` is non-empty);
            # >1: only reachable when `energy_types` started empty, which
            # this single-conversion step can't resolve either.
            return None
        new_type = next(iter(missing_types))
        k = type_max_count[new_type]

        energy_counts = Counter(
            int(db[cid].energyType)
            for cid in repaired
            if db[cid].cardType in (CardType.BASIC_ENERGY, CardType.SPECIAL_ENERGY)
        )
        if not energy_counts:
            return None
        # Deterministic tie-break: highest count wins; ties go to the
        # lowest energy-type value.
        most_common_type = max(energy_counts, key=lambda t: (energy_counts[t], -t))
        new_count = energy_counts[most_common_type] - k
        if new_count < 0:
            return None

        payable_pokemon_cids = {
            cid
            for cid in set(repaired)
            if db[cid].cardType == CardType.POKEMON and cid not in chain_bound
        }
        best_attacker_cid = None
        best_damage = -1
        for cid in sorted(payable_pokemon_cids):
            atk = _best_damage_attack(db[cid], attacks_by_id)
            if atk is not None and atk.damage > best_damage:
                best_damage = atk.damage
                best_attacker_cid = cid
        primary_cost = 0
        if best_attacker_cid is not None:
            best_atk = _best_damage_attack(db[best_attacker_cid], attacks_by_id)
            if best_atk is not None:
                primary_cost = sum(1 for e in best_atk.energies if int(e) == most_common_type)
        if new_count < primary_cost:
            return None  # would starve the deck's highest-damage attacker

        new_energy_card = _energy_by_type().get(new_type)
        if new_energy_card is None:
            return None  # no basic energy card for the missing type
        energy_card_types = (CardType.BASIC_ENERGY, CardType.SPECIAL_ENERGY)

        def _is_most_common_energy(c: int) -> bool:
            card = db[c]
            return card.cardType in energy_card_types and int(card.energyType) == most_common_type

        converted = 0
        next_repaired: list[int] = []
        old_type_name = next(db[c].name for c in repaired if _is_most_common_energy(c))
        for c in repaired:
            if converted < k and _is_most_common_energy(c):
                next_repaired.append(new_energy_card.cardId)
                converted += 1
            else:
                next_repaired.append(c)
        repaired = next_repaired
        summary_parts.append(
            f"converted {k}x '{old_type_name}' -> '{new_energy_card.name}'"
        )

    if attack_payability_problems(repaired) != [] or validate_deck(repaired) != []:
        return None

    summary = "; ".join(summary_parts) if summary_parts else "already-payable"
    return repaired, summary
