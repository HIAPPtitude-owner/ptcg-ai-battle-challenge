"""Emit a randomly-initialized policy-net spec (real architecture, no training).
Used by the Task-10 throughput gate: inference cost is architecture-dependent,
not weights-dependent, so the gate can run before any training data exists."""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptcg.search.action_features import (ACTION_FEATURE_NAMES,  # noqa: E402
                                         ACTION_FEATURE_VERSION)
from ptcg.search.features import FEATURE_NAMES, FEATURE_VERSION  # noqa: E402

# Kept in lockstep with scripts/train_policy_net.py (CARD_DIM / ATTACK_DIM).
CARD_DIM = 8
ATTACK_DIM = 4


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--hidden", type=int, default=64)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()
    rng = random.Random(args.seed)
    # First MLP layer sees 40 state + 18 action + CARD_DIM + ATTACK_DIM inputs.
    n_in = len(FEATURE_NAMES) + len(ACTION_FEATURE_NAMES) + CARD_DIM + ATTACK_DIM

    def layer(n_out, n_inp):
        return {"w": [[rng.uniform(-0.1, 0.1) for _ in range(n_inp)]
                      for _ in range(n_out)],
                "b": [0.0] * n_out}

    # Empty vocabs -> every id misses -> the single index-0 (unknown/rare) row.
    spec = {"feature_version": FEATURE_VERSION,
            "action_feature_version": ACTION_FEATURE_VERSION,
            "layers": [layer(args.hidden, n_in),
                       layer(args.hidden, args.hidden),
                       layer(1, args.hidden)],
            "card_vocab": {},
            "card_emb": [[rng.uniform(-0.1, 0.1) for _ in range(CARD_DIM)]],
            "attack_vocab": {},
            "attack_emb": [[rng.uniform(-0.1, 0.1) for _ in range(ATTACK_DIM)]],
            "golden": [], "meta": {"random": True, "seed": args.seed}}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
