"""CLI: continuous trainer worker loop (spec compute-saturation, Task 7).
Repeatedly runs `trainer_tick` (src/ptcg/factory/trainer_worker.py),
sleeping 60s after a "paused"/"idle"/"disk-capped"/"error" tick (training
cycles are expensive - no need to hammer the ledger between them) and
looping immediately after a "trained" tick, until `--max-ticks` is reached
(0 = forever - the production entry point; tests/acceptance pass a positive
bound). Guarded by its own single-instance lock (`trainer.worker.lock`) so
a second concurrently-started worker exits cleanly (exit 0, logs "busy")
rather than racing the first for the candidate ledger.

A training failure inside one tick (`run_daemon`'s own re-raise contract,
see trainer_worker.py) is caught here, logged, and treated as a non-fatal
tick so the loop survives to the next firing instead of crashing the whole
scheduled task.

Precedent for testable functions living directly in a `scripts/*.py` module
(imported by tests as `from scripts.X import Y`, per `pyproject.toml`'s
`pythonpath = ["src", "."]`): scripts/factory_matrix_worker.py,
scripts/factory_watch_once.py.
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ptcg.factory.cycle import FactoryPaths  # noqa: E402
from ptcg.factory.trainer_worker import trainer_tick  # noqa: E402
from ptcg.factory.watch import instance_lock, throttle_below_normal  # noqa: E402


def run_worker(paths: FactoryPaths, *, max_ticks: int = 0,
               trainer_factory=None, log=print) -> str:
    """Loop `trainer_tick` under the `trainer.worker.lock` single-instance
    guard. Returns the last tick's result ("paused"/"idle"/"disk-capped"/
    "trained"/"error"), or "busy" if another worker process already holds
    the lock (no ticks run in that case)."""
    throttle_below_normal(log)
    try:
        with instance_lock(paths.trainer_worker_lock):
            n = 0
            result = "idle"
            while True:
                try:
                    result = trainer_tick(paths, trainer_factory=trainer_factory, log=log)
                except Exception:
                    log(f"trainer tick failed:\n{traceback.format_exc(limit=5)}")
                    result = "error"
                n += 1
                log(f"tick {n}: {result}")
                if max_ticks and n >= max_ticks:
                    return result
                time.sleep(0 if result == "trained" else 60)
    except TimeoutError:
        log("busy: another trainer worker holds the lock")
        return "busy"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--max-ticks", type=int, default=0,
                   help="0 = loop forever (default); N = stop after N ticks")
    args = p.parse_args()

    paths = FactoryPaths(root=ROOT)
    result = run_worker(paths, max_ticks=args.max_ticks)
    print(f"factory_trainer_worker result: {result}")


if __name__ == "__main__":
    main()
