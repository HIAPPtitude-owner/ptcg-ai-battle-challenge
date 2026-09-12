"""Heuristic baseline v0: deterministic priority rules over engine-legal options.

Design: `choose` is a pure function of (observation, card DB, attack DB) so unit
tests need no engine state. HeuristicAgent caches the DBs once per process.

Unknown contexts and option types NEVER raise — the competition may append enum
members mid-season; the fallback picks the first maxCount options.
"""
from __future__ import annotations

from cg.api import (
    Attack, CardData, CardType, Observation, Option, OptionType,
    SelectContext, SelectType, all_attack, all_card_data,
)

from .base import Agent

# Contexts where fewer/cheaper selections are better (costs, discards).
_MINIMIZE_CONTEXTS = frozenset({
    SelectContext.DISCARD, SelectContext.DISCARD_ENERGY,
    SelectContext.DISCARD_ENERGY_CARD, SelectContext.DISCARD_TOOL_CARD,
    SelectContext.DISCARD_CARD_OR_ATTACHED_CARD, SelectContext.TO_DECK,
    SelectContext.TO_DECK_BOTTOM, SelectContext.TO_DECK_ENERGY,
    SelectContext.TO_PRIZE, SelectContext.DEVOLVE, SelectContext.DISABLE_ATTACK,
})

_LOOP_GUARD_ACTIONS = 50  # after this many actions in one turn, wrap up


def _opp_active_hp(obs: Observation) -> int | None:
    state = obs.current
    if state is None:
        return None
    opp = state.players[1 - state.yourIndex]
    if opp.active and opp.active[0] is not None:
        return opp.active[0].hp
    return None


def _attack_damage(option: Option, attacks: dict[int, Attack]) -> int:
    atk = attacks.get(option.attackId) if option.attackId is not None else None
    return atk.damage if atk else 0


def _main_priority(option: Option, obs: Observation,
                   attacks: dict[int, Attack]) -> tuple:
    """Lower tuple = better. Priorities: lethal attack, evolve, attach, play,
    ability, best attack, other, retreat, end."""
    t = option.type
    opp_hp = _opp_active_hp(obs)
    loop_guard = (obs.current is not None
                  and obs.current.turnActionCount > _LOOP_GUARD_ACTIONS)
    if t == OptionType.ATTACK:
        dmg = _attack_damage(option, attacks)
        if opp_hp is not None and dmg >= opp_hp > 0:
            return (0, -dmg)
        return (5, -dmg)
    if loop_guard:
        # Only attacking (handled above) or ending beats anything else now.
        return (8, 0) if t == OptionType.END else (9, 0)
    if t == OptionType.EVOLVE:
        return (1, 0)
    if t == OptionType.ATTACH:
        return (2, 0)
    if t == OptionType.PLAY:
        return (3, 0)
    if t == OptionType.ABILITY:
        return (4, 0)
    if t == OptionType.RETREAT:
        return (8, 0)
    if t == OptionType.END:
        return (9, 0)
    return (7, 0)


def _choose_main(obs: Observation, attacks: dict[int, Attack]) -> list[int]:
    options = obs.select.option
    best = min(range(len(options)),
               key=lambda i: _main_priority(options[i], obs, attacks))
    return [best]


