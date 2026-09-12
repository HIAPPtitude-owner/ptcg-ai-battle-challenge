"""Run N policy-improvement daemon cycles synchronously (gen-1 in-session;
nightly reuse). Dedicated state file — NEVER share daemon_state.json with the
7A per-deck trainer."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptcg.factory.daemon import run_daemon  # noqa: E402
from ptcg.factory.trainers import PolicyImprovementTrainer  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cycles", type=int, default=1)
    p.add_argument("--deck",
                   default="src/ptcg/decks/candidates/mega-lucario-fighting.csv")
    p.add_argument("--games", type=int, default=400)
    p.add_argument("--epochs", type=int, default=40)
    p.add_argument("--ledger",
                   default="experiments/factory/candidates.json")
    p.add_argument("--state",
                   default="experiments/factory/policy_daemon_state.json")
    p.add_argument("--stop-file",
                   default="experiments/factory/PAUSE")
    args = p.parse_args()
    trainer = PolicyImprovementTrainer(deck=Path(args.deck),
                                       games_per_cycle=args.games,
                                       epochs=args.epochs)
    state = run_daemon(trainer, args.cycles, ROOT / args.ledger,
                       ROOT / args.state, stop_path=ROOT / args.stop_file)
    print(f"next_cycle={state.next_cycle} completed={state.completed}")


if __name__ == "__main__":
    main()
