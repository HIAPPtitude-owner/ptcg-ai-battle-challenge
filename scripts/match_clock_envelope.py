"""D2: compute the max per-move budget that is provably timeout-safe under the
10-minute match clock, from D1's per-game decision distribution. Pure math, O(n log n)
for the percentile sort over at most a few hundred games — no engine, no arena."""
from __future__ import annotations

import math


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    rank = max(1, math.ceil(q * len(s)))  # nearest-rank, 1-indexed
    return s[rank - 1]


def safe_budget_ms(decisions_per_game_p95: float, match_seconds: float = 600.0,
                   safety_factor: float = 2.0, overshoot_ms: float = 0.0) -> float:
    """Largest per-move budget (ms) s.t. worst-case total search time over a game,
    inflated by safety_factor for unknown Kaggle hardware, fits the match clock.
    Subtracts the measured deadline overshoot (a single in-flight iteration is not
    interrupted at the deadline).

    The first argument is named "p95" because that was this function's original
    caller, but the formula is generic over any per-game decision count -- pass
    the observed MAX decisions/game for a provably-safe headline figure (every
    game in the sample stays under the clock), or p95 for a probabilistic
    sensitivity figure only. A max-decisions game can still blow a p95-sized
    budget (see D2 in experiments/ANALYSIS-slice5-search-architecture.md)."""
    if decisions_per_game_p95 <= 0:
        return 0.0
    budget_plus_overshoot = (match_seconds * 1000.0) / (decisions_per_game_p95 * safety_factor)
    return max(0.0, budget_plus_overshoot - overshoot_ms)
