"""tests/test_eval_value_net.py"""
import json
import math
import subprocess
import sys

import pytest

from ptcg.search.features import FEATURE_NAMES, FEATURE_VERSION
from ptcg.search.value_net import ValueNet
from scripts.eval_value_net import _advisory, bucketize, load_rows, score_bucket


def _row(g, y, x0, d=None, src=None, s=0, hte=0.5):
    x = [0.0] * len(FEATURE_NAMES)
    x[0] = x0
    row = {"v": FEATURE_VERSION, "g": g, "s": s, "y": y, "hte": hte, "x": x}
    if d is not None:
        row["d"] = d
    if src is not None:
        row["src"] = src
    return row


def _spec():
    # single linear layer, passes feature 0 through unchanged: predict(x) == sigmoid(x[0])
    w = [1.0] + [0.0] * (len(FEATURE_NAMES) - 1)
    return {"feature_version": FEATURE_VERSION, "feature_names": list(FEATURE_NAMES),
            "layers": [{"w": [w], "b": [0.0]}], "golden": []}


def _write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_single_layer_passthrough_hand_verified():
    """Hand-verify the weights-spec trick used throughout this test file:
    x0=0 -> z=0 -> sigmoid(0)=0.5; the final-layer clamp only binds at |z|>60."""
    net = ValueNet(_spec())
    assert net.predict([0.0] * len(FEATURE_NAMES)) == pytest.approx(0.5, abs=1e-12)
    x = [0.0] * len(FEATURE_NAMES)
    x[0] = 2.0
    assert net.predict(x) == pytest.approx(1.0 / (1.0 + math.exp(-2.0)), abs=1e-9)


def test_load_rows_reads_jsonl(tmp_path):
    path = tmp_path / "rows.jsonl"
    rows = [_row(0, 1.0, 1.0), _row(0, 0.0, -1.0)]
    _write_jsonl(path, rows)
    loaded = load_rows(path)
    assert len(loaded) == 2
    assert loaded[0]["y"] == 1.0
    assert loaded[1]["y"] == 0.0


def test_bucketize_sizes_and_missing_key_defaults():
    # g % 10 >= 8 -> only g=8,9 land in a_v0_val
    baseline = [_row(g, 1.0, 0.1) for g in range(10)]
    search = [
        _row(0, 1.0, 0.1, d=0, src="search"),   # explicit on-path
        _row(0, 0.0, 0.1, d=1, src="search"),   # explicit post-deviation
        _row(1, 1.0, 0.1),                      # d/src omitted -> default 0/"v0" -> on-path
    ]
    buckets = bucketize(baseline, search)
    assert set(buckets) == {"a_v0_val", "b_search_onpath", "c_search_postdev"}
    assert len(buckets["a_v0_val"]) == 2
    assert len(buckets["b_search_onpath"]) == 2
    assert len(buckets["c_search_postdev"]) == 1


def test_score_bucket_auc_hand_computed():
    # NET: x0 strictly decreasing across rows -> score order mirrors x0 order (sigmoid monotonic).
    # ranks (1=lowest score .. 4=highest): x0=-3 -> rank1, x0=-1 -> rank2, x0=1 -> rank3, x0=3 -> rank4
    # positives (y=1) are x0=3 (rank4) and x0=-1 (rank2); pos_rank_sum = 6
    # net AUC = (6 - 2*3/2) / (2*2) = (6-3)/4 = 0.75
    # HTE: hte values [0.8, 0.9, 0.1, 0.2] -> ranks 0.1->1, 0.2->2, 0.8->3, 0.9->4
    # positives (y=1) carry hte=0.8 (rank3) and hte=0.1 (rank1); pos_rank_sum = 4
    # hte AUC = (4 - 2*3/2) / (2*2) = (4-3)/4 = 0.25
    net = ValueNet(_spec())
    rows = [
        _row(0, 1.0, 3.0, hte=0.8),
        _row(0, 0.0, 1.0, hte=0.9),
        _row(0, 1.0, -1.0, hte=0.1),
        _row(0, 0.0, -3.0, hte=0.2),
    ]
    result = score_bucket(net, rows, cap=100, seed=0)
    assert result["n"] == 4
    assert result["n_decisive"] == 4
    assert result["auc"] == pytest.approx(0.75, abs=1e-9)
    assert result["hte_auc"] == pytest.approx(0.25, abs=1e-9)
    assert set(result) == {"n", "n_decisive", "auc", "bce", "acc", "hte_auc", "hte_bce"}


