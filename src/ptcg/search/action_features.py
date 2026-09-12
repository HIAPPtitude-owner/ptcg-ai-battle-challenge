"""Per-option action features for the policy net (ACTION_FEATURE_VERSION 2).

Serve-path module (bundled on Kaggle): stdlib only. Tolerates unknown enum
members and missing DB entries — the organizers may append enum members
mid-competition — by clamping numerics and treating misses as zeros, never
raising.

v2 appends 6 target-identity fields (`inPlayArea`, `inPlayIndex`,
`playerIndex`, `toolIndex`, `energyIndex` value) that v1 dropped entirely —
v1 encoded only card/attack metadata, so board-slot/target-selection options
that differ only in WHICH slot/target they act on collapsed to identical
vectors (diagnosed at 52.3% within-decision collision rate in
`.superpowers/sdd/t11-diagnosis.md`). Signature is unchanged (opt, cards,
attacks) — deliberately no state-dependent features this pass."""
from __future__ import annotations

ACTION_FEATURE_VERSION = 2

ACTION_FEATURE_NAMES = [
    "type_norm", "has_card", "has_attack", "attack_damage", "attack_cost",
    "card_hp", "card_basic", "number_norm", "area_norm", "count_norm",
    "has_energy_index", "has_special_condition",
    "in_play_area_norm", "in_play_index_norm", "has_player_index",
    "player_index_val", "tool_index_norm", "energy_index_norm",
]
N_ACTION_FEATURES = len(ACTION_FEATURE_NAMES)


def _norm(x, hi: float) -> float:
    v = -1.0 if x is None else float(int(x))
    return max(0.0, min(v, hi)) / hi


def extract_action(opt, cards: dict, attacks: dict) -> list[float]:
    """Features of one selectable Option, len == N_ACTION_FEATURES, all in [0,1].
    `cards`/`attacks` are the id-keyed DB dicts SearchAgent already builds."""
    card = cards.get(opt.cardId) if opt.cardId is not None else None
    attack = attacks.get(opt.attackId) if opt.attackId is not None else None
    damage = float(getattr(attack, "damage", 0) or 0) if attack else 0.0
    energies = (attack.energies if attack is not None
                and getattr(attack, "energies", None) else [])
    hp = float(getattr(card, "hp", 0) or 0) if card else 0.0
    return [
        _norm(opt.type, 12.0),
        1.0 if card is not None else 0.0,
        1.0 if attack is not None else 0.0,
        min(damage, 300.0) / 300.0,
        min(float(len(energies)), 5.0) / 5.0,
        min(hp, 340.0) / 340.0,
        1.0 if card is not None and getattr(card, "basic", False) else 0.0,
        _norm(opt.number, 10.0),
        _norm(opt.area, 12.0),
        _norm(opt.count, 6.0),
        1.0 if opt.energyIndex is not None else 0.0,
        1.0 if opt.specialConditionType is not None else 0.0,
        _norm(opt.inPlayArea, 12.0),
        _norm(opt.inPlayIndex, 6.0),
        1.0 if opt.playerIndex is not None else 0.0,
        _norm(opt.playerIndex, 1.0),
        _norm(opt.toolIndex, 6.0),
        _norm(opt.energyIndex, 8.0),
    ]
