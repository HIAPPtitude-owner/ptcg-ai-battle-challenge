"""Measure the real win-rate-vs-anchor distribution of the 11 reseed decks
under heuristic-vs-heuristic play, so the floor gate's bar (currently
0.45/50) can be calibrated against the RESEEDED pool instead of assumed.

Spec-mandated empirical check (`.superpowers/sdd/
2026-08-03-tournament-breeding-anchor-pressure`). Reconstructs the exact
same 11 decks `scripts/reseed_tournament_pool.py` seeds (same TEMPLATES,
same `DEFAULT_SEED`, same `mutate_deck`/`validate_deck` call order) WITHOUT
touching any DB -- this script never opens `tournament.db`. Every deck is
then played `--games` times against the anchor deck (mega-lucario-fighting,
the same CSV `reseed_tournament_pool.TEMPLATES` uses), HeuristicAgent both
sides, alternating first player, using the repo's lightest in-process game
path (`ptcg.arena.runner.play_match` -- the same helper `run_series`/
`scripts/run_arena.py` use under the hood, just driven here one game at a
time so this script can print live per-game progress and per-deck tallies).

Usage:
    uv run python scripts/measure_floor_distribution.py --games 100
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ptcg.agents.heuristic import HeuristicAgent  # noqa: E402
from ptcg.arena.runner import play_match  # noqa: E402
from ptcg.decks.validate import validate_deck  # noqa: E402
from ptcg.factory.breeding import mutate_deck  # noqa: E402

from scripts.reseed_tournament_pool import (  # noqa: E402
    DEFAULT_SEED,
    MUTATION_ATTEMPTS,
    MUTATIONS_PER_TEMPLATE,
    RESEED_PREFIX,
    TEMPLATES,
    _mutation_concept_id,
    _read_deck_csv,
)

CALIBRATION_GAMES = 5
CALIBRATION_WARN_SECONDS = 5.0
PROGRESS_EVERY = 25
BARS = (0.40, 0.42, 0.45)
GATE_N = 50
#: Historical deck count under the pre-2026-08-11 rules (len(TEMPLATES) plus
#: up to MUTATIONS_PER_TEMPLATE successful mutations per template). The
#: min-basics pool rule (MIN_BASIC_CARDS=8, ptcg.decks.validate) makes most
#: mutate_deck() candidates fail validate_deck() inside build_reseed_decks(),
#: so a degraded run can silently reconstruct as few as len(TEMPLATES) decks
#: instead of 11 -- see the loud-fail check in main() below.
EXPECTED_DECK_COUNT = 11


def build_reseed_decks(seed: int = DEFAULT_SEED) -> list[tuple[str, list[int]]]:
    """Reproduce the exact (concept_id, cards) pairs that
    `reseed_tournament_pool.run_reseed` seeds into the DB, with ZERO DB
    access -- mirrors `run_reseed`'s `_apply` nested template/mutation loop
    verbatim (same rng consumption order, same `seeded_this_run` dedup,
    same attempt budget) but skips every DB write. Reuses the real
    `mutate_deck`/`validate_deck`/`_mutation_concept_id` functions, so a
    given `seed` reproduces the identical 11-deck set the live reseed
    would (or did) produce.
    """
    import random

    rng = random.Random(seed)
    decks: list[tuple[str, list[int]]] = []
    seeded_this_run: set[str] = set()
    for template_id, csv_path in TEMPLATES:
        cards = _read_deck_csv(csv_path)
        decks.append((template_id, cards))

        for slot in range(MUTATIONS_PER_TEMPLATE):
            child: list[int] | None = None
            mutation_id: str | None = None
            for _attempt in range(MUTATION_ATTEMPTS):
                candidate = mutate_deck(rng, list(cards))
                if candidate is None or validate_deck(candidate):
                    continue
                candidate_id = _mutation_concept_id(candidate)
                if candidate_id in seeded_this_run:
                    continue
                child, mutation_id = candidate, candidate_id
                break
            if child is None or mutation_id is None:
                continue
            seeded_this_run.add(mutation_id)
            decks.append((mutation_id, child))
    return decks


def binom_cdf_le(k: int, n: int, p: float) -> float:
    """Exact P(X <= k) for X ~ Binomial(n, p). stdlib only (math.comb)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    total = 0.0
    for i in range(0, k + 1):
        total += math.comb(n, i) * (p ** i) * ((1.0 - p) ** (n - i))
    return total


