"""Per-firing helpers for the continuous watch loop (spec design 1)."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from ptcg.factory.candidates import ledger_lock

WATCH_LOCK_STALE_S = 8 * 3600.0  # > longest legitimate holder (a training run)


def instance_lock(lock_path: Path):
    """Single-instance guard for watch firings: fail FAST if another firing is
    live (Task Scheduler will fire again in 15 min), break locks older than 8h
    (a crashed holder must never wedge the loop)."""
    return ledger_lock(Path(lock_path), timeout_s=0.5,
                       stale_after_s=WATCH_LOCK_STALE_S)


def throttle_below_normal(log=print) -> bool:
    """BelowNormal priority class so the machine stays usable (spec: always-on
    but throttled). Child processes inherit the class on Windows."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        # Explicit argtypes/restype are required on 64-bit Windows: without
        # them ctypes defaults to c_int (32-bit) for both the HANDLE return
        # of GetCurrentProcess and the HANDLE argument of SetPriorityClass,
        # truncating the pseudo-handle and making SetPriorityClass fail with
        # ERROR_INVALID_HANDLE even though the call is otherwise legitimate.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.SetPriorityClass.restype = wintypes.BOOL
        kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
        handle = kernel32.GetCurrentProcess()
        ok = bool(kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS))
        if not ok:
            log("throttle: SetPriorityClass failed (continuing unthrottled)")
        return ok
    except Exception as exc:
        log(f"throttle failed (continuing unthrottled): {exc!r}")
        return False


def append_watch_log(log_path: Path, msg: str,
                     now: dt.datetime | None = None) -> None:
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = (now or dt.datetime.now()).isoformat(timespec="minutes")
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {msg}\n")
