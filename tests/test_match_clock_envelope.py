import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import match_clock_envelope as mce  # noqa: E402


def test_safe_budget_basic():
    assert mce.safe_budget_ms(30, match_seconds=600.0, safety_factor=2.0) == 10000.0


def test_safe_budget_subtracts_overshoot():
    assert mce.safe_budget_ms(30, match_seconds=600.0, safety_factor=2.0,
                              overshoot_ms=400.0) == 9600.0


def test_percentile_nearest_rank():
    assert mce.percentile(list(range(1, 101)), 0.95) == 95


def test_safe_budget_max_decisions_scenario_pins_d2_headline():
    """Pins the D2 headline: pooled max decisions/game (31, from the 2000ms D1
    sidecar) and the full-precision worst-observed overshoot (20.100299996556714
    ms, from the 500ms D1 sidecar) -- hand-verified via
    `uv run python -c "print(600000/(31*2) - 20.100299996556714)"` -> 9657.319054842153."""
    assert mce.safe_budget_ms(31, match_seconds=600.0, safety_factor=2.0,
                              overshoot_ms=20.100299996556714) == 9657.319054842153
