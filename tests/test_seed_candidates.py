"""Tests for the initial candidate pool seeding."""
from __future__ import annotations

import json

from scripts.seed_candidates import build_seed, search_priority, tournament_priorities


def test_tournament_priorities_pools_wins(tmp_path):
    doc = {"version": 1,
           "decks": {"h1": "alpha.csv", "h2": "beta.csv"},
           "pairings": [{"deck_a": "h1", "deck_b": "h2", "agent": "heuristic-v0",
                         "wins_a": 30, "wins_b": 20, "draws": 0, "games": 50,
                         "discarded": 0}]}
    p = tmp_path / "results.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    prios = tournament_priorities(p)
    # Hand-verified 2026-07-11: alpha 30/50 = 0.6, beta 20/50 = 0.4
    assert prios == {"alpha": 0.6, "beta": 0.4}


def test_search_priority_reads_decision(tmp_path):
    p = tmp_path / "asym_decision.json"
    p.write_text(json.dumps({"decision": "positive"}), encoding="utf-8")
    assert search_priority(p) == 0.90
    p.write_text(json.dumps({"decision": "flat"}), encoding="utf-8")
    assert search_priority(p) == 0.30


def test_build_seed_shapes_the_pool():
    prios = {"mega-lucario-fighting": 0.747, "mega-starmie-water": 0.675,
             "mega-lucario-v4": 0.676, "aggro-lightning": 0.040}
    seeds = build_seed(prios, search_prio=0.30)
    ids = {c.id for c in seeds}
    # one heuristic candidate per deck
    assert "mega-lucario-fighting-heuristic-v1.0" in ids
    assert "aggro-lightning-heuristic-v1.0" in ids
    # search variants on the top decks + the root-prior variant
    assert "mega-lucario-fighting-searchnet-v1.0" in ids
    assert "mega-lucario-fighting-searchnet-prior-v1.0" in ids
    assert "mega-starmie-water-searchnet-v1.0" in ids
    assert "mega-lucario-v4-searchnet-v1.0" in ids
    by_id = {c.id: c for c in seeds}
    # the current ladder identity is NOT a novel axis; everything else is
    assert by_id["mega-lucario-fighting-heuristic-v1.0"].novel_axis is False
    assert by_id["aggro-lightning-heuristic-v1.0"].novel_axis is True
    sn = by_id["mega-lucario-fighting-searchnet-v1.0"]
    assert sn.priority == 0.30 and sn.novel_axis is True
    assert sn.agent_config["net_weights"] == "src/ptcg/search/value_net_weights_v2.json"
    prior = by_id["mega-lucario-fighting-searchnet-prior-v1.0"]
    assert prior.agent_config["use_root_prior"] is True
