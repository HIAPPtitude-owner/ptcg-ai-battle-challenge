"""Tests for PerDeckNetTrainer and PolicyImprovementTrainer (subprocess
commands stubbed - no real training)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ptcg.factory.trainers import PerDeckNetTrainer, PolicyImprovementTrainer


def _fake_runner(calls):
    def run(cmd, **kw):
        calls.append([str(c) for c in cmd])
        out = Path(cmd[cmd.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=0)
    return run


def test_prepare_train_register_pipeline(tmp_path):
    calls: list[list[str]] = []
    trainer = PerDeckNetTrainer(
        deck=Path("src/ptcg/decks/candidates/mega-starmie-water.csv"),
        games_per_cycle=5, epochs=2, data_dir=tmp_path / "data",
        weights_dir=tmp_path / "weights", priority=0.3,
        runner=_fake_runner(calls))

    data = trainer.prepare_data(0)
    assert data.name == "mega-starmie-water-c0.jsonl"
    gen = calls[0]
    assert "scripts/generate_training_data.py" in " ".join(gen)
    assert "--decks" in gen and "--games-per-pairing" in gen
    assert gen[gen.index("--games-per-pairing") + 1] == "5"

    weights = trainer.train(data, 0)
    assert weights.name == "value_net_weights_mega-starmie-water.json"
    train = calls[1]
    assert "scripts/train_value_net.py" in " ".join(train)
    assert train[train.index("--epochs") + 1] == "2"

    cand = trainer.export_and_register(weights, 0, [])
    assert cand.id == "mega-starmie-water-searchnet-v0.1"
    assert cand.novel_axis is True and cand.priority == 0.3
    assert cand.agent_config["net_weights"].endswith(
        "value_net_weights_mega-starmie-water.json")
    assert cand.provenance == "daemon:per-deck-net:cycle0"


def test_register_bumps_existing_version(tmp_path):
    from ptcg.factory.candidates import Candidate
    existing = [Candidate.create(name="mega-starmie-water-searchnet", version="v0.3",
                                 deck="d.csv", agent_kind="search-net")]
    trainer = PerDeckNetTrainer(
        deck=Path("src/ptcg/decks/candidates/mega-starmie-water.csv"),
        data_dir=tmp_path, weights_dir=tmp_path, runner=_fake_runner([]))
    cand = trainer.export_and_register(tmp_path / "w.json", 4, existing)
    assert cand.version == "v0.4" and cand.novel_axis is False


def _policy_trainer(tmp_path, **kw) -> tuple[PolicyImprovementTrainer, list]:
    calls: list[list[str]] = []
    trainer = PolicyImprovementTrainer(
        deck=Path("src/ptcg/decks/candidates/mega-lucario-fighting.csv"),
        games_per_cycle=5, epochs=2, data_dir=tmp_path / "data",
        weights_dir=tmp_path / "w", runner=_fake_runner(calls), **kw)
    return trainer, calls


def test_policy_trainer_gen1_no_prior_weights(tmp_path):
    trainer, calls = _policy_trainer(tmp_path)
    out = trainer.prepare_data(0)  # cycle 0 -> looks for gen0, absent
    argv = calls[0]
    assert "--policy-targets" in argv and "--gate" in argv
    assert "--policy-weights" not in argv  # gen-1: no prior net to inject
    assert "--tree-prior" not in argv
    assert out.parent.exists()  # virgin-directory contract


def test_policy_trainer_gen2_injects_previous_weights(tmp_path):
    trainer, calls = _policy_trainer(tmp_path)
    prev = trainer._policy_weights_path(1)  # cycle 1 -> looks for gen1
    prev.parent.mkdir(parents=True, exist_ok=True)
    prev.write_text("{}", encoding="utf-8")

    trainer.prepare_data(1)
    argv = calls[0]
    assert "--policy-weights" in argv and str(prev) in argv
    assert "--tree-prior" in argv and "--policy-opponent" in argv
    assert "--policy-rollout" in argv


def test_policy_trainer_train_writes_next_generation(tmp_path):
    trainer, calls = _policy_trainer(tmp_path)
    data = trainer.data_dir / "fake.jsonl"
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text("{}\n", encoding="utf-8")

    weights = trainer.train(data, 0)  # cycle 0 -> writes gen1
    assert weights.name == "policy_net_weights_mega-lucario-fighting_gen1.json"
    train_argv = calls[0]
    assert "scripts/train_policy_net.py" in " ".join(train_argv)
    assert train_argv[train_argv.index("--epochs") + 1] == "2"

    weights2 = trainer.train(data, 1)  # cycle 1 -> writes gen2
    assert weights2.name == "policy_net_weights_mega-lucario-fighting_gen2.json"


def test_policy_trainer_register_shape(tmp_path):
    trainer, _calls = _policy_trainer(tmp_path)
    weights_path = tmp_path / "w" / "policy_net_weights_mega-lucario-fighting_gen1.json"
    cand = trainer.export_and_register(weights_path, 0, [])
    assert cand.agent_kind == "search-policy"
    assert cand.agent_config["gate"] == "off"
    assert cand.agent_config["net_weights"].endswith("value_net_weights_v2.json")
    assert cand.agent_config["policy_weights"].endswith(
        "policy_net_weights_mega-lucario-fighting_gen1.json")
    assert cand.agent_config["tree_prior"] is True
    assert cand.agent_config["policy_opponent"] is True
    assert cand.agent_config["policy_rollout"] is True
    assert cand.version == "v0.1" and cand.novel_axis is True
    assert cand.provenance == "daemon:policy-improvement:cycle0"


def test_policy_trainer_virgin_data_dir(tmp_path):
    """data_dir points at a path whose PARENT does not exist either;
    prepare_data must mkdir(parents=True) before invoking the script."""
    calls: list[list[str]] = []
    trainer = PolicyImprovementTrainer(
        deck=Path("src/ptcg/decks/candidates/mega-lucario-fighting.csv"),
        data_dir=tmp_path / "a" / "b" / "c", weights_dir=tmp_path / "w",
        runner=_fake_runner(calls))
    assert not (tmp_path / "a").exists()
    out = trainer.prepare_data(0)
    assert out.parent.is_dir()


def test_export_and_register_preserves_generated_deck_path(tmp_path):
    from ptcg.factory.trainers import PerDeckNetTrainer, ROOT
    deck = ROOT / "src" / "ptcg" / "decks" / "candidates" / "generated" / "x-energy-up2.csv"
    t = PerDeckNetTrainer(deck=deck)
    cand = t.export_and_register(tmp_path / "w.json", 0, [])
    assert cand.deck == "src/ptcg/decks/candidates/generated/x-energy-up2.csv"
