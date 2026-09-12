"""Asymmetric-pairing discriminating test: pre-registered differential computation.

Design + decision criteria: experiments/ANALYSIS-slice7a-asymmetric-test.md (locked
BEFORE any runs). Cells come from experiments/EXPERIMENTS.md rows whose notes carry
`slice7a-asym-<P#>-<A|B>-<T|C>`. Rows containing VOID are skipped; replicated cells
accumulate (pooled) by design.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

CELL_RE = re.compile(r"(P\d-[AB]-[TC])\b")


@dataclass
class Cell:
    cell_id: str  # e.g. "P1-A-T"
    wins: int     # agent-a (pilot-side) wins
    games: int

    @property
    def wr(self) -> float:
        return self.wins / self.games if self.games else 0.0


@dataclass
class AsymResult:
    pooled_treatment_wr: float
    pooled_control_wr: float
    differential: float
    ci: tuple[float, float]
    per_cell: dict[str, float] = field(default_factory=dict)  # "P1-A" -> per-cell diff
    decision: str = "flat"  # "positive" | "flat" | "negative"


def diff_ci(wins_t: int, n_t: int, wins_c: int, n_c: int,
            z: float = 1.96) -> tuple[float, tuple[float, float]]:
    """Normal-approx CI for a difference of two independent proportions."""
    pt, pc = wins_t / n_t, wins_c / n_c
    se = math.sqrt(pt * (1 - pt) / n_t + pc * (1 - pc) / n_c)
    d = pt - pc
    return d, (d - z * se, d + z * se)


def compute(cells: dict[str, Cell]) -> AsymResult:
    treat = [c for cid, c in cells.items() if cid.endswith("-T")]
    ctrl = [c for cid, c in cells.items() if cid.endswith("-C")]
    if not treat or not ctrl:
        raise ValueError("need at least one treatment and one control cell")
    wins_t, n_t = sum(c.wins for c in treat), sum(c.games for c in treat)
    wins_c, n_c = sum(c.wins for c in ctrl), sum(c.games for c in ctrl)
    d, ci = diff_ci(wins_t, n_t, wins_c, n_c)
    per_cell: dict[str, float] = {}
    for cid, cell in cells.items():
        if cid.endswith("-T"):
            control = cells.get(cid[:-2] + "-C")
            if control is not None:
                per_cell[cid[:-2]] = cell.wr - control.wr
    if ci[0] > 0:
        decision = "positive"
    elif ci[1] < 0:
        decision = "negative"
    else:
        decision = "flat"
    return AsymResult(wins_t / n_t, wins_c / n_c, d, ci, per_cell, decision)


def parse_experiments(md_text: str, prefix: str = "slice7a-asym-") -> dict[str, Cell]:
    """Accumulate cells from EXPERIMENTS.md rows. Row shape (stats.markdown_row):
    | date | A | B | deckA | deckB | games | winsA | winsB | draws | wr [ci] | avg | max | notes |
    """
    cells: dict[str, Cell] = {}
    for line in md_text.splitlines():
        if not line.startswith("|") or prefix not in line or "VOID" in line:
            continue
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) < 13:
            continue
        m = CELL_RE.search(parts[12])
        if m is None or prefix + m.group(1) not in parts[12]:
            continue
        cell_id = m.group(1)
        wins, games = int(parts[6]), int(parts[5])
        if cell_id in cells:
            cells[cell_id] = Cell(cell_id, cells[cell_id].wins + wins,
                                  cells[cell_id].games + games)
        else:
            cells[cell_id] = Cell(cell_id, wins, games)
    return cells


def write_decision(path: Path, result: AsymResult) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "decision": result.decision,
        "differential": result.differential,
        "ci": list(result.ci),
        "pooled_treatment_wr": result.pooled_treatment_wr,
        "pooled_control_wr": result.pooled_control_wr,
        "per_cell": result.per_cell,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    os.replace(tmp, path)
