"""Barrier + statement-counter harness for interleaved-mutation tests.

Every concurrency test in `ptcg.factory` must discriminate on the MUTATING
STATEMENT, never on the return value. The step functions in this package are
deliberately written so the loser of a write race reports the winner's settled
outcome (`resolve_net_check` returns the settled verdict, `resolve_floor`
returns the settled verdict, ...). That makes a return-value assertion like
`verdicts == ["adopt", "adopt"]` a TAUTOLOGY: it holds under a correct
`BEGIN IMMEDIATE` implementation AND under a fully non-atomic one, so it has
zero fail-power and pins nothing.

`race_two` instead counts executions of the statement the race is actually
about, via `sqlite3.Connection.set_trace_callback`, and asserts the overlap
that makes the count meaningful. Idiom lifted from
`tests/test_factory_loop_crown.py::test_resolve_crown_survives_concurrent_calls`
(the one place in the suite that already did this correctly) and generalized
here rather than copy-pasted into each caller.

See `.claude/rules/single-actor-worker-tests.md`.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable

from ptcg.factory import deckdb


def race_two(
    db_path: str | Path,
    fn: Callable[[sqlite3.Connection], Any],
    trace_pred: Callable[[str], bool],
    *,
    setup: Callable[[sqlite3.Connection], None] | None = None,
    timeout: float = 20.0,
) -> tuple[list[Any], int]:
    """Call `fn(conn)` on two threads released together by a barrier.

    Each thread opens its OWN connection (a shared connection would serialize
    in Python and prove nothing) and installs a trace callback; `trace_pred`
    receives each executed statement already `.strip().upper()`-ed, so callers
    match with plain `startswith("UPDATE FLOOR_CHECKS")`.

    Returns `(results, count)` where `count` is how many times a matching
    statement executed across BOTH connections. Asserts internally that both
    threads got past the barrier and that the barrier never broke -- that is
    the overlap receipt, without which a green `count == 1` could simply mean
    thread A finished before thread B ever started (vacuous on a fast machine
    or a loaded CI box).

    `setup` runs on each thread's connection before the barrier, for callers
    that need per-connection state (e.g. a second connection's interleaved
    write) established without widening the race window.
    """
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    count = 0
    entered: list[int] = []
    results: list[Any] = []
    errors: list[BaseException] = []

    def _trace(sql: str) -> None:
        nonlocal count
        if trace_pred(sql.strip().upper()):
            with lock:
                count += 1

    def _call() -> None:
        try:
            conn = deckdb.connect(Path(db_path))
            if setup is not None:
                setup(conn)
            conn.set_trace_callback(_trace)
            barrier.wait(timeout=timeout / 2)
            with lock:
                entered.append(1)
            result = fn(conn)
            with lock:
                results.append(result)
        except BaseException as exc:  # noqa: BLE001 - surfaced to the caller below
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=_call) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout)

    assert not any(t.is_alive() for t in threads), "racer thread hung"
    assert not errors, f"racer raised: {errors!r}"
    # Overlap receipt: both threads were simultaneously past a 2-party barrier,
    # so the calls genuinely interleaved rather than running back to back.
    assert not barrier.broken, "barrier broke -- the two calls never overlapped"
    assert len(entered) == 2, f"expected 2 racers past the barrier, got {len(entered)}"
    return results, count
