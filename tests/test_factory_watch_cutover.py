"""Narrowed `watch_once()` behavior after the T20 cutover: episode harvest +
submission scheduler only, with a GUARANTEED terminal log marker on every
exit path (paused / busy / harvest / submit-held / submit / error).

The terminal-marker-on-every-path assertions are the direct fix for the
36-hour silent-crash-cascade (`.claude/rules/factory-task-scheduler-liveness.md`
terminal-marker section): the legacy SUBMIT_HOLD/crash path could launch a
firing and write NO terminal line, hiding ~96 partial-progress crashes."""
import datetime as dt
import sys

import pytest

from ptcg.factory.cycle import FactoryPaths
from ptcg.factory.watch import instance_lock
from scripts import factory_watch_once as watch_once_module
from scripts.factory_watch_once import watch_once, watch_paths


# --- stubs ------------------------------------------------------------

def _harvest_stub(calls, ret="no-new", raises=False):
    def harvest_fn(paths, client, *, now, log=print):
        calls.append(now)
        if raises:
            raise RuntimeError("kaggle harvest down")
        return ret
    return harvest_fn


def _submit_stub(calls, ret=None, raises=False):
    def submit_fn(conn, client, counter, state_path, out_dir, repo, now,
                  *, no_submit=False, log=print):
        calls.append(dict(now=now, no_submit=no_submit, state_path=state_path,
                          out_dir=out_dir, repo=repo))
        if raises:
            raise RuntimeError("submit blew up")
        return list(ret if ret is not None
                    else [("tournament-champion-v0.1", "submitted", "ref-123")])
    return submit_fn


def _snapshot_stub(calls, ret="ok(3 rows)", raises=False):
    def snapshot_fn(paths, client, *, now, log=print):
        if raises:
            raise RuntimeError("snapshot boom")
        calls.append(now)
        return ret
    return snapshot_fn


def _refuse_harvest(*a, **k):
    raise AssertionError("harvest_fn must not be called")


def _refuse_submit(*a, **k):
    raise AssertionError("submit_fn must not be called")


def _refuse_snapshot(*a, **k):
    raise AssertionError("snapshot_fn must not be called")


def _log_text(paths) -> str:
    return watch_paths(paths)["watch_log"].read_text(encoding="utf-8")


def _mkpaths(tmp_path):
    return FactoryPaths(root=tmp_path), tmp_path / "t.db"


# --- pause ------------------------------------------------------------

def test_paused_short_circuits_with_terminal_marker(tmp_path):
    paths, db = _mkpaths(tmp_path)
    paths.pause_file.parent.mkdir(parents=True, exist_ok=True)
    paths.pause_file.write_text("paused for T20", encoding="utf-8")

    result = watch_once(paths, object(), db_path=db,
                        harvest_fn=_refuse_harvest, submit_fn=_refuse_submit,
                        snapshot_fn=_refuse_snapshot)

    assert result == {"paused": True}
    log = _log_text(paths)
    assert log.strip().endswith("paused")          # terminal marker present
    assert "episodes:" not in log and "submit:" not in log


# --- busy -------------------------------------------------------------

def test_busy_when_lock_held_with_terminal_marker(tmp_path):
    paths, db = _mkpaths(tmp_path)
    lock_path = watch_paths(paths)["watch_lock"]

    with instance_lock(lock_path):
        result = watch_once(paths, object(), db_path=db,
                            harvest_fn=_refuse_harvest, submit_fn=_refuse_submit,
                            snapshot_fn=_refuse_snapshot)

    assert result == {"busy": True}
    assert "busy: another firing holds the lock" in _log_text(paths)


# --- harvest ----------------------------------------------------------

def test_harvest_runs_before_submit_and_logs(tmp_path):
    paths, db = _mkpaths(tmp_path)
    hcalls, scalls = [], []

    result = watch_once(paths, object(), db_path=db,
                        harvest_fn=_harvest_stub(hcalls, ret="harvested: 2"),
                        submit_fn=_submit_stub(scalls),
                        snapshot_fn=_snapshot_stub([]))

    assert len(hcalls) == 1 and len(scalls) == 1  # both ran, harvest first
    assert result["harvest"] == "harvested: 2"
    log = _log_text(paths)
    assert "episodes: harvested: 2" in log
    assert "submit:" in log


