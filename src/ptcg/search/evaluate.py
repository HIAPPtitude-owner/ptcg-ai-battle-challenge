"""Hand-tuned leaf evaluator. Replaced wholesale by the Slice-4 value net.

v2 adds KO-awareness machinery (whether each side's active can KO the other's
active with a currently-usable attack, weighted by who moves next), verified
against the engine card/attack DB. Task-9 tuning measured W_KO=0.25 at parity-
to-slightly-below the v1 weights (E7+E9 pooled 48% vs E5+E6 pooled 54%, n=100
each, difference not significant), so the DEFAULT keeps v1 weights (W_KO=0.0);
the tested _ko_term/_cost_satisfied helpers are the Slice-4 value-net seed.
Card/attack DBs load lazily and degrade to a zero KO-term if unavailable.
"""
from __future__ import annotations

from collections import Counter

from cg.api import (Attack, CardData, EnergyType, PlayerState, Pokemon, State,
                    all_attack, all_card_data)

_W_PRIZE, _W_KO, _W_DMG, _W_DEV, _W_HAND, _W_DECK = 0.5, 0.0, 0.2, 0.15, 0.1, 0.05

_CARDS: dict[int, CardData] | None = None
_ATTACKS: dict[int, Attack] | None = None


def _dbs() -> tuple[dict[int, CardData], dict[int, Attack]]:
    global _CARDS, _ATTACKS
    if _CARDS is None or _ATTACKS is None:
        try:
            _CARDS = {c.cardId: c for c in all_card_data()}
            _ATTACKS = {a.attackId: a for a in all_attack()}
        except Exception:  # noqa: BLE001 — engine DB unavailable: degrade, don't crash
            _CARDS, _ATTACKS = {}, {}
    return _CARDS, _ATTACKS


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _board(p: PlayerState) -> list:
    return [pk for pk in list(p.active) + list(p.bench) if pk is not None]


def _damage_frac(p: PlayerState) -> float:
    board = _board(p)
    if not board:
        return 0.0
    return sum((pk.maxHp - pk.hp) / max(pk.maxHp, 1) for pk in board) / len(board)


def _development(p: PlayerState) -> float:
    return sum(len(pk.energies) for pk in _board(p)) + len(p.bench)


def _active(p: PlayerState) -> Pokemon | None:
    return p.active[0] if p.active and p.active[0] is not None else None


def _cost_satisfied(cost: list, attached: list) -> bool:
    """True if `attached` energy types can pay `cost`. COLORLESS slots accept any
    energy; each RAINBOW pays exactly ONE specific-type shortfall (shared budget,
    not per-type — the v1 bug double-counted it across types)."""
    if len(attached) < len(cost):
        return False
    have = Counter(attached)
    need = Counter(cost)
    rainbow_left = have.get(EnergyType.RAINBOW, 0)
    if EnergyType.RAINBOW in need:
        rainbow_left -= need[EnergyType.RAINBOW]
        if rainbow_left < 0:
            return False
    for etype, n in need.items():
        if etype in (EnergyType.COLORLESS, EnergyType.RAINBOW):
            continue
        short = n - have.get(etype, 0)
        if short > 0:
            rainbow_left -= short
            if rainbow_left < 0:
                return False
    # Typed requirements consume exactly sum(specific needs) distinct energies;
    # the top length check guarantees enough remain for the COLORLESS slots.
    return True


def _best_usable_damage(pk: Pokemon | None,
                        cards: dict[int, CardData], attacks: dict[int, Attack]) -> int:
    if pk is None:
        return 0
    card = cards.get(pk.id)
    if card is None:
        return 0
    best = 0
    for atk_id in card.attacks:
        atk = attacks.get(atk_id)
        if atk is not None and atk.damage > best \
                and _cost_satisfied(atk.energies, pk.energies):
            best = atk.damage
    return best


def _ko_term(state: State, my_index: int,
             cards: dict[int, CardData], attacks: dict[int, Attack]) -> float:
    """[-1, 1]: + when my active can KO theirs with a usable attack (decisive
    if I move next), - when theirs can KO mine (decisive if they move next)."""
    me, opp = state.players[my_index], state.players[1 - my_index]
    my_active, opp_active = _active(me), _active(opp)
    my_dmg = _best_usable_damage(my_active, cards, attacks)
    opp_dmg = _best_usable_damage(opp_active, cards, attacks)
    i_move = state.yourIndex == my_index
    ko = 0.0
    if opp_active is not None and opp_active.hp > 0 and my_dmg >= opp_active.hp:
        ko += 1.0 if i_move else 0.5
    if my_active is not None and my_active.hp > 0 and opp_dmg >= my_active.hp:
        ko -= 1.0 if not i_move else 0.5
    return _clamp(ko)


def evaluate(state: State, my_index: int) -> float:
    """Value of `state` for player `my_index`, in [0, 1]."""
    if state.result == my_index:
        return 1.0
    if state.result == 1 - my_index:
        return 0.0
    if state.result != -1:
        return 0.5  # draw or unknown future result code
    me, opp = state.players[my_index], state.players[1 - my_index]
    cards, attacks = _dbs()
    prize = (len(opp.prize) - len(me.prize)) / 6.0
    ko = _ko_term(state, my_index, cards, attacks) if _W_KO else 0.0
    damage = _damage_frac(opp) - _damage_frac(me)
    dev = _clamp((_development(me) - _development(opp)) / 10.0)
    hand = _clamp((me.handCount - opp.handCount) / 5.0)
    deck = -1.0 if me.deckCount == 0 else (1.0 if opp.deckCount == 0 else 0.0)
    raw = (_W_PRIZE * prize + _W_KO * ko + _W_DMG * damage + _W_DEV * dev
           + _W_HAND * hand + _W_DECK * deck)
    return 0.5 + 0.5 * _clamp(raw)
