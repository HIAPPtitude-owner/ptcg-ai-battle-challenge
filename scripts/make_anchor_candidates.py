"""Author the 4 anchor candidate decks for the min-basics-pool-rule mini-
tournament (spec 2026-08-11, Task 5).

Deterministic construction — re-running this script from the same ladder CSV
and card database always produces byte-identical output:

  A (anchor-cand-a-lucario-min8): the ladder deck (`mega-lucario-fighting.csv`)
    minus 4 copies of card id 6 (Basic {F} Energy) plus 4 copies of a SECOND
    basic Pokemon line, chosen deterministically as: among cards with
    cardType == POKEMON and basic == True whose best damaging attack
    (`builder._best_attack_for`) has non-Colorless energy costs that are a
    subset of {FIGHTING}, the one with the highest HP (ties broken by lowest
    cardId).
  B (anchor-cand-b-mega-starmie): build_deck(Concept(cores=("Mega Starmie ex",)))
  C (anchor-cand-c-palafin):      build_deck(Concept(cores=("Palafin ex",)))
  D (anchor-cand-d-tinkaton):     build_deck(Concept(cores=("Tinkaton",)))

B/C/D fall back deterministically to the next buildable, type-distinct row
of the card-pool report's top-attackers table if the preferred core is
unbuildable (`BuildResult.cards is None`) — see `_build_with_fallback`.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cg.api import CardData, CardType, EnergyType, all_card_data  # noqa: E402

from ptcg.decks.analysis import pool_summary  # noqa: E402
from ptcg.decks.validate import MIN_BASIC_CARDS, is_basic_pokemon, validate_deck  # noqa: E402
from ptcg.factory.builder import Concept, _best_attack_for, build_deck  # noqa: E402

LADDER_CSV = ROOT / "src" / "ptcg" / "decks" / "candidates" / "mega-lucario-fighting.csv"
OUT_DIR = ROOT / "src" / "ptcg" / "decks" / "candidates"

#: Basic {F} Energy, verified against the engine card DB (not the CSV) —
#: `.claude/rules/verify-game-data-claims.md`.
BASIC_F_ENERGY_ID = 6

CANDIDATE_B_PREFERRED = "Mega Starmie ex"
CANDIDATE_C_PREFERRED = "Palafin ex"
CANDIDATE_D_PREFERRED = "Tinkaton"


def _load_ladder_deck() -> list[int]:
    cards = [int(x) for x in LADDER_CSV.read_text(encoding="utf-8").split()]
    if len(cards) != 60:
        raise RuntimeError(f"ladder CSV has {len(cards)} cards, expected 60")
    n_energy = sum(1 for c in cards if c == BASIC_F_ENERGY_ID)
    if n_energy != 26:
        raise RuntimeError(
            f"ladder CSV has {n_energy} copies of card {BASIC_F_ENERGY_ID}, expected 26 "
            "(composition drift vs the brief's verified count)"
        )
    return cards


def _pick_second_basic_line(db: dict[int, CardData]) -> CardData:
    """Max-HP Basic Pokemon whose best damaging attack's non-Colorless
    energy costs are a subset of {FIGHTING}; ties broken by lowest cardId."""
    candidates: list[CardData] = []
    for card in db.values():
        if card.cardType != CardType.POKEMON or not card.basic:
            continue
        best = _best_attack_for(card)
        if best is None:
            continue
        needed = {int(e) for e in best.energies if int(e) != int(EnergyType.COLORLESS)}
        if needed - {int(EnergyType.FIGHTING)}:
            continue  # some non-Colorless cost outside {FIGHTING}
        candidates.append(card)
    if not candidates:
        raise RuntimeError("no basic Pokemon satisfies the Candidate-A energy-cost criterion")
    candidates.sort(key=lambda c: (-c.hp, c.cardId))
    return candidates[0]


def build_candidate_a(db: dict[int, CardData]) -> tuple[list[int], CardData]:
    ladder = _load_ladder_deck()
    chosen = _pick_second_basic_line(db)

    new_cards: list[int] = []
    removed = 0
    for cid in ladder:
        if cid == BASIC_F_ENERGY_ID and removed < 4:
            removed += 1
            continue
        new_cards.append(cid)
    if removed != 4:
        raise RuntimeError(f"only removed {removed}/4 copies of card {BASIC_F_ENERGY_ID}")

    # Insert the 4 new basics right after the existing Pokemon block so the
    # file reads as Pokemon -> trainers -> energy, same shape as the ladder CSV.
    insert_idx = max(i for i, cid in enumerate(new_cards) if db[cid].cardType == CardType.POKEMON) + 1
    new_cards[insert_idx:insert_idx] = [chosen.cardId] * 4

    if len(new_cards) != 60:
        raise RuntimeError(f"candidate A has {len(new_cards)} cards, expected 60")
    return new_cards, chosen


def _build_with_fallback(label: str, preferred_name: str, exclude_types: set[int]) -> tuple[list[int], str]:
    """build_deck(preferred_name); on failure, walk the card-pool report's
    top-attackers table (highest damage/energy first) for the next buildable
    core whose type is not in `exclude_types`, printing the substitution."""
    result = build_deck(Concept(cores=(preferred_name,)))
    if result.cards is not None:
        return result.cards, preferred_name

    print(
        f"WARNING: candidate {label} preferred core '{preferred_name}' is unbuildable "
        f"({result.unbuildable_reason}) - falling back to the next top-attacker row."
    )
    seen: set[str] = {preferred_name}
    for card, _atk, _eff in pool_summary().attackers_ranked:
        if card.name in seen:
            continue
        seen.add(card.name)
        if int(card.energyType) in exclude_types:
            continue
        fallback_result = build_deck(Concept(cores=(card.name,)))
        if fallback_result.cards is not None:
            print(
                f"  SUBSTITUTED: candidate {label} now uses '{card.name}' "
                f"(type {int(card.energyType)}) instead of '{preferred_name}'."
            )
            return fallback_result.cards, card.name
    raise RuntimeError(f"candidate {label}: no buildable, type-distinct substitute found for '{preferred_name}'")


def _core_energy_type(name: str, db: dict[int, CardData]) -> int:
    for card in db.values():
        if card.name == name:
            return int(card.energyType)
    raise RuntimeError(f"core '{name}' not found in card DB")


def _print_composition(label: str, path: Path, cards: list[int], db: dict[int, CardData]) -> None:
    pokes = sum(1 for c in cards if db[c].cardType == CardType.POKEMON)
    energy = sum(1 for c in cards if db[c].cardType in (CardType.BASIC_ENERGY, CardType.SPECIAL_ENERGY))
    trainers = len(cards) - pokes - energy
    basics = sum(1 for c in cards if is_basic_pokemon(c))
    problems = validate_deck(cards)
    print(f"--- {label} -> {path.name} ---")
    print(f"  60 cards: {pokes} Pokemon, {trainers} trainers, {energy} energy")
    print(f"  basics: {basics} (MIN_BASIC_CARDS={MIN_BASIC_CARDS})")
    print(f"  validate_deck: {problems if problems else 'CLEAN'}")
    if problems or basics < MIN_BASIC_CARDS:
        raise RuntimeError(f"candidate {label} failed validation: {problems}, basics={basics}")


def main() -> None:
    db = {c.cardId: c for c in all_card_data()}

    cards_a, chosen_a = build_candidate_a(db)
    cards_b, name_b = _build_with_fallback("B", CANDIDATE_B_PREFERRED, exclude_types=set())
    cards_c, name_c = _build_with_fallback("C", CANDIDATE_C_PREFERRED, exclude_types=set())
    types_bc = {_core_energy_type(name_b, db), _core_energy_type(name_c, db)}
    cards_d, name_d = _build_with_fallback("D", CANDIDATE_D_PREFERRED, exclude_types=types_bc)

    print(f"Candidate A second basic line: {chosen_a.name} (id {chosen_a.cardId}, HP {chosen_a.hp})")
    print(f"Candidate B core: {name_b}")
    print(f"Candidate C core: {name_c}")
    print(f"Candidate D core: {name_d}")

    outputs = [
        ("A", OUT_DIR / "anchor-cand-a-lucario-min8.csv", cards_a),
        ("B", OUT_DIR / "anchor-cand-b-mega-starmie.csv", cards_b),
        ("C", OUT_DIR / "anchor-cand-c-palafin.csv", cards_c),
        ("D", OUT_DIR / "anchor-cand-d-tinkaton.csv", cards_d),
    ]
    for label, path, cards in outputs:
        _print_composition(label, path, cards, db)
        path.write_text("\n".join(str(c) for c in cards) + "\n", encoding="utf-8")
        print(f"  wrote {path}")


if __name__ == "__main__":
    main()
