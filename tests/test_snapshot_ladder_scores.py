"""Tests for scripts/snapshot_ladder_scores.py (freeze-pair probe spec
2026-08-14). Covers: ref parsing reuse, counted-flag semantics, stamp gating
(incl. the pre-seeded FUTURE stamp no-op), virgin-directory append (parent NOT
pre-created — the tmp_path lesson), and CLI-failure tolerance (poll failure
appends nothing, stamp untouched, never raises)."""
from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

from ptcg.factory.kaggle_client import FakeKaggleClient, SubmissionRow
from scripts.snapshot_ladder_scores import (
    MIN_INTERVAL_S, build_rows, check_and_snapshot, load_stamp, save_stamp,
    snapshot_due, snapshot_path, stamp_path,
)

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 8, 14, 20, 0, 0, tzinfo=UTC)


def _rows():
    # Newest-first by date; the ERROR row is newest but must NOT be counted.
    return [
        SubmissionRow("s.tar.gz", "2026-08-14 19:00:00", "broken upload",
                      "ERROR", None, ref="900"),
        SubmissionRow("s.tar.gz", "2026-08-14 18:00:00", "best v0.16",
                      "COMPLETE", 510.0, ref="901"),
        SubmissionRow("s.tar.gz", "2026-08-14 17:00:00", "second lean-searchnet",
                      "PENDING", None, ref="902"),
        SubmissionRow("s.tar.gz", "2026-08-12 10:00:00", "evicted old",
                      "COMPLETE", 446.7, ref="903"),
    ]


def _paths(root):
    return SimpleNamespace(root=root)


def test_build_rows_counted_is_two_newest_non_error():
    rows = build_rows(_rows(), NOW)
    by_ref = {r["ref"]: r for r in rows}
    assert by_ref["900"]["is_counted"] is False   # ERROR never counted
    assert by_ref["901"]["is_counted"] is True
    assert by_ref["902"]["is_counted"] is True
    assert by_ref["903"]["is_counted"] is False
    assert by_ref["901"]["public_score"] == 510.0
    assert by_ref["901"]["utc_ts"] == NOW.isoformat()
    # hst_ts is the same instant at fixed UTC-10 (no tzdata on this host)
    assert by_ref["901"]["hst_ts"].endswith("-10:00")
    assert by_ref["901"]["hst_ts"].startswith("2026-08-14T10:00:00")


def test_check_and_snapshot_appends_jsonl_and_saves_stamp(tmp_path):
    paths = _paths(tmp_path)
    client = FakeKaggleClient(rows=_rows())
    res = check_and_snapshot(paths, client, now=NOW)
    assert res == "ok(4 rows)"
    lines = snapshot_path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    assert json.loads(lines[0])["ref"] == "900"
    assert load_stamp(stamp_path(tmp_path)) == NOW.isoformat()


def test_virgin_directory_parent_not_precreated(tmp_path):
    # The tmp_path lesson: point root one level BELOW tmp_path so that
    # experiments/factory (and its parents) genuinely do not exist yet.
    virgin_root = tmp_path / "never" / "created"
    res = check_and_snapshot(_paths(virgin_root),
                             FakeKaggleClient(rows=_rows()), now=NOW)
    assert res == "ok(4 rows)"
    assert snapshot_path(virgin_root).exists()
    assert stamp_path(virgin_root).exists()


def test_stamp_gates_second_poll_until_interval_elapses(tmp_path):
    paths = _paths(tmp_path)
    client = FakeKaggleClient(rows=_rows())
    assert check_and_snapshot(paths, client, now=NOW).startswith("ok(")
    soon = NOW + dt.timedelta(seconds=MIN_INTERVAL_S - 1)
    assert check_and_snapshot(paths, client, now=soon) == "not-due"
    later = NOW + dt.timedelta(seconds=MIN_INTERVAL_S)
    assert check_and_snapshot(paths, client, now=later).startswith("ok(")


def test_preseeded_future_stamp_noops(tmp_path):
    # The Task-4 inertness pre-seed: a FUTURE last_ts must yield not-due.
    sp = stamp_path(tmp_path)
    save_stamp(sp, dt.datetime(2026, 8, 20, 0, 0, 0, tzinfo=UTC))
    assert snapshot_due(sp, NOW) is False
    res = check_and_snapshot(_paths(tmp_path),
                             FakeKaggleClient(rows=_rows()), now=NOW)
    assert res == "not-due"
    assert not snapshot_path(tmp_path).exists()


