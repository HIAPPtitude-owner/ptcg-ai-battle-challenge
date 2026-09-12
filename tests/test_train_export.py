"""tests/test_train_export.py — end-to-end tiny training on synthetic data."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ptcg.search.features import FEATURE_NAMES, FEATURE_VERSION
from ptcg.search.value_net import ValueNet

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from train_value_net import load_jsonl  # noqa: E402


def _synthetic_dataset(path: Path, n_games: int = 60) -> None:
    """Learnable toy signal: y depends on feature 0 (i_move)... any separable rule."""
    import random
    rng = random.Random(0)
    with open(path, "w", encoding="utf-8") as f:
        for g in range(n_games):
            y = float(g % 2)
            for _ in range(10):
                x = [rng.random() for _ in FEATURE_NAMES]
                x[0] = y  # make the label trivially learnable
                f.write(json.dumps({"v": FEATURE_VERSION, "g": g, "s": 0,
                                    "y": y, "hte": 0.5, "x": x}) + "\n")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_load_jsonl_multi_file_offsets_game_ids_and_tracks_src_and_d(tmp_path):
    """File 2's game ids must be offset past file 1's max so ids never collide,
    src_is_search must reflect each file's rows, and 'd' must default to 0."""
    p1 = tmp_path / "v0.jsonl"
    p2 = tmp_path / "search.jsonl"
    # File 1: game ids 0..3, no "src"/"d" fields (defaults apply).
    _write_jsonl(p1, [
        {"v": FEATURE_VERSION, "g": g, "s": 0, "y": float(g % 2), "hte": 0.5,
         "x": [0.0] * len(FEATURE_NAMES)}
        for g in range(4)
    ])
    # File 2: game ids 0..3 again (would collide without offsetting),
    # all rows tagged src=search, some with d=1.
    _write_jsonl(p2, [
        {"v": FEATURE_VERSION, "g": g, "s": 0, "y": float(g % 2), "hte": 0.5,
         "d": 1 if g >= 2 else 0, "src": "search", "x": [1.0] * len(FEATURE_NAMES)}
        for g in range(4)
    ])

    X, y, hte, g, d, src = load_jsonl([p1, p2])

    n1, n2 = 4, 4
    assert len(X) == n1 + n2

    file1_ids = g[:n1]
    file2_ids = g[n1:]
    assert int(file2_ids.min()) > int(file1_ids.max())  # offset applied

    # No duplicate (g, s) collisions across the combined ids.
    combined_ids = g.tolist()
    assert len(combined_ids) == len(set(combined_ids))

    assert not src[:n1].any()  # file 1 rows: src_is_search False
    assert src[n1:].all()  # file 2 rows: src_is_search True

    assert (d[:n1] == 0).all()  # file 1: "d" absent -> defaults to 0
    assert d[n1] == 0 and d[n1 + 1] == 0  # file 2 g=0,1 -> d=0
    assert d[n1 + 2] == 1 and d[n1 + 3] == 1  # file 2 g=2,3 -> d=1


def test_load_jsonl_offset_chain_survives_empty_file(tmp_path):
    """Regression: an empty middle file must not reset the offset chain —
    file 3's ids must still be offset past file 1's, never colliding."""
    p1 = tmp_path / "a.jsonl"
    p2 = tmp_path / "empty.jsonl"
    p3 = tmp_path / "b.jsonl"
    rows = [
        {"v": FEATURE_VERSION, "g": g, "s": 0, "y": float(g % 2), "hte": 0.5,
         "x": [0.0] * len(FEATURE_NAMES)}
        for g in range(10)
    ]
    _write_jsonl(p1, rows)
    _write_jsonl(p2, [])  # EMPTY file in the middle
    _write_jsonl(p3, rows)  # same game ids 0..9 — would collide on a reset

    X, y, hte, g, d, src = load_jsonl([p1, p2, p3])

    assert len(X) == 20
    ids = g.tolist()
    assert len(set(ids)) == 20  # all game ids unique across the union
    file1_ids = g[:10]
    file3_ids = g[10:]
    assert int(file3_ids.min()) > int(file1_ids.max())


def test_train_export_roundtrip(tmp_path):
    data = tmp_path / "toy.jsonl"
    _synthetic_dataset(data)
    out = tmp_path / "weights.json"
    proc = subprocess.run(
        [sys.executable, "scripts/train_value_net.py", "--data", str(data),
         "--out", str(out), "--epochs", "40", "--hidden", "8", "--seed", "0",
         "--batch", "64", "--lr", "0.05"],  # small batches + high lr: the toy
         # rule (x[0]==y) must actually converge in seconds, not just run
        capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr
    assert "PARITY OK" in proc.stdout
    spec = json.loads(out.read_text(encoding="utf-8"))
    assert spec["feature_version"] == FEATURE_VERSION
    assert spec["feature_names"] == FEATURE_NAMES
    assert len(spec["golden"]) >= 3
    net = ValueNet(spec)
    for g in spec["golden"]:
        assert net.predict(g["x"]) == pytest.approx(g["y"], abs=1e-6)
    # the toy rule is learnable: val accuracy reported and high
    assert "val_acc" in spec["meta"] and spec["meta"]["val_acc"] > 0.9
