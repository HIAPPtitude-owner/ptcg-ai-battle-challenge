import torch

from ptcg.train.metrics import auc, metrics


def test_auc_hand_computed_with_ties():
    scores = torch.tensor([0.9, 0.8, 0.8, 0.3])
    labels = torch.tensor([1.0, 0.0, 1.0, 0.0])
    assert abs(auc(scores, labels) - 0.875) < 1e-9


def test_auc_excludes_draws_and_degenerate_returns_half():
    scores = torch.tensor([0.9, 0.1, 0.5])
    labels = torch.tensor([1.0, 1.0, 0.5])  # no negatives after draw exclusion
    assert auc(scores, labels) == 0.5


def test_metrics_keys_and_ranges():
    scores = torch.tensor([0.9, 0.2])
    labels = torch.tensor([1.0, 0.0])
    m = metrics(scores, labels)
    assert set(m) == {"bce", "acc", "auc"} and m["acc"] == 1.0 and m["auc"] == 1.0
