"""Tests for the two-factor (agent x deck) Bradley-Terry fit (`fit_two_factor`).

Deterministic throughout: expected win counts are computed directly from the
planted log-strengths via the sigmoid formula, never sampled with live RNG.
"""
import math

from ptcg.factory.bt import fit_two_factor
from ptcg.factory.genomes import cell_id

AGENT_PLANTED = {"ag-strong": 0.8, "ag-weak": -0.8}
DECK_PLANTED = {"dk-good": 0.5, "dk-bad": -0.5}
CELLS = [(ag, dk) for ag in AGENT_PLANTED for dk in DECK_PLANTED]


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _strength(cell: tuple[str, str]) -> float:
    ag, dk = cell
    return AGENT_PLANTED[ag] + DECK_PLANTED[dk]


def _synthetic_wins() -> dict[tuple[str, str], int]:
    """For every ordered cell pair, wins[(A,B)] = round(200*sigmoid(sA-sB))."""
    wins: dict[tuple[str, str], int] = {}
    for i, ci in enumerate(CELLS):
        for j, cj in enumerate(CELLS):
            if i == j:
                continue
            a_cell, b_cell = cell_id(*ci), cell_id(*cj)
            w = round(200 * _sigmoid(_strength(ci) - _strength(cj)))
            wins[(a_cell, b_cell)] = w
    return wins


def test_hand_check_sigmoid_arithmetic():
    # cell(strong,good) = +1.3, cell(weak,bad) = -1.3 -> diff 2.6.
    # sigmoid(2.6) = 1/(1+exp(-2.6)) ~= 0.93087 -> round(200*0.93087) = 186.
    assert math.isclose(_sigmoid(2.6), 0.9309, abs_tol=1e-4)
    assert round(200 * _sigmoid(2.6)) == 186


def test_synthetic_recovery_orderings_and_gaps():
    wins = _synthetic_wins()
    a, d = fit_two_factor(wins)
    assert a["ag-strong"] > a["ag-weak"]
    assert d["dk-good"] > d["dk-bad"]
    assert abs(a["ag-strong"] - 0.8) < 0.25
    assert abs(a["ag-weak"] - (-0.8)) < 0.25
    assert abs(d["dk-good"] - 0.5) < 0.25
    assert abs(d["dk-bad"] - (-0.5)) < 0.25


def test_zero_mean_identifiability():
    wins = _synthetic_wins()
    a, d = fit_two_factor(wins)
    assert abs(sum(a.values())) < 1e-6
    assert abs(sum(d.values())) < 1e-6


def test_legacy_ids_skipped_not_fatal():
    wins_before = _synthetic_wins()
    a_before, d_before = fit_two_factor(dict(wins_before))

    wins_after = dict(wins_before)
    wins_after[("old-id-a", "old-id-b")] = 5
    a_after, d_after = fit_two_factor(wins_after)  # must not raise

    assert a_after == a_before
    assert d_after == d_before
    assert "old-id-a" not in a_after and "old-id-a" not in d_after
    assert "old-id-b" not in a_after and "old-id-b" not in d_after


def test_empty_input():
    assert fit_two_factor({}) == ({}, {})


def test_single_cell_degenerate_input():
    cid = cell_id("ag-x", "dk-y")
    a, d = fit_two_factor({(cid, cid): 0})
    assert a == {"ag-x": 0.0}
    assert d == {"dk-y": 0.0}
