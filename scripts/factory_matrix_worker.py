"""CLI: continuous matrix-tournament worker loop (spec compute-saturation,
Task 6). Repeatedly runs `worker_tick` (src/ptcg/factory/tournament.py),
sleeping 5s after a "paused"/"idle" tick and looping immediately after a
"played" tick, until `--max-ticks` is reached (0 = forever - the production
entry point; tests/acceptance pass a positive bound). Guarded by its own
single-instance lock (`matrix.worker.lock`) so a second concurrently-started
worker exits cleanly (exit 0, logs "busy") rather than racing the first for
the matrix ledger.

Precedent for testable functions living directly in a `scripts/*.py` module
(imported by tests as `from scripts.X import Y`, per `pyproject.toml`'s
`pythonpath = ["src", "."]`): scripts/factory_watch_once.py, scripts/
eval_value_net.py, scripts/package_submission.py.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ptcg.factory.cycle import FactoryPaths  # noqa: E402
from ptcg.factory.evolution import evolution_tick  # noqa: E402
from ptcg.factory.tournament import (  # noqa: E402
    BLOCK_GAMES, make_series_fn, worker_tick,
)
from ptcg.factory.watch import instance_lock, throttle_below_normal  # noqa: E402


def _dispatch_tick(paths: FactoryPaths, *, series_fn, log) -> str:
    """Run one worker tick. When the evolutionary agent-population pool
    exists (`agent_pool.json`), the steady-state `evolution_tick` (play/rate/
    cull/breed over the genome pools) owns the tick; otherwise the legacy
    candidate-ledger `worker_tick` runs, byte-identically to before. Both
    honor the same PAUSE/heartbeat/matrix-lock discipline and share the
    threaded `series_fn` (so `--block-games` flows through identically)."""
    if (paths.root / "experiments" / "factory" / "agent_pool.json").exists():
        return evolution_tick(paths, series_fn=series_fn, log=log)
    return worker_tick(paths, series_fn=series_fn, log=log)


def run_worker(paths: FactoryPaths, *, max_ticks: int = 0,
               block_games: int = BLOCK_GAMES, series_fn=None,
               log=print) -> str:
    """Loop `worker_tick` under the `matrix.worker.lock` single-instance
    guard. Returns the last tick's result ("paused"/"idle"/"played"), or
    "busy" if another worker process already holds the lock (no ticks run
    in that case).
    """
    throttle_below_normal(log)
    fn = series_fn if series_fn is not None else make_series_fn(block_games)
    try:
        with instance_lock(paths.matrix_worker_lock):
            n = 0
            result = "idle"
            while True:
                result = _dispatch_tick(paths, series_fn=fn, log=log)
                n += 1
                log(f"tick {n}: {result}")
                if max_ticks and n >= max_ticks:
                    return result
                time.sleep(0 if result == "played" else 5)
    except TimeoutError:
        log("busy: another matrix worker holds the lock")
        return "busy"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--max-ticks", type=int, default=0,
                   help="0 = loop forever (default); N = stop after N ticks")
    p.add_argument("--block-games", type=int, default=BLOCK_GAMES)
    args = p.parse_args()

    paths = FactoryPaths(root=ROOT)
    result = run_worker(paths, max_ticks=args.max_ticks, block_games=args.block_games)
    print(f"factory_matrix_worker result: {result}")


if __name__ == "__main__":
    main()
