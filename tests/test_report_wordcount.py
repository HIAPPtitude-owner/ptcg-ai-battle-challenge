import subprocess
import sys
from pathlib import Path

from scripts.report_wordcount import count_words, main

FIXTURE = Path(__file__).parent / "fixtures" / "report_wordcount_sample.md"


def test_fixture_counts_twentynine_words():
    # Hand-derived in the plan: 4+7+2+0+3+0+4+3+6 = 29
    assert count_words(FIXTURE.read_text(encoding="utf-8")) == 29


def test_html_comments_and_delimiter_rows_are_not_counted():
    assert count_words("<!-- ignore me entirely -->") == 0
    assert count_words("| --- | :--- | ---: |") == 0


def test_link_url_is_dropped_but_link_text_counts():
    # See / the / ladder / log / now. = 5 (the URL contributes nothing)
    assert count_words("See [the ladder log](https://example.com/x) now.") == 5


def test_over_hard_limit_fails_with_exit_one(tmp_path):
    big = tmp_path / "big.md"
    big.write_text("word " * 2001, encoding="utf-8")
    assert main([str(big)]) == 1


def test_at_target_passes_with_exit_zero(tmp_path):
    ok = tmp_path / "ok.md"
    ok.write_text("word " * 1900, encoding="utf-8")
    assert main([str(ok)]) == 0


def test_between_target_and_limit_warns_but_exits_zero(tmp_path):
    mid = tmp_path / "mid.md"
    mid.write_text("word " * 1950, encoding="utf-8")
    assert main([str(mid)]) == 0


def test_cli_entrypoint_runs():
    proc = subprocess.run(
        [sys.executable, "scripts/report_wordcount.py", str(FIXTURE)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "29" in proc.stdout
