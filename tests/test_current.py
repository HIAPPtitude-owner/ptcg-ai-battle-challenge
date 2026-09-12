from pathlib import Path

from ptcg.agents import current
from ptcg.arena.runner import load_deck
from ptcg.decks.validate import validate_deck


def test_current_agent_name_matches_instance():
    agent = current.make_current_agent([])
    assert agent.name == current.CURRENT_AGENT_NAME


def test_current_deck_exists_and_is_legal():
    assert current.CURRENT_DECK_PATH.exists()
    deck = load_deck(current.CURRENT_DECK_PATH)
    # The ladder identity (frozen, out of scope for the 2026-08-11 min-basics
    # pool rule) predates MIN_BASIC_CARDS: it runs 4 basics. It must remain
    # engine-legal in every OTHER respect. Do not "fix" the deck — ladder
    # identity files are unchanged by design (spec Section 4).
    assert validate_deck(deck) == ["fewer than 8 Basic Pokémon cards (4)"]


def test_submission_main_uses_current_agent_factory():
    from ptcg import submission_main

    assert submission_main.make_current_agent is current.make_current_agent
