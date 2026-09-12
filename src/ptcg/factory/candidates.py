"""Candidate model + git-tracked JSON queue ledger (spec S2).

A candidate is a durable versioned identity: name + vMAJOR.MINOR mapping to an
exact deck, agent config, net weights file, and provenance, so every ladder
score is forever traceable to reproducible code.
"""
from __future__ import annotations

import contextlib
import json
import os
import time
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Callable


class Status(str, Enum):
    QUEUED = "queued"
    EVALUATING = "evaluating"
    EVALUATED = "evaluated"
    BELOW_INCUMBENT = "evaluated-below-incumbent"
    SUBMITTED = "submitted"
    SCORED = "scored"
    RETIRED = "retired"


def parse_version(version: str) -> tuple[int, int]:
    if not version.startswith("v"):
        raise ValueError(f"version must look like vMAJOR.MINOR, got {version!r}")
    major_s, _, minor_s = version[1:].partition(".")
    return int(major_s), int(minor_s)


def bump_minor(version: str) -> str:
    major, minor = parse_version(version)
    return f"v{major}.{minor + 1}"


def make_id(name: str, version: str) -> str:
    return f"{name}-{version}"


@dataclass
class Candidate:
    id: str
    name: str
    version: str
    deck: str                 # repo-relative deck csv path
    agent_kind: str           # "heuristic" | "search-net"
    agent_config: dict = field(default_factory=dict)
    provenance: str = "seed"
    priority: float = 0.5
    status: Status = Status.QUEUED
    novel_axis: bool = False  # exploration-exception eligibility (spec S4)
    is_incumbent: bool = False  # manual weekly-review "beat-this bar" pin: when
    # set, gate.incumbent() uses THIS candidate's local_wr as the bar instead of
    # the volatile counted-pair rule. Needed when local eval and ladder disagree
    # (a high-local_wr line converges LOW on the ladder) so the auto rule would
    # otherwise key the bar to the wrong line. Defaults False (JSON back-compat:
    # load_ledger defaults missing keys, drops unknown ones).
    local_wr: float | None = None
    local_games: int = 0
    local_breakdown: list | None = None  # structured per-baseline eval results:
    # [{"baseline": str, "wins": int, "games": int}, ...]. None for pre-field
    # ledger entries and for evals whose series_fn carries no breakdown (plain
    # SeriesStats stubs). Same JSON back-compat convention as
    # last_resubmitted_at: load_ledger defaults missing keys, drops unknown ones.
    matrix_rating: float | None = None  # Bradley-Terry rating from tournament.py
    # (compute-saturation matrix eval); None until matrix coverage collected.
    matrix_games: int = 0        # tournament.py MatrixLedger.total_games(id)
    matrix_opponents: int = 0    # len(tournament.py MatrixLedger.opponents_of(id))
    anchor_wr: float | None = None  # win-rate-vs-anchor (rating.py's
    # `coverage.rating`, [0,1] scale, tournament plan T5/T17) -- a DIFFERENT
    # scale from `matrix_rating` (Bradley-Terry, geometric-mean-normalized
    # around 1.0) and from `local_wr` (arena-eval win rate, evaluate.py). Set
    # ONLY by `subscheduler._baseline_candidate`/`_probe_candidate`
    # (tournament-scheduler submission path); never merged into the
    # candidates.json ledger gate.py/evaluate.py/cycle.py operate on, so it
    # never participates in `gate.effective_score`/`gate.incumbent`. Exists so
    # `submit._merit_segment` can label tournament-scheduler submission
    # descriptions honestly ("anchor-wr X.XXX of N games") instead of writing
    # this wr-scale value into the BT-documented `matrix_rating` field.
    anchor_games: int = 0  # anchor-screening `coverage.games_played` at write time
    kaggle_score: float | None = None
    submitted_at: str | None = None      # ISO datetime of most recent upload
    last_resubmitted_at: str | None = None  # ISO datetime of the last champion-
    # protection re-upload (F3): a dedicated trace so the re-submission record
    # never has to clobber authored/eval notes. Defaults None for JSON back-compat
    # (old ledgers load fine; load_ledger drops unknown keys, adds missing ones).
    score_history: list = field(default_factory=list)  # [[iso_ts, score], ...]
    notes: str = ""

    @classmethod
    def create(cls, *, name: str, version: str, deck: str, agent_kind: str,
               **kw) -> "Candidate":
        return cls(id=make_id(name, version), name=name, version=version,
                   deck=deck, agent_kind=agent_kind, **kw)