def false_fail_probability(measured_wr: float, bar: float, n: int = GATE_N) -> float:
    """P(a single n-game gate would report wr < bar) if the deck's TRUE
    win rate equals `measured_wr` -- i.e. the odds this deck would have
    been incorrectly failed by a one-shot n-game draw at this bar."""
    threshold = math.ceil(bar * n) - 1  # largest win count that FAILS (wins/n < bar)
    return binom_cdf_le(threshold, n, measured_wr)


def play_series_vs_anchor(
    deck_cards: list[int],
    anchor_cards: list[int],
    n_games: int,
    label: str,
    progress_counter: list[int],
    start_time: float,
) -> tuple[int, int, int, list[float]]:
    """Plays `n_games` of `deck_cards` vs `anchor_cards`, HeuristicAgent
    both sides, alternating who is player 0 each game (same alternation
    convention as `ptcg.arena.runner.run_series`). Returns
    (wins, draws, losses, game_seconds) from `deck_cards`'s perspective.
    `progress_counter` is a 1-element list shared across all decks so
    progress prints on a GLOBAL game count, not a per-deck one."""
    deck_agent = HeuristicAgent()
    anchor_agent = HeuristicAgent()
    wins = draws = losses = 0
    game_seconds: list[float] = []

    for g in range(n_games):
        deck_is_p0 = g % 2 == 0
        if deck_is_p0:
            r = play_match(deck_agent, anchor_agent, deck_cards, anchor_cards)
        else:
            r = play_match(anchor_agent, deck_agent, anchor_cards, deck_cards)
        if r.error is not None:
            raise RuntimeError(f"{label} game {g}: {r.error}")

        game_seconds.append(r.seconds)
        if r.winner == 2:
            draws += 1
        elif (r.winner == 0) == deck_is_p0:
            wins += 1
        else:
            losses += 1

        progress_counter[0] += 1
        if progress_counter[0] % PROGRESS_EVERY == 0:
            elapsed = time.perf_counter() - start_time
            rate = elapsed / progress_counter[0]
            played = wins + draws + losses
            print(
                f"  progress: {progress_counter[0]} games total "
                f"({elapsed:.1f}s elapsed, {rate:.3f}s/game avg) -- "
                f"current deck [{label}] {played}/{n_games} "
                f"(wr so far {wins / played:.3f})",
                flush=True,
            )

    return wins, draws, losses, game_seconds