def test_advisory_bands():
    # CONFIRMED: net deficit large AND net-specific (differential >= 0.03)
    assert _advisory(0.10, 0.08) == "CONFIRMED"
    # SHARED-DEGRADATION: net deficit large but hte degrades equally (differential < 0.03)
    assert _advisory(0.10, 0.0) == "SHARED-DEGRADATION"
    # FALSIFIED: net deficit below the noise band, regardless of differential
    assert _advisory(0.01, 0.5) == "FALSIFIED"
    # GRAY: deficit in [0.02, 0.05)
    assert _advisory(0.03, 0.08) == "GRAY"


def test_score_bucket_excludes_draws_from_decisive_count():
    net = ValueNet(_spec())
    rows = [_row(0, 1.0, 2.0), _row(0, 0.0, -2.0), _row(0, 0.5, 0.0)]
    result = score_bucket(net, rows, cap=100, seed=0)
    assert result["n"] == 3
    assert result["n_decisive"] == 2


def test_score_bucket_respects_cap_and_is_seed_deterministic():
    net = ValueNet(_spec())
    rows = [_row(g, float(g % 2), float(g)) for g in range(20)]
    r1 = score_bucket(net, rows, cap=5, seed=0)
    r2 = score_bucket(net, rows, cap=5, seed=0)
    assert r1["n"] == 5
    assert r1 == r2


def test_cli_smoke_shared_degradation(tmp_path):
    # Baseline: perfectly-ranked by BOTH net (x0 = 2y-1) and hte (0.6 win / 0.4 loss)
    #   -> bucket a (g=8,9,18,19: 2 pos + 2 neg): auc=1.0, hte_auc=1.0.
    # Bucket c: BOTH net and hte inverted -> auc=0.0, hte_auc=0.0.
    # Bucket b: single row, degenerate -> auc=0.5 (informational only).
    # So deficit(a-c)=1.0, hte_deficit(a-c)=1.0, differential=0.0
    #   -> advisory SHARED-DEGRADATION (deficit >= 0.05, differential < 0.03).
    baseline_rows = [
        _row(g, float(g % 2), float(g % 2) * 2 - 1, hte=0.4 + 0.2 * (g % 2))
        for g in range(20)
    ]
    search_rows = [
        _row(0, 1.0, 1.0, d=0, src="search", hte=0.6),
        _row(0, 1.0, -2.0, d=1, src="search", hte=0.4),
        _row(0, 0.0, 2.0, d=1, src="search", hte=0.6),
    ]
    baseline_path = tmp_path / "baseline.jsonl"
    search_path = tmp_path / "search.jsonl"
    _write_jsonl(baseline_path, baseline_rows)
    _write_jsonl(search_path, search_rows)
    weights_path = tmp_path / "weights.json"
    weights_path.write_text(json.dumps(_spec()), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "scripts/eval_value_net.py",
         "--weights", str(weights_path),
         "--baseline-data", str(baseline_path),
         "--search-data", str(search_path)],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "bucket a_v0_val:" in proc.stdout
    assert "hte_auc=1.0000" in proc.stdout  # bucket a line
    assert "bucket b_search_onpath:" in proc.stdout
    assert "bucket c_search_postdev:" in proc.stdout
    assert "deficit(a-c)=1.0000 deficit(a-b)=0.5000" in proc.stdout
    assert "hte_deficit(a-c)=1.0000 differential(net-hte)=0.0000" in proc.stdout
    assert "advisory: SHARED-DEGRADATION" in proc.stdout
