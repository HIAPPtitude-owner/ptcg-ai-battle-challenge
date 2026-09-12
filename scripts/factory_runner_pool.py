"""CLI: manual/dev invocation of the parallel game-runner pool (tournament T6).

Containment (Slice Containment Strategy §3, plan
`docs/superpowers/plans/2026-07-23-generational-champion-tournament.md`): this
is NOT registered as a scheduled task this slice -- every dev/smoke run is a
MANUAL, foreground invocation against an EXPLICIT `--db` path. Never point
`--db` at the production `experiments/factory/tournament.db`; that file is
created only at go-live (T21).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ptcg.factory import runner_pool  # noqa: E402

#: Founding v0.1 agent (Global Constraints: ISMCTS-only; founding net
#: RATIFIED 2026-07-23). Used when --agent-configs is not supplied, so a
#: bare manual smoke run (e.g. a mirror match) needs no extra plumbing.
DEFAULT_AGENT_CONFIGS: dict[str, dict[str, object]] = {
    "v0.1": {
        "agent_kind": "search-net",
        "agent_config": {"net_weights": "src/ptcg/search/value_net_weights_v2.json"},
    },
}


def main() -> None:
    p = argparse.ArgumentParser(description="Run the parallel game-runner pool.")
    p.add_argument("--db", type=Path, required=True,
                   help="explicit SQLite db path (never the production tournament.db)")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--max-games", type=int, default=None,
                   help="cap games played per worker (default: unset, no cap)")
    p.add_argument("--loop-forever", action="store_true",
                   help="keep polling for new games instead of exiting once the queue "
                        "is empty (default: exit when empty, for manual smoke runs)")
    p.add_argument("--agent-configs", type=Path, default=None,
                   help="JSON file mapping agent_version -> {agent_kind, agent_config} "
                        "(default: the founding v0.1 search-net config)")
    args = p.parse_args()

    agent_configs = DEFAULT_AGENT_CONFIGS
    if args.agent_configs is not None:
        agent_configs = json.loads(args.agent_configs.read_text(encoding="utf-8"))

    runner_pool.spawn_pool(
        str(args.db), args.workers, agent_configs, ROOT,
        stop_when_empty=not args.loop_forever, max_games=args.max_games,
    )
    print(f"runner pool finished against {args.db}")


if __name__ == "__main__":
    main()
