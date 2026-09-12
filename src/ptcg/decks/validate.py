"""Deck legality: engine card DB is the source of truth."""
from __future__ import annotations

from collections import Counter
from functools import lru_cache

from cg.api import Attack, CardData, CardType, EnergyType, all_attack, all_card_data

#: Pool rule (spec 2026-08-11): every deck must run at least this many basic
#: Pokémon CARD COPIES (basic energy does not count). Single source of truth —
#: builder.MIN_BASIC_POKEMON and deck_quality's mulligan-risk flag import this.
MIN_BASIC_CARDS = 8


@lru_cache(maxsize=1)
def _card_db() -> dict[int, CardData]:
    return {c.cardId: c for c in all_card_data()}


def is_basic_pokemon(card_id: int) -> bool:
    """True if card_id is a Basic-stage Pokémon (distinct from basic energy)."""
    db = _card_db()
    card = db.get(card_id)
    return card is not None and card.cardType == CardType.POKEMON and card.basic


@lru_cache(maxsize=1)
def _attack_db() -> dict[int, Attack]:
    """1-based attackId -> Attack (never index all_attack() directly)."""
    return {a.attackId: a for a in all_attack()}


def attack_payability_problems(deck: list[int]) -> list[str]:
    """Strict type-coverage payability (spec 2026-08-12): every non-COLORLESS
    cost type of every attack of every Pokémon must be a deck energy type.
    Quantity deliberately out of scope — mirrors deck_quality's flag."""
    db = _card_db()
    if any(cid not in db for cid in deck):
        return []  # unknown ids are validate_deck's own finding, not ours
    attacks = _attack_db()
    colorless = int(EnergyType.COLORLESS)
    energy_types = {
        int(db[cid].energyType)
        for cid in deck
        if db[cid].cardType in (CardType.BASIC_ENERGY, CardType.SPECIAL_ENERGY)
    }
    problems: list[str] = []
    for cid in sorted(set(deck)):
        card = db[cid]
        if card.cardType != CardType.POKEMON:
            continue
        for attack_id in card.attacks:
            atk = attacks.get(attack_id)
            if atk is None:
                continue  # mirror deck_quality: unknown attack ids skipped
            missing = {int(e) for e in atk.energies if int(e) != colorless} - energy_types
            if missing:
                problems.append(
                    f"'{card.name}' attack {attack_id} needs energy types "
                    f"{sorted(missing)} not provided by deck energy {sorted(energy_types)}"
                )
    return problems


def validate_deck(deck: list[int]) -> list[str]:
    problems: list[str] = []
    db = _card_db()
    if len(deck) != 60:
        problems.append(f"deck must have exactly 60 cards, has {len(deck)}")
    unknown = sorted({cid for cid in deck if cid not in db})
    if unknown:
        problems.append(f"unknown card ids: {unknown}")
        return problems  # further checks need valid ids
    name_counts = Counter(
        db[cid].name for cid in deck if db[cid].cardType != CardType.BASIC_ENERGY
    )
    for name, count in sorted(name_counts.items()):
        if count > 4:
            problems.append(f"more than 4 copies of '{name}' ({count})")
    if not any(is_basic_pokemon(cid) for cid in deck):
        problems.append("deck has no Basic Pokémon")
    basics = sum(1 for cid in deck if is_basic_pokemon(cid))
    if basics < MIN_BASIC_CARDS:
        problems.append(f"fewer than {MIN_BASIC_CARDS} Basic Pokémon cards ({basics})")
    ace_specs = sum(1 for cid in deck if db[cid].aceSpec)
    if ace_specs > 1:
        problems.append(f"more than 1 ACE SPEC card ({ace_specs})")
    problems.extend(attack_payability_problems(deck))
    return problems
