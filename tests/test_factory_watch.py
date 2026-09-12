"""`watch.py` per-firing helpers: single-instance lock, BelowNormal throttle,
and the append-only watch log.

`watch_once()`'s own behavior moved to `tests/test_factory_watch_cutover.py`
at the T20 cutover (the watch loop was narrowed to submission-scheduler +
surviving harvester); this file now covers ONLY the small stdlib helpers in
`src/ptcg/factory/watch.py`, which the cutover left untouched."""
import sys

import pytest

from ptcg.factory.watch import append_watch_log, instance_lock, throttle_below_normal


def test_instance_lock_excludes_second_holder(tmp_path):
    lock = tmp_path / "locks" / "watch.lock"  # virgin parent
    with instance_lock(lock):
        with pytest.raises(TimeoutError):
            with instance_lock(lock):
                pass
    with instance_lock(lock):  # released -> reacquirable
        pass


def test_throttle_returns_bool_and_never_raises():
    ok = throttle_below_normal(log=lambda m: None)
    assert ok is (sys.platform == "win32")


def test_append_watch_log_virgin_dir_and_utf8(tmp_path):
    log = tmp_path / "logs" / "never" / "watch.log"
    append_watch_log(log, "refill: enqueued 3 — matrix")  # em dash: utf-8 check
    append_watch_log(log, "second line")
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and lines[0].endswith("matrix")
