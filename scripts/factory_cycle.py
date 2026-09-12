"""CLI: run one factory cycle (harvest -> evaluate -> gate -> submit -> digest).

Nightly entry point for Windows Task Scheduler; also run on demand. Tee-logs to
experiments/factory/logs/ so scheduled runs are debuggable after the fact."""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ptcg.factory.cycle import FactoryPaths, run_cycle  # noqa: E402
from ptcg.factory.evaluate import EvalConfig  # noqa: E402
from ptcg.factory.kaggle_client import KaggleClient  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--no-submit", action="store_true",
                   help="dry-run: build+verify+gate but never upload")
    p.add_argument("--games", type=int, default=150)
    p.add_argument("--budget-minutes", type=float, default=240.0)
    p.add_argument("--max-candidates", type=int, default=None)
    p.add_argument("--cadence", type=int, default=5)
    args = p.parse_args()

    paths = FactoryPaths(root=ROOT)
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = paths.log_dir / f"cycle-{dt.datetime.now():%Y%m%d-%H%M%S}.log"

    with log_path.open("a", encoding="utf-8") as log_file:
        def log(msg: str) -> None:
            print(msg, flush=True)
            log_file.write(msg + "\n")
            log_file.flush()

        cfg = EvalConfig(games=args.games, budget_minutes=args.budget_minutes,
                         max_candidates=args.max_candidates)
        result = run_cycle(paths, KaggleClient(), cfg, no_submit=args.no_submit,
                           cadence_per_day=args.cadence, log=log)
        log(f"cycle result: { {k: v for k, v in result.items() if k != 'harvest'} }")


if __name__ == "__main__":
    main()
