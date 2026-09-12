"""Fixed-length feature vector for the value net.

SINGLE source of truth for both training-data generation and bundle inference —
train/serve skew is structurally impossible. Pure stdlib + cg.api only.
"""
from __future__ import annotations

from cg.api import PlayerState, State

from ptcg.search.evaluate import _active, _best_usable_damage, _dbs

FEATURE_VERSION = 1

_SIDE_NAMES = [
    "prizes", "active_present", "active_hp_frac", "active_maxhp",
    "active_dmg_taken", "active_energy", "active_best_dmg",
    "bench_count", "bench_energy", "bench_hp_frac_mean", "bench_dmg_taken",
    "hand", "deck", "discard", "poisoned", "hard_status",
]
FEATURE_NAMES = (
    ["i_move", "turn", "energy_attached", "prize_diff"]
    + [f"me_{n}" for n in _SIDE_NAMES]
    + [f"opp_{n}" for n in _SIDE_NAMES]
    + ["me_can_ko_opp", "opp_can_ko_me", "me_decked", "opp_decked"]
)

_HP_SCALE = 340.0  # max printed HP in the card pool, rounded up


def _cap(x: float, hi: float) -> float:
    return min(x, hi) / hi


def _side(p: PlayerState, best_dmg: int) -> list[float]:
    active = _active(p)
    bench = [pk for pk in p.bench if pk is not None]
    bench_hp = (sum(pk.hp / max(pk.maxHp, 1) for pk in bench) / len(bench)
                if bench else 0.0)
    return [
        len(p.prize) / 6.0,
        1.0 if active is not None else 0.0,
        (active.hp / max(active.maxHp, 1)) if active is not None else 0.0,
        (active.maxHp / _HP_SCALE) if active is not None else 0.0,
        ((active.maxHp - active.hp) / _HP_SCALE) if active is not None else 0.0,
        _cap(float(len(active.energies)), 6.0) if active is not None else 0.0,
        _cap(float(best_dmg), 300.0),
        len(bench) / 5.0,
        _cap(float(sum(len(pk.energies) for pk in bench)), 10.0),
        bench_hp,
        _cap(float(sum(pk.maxHp - pk.hp for pk in bench)), 600.0),
        _cap(float(p.handCount), 12.0),
        p.deckCount / 60.0,
        _cap(float(len(p.discard)), 60.0),
        1.0 if p.poisoned else 0.0,
        1.0 if (p.asleep or p.paralyzed or p.confused) else 0.0,
    ]


def extract(state: State, my_index: int,
            dbs: tuple[dict, dict] | None = None) -> list[float]:
    """Features of `state` from `my_index`'s perspective, len == FEATURE_NAMES."""
    cards, attacks = dbs if dbs is not None else _dbs()
    me, opp = state.players[my_index], state.players[1 - my_index]
    my_best = _best_usable_damage(_active(me), cards, attacks)
    opp_best = _best_usable_damage(_active(opp), cards, attacks)
    my_active, opp_active = _active(me), _active(opp)
    vec = [
        1.0 if state.yourIndex == my_index else 0.0,
        _cap(float(state.turn), 30.0),
        1.0 if state.energyAttached else 0.0,
        (len(opp.prize) - len(me.prize)) / 6.0,
    ]
    vec += _side(me, my_best)
    vec += _side(opp, opp_best)
    vec += [
        1.0 if (opp_active is not None and opp_active.hp > 0
                and my_best >= opp_active.hp) else 0.0,
        1.0 if (my_active is not None and my_active.hp > 0
                and opp_best >= my_active.hp) else 0.0,
        1.0 if me.deckCount == 0 else 0.0,
        1.0 if opp.deckCount == 0 else 0.0,
    ]
    return vec
