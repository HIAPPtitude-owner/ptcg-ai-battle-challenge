"""Tests for the pre-registered asymmetric-test differential computation."""
from __future__ import annotations

import json

import pytest

from ptcg.factory.asym import Cell, compute, diff_ci, parse_experiments, write_decision


def test_diff_ci_matches_hand_verified_vector():
    # Hand-verified 2026-07-11: pt=0.6 (90/150), pc=0.5 (150/300)
    # se = sqrt(.24/150 + .25/300) = 0.0493288; d=0.1; CI = [0.0033155, 0.1966845]
    d, (lo, hi) = diff_ci(90, 150, 150, 300)
    assert d == pytest.approx(0.1)
    assert lo == pytest.approx(0.0033155, abs=1e-5)
    assert hi == pytest.approx(0.1966845, abs=1e-5)


def test_compute_pools_cells_and_decides_positive():
    cells = {
        "P1-A-T": Cell("P1-A-T", 90, 150),
        "P1-A-C": Cell("P1-A-C", 75, 150),
        "P1-B-C": Cell("P1-B-C", 75, 150),
    }
    r = compute(cells)
    assert r.pooled_treatment_wr == pytest.approx(0.6)
    assert r.pooled_control_wr == pytest.approx(0.5)
    assert r.differential == pytest.approx(0.1)
    assert r.per_cell["P1-A"] == pytest.approx(0.1)
    assert r.decision == "positive"  # CI [0.0033, 0.1967] wholly above 0


def test_compute_flat_when_ci_spans_zero():
    # Hand-verified 2026-07-11: both sides 150/300 -> d=0, CI = +/-0.0800167
    cells = {
        "P1-A-T": Cell("P1-A-T", 75, 150),
        "P1-B-T": Cell("P1-B-T", 75, 150),
        "P1-A-C": Cell("P1-A-C", 75, 150),
        "P1-B-C": Cell("P1-B-C", 75, 150),
    }
    r = compute(cells)
    assert r.differential == pytest.approx(0.0)
    assert r.ci[1] == pytest.approx(0.0800167, abs=1e-5)
    assert r.decision == "flat"


ROW = ("| 2026-07-12 | search-net-v2 | heuristic-v0 | x.csv | y.csv | 150 | 90 | 60 | 0 "
       "| 0.600 [0.520, 0.675] | 2.10 | 0.210 | slice7a-asym-P1-A-T dev=8.0% |")
CTRL = ("| 2026-07-12 | heuristic-v0 | heuristic-v0 | x.csv | y.csv | 150 | 75 | 75 | 0 "
        "| 0.500 [0.421, 0.579] | 0.09 | 0.002 | slice7a-asym-P1-A-C |")
VOIDED = ("| 2026-07-12 | search-net-v2 | heuristic-v0 | x.csv | y.csv | 150 | 10 | 5 | 0 "
          "| 0.667 [0.417, 0.848] | 2.10 | 0.210 | slice7a-asym-P1-B-T VOID crashed |")
DRYRUN = ("| 2026-07-12 | search-net-v2 | heuristic-v0 | x.csv | y.csv | 1 | 1 | 0 | 0 "
          "| 1.000 [0.207, 1.000] | 2.10 | 0.210 | slice7a-asym-dryrun |")


def test_parse_experiments_extracts_cells_skips_void_and_dryrun():
    cells = parse_experiments("\n".join(["# header", ROW, CTRL, VOIDED, DRYRUN]))
    assert set(cells) == {"P1-A-T", "P1-A-C"}
    assert cells["P1-A-T"].wins == 90 and cells["P1-A-T"].games == 150


def test_parse_experiments_accumulates_replicated_cells():
    cells = parse_experiments("\n".join([ROW, ROW]))
    assert cells["P1-A-T"].wins == 180 and cells["P1-A-T"].games == 300


def test_write_decision_round_trips(tmp_path):
    r = compute({"P1-A-T": Cell("P1-A-T", 90, 150), "P1-A-C": Cell("P1-A-C", 75, 150),
                 "P1-B-C": Cell("P1-B-C", 75, 150)})
    out = tmp_path / "asym_decision.json"
    write_decision(out, r)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["decision"] == "positive"
    assert doc["differential"] == pytest.approx(0.1)
