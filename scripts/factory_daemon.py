"""CLI: run the continuous-training daemon with the 7A PerDeckNetTrainer."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ptcg.factory.daemon import run_daemon  # noqa: E402
from ptcg.factory.trainers import PerDeckNetTrainer  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--deck", required=True, help="deck csv the net is trained for")
    p.add_argument("--cycles", type=int, default=1)
    p.add_argument("--games-per-cycle", type=int, default=300)
    p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--ledger",
                   default=str(ROOT / "experiments/factory/candidates.json"))
    p.add_argument("--state",
                   default=str(ROOT / "experiments/factory/daemon_state.json"))
    p.add_argument("--stop-file",
                   default=str(ROOT / "experiments/factory/DAEMON_STOP"),
                   help="cooperative stop-file, checked between cycles and "
                        "between producer/consumer stages within a cycle "
                        "(Task-11 carry-forward: the daemon has no other "
                        "way to be stopped short of killing the process)")
    args = p.parse_args()

    priority = 0.3
    decision_path = ROOT / "experiments" / "factory" / "asym_decision.json"
    if decision_path.exists():
        doc = json.loads(decision_path.read_text(encoding="utf-8"))
        priority = 0.90 if doc.get("decision") == "positive" else 0.30

    trainer = PerDeckNetTrainer(deck=Path(args.deck),
                                games_per_cycle=args.games_per_cycle,
                                epochs=args.epochs, priority=priority)
    state = run_daemon(trainer, args.cycles, Path(args.ledger), Path(args.state),
                       stop_path=Path(args.stop_file))
    print(f"daemon done: completed cycles {state.completed}, "
          f"next_cycle={state.next_cycle}")


if __name__ == "__main__":
    main()
