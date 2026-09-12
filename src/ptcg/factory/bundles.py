"""Parametrized Kaggle bundle builder: Candidate -> submission.tar.gz (spec S1/S9).

Reuses scripts/package_submission.py wholesale. Heuristic candidates produce a
bundle IDENTICAL to the current pipeline. Search-net and search-policy (Slice
7B) candidates get the stdlib-pure search modules + weights JSON (+ a policy
weights JSON for search-policy) and a generated ptcg/agents/current.py written
into the STAGED copy only - main.py and the repo current.py never change.
"""
from __future__ import annotations

import shutil
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:  # scripts/ is a package at the repo root
    sys.path.insert(0, str(ROOT))

from scripts.package_submission import build_bundle, smoke_bundle, verify_bundle  # noqa: E402

from ptcg.factory.candidates import Candidate  # noqa: E402

SEARCH_MODULES = [
    "src/ptcg/agents/search_agent.py",
    "src/ptcg/search/__init__.py",
    # action_features.py and policy_net.py are required by EVERY search-kind
    # bundle (not just search-policy): search_agent.py imports PolicyNetPolicy
    # unconditionally at module load (Slice-7B policy-injection wiring), so a
    # search-net bundle missing these two modules fails at import time even
    # though it never constructs a policy. Verified via the bundle smoke test
    # (scripts/package_submission.smoke_bundle) - a stale module list here is
    # invisible to test_search_bundle_structure (name-membership only) and
    # only surfaces when the staged bundle is actually imported and run.
    "src/ptcg/search/action_features.py",
    "src/ptcg/search/belief.py",
    "src/ptcg/search/evaluate.py",
    "src/ptcg/search/features.py",
    "src/ptcg/search/policy_net.py",
    "src/ptcg/search/searcher.py",
    "src/ptcg/search/timing.py",
    "src/ptcg/search/tree.py",
    "src/ptcg/search/value_net.py",
]

# search-policy bundles currently need nothing beyond the base set above.
POLICY_MODULES = SEARCH_MODULES

# 480 s think-time bank: conservative margin under Kaggle's 600 s per-episode budget.
SEARCH_CURRENT_TEMPLATE = '''"""Factory-generated ladder identity for {candidate_id}. Do not edit."""
from pathlib import Path

from ptcg.agents.base import Agent
from ptcg.agents.search_agent import SearchAgent
from ptcg.search.searcher import SearchConfig
from ptcg.search.timing import TimeManager
from ptcg.search.value_net import ValueNetEvaluator

CURRENT_AGENT_NAME = "{candidate_id}"
CURRENT_DECK_PATH = Path("deck.csv")


def make_current_agent(deck: list) -> Agent:
    cfg = SearchConfig(rollout_depth={rollout_depth},
                       deviate_min_visits={deviate_min_visits},
                       deviate_value_edge={deviate_value_edge},
                       final_move_rule="{final_move_rule}",
                       use_root_prior={use_root_prior},
                       prior_tau={prior_tau},
                       c_puct={c_puct})
    weights = Path(__file__).resolve().parents[1] / "search" / "{weights_name}"
    agent = SearchAgent(deck, config=cfg,
                        time_manager=TimeManager(total_s=480.0,
                                                 max_move_s={max_move_s}),
                        evaluator=ValueNetEvaluator.load(weights))
    agent.name = "{candidate_id}"
    return agent
'''


POLICY_CURRENT_TEMPLATE = '''"""Factory-generated ladder identity for {candidate_id}. Do not edit."""
from pathlib import Path

from ptcg.agents.base import Agent
from ptcg.agents.search_agent import SearchAgent
from ptcg.search.searcher import SearchConfig
from ptcg.search.timing import TimeManager
from ptcg.search.value_net import ValueNetEvaluator

CURRENT_AGENT_NAME = "{candidate_id}"
CURRENT_DECK_PATH = Path("deck.csv")


def make_current_agent(deck: list) -> Agent:
    cfg = SearchConfig(rollout_depth={rollout_depth},
                       deviate_min_visits={deviate_min_visits},
                       deviate_value_edge={deviate_value_edge},
                       use_tree_prior={use_tree_prior},
                       policy_opponent={policy_opponent},
                       policy_rollout={policy_rollout})
    search_dir = Path(__file__).resolve().parents[1] / "search"
    weights = search_dir / "{weights_name}"
    policy_weights = search_dir / "{policy_weights_name}"
    agent = SearchAgent(deck, config=cfg,
                        time_manager=TimeManager(total_s=480.0,
                                                 max_move_s={max_move_s}),
                        evaluator=ValueNetEvaluator.load(weights),
                        policy_weights=policy_weights)
    agent.name = "{candidate_id}"
    return agent
'''


