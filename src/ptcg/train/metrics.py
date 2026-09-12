"""Offline eval metrics (torch, dev-only — never bundled)."""
from __future__ import annotations

import torch


def auc(scores: torch.Tensor, labels: torch.Tensor) -> float:
    """Rank-based AUC over win/loss labels (draws 0.5 excluded).

    Uses the Mann-Whitney U / rank-sum equivalence so cost is O(n log n)
    instead of materializing an O(n_pos * n_neg) pairwise comparison matrix
    (the naive form OOMs at validation-set sizes in the hundreds of
    thousands: n_pos*n_neg*4 bytes blows past available RAM).
    """
    mask = labels != 0.5
    s, y = scores[mask], labels[mask]
    n_pos = int((y == 1.0).sum())
    n_neg = int((y == 0.0).sum())
    if n_pos == 0 or n_neg == 0:
        return 0.5
    sorted_s, order = torch.sort(s)
    n = len(s)
    ranks = torch.arange(1, n + 1, dtype=torch.float64)
    unique_vals, inverse, counts = torch.unique(
        sorted_s, return_inverse=True, return_counts=True)
    rank_sum_per_group = torch.zeros(len(unique_vals), dtype=torch.float64).scatter_add_(
        0, inverse, ranks)
    avg_rank_per_group = rank_sum_per_group / counts.to(torch.float64)
    avg_ranks_sorted = avg_rank_per_group[inverse]
    final_ranks = torch.empty(n, dtype=torch.float64)
    final_ranks[order] = avg_ranks_sorted
    pos_rank_sum = float(final_ranks[y == 1.0].sum())
    return float((pos_rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def metrics(scores: torch.Tensor, labels: torch.Tensor) -> dict:
    eps = 1e-7
    p = scores.clamp(eps, 1 - eps)
    bce = float(-(labels * p.log() + (1 - labels) * (1 - p).log()).mean())
    mask = labels != 0.5
    acc = float(((p[mask] > 0.5) == (labels[mask] == 1.0)).float().mean()) if mask.any() else 0.0
    return {"bce": round(bce, 4), "acc": round(acc, 4),
            "auc": round(auc(scores, labels), 4)}
