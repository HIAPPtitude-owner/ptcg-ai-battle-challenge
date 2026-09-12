"""Single source of truth for the ladder identity: which agent pilots which deck.

The tournament, the regression pin, and the Kaggle submission all read from
here so they can never silently diverge.
"""

from pathlib import Path

from ptcg.agents.base import Agent
from ptcg.agents.heuristic import HeuristicAgent

CURRENT_AGENT_NAME = "heuristic-v0"
CURRENT_DECK_PATH = Path("src/ptcg/decks/candidates/mega-lucario-fighting.csv")


def make_current_agent(deck: list[int]) -> Agent:
    """Build the current ladder agent. `deck` is accepted for signature stability
    (a future search-agent pilot needs it); heuristic-v0 ignores it."""
    return HeuristicAgent()
