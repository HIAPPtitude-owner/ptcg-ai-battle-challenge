# One-off evidence script (Slice 7B close-out): re-derives the class-level
# agreement figures cited in ANALYSIS-slice7b-policy-improvement.md from the
# committed gen-1 v3 net + the v3 dataset. Not part of the test suite.
#
# Usage: uv run python experiments/analyze_slice7b_class_agreement.py
# Recorded output (2026-07-14): index 0.3067, class 0.4908, dup-group 0.4650,
# random-class baseline 0.4071 (=> mean ~2.46 distinguishable classes/decision).
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptcg.search.policy_net import PolicyNet  # noqa: E402

WEIGHTS = ROOT / "src" / "ptcg" / "search" / "policy_net_weights_mega-lucario-fighting_gen1.json"
DATA = ROOT / "experiments" / "data" / "slice7b" / "policy_gen1v3_data.jsonl"


def argmax(xs: list[float]) -> int:
    return max(range(len(xs)), key=xs.__getitem__)


def main() -> None:
    net = PolicyNet(json.loads(WEIGHTS.read_text(encoding="utf-8")))
    with open(DATA, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    val = [r for r in rows if r["g"] % 10 >= 8]
    n = idx_ok = cls_ok = dup_target = 0
    cls_random_sum = 0.0
    for r in val:
        probs = net.score_options(r["x"], r["opts"], r["ids"])
        pred, tgt = argmax(probs), argmax(r["n"])

        def key(i: int) -> tuple:
            return (tuple(r["opts"][i]), tuple(r["ids"][i]))

        groups: dict[tuple, list[int]] = {}
        for i in range(len(r["opts"])):
            groups.setdefault(key(i), []).append(i)
        n += 1
        idx_ok += pred == tgt
        cls_ok += key(pred) == key(tgt)
        dup_target += len(groups[key(tgt)]) > 1
        cls_random_sum += 1.0 / len(groups)
    print(f"val_decisions={n}")
    print(f"index_agreement={idx_ok / n:.4f}")
    print(f"class_agreement={cls_ok / n:.4f}")
    print(f"target_in_duplicate_group={dup_target / n:.4f}")
    print(f"class_random_baseline={cls_random_sum / n:.4f}")


if __name__ == "__main__":
    main()
