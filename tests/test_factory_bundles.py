"""Tests for the parametrized candidate bundle builder."""
from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from ptcg.factory.bundles import (POLICY_MODULES, SEARCH_MODULES,
                                  build_candidate_bundle,
                                  bundle_import_violations, generate_current_py)
from ptcg.factory.candidates import Candidate
from scripts.package_submission import build_bundle, smoke_bundle, verify_bundle

ROOT = Path(__file__).resolve().parents[1]
DECK = "src/ptcg/decks/candidates/mega-lucario-fighting.csv"


def _heuristic_cand() -> Candidate:
    return Candidate.create(name="mega-lucario-fighting-heuristic", version="v1.0",
                            deck=DECK, agent_kind="heuristic")


def _search_cand() -> Candidate:
    return Candidate.create(
        name="mega-lucario-fighting-searchnet", version="v1.0", deck=DECK,
        agent_kind="search-net",
        agent_config={"search_budget_ms": 200, "rollout_depth": 12,
                      "use_root_prior": True, "prior_tau": 0.05, "c_puct": 1.5,
                      "net_weights": "src/ptcg/search/value_net_weights_v2.json"})


def _policy_weights_file(tmp_path) -> Path:
    spec = {"feature_version": 1, "action_feature_version": 2,
            "layers": [{"w": [[0.0]], "b": [0.0]}],
            "card_vocab": {}, "card_emb": [[0.0] * 8],
            "attack_vocab": {}, "attack_emb": [[0.0] * 4]}
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "policy_net_weights_mega-lucario-fighting_gen1.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    return p


def _policy_cand(policy_weights: Path) -> Candidate:
    return Candidate.create(
        name="mega-lucario-fighting-policyloop", version="v0.1", deck=DECK,
        agent_kind="search-policy",
        agent_config={"search_budget_ms": 200, "rollout_depth": 0,
                      "net_weights": "src/ptcg/search/value_net_weights_v2.json",
                      "policy_weights": str(policy_weights),
                      "tree_prior": True, "policy_opponent": True,
                      "policy_rollout": True, "gate": "off"})


def test_generated_current_py_compiles_and_pins_config():
    src = generate_current_py(_search_cand())
    compile(src, "current.py", "exec")  # syntactically valid
    assert "prior_tau=0.05" in src
    assert "use_root_prior=True" in src
    assert "value_net_weights_v2.json" in src
    assert "max_move_s=0.2" in src
    assert "total_s=480.0" in src  # per-match clock safety margin under Kaggle's 600 s


def _tar_with(py_source: str, tmp_path: Path) -> Path:
    tar_path = tmp_path / "t.tar.gz"
    data = py_source.encode("utf-8")
    with tarfile.open(tar_path, "w:gz") as tf:
        info = tarfile.TarInfo("mod.py")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return tar_path


def test_import_scan_flags_torch_and_numpy(tmp_path):
    assert bundle_import_violations(_tar_with("import torch\n", tmp_path))
    assert bundle_import_violations(_tar_with("from numpy import array\n", tmp_path))
    assert bundle_import_violations(_tar_with("import json\n", tmp_path)) == []


def test_heuristic_candidate_bundle_matches_baseline(tmp_path):
    baseline = build_bundle(ROOT / DECK, tmp_path / "a")
    cand_tar = build_candidate_bundle(_heuristic_cand(), tmp_path / "b")
    with tarfile.open(baseline) as ta, tarfile.open(cand_tar) as tb:
        assert sorted(ta.getnames()) == sorted(tb.getnames())
        for member in ("main.py", "deck.csv", "ptcg/agents/current.py"):
            assert ta.extractfile(member).read() == tb.extractfile(member).read()
    assert verify_bundle(cand_tar) == []
    assert bundle_import_violations(cand_tar) == []


def test_search_bundle_structure(tmp_path):
    tar = build_candidate_bundle(_search_cand(), tmp_path)
    with tarfile.open(tar) as tf:
        names = set(tf.getnames())
        for mod in SEARCH_MODULES:
            assert Path(mod).relative_to("src").as_posix() in names
        assert "ptcg/search/value_net_weights_v2.json" in names
        current = tf.extractfile("ptcg/agents/current.py").read().decode("utf-8")
    assert "SearchAgent" in current and "prior_tau=0.05" in current
    assert verify_bundle(tar) == []
    assert bundle_import_violations(tar) == []


@pytest.mark.slow
def test_search_bundle_smokes(tmp_path):
    build_candidate_bundle(_search_cand(), tmp_path)
    smoke_bundle(tmp_path / "submission")  # raises SystemExit on failure


def test_generated_policy_current_py_compiles_and_pins_config(tmp_path):
    weights = _policy_weights_file(tmp_path)
    src = generate_current_py(_policy_cand(weights))
    compile(src, "current.py", "exec")  # syntactically valid
    assert "use_tree_prior=True" in src
    assert "policy_opponent=True" in src
    assert "policy_rollout=True" in src
    assert "deviate_min_visits=0" in src  # gate == "off"
    assert "deviate_value_edge=0.0" in src
    assert "value_net_weights_v2.json" in src
    assert "policy_net_weights_mega-lucario-fighting_gen1.json" in src
    assert "policy_weights=policy_weights" in src


def test_policy_bundle_structure(tmp_path):
    weights = _policy_weights_file(tmp_path / "src")
    tar = build_candidate_bundle(_policy_cand(weights), tmp_path / "out")
    with tarfile.open(tar) as tf:
        names = set(tf.getnames())
        for mod in POLICY_MODULES:
            assert Path(mod).relative_to("src").as_posix() in names
        assert "ptcg/search/value_net_weights_v2.json" in names
        assert "ptcg/search/policy_net_weights_mega-lucario-fighting_gen1.json" in names
        current = tf.extractfile("ptcg/agents/current.py").read().decode("utf-8")
    assert "SearchAgent" in current and "policy_weights=policy_weights" in current
    assert verify_bundle(tar) == []
    assert bundle_import_violations(tar) == []


@pytest.mark.slow
def test_policy_bundle_smokes(tmp_path):
    weights = _policy_weights_file(tmp_path / "src")
    build_candidate_bundle(_policy_cand(weights), tmp_path / "out")
    result = smoke_bundle(tmp_path / "out" / "submission")  # raises SystemExit on failure
    # A rejected/incompatible policy spec doesn't fail the smoke test outright —
    # SearchAgent's warn-and-disable ladder catches it and falls back to v0,
    # which still prints "SMOKE OK" to stdout. Assert the policy path actually
    # loaded (no fallback warning on stderr), so this test can't silently pass
    # while exercising only the v0 fallback instead of the policy-net it targets.
    assert "policy-net load failed" not in result.stderr