def test_harvest_failure_isolated_submit_still_runs(tmp_path):
    """A raising harvester must NOT block the submission scheduler and must
    still emit its own terminal `episodes: error (isolated)` marker."""
    paths, db = _mkpaths(tmp_path)
    scalls = []

    result = watch_once(paths, object(), db_path=db,
                        harvest_fn=_harvest_stub([], raises=True),
                        submit_fn=_submit_stub(scalls),
                        snapshot_fn=_snapshot_stub([]))

    assert result["harvest"] == "error"
    assert len(scalls) == 1                         # submit ran despite harvest failure
    log = _log_text(paths)
    assert "episodes: error (isolated)" in log
    assert "submit:" in log


# --- ladder score snapshot (freeze-pair probe spec 2026-08-14) --------

def test_snapshot_runs_after_harvest_and_logs_marker(tmp_path):
    paths, db = _mkpaths(tmp_path)
    scalls = []

    result = watch_once(paths, object(), db_path=db,
                        harvest_fn=_harvest_stub([], ret="no-new"),
                        snapshot_fn=_snapshot_stub(scalls),
                        submit_fn=_submit_stub([], ret=[]))

    assert scalls, "snapshot step never ran"
    log = _log_text(paths)
    assert "snapshot: ok(3 rows)" in log
    assert log.index("episodes:") < log.index("snapshot:") < log.index("submit:")
    assert result["snapshot"] == "ok(3 rows)"


def test_snapshot_failure_isolated_submit_still_runs(tmp_path):
    paths, db = _mkpaths(tmp_path)
    scalls = []

    result = watch_once(paths, object(), db_path=db,
                        harvest_fn=_harvest_stub([]),
                        snapshot_fn=_snapshot_stub([], raises=True),
                        submit_fn=_submit_stub(scalls, ret=[]))

    assert scalls, "submit must run despite snapshot exception"
    assert "snapshot: error (isolated)" in _log_text(paths)
    assert result["snapshot"] == "error"


def test_snapshot_runs_even_when_submit_hold_present(tmp_path):
    paths, db = _mkpaths(tmp_path)
    paths.submit_hold_file.parent.mkdir(parents=True, exist_ok=True)
    paths.submit_hold_file.write_text("", encoding="utf-8")
    scalls = []

    result = watch_once(paths, object(), db_path=db,
                        harvest_fn=_harvest_stub([]),
                        snapshot_fn=_snapshot_stub(scalls),
                        submit_fn=_refuse_submit)

    assert scalls, "snapshot is read-only and must run under SUBMIT_HOLD"
    log = _log_text(paths)
    assert "snapshot: ok(3 rows)" in log
    assert "submit: held (SUBMIT_HOLD present)" in log
    assert result["snapshot"] == "ok(3 rows)"
    assert result["submit_held"] is True


def test_snapshot_not_due_marker_logged(tmp_path):
    paths, db = _mkpaths(tmp_path)

    watch_once(paths, object(), db_path=db,
               harvest_fn=_harvest_stub([]),
               snapshot_fn=_snapshot_stub([], ret="not-due"),
               submit_fn=_submit_stub([], ret=[]))

    assert "snapshot: not-due" in _log_text(paths)


# --- submit-hold ------------------------------------------------------

def test_submit_hold_skips_scheduler_with_terminal_marker(tmp_path):
    """SUBMIT_HOLD is the CALLER's gate (mirror cycle.py:256): maybe_submit is
    NOT invoked, but the hold path MUST emit its terminal marker (the exact
    path the 36h silent-crash-cascade rule flags)."""
    paths, db = _mkpaths(tmp_path)
    paths.submit_hold_file.parent.mkdir(parents=True, exist_ok=True)
    paths.submit_hold_file.write_text("hold", encoding="utf-8")
    hcalls = []

    result = watch_once(paths, object(), db_path=db,
                        harvest_fn=_harvest_stub(hcalls), submit_fn=_refuse_submit,
                        snapshot_fn=_snapshot_stub([]))

    assert result == {"harvest": "no-new", "snapshot": "ok(3 rows)", "submit_held": True}
    assert len(hcalls) == 1                          # harvest STILL runs under hold
    log = _log_text(paths)
    assert "submit: held (SUBMIT_HOLD present)" in log


# --- submit -----------------------------------------------------------

def test_submit_runs_and_logs_summary(tmp_path):
    paths, db = _mkpaths(tmp_path)
    scalls = []
    ret = [("tournament-champion-v0.2", "submitted", "ref-999")]

    result = watch_once(paths, object(), db_path=db,
                        harvest_fn=_harvest_stub([]), submit_fn=_submit_stub(scalls, ret=ret),
                        snapshot_fn=_snapshot_stub([]))

    assert result["submit"] == ret
    assert "submit: tournament-champion-v0.2=submitted" in _log_text(paths)


