"""Per-move time budgeting under the 10-minute match cap (timeout = loss)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TimeManager:
    """Allocates per-decision search budget from the remaining match clock.

    total_s defaults to 480 (8 of the 10 minutes; the margin covers engine
    time, I/O, and a 2-3x slower Kaggle machine).
    """

    total_s: float = 480.0
    min_move_s: float = 0.05
    max_move_s: float = 1.5
    est_total_moves: int = 13  # Task-1 spike: median 25 decisions/game / 2
    moves_floor: int = 10
    spent_s: float = 0.0
    moves_done: int = 0

    def move_budget(self) -> float:
        remaining = max(self.total_s - self.spent_s, 0.0)
        est_remaining = max(self.est_total_moves - self.moves_done, self.moves_floor)
        return min(self.max_move_s, max(self.min_move_s, remaining / est_remaining))

    def note_move(self, seconds: float) -> None:
        self.spent_s += seconds
        self.moves_done += 1
