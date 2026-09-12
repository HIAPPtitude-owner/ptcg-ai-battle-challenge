from pathlib import Path

from ptcg.agents.heuristic import HeuristicAgent
from ptcg.agents.random_agent import RandomAgent
from ptcg.arena.runner import load_deck, play_match, run_series

DECK = Path("tests/fixtures/sample_deck.csv")


def test_load_deck():
    deck = load_deck(DECK)
    assert len(deck) == 60
    assert all(isinstance(c, int) for c in deck)


def test_single_match_completes():
    deck = load_deck(DECK)
    r = play_match(RandomAgent(seed=1), RandomAgent(seed=2), deck, deck)
    assert r.error is None
    assert r.winner in (0, 1, 2)
    assert r.moves > 0
    assert r.seconds > 0


def test_series_alternates_and_aggregates():
    deck = load_deck(DECK)
    s = run_series(RandomAgent(seed=1), RandomAgent(seed=2), deck, deck, n_games=4)
    assert s.n == 4
    assert s.wins_a + s.wins_b + s.draws == 4


def test_run_series_calls_on_game_end_once_per_game():
    deck = load_deck(DECK)
    seen: list[int] = []
    run_series(HeuristicAgent(), HeuristicAgent(), deck, deck, n_games=4,
               on_game_end=seen.append)
    assert seen == [0, 1, 2, 3]
