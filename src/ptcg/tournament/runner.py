"""Plays one batch of games for one pairing. Crash-safe measurement policy."""

from dataclasses import dataclass, field
from typing import Callable

from ptcg.agents.base import Agent
from ptcg.arena.runner import MatchResult, play_match


class TournamentAbort(RuntimeError):
    """Raised when a pairing crashes repeatedly — standings must not be skewed."""


@dataclass
class BatchResult:
    wins_a: int = 0
    wins_b: int = 0
    draws: int = 0
    discarded: int = 0
    errors: list[str] = field(default_factory=list)


def play_batch(
    agent_factory: Callable[[list[int]], Agent],
    deck_a: list[int],
    deck_b: list[int],
    n_games: int,
    seat_offset: int,
    play_fn: Callable[..., MatchResult] = play_match,
    max_crashes: int = 3,
) -> BatchResult:
    out = BatchResult()
    for g in range(n_games):
        a_is_p0 = (seat_offset + g) % 2 == 0
        decks = (deck_a, deck_b) if a_is_p0 else (deck_b, deck_a)
        agents = (agent_factory(decks[0]), agent_factory(decks[1]))
        r = play_fn(agents[0], agents[1], decks[0], decks[1])
        if r.error is not None or r.winner == -1:
            out.discarded += 1
            out.errors.append(r.error or "unknown error")
            if out.discarded >= max_crashes:
                raise TournamentAbort(
                    f"{out.discarded} crashed games in one batch: {out.errors}"
                )
            continue
        if r.winner == 2:
            out.draws += 1
        elif (r.winner == 0) == a_is_p0:
            out.wins_a += 1
        else:
            out.wins_b += 1
    return out
