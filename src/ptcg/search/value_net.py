"""Pure-stdlib value-net inference. Weights are torch-trained and JSON-exported;
this module must stay importable inside the Kaggle bundle (no torch, no numpy)."""
from __future__ import annotations

import json
import math
from pathlib import Path

from ptcg.search.features import FEATURE_VERSION, extract

DEFAULT_WEIGHTS_PATH = Path(__file__).with_name("value_net_weights.json")


def _affine(x: list[float], w: list[list[float]], b: list[float]) -> list[float]:
    return [sum(wi * xi for wi, xi in zip(row, x)) + bi
            for row, bi in zip(w, b)]


class ValueNet:
    def __init__(self, spec: dict) -> None:
        if spec.get("feature_version") != FEATURE_VERSION:
            raise ValueError(
                f"feature_version {spec.get('feature_version')!r} != "
                f"extractor version {FEATURE_VERSION}")
        self.layers = spec["layers"]

    def predict(self, x: list[float]) -> float:
        h = x
        for layer in self.layers[:-1]:
            h = [v if v > 0.0 else 0.0 for v in _affine(h, layer["w"], layer["b"])]
        z = _affine(h, self.layers[-1]["w"], self.layers[-1]["b"])[0]
        z = max(-60.0, min(60.0, z))
        return 1.0 / (1.0 + math.exp(-z))


class ValueNetEvaluator:
    """Drop-in for ptcg.search.evaluate.evaluate (Searcher's evaluator contract)."""

    def __init__(self, net: ValueNet, dbs: tuple[dict, dict] | None = None) -> None:
        self.net = net
        self.dbs = dbs

    @classmethod
    def load(cls, path: str | Path) -> "ValueNetEvaluator":
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(ValueNet(spec))

    @classmethod
    def load_default(cls) -> "ValueNetEvaluator":
        return cls.load(DEFAULT_WEIGHTS_PATH)

    def __call__(self, state, my_index: int) -> float:
        if state.result == my_index:
            return 1.0
        if state.result == 1 - my_index:
            return 0.0
        if state.result != -1:
            return 0.5
        return self.net.predict(extract(state, my_index, dbs=self.dbs))
