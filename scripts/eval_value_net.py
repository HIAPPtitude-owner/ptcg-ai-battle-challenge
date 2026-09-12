"""Phase-0 diagnostic: per-bucket AUC/BCE of a value net on baseline-v0 vs
search on-path vs search post-deviation positions (torch, dev-only)."""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptcg.search.value_net import ValueNet  # noqa: E402
from ptcg.train.metrics import metrics  # noqa: E402

# Amended D0 thresholds (coordinator-approved amendment to the plan's D0 criterion):
# the net-vs-hte differential separates net-specific off-policy blindness from
# positions that are intrinsically harder to predict (hte degrades on those too).
CONFIRM_THRESHOLD = 0.05
FALSIFY_THRESHOLD = 0.02
DIFFERENTIAL_THRESHOLD = 0.03


def load_rows(path: str | Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def bucketize(baseline_rows: list[dict], search_rows: list[dict]) -> dict[str, list[dict]]:
    a_v0_val = [r for r in baseline_rows if r["g"] % 10 >= 8]
    b_search_onpath = [r for r in search_rows if r.get("d", 0) == 0]
    c_search_postdev = [r for r in search_rows if r.get("d", 0) == 1]
    return {"a_v0_val": a_v0_val, "b_search_onpath": b_search_onpath,
            "c_search_postdev": c_search_postdev}


def score_bucket(net: ValueNet, rows: list[dict], cap: int, seed: int) -> dict:
    scored_rows = rows if len(rows) <= cap else random.Random(seed).sample(rows, cap)
    scores = [net.predict(r["x"]) for r in scored_rows]
    labels = torch.tensor([r["y"] for r in scored_rows], dtype=torch.float32)
    m = metrics(torch.tensor(scores, dtype=torch.float32), labels)
    m_hte = metrics(torch.tensor([r["hte"] for r in scored_rows], dtype=torch.float32),
                    labels)
    n_decisive = sum(1 for r in scored_rows if r["y"] != 0.5)
    return {"n": len(scored_rows), "n_decisive": n_decisive,
            "auc": m["auc"], "bce": m["bce"], "acc": m["acc"],
            "hte_auc": m_hte["auc"], "hte_bce": m_hte["bce"]}


# CAVEAT: the advisory verdict is computed from AUC deltas between buckets and
# is only meaningful when every bucket has a substantial decisive-n. On a tiny
# smoke run (e.g. n~20/bucket) the AUCs are pure noise, so a "CONFIRMED"/
# "SHARED-DEGRADATION"/"FALSIFIED" label there is statistically empty — read the
# per-bucket n printed above before trusting the advisory line. Real diagnostic
# runs bucket at n~100k. If this ever misleads at real scale, upgrade this from
# a comment to a logic guard: thread the min bucket n_decisive into _advisory and
# prefix "LOW-N " to the label when it falls below a floor (~200).
def _advisory(deficit_ac: float, differential: float) -> str:
    if deficit_ac < FALSIFY_THRESHOLD:
        return "FALSIFIED"
    if deficit_ac >= CONFIRM_THRESHOLD:
        if differential >= DIFFERENTIAL_THRESHOLD:
            return "CONFIRMED"
        return "SHARED-DEGRADATION"
    return "GRAY"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--weights", required=True)
    p.add_argument("--baseline-data", required=True)
    p.add_argument("--search-data", required=True)
    p.add_argument("--max-rows-per-bucket", type=int, default=100_000)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()

    spec = json.loads(Path(args.weights).read_text(encoding="utf-8"))
    net = ValueNet(spec)

    baseline_rows = load_rows(args.baseline_data)
    search_rows = load_rows(args.search_data)
    buckets = bucketize(baseline_rows, search_rows)

    results = {}
    for name in ("a_v0_val", "b_search_onpath", "c_search_postdev"):
        rows = buckets[name]
        full_n = len(rows)
        result = score_bucket(net, rows, args.max_rows_per_bucket, args.seed)
        results[name] = result
        print(f"bucket {name}: n={result['n']}/{full_n} n_decisive={result['n_decisive']} "
              f"auc={result['auc']:.4f} bce={result['bce']:.4f} acc={result['acc']:.4f} "
              f"hte_auc={result['hte_auc']:.4f} hte_bce={result['hte_bce']:.4f}")

    deficit_ac = results["a_v0_val"]["auc"] - results["c_search_postdev"]["auc"]
    deficit_ab = results["a_v0_val"]["auc"] - results["b_search_onpath"]["auc"]
    hte_deficit_ac = (results["a_v0_val"]["hte_auc"]
                      - results["c_search_postdev"]["hte_auc"])
    differential = deficit_ac - hte_deficit_ac
    print(f"deficit(a-c)={deficit_ac:.4f} deficit(a-b)={deficit_ab:.4f}")
    print(f"hte_deficit(a-c)={hte_deficit_ac:.4f} differential(net-hte)={differential:.4f}")
    print(f"advisory: {_advisory(deficit_ac, differential)} per amended D0 thresholds "
          f"(deficit >=0.05 / <0.02; differential >=0.03)")


if __name__ == "__main__":
    main()
