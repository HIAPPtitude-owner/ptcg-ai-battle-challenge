"""Source-level guard: every read_text/write_text call in the experiment-log
CLIs must pin encoding="utf-8".

Both scripts.run_tournament and scripts.run_arena append rows containing
non-ASCII glyphs (e.g. the mulligan-flag warning "⚠") to experiments/EXPERIMENTS.md
via Path.read_text()/Path.write_text(). Without an explicit encoding="utf-8",
Python falls back to the platform default codec — cp1252 on Windows, which
cannot encode "⚠" and raises UnicodeEncodeError. Because write_text() opens
its target in truncating write mode BEFORE the encode is attempted, a crash
here doesn't just fail loudly: it truncates EXPERIMENTS.md to zero bytes
first, destroying the whole experiment log (see .superpowers/sdd/task-10-report.md
for the incident this test was written to prevent recurrence of).

This is a source-level regression pin rather than a runtime repro because the
failure only reproduces under a cp1252 default locale (Windows); asserting the
fixed source pattern is the durable guard.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Matches `read_text(` or `write_text(` optionally followed immediately by a
# closing paren (no-arg call) or arguments; captures up to the matching close
# paren is overkill here — instead we just capture the call site and confirm
# the token `encoding=` appears before the next top-level ")" that closes it.
CALL_RE = re.compile(r"(read_text|write_text)\(")


def _call_sites_missing_encoding(source: str) -> list[str]:
    """Return a list of read_text(/write_text( call snippets that do not pass
    `encoding=` in THEIR OWN argument list.

    Uses a lightweight bracket-matching scan rather than a single regex, since
    calls may be wrapped/multi-line (as in run_tournament.py). Crucially, the
    `encoding=` check only considers text at bracket depth 1 relative to the
    matched call's opening paren — characters inside nested parenthesized
    spans are excluded. Otherwise `write_text(read_text(encoding="utf-8") + x)`
    would pass on the strength of the NESTED call's kwarg while write_text
    itself still used the platform-default codec — exactly the shape that
    reproduces the original truncate-then-crash data loss.
    """
    missing = []
    for match in CALL_RE.finditer(source):
        start = match.end()  # just past the opening "("
        depth = 1
        i = start
        own_args_chars: list[str] = []  # text at depth 1 only: this call's own args
        while i < len(source) and depth > 0:
            ch = source[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif depth == 1:
                own_args_chars.append(ch)
            i += 1
        own_args = "".join(own_args_chars)
        if "encoding=" not in own_args:
            missing.append(source[match.start() : i])
    return missing


def test_matcher_flags_encoding_only_on_nested_call():
    """Regression for the matcher itself: a partial revert where only the
    nested read_text carries encoding= must be FLAGGED (write_text's own
    kwarg is missing), and the fully-fixed shape must PASS."""
    bad = 'log.write_text(log.read_text(encoding="utf-8") + standings)\n'
    flagged = _call_sites_missing_encoding(bad)
    assert len(flagged) == 1 and flagged[0].startswith("write_text("), (
        f"matcher failed to flag write_text missing its own encoding=: {flagged!r}"
    )

    good = (
        "log.write_text(\n"
        '    log.read_text(encoding="utf-8") + "\\n" + standings + "\\n",'
        ' encoding="utf-8"\n'
        ")\n"
    )
    assert _call_sites_missing_encoding(good) == []


def test_run_tournament_experiments_append_has_encoding():
    source = (ROOT / "scripts" / "run_tournament.py").read_text(encoding="utf-8")
    assert "read_text(" in source and "write_text(" in source, (
        "expected read_text/write_text calls in run_tournament.py — "
        "test target may have moved"
    )
    missing = _call_sites_missing_encoding(source)
    assert missing == [], (
        f"scripts/run_tournament.py has read_text()/write_text() call(s) "
        f"missing encoding=\"utf-8\": {missing}"
    )


def test_run_arena_experiments_append_has_encoding():
    source = (ROOT / "scripts" / "run_arena.py").read_text(encoding="utf-8")
    assert "read_text(" in source and "write_text(" in source, (
        "expected read_text/write_text calls in run_arena.py — "
        "test target may have moved"
    )
    missing = _call_sites_missing_encoding(source)
    assert missing == [], (
        f"scripts/run_arena.py has read_text()/write_text() call(s) "
        f"missing encoding=\"utf-8\": {missing}"
    )


def test_run_arena_sidecar_write_has_encoding():
    source = (ROOT / "scripts" / "run_arena.py").read_text(encoding="utf-8")
    assert "write_sidecar" in source, "write_sidecar helper missing from run_arena.py"
    # the existing _call_sites_missing_encoding scan already covers ALL write_text/
    # read_text in the file; this test just pins that write_sidecar exists to guard it.


def test_factory_files_pin_utf8_encoding():
    """Spec S9: every factory text write pins encoding="utf-8". Glob-based so
    files added later in the slice are covered without editing this test."""
    targets = sorted((ROOT / "src" / "ptcg" / "factory").glob("*.py"))
    targets += sorted(ROOT.glob("scripts/factory_*.py"))
    targets += sorted(ROOT.glob("scripts/seed_candidates.py"))
    targets += sorted(ROOT.glob("scripts/asym_report.py"))
    assert targets, "factory sources not found - test target may have moved"
    problems = {}
    for target in targets:
        missing = _call_sites_missing_encoding(target.read_text(encoding="utf-8"))
        if missing:
            problems[str(target)] = missing
    assert problems == {}, f"factory file(s) missing encoding= kwarg: {problems}"
