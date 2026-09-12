"""Pure-stdlib policy-net inference (per-option scorer) + Policy wrapper.

Weights are torch-trained (scripts/train_policy_net.py) and JSON-exported;
this module must stay importable inside the Kaggle bundle (no torch/numpy).
Input per option = 40 state features ++ 18 action features ++ card-identity
embedding ++ attack-identity embedding -> scalar logit; softmax across the
decision's option list.

v3 (identity embeddings): the 18 action features project cardId->(hp,basic)
and attackId->(damage,cost-count), so distinct cards/attacks with the same
stats collapse to identical vectors (0.5446 argmax ceiling). v3 concatenates a
learned per-card (~8-dim) and per-attack (~4-dim) embedding, looked up from the
raw option [cardId, attackId] via a train-derived vocab (index 0 = the shared
unknown/rare bucket; any vocab miss -> index 0). The spec JSON therefore carries
`card_vocab`/`card_emb`/`attack_vocab`/`attack_emb` alongside `layers`; a spec
missing those keys is a pre-v3 format and is rejected (ValueError) so
SearchAgent's warn-and-disable ladder handles it."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Callable

from ptcg.search.action_features import ACTION_FEATURE_VERSION, extract_action
from ptcg.search.features import FEATURE_VERSION, extract
from ptcg.search.tree import ActionSig, action_signature
from ptcg.search.value_net import _affine


class PolicyNet:
    def __init__(self, spec: dict) -> None:
        if spec.get("feature_version") != FEATURE_VERSION:
            raise ValueError(f"feature_version {spec.get('feature_version')!r} "
                             f"!= extractor version {FEATURE_VERSION}")
        if spec.get("action_feature_version") != ACTION_FEATURE_VERSION:
            raise ValueError(
                f"action_feature_version {spec.get('action_feature_version')!r} "
                f"!= featurizer version {ACTION_FEATURE_VERSION}")
        for key in ("card_vocab", "card_emb", "attack_vocab", "attack_emb"):
            if key not in spec:
                raise ValueError(
                    f"policy-net spec missing v3 identity-embedding key {key!r} "
                    f"(pre-v3 format); refusing to load")
        self.layers = spec["layers"]
        # vocabs are id-string -> row-index; index 0 is the shared unknown/rare
        # bucket, so any id absent from the vocab falls back to embedding row 0.
        self.card_vocab: dict = spec["card_vocab"]
        self.card_emb: list = spec["card_emb"]
        self.attack_vocab: dict = spec["attack_vocab"]
        self.attack_emb: list = spec["attack_emb"]

    def _emb(self, table: list, vocab: dict, id_val: int) -> list[float]:
        return table[vocab.get(str(id_val), 0)]

    def _logit(self, x: list[float]) -> float:
        h = x
        for layer in self.layers[:-1]:
            h = [v if v > 0.0 else 0.0 for v in _affine(h, layer["w"], layer["b"])]
        return _affine(h, self.layers[-1]["w"], self.layers[-1]["b"])[0]

    def score_options(self, state_x: list[float],
                      option_xs: list[list[float]],
                      option_ids: list[list[int]]) -> list[float]:
        """Softmax over per-option logits; uniform on any non-finite logit
        (degenerate-weights guard — spec's failure ladder). `option_ids` is one
        [cardId_or_-1, attackId_or_-1] pair per option (vocab miss -> row 0)."""
        logits = [
            self._logit(state_x + ox
                        + self._emb(self.card_emb, self.card_vocab, ids[0])
                        + self._emb(self.attack_emb, self.attack_vocab, ids[1]))
            for ox, ids in zip(option_xs, option_ids)]
        if not all(math.isfinite(z) for z in logits):
            return [1.0 / len(option_xs)] * len(option_xs)
        m = max(logits)
        exps = [math.exp(z - m) for z in logits]
        total = sum(exps)
        return [e / total for e in exps]


class PolicyNetPolicy:
    """Policy callable + prior provider for Searcher injection.

    Scores only 1-of-1 selects with >=2 options; everything else (multi-selects,
    forced picks, any scoring error) delegates to `fallback` (heuristic-v0)."""

    def __init__(self, net: PolicyNet, fallback: Callable,
                 dbs: tuple[dict, dict]) -> None:
        self.net = net
        self.fallback = fallback
        self.dbs = dbs

    @classmethod
    def load(cls, path: str | Path, fallback: Callable,
             dbs: tuple[dict, dict]) -> "PolicyNetPolicy":
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(PolicyNet(spec), fallback, dbs)

    def _probs(self, obs) -> list[float]:
        st = obs.current
        state_x = extract(st, st.yourIndex, dbs=self.dbs)
        option_xs = [extract_action(o, self.dbs[0], self.dbs[1])
                     for o in obs.select.option]
        option_ids = [[o.cardId if o.cardId is not None else -1,
                       o.attackId if o.attackId is not None else -1]
                      for o in obs.select.option]
        return self.net.score_options(state_x, option_xs, option_ids)

    def __call__(self, obs) -> list[int]:
        sel = obs.select
        if (sel is None or sel.minCount != 1 or sel.maxCount != 1
                or len(sel.option) < 2):
            return self.fallback(obs)
        try:
            probs = self._probs(obs)
        except (ValueError, RuntimeError, KeyError, AttributeError, TypeError):
            return self.fallback(obs)
        return [max(range(len(probs)), key=probs.__getitem__)]

    def priors_for(self, obs) -> dict[ActionSig, float] | None:
        """PUCT priors keyed by action signature; None (never partial) on any
        failure so the caller falls back to plain UCB for that node."""
        sel = obs.select
        if (sel is None or sel.minCount != 1 or sel.maxCount != 1
                or len(sel.option) < 2):
            return None
        try:
            probs = self._probs(obs)
        except (ValueError, RuntimeError, KeyError, AttributeError, TypeError):
            return None
        return {action_signature(sel, (i,)): p for i, p in enumerate(probs)}
