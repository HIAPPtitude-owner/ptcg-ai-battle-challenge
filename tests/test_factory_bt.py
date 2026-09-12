"""Tests for the pure-stdlib Bradley-Terry solver."""
import math

from ptcg.factory.bt import fit_ratings, head_to_head_p


def test_two_player_analytic():
    # A beats B 3, B beats A 1 -> with +0.1 smoothing both ways:
    # effective wins A=3.1, B=1.1 -> P(A beats B) = 3.1/4.2
    ratings = fit_ratings({("A", "B"): 3, ("B", "A"): 1})
    p = head_to_head_p(ratings, "A", "B")
    assert math.isclose(p, 3.1 / 4.2, rel_tol=1e-6)


def test_symmetric_round_robin_all_equal():
    wins = {}
    ids = ["A", "B", "C"]
    for i in ids:
        for j in ids:
            if i != j:
                wins[(i, j)] = 5  # everyone 5-5 vs everyone
    ratings = fit_ratings(wins)
    vals = list(ratings.values())
    assert max(vals) / min(vals) < 1.0001


def test_dominance_ordering():
    # A dominates B dominates C -> rating order must follow
    wins = {("A", "B"): 8, ("B", "A"): 2, ("B", "C"): 8, ("C", "B"): 2,
            ("A", "C"): 9, ("C", "A"): 1}
    r = fit_ratings(wins)
    assert r["A"] > r["B"] > r["C"]


def test_undefeated_candidate_finite():
    r = fit_ratings({("A", "B"): 10})  # B never wins
    assert 0 < r["B"] < r["A"] < float("inf")


def test_normalization_geometric_mean_one():
    r = fit_ratings({("A", "B"): 3, ("B", "A"): 1, ("B", "C"): 2, ("C", "B"): 2})
    gm = math.exp(sum(math.log(v) for v in r.values()) / len(r))
    assert math.isclose(gm, 1.0, rel_tol=1e-6)
