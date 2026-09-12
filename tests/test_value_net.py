"""tests/test_value_net.py"""
import json
from pathlib import Path

import pytest

from ptcg.search.features import FEATURE_NAMES, FEATURE_VERSION, extract
from ptcg.search.value_net import (DEFAULT_WEIGHTS_PATH, ValueNet,
                                   ValueNetEvaluator)
from tests.fixtures.obs import make_state


def _spec(layers):
    return {"feature_version": FEATURE_VERSION, "feature_names": FEATURE_NAMES,
            "layers": layers, "golden": [], "meta": {}}


def test_single_layer_hand_computed():
    net = ValueNet(_spec([{"w": [[1.0, -1.0]], "b": [0.5]}]))
    assert net.predict([2.0, 1.0]) == pytest.approx(0.8175744762, abs=1e-9)


def test_two_layer_relu_hand_computed():
    net = ValueNet(_spec([
        {"w": [[1.0, 0.0], [0.0, 1.0]], "b": [0.0, -2.0]},
        {"w": [[1.0, 1.0]], "b": [0.0]},
    ]))
    assert net.predict([3.0, 1.0]) == pytest.approx(0.9525741268, abs=1e-9)


def test_evaluator_terminal_shortcuts_and_range():
    tiny = ValueNet(_spec([{"w": [[0.01] * len(FEATURE_NAMES)], "b": [0.0]}]))
    ev = ValueNetEvaluator(tiny)
    st = make_state()
    assert 0.0 <= ev(st, 0) <= 1.0
    st_win = make_state();  st_win.result = 0
    st_loss = make_state(); st_loss.result = 1
    st_draw = make_state(); st_draw.result = 2
    assert ev(st_win, 0) == 1.0
    assert ev(st_loss, 0) == 0.0
    assert ev(st_draw, 0) == 0.5


def test_feature_version_mismatch_rejected():
    spec = _spec([{"w": [[0.0, 0.0]], "b": [0.0]}])
    spec["feature_version"] = 999
    with pytest.raises(ValueError, match="feature_version"):
        ValueNet(spec)


def test_load_from_path_builds_working_evaluator(tmp_path):
    spec = _spec([{"w": [[0.01] * len(FEATURE_NAMES)], "b": [0.0]}])
    p = tmp_path / "w.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    ev = ValueNetEvaluator.load(p)
    y = ev.net.predict([0.0] * len(FEATURE_NAMES))
    assert 0.0 <= y <= 1.0


def test_load_feature_version_mismatch_raises(tmp_path):
    spec = _spec([{"w": [[0.0] * len(FEATURE_NAMES)], "b": [0.0]}])
    spec["feature_version"] = 999
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ValueError, match="feature_version"):
        ValueNetEvaluator.load(p)


def test_shipped_weights_golden_parity():
    """Torch-exported goldens must reproduce through the pure-Python path."""
    if not Path(DEFAULT_WEIGHTS_PATH).exists():
        pytest.skip("no production weights artifact yet (created in Task 9)")
    spec = json.loads(Path(DEFAULT_WEIGHTS_PATH).read_text(encoding="utf-8"))
    assert spec["golden"], "export must embed golden vectors"
    net = ValueNet(spec)
    for g in spec["golden"]:
        assert net.predict(g["x"]) == pytest.approx(g["y"], abs=1e-6)