def test_submit_receives_utc_aware_now(tmp_path):
    """subscheduler.mark_index buckets by UTC calendar day, so watch_once MUST
    hand maybe_submit a tz-aware UTC datetime (T17 review carry-forward)."""
    paths, db = _mkpaths(tmp_path)
    scalls = []

    watch_once(paths, object(), db_path=db,
               harvest_fn=_harvest_stub([]), submit_fn=_submit_stub(scalls),
               snapshot_fn=_snapshot_stub([]))

    now = scalls[0]["now"]
    assert now.tzinfo is not None and now.utcoffset() == dt.timedelta(0)


def test_submit_no_op_summary(tmp_path):
    paths, db = _mkpaths(tmp_path)

    result = watch_once(paths, object(), db_path=db,
                        harvest_fn=_harvest_stub([]), submit_fn=_submit_stub([], ret=[]),
                        snapshot_fn=_snapshot_stub([]))

    assert result["submit"] == []
    assert "submit: no-op" in _log_text(paths)


def test_no_submit_flag_forwarded(tmp_path):
    paths, db = _mkpaths(tmp_path)
    scalls = []

    watch_once(paths, object(), db_path=db, no_submit=True,
               harvest_fn=_harvest_stub([]), submit_fn=_submit_stub(scalls),
               snapshot_fn=_snapshot_stub([]))

    assert scalls[0]["no_submit"] is True


# --- error paths (terminal marker + nonzero exit) ---------------------

def test_naive_now_is_rejected_as_error(tmp_path):
    """A naive (non-UTC) `now` from the seam trips the runtime guard, which
    surfaces as the error path with its terminal `cycle-error:` marker."""
    paths, db = _mkpaths(tmp_path)

    result = watch_once(paths, object(), db_path=db,
                        harvest_fn=_harvest_stub([]), submit_fn=_submit_stub([]),
                        snapshot_fn=_snapshot_stub([]),
                        now_fn=lambda: dt.datetime(2026, 7, 24, 12, 0, 0))  # naive

    assert result["error"].startswith("ValueError")
    log = _log_text(paths)
    assert "cycle-error: ValueError" in log


def test_submit_exception_is_caught_logged_with_terminal_marker(tmp_path):
    """A crashing maybe_submit must be caught, produce an {"error": ...}
    result AND write a `cycle-error:` terminal marker with a traceback -- the
    silent-crash-cascade fix. Harvest already logged its own marker first."""
    paths, db = _mkpaths(tmp_path)

    result = watch_once(paths, object(), db_path=db,
                        harvest_fn=_harvest_stub([]), submit_fn=_submit_stub([], raises=True),
                        snapshot_fn=_snapshot_stub([]))

    assert result["error"] == "RuntimeError: submit blew up"
    log = _log_text(paths)
    assert "episodes:" in log                        # harvest marker landed first
    assert "cycle-error: RuntimeError: submit blew up" in log
    assert "Traceback (most recent call last)" in log


def test_lock_released_after_submit_exception(tmp_path):
    paths, db = _mkpaths(tmp_path)

    watch_once(paths, object(), db_path=db,
               harvest_fn=_harvest_stub([]), submit_fn=_submit_stub([], raises=True),
               snapshot_fn=_snapshot_stub([]))

    with instance_lock(watch_paths(paths)["watch_lock"]):  # no stale hold
        pass


# --- main() exit-code contract ----------------------------------------

def test_main_exits_nonzero_on_error_result(monkeypatch):
    monkeypatch.setattr(watch_once_module, "KaggleClient", lambda: object())
    monkeypatch.setattr(watch_once_module, "watch_once",
                        lambda *a, **k: {"error": "RuntimeError: boom"})
    monkeypatch.setattr(sys, "argv", ["factory_watch_once.py"])

    with pytest.raises(SystemExit) as exc_info:
        watch_once_module.main()
    assert exc_info.value.code == 1


def test_main_exits_zero_on_routine_result(monkeypatch, capsys):
    monkeypatch.setattr(watch_once_module, "KaggleClient", lambda: object())
    monkeypatch.setattr(watch_once_module, "watch_once",
                        lambda *a, **k: {"paused": True})
    monkeypatch.setattr(sys, "argv", ["factory_watch_once.py"])

    watch_once_module.main()  # must not raise SystemExit
    assert "watch_once result" in capsys.readouterr().out
