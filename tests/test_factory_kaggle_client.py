"""Tests for the Kaggle CLI wrapper (parser + fake client). The CSV fixture
mirrors headers AND values observed from the real CLI in the T7 investigation
step (see the appendix in ANALYSIS-slice7a-asymmetric-test.md): the real
`competitions submissions --csv` output has extra `ref`/`privateScore`
columns (harmless, ignored) and status values wrapped as
`SubmissionStatus.COMPLETE` / `SubmissionStatus.ERROR` rather than the bare
enum name — parse_submissions_csv normalizes that prefix away. Missing scores
on real ERROR rows are an empty string, not the literal "None"; both forms
are tolerated."""
from __future__ import annotations

from pathlib import Path

import pytest

from ptcg.factory.kaggle_client import (FakeKaggleClient, KaggleClient,
                                        SubmissionRow, parse_submissions_csv)

# Real-shape fixture: header order/extra columns and SubmissionStatus.* prefix
# as observed 2026-07-11 via `uvx kaggle competitions submissions -c
# pokemon-tcg-ai-battle --csv` (see appendix). Third row's empty publicScore
# mirrors the real ERROR-status row's blank field (not literal "None").
CSV = """ref,fileName,date,description,status,publicScore,privateScore
54517391,submission.tar.gz,2026-07-12 04:00:00,lucario-heuristic v1.0 | deck=mega-lucario-fighting | agent=heuristic | abcd1234 | factory,SubmissionStatus.COMPLETE,1543.2,
54500683,submission.tar.gz,2026-07-11 04:00:00,older thing,SubmissionStatus.PENDING,,
54474833,submission.tar.gz,2026-07-08 22:53:43,Prototype Testing,SubmissionStatus.ERROR,,
"""


def test_parse_submissions_csv_rows_and_scores():
    rows = parse_submissions_csv(CSV)
    assert rows[0].public_score == 1543.2
    assert rows[0].status == "COMPLETE"
    assert rows[0].description.startswith("lucario-heuristic v1.0")
    assert rows[1].public_score is None  # empty field -> None
    assert rows[1].status == "PENDING"
    assert rows[2].status == "ERROR"
    assert rows[0].ref == "54517391"  # ref captured (2026-08-14 additive field)


def test_parse_submissions_csv_tolerates_literal_none_string():
    text = ("fileName,date,description,status,publicScore\n"
            "s.tar.gz,2026-07-11 00:00:00,older thing,PENDING,None\n")
    rows = parse_submissions_csv(text)
    assert rows[0].public_score is None
    assert rows[0].status == "PENDING"  # no SubmissionStatus. prefix -> passthrough
    assert rows[0].ref == ""  # absent ref column -> default


def test_kaggle_client_builds_uvx_commands_and_raises_on_failure(tmp_path):
    calls = []

    class Proc:
        def __init__(self, code, out):
            self.returncode, self.stdout, self.stderr = code, out, "boom"

    def runner(cmd, **kw):
        calls.append(cmd)
        return Proc(0, CSV)

    client = KaggleClient(runner=runner)
    rows = client.list_submissions()
    assert rows[0].public_score == 1543.2
    assert calls[0][:4] == ["uvx", "kaggle", "competitions", "submissions"]
    client.submit(tmp_path / "submission.tar.gz", "desc here")
    assert calls[1][:4] == ["uvx", "kaggle", "competitions", "submit"]
    assert "-m" in calls[1] and "desc here" in calls[1]

    failing = KaggleClient(runner=lambda cmd, **kw: Proc(1, ""))
    with pytest.raises(RuntimeError, match="kaggle CLI failed"):
        failing.list_submissions()


def test_kaggle_client_falls_back_to_stdout_when_stderr_empty(tmp_path):
    """Regression test: the real kaggle CLI writes user-facing errors (e.g.
    'Authentication required to call the Kaggle API.') to STDOUT and exits
    nonzero with EMPTY stderr. Before the fix, `_cli` only read stderr, so
    production failures logged as 'kaggle CLI failed (1): ' with no detail
    (see experiments/factory/digests/cycle-20260720-093001.md)."""

    class Proc:
        def __init__(self, code, out, err):
            self.returncode, self.stdout, self.stderr = code, out, err

    failing = KaggleClient(runner=lambda cmd, **kw: Proc(
        1, "Authentication required to call the Kaggle API.", ""))
    with pytest.raises(RuntimeError) as exc_info:
        failing.list_submissions()
    assert "Authentication required to call the Kaggle API." in str(exc_info.value)


def test_kaggle_client_prefers_stderr_over_stdout_when_both_present(tmp_path):
    """When stderr IS present, it must win over stdout in the error message."""

    class Proc:
        def __init__(self, code, out, err):
            self.returncode, self.stdout, self.stderr = code, out, err

    failing = KaggleClient(runner=lambda cmd, **kw: Proc(
        1, "stdout noise", "real stderr detail"))
    with pytest.raises(RuntimeError) as exc_info:
        failing.list_submissions()
    assert "real stderr detail" in str(exc_info.value)
    assert "stdout noise" not in str(exc_info.value)


def test_fake_client_records_submissions():
    fake = FakeKaggleClient()
    fake.submit(Path("x.tar.gz"), "name v1.0 | factory")
    assert fake.submitted == [(Path("x.tar.gz"), "name v1.0 | factory")]
    assert fake.list_submissions()[0].status == "PENDING"
    assert isinstance(fake.list_submissions()[0], SubmissionRow)
