"""Tests for the continuous-training daemon (FakeTrainer - no real training)."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from ptcg.factory.candidates import Candidate, load_ledger, next_version
from ptcg.factory.daemon import DaemonState, load_state, run_daemon, save_state


class OverlapProbeTrainer:
    """train(N) blocks until prepare_data(N+1) has STARTED - if the daemon does
    not overlap producer and consumer, the wait times out and the test fails."""

    name = "probe"

    def __init__(self, n_cycles: int) -> None:
        self.prepare_started = {i: threading.Event() for i in range(n_cycles)}
        self.events: list[tuple[str, int]] = []

    def prepare_data(self, cycle: int) -> Path:
        self.prepare_started[cycle].set()
        self.events.append(("prepare", cycle))
        return Path(f"data-{cycle}.jsonl")

    def train(self, data_path: Path, cycle: int) -> Path:
        nxt = self.prepare_started.get(cycle + 1)
        if nxt is not None:
            assert nxt.wait(timeout=10), \
                f"prepare_data({cycle + 1}) never started while train({cycle}) ran"
        self.events.append(("train", cycle))
        return Path(f"weights-{cycle}.json")

    def export_and_register(self, weights_path: Path, cycle: int,
                            candidates: list[Candidate]) -> Candidate:
        return Candidate.create(
            name="probe-net", version=next_version(candidates, "probe-net"),
            deck="d.csv", agent_kind="search-net", provenance=f"daemon:probe:c{cycle}")


def test_daemon_overlaps_and_registers_candidates(tmp_path):
    trainer = OverlapProbeTrainer(n_cycles=2)
    ledger, state_p = tmp_path / "candidates.json", tmp_path / "daemon_state.json"
    state = run_daemon(trainer, 2, ledger, state_p, log=lambda m: None)
    assert state.next_cycle == 2 and state.completed == [0, 1]
    cands = load_ledger(ledger)
    assert [c.id for c in cands] == ["probe-net-v0.1", "probe-net-v0.2"]
    assert ("train", 0) in trainer.events and ("prepare", 1) in trainer.events


class FlakyTrainer(OverlapProbeTrainer):
    def __init__(self, n_cycles: int) -> None:
        super().__init__(n_cycles)
        self.fail_next_train = True

    def train(self, data_path: Path, cycle: int) -> Path:
        if self.fail_next_train:
            self.fail_next_train = False
            raise RuntimeError("gpu fell over")
        return super().train(data_path, cycle)


def test_daemon_persists_state_and_resumes_after_crash(tmp_path):
    ledger, state_p = tmp_path / "candidates.json", tmp_path / "daemon_state.json"
    flaky = FlakyTrainer(n_cycles=1)
    with pytest.raises(RuntimeError, match="gpu fell over"):
        run_daemon(flaky, 1, ledger, state_p, log=lambda m: None)
    state = load_state(state_p)
    assert state.next_cycle == 0 and state.last_error is not None  # resume point

    ok = OverlapProbeTrainer(n_cycles=1)
    state = run_daemon(ok, 1, ledger, state_p, log=lambda m: None)
    assert state.next_cycle == 1 and state.completed == [0]
    assert load_ledger(ledger)[0].id == "probe-net-v0.1"


def test_state_round_trip(tmp_path):
    p = tmp_path / "s.json"
    save_state(p, DaemonState(next_cycle=3, completed=[0, 1, 2]))
    s = load_state(p)
    assert s.next_cycle == 3 and s.completed == [0, 1, 2]
    assert load_state(tmp_path / "missing.json").next_cycle == 0


def test_daemon_stop_file_before_start_exits_without_running(tmp_path):
    """Task-11 carry-forward: a stop file present before the run even begins
    must short-circuit cleanly -- no producer/consumer work is started."""
    ledger, state_p = tmp_path / "candidates.json", tmp_path / "daemon_state.json"
    stop = tmp_path / "STOP"
    stop.write_text("stop", encoding="utf-8")
    trainer = OverlapProbeTrainer(n_cycles=1)
    state = run_daemon(trainer, 1, ledger, state_p, log=lambda m: None, stop_path=stop)
    assert state.next_cycle == 0 and state.completed == []
    assert trainer.events == []


class _StopBetweenProducerConsumerTrainer:
    """prepare_data(0) itself creates the stop file (synchronously, before
    returning), so the daemon's producer/consumer-boundary check must catch
    it before train(0) is ever submitted."""

    name = "stop-between"

    def __init__(self, stop_path: Path) -> None:
        self.stop_path = stop_path
        self.prepared: list[int] = []
        self.trained: list[int] = []

    def prepare_data(self, cycle: int) -> Path:
        self.prepared.append(cycle)
        if cycle == 0:
            self.stop_path.write_text("stop", encoding="utf-8")
        return Path(f"data-{cycle}.jsonl")

    def train(self, data_path: Path, cycle: int) -> Path:
        self.trained.append(cycle)
        return Path(f"weights-{cycle}.json")

    def export_and_register(self, weights_path: Path, cycle: int,
                            candidates: list[Candidate]) -> Candidate:
        return Candidate.create(
            name="stop-between-net",
            version=next_version(candidates, "stop-between-net"),
            deck="d.csv", agent_kind="search-net",
            provenance=f"daemon:stop-between:c{cycle}")


def test_daemon_stop_between_producer_consumer_skips_train(tmp_path):
    ledger, state_p = tmp_path / "candidates.json", tmp_path / "daemon_state.json"
    stop = tmp_path / "STOP"
    trainer = _StopBetweenProducerConsumerTrainer(stop)
    state = run_daemon(trainer, 2, ledger, state_p, log=lambda m: None, stop_path=stop)
    assert trainer.prepared == [0] and trainer.trained == []
    assert state.next_cycle == 0 and state.completed == []


class _StopAfterCycleTrainer:
    """train(0) creates the stop file (synchronously, before returning), so
    cycle 0 finishes and registers normally but cycle 1 never starts -- the
    between-cycles check catches it at the top of the next iteration."""

    name = "stop-after"

    def __init__(self, stop_path: Path) -> None:
        self.stop_path = stop_path
        self.events: list[tuple[str, int]] = []

    def prepare_data(self, cycle: int) -> Path:
        self.events.append(("prepare", cycle))
        return Path(f"data-{cycle}.jsonl")

    def train(self, data_path: Path, cycle: int) -> Path:
        self.events.append(("train", cycle))
        if cycle == 0:
            self.stop_path.write_text("stop", encoding="utf-8")
        return Path(f"weights-{cycle}.json")

    def export_and_register(self, weights_path: Path, cycle: int,
                            candidates: list[Candidate]) -> Candidate:
        return Candidate.create(
            name="stop-after-net", version=next_version(candidates, "stop-after-net"),
            deck="d.csv", agent_kind="search-net",
            provenance=f"daemon:stop-after:c{cycle}")


def test_daemon_stop_between_cycles_completes_current_cycle(tmp_path):
    ledger, state_p = tmp_path / "candidates.json", tmp_path / "daemon_state.json"
    stop = tmp_path / "STOP"
    trainer = _StopAfterCycleTrainer(stop)
    state = run_daemon(trainer, 3, ledger, state_p, log=lambda m: None, stop_path=stop)
    assert state.completed == [0] and state.next_cycle == 1
    assert ("train", 1) not in trainer.events
    assert load_ledger(ledger)[0].id == "stop-after-net-v0.1"
