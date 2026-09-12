"""Tests for the factory candidate model + JSON ledger."""
from __future__ import annotations

import json
import os
import threading
import time

import pytest

from ptcg.factory.candidates import (Candidate, Status, bump_minor, ledger_lock,
                                     load_ledger, make_id, merge_save, next_version,
                                     parse_version, save_ledger)


def test_version_parse_and_bump():
    assert parse_version("v1.2") == (1, 2)
    assert bump_minor("v1.2") == "v1.3"
    assert make_id("lucario-heuristic", "v1.0") == "lucario-heuristic-v1.0"
    with pytest.raises(ValueError):
        parse_version("1.2")


def test_next_version_bumps_max_minor():
    cands = [
        Candidate.create(name="a-net", version="v0.1", deck="d.csv", agent_kind="heuristic"),
        Candidate.create(name="a-net", version="v0.3", deck="d.csv", agent_kind="heuristic"),
        Candidate.create(name="other", version="v9.9", deck="d.csv", agent_kind="heuristic"),
    ]
    assert next_version(cands, "a-net") == "v0.4"
    assert next_version(cands, "brand-new") == "v0.1"


def test_ledger_round_trip_atomic_and_utf8(tmp_path):
    path = tmp_path / "candidates.json"
    cand = Candidate.create(
        name="lucario-heuristic", version="v1.0",
        deck="src/ptcg/decks/candidates/mega-lucario-fighting.csv",
        agent_kind="heuristic", provenance="seed", priority=0.747,
        notes="unicode ok: ⚠")
    cand.status = Status.EVALUATED
    cand.local_wr = 0.6
    save_ledger(path, [cand])
    assert not path.with_suffix(".json.tmp").exists()  # temp+rename cleaned up
    loaded = load_ledger(path)
    assert loaded[0].id == "lucario-heuristic-v1.0"
    assert loaded[0].status is Status.EVALUATED
    assert loaded[0].local_wr == 0.6
    assert loaded[0].notes.endswith("⚠")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["candidates"][0]["status"] == "evaluated"


def test_load_ledger_missing_file_and_unknown_keys(tmp_path):
    assert load_ledger(tmp_path / "nope.json") == []
    doc = {"version": 1, "candidates": [{
        "id": "x-v1.0", "name": "x", "version": "v1.0", "deck": "d.csv",
        "agent_kind": "heuristic", "status": "queued",
        "some_future_field": 42}]}
    p = tmp_path / "candidates.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    loaded = load_ledger(p)
    assert loaded[0].name == "x" and loaded[0].status is Status.QUEUED


def test_ledger_lock_excludes_second_acquirer_until_release(tmp_path):
    """Two threads simulate two processes racing for the same ledger lock:
    the second acquirer must block until the first releases, never overlap."""
    path = tmp_path / "candidates.json"
    order: list[str] = []
    first_holding = threading.Event()
    release_first = threading.Event()

    def holder():
        with ledger_lock(path, timeout_s=5.0):
            order.append("first-acquired")
            first_holding.set()
            release_first.wait(timeout=5)
            order.append("first-released")

    def contender():
        assert first_holding.wait(timeout=5), "first thread never acquired the lock"
        with ledger_lock(path, timeout_s=5.0):
            order.append("second-acquired")

    t1 = threading.Thread(target=holder)
    t2 = threading.Thread(target=contender)
    t1.start()
    t2.start()
    time.sleep(0.2)  # let contender start blocking on the held lock
    assert order == ["first-acquired"]  # contender must not have acquired yet
    release_first.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert order == ["first-acquired", "first-released", "second-acquired"]
    assert not (tmp_path / "candidates.json.lock").exists()  # lock cleaned up


