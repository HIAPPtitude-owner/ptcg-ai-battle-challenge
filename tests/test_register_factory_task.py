"""Tests for scripts/register_factory_task.ps1.

These tests only ever invoke the script with -DryRun, which prints the
task name, action, trigger, and settings it WOULD register/unregister
without calling Register-ScheduledTask / Unregister-ScheduledTask. This
exercises command construction without ever touching the real Windows
Task Scheduler -- real registration is deferred to T13 (Brad's machine,
final factory state), and must never happen as a side effect of running
the test suite.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "register_factory_task.ps1"

POWERSHELL = shutil.which("powershell") or shutil.which("powershell.exe")

pytestmark = pytest.mark.skipif(
    POWERSHELL is None, reason="powershell.exe not available on PATH"
)


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(SCRIPT), *args],
        capture_output=True, text=True, cwd=ROOT, timeout=30,
    )


def test_script_file_exists():
    assert SCRIPT.exists()


def test_dry_run_register_default_time():
    result = _run("-DryRun")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "ptcg-factory-continuous" in out
    assert "ptcg-factory-matrix" in out
    assert "ptcg-factory-trainer" in out
    assert "factory_watch_once.py" in out
    assert "factory_matrix_worker.py" in out
    assert "factory_trainer_worker.py" in out
    assert "15 minute" in out
    assert "AtStartup" in out
    assert "8:00:00" in out
    assert "IgnoreNew" in out
    assert "ptcg-factory-nightly" in out  # retirement of the old task is named


def test_dry_run_register_workers_no_execution_time_limit():
    """The two workers run forever (no ExecutionTimeLimit), unlike the
    8h-limited watch task -- this must be visible in the dry-run plan for
    both ptcg-factory-matrix and ptcg-factory-trainer."""
    result = _run("-DryRun")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert out.count("no execution time limit") == 2


def test_dry_run_register_workers_watchdog_triggers():
    """Workers hold single-instance locks, so their AtStartup + 15-minute
    repetition trigger is a watchdog -- a firing while alive exits busy
    instantly, mirroring the existing watch-task pattern."""
    result = _run("-DryRun")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert out.count("(watchdog)") == 2
    assert out.count("AtStartup") >= 3  # main task + 2 workers


def test_dry_run_register_custom_interval():
    result = _run("-IntervalMinutes", "30", "-DryRun")
    assert result.returncode == 0, result.stderr
    assert "30 minute" in result.stdout


def test_dry_run_unregister():
    result = _run("-Unregister", "-DryRun")
    assert result.returncode == 0, result.stderr
    out = result.stdout.lower()
    assert "ptcg-factory-continuous" in result.stdout
    assert "ptcg-factory-matrix" in result.stdout
    assert "ptcg-factory-trainer" in result.stdout
    assert "ptcg-factory-nightly" in result.stdout
    assert "unregister" in out


def test_dry_run_never_calls_real_registration_cmdlets():
    """Every non-blank line of -DryRun output is prefixed DRYRUN -- a stray
    unprefixed 'registered'/'unregistered' line would indicate the script
    fell through to the real Register-ScheduledTask/Unregister-ScheduledTask
    path despite -DryRun being passed."""
    result = _run("-DryRun")
    for line in result.stdout.splitlines():
        if line.strip():
            assert line.startswith("DRYRUN"), f"unexpected non-dry-run output: {line!r}"


def test_dry_run_unregister_never_calls_real_registration_cmdlets():
    result = _run("-Unregister", "-DryRun")
    for line in result.stdout.splitlines():
        if line.strip():
            assert line.startswith("DRYRUN"), f"unexpected non-dry-run output: {line!r}"


def test_script_syntax_parses():
    """Get-Command -Syntax loads and parses the script without executing its
    body -- catches a malformed param block or syntax error independent of
    -DryRun actually being wired correctly."""
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command",
         f"Get-Command -Syntax '{SCRIPT}'"],
        capture_output=True, text=True, cwd=ROOT, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "register_factory_task.ps1" in result.stdout
