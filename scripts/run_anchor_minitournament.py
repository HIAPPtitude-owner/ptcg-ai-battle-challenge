"""Mini-tournament: round-robin among the min-basics anchor candidates
(Task 5) plus a per-candidate strength-reference series against the OLD
anchor deck (mega-lucario-fighting).

Spec Section 2 (`.superpowers/sdd/2026-08-11-min-basics-pool-rule`):
Round-robin: C(4,2)=6 pairs x 200 games, heuristic-v0 both sides,
in-process. Crowning: highest pooled win rate; tie broken by direct
head-to-head. Record every candidate's WR vs the OLD anchor as a
strength reference (reference only, not a gate).

Follows `scripts/measure_floor_distribution.py`'s structure (sys.path
insert, live progress prints) but drives games through
`ptcg.arena.runner.run_series` (its `on_game_end` hook gives live
progress for free) rather than a hand-rolled `play_match` loop --
`run_series` already implements the exact alternating-seat convention
the spec calls for (even game index: A is player 0; odd: B is player 0,
winner mapping inverted accordingly).

Usage:
    uv run python scripts/run_anchor_minitournament.py
    uv run python scripts/run_anchor_minitournament.py --games-per-pair 20 --reference-games 20
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ptcg.agents.heuristic import HeuristicAgent  # noqa: E402
from ptcg.arena.runner import load_deck, run_series  # noqa: E402
from ptcg.arena.stats import SeriesStats  # noqa: E402

OLD_ANCHOR_PATH = ROOT / "src" / "ptcg" / "decks" / "candidates" / "mega-lucario-fighting.csv"
DEFAULT_CANDIDATES_GLOB = "src/ptcg/decks/candidates/anchor-cand-*.csv"
DEFAULT_GAMES_PER_PAIR = 200
DEFAULT_REFERENCE_GAMES = 200
PROGRESS_EVERY = 25
SECONDS_PER_GAME_ESTIMATE = 0.022  # observed in-process heuristic-v0 rate (see project CLAUDE.md)


def pooled_score(wins: int, draws: int, games: int) -> float:
    """Pooled win rate with draws counted 0.5 (symmetric argmax metric)."""
    return (wins + 0.5 * draws) / games


def pick_winner(scores: dict[str, float],
                head_to_head: dict[tuple[str, str], float]) -> str:
    """Highest pooled score; ties broken by direct head-to-head score
    (spec Section 2), then lexicographic candidate name (determinism)."""
    best = max(scores.values())
    tied = sorted(k for k, v in scores.items() if v == best)
    if len(tied) == 1:
        return tied[0]
    a, b = tied[0], tied[1]  # >2-way tie: compare the first two, winner stands
    return a if head_to_head.get((a, b), 0.5) >= 0.5 else b


@dataclass
class TournamentResult:
    """Pure tally of one mini-tournament run. `pair_stats`/`head_to_head`
    cover only the round-robin; `reference_stats` is the OLD-anchor
    strength reference (recorded, never a gate -- spec Section 2)."""
    pair_stats: dict[tuple[str, str], SeriesStats] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)
    head_to_head: dict[tuple[str, str], float] = field(default_factory=dict)
    winner: str = ""
    reference_stats: dict[str, SeriesStats] = field(default_factory=dict)


PlaySeries = Callable[[list[int], list[int], int], SeriesStats]


def _default_play_series(deck_a: list[int], deck_b: list[int], n_games: int) -> SeriesStats:
    return run_series(HeuristicAgent(), HeuristicAgent(), deck_a, deck_b, n_games)


def run_tournament(
    decks: dict[str, list[int]],
    games_per_pair: int,
    reference_deck: tuple[str, list[int]] | None = None,
    reference_games: int = 0,
    play_series: PlaySeries = _default_play_series,
) -> TournamentResult:
    """Round-robins every pair in `decks` for `games_per_pair` games each,
    tallies pooled win rate per candidate, picks the winner (spec Section
    2), then -- if `reference_deck` is given -- plays each candidate
    `reference_games` games against it as a strength REFERENCE (recorded
    in `reference_stats`, never consulted by `scores`/`winner`).

    Game-play is pure with respect to `play_series` (default: real
    HeuristicAgent-vs-HeuristicAgent play through `run_series`), so this
    function is testable end to end with synthetic `SeriesStats` -- no
    real games required.

    Degenerate case: a single-candidate `decks` dict has zero round-robin
    pairs (C(1,2) = 0); `play_series` is never called and that candidate
    is returned as the trivial winner with a placeholder 0.0 score (no
    games were played, so there is no win-rate evidence to report).
    """
    names = list(decks.keys())
    if len(names) == 1:
        only = names[0]
        return TournamentResult(scores={only: 0.0}, winner=only)

    pair_stats: dict[tuple[str, str], SeriesStats] = {}
    head_to_head: dict[tuple[str, str], float] = {}
    totals: dict[str, list[int]] = {name: [0, 0, 0] for name in names}  # [wins, draws, games]

    for a, b in itertools.combinations(names, 2):
        stats = play_series(decks[a], decks[b], games_per_pair)
        pair_stats[(a, b)] = stats
        head_to_head[(a, b)] = pooled_score(stats.wins_a, stats.draws, stats.n)
        head_to_head[(b, a)] = pooled_score(stats.wins_b, stats.draws, stats.n)
        totals[a][0] += stats.wins_a
        totals[a][1] += stats.draws
        totals[a][2] += stats.n
        totals[b][0] += stats.wins_b
        totals[b][1] += stats.draws
        totals[b][2] += stats.n

    scores = {name: pooled_score(w, d, g) for name, (w, d, g) in totals.items()}
    winner = pick_winner(scores, head_to_head)

    reference_stats: dict[str, SeriesStats] = {}
    if reference_deck is not None and reference_games > 0:
        _ref_name, ref_cards = reference_deck
        for name in names:
            reference_stats[name] = play_series(decks[name], ref_cards, reference_games)

    return TournamentResult(pair_stats=pair_stats, scores=scores,
                            head_to_head=head_to_head, winner=winner,
                            reference_stats=reference_stats)


def _make_progress_play_series(total_games: int) -> tuple[PlaySeries, list[int], float]:
    """Wraps the real `run_series` with a live per-game progress print,
    using its `on_game_end` hook -- the same "global game counter shared
    across every series" pattern as
    `measure_floor_distribution.play_series_vs_anchor`, but driven off
    `run_series` (which already implements the spec's alternating-seat
    convention) instead of a hand-rolled `play_match` loop."""
    counter = [0]
    start = time.perf_counter()

    def play_series(deck_a: list[int], deck_b: list[int], n_games: int) -> SeriesStats:
        def on_game_end(_g: int) -> None:
            counter[0] += 1
            if counter[0] % PROGRESS_EVERY == 0:
                elapsed = time.perf_counter() - start
                rate = elapsed / counter[0]
                print(
                    f"  progress: {counter[0]}/{total_games} games "
                    f"({elapsed:.1f}s elapsed, {rate:.3f}s/game avg)",
                    flush=True,
                )

        return run_series(HeuristicAgent(), HeuristicAgent(), deck_a, deck_b, n_games,
                          on_game_end=on_game_end)

    return play_series, counter, start


def main() -> None:
    # `reconfigure` exists on the real TextIOWrapper but not on the `TextIO`
    # protocol typeshed narrows sys.stdout to, hence the guard + ignore.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--games-per-pair", type=int, default=DEFAULT_GAMES_PER_PAIR,
                    help=f"games per round-robin pair (default {DEFAULT_GAMES_PER_PAIR})")
    p.add_argument("--reference-games", type=int, default=DEFAULT_REFERENCE_GAMES,
                    help=f"games per candidate vs the OLD anchor (default {DEFAULT_REFERENCE_GAMES})")
    p.add_argument("--candidates-glob", type=str, default=DEFAULT_CANDIDATES_GLOB,
                    help=f"glob for candidate deck CSVs (default {DEFAULT_CANDIDATES_GLOB})")
    args = p.parse_args()

    candidate_paths = sorted(ROOT.glob(args.candidates_glob))
    if not candidate_paths:
        raise SystemExit(f"no candidates matched glob: {args.candidates_glob}")
    decks = {path.stem: load_deck(path) for path in candidate_paths}
    print(f"anchor mini-tournament: {len(decks)} candidates -> {list(decks.keys())}")

    old_anchor_cards = load_deck(OLD_ANCHOR_PATH)
    reference_deck = (OLD_ANCHOR_PATH.stem, old_anchor_cards)

    n_pairs = len(decks) * (len(decks) - 1) // 2
    total_games = n_pairs * args.games_per_pair + len(decks) * args.reference_games
    print(
        f"round-robin: {n_pairs} pairs x {args.games_per_pair} games; "
        f"reference: {len(decks)} candidates x {args.reference_games} games vs "
        f"{reference_deck[0]}; total {total_games} games "
        f"(est. ~{total_games * SECONDS_PER_GAME_ESTIMATE:.0f}s "
        f"at {SECONDS_PER_GAME_ESTIMATE:.3f}s/game)"
    )

    play_series, counter, start = _make_progress_play_series(total_games)
    result = run_tournament(
        decks, args.games_per_pair,
        reference_deck=reference_deck, reference_games=args.reference_games,
        play_series=play_series,
    )

    date = dt.date.today().isoformat()

    print("\nround-robin pair results (EXPERIMENTS.md-format rows):")
    for (a, b), stats in result.pair_stats.items():
        print(f"  {a} vs {b}: {stats.wins_a}-{stats.wins_b}-{stats.draws}")
        print("  " + stats.markdown_row(date, "heuristic-v0", "heuristic-v0", a, b,
                                        "anchor mini-tournament round-robin"))

    print("\npooled scores (win rate, draws counted 0.5):")
    for name, score in sorted(result.scores.items(), key=lambda kv: -kv[1]):
        print(f"  {name}: {score:.3f}")

    print(f"\nstrength reference vs OLD anchor ({reference_deck[0]}) -- reference only, not a gate:")
    for name, stats in result.reference_stats.items():
        print(f"  {name} vs {reference_deck[0]}: {stats.wins_a}-{stats.wins_b}-{stats.draws} "
              f"wr={stats.win_rate_a:.3f}")
        print("  " + stats.markdown_row(date, "heuristic-v0", "heuristic-v0", name,
                                        reference_deck[0],
                                        "anchor mini-tournament reference vs OLD anchor"))

    total_elapsed = time.perf_counter() - start
    played = max(counter[0], 1)
    print(f"\ntotal wall-clock: {total_elapsed:.1f}s "
          f"({total_elapsed / played:.3f}s/game over {counter[0]} games)")

    print(f"\nWINNER: {result.winner}")


if __name__ == "__main__":
    main()