def test_ledger_lock_breaks_stale_lock(tmp_path):
    path = tmp_path / "candidates.json"
    lock_path = tmp_path / "candidates.json.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, b"99999 0")
    os.close(fd)
    old = time.time() - 999  # well past the stale threshold
    os.utime(lock_path, (old, old))

    broke: list[str] = []
    with ledger_lock(path, timeout_s=5.0, stale_after_s=120.0,
                     log=lambda m: broke.append(m)):
        pass  # acquisition succeeding at all proves the stale lock was broken
    assert any("stale" in m for m in broke)
    assert not lock_path.exists()  # released cleanly after our own hold


def test_ledger_lock_timeout_raises_clear_error(tmp_path):
    path = tmp_path / "candidates.json"
    lock_path = tmp_path / "candidates.json.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, f"{os.getpid()} {time.time()}".encode("utf-8"))
    os.close(fd)  # lock held by "another process", fresh (not stale)
    try:
        with pytest.raises(TimeoutError, match="timed out.*ledger lock"):
            with ledger_lock(path, timeout_s=0.3, stale_after_s=120.0):
                pass
    finally:
        lock_path.unlink()


def test_ledger_lock_creates_missing_parent_directory(tmp_path):
    """Regression for factory_daemon.py crashing on a first-ever run: the
    lock file is created UNDER `path`'s parent, which may not exist yet
    (nothing has created experiments/factory/ before the daemon's first
    write). ledger_lock must ensure its own parent dir exists rather than
    relying on save_ledger's mkdir, which runs too late (after lock
    acquisition already failed)."""
    path = tmp_path / "nested" / "does" / "not" / "exist" / "candidates.json"
    assert not path.parent.exists()
    with ledger_lock(path, timeout_s=5.0):
        pass  # acquiring at all proves the parent dir was created first
    assert path.parent.exists()
    assert not (path.parent / "candidates.json.lock").exists()  # cleaned up


def test_merge_save_creates_missing_parent_directory(tmp_path):
    """End-to-end regression matching the exact daemon crash: merge_save on
    a ledger path whose parent tree doesn't exist yet must succeed, leave
    the ledger file written, and leave no stale .lock behind."""
    path = tmp_path / "nested" / "does" / "not" / "exist" / "candidates.json"
    cand = Candidate.create(name="x", version="v0.1", deck="d.csv", agent_kind="heuristic")
    merged = merge_save(path, [cand])
    assert merged[0].id == "x-v0.1"
    assert path.exists()
    assert not (path.parent / "candidates.json.lock").exists()


def test_local_breakdown_round_trips_through_ledger(tmp_path):
    """local_breakdown (structured per-baseline eval results) must survive
    save_ledger -> load_ledger and merge_save untouched."""
    path = tmp_path / "candidates.json"
    cand = Candidate.create(name="dual", version="v1.0", deck="d.csv",
                            agent_kind="heuristic")
    cand.local_breakdown = [
        {"baseline": "mega-lucario-fighting", "wins": 43, "games": 75},
        {"baseline": "mega-starmie-water", "wins": 54, "games": 75},
    ]
    save_ledger(path, [cand])
    loaded = load_ledger(path)
    assert loaded[0].local_breakdown == [
        {"baseline": "mega-lucario-fighting", "wins": 43, "games": 75},
        {"baseline": "mega-starmie-water", "wins": 54, "games": 75},
    ]
    merged = merge_save(path, loaded)
    assert merged[0].local_breakdown == loaded[0].local_breakdown


def test_old_ledger_entry_without_breakdown_loads_as_none(tmp_path):
    """Schema tolerance: an old ledger entry written before the field existed
    must load with local_breakdown None (same back-compat convention as
    last_resubmitted_at; unknown future keys stay tolerated too)."""
    doc = {"version": 1, "candidates": [{
        "id": "x-v1.0", "name": "x", "version": "v1.0", "deck": "d.csv",
        "agent_kind": "heuristic", "status": "queued",
        "some_future_field": 42}]}
    p = tmp_path / "candidates.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    loaded = load_ledger(p)
    assert loaded[0].local_breakdown is None
    assert loaded[0].name == "x"  # unknown-key tolerance unchanged
