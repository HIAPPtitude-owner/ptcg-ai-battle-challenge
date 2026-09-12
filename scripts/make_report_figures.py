"""Render report figure F3 (ladder score trajectory) from the snapshot log."""
from __future__ import annotations

import collections
import datetime as dt
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HIGHLIGHT = ("55512672", "55512669")  # the final counted pair
SNAPSHOTS = Path("experiments/factory/ladder_snapshots.jsonl")
OUT = Path("docs/report/figures/f3-ladder-trajectory.png")


def load_series(snapshot_path: Path) -> dict[str, list[tuple[dt.datetime, float]]]:
    series: dict[str, list[tuple[dt.datetime, float]]] = collections.OrderedDict()
    for line in snapshot_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        score = row.get("public_score")
        if score is None:
            continue
        ts = dt.datetime.fromisoformat(row["utc_ts"])
        series.setdefault(row["ref"], []).append((ts, float(score)))
    for pts in series.values():
        pts.sort(key=lambda p: p[0])
    return series


def render_f3(snapshot_path: Path, out_path: Path) -> None:
    series = load_series(snapshot_path)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    saw_evicted = False
    for ref, pts in series.items():
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if ref in HIGHLIGHT:
            ax.plot(xs, ys, linewidth=2.4, marker="o", markersize=3,
                    label=f"ref {ref} (counted)")
        else:
            label = "evicted / frozen at eviction score" if not saw_evicted else None
            saw_evicted = True
            ax.plot(xs, ys, linewidth=1.3, alpha=0.6, color="#64748b",
                    linestyle="--", label=label)
    ax.axhline(600.0, color="#c2410c", linestyle=":", linewidth=1.2, alpha=0.7)
    ax.text(0.99, 0.995, "600.0 = new-submission seed score", transform=ax.transAxes,
             fontsize=7, color="#c2410c", va="top", ha="right")
    ax.set_xlabel("UTC date")
    ax.set_ylabel("Kaggle public score")
    ax.set_title("Ladder scores, submissions logged from 2026-08-14 (counted pair highlighted)")
    # Dated provisional marker: scores keep evolving until the post-deadline
    # leaderboard converges (~2026-08-31), so any render before then shows
    # transients. Derived from the data so a re-render self-updates.
    latest = max((p[0] for pts in series.values() for p in pts), default=None)
    if latest is not None:
        stamp = latest.strftime("%Y-%m-%d")
        note = (f"Data through {stamp} UTC — PROVISIONAL: leaderboard converges ~2026-08-31"
                if stamp < "2026-08-31" else f"Data through {stamp} UTC — post-convergence")
        ax.text(0.01, 0.995, note, transform=ax.transAxes, fontsize=7,
                color="#475569", va="top", ha="left")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def main() -> int:
    render_f3(SNAPSHOTS, OUT)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
