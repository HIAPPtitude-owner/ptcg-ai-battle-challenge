from cg.api import AreaType, Attack, Card, CardData, CardType, EnergyType, Option, OptionType, SelectContext, SelectType

from ptcg.agents.heuristic import _resolve_card_id, choose
from tests.fixtures.obs import make_obs, make_pokemon, make_player, make_select, make_state

ATTACKS = {
    10: Attack(attackId=10, name="Weak", text="", damage=30, energies=[EnergyType.COLORLESS]),
    11: Attack(attackId=11, name="Strong", text="", damage=90, energies=[EnergyType.WATER]),
}
CARDS: dict[int, CardData] = {}


def test_lethal_attack_preferred_over_end():
    opp = make_player(make_pokemon(hp=30))
    you = make_player(make_pokemon())
    state = make_state(you=you, opp=opp)
    options = [
        Option(type=OptionType.END),
        Option(type=OptionType.ATTACK, attackId=10),  # 30 dmg == opp hp -> lethal
    ]
    obs = make_obs(make_select(options), state)
    assert choose(obs, CARDS, ATTACKS) == [1]


def test_highest_damage_lethal_wins_tiebreak():
    opp = make_player(make_pokemon(hp=30))
    state = make_state(you=make_player(make_pokemon()), opp=opp)
    options = [
        Option(type=OptionType.ATTACK, attackId=10),  # lethal, 30 dmg
        Option(type=OptionType.ATTACK, attackId=11),  # lethal, 90 dmg -> preferred
    ]
    obs = make_obs(make_select(options), state)
    assert choose(obs, CARDS, ATTACKS) == [1]


def test_higher_damage_attack_when_no_lethal():
    opp = make_player(make_pokemon(hp=200))
    state = make_state(you=make_player(make_pokemon()), opp=opp)
    options = [
        Option(type=OptionType.ATTACK, attackId=10),
        Option(type=OptionType.ATTACK, attackId=11),
        Option(type=OptionType.END),
    ]
    obs = make_obs(make_select(options), state)
    assert choose(obs, CARDS, ATTACKS) == [1]


def test_evolve_preferred_over_plain_attack():
    state = make_state(you=make_player(make_pokemon()), opp=make_player(make_pokemon(hp=200)))
    options = [
        Option(type=OptionType.ATTACK, attackId=10),
        Option(type=OptionType.EVOLVE, area=AreaType.HAND, index=0,
               inPlayArea=AreaType.ACTIVE, inPlayIndex=0),
    ]
    obs = make_obs(make_select(options), state)
    assert choose(obs, CARDS, ATTACKS) == [1]


def test_look_context_card_selection_respects_counts():
    options = [Option(type=OptionType.CARD, area=AreaType.HAND, index=i, playerIndex=0) for i in range(4)]
    sel = make_select(options, select_type=SelectType.CARD,
                      context=SelectContext.LOOK, min_count=0, max_count=2)
    obs = make_obs(sel)
    r = choose(obs, CARDS, ATTACKS)
    assert 0 <= len(r) <= 2
    assert all(0 <= i < 4 for i in r)


def test_unknown_select_type_falls_back_safely():
    """Mid-season enum addition may arrive as a raw int not in SelectType;
    choose() must never raise and must return a valid, in-range selection."""
    options = [Option(type=OptionType.CARD, area=AreaType.HAND, index=i, playerIndex=0) for i in range(5)]
    sel = make_select(options, select_type=999, context=SelectContext.MAIN,
                      min_count=1, max_count=3)
    obs = make_obs(sel)
    r = choose(obs, CARDS, ATTACKS)
    assert 0 <= len(r) <= 3
    assert all(0 <= i < 5 for i in r)


def test_resolve_card_id_uses_tool_and_energy_index_not_host_pokemon():
    """DISCARD_ENERGY_CARD/DISCARD_TOOL_CARD options must resolve to the
    attached card, not the host Pokémon's own card id."""
    tool_card = Card(id=501, serial=10, playerIndex=0)
    energy_card = Card(id=502, serial=11, playerIndex=0)
    bench_mon = make_pokemon(card_id=1)
    bench_mon.tools = [tool_card]
    bench_mon.energyCards = [energy_card]
    you = make_player(make_pokemon())
    you.bench = [bench_mon]
    state = make_state(you=you, opp=make_player(make_pokemon()))
    obs = make_obs(make_select([]), state)

    tool_option = Option(type=OptionType.TOOL_CARD, area=AreaType.BENCH, index=0,
                        playerIndex=0, toolIndex=0)
    energy_option = Option(type=OptionType.ENERGY_CARD, area=AreaType.BENCH, index=0,
                           playerIndex=0, energyIndex=0)

    assert _resolve_card_id(obs, tool_option) == 501
    assert _resolve_card_id(obs, energy_option) == 502


def test_loop_guard_prefers_end_after_many_actions():
    state = make_state(turn_action_count=60,
                       you=make_player(make_pokemon()), opp=make_player(make_pokemon(hp=200)))
    options = [
        Option(type=OptionType.ABILITY, area=AreaType.ACTIVE, index=0),
        Option(type=OptionType.END),
    ]
    obs = make_obs(make_select(options), state)
    assert choose(obs, CARDS, ATTACKS) == [1]


def test_heuristic_beats_random_smoke():
    """20-game smoke: heuristic must clearly dominate random."""
    from pathlib import Path

    from ptcg.agents.heuristic import HeuristicAgent
    from ptcg.agents.random_agent import RandomAgent
    from ptcg.arena.runner import load_deck, run_series

    deck = load_deck(Path("tests/fixtures/sample_deck.csv"))
    s = run_series(HeuristicAgent(), RandomAgent(seed=3), deck, deck, n_games=20)
    assert s.win_rate_a >= 0.7, f"heuristic only won {s.wins_a}/20"


import pytest


@pytest.mark.slow
def test_heuristic_acceptance_200_games():
    """Spec bar: >=90% vs random over 200 games, zero errors."""
    from pathlib import Path

    from ptcg.agents.heuristic import HeuristicAgent
    from ptcg.agents.random_agent import RandomAgent
    from ptcg.arena.runner import load_deck, run_series

    deck = load_deck(Path("tests/fixtures/sample_deck.csv"))
    s = run_series(HeuristicAgent(), RandomAgent(seed=11), deck, deck, n_games=200)
    assert s.win_rate_a >= 0.9, f"win-rate {s.win_rate_a:.3f} below 0.9 bar"
