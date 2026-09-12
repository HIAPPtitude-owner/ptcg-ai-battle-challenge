"""Tests for the continuous matrix worker loop + entry script (spec
compute-saturation, Task 6): `worker_tick`'s paused/idle/played ticks, the
always-on heartbeat, the win/loss-excludes-draws recording contract, and the
script's single-instance "busy" exit.
"""
from __future__ import annotations

import datetime as dt
import json

from ptcg.factory import tournament
from ptcg.factory.candidates import Candidate, Status, load_ledger, merge_save
from ptcg.factory.cycle import FactoryPaths
from ptcg.factory.tournament import MatrixLedger, worker_tick
from ptcg.factory.watch import instance_lock
from scripts.factory_matrix_worker import run_worker


def _make(name: str) -> Candidate:
    return Candidate.create(name=name, version="v0.1", deck="d.csv", agent_kind="heuristic")


def _fake_series(wins_a: int, wins_b: int):
    def fn(cand_a, cand_b, n_games):
        return wins_a, wins_b
    return fn


def test_worker_tick_paused_when_pause_file_present(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    paths.pause_file.parent.mkdir(parents=True, exist_ok=True)
    paths.pause_file.write_text("", encoding="utf-8")
    calls: list[tuple[str, str]] = []

    def fn(cand_a, cand_b, n_games):
        calls.append((cand_a.id, cand_b.id))
        return 1, 0

    a, b = _make("a"), _make("b")
    merge_save(paths.ledger, [a, b])

    result = worker_tick(paths, series_fn=fn)

    assert result == "paused"
    assert calls == []  # no games played while paused
    assert paths.matrix_heartbeat.exists()


def test_worker_tick_idle_when_fewer_than_two_candidates(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    merge_save(paths.ledger, [_make("solo")])

    result = worker_tick(paths, series_fn=_fake_series(1, 0))

    assert result == "idle"
    assert paths.matrix_heartbeat.exists()
    # nothing recorded to a matrix ledger that was never even created
    assert not paths.matrix.exists()


def test_worker_tick_played_records_games_and_updates_candidates(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    a, b = _make("a"), _make("b")
    merge_save(paths.ledger, [a, b])
    fixed_now = dt.datetime(2026, 7, 20, 12, 0, tzinfo=dt.timezone.utc)

    result = worker_tick(paths, series_fn=_fake_series(7, 3), now=fixed_now)

    assert result == "played"

    ledger = MatrixLedger.load(paths.matrix)
    assert ledger.games_between(a.id, b.id) == 10

    saved = {c.id: c for c in load_ledger(paths.ledger)}
    assert saved[a.id].matrix_games == 10
    assert saved[b.id].matrix_games == 10
    assert saved[a.id].matrix_opponents == 1

    # daily snapshot appended for the fixed UTC date
    text = paths.experiments_md.read_text(encoding="utf-8")
    assert "2026-07-20" in text


def test_worker_tick_played_excludes_draws_from_recorded_games(tmp_path):
    """CRITICAL contract (reviewer-caught bug class): play_block's returned
    (wins_a, wins_b) EXCLUDES draws. The worker must record
    games=wins_a+wins_b, never the requested BLOCK_GAMES block size -
    otherwise draws get silently counted as losses, corrupting the matrix.
    """
    paths = FactoryPaths(root=tmp_path)
    a, b = _make("a"), _make("b")
    merge_save(paths.ledger, [a, b])

    # wins_a=4, wins_b=3 -> 7 decided games (3 draws dropped from a nominal
    # BLOCK_GAMES=10 block).
    result = worker_tick(paths, series_fn=_fake_series(4, 3))

    assert result == "played"
    ledger = MatrixLedger.load(paths.matrix)
    assert ledger.games_between(a.id, b.id) == 7  # NOT 10 (BLOCK_GAMES)


def test_worker_tick_heartbeat_written_every_tick_including_paused(tmp_path):
    paths = FactoryPaths(root=tmp_path)

    # paused tick, virgin experiments/factory dir
    paths.pause_file.parent.mkdir(parents=True, exist_ok=True)
    paths.pause_file.write_text("", encoding="utf-8")
    result = worker_tick(paths, series_fn=_fake_series(1, 0))
    assert result == "paused"
    payload = json.loads(paths.matrix_heartbeat.read_text(encoding="utf-8"))
    assert set(payload) == {"ts", "detail"}
    assert isinstance(payload["ts"], str) and isinstance(payload["detail"], str)

    # idle tick
    paths.pause_file.unlink()
    merge_save(paths.ledger, [_make("solo")])
    result = worker_tick(paths, series_fn=_fake_series(1, 0))
    assert result == "idle"
    payload = json.loads(paths.matrix_heartbeat.read_text(encoding="utf-8"))
    assert set(payload) == {"ts", "detail"}

    # played tick
    merge_save(paths.ledger, [_make("partner")])
    result = worker_tick(paths, series_fn=_fake_series(1, 0))
    assert result == "played"
    payload = json.loads(paths.matrix_heartbeat.read_text(encoding="utf-8"))
    assert set(payload) == {"ts", "detail"}


def test_worker_tick_reloads_candidates_under_lock_survives_concurrent_submission(tmp_path):
    """CRITICAL contract (reviewer-caught bug class): worker_tick loads
    `candidates.json` ONCE before the minutes-long unlocked `play_block`
    call. If refresh_ratings/enforce_pool_cap later mutate that STALE
    snapshot and hand it to merge_save - which does a full-row replace by
    id - any write a concurrent process makes to the same candidate WHILE
    play_block is running (e.g. the watch loop marking a Kaggle submission)
    is silently clobbered. worker_tick must reload candidates.json fresh
    UNDER THE LOCK, immediately before mutating, so only current rows are
    ever written back.

    The injected series_fn's side effect simulates that concurrent write:
    it fires mid-play_block (the exact stale-snapshot window) and directly
    merge_saves a SUBMITTED status + submitted_at onto candidate `a`.
    """
    paths = FactoryPaths(root=tmp_path)
    a, b = _make("a"), _make("b")
    merge_save(paths.ledger, [a, b])
    fixed_now = dt.datetime(2026, 7, 20, 12, 0, tzinfo=dt.timezone.utc)

    def concurrent_submit_series(cand_a, cand_b, n_games):
        # Fires while worker_tick still holds only its PRE-play_block
        # candidates snapshot - mirrors a real concurrent watch-loop
        # submission landing on disk during the minutes-long series.
        fresh = {c.id: c for c in load_ledger(paths.ledger)}
        submitted = fresh[a.id]
        submitted.status = Status.SUBMITTED
        submitted.submitted_at = "2026-07-20T12:00:30+00:00"
        merge_save(paths.ledger, [submitted])
        return 7, 3  # 7 decided games (BLOCK_GAMES=10, 3 draws dropped)

    result = worker_tick(paths, series_fn=concurrent_submit_series, now=fixed_now)

    assert result == "played"
    saved = {c.id: c for c in load_ledger(paths.ledger)}
    # The concurrent submission must SURVIVE the worker's own matrix-fields
    # write - not be clobbered by a full-row replace built from the stale
    # pre-play_block snapshot.
    assert saved[a.id].status == Status.SUBMITTED
    assert saved[a.id].submitted_at == "2026-07-20T12:00:30+00:00"
    # AND the worker's own matrix-tournament fields must still have landed
    # on the (now-fresh) row - the fix must not lose the real update either.
    assert saved[a.id].matrix_games == 10
    assert saved[b.id].matrix_games == 10


def test_worker_tick_appends_provenance_line_with_correct_fields(tmp_path, monkeypatch):
    """Finding 3 / spec Invariant 13 (reproducibility): every matrix block
    must append one JSONL line to the matrix_blocks.jsonl sidecar carrying
    pair identities, wins, timestamp, and the worker's git commit."""
    monkeypatch.setattr(tournament, "_worker_commit", lambda: "deadbee")
    paths = FactoryPaths(root=tmp_path)
    a, b = _make("a"), _make("b")
    merge_save(paths.ledger, [a, b])
    fixed_now = dt.datetime(2026, 7, 20, 12, 0, tzinfo=dt.timezone.utc)

    result = worker_tick(paths, series_fn=_fake_series(7, 3), now=fixed_now)

    assert result == "played"
    blocks_path = paths.matrix.with_name("matrix_blocks.jsonl")
    lines = blocks_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert {entry["a"], entry["b"]} == {a.id, b.id}
    assert {entry["wins_a"], entry["wins_b"]} == {7, 3}
    assert entry["commit"] == "deadbee"
    assert entry["ts"] == fixed_now.isoformat()


def test_worker_tick_provenance_append_preserves_prior_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(tournament, "_worker_commit", lambda: "abc0000")
    paths = FactoryPaths(root=tmp_path)
    a, b = _make("a"), _make("b")
    merge_save(paths.ledger, [a, b])

    worker_tick(paths, series_fn=_fake_series(1, 0),
               now=dt.datetime(2026, 7, 20, 12, 0, tzinfo=dt.timezone.utc))
    blocks_path = paths.matrix.with_name("matrix_blocks.jsonl")
    first_line = blocks_path.read_text(encoding="utf-8").splitlines()[0]

    worker_tick(paths, series_fn=_fake_series(1, 0),
               now=dt.datetime(2026, 7, 20, 12, 15, tzinfo=dt.timezone.utc))
    lines = blocks_path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    assert lines[0] == first_line  # append-only - prior line untouched


def test_worker_tick_survives_provenance_commit_failure(tmp_path, monkeypatch):
    """Provenance logging must never break a tick - a failure resolving the
    worker's git commit (subprocess unavailable, git missing, etc.) must
    still let the tick complete and return "played"."""
    def _raise():
        raise RuntimeError("git unavailable")
    monkeypatch.setattr(tournament, "_worker_commit", _raise)
    paths = FactoryPaths(root=tmp_path)
    a, b = _make("a"), _make("b")
    merge_save(paths.ledger, [a, b])

    result = worker_tick(paths, series_fn=_fake_series(1, 0))

    assert result == "played"


def test_run_worker_second_instance_returns_busy_without_playing(tmp_path):
    """Mirrors test_instance_lock_excludes_second_holder (test_factory_watch.py):
    a second worker that finds matrix.worker.lock already held must return
    "busy" cleanly rather than racing the first for the matrix ledger."""
    paths = FactoryPaths(root=tmp_path)
    a, b = _make("a"), _make("b")
    merge_save(paths.ledger, [a, b])
    calls: list[int] = []

    def fn(cand_a, cand_b, n_games):
        calls.append(1)
        return 1, 0

    with instance_lock(paths.matrix_worker_lock):
        result = run_worker(paths, max_ticks=1, series_fn=fn, log=lambda m: None)

    assert result == "busy"
    assert calls == []  # never got to play

    # lock is released afterward -> a fresh worker can proceed normally
    result2 = run_worker(paths, max_ticks=1, series_fn=fn, log=lambda m: None)
    assert result2 == "played"
    assert calls == [1]


def test_run_worker_max_ticks_zero_would_loop_forever_bounded_by_one(tmp_path):
    """--max-ticks 0 means loop forever; exercise the finite-N path (the
    production default) is exactly max_ticks ticks, not more."""
    paths = FactoryPaths(root=tmp_path)
    a, b = _make("a"), _make("b")
    merge_save(paths.ledger, [a, b])
    calls: list[int] = []

    def fn(cand_a, cand_b, n_games):
        calls.append(1)
        return 1, 0

    result = run_worker(paths, max_ticks=2, series_fn=fn, log=lambda m: None)

    assert result == "played"
    assert calls == [1, 1]  # exactly two ticks played (same pair both times)
