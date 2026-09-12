"""Tests for pool-cap auto-retire + daily matrix snapshot (spec
compute-saturation, Task 5).
"""
from __future__ import annotations

from ptcg.factory.candidates import Candidate, Status
from ptcg.factory.tournament import (
    POOL_CAP,
    RETIRE_FLOOR_GAMES,
    MatrixLedger,
    active_pool,
    append_daily_snapshot,
    enforce_pool_cap,
)


def _make(name: str, *, rating: float | None, games: int = RETIRE_FLOOR_GAMES,
          incumbent: bool = False, submitted: str | None = None) -> Candidate:
    c = Candidate.create(name=name, version="v0.1", deck="d.csv", agent_kind="heuristic")
    c.matrix_rating = rating
    c.matrix_games = games
    c.is_incumbent = incumbent
    c.submitted_at = submitted
    return c


def _over_cap_pool(n_extra: int = 1, **kwargs) -> list[Candidate]:
    """POOL_CAP + n_extra eligible candidates, ratings 0, 1, 2, ... so
    index 0 is always the lowest-rated (and thus the expected victim
    absent any protection/floor override)."""
    return [_make(f"cand-{i}", rating=float(i)) for i in range(POOL_CAP + n_extra)]


def test_enforce_pool_cap_retires_lowest_rated_victim():
    candidates = _over_cap_pool(n_extra=1)
    ledger = MatrixLedger()

    modified = enforce_pool_cap(candidates, ledger)

    assert [c.id for c in modified] == [candidates[0].id]
    assert candidates[0].status == Status.RETIRED
    assert all(c.status != Status.RETIRED for c in candidates[1:])
    assert len(active_pool(candidates)) == POOL_CAP


def test_enforce_pool_cap_loops_until_at_cap():
    candidates = _over_cap_pool(n_extra=3)
    ledger = MatrixLedger()

    modified = enforce_pool_cap(candidates, ledger)

    assert {c.id for c in modified} == {candidates[0].id, candidates[1].id, candidates[2].id}
    assert len(active_pool(candidates)) == POOL_CAP


def test_enforce_pool_cap_skips_incumbent_even_when_lowest():
    candidates = _over_cap_pool(n_extra=1)
    candidates[0].is_incumbent = True  # would be the victim, but protected

    modified = enforce_pool_cap(candidates, MatrixLedger())

    assert candidates[0].status != Status.RETIRED
    # next-lowest (index 1) is retired instead
    assert [c.id for c in modified] == [candidates[1].id]
    assert len(active_pool(candidates)) == POOL_CAP


def test_enforce_pool_cap_skips_on_ladder_even_when_lowest():
    candidates = _over_cap_pool(n_extra=1)
    candidates[0].submitted_at = "2026-07-20T00:00:00Z"  # protected via is_protected

    modified = enforce_pool_cap(candidates, MatrixLedger())

    assert candidates[0].status != Status.RETIRED
    assert [c.id for c in modified] == [candidates[1].id]
    assert len(active_pool(candidates)) == POOL_CAP


def test_enforce_pool_cap_never_retires_under_floor():
    candidates = _over_cap_pool(n_extra=1)
    candidates[0].matrix_games = RETIRE_FLOOR_GAMES - 1  # under-sampled, lowest rating

    modified = enforce_pool_cap(candidates, MatrixLedger())

    assert candidates[0].status != Status.RETIRED
    assert [c.id for c in modified] == [candidates[1].id]
    assert len(active_pool(candidates)) == POOL_CAP


def test_enforce_pool_cap_stops_when_no_eligible_victim():
    # Every candidate over cap is either protected or under-floor - no
    # eligible victim exists anywhere, so the pool stays over cap and
    # nothing is retired.
    candidates = [_make(f"cand-{i}", rating=float(i), incumbent=True)
                  for i in range(POOL_CAP + 2)]

    modified = enforce_pool_cap(candidates, MatrixLedger())

    assert modified == []
    assert all(c.status != Status.RETIRED for c in candidates)
    assert len(active_pool(candidates)) == POOL_CAP + 2


def test_enforce_pool_cap_noop_when_at_or_under_cap():
    candidates = [_make(f"cand-{i}", rating=float(i)) for i in range(POOL_CAP)]

    modified = enforce_pool_cap(candidates, MatrixLedger())

    assert modified == []
    assert len(active_pool(candidates)) == POOL_CAP


def test_append_daily_snapshot_writes_once_per_day(tmp_path):
    ledger = MatrixLedger()
    candidates = [_make("cand-a", rating=1.5), _make("cand-b", rating=1.2)]
    path = tmp_path / "EXPERIMENTS.md"

    wrote = append_daily_snapshot(ledger, candidates, path, "2026-07-20")

    assert wrote is True
    assert ledger.meta["last_snapshot_date"] == "2026-07-20"
    first_text = path.read_text(encoding="utf-8")
    assert "2026-07-20" in first_text
    assert "cand-a" in first_text

    wrote_again = append_daily_snapshot(ledger, candidates, path, "2026-07-20")

    assert wrote_again is False
    assert path.read_text(encoding="utf-8") == first_text  # file unchanged


def test_append_daily_snapshot_appends_never_truncates(tmp_path):
    path = tmp_path / "EXPERIMENTS.md"
    prior_content = "# Experiment Log\n\n| Date | Notes |\n|---|---|\n| 2026-07-19 | prior row |\n"
    path.write_text(prior_content, encoding="utf-8")

    ledger = MatrixLedger()
    candidates = [_make("cand-a", rating=1.5)]

    wrote = append_daily_snapshot(ledger, candidates, path, "2026-07-20")

    assert wrote is True
    new_text = path.read_text(encoding="utf-8")
    assert new_text.startswith(prior_content)
    assert "prior row" in new_text
    assert "2026-07-20" in new_text


def test_append_daily_snapshot_new_day_after_prior_snapshot_writes_again(tmp_path):
    path = tmp_path / "EXPERIMENTS.md"
    ledger = MatrixLedger()
    candidates = [_make("cand-a", rating=1.5)]

    assert append_daily_snapshot(ledger, candidates, path, "2026-07-20") is True
    assert append_daily_snapshot(ledger, candidates, path, "2026-07-21") is True
    assert ledger.meta["last_snapshot_date"] == "2026-07-21"

    text = path.read_text(encoding="utf-8")
    assert "2026-07-20" in text
    assert "2026-07-21" in text