def main() -> None:
    # `reconfigure` exists on the real TextIOWrapper but not on the `TextIO`
    # protocol typeshed narrows sys.stdout to, hence the guard + ignore.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--games", type=int, default=100,
                    help="games per deck vs the anchor (default 100)")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED,
                    help="rng seed for deck reconstruction (default matches reseed script)")
    args = p.parse_args()

    print(f"measure_floor_distribution: seed={args.seed} games_per_deck={args.games}")

    decks = build_reseed_decks(args.seed)
    print(f"reconstructed {len(decks)} reseed decks (expected {EXPECTED_DECK_COUNT})")
    if len(decks) < EXPECTED_DECK_COUNT:
        raise RuntimeError(
            f"measure_floor_distribution: only reconstructed {len(decks)} of "
            f"{EXPECTED_DECK_COUNT} expected reseed decks. This is almost "
            f"certainly the 2026-08-11 min-basics pool rule "
            f"(MIN_BASIC_CARDS=8 in ptcg.decks.validate) rejecting most "
            f"mutate_deck() candidates via validate_deck() inside "
            f"build_reseed_decks() -- not a fluke. Any bar this script "
            f"calibrates (FLOOR_BAR, ANCHOR_BAR) from a degraded deck set is "
            f"calibrated against the WRONG regime and must not be trusted; "
            f"re-derive TEMPLATES/mutation rules for the min-basics pool "
            f"before recalibrating (see "
            f".claude/rules/single-actor-worker-tests.md's "
            f"empirical-check-against-wrong-regime lesson)."
        )

    anchor_id = f"{RESEED_PREFIX}mega-lucario-fighting"
    anchor_cards = next((cards for cid, cards in decks if cid == anchor_id), None)
    if anchor_cards is None:
        raise RuntimeError(f"anchor deck '{anchor_id}' not found among reconstructed decks")
    print(f"anchor deck: {anchor_id}")

    # Calibration: 5 untimed-toward-results games of deck[0] vs anchor, to
    # warn early if the per-game rate makes the requested --games count
    # impractically slow, before committing to the full measurement.
    calib_label, calib_cards = decks[0]
    calib_start = time.perf_counter()
    calib_counter = [0]
    _, _, _, calib_seconds = play_series_vs_anchor(
        calib_cards, anchor_cards, CALIBRATION_GAMES, f"calibration:{calib_label}",
        calib_counter, calib_start,
    )
    calib_rate = sum(calib_seconds) / len(calib_seconds)
    print(f"calibration: {CALIBRATION_GAMES} games, {calib_rate:.3f}s/game avg")
    if calib_rate > CALIBRATION_WARN_SECONDS:
        est_total = calib_rate * args.games * len(decks)
        print(
            f"WARNING: measured {calib_rate:.2f}s/game exceeds the "
            f"{CALIBRATION_WARN_SECONDS:.0f}s/game budget -- estimated total wall-clock "
            f"for --games {args.games} across {len(decks)} decks is "
            f"~{est_total / 60:.1f} minutes. Consider a smaller --games."
        )

    results: list[tuple[str, int, int, float]] = []  # (deck_id, wins, games, wr)
    global_counter = [0]
    run_start = time.perf_counter()
    for deck_id, cards in decks:
        wins, draws, losses, _ = play_series_vs_anchor(
            cards, anchor_cards, args.games, deck_id, global_counter, run_start,
        )
        games = wins + draws + losses
        wr = wins / games if games else 0.0
        results.append((deck_id, wins, games, wr))
        print(f"{deck_id}: wins={wins} games={games} wr={wr:.3f} (draws={draws})", flush=True)

    total_wins = sum(w for _, w, _, _ in results)
    total_games = sum(g for _, _, g, _ in results)
    pooled_wr = total_wins / total_games if total_games else 0.0
    wrs = [wr for _, _, _, wr in results]
    print("")
    print(f"summary: pooled wr={pooled_wr:.3f} ({total_wins}/{total_games}) "
          f"min={min(wrs):.3f} max={max(wrs):.3f}")

    print("")
    print(f"implied false-fail probability at n={GATE_N} per bar (deck's measured wr "
          f"treated as its true strength):")
    header = "deck_id".ljust(40) + "measured_wr".rjust(12) + "".join(
        f"bar={b:.2f}".rjust(12) for b in BARS
    )
    print(header)
    for deck_id, _, _, wr in results:
        row = deck_id.ljust(40) + f"{wr:.3f}".rjust(12)
        for bar in BARS:
            ffp = false_fail_probability(wr, bar)
            row += f"{ffp:.3f}".rjust(12)
        print(row)

    total_elapsed = time.perf_counter() - run_start
    print("")
    print(f"total measurement wall-clock: {total_elapsed:.1f}s "
          f"({total_elapsed / total_games:.3f}s/game avg over {total_games} games)")


if __name__ == "__main__":
    main()
