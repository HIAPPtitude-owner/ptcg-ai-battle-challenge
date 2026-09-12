import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from scripts.package_submission import build_bundle, smoke_bundle, verify_bundle

# Minimal variant of scripts.package_submission.SMOKE_CODE: only the first-obs
# (deck request) assertion, not the full battle loop, to keep this test fast.
# Loads main.py the same way kaggle_environments does: exec() into a bare env
# dict with no __file__, not `import main`.
FIRST_OBS_CODE = """
env = {}
with open("main.py") as f:
    code = compile(f.read(), "main.py", "exec")
exec(code, env)  # NOTE: env deliberately has no __file__, mirroring kaggle_environments
agent = env["agent"]
read_deck_csv = env["read_deck_csv"]

deck = read_deck_csv()
first = agent({"select": None, "logs": [], "current": None})
assert isinstance(first, list) and len(first) == 60, f"deck request returned {first!r}"
assert all(isinstance(i, int) for i in first), f"deck request returned non-ints: {first!r}"
assert first == deck, "agent() deck request does not match staged deck.csv"
print("FIRST_OBS_OK")
"""


def test_bundle_structure(tmp_path):
    tar = build_bundle(Path("tests/fixtures/sample_deck.csv"), tmp_path)
    assert verify_bundle(tar) == []
    with tarfile.open(tar) as tf:
        names = tf.getnames()
    assert "main.py" in names
    assert "deck.csv" in names
    assert any(n.startswith("cg/") for n in names)
    assert any(n.startswith("ptcg/") for n in names)


def test_verify_catches_missing_main(tmp_path):
    import tarfile as tf_mod

    bad = tmp_path / "bad.tar.gz"
    with tf_mod.open(bad, "w:gz") as tf:
        deck = Path("tests/fixtures/sample_deck.csv")
        tf.add(deck, arcname="deck.csv")
    assert any("main.py" in p for p in verify_bundle(bad))


def test_first_obs_returns_shipping_deck(tmp_path):
    tar = build_bundle(Path("tests/fixtures/sample_deck.csv"), tmp_path)
    assert verify_bundle(tar) == []

    staging = tmp_path / "submission"
    deck_csv = staging / "deck.csv"
    expected = [int(line) for line in deck_csv.read_text().strip().splitlines() if line.strip()]
    assert len(expected) == 60

    result = subprocess.run(
        [sys.executable, "-c", FIRST_OBS_CODE],
        cwd=staging,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "FIRST_OBS_OK" in result.stdout


# --- D2 hardening: smoke must FAIL when the intended agent path never ran ---

#: Sabotaged ladder-identity fixture: act() raises on every decision where the
#: `[0]` fallback is a LEGAL reply (minCount <= 1 <= maxCount), so main.py's
#: last-line-of-defense `except Exception` silently answers those calls with
#: the degraded `[0]`. Multi-select decisions (minCount > 1, where `[0]` has an
#: invalid count and would crash the local battle loop with IndexError) are
#: delegated to the real heuristic so the battle COMPLETES — this is exactly
#: the fallback-blind hole: a mostly-degraded agent riding a green SMOKE OK.
BROKEN_CURRENT_PY = '''"""Sabotaged ladder identity (test fixture). Do not ship."""
from pathlib import Path

from ptcg.agents.heuristic import HeuristicAgent

CURRENT_AGENT_NAME = "broken-agent"
CURRENT_DECK_PATH = Path("deck.csv")


class BrokenAgent:
    name = "broken-agent"

    def __init__(self):
        self._inner = HeuristicAgent()

    def act(self, obs):
        sel = obs.select
        if sel is not None and sel.minCount <= 1 <= sel.maxCount:
            raise RuntimeError("intended agent path is broken")
        return self._inner.act(obs)


def make_current_agent(deck):
    return BrokenAgent()
'''

INCUMBENT_DECK = Path("src/ptcg/decks/candidates/mega-lucario-fighting.csv")


def test_smoke_rejects_bundle_whose_agent_always_falls_back(tmp_path):
    """D2 minimal fix (2026-07-31): a bundle whose agent raises internally on
    every decision rides main.py's `[0]` exception fallback to a completed
    battle. Pre-fix, smoke_bundle printed SMOKE OK for such a bundle (the
    RED receipt for this test); post-fix it must reject the bundle because
    the intended agent path never actually ran."""
    build_bundle(INCUMBENT_DECK, tmp_path)
    staging = tmp_path / "submission"
    (staging / "ptcg" / "agents" / "current.py").write_text(
        BROKEN_CURRENT_PY, encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        smoke_bundle(staging)
    assert "fallback" in str(excinfo.value)


def test_smoke_still_passes_healthy_bundle(tmp_path):
    """The hardened smoke must not false-positive: the incumbent heuristic
    bundle (real ladder identity, real deck) still passes end to end."""
    build_bundle(INCUMBENT_DECK, tmp_path)
    result = smoke_bundle(tmp_path / "submission")
    assert "SMOKE OK" in result.stdout
