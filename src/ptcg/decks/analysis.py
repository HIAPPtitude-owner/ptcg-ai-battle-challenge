"""Card-pool analysis over the engine's own card database.

The engine (all_card_data / all_attack) is the source of truth for what is
playable; EN_Card_Data.csv adds human-readable effect text for the report.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from cg.api import Attack, CardData, CardType, all_attack, all_card_data


@dataclass
class PoolSummary:
    total_cards: int
    pokemon: list[CardData] = field(default_factory=list)
    trainers: list[CardData] = field(default_factory=list)
    energies: list[CardData] = field(default_factory=list)
    evolution_lines: dict[str, list[CardData]] = field(default_factory=dict)
    attackers_ranked: list[tuple[CardData, Attack, float]] = field(default_factory=list)


def _evolution_lines(pokemon: list[CardData]) -> dict[str, list[CardData]]:
    """Group Pokémon into lines keyed by the basic stage's name."""
    by_name: dict[str, list[CardData]] = {}
    for c in pokemon:
        by_name.setdefault(c.name, []).append(c)

    def base_of(card: CardData, seen: frozenset[str] = frozenset()) -> str:
        if card.basic or not card.evolvesFrom or card.name in seen:
            return card.name
        parents = by_name.get(card.evolvesFrom)
        if not parents:
            return card.evolvesFrom
        return base_of(parents[0], seen | {card.name})

    lines: dict[str, list[CardData]] = {}
    for c in pokemon:
        lines.setdefault(base_of(c), []).append(c)

    # A handful of Pokémon (e.g. Lileep, Tirtouga, Archen, Amaura, Tyrunt)
    # evolve from an "Antique ... Fossil" card, which is a Trainer Item, not
    # a Pokémon in this pool. base_of() falls back to that Item's name for
    # such cards, producing a group with no Basic Pokémon member. These are
    # not usable evolution lines for deck construction (there is no
    # in-pool Basic to build the line from), so drop groups lacking a Basic.
    return {name: members for name, members in lines.items()
            if any(c.basic for c in members)}


@lru_cache(maxsize=1)
def _dbs() -> tuple[list[CardData], dict[int, Attack]]:
    return all_card_data(), {a.attackId: a for a in all_attack()}


def pool_summary() -> PoolSummary:
    cards, attacks = _dbs()
    pokemon = [c for c in cards if c.cardType == CardType.POKEMON]
    energies = [c for c in cards
                if c.cardType in (CardType.BASIC_ENERGY, CardType.SPECIAL_ENERGY)]
    trainers = [c for c in cards
                if c.cardType in (CardType.ITEM, CardType.TOOL,
                                  CardType.SUPPORTER, CardType.STADIUM)]
    attackers: list[tuple[CardData, Attack, float]] = []
    for c in pokemon:
        for attack_id in c.attacks:
            atk = attacks.get(attack_id)
            if atk and atk.damage > 0:
                cost = max(1, len(atk.energies))
                attackers.append((c, atk, atk.damage / cost))
    attackers.sort(key=lambda t: t[2], reverse=True)
    return PoolSummary(
        total_cards=len(cards), pokemon=pokemon, trainers=trainers,
        energies=energies, evolution_lines=_evolution_lines(pokemon),
        attackers_ranked=attackers,
    )


def print_report(top_n: int = 25) -> str:
    s = pool_summary()
    lines = [
        "# Card Pool Report",
        f"- Total cards: {s.total_cards} "
        f"(Pokémon {len(s.pokemon)}, Trainers {len(s.trainers)}, Energy {len(s.energies)})",
        f"- Evolution lines: {len(s.evolution_lines)}",
        "",
        "## Top attackers (damage per energy)",
        "| Pokémon | HP | Type | Attack | Dmg | Cost | Dmg/energy | ex/Mega |",
        "|---------|----|------|--------|-----|------|-----------|---------|",
    ]
    for c, a, eff in s.attackers_ranked[:top_n]:
        badge = "Mega" if c.megaEx else ("ex" if c.ex else "")
        lines.append(f"| {c.name} | {c.hp} | {c.energyType} | {a.name} "
                     f"| {a.damage} | {len(a.energies)} | {eff:.0f} | {badge} |")
    return "\n".join(lines)


if __name__ == "__main__":
    print(print_report())
