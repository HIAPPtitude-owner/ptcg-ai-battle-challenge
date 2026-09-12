"""tests/test_features.py"""
import pytest

from ptcg.search.features import FEATURE_NAMES, FEATURE_VERSION, extract
from tests.fixtures.obs import make_pokemon, make_player, make_state

EMPTY_DBS = ({}, {})


def _named(vec):
    return dict(zip(FEATURE_NAMES, vec))


def test_length_and_version_pinned():
    assert FEATURE_VERSION == 1
    assert len(FEATURE_NAMES) == 40
    vec = extract(make_state(), 0, dbs=EMPTY_DBS)
    assert len(vec) == len(FEATURE_NAMES)
    assert all(isinstance(v, float) for v in vec)


def test_default_state_hand_computed_values():
    # make_state(): both sides one 70/70 active, no energies, empty bench,
    # deckCount=40, 6 prizes, handCount=0, turn=3, yourIndex=0.
    f = _named(extract(make_state(), 0, dbs=EMPTY_DBS))
    assert f["i_move"] == 1.0
    assert f["turn"] == pytest.approx(3 / 30)
    assert f["energy_attached"] == 0.0
    assert f["prize_diff"] == 0.0
    assert f["me_prizes"] == 1.0
    assert f["me_active_present"] == 1.0
    assert f["me_active_hp_frac"] == 1.0
    assert f["me_active_maxhp"] == pytest.approx(70 / 340)
    assert f["me_active_dmg_taken"] == 0.0
    assert f["me_active_best_dmg"] == 0.0   # empty DB -> no usable attack
    assert f["me_bench_count"] == 0.0
    assert f["me_deck"] == pytest.approx(40 / 60)
    assert f["opp_hand"] == 0.0
    assert f["me_can_ko_opp"] == 0.0


def test_perspective_flip_swaps_sides():
    you = make_player(make_pokemon(hp=35, max_hp=70))
    opp = make_player(make_pokemon(hp=70, max_hp=70))
    st = make_state(you=you, opp=opp, your_index=0)
    mine = _named(extract(st, 0, dbs=EMPTY_DBS))
    theirs = _named(extract(st, 1, dbs=EMPTY_DBS))
    assert mine["me_active_hp_frac"] == pytest.approx(0.5)
    assert theirs["opp_active_hp_frac"] == pytest.approx(0.5)
    assert theirs["me_active_hp_frac"] == pytest.approx(1.0)
    assert mine["i_move"] == 1.0 and theirs["i_move"] == 0.0