def generate_current_py(candidate: Candidate) -> str:
    cfg = candidate.agent_config
    if candidate.agent_kind == "search-policy":
        gate_off = cfg.get("gate", "off") == "off"
        return POLICY_CURRENT_TEMPLATE.format(
            candidate_id=candidate.id,
            rollout_depth=int(cfg.get("rollout_depth", 0)),
            deviate_min_visits=0 if gate_off else int(cfg.get("deviate_min_visits", 20)),
            deviate_value_edge=0.0 if gate_off else float(cfg.get("deviate_value_edge", 0.12)),
            use_tree_prior=bool(cfg.get("tree_prior", False)),
            policy_opponent=bool(cfg.get("policy_opponent", False)),
            policy_rollout=bool(cfg.get("policy_rollout", False)),
            weights_name=Path(cfg["net_weights"]).name,
            policy_weights_name=Path(cfg["policy_weights"]).name,
            max_move_s=float(cfg.get("search_budget_ms", 200)) / 1000.0,
        )
    return SEARCH_CURRENT_TEMPLATE.format(
        candidate_id=candidate.id,
        rollout_depth=int(cfg.get("rollout_depth", 12)),
        deviate_min_visits=int(cfg.get("deviate_min_visits", 20)),
        deviate_value_edge=float(cfg.get("deviate_value_edge", 0.12)),
        final_move_rule=str(cfg.get("final_move_rule", "most_visited")),
        use_root_prior=bool(cfg.get("use_root_prior", False)),
        prior_tau=float(cfg.get("prior_tau", 0.1)),
        c_puct=float(cfg.get("c_puct", 1.5)),
        weights_name=Path(cfg["net_weights"]).name,
        max_move_s=float(cfg.get("search_budget_ms", 200)) / 1000.0,
    )


def bundle_import_violations(tar_path: Path) -> list[str]:
    """Scan every .py member for torch/numpy imports (serve-time is pure stdlib)."""
    bad: list[str] = []
    with tarfile.open(tar_path) as tf:
        for member in tf.getmembers():
            if not member.name.endswith(".py"):
                continue
            source = tf.extractfile(member).read().decode("utf-8")
            for line in source.splitlines():
                stripped = line.strip()
                if stripped.startswith(("import torch", "from torch",
                                        "import numpy", "from numpy")):
                    bad.append(f"{member.name}: {stripped}")
    return bad


def build_candidate_bundle(candidate: Candidate, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    tar_path = build_bundle(ROOT / candidate.deck, out_dir)
    if candidate.agent_kind == "heuristic":
        return tar_path  # byte-identical to the existing pipeline
    if candidate.agent_kind not in ("search-net", "search-policy"):
        raise ValueError(f"unknown agent_kind {candidate.agent_kind!r}")
    staging = out_dir / "submission"
    (staging / "ptcg" / "agents" / "current.py").write_text(
        generate_current_py(candidate), encoding="utf-8")
    modules = (POLICY_MODULES if candidate.agent_kind == "search-policy"
              else SEARCH_MODULES)
    for mod in modules:
        dst = staging / Path(mod).relative_to("src")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / mod, dst)
    weights_src = ROOT / candidate.agent_config["net_weights"]
    shutil.copy(weights_src, staging / "ptcg" / "search" / weights_src.name)
    if candidate.agent_kind == "search-policy":
        policy_src = ROOT / candidate.agent_config["policy_weights"]
        shutil.copy(policy_src, staging / "ptcg" / "search" / policy_src.name)
    with tarfile.open(tar_path, "w:gz") as tf:
        for item in sorted(staging.iterdir()):
            tf.add(item, arcname=item.name)
    return tar_path


def verify_candidate_bundle(candidate: Candidate, tar_path: Path,
                            staging_dir: Path) -> None:
    """Every auto-built bundle passes verify + import scan + full-battle smoke
    before upload (spec S9). Raises RuntimeError/SystemExit on any failure."""
    problems = verify_bundle(tar_path)
    problems += bundle_import_violations(tar_path)
    if problems:
        raise RuntimeError(f"bundle verification failed for {candidate.id}: "
                           + "; ".join(problems))
    smoke_bundle(staging_dir)
