"""Leaf evaluator ordering sanity."""
from ptcg.search.evaluate import evaluate
from tests.fixtures.obs import make_player, make_pokemon, make_state


def test_terminal_win_loss_draw():
    st = make_state()
    st.result = 0
    assert evaluate(st, 0) == 1.0
    assert evaluate(st, 1) == 0.0
    st.result = 2
    assert evaluate(st, 0) == 0.5


def test_fewer_prizes_remaining_scores_higher():
    ahead, even = make_state(), make_state()
    ahead.players[0].prize = [None] * 1   # we've taken 5
    assert evaluate(ahead, 0) > evaluate(even, 0)


def test_damage_on_opponent_scores_higher():
    hurt_opp = make_state(opp=make_player(make_pokemon(hp=10, max_hp=70)))
    even = make_state()
    assert evaluate(hurt_opp, 0) > evaluate(even, 0)


def test_symmetric_state_is_half():
    assert abs(evaluate(make_state(), 0) - 0.5) < 1e-9


def test_own_deckout_is_bad():
    st = make_state()
    st.players[0].deckCount = 0
    assert evaluate(st, 0) < 0.5


def test_bounded():
    st = make_state()
    st.players[0].prize = []
    st.players[0].handCount = 40
    assert 0.0 <= evaluate(st, 0) <= 1.0


def test_ko_term_prefers_position_where_we_ko_first():
    """v2 KO-awareness: with injected DBs, a state where my active can KO the
    opponent's active (and I move next) beats the symmetric no-KO state."""
    from cg.api import EnergyType
    from ptcg.search import evaluate as ev

    cards = {1: type("C", (), {"cardId": 1, "attacks": [10]})()}
    attacks = {10: type("A", (), {"attackId": 10, "damage": 50,
                                  "energies": [EnergyType.FIGHTING]})()}
    armed = make_state(
        you=make_player(make_pokemon(card_id=1, hp=70, max_hp=70,
                                     energies=[EnergyType.FIGHTING])),
        opp=make_player(make_pokemon(card_id=1, hp=40, max_hp=70)))
    assert ev._ko_term(armed, 0, cards, attacks) == 1.0   # I move (yourIndex=0), I KO
    assert ev._ko_term(armed, 1, cards, attacks) == -1.0  # from opp's view: threatened
    no_energy = make_state(
        you=make_player(make_pokemon(card_id=1, hp=70, max_hp=70)),
        opp=make_player(make_pokemon(card_id=1, hp=40, max_hp=70)))
    assert ev._ko_term(no_energy, 0, cards, attacks) == 0.0  # attack not usable


def test_cost_satisfied_colorless_and_specific():
    from cg.api import EnergyType as E
    from ptcg.search.evaluate import _cost_satisfied

    assert _cost_satisfied([E.FIGHTING], [E.FIGHTING])
    assert not _cost_satisfied([E.FIGHTING], [E.WATER])
    assert _cost_satisfied([E.COLORLESS, E.COLORLESS], [E.WATER, E.FIRE])
    assert not _cost_satisfied([E.FIGHTING, E.COLORLESS], [E.FIGHTING])  # short one
    assert _cost_satisfied([E.FIGHTING, E.COLORLESS], [E.FIGHTING, E.WATER])
    assert _cost_satisfied([E.FIGHTING], [E.RAINBOW])  # rainbow pays any type


class TestCostSatisfiedRainbow:
    def test_one_rainbow_cannot_pay_two_specific_colors(self):
        from cg.api import EnergyType
        from ptcg.search.evaluate import _cost_satisfied
        # v1 bug: RAINBOW added to FIGHTING availability AND WATER availability
        assert not _cost_satisfied(
            [EnergyType.FIGHTING, EnergyType.WATER],
            [EnergyType.RAINBOW, EnergyType.GRASS])

    def test_two_rainbows_pay_two_specific_colors(self):
        from cg.api import EnergyType
        from ptcg.search.evaluate import _cost_satisfied
        assert _cost_satisfied(
            [EnergyType.FIGHTING, EnergyType.WATER],
            [EnergyType.RAINBOW, EnergyType.RAINBOW])

    def test_rainbow_pays_one_specific_and_other_pays_colorless(self):
        from cg.api import EnergyType
        from ptcg.search.evaluate import _cost_satisfied
        assert _cost_satisfied(
            [EnergyType.FIGHTING, EnergyType.COLORLESS],
            [EnergyType.RAINBOW, EnergyType.GRASS])

    def test_specific_plus_rainbow_covers_double_requirement(self):
        from cg.api import EnergyType
        from ptcg.search.evaluate import _cost_satisfied
        assert _cost_satisfied(
            [EnergyType.FIGHTING, EnergyType.FIGHTING],
            [EnergyType.FIGHTING, EnergyType.RAINBOW])

    def test_colorless_only_paid_by_anything(self):
        from cg.api import EnergyType
        from ptcg.search.evaluate import _cost_satisfied
        assert _cost_satisfied(
            [EnergyType.COLORLESS, EnergyType.COLORLESS],
            [EnergyType.GRASS, EnergyType.WATER])

    def test_insufficient_total_count_fails(self):
        from cg.api import EnergyType
        from ptcg.search.evaluate import _cost_satisfied
        assert not _cost_satisfied([EnergyType.FIGHTING], [])
