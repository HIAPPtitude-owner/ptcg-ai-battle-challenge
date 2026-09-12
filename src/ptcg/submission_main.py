"""Kaggle submission entry point. Copied to the bundle root as main.py.

The Kaggle harness calls agent(obs_dict). First call (select is None) must
return the 60-card deck; afterwards, option indices.
"""
import os
import sys


def _agent_dir() -> str:
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:  # kaggle_environments exec()s this source without __file__
        pass
    kaggle_dir = "/kaggle_simulations/agent"
    if os.path.isdir(kaggle_dir):
        return kaggle_dir
    return os.getcwd()


AGENT_DIR = _agent_dir()
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

from cg.api import to_observation_class  # noqa: E402
from ptcg.agents.current import make_current_agent  # noqa: E402

_agent = None
# Instrumentation seam for the bundle smoke test (scripts/package_submission.py):
# counts decisions answered by the degraded `[0]` exception fallback below. A
# bundle whose intended agent path silently broke can otherwise ride the
# fallback to a completed battle and a green "SMOKE OK". Serving-safe: a bare
# int, never read on Kaggle.
_fallback_count = 0


def read_deck_csv() -> list:
    path = os.path.join(AGENT_DIR, "deck.csv")
    if not os.path.exists(path):
        path = "deck.csv"
    if not os.path.exists(path):
        path = "/kaggle_simulations/agent/deck.csv"
    with open(path) as f:
        lines = f.read().strip().splitlines()
    deck = [int(line.strip()) for line in lines if line.strip()]
    if len(deck) != 60:
        raise ValueError(f"{path}: expected 60 cards, got {len(deck)}")
    return deck


def agent(obs_dict: dict) -> list:
    global _agent, _fallback_count
    try:
        obs = to_observation_class(obs_dict)
        if obs.select is None:
            return read_deck_csv()
        if _agent is None:
            _agent = make_current_agent(read_deck_csv())
        return _agent.act(obs)
    except Exception:
        # Last line of defense against unexpected observation shapes
        # (e.g. missing 'logs'/'current' keys raise TypeError in
        # to_observation_class today). The heuristic agent already has
        # its own internal fallbacks for in-scope failures.
        if obs_dict.get("select") is None:
            return read_deck_csv()
        _fallback_count += 1  # degraded decision: intended agent path did not run
        return [0]  # index 0 is always valid: option lists are non-empty
