"""Unit tests for the pure helpers extracted from scripts/run_arena.py:
build_search_config(args) -> SearchConfig and write_sidecar(...) -> Path.
Neither helper runs an engine game, so these stay fast/unit-scoped."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import run_arena  # noqa: E402
from ptcg.arena.search_metrics import summarize  # noqa: E402
from ptcg.search.features import FEATURE_NAMES, FEATURE_VERSION  # noqa: E402
from ptcg.search.searcher import SearchStats  # noqa: E402


def _args(**kw):
    base = dict(deviate_min_visits=20, deviate_value_edge=0.12,
                deviate_min_visit_frac=0.0, rollout_depth=12,
                final_move_rule="most_visited", collect_stats=False,
                search_budget_ms=200, real_clock=False, net_weights=None,
                root_prior=False, prior_tau=0.1, c_puct=1.5,
                policy_weights=None, tree_prior=False, policy_opponent=False,
                policy_rollout=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_build_search_config_defaults_match_slice4():
    cfg = run_arena.build_search_config(_args())
    assert cfg.deviate_min_visits == 20
    assert cfg.deviate_value_edge == 0.12
    assert cfg.deviate_min_visit_frac == 0.0
    assert cfg.rollout_depth == 12
    assert cfg.final_move_rule == "most_visited"


def test_build_search_config_gate_off():
    cfg = run_arena.build_search_config(_args(deviate_min_visits=0, deviate_value_edge=0.0))
    assert cfg.deviate_min_visits == 0 and cfg.deviate_value_edge == 0.0


def test_search_net_agent_renamed_from_net_weights_stem(tmp_path):
    """AGENTS["search-net"] builds its evaluator from --net-weights when set,
    and the CLI-choice-keyed rename helper produces search-net-w for a
    value_net_weights_w.json-style stem (mirrors what main() does right
    after constructing agent_a/agent_b)."""
    spec = {"feature_version": FEATURE_VERSION, "feature_names": FEATURE_NAMES,
            "layers": [{"w": [[0.0] * len(FEATURE_NAMES)], "b": [0.0]}],
            "golden": [], "meta": {}}
    p = tmp_path / "w.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    args = _args(net_weights=str(p))

    agent = run_arena.AGENTS["search-net"]([3] * 60, args)
    run_arena._apply_net_weights_name(agent, "search-net", args)

    assert agent.name == "search-net-w"


def test_apply_net_weights_name_noop_without_flag():
    args = _args(net_weights=None)
    ns = argparse.Namespace(name="search-net-v1")
    run_arena._apply_net_weights_name(ns, "search-net", args)
    assert ns.name == "search-net-v1"


def test_apply_net_weights_name_keyed_on_cli_choice_not_kind():
    """Renaming is keyed on the CLI agent choice string, not the agent's
    own type — a "search" (no evaluator override) agent must not be
    renamed even if --net-weights happens to be set for the other side."""
    args = _args(net_weights="value_net_weights_v2.json")
    ns = argparse.Namespace(name="search-v1")
    run_arena._apply_net_weights_name(ns, "search", args)
    assert ns.name == "search-v1"


def test_build_search_config_carries_policy_injection_flags():
    cfg = run_arena.build_search_config(
        _args(tree_prior=True, policy_opponent=True, policy_rollout=True))
    assert cfg.use_tree_prior is True
    assert cfg.policy_opponent is True
    assert cfg.policy_rollout is True


def _policy_weights_file(tmp_path, name="policy.json"):
    spec = {"feature_version": FEATURE_VERSION, "action_feature_version": 2,
            "layers": [{"w": [[0.0]], "b": [0.0]}],
            "card_vocab": {}, "card_emb": [[0.0] * 8],
            "attack_vocab": {}, "attack_emb": [[0.0] * 4]}
    p = tmp_path / name
    p.write_text(json.dumps(spec), encoding="utf-8")
    return p


def test_search_policy_agent_defaults_to_v2_value_weights():
    """AGENTS["search-policy"] must default its value evaluator to
    value_net_weights_v2.json (never ValueNetEvaluator.load_default(),
    which is v1) when --net-weights is not passed."""
    from ptcg.search.value_net import ValueNetEvaluator

    agent = run_arena.AGENTS["search-policy"]([3] * 60, _args())
    expected = ValueNetEvaluator.load(run_arena.V2_WEIGHTS)
    assert agent.searcher.evaluator.net.layers == expected.net.layers


def test_search_policy_agent_wires_policy_weights_and_flags(tmp_path):
    weights_path = _policy_weights_file(tmp_path)
    args = _args(policy_weights=str(weights_path), tree_prior=True,
                policy_opponent=True, policy_rollout=True)

    agent = run_arena.AGENTS["search-policy"]([3] * 60, args)

    assert agent.policy is not None
    assert agent.name == "search-policy"


def test_apply_policy_weights_name_appends_generation_stem():
    args = _args(policy_weights="policy_net_weights_mega-lucario-fighting_gen1.json")
    ns = argparse.Namespace(name="search-policy")
    run_arena._apply_policy_weights_name(ns, "search-policy", args)
    assert ns.name == "search-policy-mega-lucario-fighting_gen1"


def test_apply_policy_weights_name_noop_without_flag():
    args = _args(policy_weights=None)
    ns = argparse.Namespace(name="search-policy")
    run_arena._apply_policy_weights_name(ns, "search-policy", args)
    assert ns.name == "search-policy"


def test_write_sidecar_is_utf8_json(tmp_path):
    stats = [SearchStats(iterations=10, root_children=2, top_child_visits=10,
                         top_child_value=0.5, v0_sig=(0,), search_sig=(1,),
                         most_visited_sig=(1,), deviated=True, gate_blocked=False,
                         begin_failures=0, step_failures=0)]
    summary = summarize(stats, [1])
    out = tmp_path / "probe.json"
    run_arena.write_sidecar(out, "slice5 D1 budget=200ms", summary, [1], 0.31)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["label"] == "slice5 D1 budget=200ms"
    assert doc["per_game_decisions"] == [1]
    assert doc["deviation_rate"] == 1.0
    assert doc["max_move_seconds"] == 0.31


def test_write_sidecar_collision_produces_distinct_files(tmp_path):
    """Two concurrent series sharing the same --notes label (and therefore the
    same date-derived filename) must not silently clobber each other's
    instrumentation JSON — the second write lands on a distinct sibling file
    and both files' content stays intact. Deterministic: same target path
    passed twice, no sleeps/timestamps involved."""
    stats = [SearchStats(iterations=10, root_children=2, top_child_visits=10,
                         top_child_value=0.5, v0_sig=(0,), search_sig=(1,),
                         most_visited_sig=(1,), deviated=True, gate_blocked=False,
                         begin_failures=0, step_failures=0)]
    summary_a = summarize(stats, [1])
    summary_b = summarize(stats, [2])
    target = tmp_path / "2026-07-10-shared-notes.json"

    path_a = run_arena.write_sidecar(target, "series A", summary_a, [1], 0.31)
    path_b = run_arena.write_sidecar(target, "series B", summary_b, [2], 0.42)

    assert path_a == target
    assert path_b != target
    assert path_a.exists() and path_b.exists()

    doc_a = json.loads(path_a.read_text(encoding="utf-8"))
    doc_b = json.loads(path_b.read_text(encoding="utf-8"))
    assert doc_a["label"] == "series A"
    assert doc_a["per_game_decisions"] == [1]
    assert doc_b["label"] == "series B"
    assert doc_b["per_game_decisions"] == [2]
