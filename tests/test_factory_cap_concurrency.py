"""Money-path invariant: the 5/day Kaggle hard cap can never be exceeded, even
with concurrent processes (two entry points constructed from the same on-disk
counter, each holding a stale in-memory snapshot).

Reproduces the Pass-2 critic's receipt: two SubmissionCounter objects built
from a count=4 file both pass gate.decide (4 < 5) and both record; a blind
post-hoc increment reaches disk count 6 (cap breached). The atomic
cap-aware admission (SubmissionCounter.try_reserve / cap-aware record) must
cap the persisted count at HARD_DAILY_CAP regardless of stale snapshots or
entry-point interleaving.
"""
from __future__ import annotations

from pathlib import Path

from ptcg.factory.candidates import Candidate, Status
from ptcg.factory.gate import HARD_DAILY_CAP, SubmissionCounter, decide
from ptcg.factory.kaggle_client import FakeKaggleClient
from ptcg.factory.submit import submit_candidates

TODAY = "2026-07-17"


def _evaluated(name: str, wr: float) -> Candidate:
    c = Candidate.create(name=name, version="v1.0", deck="decks/mega-x.csv",
                         agent_kind="heuristic")
    c.status, c.local_wr, c.local_games = Status.EVALUATED, wr, 150
    return c


def _seed_counter_at(path: Path, n: int) -> None:
    c = SubmissionCounter(path)
    for _ in range(n):
        c.record(TODAY)


def test_two_stale_counters_cannot_exceed_cap(tmp_path):
    """The critic's exact receipt, thread-free. Two counters loaded from a
    count=4 file each hold in-memory count=4; both pass gate.decide and both
    record. Current (blind) record -> disk 6 (RED). Atomic cap-aware record
    -> disk capped at 5 (GREEN)."""
    path = tmp_path / "counter.json"
    _seed_counter_at(path, HARD_DAILY_CAP - 1)  # disk count = 4

    c1 = SubmissionCounter(path)  # stale snapshot: in-memory 4
    c2 = SubmissionCounter(path)  # stale snapshot: in-memory 4
    cand = _evaluated("challenger", 0.55)

    # Both admissions pass from their identical stale snapshot (4 < 5) - this
    # is the setup that makes the race exploitable, exactly as the critic
    # described (both pass gate.decide).
    assert decide(cand, [cand], c1, TODAY).submit
    assert decide(cand, [cand], c2, TODAY).submit

    c1.record(TODAY)
    c2.record(TODAY)

    final = SubmissionCounter(path).today_count(TODAY)
    assert final <= HARD_DAILY_CAP, f"cap breached: persisted count={final}"
    assert final == HARD_DAILY_CAP  # exactly one of the two admissions counted


def test_try_reserve_is_atomic_and_capped(tmp_path):
    """try_reserve claims n slots atomically and refuses (no state change)
    when it would exceed the fixed cap."""
    path = tmp_path / "c.json"
    _seed_counter_at(path, 3)  # 3 used
    c = SubmissionCounter(path)

    assert c.try_reserve(TODAY, 2) is True   # 3 + 2 == 5 <= cap
    assert SubmissionCounter(path).today_count(TODAY) == 5
    assert c.try_reserve(TODAY, 1) is False  # 5 + 1 == 6 > cap -> refused
    assert SubmissionCounter(path).today_count(TODAY) == 5  # unchanged on refusal


def test_stale_counter_try_reserve_rechecks_disk(tmp_path):
    """A stale counter's try_reserve must re-read disk under the lock, so a
    reservation another instance already persisted is respected."""
    path = tmp_path / "c.json"
    _seed_counter_at(path, 3)
    a = SubmissionCounter(path)  # in-memory 3
    b = SubmissionCounter(path)  # in-memory 3 (stale)

    assert a.try_reserve(TODAY, 2) is True   # disk -> 5
    assert b.try_reserve(TODAY, 1) is False  # b refreshes, sees 5, refuses
    assert SubmissionCounter(path).today_count(TODAY) == HARD_DAILY_CAP


def test_release_returns_reserved_slots(tmp_path):
    """release gives back reserved-but-unused slots (upload failure path),
    never dropping below zero."""
    path = tmp_path / "c.json"
    _seed_counter_at(path, 2)
    c = SubmissionCounter(path)
    assert c.try_reserve(TODAY, 2) is True   # -> 4
    c.release(TODAY, 2)                        # -> 2
    assert SubmissionCounter(path).today_count(TODAY) == 2
    c.release(TODAY, 10)                       # floors at 0
    assert SubmissionCounter(path).today_count(TODAY) == 0


def test_failed_upload_releases_reserved_slot(tmp_path):
    """Reserve-before-upload must not leak a slot when the upload fails: the
    lone-challenger path reserves 1, both upload attempts fail, and the slot
    is returned so the day's real budget is unaffected."""
    cand = _evaluated("solo", 0.55)  # edge (a): no counted subs -> lone
    counter = SubmissionCounter(tmp_path / "c.json")

    class AlwaysFails(FakeKaggleClient):
        def submit(self, bundle, description):
            raise RuntimeError("network")

    submit_candidates([cand], AlwaysFails(), counter, tmp_path, tmp_path,
                      today=TODAY, build_fn=lambda c, o: Path(o) / "b.tar.gz",
                      verify_fn=lambda *a: None, log=lambda m: None)

    assert counter.today_count(TODAY) == 0  # reserved slot returned, no leak


def test_successful_lone_challenger_consumes_exactly_one_slot(tmp_path):
    """Sanity companion to the failure path: a successful lone-challenger
    submit consumes exactly one reserved slot (no double-count from the
    reserve-then-record transition)."""
    cand = _evaluated("solo", 0.55)
    counter = SubmissionCounter(tmp_path / "c.json")
    submit_candidates([cand], FakeKaggleClient(), counter, tmp_path, tmp_path,
                      today=TODAY, build_fn=lambda c, o: Path(o) / "b.tar.gz",
                      verify_fn=lambda *a: None, log=lambda m: None)
    assert counter.today_count(TODAY) == 1
