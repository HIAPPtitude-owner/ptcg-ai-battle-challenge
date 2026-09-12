"""Tests for baseline + offspring state helpers over SQLite (tournament T11).

Covers version-progression arithmetic (`next_offspring_version`/
`crown_baseline` built on `candidates.parse_version`/`bump_minor`), the
founding-baseline write path, and the offspring row lifecycle
(`insert_offspring`/`set_offspring_status`/`list_offspring`).
"""

from __future__ import annotations

import json

import pytest

from ptcg.factory import deckdb, loop_state


def _db(tmp_path):
    d = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(d)
    return d


def test_version_progression(tmp_path):
    """Plan-authored test (T11 spec), transcribed verbatim after hand-checking
    the arithmetic: `bump_minor("v0.1") == "v0.2"` (verified against
    `candidates.py:36` via a python one-liner before transcription)."""
    db = _db(tmp_path)
    loop_state.set_founding_baseline(db, "dA", {"agent_kind": "search-net"})
    assert loop_state.current_baseline(db)["version"] == "v0.1"
    assert loop_state.next_offspring_version(db) == "v0.1.1"  # first offspring under v0.1
    # crown bumps minor: v0.1 -> v0.2  (parse_version/bump_minor verified at candidates.py)
    assert loop_state.crown_baseline(db, "off-1", "dB") == "v0.2"
    assert loop_state.next_offspring_version(db) == "v0.2.1"  # offspring numbering resets under v0.2


def test_current_baseline_none_before_founding(tmp_path):
    db = _db(tmp_path)
    assert loop_state.current_baseline(db) is None


def test_set_founding_baseline_persists_deck_and_agent_config(tmp_path):
    db = _db(tmp_path)
    loop_state.set_founding_baseline(db, "dA", {"agent_kind": "search-net", "net": "v2"})
    baseline = loop_state.current_baseline(db)
    assert baseline["deck_id"] == "dA"
    assert baseline["offspring_id"] is None
    assert baseline["crowned_at"]
    # No dedicated column exists on `baselines` for agent_config (locked Phase-1
    # schema) — stored as JSON in `meta` for retrievability (JUDGMENT CALL, see
    # task report).
    stored = db.execute(
        "SELECT value FROM meta WHERE key='founding_agent_config'"
    ).fetchone()
    assert json.loads(stored["value"]) == {"agent_kind": "search-net", "net": "v2"}


def test_insert_offspring_stamps_parent_baseline_version(tmp_path):
    db = _db(tmp_path)
    loop_state.set_founding_baseline(db, "dA", {"agent_kind": "search-net"})
    loop_state.insert_offspring(db, "off-1", '{"gene":1}', "weights.json")
    rows = loop_state.list_offspring(db, "training")
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "off-1"
    assert row["parent_baseline_version"] == "v0.1"
    assert row["search_config_json"] == '{"gene":1}'
    assert row["value_net_ref"] == "weights.json"
    assert row["status"] == "training"
    assert row["created_at"]


def test_insert_offspring_advances_next_offspring_version(tmp_path):
    db = _db(tmp_path)
    loop_state.set_founding_baseline(db, "dA", {"agent_kind": "search-net"})
    assert loop_state.next_offspring_version(db) == "v0.1.1"
    loop_state.insert_offspring(db, "off-1", "{}", None)
    assert loop_state.next_offspring_version(db) == "v0.1.2"
    loop_state.insert_offspring(db, "off-2", "{}", None)
    assert loop_state.next_offspring_version(db) == "v0.1.3"


def test_set_offspring_status_updates_row(tmp_path):
    db = _db(tmp_path)
    loop_state.set_founding_baseline(db, "dA", {"agent_kind": "search-net"})
    loop_state.insert_offspring(db, "off-1", "{}", None)
    loop_state.set_offspring_status(db, "off-1", "queued_for_match")
    rows = loop_state.list_offspring(db, "queued_for_match")
    assert [r["id"] for r in rows] == ["off-1"]
    assert loop_state.list_offspring(db, "training") == []


def test_set_offspring_status_rejects_invalid_status(tmp_path):
    db = _db(tmp_path)
    loop_state.set_founding_baseline(db, "dA", {"agent_kind": "search-net"})
    loop_state.insert_offspring(db, "off-1", "{}", None)
    with pytest.raises(ValueError):
        loop_state.set_offspring_status(db, "off-1", "not-a-real-status")


def test_list_offspring_filters_by_status(tmp_path):
    db = _db(tmp_path)
    loop_state.set_founding_baseline(db, "dA", {"agent_kind": "search-net"})
    loop_state.insert_offspring(db, "off-1", "{}", None)
    loop_state.insert_offspring(db, "off-2", "{}", None)
    loop_state.set_offspring_status(db, "off-2", "survivor")
    training = loop_state.list_offspring(db, "training")
    survivors = loop_state.list_offspring(db, "survivor")
    assert [r["id"] for r in training] == ["off-1"]
    assert [r["id"] for r in survivors] == ["off-2"]


def test_crown_baseline_inserts_baselines_row_and_updates_meta(tmp_path):
    db = _db(tmp_path)
    loop_state.set_founding_baseline(db, "dA", {"agent_kind": "search-net"})
    loop_state.insert_offspring(db, "off-1", "{}", None)
    new_version = loop_state.crown_baseline(db, "off-1", "dB")
    assert new_version == "v0.2"
    baseline = loop_state.current_baseline(db)
    assert baseline["version"] == "v0.2"
    assert baseline["offspring_id"] == "off-1"
    assert baseline["deck_id"] == "dB"
    # the founding baseline row is still present in the history table
    old = db.execute("SELECT * FROM baselines WHERE version='v0.1'").fetchone()
    assert old is not None
    assert old["deck_id"] == "dA"


def test_next_offspring_version_without_baseline_raises(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(RuntimeError):
        loop_state.next_offspring_version(db)
