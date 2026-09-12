"""CLI: run the deck tournament and log standings to experiments/EXPERIMENTS.md."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptcg.agents.current import CURRENT_AGENT_NAME, make_current_agent  # noqa: E402
from ptcg.tournament.run import run_tournament  # noqa: E402


def main() -> None:
    # Windows console defaults to cp1252, which can't encode the mulligan-flag
    # warning glyph (⚠) in standings output; force UTF-8 for stdout/stderr.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser()
    p.add_argument("--candidates-dir", default="src/ptcg/decks/candidates")
    p.add_argument("--ledger", default="experiments/tournament/results.json")
    p.add_argument("--max-games", type=int, default=400)
    args = p.parse_args()

    standings, open_pairings, games_played = run_tournament(
        Path(args.candidates_dir),
        Path(args.ledger),
        CURRENT_AGENT_NAME,
        make_current_agent,
        max_games_session=args.max_games,
    )

    print(standings)

    if open_pairings == 0 and games_played > 0:
        log = ROOT / "experiments" / "EXPERIMENTS.md"
        log.write_text(
            log.read_text(encoding="utf-8") + "\n" + standings + "\n", encoding="utf-8"
        )
        print(f"Logged to {log}")
        print("TOURNAMENT COMPLETE")
    elif open_pairings == 0:
        print("TOURNAMENT COMPLETE (already logged — no games played this session)")
    else:
        print(f"TOURNAMENT INCOMPLETE ({open_pairings} open pairings — rerun to continue)")


if __name__ == "__main__":
    main()
