"""Trainer implementations for the daemon. 7A occupant: PerDeckNetTrainer.

Honest framing (spec S8, recorded): value-net-quality improvements on the
mega-lucario mirror are a PROVEN dead-end for win rate (Slices 4-6). This
trainer's value is (a) proving the continuous-training infrastructure end to
end and (b) distribution coverage - all existing nets were trained exclusively
on mega-lucario mirror data, so search candidates on the other 8 decks
currently run an off-distribution net. The win-rate hypothesis lives in 7B's
policy-improvement trainer, which plugs into the same Trainer protocol."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ptcg.factory.candidates import Candidate, next_version

ROOT = Path(__file__).resolve().parents[3]

# A hung generation/training subprocess must not wedge the daemon forever
# (Task-11 carry-forward finding: wedged-trainer hazard). 24h is generous
# enough to never fire on a legitimate long training run, but it guarantees
# the daemon's crash isolation (run_daemon's except-block) eventually takes
# over instead of blocking indefinitely.
SUBPROCESS_TIMEOUT_S = 24 * 3600


@dataclass
class PerDeckNetTrainer:
    deck: Path
    games_per_cycle: int = 300
    epochs: int = 30
    data_dir: Path = field(default_factory=lambda: ROOT / "experiments" / "data" / "slice7a")
    weights_dir: Path = field(default_factory=lambda: ROOT / "src" / "ptcg" / "search")
    priority: float = 0.3
    runner: Callable = subprocess.run
    name: str = "per-deck-net"

    @property
    def slug(self) -> str:
        return Path(self.deck).stem

    def _run(self, args: list[str]) -> None:
        proc = self.runner([sys.executable, *args], cwd=ROOT,
                           timeout=SUBPROCESS_TIMEOUT_S)
        code = getattr(proc, "returncode", 0)
        if code != 0:
            raise RuntimeError(f"{args[0]} exited {code}")

    def prepare_data(self, cycle: int) -> Path:
        out = Path(self.data_dir) / f"{self.slug}-c{cycle}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        self._run(["scripts/generate_training_data.py",
                   "--decks", str(self.deck),
                   "--games-per-pairing", str(self.games_per_cycle),
                   "--seed", str(7 + cycle),
                   "--out", str(out)])
        return out

    def train(self, data_path: Path, cycle: int) -> Path:
        out = Path(self.weights_dir) / f"value_net_weights_{self.slug}.json"
        self._run(["scripts/train_value_net.py",
                   "--data", str(data_path),
                   "--out", str(out),
                   "--epochs", str(self.epochs)])
        return out

    def export_and_register(self, weights_path: Path, cycle: int,
                            candidates: list[Candidate]) -> Candidate:
        name = f"{self.slug}-searchnet"
        version = next_version(candidates, name)
        weights_rel = Path(weights_path)
        if weights_rel.is_absolute():
            try:
                weights_rel = weights_rel.relative_to(ROOT)
            except ValueError:
                pass  # outside ROOT (e.g. a test's tmp_path) - keep absolute
        deck_rel = Path(self.deck)
        if deck_rel.is_absolute():
            try:
                deck_rel = deck_rel.relative_to(ROOT)
            except ValueError:
                pass  # outside ROOT (e.g. a test's tmp_path) - keep absolute
        return Candidate.create(
            name=name, version=version,
            deck=deck_rel.as_posix(),
            agent_kind="search-net",
            agent_config={"search_budget_ms": 200, "rollout_depth": 12,
                          "net_weights": weights_rel.as_posix()},
            provenance=f"daemon:{self.name}:cycle{cycle}",
            priority=self.priority,
            novel_axis=version == "v0.1")


@dataclass
class PolicyImprovementTrainer:
    """7B occupant of the Trainer protocol: expert iteration. Cycle k trains
    generation k+1; prepare_data(k) injects generation k's net (if its weights
    file exists) into the self-play searchers, closing the improvement loop."""
    deck: Path
    games_per_cycle: int = 400
    epochs: int = 40
    data_dir: Path = field(default_factory=lambda: ROOT / "experiments" / "data" / "slice7b")
    weights_dir: Path = field(default_factory=lambda: ROOT / "src" / "ptcg" / "search")
    value_weights: str = "src/ptcg/search/value_net_weights_v2.json"
    priority: float = 0.6
    runner: Callable = subprocess.run
    name: str = "policy-improvement"

    @property
    def slug(self) -> str:
        return Path(self.deck).stem

    def _policy_weights_path(self, gen: int) -> Path:
        return Path(self.weights_dir) / f"policy_net_weights_{self.slug}_gen{gen}.json"

    def _run(self, args: list[str]) -> None:
        proc = self.runner([sys.executable, *args], cwd=ROOT,
                           timeout=SUBPROCESS_TIMEOUT_S)
        code = getattr(proc, "returncode", 0)
        if code != 0:
            raise RuntimeError(f"{args[0]} exited {code}")

    def prepare_data(self, cycle: int) -> Path:
        out = Path(self.data_dir) / f"{self.slug}-policy-c{cycle}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        args = ["scripts/generate_training_data.py", "--agent", "search",
                "--policy-targets", "--gate", "off",
                "--decks", str(self.deck),
                "--games-per-pairing", str(self.games_per_cycle),
                "--seed", str(7 + cycle), "--out", str(out)]
        prev = self._policy_weights_path(cycle)  # gen k, written by cycle k-1
        if prev.exists():
            args += ["--policy-weights", str(prev), "--tree-prior",
                     "--policy-opponent", "--policy-rollout"]
        self._run(args)
        return out

    def train(self, data_path: Path, cycle: int) -> Path:
        out = self._policy_weights_path(cycle + 1)
        self._run(["scripts/train_policy_net.py", "--data", str(data_path),
                   "--out", str(out), "--epochs", str(self.epochs)])
        return out

    def export_and_register(self, weights_path: Path, cycle: int,
                            candidates: list[Candidate]) -> Candidate:
        name = f"{self.slug}-policyloop"
        version = next_version(candidates, name)
        rel = Path(weights_path)
        if rel.is_absolute():
            try:
                rel = rel.relative_to(ROOT)
            except ValueError:
                pass  # outside ROOT (e.g. a test's tmp_path) - keep absolute
        return Candidate.create(
            name=name, version=version,
            deck=f"src/ptcg/decks/candidates/{self.slug}.csv",
            agent_kind="search-policy",
            agent_config={"search_budget_ms": 200, "rollout_depth": 0,
                          "net_weights": self.value_weights,
                          "policy_weights": rel.as_posix(),
                          "tree_prior": True, "policy_opponent": True,
                          "policy_rollout": True, "gate": "off"},
            provenance=f"daemon:{self.name}:cycle{cycle}",
            priority=self.priority,
            novel_axis=version == "v0.1")
