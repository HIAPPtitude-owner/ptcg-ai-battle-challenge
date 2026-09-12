"""Conservative word-count gate for the Strategy-category Kaggle Writeup.

Kaggle's own counter is unknown, so this counter approximates rendered
prose: headings, list text and table cell content all count, while markdown
table borders/separators and HTML comments do not. It therefore reads lower
than a raw `wc -w` over the source file (109 tokens lower as of 2026-09-11;
the gap is markdown punctuation - table pipes, delimiter rows and standalone
em dashes), and its agreement with Kaggle's counter is not guaranteed in
either direction. Counting rule documented in
docs/superpowers/plans/2026-08-18-strategy-report.md, Task 5.
"""
from __future__ import annotations

import argparse
import re
import sys

HARD_LIMIT = 2000
DRAFT_TARGET = 1900

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_DELIM_ROW = re.compile(r"^[|\-:\s]+$")
_LEADING_MARKER = re.compile(r"^\s*(?:>+\s*)?(?:#{1,6}\s+|[-*+]\s+|\d+\.\s+)")
_PUNCT = re.compile(r"[*_`]")
_ALNUM = re.compile(r"[0-9A-Za-z]")


def count_words(text: str) -> int:
    text = _COMMENT.sub(" ", text)
    total = 0
    for raw in text.splitlines():
        if "|" in raw and _DELIM_ROW.match(raw):
            continue
        line = _IMAGE.sub(r"\1", raw)
        line = _LINK.sub(r"\1", line)
        line = line.replace("|", " ")
        line = _LEADING_MARKER.sub("", line)
        line = _PUNCT.sub("", line)
        total += sum(1 for tok in line.split() if _ALNUM.search(tok))
    return total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Word-count gate for the Writeup draft.")
    ap.add_argument("path", help="markdown file to count")
    args = ap.parse_args(argv)
    with open(args.path, encoding="utf-8") as fh:
        count = count_words(fh.read())
    if count > HARD_LIMIT:
        verdict, code = "FAIL", 1
    elif count > DRAFT_TARGET:
        verdict, code = "WARN", 0
    else:
        verdict, code = "PASS", 0
    print(f"{count} words (target {DRAFT_TARGET}, hard limit {HARD_LIMIT}): {verdict}")
    return code


if __name__ == "__main__":
    sys.exit(main())
