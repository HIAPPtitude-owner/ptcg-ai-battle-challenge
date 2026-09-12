"""Builders for synthetic Observations so agent logic is testable without the engine."""
from __future__ import annotations

from cg.api import (
    Observation, SelectData, SelectType, SelectContext, Option, OptionType,
    State, PlayerState, Pokemon,
)


def make_pokemon(card_id: int = 721, hp: int = 70, max_hp: int = 70,
                 energies: list | None = None) -> Pokemon:
    return Pokemon(id=card_id, serial=1, hp=hp, maxHp=max_hp, appearThisTurn=False,
                   energies=energies or [], energyCards=[], tools=[], preEvolution=[])


def make_player(active: Pokemon | None = None, hand: list | None = None,
               hand_hidden: bool = False, hand_count: int | None = None) -> PlayerState:
    resolved_hand = None if hand_hidden else (hand or [])
    return PlayerState(
        active=[active] if active else [], bench=[], benchMax=5, deckCount=40,
        discard=[], prize=[None] * 6,
        handCount=hand_count if hand_count is not None else len(hand or []),
        hand=resolved_hand,
        poisoned=False, burned=False, asleep=False, paralyzed=False, confused=False,
    )


def make_state(you: PlayerState | None = None, opp: PlayerState | None = None,
               your_index: int = 0, turn: int = 3,
               energy_attached: bool = False, turn_action_count: int = 0) -> State:
    players = [you or make_player(make_pokemon()), opp or make_player(make_pokemon())]
    if your_index == 1:
        players.reverse()
    return State(turn=turn, turnActionCount=turn_action_count, yourIndex=your_index,
                 firstPlayer=0, supporterPlayed=False, stadiumPlayed=False,
                 energyAttached=energy_attached, retreated=False, result=-1,
                 stadium=[], looking=None, players=players)


def make_select(options: list[Option], select_type: SelectType = SelectType.MAIN,
                context: SelectContext = SelectContext.MAIN,
                min_count: int = 1, max_count: int = 1) -> SelectData:
    return SelectData(type=select_type, context=context, minCount=min_count,
                      maxCount=max_count, remainDamageCounter=0, remainEnergyCost=0,
                      option=options, deck=None, contextCard=None, effect=None)


def make_obs(select: SelectData, state: State | None = None) -> Observation:
    return Observation(select=select, logs=[], current=state or make_state())
