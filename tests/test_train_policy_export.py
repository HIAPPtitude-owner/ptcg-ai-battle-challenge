"""Train on trivially separable synthetic targets; verify export + parity +
learnability. Torch is a dev-only dependency (mirrors test_train_export.py)."""
import json
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _write_synthetic(path: Path, n_decisions: int = 960) -> None:
    # v3 identity test: the target is a PURE function of cardId (the option
    # whose card is "best" == highest cardId), while the 18 action features are
    # drawn as pure noise. So the ONLY generalizable signal is the identity
    # embedding — the net cannot reach the target through the action features,
    # proving the embeddings carry card identity. Cards come from a small pool
    # (each appears far more than the default min-id-freq=5, so all earn their
    # own embedding row) and are distinct within a decision to avoid target
    # ties. Sizing: kept at 960 decisions (well above the 240-decision
    # fixture-capacity floor documented for v1 above) so val agreement lands
    # comfortably >= 0.8; the identity signal is deterministic so this converges
    # far more easily than the v2 feature-only fixture did.
    rng = random.Random(0)
    cards = [10, 11, 12, 13, 14, 15]
    rows = []
    for g in range(n_decisions):
        k = rng.choice([2, 3, 4])
        chosen_cards = rng.sample(cards, k)          # distinct cards, no ties
        opts = [[round(rng.random(), 4) for _ in range(18)] for _ in range(k)]
        ids = [[c, -1] for c in chosen_cards]        # attackId inert (all -1)
        best = max(range(k), key=lambda i: chosen_cards[i])
        n = [9 if i == best else 1 for i in range(k)]
        rows.append({"v": 1, "pv": 2, "g": g, "s": g % 2, "y": float(g % 2),
                     "x": [round(rng.random(), 4) for _ in range(40)],
                     "opts": opts, "ids": ids, "n": n, "chosen": best})
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_train_export_parity_and_learnability(tmp_path):
    data = tmp_path / "syn.jsonl"
    out = tmp_path / "policy.json"
    _write_synthetic(data)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "train_policy_net.py"),
         "--data", str(data), "--out", str(out),
         "--epochs", "30", "--hidden", "32", "--seed", "0"],
        capture_output=True, text=True, cwd=ROOT, timeout=300)
    assert proc.returncode == 0, proc.stderr
    assert "PARITY OK" in proc.stdout
    spec = json.loads(out.read_text(encoding="utf-8"))
    assert spec["feature_version"] == 1 and spec["action_feature_version"] == 2
    # v3 identity keys are present and shaped (card 8-dim, attack 4-dim; row 0
    # is the unknown/rare bucket, always present).
    assert set(spec["card_vocab"]) == {"10", "11", "12", "13", "14", "15"}
    assert len(spec["card_emb"]) == len(spec["card_vocab"]) + 1
    assert all(len(r) == 8 for r in spec["card_emb"])
    assert all(len(r) == 4 for r in spec["attack_emb"])
    # target is a pure function of cardId -> only the embedding can reach it
    assert spec["meta"]["val_agreement"] >= 0.8


def test_make_random_policy_weights(tmp_path):
    out = tmp_path / "rand.json"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_random_policy_weights.py"),
         "--out", str(out)],
        capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert proc.returncode == 0, proc.stderr
    from ptcg.search.policy_net import PolicyNet
    net = PolicyNet(json.loads(out.read_text(encoding="utf-8")))
    probs = net.score_options([0.0] * 40, [[0.0] * 18, [1.0] * 18],
                              [[-1, -1], [-1, -1]])
    assert len(probs) == 2 and abs(sum(probs) - 1.0) < 1e-9


def test_make_random_policy_weights_virgin_out_dir(tmp_path):
    # --out parent chain does not exist yet (first-run-on-fresh-machine class).
    out = tmp_path / "a" / "b" / "rand.json"
    assert not out.parent.exists()
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_random_policy_weights.py"),
         "--out", str(out)],
        capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert out.exists()


def test_train_policy_net_virgin_out_dir(tmp_path):
    # --out parent chain does not exist yet (first-run-on-fresh-machine class).
    data = tmp_path / "syn.jsonl"
    out = tmp_path / "c" / "d" / "policy.json"
    _write_synthetic(data)
    assert not out.parent.exists()
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "train_policy_net.py"),
         "--data", str(data), "--out", str(out),
         "--epochs", "1", "--hidden", "8", "--seed", "0"],
        capture_output=True, text=True, cwd=ROOT, timeout=300)
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
