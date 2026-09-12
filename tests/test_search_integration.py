"""SearchAgent vs the real engine at tiny budgets — crash-free is the bar."""
from pathlib import Path

from ptcg.agents.heuristic import HeuristicAgent
from ptcg.agents.random_agent import RandomAgent
from ptcg.agents.search_agent import SearchAgent
from ptcg.arena.runner import load_deck, play_match
from ptcg.search.searcher import SearchConfig
from ptcg.search.timing import TimeManager

DECK = load_deck(Path(__file__).resolve().parents[1]
                 / "src/ptcg/decks/candidates/mega-lucario-fighting.csv")


def tiny_search_agent() -> SearchAgent:
    return SearchAgent(DECK, config=SearchConfig(max_iterations=8, max_depth=20),
                       time_manager=TimeManager(total_s=1e9, max_move_s=0.03))


def test_full_game_vs_random_crash_free():
    r = play_match(tiny_search_agent(), RandomAgent(seed=1), DECK, DECK)
    assert r.error is None
    assert r.winner in (0, 1, 2)


def test_full_game_vs_heuristic_crash_free_and_search_actually_ran():
    a = tiny_search_agent()
    r = play_match(a, HeuristicAgent(), DECK, DECK)
    assert r.error is None
    total_decisions = a.tm.moves_done
    assert total_decisions > 0
    # Not every decision may search (forced/multi-select shortcuts), but a full
    # game must not be 100% fallback: that would mean search_begin never works.
    assert a.fallbacks < total_decisions


def test_two_consecutive_games_reuse_agent_cleanly():
    a = tiny_search_agent()
    for seed in (1, 2):
        r = play_match(a, RandomAgent(seed=seed), DECK, DECK)
        assert r.error is None
