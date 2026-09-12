"""CLI: the tournament loop scheduler -- `ptcg-factory-scheduler`'s entrypoint
(tournament T16).

Loops `loop_scheduler.loop_tick` on a cadence, honoring the `PAUSE` kill switch
exactly as every other factory worker does, under a single-instance lock so a
second concurrently-started scheduler exits cleanly rather than racing the
first. Crash-safe: ALL loop state lives in SQLite, so a killed scheduler
resumes on restart with no lost work (`loop_tick` reads pure DB state, no
in-memory state).

Never registered as a Windows Scheduled Task until T21 (post-merge go-live).
Every dev/smoke run is a MANUAL foreground invocation against an EXPLICIT temp
`--db` path -- never the production `tournament.db` (plan Slice Containment
Strategy). This module is NEW and unreachable from `scripts/factory_watch_once.py`'s
import graph, so a 15-minute watch-loop firing keeps running current behavior.

TRAIN ownership (JUDGMENT CALL -- see `loop_scheduler.loop_tick`): `--pipeline-target`
defaults to 0, i.e. the scheduler does NOT breed offspring -- the separate
`ptcg-factory-trainer` worker (retargeted to the offspring-faucet in T20) owns
the TRAIN faucet. Pass `--pipeline-target N` (N>0) to also breed inside the
scheduler using the production `PerDeckNetTrainer`; T20 settles faucet
ownership.

Precedent for testable functions living directly in a `scripts/*.py` module
(imported by tests as `from scripts.X import Y`, per `pyproject.toml`'s
`pythonpath = ["src", "."]`): `scripts/factory_matrix_worker.py`,
`scripts/factory_watch_once.py`, `scripts/run_census.py`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
import sys
import time
import traceback
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ptcg.factory import census, deckdb, loop_scheduler  # noqa: E402
from ptcg.factory.cycle import FactoryPaths  # noqa: E402
from ptcg.factory.watch import (  # noqa: E402
    append_watch_log,
    instance_lock,
    throttle_below_normal,
)

#: Seconds to wait between ticks that did no forward progress / were paused.
POLL_INTERVAL_S = 10.0

#: Single-instance lock for the scheduler (sibling of the matrix/trainer/UI
#: worker locks under experiments/factory/).
SCHEDULER_LOCK = ROOT / "experiments" / "factory" / "tournament_scheduler.lock"

#: Persisted crash trail for the scheduler worker. `ptcg-factory-scheduler` is
#: a LOOP-FOREVER worker under a 15-minute `MultipleInstances=IgnoreNew`
#: watchdog: an unhandled `loop_tick` exception would kill the process, the
#: watchdog would respawn it, and it would die again on the same poisoned row
#: -- a silent crash-loop whose only symptom is a `Running` task making no
#: progress. Per `.claude/rules/factory-task-scheduler-liveness.md`'s
#: terminal-marker rule, every tick failure gets a LOUD persisted line here
#: (utf-8) and the loop CONTINUES; the process never dies on a tick error.
SCHEDULER_LOG = ROOT / "experiments" / "factory" / "logs" / "scheduler.log"

#: Terminal marker written for a tick that raised. Grep this to distinguish a
#: crash-looping scheduler from a merely idle one (a healthy scheduler writes
#: none of these).
TICK_ERROR_MARKER = "scheduler-error:"


def _utc_now() -> dt.datetime:
    """tz-aware UTC now -- `loop_tick` compares it against ISO `claimed_at`
    strings and the persisted refresh timestamp, so it MUST carry the same
    `+00:00` offset the queue writes (`deckdb.claim_next_game`)."""
    return dt.datetime.now(dt.timezone.utc)


def seed_if_empty(conn, log=print) -> bool:
    """Idempotently seed the Founding Census (all single-core + all pair
    concepts) if the DB has no concepts yet. Returns True if it seeded this
    call. Heavy (~332k rows) but one-time -- the founding enumeration the plan
    requires in-slice; a warm DB no-ops."""
    if conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] > 0:
        return False
    stats = census.seed_census(conn)
    pairs = census.seed_pair_concepts(conn)
    log(f"scheduler: seeded census -- {stats} + {pairs} pair concepts")
    return True


def _production_trainer_factory() -> Callable[[Path], object]:
    """Lazily build the production per-deck trainer factory (only reached when
    `--pipeline-target > 0`, so its `torch`/subprocess dependency never loads
    for the default TRAIN-off scheduler)."""
    from ptcg.factory.trainers import PerDeckNetTrainer

    return lambda deck: PerDeckNetTrainer(deck=Path(deck))


def run_scheduler(
    db_path: str | Path,
    paths: FactoryPaths,
    *,
    max_ticks: int = 0,
    interval_s: float = POLL_INTERVAL_S,
    pipeline_target: int = 0,
    trainer_factory: Callable[..., object] | None = None,
    rng: random.Random | None = None,
    now_fn: Callable[[], dt.datetime] = _utc_now,
    lock_path: Path = SCHEDULER_LOCK,
    log_path: Path = SCHEDULER_LOG,
    log=print,
) -> str:
    """Loop `loop_tick` under the single-instance lock, honoring `PAUSE`.

    Returns the last tick's `phase` (`census`/`bootstrap`/`bootstrap_wait`/
    `loop`), `"paused"` if the last iteration was paused, `"error"` if the last
    iteration's tick raised, or `"busy"` if another scheduler already holds the
    lock. `max_ticks=0` loops forever (the production entry point);
    tests/smokes pass a positive bound. `now_fn` is a seam so tests can drive
    deterministic timestamps.

    Tick-level crash isolation (Pass-2 fix, terminal-marker-liveness rule): a
    `loop_tick` that raises is NOT allowed to kill the process. This worker is
    respawned by a 15-minute watchdog trigger, so a propagating exception on a
    poisoned DB row becomes an invisible crash-loop -- the task reports
    `Running`, `LastTaskResult` stays 0, and no work happens. Instead the
    exception is caught, written as a loud `scheduler-error:` line (with
    traceback) to `log_path`, and the loop CONTINUES to the next tick. Only
    `TimeoutError` propagates -- that is the instance-lock's own busy signal,
    handled by the outer handler, and must never be mistaken for a tick fault.
    """
    throttle_below_normal(log)
    rng = rng if rng is not None else random.Random()
    conn = deckdb.connect(Path(db_path))

    try:
        with instance_lock(lock_path):
            n = 0
            last = "idle"
            while True:
                if paths.pause_file.exists():
                    last = "paused"
                    log("scheduler: paused (PAUSE present)")
                else:
                    try:
                        result = loop_scheduler.loop_tick(
                            conn, rng, trainer_factory, now_fn(),
                            pipeline_target=pipeline_target,
                        )
                    except TimeoutError:
                        raise  # instance-lock busy signal -- outer handler owns it
                    except Exception as exc:  # noqa: BLE001 - crash isolation
                        # Loud + persisted, then keep looping: dying here would
                        # hand the watchdog a crash-loop with no visible symptom.
                        msg = (f"{TICK_ERROR_MARKER} tick {n + 1} "
                               f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
                        append_watch_log(log_path, msg)
                        log(f"scheduler: {msg}")
                        last = "error"
                    else:
                        last = result["phase"]
                        log(f"scheduler: tick {n + 1} -> {result}")
                n += 1
                if max_ticks and n >= max_ticks:
                    return last
                time.sleep(interval_s)
    except TimeoutError:
        log("scheduler: busy -- another scheduler holds the lock")
        return "busy"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db", required=True,
        help="Path to the tournament DB. Never the production tournament.db in dev/smoke.",
    )
    p.add_argument(
        "--seed", action="store_true", help="Seed the Founding Census if the DB is empty."
    )
    p.add_argument(
        "--max-ticks", type=int, default=0,
        help="0 = loop forever (default); N = stop after N.",
    )
    p.add_argument("--interval", type=float, default=POLL_INTERVAL_S, dest="interval_s")
    p.add_argument(
        "--pipeline-target", type=int, default=0, dest="pipeline_target",
        help="In-flight offspring target for the in-scheduler TRAIN faucet "
             "(0 = TRAIN off; the separate ptcg-factory-trainer worker owns the faucet).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    paths = FactoryPaths(root=ROOT)

    if args.seed:
        conn = deckdb.connect(Path(args.db))
        deckdb.init_db(conn)
        seed_if_empty(conn)

    trainer_factory = _production_trainer_factory() if args.pipeline_target > 0 else None
    result = run_scheduler(
        args.db,
        paths,
        max_ticks=args.max_ticks,
        interval_s=args.interval_s,
        pipeline_target=args.pipeline_target,
        trainer_factory=trainer_factory,
    )
    print(f"factory_tournament_scheduler result: {result}")


if __name__ == "__main__":
    main()