def test_missing_or_corrupt_stamp_is_due(tmp_path):
    sp = stamp_path(tmp_path)
    assert snapshot_due(sp, NOW) is True                      # missing
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text("{not json", encoding="utf-8")
    assert snapshot_due(sp, NOW) is True                      # corrupt


def test_cli_failure_appends_nothing_and_never_raises(tmp_path):
    class DeadClient:
        def list_submissions(self):
            raise RuntimeError("kaggle CLI failed (1): Authentication required")

    logged = []
    res = check_and_snapshot(_paths(tmp_path), DeadClient(), now=NOW,
                             log=logged.append)
    assert res.startswith("error:")
    assert not snapshot_path(tmp_path).exists()   # appended NOTHING
    assert load_stamp(stamp_path(tmp_path)) is None  # stamp untouched -> retry next firing
    assert logged  # failure was logged, not swallowed silently


def test_error_return_is_single_line_and_capped(tmp_path):
    """F1: a raised exception whose message embeds a multi-line traceback
    (the real 2026-08-14 10:17 HST incident: an SSL error's str() carried an
    8-line urllib3 traceback) must not leak newlines into the returned
    string -- watch.log appends `f"snapshot: {res}"` verbatim as one line."""
    multiline_msg = (
        "kaggle CLI failed (1): Traceback (most recent call last):\n"
        + "\n".join(f"  File \"line_{i}.py\", line {i}, in frame_{i}"
                     for i in range(40))
        + "\nRuntimeError: boom"
    )

    class DeadClient:
        def list_submissions(self):
            raise RuntimeError(multiline_msg)

    res = check_and_snapshot(_paths(tmp_path), DeadClient(), now=NOW,
                             log=lambda *_: None)
    assert res.startswith("error:")
    assert "\n" not in res            # single line, even though exc had 40+ lines
    assert len(res) <= 400            # capped, per episodes.py:268 precedent
    assert not snapshot_path(tmp_path).exists()
    assert load_stamp(stamp_path(tmp_path)) is None


def test_load_stamp_self_heals_on_valid_json_non_dict(tmp_path):
    """F5: valid JSON that isn't a dict ([1, 2] or "x") must not raise
    AttributeError out of .get() -- that would escape snapshot_due and
    permanently disable the poll step. Treated the same as a missing stamp:
    load_stamp -> None, snapshot_due -> True (poll now)."""
    sp = stamp_path(tmp_path)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text("[1, 2]", encoding="utf-8")
    assert load_stamp(sp) is None
    assert snapshot_due(sp, NOW) is True

    sp.write_text('"just a string"', encoding="utf-8")
    assert load_stamp(sp) is None
    assert snapshot_due(sp, NOW) is True

    # end to end: check_and_snapshot must self-heal, not crash
    res = check_and_snapshot(_paths(tmp_path), FakeKaggleClient(rows=_rows()),
                             now=NOW)
    assert res.startswith("ok(")


def test_non_string_last_ts_self_heals_to_due(tmp_path):
    """Post-approval hardening: a stamp shaped {"last_ts": 12345} (valid
    dict, but last_ts is an int, not a str) makes fromisoformat raise
    TypeError, not ValueError -- `TypeError('fromisoformat: argument must
    be str')`. The pre-fix `except ValueError` let that escape snapshot_due
    and wedge the step in a loud error loop. Must self-heal to due, same as
    the other corrupt-stamp shapes above."""
    sp = stamp_path(tmp_path)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps({"last_ts": 12345}), encoding="utf-8")
    assert snapshot_due(sp, NOW) is True

    # end to end: check_and_snapshot must self-heal, not raise
    res = check_and_snapshot(_paths(tmp_path), FakeKaggleClient(rows=_rows()),
                             now=NOW)
    assert res.startswith("ok(")


def test_empty_listing_is_treated_as_failure_not_saved(tmp_path):
    """F8: an empty parsed listing (CLI exit 0, 0 rows) must be reported as
    a failure and must NOT save the stamp -- this account always has
    submissions, so 0 rows means the listing broke; saving the stamp on
    `ok(0 rows)` would silently burn the 4h poll slot recording nothing."""
    paths = _paths(tmp_path)
    res = check_and_snapshot(paths, FakeKaggleClient(rows=[]), now=NOW)
    assert res == "error:empty-listing"
    assert not snapshot_path(tmp_path).exists()
    assert load_stamp(stamp_path(tmp_path)) is None  # stamp untouched -> retry next firing
