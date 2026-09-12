"""Continuous-training daemon (spec S8): producer-consumer overlap behind a
pluggable Trainer interface. 7A occupant: PerDeckNetTrainer; 7B swaps in the
policy-improvement trainer behind the same Protocol with no factory rework."""
from __future__ import annotations

import json
import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from ptcg.factory.candidates import Candidate, load_ledger, merge_save


class Trainer(Protocol):
    name: str

    def prepare_data(self, cycle: int) -> Path: ...      # CPU producer

    def train(self, data_path: Path, cycle: int) -> Path: ...  # GPU consumer

    def export_and_register(self, weights_path: Path, cycle: int,
                            candidates: list[Candidate]) -> Candidate: ...


@dataclass
class DaemonState:
    next_cycle: int = 0
    completed: list = field(default_factory=list)
    last_error: str | None = None


def load_state(path: Path) -> DaemonState:
    path = Path(path)
    if not path.exists():
        return DaemonState()
    doc = json.loads(path.read_text(encoding="utf-8"))
    return DaemonState(next_cycle=int(doc.get("next_cycle", 0)),
                       completed=list(doc.get("completed", [])),
                       last_error=doc.get("last_error"))


def save_state(path: Path, state: DaemonState) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
    os.replace(tmp, path)


def run_daemon(trainer: Trainer, n_cycles: int, ledger_path: Path,
               state_path: Path, log: Callable = print,
               stop_path: Path | None = None) -> DaemonState:
    """Pipelined loop: while train(N) runs on the GPU worker, prepare_data(N+1)
    runs on the CPU worker. On any failure: persist state (next_cycle unchanged
    -> a rerun resumes at the failed cycle) and re-raise (crash isolation is
    per-run; a broken trainer must not silently spin).

    `stop_path`, when given, is a cooperative stop-file (Task-11 carry-forward
    finding: the daemon previously had no way to be stopped short of killing
    the process). It is checked at two points so a stop request is honored
    promptly rather than only between whole multi-stage cycles: (1) between
    cycles, before starting the next one's producer/consumer work, and
    (2) between the producer and consumer stages WITHIN a cycle, after
    prepare_data has returned but before train is submitted. Either
    checkpoint exits the loop cleanly (no exception, state persisted exactly
    as of the last fully-completed cycle) so a rerun resumes at next_cycle."""
    state = load_state(state_path)
    first = state.next_cycle
    last = first + n_cycles

    def _stop_requested(where: str) -> bool:
        if stop_path is not None and Path(stop_path).exists():
            log(f"daemon[{trainer.name}] stop file detected {where}; "
                f"exiting cleanly (next_cycle={state.next_cycle})")
            return True
        return False

    if _stop_requested("before starting"):
        return state
    with ThreadPoolExecutor(max_workers=2) as pool:
        data_future = pool.submit(trainer.prepare_data, first)
        for cycle in range(first, last):
            if _stop_requested(f"between cycles (before cycle {cycle})"):
                break
            try:
                data_path = data_future.result()
                if _stop_requested(
                        f"between producer/consumer stages of cycle {cycle}"):
                    break
                train_future = pool.submit(trainer.train, data_path, cycle)
                if cycle + 1 < last:  # producer-consumer overlap
                    data_future = pool.submit(trainer.prepare_data, cycle + 1)
                weights = train_future.result()
                # Registering this cycle's new candidate is a read-modify-write
                # on candidates.json. `candidates` below is an unlocked,
                # optimistic snapshot used only to compute the next version
                # number (next_version, via trainer.export_and_register) - it
                # may be stale by the time we save, since a factory cycle
                # (cycle.py) can hold the ledger for a long-running evaluation
                # series in between. `merge_save` (candidates.py) closes that
                # gap: it re-locks, fresh-loads the ledger, merges in just the
                # one candidate this daemon cycle created (preserving every
                # entry a concurrent cycle changed or added), and saves -
                # rather than blindly re-serializing this stale snapshot,
                # which would silently drop the cycle's concurrent update
                # (lost update, same failure mode this helper fixes in
                # cycle.py).
                candidates = load_ledger(ledger_path)
                cand = trainer.export_and_register(weights, cycle, candidates)
                merge_save(ledger_path, [cand], log=log)
                state.completed.append(cycle)
                state.next_cycle = cycle + 1
                state.last_error = None
                save_state(state_path, state)
                log(f"daemon[{trainer.name}] cycle {cycle} complete -> {cand.id}")
            except Exception:
                state.last_error = traceback.format_exc(limit=5)
                save_state(state_path, state)
                log(f"daemon[{trainer.name}] cycle {cycle} FAILED; "
                    f"state persisted for resume")
                raise
    return state
