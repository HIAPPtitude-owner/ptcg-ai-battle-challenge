"""Train the policy MLP (torch, dev-only) on visit-distribution targets and
export stdlib-JSON weights + goldens. Mirrors scripts/train_value_net.py.

v3 (identity embeddings): each option additionally carries a raw
`[cardId, attackId]` pair (row schema key "ids"). We learn small per-card
(CARD_DIM) and per-attack (ATTACK_DIM) embedding tables, concatenated onto the
40 state + 18 action features before the MLP. Vocabs are built from the TRAIN
split only; index 0 is a shared unknown/rare bucket, so any id absent from the
vocab (rare in train, or unseen at serve) resolves to embedding row 0. This
breaks the feature-collision ceiling: the 18 action features project
cardId->(hp,basic) / attackId->(damage,cost-count), so distinct cards with the
same stats were previously indistinguishable to the net."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptcg.search.action_features import (ACTION_FEATURE_NAMES,  # noqa: E402
                                         ACTION_FEATURE_VERSION)
from ptcg.search.features import FEATURE_NAMES, FEATURE_VERSION  # noqa: E402
from ptcg.search.policy_net import PolicyNet  # noqa: E402

N_FEAT = len(FEATURE_NAMES) + len(ACTION_FEATURE_NAMES)  # 40 state + 18 action
CARD_DIM = 8      # per-card identity embedding width
ATTACK_DIM = 4    # per-attack identity embedding width (attacks are far fewer)


def load_decisions(paths: list[Path]) -> list[dict]:
    decisions = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if r["v"] != FEATURE_VERSION or r["pv"] != ACTION_FEATURE_VERSION:
                    raise SystemExit(f"feature version mismatch in {path}: "
                                     f"v={r['v']} pv={r['pv']}")
                if "ids" not in r:
                    raise SystemExit(
                        f"row in {path} lacks 'ids' (pre-v3 data); regenerate "
                        f"the dataset with the v3 recorder before training")
                decisions.append(r)
    return decisions


def build_vocab(train: list[dict], which: int, min_freq: int) -> dict[str, int]:
    """id-string -> row index over ids[which] across TRAIN options. Index 0 is
    the shared unknown/rare bucket; ids seen < min_freq times stay pooled there
    (a single-occurrence id cannot train a stable embedding row and would just
    overfit, so pooling rare/unseen ids into one generic 'unknown identity'
    prior is both the safe default and what serve does on any vocab miss)."""
    counts: Counter = Counter()
    for d in train:
        for ids in d["ids"]:
            counts[ids[which]] += 1
    vocab: dict[str, int] = {}
    for id_val, c in counts.items():
        if c >= min_freq:
            vocab[str(id_val)] = len(vocab) + 1  # 0 reserved for unknown/rare
    return vocab


def _idx(vocab: dict[str, int], id_val: int) -> int:
    return vocab.get(str(id_val), 0)


def pad_batch(batch: list[dict], card_vocab: dict, attack_vocab: dict):
    b, k = len(batch), max(len(d["opts"]) for d in batch)
    feats = torch.zeros(b, k, N_FEAT)
    card_idx = torch.zeros(b, k, dtype=torch.long)
    attack_idx = torch.zeros(b, k, dtype=torch.long)
    mask = torch.zeros(b, k, dtype=torch.bool)
    target = torch.zeros(b, k)
    for i, d in enumerate(batch):
        s = torch.tensor(d["x"], dtype=torch.float32)
        for j, (ox, ids) in enumerate(zip(d["opts"], d["ids"])):
            feats[i, j] = torch.cat([s, torch.tensor(ox, dtype=torch.float32)])
            card_idx[i, j] = _idx(card_vocab, ids[0])
            attack_idx[i, j] = _idx(attack_vocab, ids[1])
            mask[i, j] = True
        n = torch.tensor(d["n"], dtype=torch.float32)
        target[i, :len(d["n"])] = n / n.sum()
    return feats, card_idx, attack_idx, mask, target


class PolicyModel(nn.Module):
    def __init__(self, n_card: int, n_attack: int, hidden: int) -> None:
        super().__init__()
        self.card_emb = nn.Embedding(n_card, CARD_DIM)
        self.attack_emb = nn.Embedding(n_attack, ATTACK_DIM)
        n_in = N_FEAT + CARD_DIM + ATTACK_DIM
        self.mlp = nn.Sequential(nn.Linear(n_in, hidden), nn.ReLU(),
                                 nn.Linear(hidden, hidden), nn.ReLU(),
                                 nn.Linear(hidden, 1))

    def forward(self, feats, card_idx, attack_idx):
        # feats (N, N_FEAT); card_idx/attack_idx (N,) -> logits (N,)
        x = torch.cat([feats, self.card_emb(card_idx),
                       self.attack_emb(attack_idx)], dim=1)
        return self.mlp(x).squeeze(-1)


def masked_ce(logits, mask, target):
    logits = logits.masked_fill(~mask, -1e9)
    logp = torch.log_softmax(logits, dim=1)
    return -(target * logp).sum(dim=1).mean()


def agreement(logits, mask, target) -> float:
    logits = logits.masked_fill(~mask, -1e9)
    return float((logits.argmax(1) == target.argmax(1)).float().mean())


def _run_model(model, feats, card_idx, attack_idx):
    b, k = feats.shape[0], feats.shape[1]
    flat = model(feats.view(-1, N_FEAT), card_idx.view(-1), attack_idx.view(-1))
    return flat.view(b, k)


def evaluate_split(model, split, batch_size, card_vocab, attack_vocab):
    losses, agrees, n = [], [], 0
    with torch.no_grad():
        for i in range(0, len(split), batch_size):
            feats, ci, ai, mask, tgt = pad_batch(
                split[i:i + batch_size], card_vocab, attack_vocab)
            logits = _run_model(model, feats, ci, ai)
            losses.append(float(masked_ce(logits, mask, tgt)) * feats.shape[0])
            agrees.append(agreement(logits, mask, tgt) * feats.shape[0])
            n += feats.shape[0]
    return sum(losses) / n, sum(agrees) / n


def identity_ceiling(split: list[dict]) -> float:
    """Fraction of decisions whose argmax-n target is the FIRST option in its
    (features AND ids) equality class. A perfect scorer gives identical logits
    to options with identical (state, features, ids) and breaks ties by lowest
    index, so it can only be right when the target is that lowest index. This is
    the gate denominator (the argmax agreement ceiling)."""
    hits = 0
    for d in split:
        sigs = [(tuple(ox), tuple(ids)) for ox, ids in zip(d["opts"], d["ids"])]
        n = d["n"]
        tgt = max(range(len(n)), key=n.__getitem__)
        first = min(i for i in range(len(sigs)) if sigs[i] == sigs[tgt])
        if first == tgt:
            hits += 1
    return hits / len(split)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True, nargs="+")
    p.add_argument("--out", required=True)
    p.add_argument("--epochs", type=int, default=40)
    p.add_argument("--hidden", type=int, default=64)
    p.add_argument("--batch", type=int, default=256)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--patience", type=int, default=5)
    p.add_argument("--seed", type=int, default=0)
    # An id must appear >= min-id-freq times in TRAIN to earn its own embedding
    # row; rarer ids pool into the index-0 unknown bucket (see build_vocab).
    p.add_argument("--min-id-freq", type=int, default=5)
    args = p.parse_args()
    torch.manual_seed(args.seed)

    decisions = load_decisions([Path(d) for d in args.data])
    train = [d for d in decisions if d["g"] % 10 < 8]   # split BY GAME: 80/20
    val = [d for d in decisions if d["g"] % 10 >= 8]
    print(f"train {len(train)} / val {len(val)} decisions")
    if not train or not val:
        raise SystemExit("empty train or val split")

    card_vocab = build_vocab(train, 0, args.min_id_freq)
    attack_vocab = build_vocab(train, 1, args.min_id_freq)
    print(f"card_vocab {len(card_vocab)} (+unk) / "
          f"attack_vocab {len(attack_vocab)} (+unk), min_freq {args.min_id_freq}")
    print(f"VAL identity ceiling {identity_ceiling(val):.4f}")

    model = PolicyModel(len(card_vocab) + 1, len(attack_vocab) + 1, args.hidden)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    best_val, best_state, stale = float("inf"), None, 0
    rng = torch.Generator().manual_seed(args.seed)
    for epoch in range(args.epochs):
        model.train()
        perm = torch.randperm(len(train), generator=rng).tolist()
        for i in range(0, len(perm), args.batch):
            batch = [train[j] for j in perm[i:i + args.batch]]
            feats, ci, ai, mask, tgt = pad_batch(batch, card_vocab, attack_vocab)
            opt.zero_grad()
            logits = _run_model(model, feats, ci, ai)
            loss = masked_ce(logits, mask, tgt)
            loss.backward()
            opt.step()
        model.eval()
        vl, va = evaluate_split(model, val, args.batch, card_vocab, attack_vocab)
        print(f"epoch {epoch}: val_ce {vl:.4f} val_agreement {va:.4f}",
              flush=True)
        if vl < best_val - 1e-5:
            best_val, stale = vl, 0
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            stale += 1
            if stale >= args.patience:
                print("early stop")
                break
    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    val_ce, val_agreement = evaluate_split(
        model, val, args.batch, card_vocab, attack_vocab)
    print(f"VAL ce {val_ce:.4f}")
    print(f"VAL agreement {val_agreement:.4f}")

    linears = [m for m in model.mlp if isinstance(m, nn.Linear)]
    layers = [{"w": m.weight.detach().tolist(), "b": m.bias.detach().tolist()}
              for m in linears]
    card_emb = model.card_emb.weight.detach().tolist()
    attack_emb = model.attack_emb.weight.detach().tolist()
    goldens = []
    with torch.no_grad():
        for d in val[:3]:
            feats, ci, ai, mask, _ = pad_batch([d], card_vocab, attack_vocab)
            logits = _run_model(model, feats, ci, ai).view(1, -1)
            probs = torch.softmax(logits.masked_fill(~mask, -1e9), dim=1)[0]
            goldens.append({"x": d["x"], "opts": d["opts"], "ids": d["ids"],
                            "p": [round(float(v), 10)
                                  for v in probs[:len(d["opts"])]]})
    spec = {"feature_version": FEATURE_VERSION,
            "action_feature_version": ACTION_FEATURE_VERSION,
            "layers": layers,
            "card_vocab": card_vocab, "card_emb": card_emb,
            "attack_vocab": attack_vocab, "attack_emb": attack_emb,
            "golden": goldens,
            "meta": {"val_ce": val_ce, "val_agreement": val_agreement,
                     "val_identity_ceiling": identity_ceiling(val),
                     "n_train": len(train), "n_val": len(val),
                     "hidden": args.hidden, "seed": args.seed,
                     "card_dim": CARD_DIM, "attack_dim": ATTACK_DIM,
                     "min_id_freq": args.min_id_freq,
                     "data": [str(d) for d in args.data]}}
    pure = PolicyNet(spec)
    for g in goldens:
        got = pure.score_options(g["x"], g["opts"], g["ids"])
        for a, b in zip(got, g["p"]):
            if abs(a - b) > 1e-6:
                raise SystemExit(f"PARITY FAIL: pure {a} vs torch {b}")
    print("PARITY OK: pure-Python softmax matches torch on golden decisions")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
