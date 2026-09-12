"""Top-level tournament orchestration: discover candidate decks, sync the
ledger, play adaptive-sampling batches within a session budget, and render
standings.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ptcg.agents.base import Agent
from ptcg.arena.runner import MatchResult, load_deck, play_match
from ptcg.decks.mulligan import mulligan_rate
from ptcg.tournament.ledger import Ledger, deck_hash
from ptcg.tournament.runner import play_batch
from ptcg.tournament.scheduler import classify, ensure_pairings, next_batch
from ptcg.tournament.standings import standings_markdown


def _discover_decks(candidates_dir: Path) -> dict[str, tuple[str, list[int]]]:
    """Load every *.csv in candidates_dir, keyed by content hash.

    Raises ValueError naming both files if two candidates hash identically
    (duplicate deck content under different filenames).
    """
    decks: dict[str, tuple[str, list[int]]] = {}
    for path in sorted(Path(candidates_dir).glob("*.csv")):
        deck = load_deck(path)
        h = deck_hash(deck)
        if h in decks:
            other_name, _ = decks[h]
            raise ValueError(
                f"Duplicate deck content: {path.name} and {other_name} hash to {h}"
            )
        decks[h] = (path.name, deck)
    return decks


def run_tournament(
    candidates_dir: Path,
    ledger_path: Path,
    agent_name: str,
    agent_factory: Callable[[list[int]], Agent],
    max_games_session: int,
    play_fn: Callable[..., MatchResult] = play_match,
) -> tuple[str, int, int]:
    decks = _discover_decks(candidates_dir)
    active = {h: name for h, (name, _deck) in decks.items()}

    ledger = Ledger.load(ledger_path)
    ledger.sync(active, agent_name)
    ensure_pairings(ledger, list(active.keys()), agent_name)
    ledger.save(ledger_path)

    games_played = 0
    while games_played < max_games_session:
        batch = next_batch(ledger)
        if batch is None:
            break
        (hash_a, hash_b), batch_size = batch
        batch_size = min(batch_size, max_games_session - games_played)
        if batch_size <= 0:
            break
        rec = ledger.pairings[(hash_a, hash_b)]
        _, deck_a = decks[hash_a]
        _, deck_b = decks[hash_b]
        result = play_batch(
            agent_factory, deck_a, deck_b, batch_size,
            seat_offset=rec.games, play_fn=play_fn,
        )
        ledger.record(
            hash_a, hash_b, agent_name,
            result.wins_a, result.wins_b, result.draws, result.discarded,
        )
        ledger.save(ledger_path)
        games_played += batch_size

    mulligan = {h: mulligan_rate(deck) for h, (_name, deck) in decks.items()}
    open_pairings = sum(1 for rec in ledger.pairings.values()
                        if classify(rec) == "open")
    return standings_markdown(ledger, mulligan), open_pairings, games_played
