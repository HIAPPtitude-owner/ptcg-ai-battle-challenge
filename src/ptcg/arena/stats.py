"""Series statistics: win-rates with Wilson confidence intervals, timing."""
from __future__ import annotations

import math
from dataclasses import dataclass, field


def _esc(s: str) -> str:
    return str(s).replace("|", "\\|")


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


@dataclass
class SeriesStats:
    wins_a: int = 0
    wins_b: int = 0
    draws: int = 0
    game_seconds: list[float] = field(default_factory=list)
    max_move_seconds: float = 0.0

    @property
    def n(self) -> int:
        return self.wins_a + self.wins_b + self.draws

    @property
    def win_rate_a(self) -> float:
        return self.wins_a / self.n if self.n else 0.0

    @property
    def ci_a(self) -> tuple[float, float]:
        return wilson_ci(self.wins_a, self.n)

    def markdown_row(self, date: str, agent_a: str, agent_b: str,
                     deck_a: str, deck_b: str, notes: str) -> str:
        lo, hi = self.ci_a
        avg_s = sum(self.game_seconds) / len(self.game_seconds) if self.game_seconds else 0.0
        date, agent_a, agent_b, deck_a, deck_b, notes = (
            _esc(date), _esc(agent_a), _esc(agent_b), _esc(deck_a), _esc(deck_b), _esc(notes)
        )
        return (
            f"| {date} | {agent_a} | {agent_b} | {deck_a} | {deck_b} | {self.n} "
            f"| {self.wins_a} | {self.wins_b} | {self.draws} "
            f"| {self.win_rate_a:.3f} [{lo:.3f}, {hi:.3f}] "
            f"| {avg_s:.2f} | {self.max_move_seconds:.3f} | {notes} |"
        )
