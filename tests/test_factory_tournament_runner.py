"""Tests for the candidate-vs-candidate block runner + BT rating
write-back (spec compute-saturation, Task 4).

All tests inject a fake `series_fn` into `play_block` - no real games are
played in this unit-test file.
"""
from __future__ import annotations

from ptcg.factory.candidates import Candidate, Status
from ptcg.factory.tournament import (
    BLOCK_GAMES,
    MIN_COVERAGE_GAMES,
    MIN_COVERAGE_OPPONENTS,
    MatrixLedger,
    play_block,
    refresh_ratings,
)


def _make(name: str, status: Status = Status.QUEUED) -> Candidate:
    c = Candidate.create(name=name, version="v0.1", deck="d.csv", agent_kind="heuristic")
    c.status = status
    return c


def _record_games(ledger: MatrixLedger, cand: Candidate, opponents: list[Candidate],
                   games_per_opponent: list[int]) -> None:
    """Record `games_per_opponent[i]` games between `cand` and `opponents[i]`,
    crediting all wins to `cand` (win/loss split is irrelevant to coverage
    tests - only game/opponent counts matter)."""
    for opp, n in zip(opponents, games_per_opponent):
        ledger.record(cand.id, opp.id, wins=n, games=n)


# --- play_block ---------------------------------------------------------

def test_play_block_forwards_args_to_series_fn_and_returns_its_result():
    a = _make("a")
    b = _make("b")
    calls = []

    def fake_series_fn(cand_a, cand_b, n_games):
        calls.append((cand_a, cand_b, n_games))
        return (7, 3)

    result = play_block(a, b, n_games=10, series_fn=fake_series_fn)

    assert result == (7, 3)
    assert calls == [(a, b, 10)]


def test_play_block_defaults_n_games_to_BLOCK_GAMES():
    a = _make("a")
    b = _make("b")
    calls = []

    def fake_series_fn(cand_a, cand_b, n_games):
        calls.append(n_games)
        return (0, 0)

    play_block(a, b, series_fn=fake_series_fn)

    assert calls == [BLOCK_GAMES]


def test_play_block_excludes_draws_and_callers_record_with_actual_games():
    """A block with draws must return only decided-game wins - draws are
    excluded from the returned tuple (BT is win/loss). Callers must record
    with games = wins_a + wins_b, never the requested n_games, or draws get
    silently counted as losses for the other side (MatrixLedger.record
    credits `games - wins` to the loser)."""
    a = _make("a")
    b = _make("b")

    def fake_series_fn(cand_a, cand_b, n_games):
        # 10 requested games: 4 decided wins for a, 3 decided wins for b,
        # 3 draws - the draws are simply absent from the returned tuple.
        return (4, 3)

    wins_a, wins_b = play_block(a, b, n_games=10, series_fn=fake_series_fn)
    assert (wins_a, wins_b) == (4, 3)

    ledger = MatrixLedger()
    # Correct contract: record with games = wins_a + wins_b (7 decided
    # games), never the requested n_games (10).
    ledger.record(a.id, b.id, wins=wins_a, games=wins_a + wins_b)

    assert ledger.games_between(a.id, b.id) == 7
    wins = ledger.wins_dict()
    # a's credited wins = 4 (as recorded); b's credited wins = games - wins
    # = 7 - 4 = 3 - matching the actual decided-game result, not b=10-4=6
    # losses, which is what recording with the requested n_games=10 would
    # have produced.
    assert wins[(a.id, b.id)] == 4
    assert wins[(b.id, a.id)] == 3


# --- refresh_ratings: coverage promotion boundary -----------------------

def test_refresh_ratings_stays_queued_at_14_games_8_opponents():
    x = _make("x", Status.QUEUED)
    opponents = [_make(f"o{i}") for i in range(8)]
    ledger = MatrixLedger()
    # 6 opponents x 2 games + 2 opponents x 1 game = 14 games, 8 opponents.
    _record_games(ledger, x, opponents, [2, 2, 2, 2, 2, 2, 1, 1])
    assert ledger.total_games(x.id) == 14
    assert len(ledger.opponents_of(x.id)) == 8
    assert 14 < MIN_COVERAGE_GAMES  # sanity-check the boundary premise

    modified = refresh_ratings(ledger, [x] + opponents)

    assert x.status == Status.QUEUED
    assert x.matrix_games == 14
    assert x.matrix_opponents == 8
    modified_ids = {c.id for c in modified}
    assert x.id in modified_ids  # rating/games/opponents changed from defaults


def test_refresh_ratings_promotes_at_15_games_8_opponents():
    x = _make("x", Status.QUEUED)
    opponents = [_make(f"o{i}") for i in range(8)]
    ledger = MatrixLedger()
    # 6 opponents x 2 games + 1 opponent x 2 games + 1 opponent x 1 game
    # = 15 games, 8 opponents.
    _record_games(ledger, x, opponents, [2, 2, 2, 2, 2, 2, 2, 1])
    assert ledger.total_games(x.id) == 15
    assert len(ledger.opponents_of(x.id)) == 8
    assert MIN_COVERAGE_GAMES == 15 and MIN_COVERAGE_OPPONENTS == 8  # sanity

    modified = refresh_ratings(ledger, [x] + opponents)

    assert x.status == Status.EVALUATED
    assert x.matrix_games == 15
    assert x.matrix_opponents == 8
    modified_ids = {c.id for c in modified}
    assert x.id in modified_ids


