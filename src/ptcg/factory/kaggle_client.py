"""Thin wrapper over the Kaggle CLI, run via uvx (spec S1/S5).

Auth is cached in ~/.kaggle (one-time `uvx kaggle auth login`). Column names in
parse_submissions_csv were verified against the real CLI output in the T7
investigation step (see the appendix in ANALYSIS-slice7a-asymmetric-test.md):
the real `competitions submissions --csv` output is
`ref,fileName,date,description,status,publicScore,privateScore` — the extra
`privateScore` column is ignored; `ref` is captured (2026-08-14) for the
ladder snapshot logger. Both camelCase and snake_case variants of the fields
we do use are tolerated. Real status values are wrapped as
`SubmissionStatus.COMPLETE` (Python enum repr leaking through the CLI's CSV
writer) rather than the bare name; `_parse_status` strips that prefix so
callers always see plain "COMPLETE"/"PENDING"/"ERROR". Missing scores appear
as an empty field on real ERROR rows (not the literal string "None"); both
forms are tolerated by `_parse_score`.
"""
from __future__ import annotations

import csv
import io
import subprocess
from dataclasses import dataclass
from pathlib import Path

COMPETITION = "pokemon-tcg-ai-battle"

_STATUS_ENUM_PREFIX = "SubmissionStatus."


@dataclass
class SubmissionRow:
    file_name: str
    date: str
    description: str
    status: str
    public_score: float | None
    #: Kaggle's submission ref/id column (additive 2026-08-14, freeze-pair
    #: snapshot logger). Previously dropped by the parser; defaulted so all
    #: pre-existing positional constructions remain valid.
    ref: str = ""


def _parse_score(raw: str) -> float | None:
    raw = (raw or "").strip()
    if raw in ("", "None", "null", "-"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_status(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith(_STATUS_ENUM_PREFIX):
        return raw[len(_STATUS_ENUM_PREFIX):]
    return raw


def parse_submissions_csv(text: str) -> list[SubmissionRow]:
    reader = csv.DictReader(io.StringIO(text.strip()))
    rows: list[SubmissionRow] = []
    for r in reader:
        def get(*keys: str) -> str:
            for k in keys:
                if k in r and r[k] is not None:
                    return r[k]
            return ""
        rows.append(SubmissionRow(
            file_name=get("fileName", "file_name", "fileNameNullable"),
            date=get("date"),
            description=get("description", "descriptionNullable"),
            status=_parse_status(get("status")),
            public_score=_parse_score(get("publicScore", "public_score",
                                          "publicScoreNullable")),
            ref=get("ref"),
        ))
    return rows


class KaggleClient:
    def __init__(self, competition: str = COMPETITION, runner=subprocess.run) -> None:
        self.competition = competition
        self._run = runner

    def _cli(self, *args: str) -> str:
        cmd = ["uvx", "kaggle", *args]
        proc = self._run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            # The kaggle CLI writes user-facing errors (e.g. "Authentication
            # required to call the Kaggle API.") to STDOUT with an EMPTY
            # stderr; fall back to stdout when stderr has nothing to say.
            detail = (proc.stderr.strip() or proc.stdout.strip())[:500]
            raise RuntimeError(f"kaggle CLI failed ({proc.returncode}): {detail}")
        return proc.stdout

    def list_submissions(self) -> list[SubmissionRow]:
        out = self._cli("competitions", "submissions", "-c", self.competition, "--csv")
        return parse_submissions_csv(out)

    def submit(self, bundle: Path, description: str) -> None:
        self._cli("competitions", "submit", "-c", self.competition,
                  "-f", str(bundle), "-m", description)

    # --- dataset ops (Task 10 episode harvester) ---------------------------
    # The episode-replay corpus is published as Kaggle datasets: a tiny index
    # dataset (manifest.csv listing each day) plus one ~742MB-compressed
    # per-day dataset of `<episode_id>.json` replay files. Flag spellings
    # verified live against `uvx kaggle datasets {files,download} --help`
    # during the Task-10 schema probe (see experiments/factory/episodes/
    # schema-probe.txt): `datasets files` uses `--csv`; `datasets download`
    # uses `-d <ref>`, `-p <dest>`, optional `-f <filename>`, and `--unzip`.

    def dataset_files(self, dataset: str) -> str:
        """CSV listing of a dataset's files (name,size,creationDate)."""
        return self._cli("datasets", "files", "-d", dataset, "--csv")

    def dataset_download(self, dataset: str, dest: Path,
                         filename: str | None = None, *, unzip: bool = True) -> None:
        """Download a dataset (or one file of it) into `dest`.

        `unzip=True` extracts and deletes the zip (used for the tiny index
        manifest); `unzip=False` keeps the raw `.zip` (used for a full day so
        the ~21GB *uncompressed* corpus is never all written to disk at once --
        episodes.py streams episode files straight out of the zip instead).
        """
        args = ["datasets", "download", "-d", dataset, "-p", str(dest)]
        if filename is not None:
            args += ["-f", filename]
        if unzip:
            args.append("--unzip")
        self._cli(*args)


def check_auth(client) -> str | None:
    """Cheap read-only auth probe (pre-submit auth guard).

    Reuses the same `list_submissions()` capability the harvester already
    calls, so it works identically for OAuth or static-API-key auth -- it
    never parses/inspects credential files, only the call outcome matters.
    Returns None when auth is alive, or a truncated error detail string when
    the call raised (expired OAuth token, network down, etc). Any failure of
    this cheap read is treated as "cannot safely attempt a real submission
    right now" by the caller.
    """
    try:
        client.list_submissions()
    except Exception as exc:
        return str(exc)[:500]
    return None


class FakeKaggleClient:
    """Duck-typed test double for KaggleClient (spec S9 dry-run mode)."""

    def __init__(self, rows: list[SubmissionRow] | None = None, *,
                 files_by_dataset: dict[str, dict[str, object]] | None = None,
                 dataset_files_csv: dict[str, str] | None = None) -> None:
        self.rows = list(rows or [])
        self.submitted: list[tuple[Path, str]] = []
        # dataset ref -> {filename: str|bytes content written on download}
        self.files_by_dataset = files_by_dataset or {}
        self.dataset_files_csv = dataset_files_csv or {}
        self.downloads: list[tuple] = []

    def list_submissions(self) -> list[SubmissionRow]:
        return list(self.rows)

    def submit(self, bundle: Path, description: str) -> None:
        self.submitted.append((Path(bundle), description))
        self.rows.insert(0, SubmissionRow(Path(bundle).name, "2026-01-01 00:00:00",
                                          description, "PENDING", None))

    def dataset_files(self, dataset: str) -> str:
        return self.dataset_files_csv.get(dataset, "")

    def dataset_download(self, dataset: str, dest: Path,
                         filename: str | None = None, *, unzip: bool = True) -> None:
        dest = Path(dest)
        self.downloads.append((dataset, dest, filename, unzip))
        dest.mkdir(parents=True, exist_ok=True)
        for name, content in self.files_by_dataset.get(dataset, {}).items():
            if filename is not None and name != filename:
                continue
            target = dest / name
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(str(content), encoding="utf-8")
