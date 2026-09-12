"""Train the value MLP (torch, dev-only) and export stdlib-JSON weights + goldens."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptcg.search.features import FEATURE_NAMES, FEATURE_VERSION  # noqa: E402
from ptcg.search.value_net import ValueNet  # noqa: E402
from ptcg.train.metrics import auc, metrics  # noqa: E402


def load_jsonl(paths: list[Path]):
    """Load one or more JSONL datasets, namespacing game ids across files so
    they never collide: file k's ids are offset by 1 + max(g) of all prior
    files. This shifts which rows fall into which g % 10 bucket within later
    files, but the val split stays a deterministic by-game 80/20 overall —
    do NOT try to preserve per-file mod classes.
    """
    xs, ys, htes, games, ds, srcs = [], [], [], [], [], []
    offset = 0
    for path in paths:
        # Seed at offset - 1 so an empty file leaves the offset chain
        # unchanged (max_g = -1 would reset offset to 0 and collide ids).
        max_g = offset - 1
        with open(path, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if r["v"] != FEATURE_VERSION:
                    raise SystemExit(f"record feature_version {r['v']} != {FEATURE_VERSION}")
                g = r["g"] + offset
                max_g = max(max_g, g)
                xs.append(r["x"]); ys.append(r["y"]); htes.append(r["hte"])
                games.append(g)
                ds.append(int(r.get("d", 0)))
                srcs.append(r.get("src", "v0") == "search")
        offset = max_g + 1
    return (torch.tensor(xs, dtype=torch.float32),
            torch.tensor(ys, dtype=torch.float32),
            torch.tensor(htes, dtype=torch.float32),
            torch.tensor(games, dtype=torch.int64),
            torch.tensor(ds, dtype=torch.int64),
            torch.tensor(srcs, dtype=torch.bool))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True, nargs="+")
    p.add_argument("--out", required=True)
    p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--hidden", type=int, default=64)
    p.add_argument("--batch", type=int, default=4096)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--patience", type=int, default=5)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()
    torch.manual_seed(args.seed)

    X, y, hte, g, d, src_is_search = load_jsonl([Path(p) for p in args.data])
    val_mask = (g % 10) >= 8  # split BY GAME: 80/20
    Xtr, ytr = X[~val_mask], y[~val_mask]
    Xv, yv, hv = X[val_mask], y[val_mask], hte[val_mask]
    print(f"train {len(Xtr)} / val {len(Xv)} positions "
          f"({int((~val_mask).sum())}/{int(val_mask.sum())} by rows)")

    v0_val = val_mask & ~src_is_search
    son_val = val_mask & src_is_search & (d == 0)
    sdev_val = val_mask & src_is_search & (d == 1)
    n_search_train = int((~val_mask & src_is_search).sum())
    n_search_val = int((val_mask & src_is_search).sum())

    model = nn.Sequential(
        nn.Linear(X.shape[1], args.hidden), nn.ReLU(),
        nn.Linear(args.hidden, args.hidden), nn.ReLU(),
        nn.Linear(args.hidden, 1))
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    loss_fn = nn.BCEWithLogitsLoss()
    best_val, best_state, stale = float("inf"), None, 0
    for epoch in range(args.epochs):
        model.train()
        perm = torch.randperm(len(Xtr))
        for i in range(0, len(Xtr), args.batch):
            idx = perm[i:i + args.batch]
            opt.zero_grad()
            loss = loss_fn(model(Xtr[idx]).squeeze(-1), ytr[idx])
            loss.backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            vl = float(loss_fn(model(Xv).squeeze(-1), yv))
        print(f"epoch {epoch}: val_bce {vl:.4f}", flush=True)
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
    with torch.no_grad():
        val_scores = torch.sigmoid(model(Xv).squeeze(-1))
    net_m = metrics(val_scores, yv)
    hte_m = metrics(hv, yv)
    print(f"NET  val: {net_m}")
    print(f"HTE  val: {hte_m}  (hand-tuned evaluator baseline)")

    def _bucket_metrics(mask, scores_full, labels_full, label):
        n = int(mask[val_mask].sum())
        if n == 0:
            print(f"{label} n=0")
            return None
        m = metrics(scores_full[mask[val_mask]], labels_full[mask[val_mask]])
        print(f"{label} {m}  (n={n})")
        return m

    print("--- per-bucket val metrics ---")
    _bucket_metrics(v0_val, val_scores, yv, "NET  val[v0]:        ")
    _bucket_metrics(son_val, val_scores, yv, "NET  val[search-on]:")
    sdev_net_m = _bucket_metrics(sdev_val, val_scores, yv, "NET  val[search-dev]:")
    _bucket_metrics(v0_val, hv, yv, "HTE  val[v0]:        ")
    val_auc_search_dev = sdev_net_m["auc"] if sdev_net_m is not None else None

    linears = [m for m in model if isinstance(m, nn.Linear)]
    layers = [{"w": m.weight.detach().tolist(), "b": m.bias.detach().tolist()}
              for m in linears]
    golden_inputs = [[0.0] * X.shape[1], [0.5] * X.shape[1],
                     [round(v, 4) for v in Xv[0].tolist()]]
    with torch.no_grad():
        golden = [{"x": gx, "y": round(float(torch.sigmoid(
            model(torch.tensor([gx], dtype=torch.float32)).squeeze(-1))[0]), 10)}
            for gx in golden_inputs]
    spec = {"feature_version": FEATURE_VERSION, "feature_names": FEATURE_NAMES,
            "layers": layers, "golden": golden,
            "meta": {"val_bce": net_m["bce"], "val_acc": net_m["acc"],
                     "val_auc": net_m["auc"], "hte_bce": hte_m["bce"],
                     "hte_acc": hte_m["acc"], "hte_auc": hte_m["auc"],
                     "n_train": len(Xtr), "n_val": len(Xv),
                     "n_search_train": n_search_train, "n_search_val": n_search_val,
                     "val_auc_search_dev": val_auc_search_dev,
                     "hidden": args.hidden, "seed": args.seed,
                     "data": [str(p) for p in args.data]}}

    pure = ValueNet(spec)
    for gg in golden:
        got = pure.predict(gg["x"])
        if abs(got - gg["y"]) > 1e-6:
            raise SystemExit(f"PARITY FAIL: pure {got} vs torch {gg['y']}")
    print("PARITY OK: pure-Python forward matches torch on golden vectors")

    Path(args.out).write_text(json.dumps(spec), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
