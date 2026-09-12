"""Pure series-level aggregation of per-decision SearchStats. No engine import,
so it stays unit-testable and import-cheap. Complexity is O(n_decisions): a single
linear pass over the flat decision list (n = decisions/game * games, ~hundreds to
low-thousands per series), plus O(n_games) over per-game counts."""
from __future__ import annotations

from dataclasses import dataclass

from ptcg.search.searcher import SearchStats


@dataclass
class DecisionSummary:
    n_decisions: int
    n_gated: int
    deviation_rate: float
    gate_blocked_rate: float
    mean_iterations: float
    decisions_per_game_mean: float
    begin_failures: int
    step_failures: int


def summarize(stats: list[SearchStats], per_game_counts: list[int]) -> DecisionSummary:
    n = len(stats)                                        # O(n) single pass below
    gated = [s for s in stats if s.v0_sig is not None]
    n_gated = len(gated)
    deviations = sum(1 for s in gated if s.deviated)
    blocked = sum(1 for s in gated if s.gate_blocked)
    total_iters = sum(s.iterations for s in stats)
    n_games = len(per_game_counts)
    return DecisionSummary(
        n_decisions=n,
        n_gated=n_gated,
        deviation_rate=(deviations / n_gated) if n_gated else 0.0,
        gate_blocked_rate=(blocked / n_gated) if n_gated else 0.0,
        mean_iterations=(total_iters / n) if n else 0.0,
        decisions_per_game_mean=(n / n_games) if n_games else 0.0,
        begin_failures=sum(s.begin_failures for s in stats),
        step_failures=sum(s.step_failures for s in stats),
    )


def format_note(summary: DecisionSummary) -> str:
    """Compact, pipe-free suffix for the EXPERIMENTS.md Notes cell."""
    return (f"dev={summary.deviation_rate:.1%} "
            f"gate_blk={summary.gate_blocked_rate:.1%} "
            f"iters={summary.mean_iterations:.1f} "
            f"dec/g={summary.decisions_per_game_mean:.1f} "
            f"gated={summary.n_gated}/{summary.n_decisions}")
