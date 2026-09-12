"""CLI: render the factory status dashboard on demand (design spec 2026-07-20).

Writes a self-contained HTML page to <root>/experiments/factory/dashboard.html.
Read-only: touches no factory decision state. Run between watch firings to
inspect current state without waiting for the next 15-minute cycle."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ptcg.factory import dashboard  # noqa: E402


def render_for_root(root: Path) -> Path:
    return dashboard.write_dashboard(Path(root))


def main() -> None:
    p = argparse.ArgumentParser(description="Render the factory status dashboard.")
    p.add_argument("--root", type=Path, default=ROOT,
                   help="repo root (defaults to this repo)")
    args = p.parse_args()
    out = render_for_root(args.root)
    print(f"dashboard written: {out}")


if __name__ == "__main__":
    main()
