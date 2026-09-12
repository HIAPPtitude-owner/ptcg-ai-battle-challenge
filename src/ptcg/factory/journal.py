"""Append-only methodology journal writer (spec S6: docs/writeup-notes.md)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

JOURNAL_HEADER = (
    "# Writeup Notes - methodology journal (append-only)\n\n"
    "Factory scripts log decisions with reasons; the weekly review adds strategic\n"
    "prose. The Strategy report is an editing job over this file plus\n"
    "experiments/LADDER.md and experiments/EXPERIMENTS.md.\n"
)


def append_journal(path: Path, title: str, body: str,
                   now: dt.datetime | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = (now or dt.datetime.now()).isoformat(timespec="minutes")
    if not path.exists():
        path.write_text(JOURNAL_HEADER, encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"\n## {stamp} - {title}\n\n{body}\n")
