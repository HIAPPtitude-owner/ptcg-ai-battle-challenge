import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from generate_training_data import (V2_WEIGHTS, deck_pairings, main,  # noqa: E402
                                    make_agents, make_policy_target_agents)
from ptcg.search.value_net import ValueNetEvaluator  # noqa: E402
from ptcg.train.policy_targets import PolicyRecordingAgent  # noqa: E402
from ptcg.train.trajectory import DeviationTracker, RecordingAgent  # noqa: E402


def test_make_agents_search_gate_off_config():
    sink, tracker = [], DeviationTracker()
    deck = [3] * 60
    a0, a1 = make_agents("search", deck, deck, 200, "off", sink, tracker)
    for a in (a0, a1):
        assert isinstance(a, RecordingAgent)
        inner = a.inner
        assert inner.searcher.config.deviate_min_visits == 0
        assert inner.searcher.config.deviate_value_edge == 0.0
        assert inner.searcher.config.rollout_depth == 0
        assert abs(inner.tm.move_budget() - 0.2) < 1e-9
        assert a.tracker is tracker and a.v0_policy is not None


def test_make_agents_v0_has_no_tracker():
    sink, tracker = [], DeviationTracker()
    a0, a1 = make_agents("v0", [3] * 60, [3] * 60, 200, "off", sink, tracker)
    for a in (a0, a1):
        assert a.tracker is None and a.v0_policy is None


def test_deck_pairings_only_filter():
    one = [Path("a.csv")]
    assert deck_pairings(Path("ignored"), only=one) == [(Path("a.csv"), Path("a.csv"))]
    two = [Path("b.csv"), Path("a.csv")]  # sorted inside
    assert deck_pairings(Path("ignored"), only=two) == [
        (Path("a.csv"), Path("a.csv")),
        (Path("a.csv"), Path("b.csv")),
        (Path("b.csv"), Path("b.csv")),
    ]


def test_policy_targets_requires_search_agent():
    with pytest.raises(SystemExit):
        main(["--policy-targets", "--agent", "v0"])


def test_policy_targets_forces_gate_off():
    with pytest.raises(SystemExit) as exc:
        main(["--policy-targets", "--agent", "search", "--gate", "on"])
    assert "gate off" in str(exc.value)
    assert "trajector" in str(exc.value)


def test_make_policy_target_agents_wiring():
    sink: list = []
    deck = [3] * 60
    args = SimpleNamespace(policy_weights=None, tree_prior=False,
                           policy_opponent=False, policy_rollout=False)
    a0, a1 = make_policy_target_agents(deck, deck, 200, sink, args)
    v2 = ValueNetEvaluator.load(V2_WEIGHTS)
    for a in (a0, a1):
        assert isinstance(a, PolicyRecordingAgent)
        inner = a.inner
        cfg = inner.searcher.config
        assert cfg.rollout_depth == 0
        assert cfg.deviate_min_visits == 0
        assert cfg.deviate_value_edge == 0.0
        assert cfg.collect_stats is True
        assert isinstance(inner.searcher.evaluator, ValueNetEvaluator)
        assert inner.searcher.evaluator.net.layers == v2.net.layers
