"""Battle runner: pits two Agents against each other via the cg engine.

The cg SDK holds one battle per process (module-global Battle state), so
matches run sequentially within a process.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from cg.api import to_observation_class
from cg.game import battle_finish, battle_select, battle_start

from ptcg.agents.base import Agent
from ptcg.arena.stats import SeriesStats


def load_deck(path: str | Path) -> list[int]:
    lines = Path(path).read_text().strip().splitlines()
    deck = [int(line.strip()) for line in lines if line.strip()]
    if len(deck) != 60:
        raise ValueError(f"{path}: expected 60 cards, got {len(deck)}")
    return deck


def _validate_selection(selection: list[int], n_options: int,
                        min_count: int, max_count: int, agent_name: str) -> None:
    if not (min_count <= len(selection) <= max_count):
        raise AssertionError(
            f"{agent_name}: returned {len(selection)} selections, "
            f"allowed [{min_count}, {max_count}]")
    if len(set(selection)) != len(selection):
        raise AssertionError(f"{agent_name}: duplicate selections {selection}")
    if any(not (0 <= i < n_options) for i in selection):
        raise AssertionError(f"{agent_name}: selection out of range {selection}")


@dataclass
class MatchResult:
    winner: int  # 0/1 player index, 2 = draw, -1 = errored
    turns: int
    moves: int
    seconds: float
    max_move_seconds: tuple[float, float]  # per player index
    error: str | None = None


def play_match(agent0: Agent, agent1: Agent, deck0: list[int], deck1: list[int],
               max_moves: int = 3000) -> MatchResult:
    agents = (agent0, agent1)
    max_move = [0.0, 0.0]
    start = time.perf_counter()
    obs_dict, start_data = battle_start(deck0, deck1)
    if obs_dict is None:
        return MatchResult(-1, 0, 0, 0.0, (0.0, 0.0),
                           error=f"battle_start failed: player={start_data.errorPlayer} "
                                 f"type={start_data.errorType}")
    moves = 0
    try:
        while True:
            obs = to_observation_class(obs_dict)
            state = obs.current
            if state.result != -1:
                winner = state.result
                break
            if moves >= max_moves:
                return MatchResult(-1, state.turn, moves, time.perf_counter() - start,
                                   (max_move[0], max_move[1]), error="max_moves exceeded")
            idx = state.yourIndex
            t0 = time.perf_counter()
            selection = agents[idx].act(obs)
            max_move[idx] = max(max_move[idx], time.perf_counter() - t0)
            _validate_selection(selection, len(obs.select.option),
                                obs.select.minCount, obs.select.maxCount,
                                agents[idx].name)
            obs_dict = battle_select(selection)
            moves += 1
        return MatchResult(winner, obs.current.turn, moves,
                           time.perf_counter() - start, (max_move[0], max_move[1]))
    except Exception as exc:  # noqa: BLE001 — record, don't crash the series
        return MatchResult(-1, 0, moves, time.perf_counter() - start,
                           (max_move[0], max_move[1]), error=f"{type(exc).__name__}: {exc}")
    finally:
        battle_finish()


def run_series(agent_a: Agent, agent_b: Agent, deck_a: list[int], deck_b: list[int],
               n_games: int,
               on_game_end: "Callable[[int], None] | None" = None) -> SeriesStats:
    """Alternates seats: even games A is player 0, odd games B is player 0.
    on_game_end(game_index) is called after each completed game when provided."""
    stats = SeriesStats()
    for g in range(n_games):
        a_is_p0 = g % 2 == 0
        if a_is_p0:
            r = play_match(agent_a, agent_b, deck_a, deck_b)
        else:
            r = play_match(agent_b, agent_a, deck_b, deck_a)
        if r.error is not None:
            raise RuntimeError(f"game {g}: {r.error}")
        stats.game_seconds.append(r.seconds)
        stats.max_move_seconds = max(stats.max_move_seconds, *r.max_move_seconds)
        if r.winner == 2:
            stats.draws += 1
        elif (r.winner == 0) == a_is_p0:
            stats.wins_a += 1
        else:
            stats.wins_b += 1
        if on_game_end is not None:
            on_game_end(g)
    return stats