def next_version(candidates: list[Candidate], name: str) -> str:
    versions = [parse_version(c.version) for c in candidates if c.name == name]
    if not versions:
        return "v0.1"
    major, minor = max(versions)
    return f"v{major}.{minor + 1}"


def load_ledger(path: Path) -> list[Candidate]:
    path = Path(path)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    known = {f.name for f in fields(Candidate)}
    out: list[Candidate] = []
    for row in payload.get("candidates", []):
        row = {k: v for k, v in row.items() if k in known}
        row["status"] = Status(row.get("status", "queued"))
        out.append(Candidate(**row))
    return out


def save_ledger(path: Path, candidates: list[Candidate]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "candidates": [{**asdict(c), "status": c.status.value} for c in candidates],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


LEDGER_LOCK_STALE_S = 120.0  # a lockfile older than this is presumed abandoned


@contextlib.contextmanager
def ledger_lock(path: Path, timeout_s: float = 30.0,
                stale_after_s: float = LEDGER_LOCK_STALE_S,
                poll_s: float = 0.05, log: Callable[[str], None] | None = None):
    """Cross-process mutual exclusion around a ledger read-modify-write sequence.

    The training daemon (daemon.py) and factory cycles (cycle.py) both do
    load_ledger -> mutate -> save_ledger on the same candidates.json
    concurrently; the atomic temp+os.replace in save_ledger protects each
    individual write from torn/corrupted content but not this
    read-modify-write race, where one process's save can silently clobber
    another's already-written update (lost update).

    Implemented as a sidecar `<path>.lock` file created with
    O_CREAT | O_EXCL, which is atomic across processes on the same
    filesystem and works identically on Windows and POSIX (stdlib-only).
    Only one process can hold the lock at a time; others retry until they
    acquire it or `timeout_s` elapses. A lock file older than `stale_after_s`
    is presumed abandoned by a crashed holder and is broken (unlinked) so a
    dead process can never wedge the pipeline forever.
    """
    path = Path(path)
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age_s = time.time() - lock_path.stat().st_mtime
            except FileNotFoundError:
                continue  # released between our EEXIST and stat() - retry now
            if age_s > stale_after_s:
                if log is not None:
                    log(f"ledger_lock: breaking stale lock {lock_path} "
                        f"(age {age_s:.0f}s > {stale_after_s:.0f}s)")
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                continue  # retry acquire immediately after breaking the lock
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"timed out after {timeout_s}s waiting for ledger lock "
                    f"{lock_path} (held by another process)")
            time.sleep(poll_s)
        else:
            try:
                os.write(fd, f"{os.getpid()} {time.time()}".encode("utf-8"))
            finally:
                os.close(fd)
            break
    try:
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def merge_save(path: Path, modified: list[Candidate],
               log: Callable[[str], None] | None = None) -> list[Candidate]:
    """Refresh-under-lock merge: the fix for the stale-list overwrite this
    project's own `ledger_lock` does NOT prevent on its own.

    `ledger_lock` serializes concurrent writers but does nothing about a
    caller that loaded the ledger long before it saves (evaluation phases in
    cycle.py can run for hours between candidates.json reads) - a plain
    locked `save_ledger(path, stale_list)` still silently clobbers anything
    a concurrent writer (e.g. the training daemon) appended to disk in the
    meantime, because it blindly serializes the caller's stale snapshot.

    This helper closes that gap: under the SAME lock hold, it (1) fresh-loads
    the ledger from disk, (2) replaces/updates the entries whose ids appear
    in `modified` (the candidates THIS caller actually changed) - preserving
    every disk-only entry `modified` never touched - and appends any ids in
    `modified` not yet present on disk, (3) saves the merged result, and
    (4) returns the merged list so the caller can refresh its own in-memory
    working list and operate on current state going forward.

    Callers must NOT already be holding `path`'s lock (this acquires its
    own; `ledger_lock` is not reentrant and a nested acquire would deadlock
    until `timeout_s` and then raise).
    """
    path = Path(path)
    with ledger_lock(path, log=log):
        fresh = load_ledger(path)
        fresh_ids = {c.id for c in fresh}
        by_id = {c.id: c for c in modified}
        merged = [by_id.get(c.id, c) for c in fresh]
        merged.extend(c for c in modified if c.id not in fresh_ids)
        save_ledger(path, merged)
    return merged
