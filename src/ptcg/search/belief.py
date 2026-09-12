"""Belief v2: exact accounting for our zones, mirror-prior sampling with the face-down
active reserved from the pool.

The engine's search_begin needs a full prediction of every hidden zone. We know our
own deck list exactly; the opponent's unseen cards are padded from a mirror prior
(our own list) minus everything they have revealed. Log tracking is limited to
opponent-hand knowledge (cards seen moving to/from their hand).

Belief v2: a face-down opponent active is reserved from the pool BEFORE hand/prize/
deck are dealt, so the same predicted card can no longer occupy two zones at once.
"""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field

from cg.api import AreaType, Observation, PlayerState, State


@dataclass
class Determinization:
    your_deck: list[int]
    your_prize: list[int]
    opponent_deck: list[int]
    opponent_prize: list[int]
    opponent_hand: list[int]
    opponent_active: list[int]


def _visible_ids(p: PlayerState, include_hand: bool) -> list[int]:
    ids = [c.id for c in p.discard]
    for pk in list(p.active) + list(p.bench):
        if pk is None:
            continue
        ids.append(pk.id)
        for group in (pk.energyCards, pk.tools, pk.preEvolution):
            ids.extend(c.id for c in group)
    ids.extend(c.id for c in p.prize if c is not None)
    if include_hand and p.hand is not None:
        ids.extend(c.id for c in p.hand)
    return ids


def _pool_list(counter: Counter, rng: random.Random) -> list[int]:
    pool = [cid for cid, n in counter.items() for _ in range(max(n, 0))]
    rng.shuffle(pool)
    return pool


@dataclass
class BeliefState:
    my_deck: list[int]
    basic_ids: frozenset[int] = frozenset()
    opp_known_hand: dict[int, int] = field(default_factory=dict)  # serial -> cardId
    _last_turn: int = -1

    def update(self, obs: Observation) -> None:
        st = obs.current
        if st is None:
            return
        if st.turn < self._last_turn:  # arena reuses agents across matches
            self.opp_known_hand.clear()
        self._last_turn = st.turn
        opp = 1 - st.yourIndex
        for log in obs.logs:
            if log.playerIndex != opp or log.serial is None:
                continue
            if log.toArea == AreaType.HAND and log.cardId is not None:
                self.opp_known_hand[log.serial] = log.cardId
            elif log.fromArea == AreaType.HAND:
                self.opp_known_hand.pop(log.serial, None)

    def sample(self, obs: Observation, rng: random.Random) -> Determinization:
        st: State = obs.current
        me_i = st.yourIndex
        me, opp = st.players[me_i], st.players[1 - me_i]
        my_stadium = [c.id for c in st.stadium if c.playerIndex == me_i]
        opp_stadium = [c.id for c in st.stadium if c.playerIndex != me_i]
        filler = Counter(self.my_deck).most_common(1)[0][0]

        # --- our side: exact multiset ---
        unseen = Counter(self.my_deck)
        unseen.subtract(Counter(_visible_ids(me, include_hand=True) + my_stadium))
        pool = _pool_list(unseen, rng)
        need = me.deckCount + sum(1 for c in me.prize if c is None)
        pool.extend([filler] * max(need - len(pool), 0))
        your_prize = [c.id if c is not None else pool.pop() for c in me.prize]
        your_deck = [pool.pop() for _ in range(me.deckCount)]

        # --- opponent side: mirror prior ---
        known_hand = list(self.opp_known_hand.values())[: opp.handCount]
        prior = Counter(self.my_deck)
        prior.subtract(Counter(_visible_ids(opp, include_hand=False) + opp_stadium))
        prior.subtract(Counter(known_hand))
        pool = _pool_list(prior, rng)
        need = (opp.deckCount + sum(1 for c in opp.prize if c is None)
                + (opp.handCount - len(known_hand)) + 1)  # +1 for a possible active
        pool.extend([filler] * max(need - len(pool), 0))

        # belief v2: reserve the face-down active FIRST and pop it from the pool,
        # so one predicted card cannot occupy two zones. `need` already budgets +1.
        opponent_active: list[int] = []
        if opp.active and opp.active[0] is None:
            cand = next((c for c in pool if c in self.basic_ids), None)
            if cand is not None:
                pool.remove(cand)
            else:  # prior has no basic left: fall back without popping (mirror v1)
                cand = next((c for c in self.my_deck if c in self.basic_ids), filler)
            opponent_active = [cand]

        opponent_hand = known_hand + [pool.pop() for _ in range(opp.handCount - len(known_hand))]
        opponent_prize = [c.id if c is not None else pool.pop() for c in opp.prize]
        opponent_deck = [pool.pop() for _ in range(opp.deckCount)]

        if st.turn == 0 and self.basic_ids and opponent_deck and \
                not any(c in self.basic_ids for c in opponent_deck):
            basic = next((c for c in self.my_deck if c in self.basic_ids), None)
            if basic is not None:
                opponent_deck[0] = basic

        return Determinization(your_deck, your_prize, opponent_deck,
                               opponent_prize, opponent_hand, opponent_active)