def _resolve_card_id(obs: Observation, option: Option) -> int | None:
    """Map a CARD option to the underlying card id via the state containers."""
    state, sel = obs.current, obs.select
    if option.area is None or option.index is None:
        return None
    try:
        area = int(option.area)
        if area == 1 and sel.deck is not None:  # DECK
            return sel.deck[option.index].id
        if state is None or option.playerIndex is None:
            return None
        player = state.players[option.playerIndex]
        if option.toolIndex is not None or option.energyIndex is not None:
            # TOOL_CARD/ENERGY_CARD: area/index identify the HOST Pokémon
            # in play; the selected card is one of its attachments.
            host = None
            if area == 4 and player.active and player.active[0]:  # ACTIVE
                host = player.active[0]
            elif area == 5:  # BENCH
                host = player.bench[option.index]
            if host is None:
                return None
            if option.toolIndex is not None:
                return host.tools[option.toolIndex].id
            return host.energyCards[option.energyIndex].id
        if area == 2 and player.hand is not None:  # HAND
            return player.hand[option.index].id
        if area == 3:  # DISCARD
            return player.discard[option.index].id
        if area == 4 and player.active and player.active[0]:  # ACTIVE
            return player.active[0].id
        if area == 5:  # BENCH
            return player.bench[option.index].id
        if area == 12 and state.looking:  # LOOKING
            card = state.looking[option.index]
            return card.id if card else None
    except (IndexError, TypeError):
        return None
    return None


def _card_value(card_id: int | None, cards: dict[int, CardData]) -> float:
    """Static desirability of a card. Pokémon > supporter > item > energy."""
    if card_id is None or card_id not in cards:
        return 1.0
    card = cards[card_id]
    ct = card.cardType
    if ct == CardType.POKEMON:
        return 3.0 + card.hp / 1000.0
    if ct == CardType.SUPPORTER:
        return 2.0
    if ct in (CardType.ITEM, CardType.TOOL, CardType.STADIUM):
        return 1.5
    return 0.5  # energies are replaceable


def _choose_cards(obs: Observation, cards: dict[int, CardData]) -> list[int]:
    sel = obs.select
    minimize = sel.context in _MINIMIZE_CONTEXTS
    k = sel.minCount if minimize else sel.maxCount
    if k == 0:
        return []
    scored = sorted(
        range(len(sel.option)),
        key=lambda i: _card_value(_resolve_card_id(obs, sel.option[i]), cards),
        reverse=not minimize,
    )
    return scored[:k]


def _choose_yes_no(obs: Observation) -> list[int]:
    sel = obs.select
    want_no = sel.context == SelectContext.MULLIGAN
    target = OptionType.NO if want_no else OptionType.YES
    for i, option in enumerate(sel.option):
        if option.type == target:
            return [i]
    return [0]


def _choose_count(obs: Observation) -> list[int]:
    sel = obs.select
    best = max(range(len(sel.option)),
               key=lambda i: sel.option[i].number or 0)
    return [best]


def _choose_attack(obs: Observation, attacks: dict[int, Attack]) -> list[int]:
    sel = obs.select
    opp_hp = _opp_active_hp(obs)

    def key(i: int) -> tuple:
        dmg = _attack_damage(sel.option[i], attacks)
        lethal = opp_hp is not None and dmg >= opp_hp > 0
        return (0 if lethal else 1, -dmg)

    return [min(range(len(sel.option)), key=key)]


def _fallback(obs: Observation) -> list[int]:
    sel = obs.select
    k = sel.maxCount
    return list(range(min(k, len(sel.option))))


def choose(obs: Observation, cards: dict[int, CardData],
           attacks: dict[int, Attack]) -> list[int]:
    sel = obs.select
    try:
        st = sel.type
        if st == SelectType.MAIN:
            return _choose_main(obs, attacks)
        if st in (SelectType.CARD, SelectType.CARD_OR_ATTACHED_CARD,
                  SelectType.ATTACHED_CARD):
            return _choose_cards(obs, cards)
        if st == SelectType.YES_NO:
            return _choose_yes_no(obs)
        if st == SelectType.COUNT:
            return _choose_count(obs)
        if st == SelectType.ATTACK:
            return _choose_attack(obs, attacks)
        return _fallback(obs)
    except Exception:  # noqa: BLE001 — never crash on engine surprises
        return _fallback(obs)


class HeuristicAgent(Agent):
    name = "heuristic-v0"

    def __init__(self) -> None:
        self.cards = {c.cardId: c for c in all_card_data()}
        self.attacks = {a.attackId: a for a in all_attack()}

    def act(self, obs: Observation) -> list[int]:
        return choose(obs, self.cards, self.attacks)
