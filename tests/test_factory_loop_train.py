"""Tests for the TRAIN step -- offspring faucet (tournament T12).

`train_offspring` breeds one `SearchConfig` mutation off the CURRENT
baseline's agent config via the reused `ptcg.factory.breeding` operators,
retrains a value net via an injected `trainer_factory` (a stub here -- no
real GPU/subprocess in the fast suite, per this task's dispatch directive),
and records the result as a new `offspring` row.

Reuse-verification (plan Step 1): the mutated config's keys must stay a
subset of `GENE_SPEC ∪ CATEGORICAL_GENES` -- proof that `mutate_agent` was
called unmodified rather than re-authored, and that no extra metadata key
(`net_weights`, `agent_kind`) leaked into the gene dict.
"""
from __future__ import annotations

import datetime as dt
import json
import random
from pathlib import Path

from ptcg.factory import deckdb, loop, loop_state

_NOW = dt.datetime(2026, 7, 24, 12, 0, 0, tzinfo=dt.timezone.utc)

#: Hand-verified (see task report / dispatch): `random.Random(0)` applied to
#: `mutate_agent` against `loop.FOUNDING_AGENT_CONFIG`'s gene subset changes
#: `deviate_value_edge` (0.12 -> 0.0793...) and `c_puct` (1.5 -> 1.4820...) --
#: confirmed by executing `mutate_agent(random.Random(0), parent)` directly
#: before transcribing this seed into the test (`.claude/rules/plan-test-arithmetic-sanity.md`).
_SEED = 0


def _seed_founding(tmp_path: Path, agent_config: dict | None = None) -> object:
    """A deckdb with one concept/deck (`cA`/`dA`) and a founding `v0.1`
    baseline on that deck, mirroring the `_seed` helper convention already
    established in `tests/test_factory_runner_pool.py` /
    `tests/test_factory_rating.py` (tiny single-card `cards` lists -- deck
    legality is not under test here)."""
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)

    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cA','[\"X\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dA','cA','[101]')")

    deckdb._write(db, _s)
    loop_state.set_founding_baseline(db, "dA", agent_config or loop.FOUNDING_AGENT_CONFIG)
    return db


class _FakeTrainer:
    """Records calls, never touches torch/subprocess/GPU. Mirrors
    `PerDeckNetTrainer`'s producer/consumer method shapes (`prepare_data`/
    `train`) closely enough to exercise `train_offspring`'s real wiring, per
    the pattern already established in `tests/test_factory_trainer_worker.py`
    `_FakeTrainer`/`_fake_factory`."""

    def __init__(self, deck: Path, calls: list, data_dir: Path):
        self.deck = deck
        self.calls = calls
        self.data_dir = Path(data_dir)
        self.name = "fake"

    def prepare_data(self, cycle: int) -> Path:
        self.calls.append(("prepare_data", cycle, self.deck))
        out = self.data_dir / f"{Path(self.deck).stem}-c{cycle}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("{}\n", encoding="utf-8")
        return out

    def train(self, data_path: Path, cycle: int) -> Path:
        self.calls.append(("train", cycle))
        weights = self.data_dir / f"weights-c{cycle}.json"
        weights.write_text("{}", encoding="utf-8")
        return weights


def _fake_factory(calls: list, data_dir: Path):
    def factory(deck: Path) -> _FakeTrainer:
        return _FakeTrainer(deck, calls, data_dir)

    return factory


def test_train_offspring_no_baseline_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "GENERATED_DIR", tmp_path / "generated")
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)
    calls: list = []

    result = loop.train_offspring(db, random.Random(_SEED), _fake_factory(calls, tmp_path), _NOW)

    assert result is None
    assert calls == []  # never touched the trainer -- nothing to breed from yet


