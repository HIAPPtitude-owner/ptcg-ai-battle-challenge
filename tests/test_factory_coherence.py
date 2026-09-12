"""Coherence checks over the REAL factory ledgers (run on every suite pass)."""
from __future__ import annotations

from pathlib import Path

import pytest

from ptcg.factory.candidates import load_ledger, make_id, parse_version

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "experiments" / "factory" / "candidates.json"


@pytest.mark.skipif(not LEDGER.exists(), reason="factory ledger not seeded yet")
def test_real_ledger_is_coherent():
    cands = load_ledger(LEDGER)
    assert cands, "ledger exists but is empty"
    ids = [c.id for c in cands]
    assert len(ids) == len(set(ids)), f"duplicate candidate ids: {ids}"
    for c in cands:
        assert c.id == make_id(c.name, c.version)
        parse_version(c.version)  # raises on malformed versions
        assert (ROOT / c.deck).exists(), f"{c.id}: deck missing {c.deck}"
        assert 0.0 <= c.priority <= 1.0
        if c.agent_kind == "search-net":
            weights = c.agent_config.get("net_weights")
            assert weights, f"{c.id}: search-net without net_weights"
            assert (ROOT / weights).exists(), f"{c.id}: weights missing {weights}"
        if c.local_wr is not None:
            assert 0.0 <= c.local_wr <= 1.0
        if c.submitted_at is not None:
            assert c.status.value in ("submitted", "scored", "retired")
