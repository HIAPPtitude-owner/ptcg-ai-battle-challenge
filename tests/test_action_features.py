"""Action featurizer: fixed-length, [0,1]-bounded, tolerant of unknowns."""
from types import SimpleNamespace

import pytest

from ptcg.search.action_features import (ACTION_FEATURE_NAMES,
                                         ACTION_FEATURE_VERSION,
                                         N_ACTION_FEATURES, extract_action)


def _opt(**kw):
    base = dict(type=None, cardId=None, attackId=None, number=None, area=None,
                playerIndex=None, toolIndex=None, energyIndex=None, count=None,
                inPlayArea=None, inPlayIndex=None, specialConditionType=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_version_and_shape():
    assert ACTION_FEATURE_VERSION == 2
    assert N_ACTION_FEATURES == len(ACTION_FEATURE_NAMES) == 18


def test_all_none_option_is_zero_vector():
    vec = extract_action(_opt(), {}, {})
    assert vec == [0.0] * 18


def test_full_option_values():
    card = SimpleNamespace(hp=170, basic=True)
    attack = SimpleNamespace(damage=120, energies=[1, 2])
    vec = extract_action(
        _opt(type=5, cardId=10, attackId=3, number=3, area=6, count=3,
             energyIndex=1, specialConditionType=2,
             inPlayArea=6, inPlayIndex=3, playerIndex=1),
        {10: card}, {3: attack})
    # v1 dims (hand-derived): 5/12, 1, 1, 120/300, 2/5, 170/340, 1, 3/10, 6/12,
    # 3/6, 1, 1
    # v2-appended dims (hand-derived):
    #   in_play_area_norm = inPlayArea(6)/12  = 0.5
    #   in_play_index_norm = inPlayIndex(3)/6 = 0.5
    #   has_player_index = 1.0 (playerIndex=1 is not None)
    #   player_index_val = playerIndex(1)/1   = 1.0
    #   tool_index_norm = toolIndex(None)     = 0.0
    #   energy_index_norm = energyIndex(1)/8  = 0.125
    assert vec == pytest.approx([5 / 12, 1.0, 1.0, 0.4, 0.4, 0.5, 1.0,
                                 0.3, 0.5, 0.5, 1.0, 1.0,
                                 0.5, 0.5, 1.0, 1.0, 0.0, 0.125])


def test_caps_and_unknown_ids_never_raise():
    vec = extract_action(_opt(type=999, cardId=424242, attackId=999999,
                              number=50, area=99, count=50,
                              inPlayArea=99, inPlayIndex=99,
                              playerIndex=99, toolIndex=99, energyIndex=99),
                         {}, {})
    assert all(0.0 <= v <= 1.0 for v in vec) and len(vec) == 18


def test_v2_breaks_v1_collision_via_in_play_index():
    """v1's fields alone (type/area) collide two board-slot options into
    identical vectors (the 52.3% collision rate diagnosed in
    .superpowers/sdd/t11-diagnosis.md); v2 must distinguish them via
    inPlayIndex even when every other field matches."""
    opt_a = _opt(type=2, area=6, inPlayIndex=2)
    opt_b = _opt(type=2, area=6, inPlayIndex=5)
    vec_a = extract_action(opt_a, {}, {})
    vec_b = extract_action(opt_b, {}, {})
    assert vec_a != vec_b
