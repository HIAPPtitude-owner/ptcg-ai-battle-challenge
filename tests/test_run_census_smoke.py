"""Fast unit test for the census slice runner (tournament plan Task 9):
exercises the real seed -> pair-seed -> schedule -> pool -> rating pipeline
end to end against a genuinely fresh temp DB, with a STUBBED `play_match` (no
real engine invocation, so this stays out of `@pytest.mark.slow`). The
real-engine rung-3 throughput smoke is a manual CLI invocation
(`scripts/run_census.py --smoke`), not part of this automated suite -- see
plan Task 9 Step 4.

`n_concepts=1` is a deliberate, hand-verified choice (not n_concepts=2+):
`census.schedule_screening_games`'s candidate list and opponent-rotation
cursor both sort by `concept_id ASC` over the SAME real ~815-concept field,
so for n_concepts>=2 a later candidate can be pre-consumed as an earlier
candidate's opponent before its own turn, redistributing (but never
inflating) how many DISTINCT concepts end up as subjects -- the aggregate
`games_enqueued` total is still bounded by `batch`, but per-concept counts
become data-dependent on the real field's hash-ordered concept ids.
With n_concepts=1, `target_per_concept == batch`, so the FIRST (and only)
candidate's own deficit alone exhausts the whole batch inside the first
loop iteration -- deterministic and hand-verifiable without touching real
concept-id ordering.
"""
from __future__ import annotations

from ptcg.factory import deckdb, runner_pool

import scripts.run_census as run_census


class _StubMatch:
    """Matches just enough of `MatchResult`'s shape for `run_one_game`."""

    def __init__(self, winner: int = 0) -> None:
        self.winner = winner
        self.error: str | None = None
        self.turns = 1
        self.moves = 1


def test_run_census_slice_virgin_dir_seeds_schedules_plays_and_rates(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_pool, "play_match", lambda *a, **k: _StubMatch(winner=0))
    monkeypatch.setattr(runner_pool, "build_agent", lambda cand, deck: object())

    fresh_db = tmp_path / "never" / "created" / "here" / "fresh.db"
    assert not fresh_db.parent.exists()  # Pattern VIRGIN-DIR-TEST

    result = run_census.run_census_slice(
        fresh_db, n_concepts=1, games_per_concept=3, n_workers=1
    )

    assert fresh_db.exists()

    # Real full-field seeding (never sliced -- the plan's own smoke command
    # relies on this to exercise the full 332,520-row enumeration).
    assert result["concepts_seeded"] >= 800
    assert result["buildable"] >= 1
    assert result["pairs_seeded"] > 300_000

    # Hand-verified (see module docstring): n_concepts=1 makes the first
    # candidate's own deficit == batch, so exactly `games_per_concept` games
    # are enqueued and all of them are played by the single in-process worker.
    assert result["games_enqueued"] == 3
    assert result["games_played"] == 3
    assert result["rated"] >= 1

    conn = deckdb.connect(fresh_db)
    assert conn.execute("SELECT COUNT(*) FROM games WHERE status='done'").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM games WHERE status!='done'").fetchone()[0] == 0
    rated_rows = conn.execute(
        "SELECT COUNT(*) FROM coverage WHERE rating IS NOT NULL"
    ).fetchone()[0]
    assert rated_rows >= 1

    assert result["play_elapsed_s"] >= 0.0
    assert result["seed_elapsed_s"] >= 0.0


def test_run_census_slice_zero_budget_enqueues_and_plays_nothing(tmp_path, monkeypatch):
    """Degenerate-input probe (`.claude/rules/plan-test-arithmetic-sanity.md`):
    games_per_concept=0 must not crash or hang -- zero deficit, zero enqueue,
    zero played, zero rated."""
    monkeypatch.setattr(runner_pool, "play_match", lambda *a, **k: _StubMatch(winner=0))
    monkeypatch.setattr(runner_pool, "build_agent", lambda cand, deck: object())

    result = run_census.run_census_slice(
        tmp_path / "zero.db", n_concepts=1, games_per_concept=0, n_workers=1
    )

    assert result["games_enqueued"] == 0
    assert result["games_played"] == 0
    assert result["rated"] == 0