def test_train_offspring_creates_offspring_resting_at_training_pending_netcheck(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(loop, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    calls: list = []

    result = loop.train_offspring(db, random.Random(_SEED), _fake_factory(calls, tmp_path), _NOW)

    assert result == "v0.1.1"  # first offspring under founding v0.1 (T11 versioning)
    row = db.execute("SELECT * FROM offspring WHERE id=?", (result,)).fetchone()
    assert row is not None
    # Rests at the table default 'training' -- it does NOT auto-advance to
    # 'queued_for_match' -- while its net check plays (FOUNDING_AGENT_CONFIG
    # carries a real net_weights path, and the fresh trainer.train() output
    # is a distinct path, so candidate != incumbent -- a real netcheck
    # series, not the 'auto' short-circuit).
    assert row["status"] == "training"
    assert row["parent_baseline_version"] == "v0.1"
    assert row["value_net_ref"]  # a retrained net path was recorded

    net_check = db.execute(
        "SELECT verdict, games_planned FROM net_checks WHERE offspring_id=?", (result,)
    ).fetchone()
    assert net_check is not None
    assert net_check["verdict"] == "pending"
    assert net_check["games_planned"] == 100
    assert (
        db.execute(
            "SELECT COUNT(*) FROM games WHERE purpose='netcheck'"
        ).fetchone()[0]
        == 100
    )

    parent_gene_config = {
        k: v for k, v in loop.FOUNDING_AGENT_CONFIG.items() if k in loop._GENE_KEYS
    }
    child_gene_config = json.loads(row["search_config_json"])
    assert child_gene_config != parent_gene_config  # hand-verified above (seed=0)

    # Reuse-verification: mutate_agent must not have leaked non-gene keys
    # (net_weights, agent_kind) into the stored config.
    gene_keys = set(loop.GENE_SPEC) | set(loop.CATEGORICAL_GENES)
    assert set(child_gene_config.keys()) <= gene_keys

    # The trainer was actually driven (prepare_data then train, same cycle).
    assert [c[0] for c in calls] == ["prepare_data", "train"]
    assert calls[0][1] == calls[1][1]  # same cycle threaded through both calls

    # Deck materialization: a real CSV was written under GENERATED_DIR for
    # the trainer to reference (mirrors episodes.py/breeding.py convention).
    deck_csv = tmp_path / "generated" / "dA.csv"
    assert deck_csv.exists()
    assert deck_csv.read_text(encoding="utf-8").strip() == "101"


def test_train_offspring_advances_offspring_version_across_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    calls: list = []

    first = loop.train_offspring(db, random.Random(_SEED), _fake_factory(calls, tmp_path), _NOW)
    second = loop.train_offspring(
        db, random.Random(_SEED + 1), _fake_factory(calls, tmp_path), _NOW
    )

    assert first == "v0.1.1"
    assert second == "v0.1.2"
    # Both rest at 'training' pending their own net check (see the
    # resting-at-training test above) rather than auto-advancing.
    assert loop_state.list_offspring(db, "training")[0]["id"] == "v0.1.1"
    assert [r["id"] for r in loop_state.list_offspring(db, "training")] == [
        "v0.1.1",
        "v0.1.2",
    ]


def test_current_baseline_gene_config_founding_provenance(tmp_path):
    """Provenance shape 1/2 (`.claude/rules/provenance-shaped-optional-fields.md`):
    the FOUNDING baseline's gene config comes from `meta['founding_agent_config']`
    (T11 `set_founding_baseline`), filtered to gene-only keys."""
    db = _seed_founding(tmp_path)
    baseline = loop_state.current_baseline(db)

    gene_config = loop._current_baseline_gene_config(db, baseline)

    expected = {k: v for k, v in loop.FOUNDING_AGENT_CONFIG.items() if k in loop._GENE_KEYS}
    assert gene_config == expected
    assert "net_weights" not in gene_config  # non-gene key filtered out


def test_current_baseline_gene_config_crowned_provenance(tmp_path):
    """Provenance shape 2/2: a CROWNED baseline's gene config comes from the
    winning offspring's OWN `search_config_json` -- T11's `crown_baseline`
    does not persist a separate copy, the offspring row IS the record."""
    db = _seed_founding(tmp_path)
    crowned_gene_config = {
        **{k: v for k, v in loop.FOUNDING_AGENT_CONFIG.items() if k in loop._GENE_KEYS},
        "rollout_depth": 3,
    }
    loop_state.insert_offspring(db, "off-1", json.dumps(crowned_gene_config), "w.json")
    loop_state.crown_baseline(db, "off-1", "dA")

    baseline = loop_state.current_baseline(db)
    gene_config = loop._current_baseline_gene_config(db, baseline)

    assert gene_config == crowned_gene_config
    founding_gene_config = {
        k: v for k, v in loop.FOUNDING_AGENT_CONFIG.items() if k in loop._GENE_KEYS
    }
    assert gene_config != founding_gene_config  # did NOT fall back to the founding config


def test_train_offspring_after_crown_breeds_under_new_baseline_version(tmp_path, monkeypatch):
    """End-to-end: after a CROWN event, train_offspring resumes numbering
    under the NEW baseline version (v0.2.1, not v0.1.2) -- proving
    `current_baseline`/`next_offspring_version` (not a stale founding
    reference) drove the call."""
    monkeypatch.setattr(loop, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    crowned_gene_config = {
        k: v for k, v in loop.FOUNDING_AGENT_CONFIG.items() if k in loop._GENE_KEYS
    }
    loop_state.insert_offspring(db, "off-1", json.dumps(crowned_gene_config), "w.json")
    loop_state.crown_baseline(db, "off-1", "dA")
    calls: list = []

    result = loop.train_offspring(db, random.Random(_SEED), _fake_factory(calls, tmp_path), _NOW)

    assert result == "v0.2.1"
    row = db.execute(
        "SELECT parent_baseline_version FROM offspring WHERE id=?", (result,)
    ).fetchone()
    assert row["parent_baseline_version"] == "v0.2"


def test_train_offspring_auto_advances_when_founding_config_has_no_net(tmp_path, monkeypatch):
    """Degenerate netcheck shape (`ptcg.factory.netcheck.enqueue_net_check`):
    when the founding baseline's stored config has no `net_weights` key,
    `_incumbent_net_ref` resolves to None, so the net check short-circuits
    to verdict='auto' and the offspring is advanced straight to
    'queued_for_match' -- no netcheck games played."""
    monkeypatch.setattr(loop, "GENERATED_DIR", tmp_path / "generated")
    no_net_config = {k: v for k, v in loop.FOUNDING_AGENT_CONFIG.items() if k != "net_weights"}
    db = _seed_founding(tmp_path, agent_config=no_net_config)
    calls: list = []

    result = loop.train_offspring(db, random.Random(_SEED), _fake_factory(calls, tmp_path), _NOW)

    assert result == "v0.1.1"
    row = db.execute("SELECT status FROM offspring WHERE id=?", (result,)).fetchone()
    assert row["status"] == "queued_for_match"

    net_check = db.execute(
        "SELECT verdict, games_planned FROM net_checks WHERE offspring_id=?", (result,)
    ).fetchone()
    assert net_check is not None
    assert net_check["verdict"] == "auto"
    assert net_check["games_planned"] == 0
    assert db.execute("SELECT COUNT(*) FROM games WHERE purpose='netcheck'").fetchone()[0] == 0


def test_founding_agent_config_is_search_net_only_and_gene_complete():
    """Locked Decision 1 (ISMCTS only, no HeuristicAgent) + reuse-verification
    that `FOUNDING_AGENT_CONFIG`'s gene subset covers every GENE_SPEC/
    CATEGORICAL_GENES key (so `mutate_agent` never needs to invent a
    fallback default for a missing key)."""
    gene_keys = set(loop.GENE_SPEC) | set(loop.CATEGORICAL_GENES)
    present_gene_keys = {k for k in loop.FOUNDING_AGENT_CONFIG if k in gene_keys}
    assert present_gene_keys == gene_keys
    assert loop.FOUNDING_AGENT_CONFIG["net_weights"] == "src/ptcg/search/value_net_weights_v2.json"
    assert "agent_kind" not in loop.FOUNDING_AGENT_CONFIG or (
        loop.FOUNDING_AGENT_CONFIG.get("agent_kind") == "search-net"
    )
