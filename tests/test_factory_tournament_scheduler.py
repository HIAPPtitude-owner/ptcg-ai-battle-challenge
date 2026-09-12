"""Tests for the active pool + fewest-games-first pair scheduler
(spec compute-saturation, Task 3).

Property-style, not vectors, per the task brief: uniform-coverage over
repeated picks, None for degenerate pools, RETIRED exclusion, and the
is_protected predicate.
"""
from __future__ import annotations

import itertools

from ptcg.factory.candidates import Candidate, Status
from ptcg.factory.tournament import BLOCK_GAMES, MatrixLedger, active_pool, is_protected, next_pair


def _make(name: str) -> Candidate:
    return Candidate.create(name=name, version="v0.1", deck="d.csv", agent_kind="heuristic")


def test_next_pair_uniform_coverage_before_any_repeat():
    candidates = [_make(f"cand-{i}") for i in range(5)]
    ledger = MatrixLedger()
    expected_pairs = set(
        frozenset((a.id, b.id)) for a, b in itertools.combinations(candidates, 2)
    )
    assert len(expected_pairs) == 10

    seen: list[frozenset] = []
    for _ in range(10):
        pair = next_pair(candidates, ledger)
        assert pair is not None
        key = frozenset((pair[0].id, pair[1].id))
        assert key not in seen, f"pair {key} repeated before full coverage: {seen}"
        seen.append(key)
        ledger.record(pair[0].id, pair[1].id, wins=BLOCK_GAMES, games=BLOCK_GAMES)

    assert set(seen) == expected_pairs


def test_next_pair_none_for_empty_pool():
    ledger = MatrixLedger()
    assert next_pair([], ledger) is None


def test_next_pair_none_for_single_candidate_pool():
    ledger = MatrixLedger()
    assert next_pair([_make("solo")], ledger) is None


def test_active_pool_excludes_retired():
    live = _make("live")
    retired = _make("retired")
    retired.status = Status.RETIRED
    evaluating = _make("evaluating")
    evaluating.status = Status.EVALUATING

    pool = active_pool([live, retired, evaluating])

    ids = {c.id for c in pool}
    assert ids == {live.id, evaluating.id}
    assert retired.id not in ids


def test_active_pool_empty_when_all_retired():
    c = _make("only")
    c.status = Status.RETIRED
    assert active_pool([c]) == []


def test_is_protected_true_for_incumbent():
    c = _make("champ")
    c.is_incumbent = True
    assert is_protected(c) is True


def test_is_protected_true_for_submitted_at_set():
    c = _make("uploaded")
    c.submitted_at = "2026-07-20T00:00:00Z"
    assert is_protected(c) is True


def test_is_protected_false_for_plain_candidate():
    c = _make("plain")
    assert is_protected(c) is False
