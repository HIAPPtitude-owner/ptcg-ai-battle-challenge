"""CLI: compute the pre-registered asymmetric-test differentials from EXPERIMENTS.md."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptcg.factory.asym import compute, parse_experiments, write_decision  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--experiments", default=str(ROOT / "experiments" / "EXPERIMENTS.md"))
    p.add_argument("--write-decision", default=None,
                   help="path to write asym_decision.json (e.g. "
                        "experiments/factory/asym_decision.json)")
    args = p.parse_args()

    text = Path(args.experiments).read_text(encoding="utf-8")
    cells = parse_experiments(text)
    if not cells:
        raise SystemExit("no slice7a-asym cells found in EXPERIMENTS.md")
    result = compute(cells)

    print("| Cell | Pilot wins | Games | WR |")
    print("|------|-----------:|------:|----|")
    for cid in sorted(cells):
        c = cells[cid]
        print(f"| {cid} | {c.wins} | {c.games} | {c.wr:.3f} |")
    print()
    print("| Pairing-orientation | Differential (treatment - control) |")
    print("|---------------------|-------------------------------------|")
    for key in sorted(result.per_cell):
        print(f"| {key} | {result.per_cell[key]:+.3f} |")
    lo, hi = result.ci
    print()
    print(f"Pooled treatment WR: {result.pooled_treatment_wr:.4f}")
    print(f"Pooled control   WR: {result.pooled_control_wr:.4f}")
    print(f"Pooled differential: {result.differential:+.4f}  95% CI [{lo:+.4f}, {hi:+.4f}]")
    print(f"DECISION (pre-registered criteria): {result.decision.upper()}")
    if args.write_decision:
        write_decision(Path(args.write_decision), result)
        print(f"decision written -> {args.write_decision}")


if __name__ == "__main__":
    main()
