"""Tests for scripts/register_tournament_tasks.ps1 (tournament T20).

These tests only ever invoke the script with -DryRun, which prints the tasks
it WOULD register/retire without calling Register-ScheduledTask /
Unregister-ScheduledTask. This exercises command construction without ever
touching the real Windows Task Scheduler -- real registration is deferred to
T21 (post-merge, elevated, Brad's/orchestrator's machine) and must never
happen as a side effect of the test suite. The real (non-DryRun) branch's
loud-fail discipline (-ErrorAction Stop + exit 1, register-before-retire) is
verified STATICALLY against the script source, since its real cmdlets cannot
be exercised in CI.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "register_tournament_tasks.ps1"

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


def test_dry_run_registers_three_new_tasks_and_entrypoints():
    result = _run("-DryRun")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for name in ("ptcg-factory-runner", "ptcg-factory-scheduler", "ptcg-factory-ui"):
        assert f"register task={name}" in out
    assert "factory_runner_pool.py" in out
    assert "factory_tournament_scheduler.py" in out
    assert "factory_ui.py" in out


def test_dry_run_register_before_retire_ordering():
    """Register-before-retire (go-live incident 72bc0f8): every new-task
    register line must appear BEFORE the first retire line, so a mid-run
    failure can never leave the machine with zero factory tasks."""
    out = _run("-DryRun").stdout
    lines = out.splitlines()
    first_retire = next(i for i, ln in enumerate(lines) if "retire task=" in ln)
    for name in ("ptcg-factory-runner", "ptcg-factory-scheduler", "ptcg-factory-ui"):
        reg = next(i for i, ln in enumerate(lines) if f"register task={name}" in ln)
        assert reg < first_retire, f"{name} registered AFTER a retire line"


def test_dry_run_retires_matrix_and_trainer():
    out = _run("-DryRun").stdout
    assert "retire task=ptcg-factory-matrix" in out
    assert "retire task=ptcg-factory-trainer" in out


def test_dry_run_leaves_continuous_untouched():
    """ptcg-factory-continuous (narrowed watch loop) must NOT be registered or
    retired by this script -- it stays as-is and picks up the narrowed code on
    its next ~15-min firing."""
    out = _run("-DryRun").stdout
    assert "task=ptcg-factory-continuous" not in out


def test_dry_run_ui_atstartup_watchdog_ignorenew():
    """PD-B: the UI is the sole surface, registered AtStartup + 15-min watchdog
    + IgnoreNew (same worker-watchdog shape as the retired matrix/trainer)."""
    out = _run("-DryRun").stdout
    # all three new tasks are long-lived workers -> 3x each of these markers
    assert out.count("(watchdog)") == 3
    assert out.count("AtStartup") == 3
    assert out.count("IgnoreNew") == 3
    assert out.count("no execution time limit") == 3


def test_dry_run_faucet_pipeline_target_default_and_seed():
    """R1 faucet: the scheduler action carries --pipeline-target 4 (default,
    faucet ON) AND --seed (founds the census on first run)."""
    out = _run("-DryRun").stdout
    assert "--pipeline-target 4" in out
    assert "--seed" in out
    assert "pipeline-target=4" in out


def test_dry_run_pipeline_target_flag_flows_through_for_r2():
    """-PipelineTarget 0 flips to the R2 shape (scheduler faucet OFF)."""
    out = _run("-PipelineTarget", "0", "-DryRun").stdout
    assert "--pipeline-target 0" in out


def test_dry_run_never_calls_real_registration_cmdlets():
    result = _run("-DryRun")
    for line in result.stdout.splitlines():
        if line.strip():
            assert line.startswith("DRYRUN"), f"unexpected non-dry-run output: {line!r}"


def test_dry_run_unregister_names_the_three_new_tasks():
    result = _run("-Unregister", "-DryRun")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for name in ("ptcg-factory-runner", "ptcg-factory-scheduler", "ptcg-factory-ui"):
        assert name in out
    assert "unregister" in out.lower()


def test_dry_run_unregister_never_calls_real_cmdlets():
    result = _run("-Unregister", "-DryRun")
    for line in result.stdout.splitlines():
        if line.strip():
            assert line.startswith("DRYRUN"), f"unexpected non-dry-run output: {line!r}"


def test_script_syntax_parses():
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", f"Get-Command -Syntax '{SCRIPT}'"],
        capture_output=True, text=True, cwd=ROOT, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "register_tournament_tasks.ps1" in result.stdout


def test_real_branch_loud_fail_and_register_before_retire_static():
    """The real (non-DryRun) registration branch cannot be exercised in CI, so
    its loud-fail discipline is checked against the source: every real
    Register-ScheduledTask uses -ErrorAction Stop, a registration failure
    exits 1 (no exit-code laundering -- 225686c/72bc0f8), and the Register
    loop precedes the Unregister-of-retired-tasks loop."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "-ErrorAction Stop" in src
    assert "exit 1" in src
    assert "Register-ScheduledTask" in src
    # register-before-retire: the real Register-ScheduledTask call precedes the
    # real retire cmdlet. Anchor on `Unregister-ScheduledTask -TaskName $old`,
    # which is UNIQUE to the retire-retired-tasks loop (the DryRun branch only
    # prints, and the -Unregister rollback block iterates $name, not $old).
    reg = src.index("Register-ScheduledTask -TaskName $t.Name")
    retire = src.index("Unregister-ScheduledTask -TaskName $old")
    assert reg < retire, "retire cmdlet precedes registration -- ordering violated"
