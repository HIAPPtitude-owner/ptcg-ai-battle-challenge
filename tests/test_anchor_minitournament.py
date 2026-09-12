"""Tests for the anchor mini-tournament pure tally/selection logic and the
`run_tournament` aggregator (min-basics-pool-rule Task 6, design 2). Every
test feeds synthetic `SeriesStats` via an injected `play_series` -- no
real games are played, per the brief's "write the pure tally/selection
logic first with tests (no games -- feed synthetic results)" directive.
The real round-robin + reference series (real games, real wall-clock) is
Task 7, orchestrator-owned; this file only proves the wiring is correct.
"""
from __future__ import annotations

import pytest

from ptcg.arena.stats import SeriesStats

from scripts.run_anchor_minitournament import (
    TournamentResult,
    pick_winner,
    pooled_score,
    run_tournament,
)


class TestPooledScore:
    def test_hand_verified_value(self):
        # (120 + 0.5*10) / 200 = (120 + 5) / 200 = 125 / 200 = 0.625
        assert pooled_score(120, 10, 200) == 0.625

    def test_no_draws(self):
        assert pooled_score(50, 0, 100) == 0.5

    def test_all_draws(self):
        assert pooled_score(0, 100, 100) == 0.5


class TestPickWinner:
    def test_single_candidate_returns_it(self):
        # degenerate-probe: a one-candidate dict must not crash and must
        # return that candidate (loader/aggregator degenerate-input rule,
        # .claude/rules/plan-test-arithmetic-sanity.md).
        assert pick_winner({"only": 0.5}, {}) == "only"

    def test_clear_winner_no_tie(self):
        assert pick_winner({"a": 0.7, "b": 0.4}, {}) == "a"

    def test_two_way_tie_broken_by_head_to_head_favoring_a(self):
        scores = {"a": 0.5, "b": 0.5}
        head_to_head = {("a", "b"): 0.55}
        assert pick_winner(scores, head_to_head) == "a"

    def test_two_way_tie_broken_by_head_to_head_favoring_b(self):
        scores = {"a": 0.5, "b": 0.5}
        head_to_head = {("a", "b"): 0.45}
        assert pick_winner(scores, head_to_head) == "b"

    def test_two_way_tie_missing_head_to_head_defaults_to_a(self):
        # head_to_head.get(..., 0.5) >= 0.5 -> the lexicographically-first
        # tied candidate wins when no head-to-head data is recorded.
        scores = {"a": 0.5, "b": 0.5}
        assert pick_winner(scores, {}) == "a"

    def test_three_way_tie_compares_first_two_lexicographically(self):
        # >2-way tie: only the first two (sorted) tied names are compared;
        # the third ("c") is never consulted even though it is also tied.
        scores = {"c": 0.6, "a": 0.6, "b": 0.6}
        head_to_head = {("a", "b"): 0.4}  # b beats a head-to-head
        assert pick_winner(scores, head_to_head) == "b"


def _stats(wins_a: int, wins_b: int, draws: int) -> SeriesStats:
    n = wins_a + wins_b + draws
    return SeriesStats(wins_a=wins_a, wins_b=wins_b, draws=draws,
                       game_seconds=[0.01] * n)


def _table_play_series(table: dict[tuple[int, int], SeriesStats]):
    """Builds an injectable `play_series` that looks up a canned
    `SeriesStats` by the decks' single-card int tags, so `run_tournament`'s
    aggregation can be tested with zero real games."""

    def play_series(deck_a: list[int], deck_b: list[int], n_games: int) -> SeriesStats:
        return table[(deck_a[0], deck_b[0])]

    return play_series


class TestRunTournament:
    def test_round_robin_aggregation_and_head_to_head(self):
        # 3 candidates, tags 1/2/3. Round-robin combinations in insertion
        # order: (x,y), (x,z), (y,z) -- itertools.combinations preserves
        # dict key order.
        table = {
            (1, 2): _stats(wins_a=7, wins_b=3, draws=0),  # x vs y: 10 games
            (1, 3): _stats(wins_a=6, wins_b=2, draws=2),  # x vs z: 10 games
            (2, 3): _stats(wins_a=5, wins_b=5, draws=0),  # y vs z: 10 games
        }
        decks = {"x": [1], "y": [2], "z": [3]}

        result = run_tournament(decks, games_per_pair=10,
                                play_series=_table_play_series(table))

        assert isinstance(result, TournamentResult)
        # x: wins=7+6=13, draws=0+2=2, games=20 -> (13+1)/20 = 0.700
        assert result.scores["x"] == pytest.approx(0.7)
        # y: wins=3+5=8, draws=0, games=20 -> 8/20 = 0.400
        assert result.scores["y"] == pytest.approx(0.4)
        # z: wins=2+5=7, draws=2+0=2, games=20 -> (7+1)/20 = 0.400
        assert result.scores["z"] == pytest.approx(0.4)
        # x is the unique max score -> winner without needing a tie-break.
        assert result.winner == "x"

        # head-to-head is recorded both directions per pair.
        assert result.head_to_head[("x", "y")] == pytest.approx(0.7)
        assert result.head_to_head[("y", "x")] == pytest.approx(0.3)
        # x vs z: wins_a=6, draws=2, games=10 -> (6+1)/10 = 0.700
        assert result.head_to_head[("x", "z")] == pytest.approx(0.7)
        # wins_b=2, draws=2, games=10 -> (2+1)/10 = 0.300
        assert result.head_to_head[("z", "x")] == pytest.approx(0.3)

        assert result.pair_stats[("x", "y")] is table[(1, 2)]
        assert result.pair_stats[("x", "z")] is table[(1, 3)]
        assert result.pair_stats[("y", "z")] is table[(2, 3)]

    def test_reference_series_recorded_but_not_a_gate(self):
        table = {
            (1, 2): _stats(wins_a=5, wins_b=5, draws=0),
            (1, 9): _stats(wins_a=4, wins_b=1, draws=0),
            (2, 9): _stats(wins_a=2, wins_b=3, draws=0),
        }
        decks = {"x": [1], "y": [2]}

        result = run_tournament(
            decks, games_per_pair=10,
            reference_deck=("old-anchor", [9]), reference_games=5,
            play_series=_table_play_series(table),
        )

        assert result.reference_stats["x"] is table[(1, 9)]
        assert result.reference_stats["y"] is table[(2, 9)]
        # reference series never touches scores/winner -- both x and y
        # tied 0.5 on round-robin alone (only pair played), and the
        # reference results (4-1, 2-3) play no part in that tie-break.
        assert result.scores["x"] == pytest.approx(0.5)
        assert result.scores["y"] == pytest.approx(0.5)

    def test_no_reference_deck_leaves_reference_stats_empty(self):
        table = {(1, 2): _stats(wins_a=6, wins_b=4, draws=0)}
        decks = {"x": [1], "y": [2]}

        result = run_tournament(decks, games_per_pair=10,
                                play_series=_table_play_series(table))

        assert result.reference_stats == {}

    def test_single_candidate_degenerate_no_games_played(self):
        # loader/aggregator degenerate-input probe: a single-candidate
        # dict has zero round-robin pairs (C(1,2) = 0) and must not divide
        # by zero. `play_series` must never be called.
        def play_series_should_not_be_called(deck_a, deck_b, n_games):
            raise AssertionError("play_series must not be called for a single candidate")

        result = run_tournament({"only": [7]}, games_per_pair=10,
                                play_series=play_series_should_not_be_called)

        assert result.winner == "only"
        assert result.pair_stats == {}