def test_refresh_ratings_does_not_promote_below_opponent_floor():
    """15+ games but fewer than 8 distinct opponents must NOT promote."""
    x = _make("x", Status.QUEUED)
    opponents = [_make(f"o{i}") for i in range(3)]
    ledger = MatrixLedger()
    _record_games(ledger, x, opponents, [5, 5, 5])  # 15 games, 3 opponents
    assert ledger.total_games(x.id) == 15
    assert len(ledger.opponents_of(x.id)) == 3

    refresh_ratings(ledger, [x] + opponents)

    assert x.status == Status.QUEUED


def test_refresh_ratings_does_not_promote_at_exactly_7_opponents():
    """15+ games but exactly 7 distinct opponents (one below
    MIN_COVERAGE_OPPONENTS=8) must NOT promote - pins the exact opponent
    boundary, distinct from the far-below-floor case above (3 opponents)."""
    x = _make("x", Status.QUEUED)
    opponents = [_make(f"o{i}") for i in range(7)]
    ledger = MatrixLedger()
    _record_games(ledger, x, opponents, [3, 3, 3, 3, 3, 3, 3])  # 21 games, 7 opponents
    assert ledger.total_games(x.id) == 21
    assert len(ledger.opponents_of(x.id)) == 7
    assert MIN_COVERAGE_OPPONENTS == 8  # sanity-check the boundary premise

    refresh_ratings(ledger, [x] + opponents)

    assert x.status == Status.QUEUED


def test_refresh_ratings_promotes_at_exactly_8_opponents():
    """Same games-per-opponent shape as the 7-opponent case above but with
    an 8th opponent added - pins that crossing exactly 8 distinct
    opponents (with games already >= MIN_COVERAGE_GAMES) promotes."""
    x = _make("x", Status.QUEUED)
    opponents = [_make(f"o{i}") for i in range(8)]
    ledger = MatrixLedger()
    _record_games(ledger, x, opponents, [3, 3, 3, 3, 3, 3, 3, 3])  # 24 games, 8 opponents
    assert ledger.total_games(x.id) == 24
    assert len(ledger.opponents_of(x.id)) == 8

    refresh_ratings(ledger, [x] + opponents)

    assert x.status == Status.EVALUATED


# --- refresh_ratings: modified-only return -------------------------------

def test_refresh_ratings_returns_only_changed_candidates():
    x = _make("x", Status.QUEUED)
    untouched = _make("untouched", Status.QUEUED)  # no games recorded at all
    opponents = [_make(f"o{i}") for i in range(8)]
    ledger = MatrixLedger()
    _record_games(ledger, x, opponents, [2, 2, 2, 2, 2, 2, 1, 1])

    modified = refresh_ratings(ledger, [x, untouched] + opponents)

    modified_ids = {c.id for c in modified}
    assert x.id in modified_ids
    assert untouched.id not in modified_ids
    # untouched candidate's fields must be left at their prior (default) values
    assert untouched.matrix_rating is None
    assert untouched.matrix_games == 0
    assert untouched.matrix_opponents == 0


# --- refresh_ratings: never overwrites ladder statuses --------------------

def test_refresh_ratings_never_promotes_submitted_or_scored_candidates():
    submitted = _make("submitted", Status.SUBMITTED)
    scored = _make("scored", Status.SCORED)
    opponents = [_make(f"o{i}") for i in range(8)]
    ledger = MatrixLedger()
    _record_games(ledger, submitted, opponents, [2, 2, 2, 2, 2, 2, 2, 1])  # 15/8
    _record_games(ledger, scored, opponents, [2, 2, 2, 2, 2, 2, 2, 1])  # 15/8

    modified = refresh_ratings(ledger, [submitted, scored] + opponents)

    assert submitted.status == Status.SUBMITTED
    assert scored.status == Status.SCORED
    # coverage-crossing candidates' rating/games/opponents fields still update
    # even though their status is frozen.
    modified_ids = {c.id for c in modified}
    assert submitted.id in modified_ids
    assert scored.id in modified_ids
    assert submitted.matrix_games == 15
    assert scored.matrix_games == 15


def test_refresh_ratings_excludes_retired_candidates():
    retired = _make("retired", Status.RETIRED)
    opponents = [_make(f"o{i}") for i in range(8)]
    ledger = MatrixLedger()
    _record_games(ledger, retired, opponents, [2, 2, 2, 2, 2, 2, 2, 1])  # 15/8

    modified = refresh_ratings(ledger, [retired] + opponents)

    modified_ids = {c.id for c in modified}
    assert retired.id not in modified_ids
    assert retired.matrix_games == 0  # untouched - never entered active_pool
    assert retired.status == Status.RETIRED
