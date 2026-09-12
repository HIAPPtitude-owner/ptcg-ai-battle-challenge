"""Event-driven ladder harvest (spec S5): pull freshest Kaggle data, map scores
onto candidates, regenerate experiments/LADDER.md, re-prioritize the queue."""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from pathlib import Path

from ptcg.factory.candidates import Candidate, Status, parse_version
from ptcg.factory.gate import utc_today
from ptcg.factory.kaggle_client import SubmissionRow

LADDER_HEADER = (
    "# Ladder Log - Kaggle submissions (auto-written by the factory harvester)\n\n"
    "One row per factory submission: versioned identity, score trajectory, verdict.\n\n"
    "| Version | Submitted | Description | Status | Score trajectory | Verdict |\n"
    "|---------|-----------|-------------|--------|------------------|---------|\n"
)


@dataclass
class HarvestResult:
    matched: int = 0
    scored_updates: int = 0
    submissions_today: int = 0
    unmatched_descriptions: list[str] = field(default_factory=list)


def match_row(candidates: list[Candidate], row: SubmissionRow) -> Candidate | None:
    for cand in candidates:
        if row.description.startswith(f"{cand.name} {cand.version}"):
            return cand
    return None


def _authoritative_row(rows: list[SubmissionRow]) -> SubmissionRow:
    """Pick the ONE row that speaks for a candidate when several ladder rows
    share its versioned identity (a champion re-upload re-submits the SAME
    name+version, so 2+ live rows match one candidate - F3 finding 1).

    Rule: the MOST RECENT row by upload date wins - that's the copy inside
    Kaggle's two-most-recent counted window, which is what champion()/
    incumbent()/reprioritize() are about. Kaggle dates are fixed-width
    "YYYY-MM-DD HH:MM:SS" (the same format `harvest` already relies on for its
    `startswith(today)` day check), so lexicographic string order IS
    chronological order - no parsing needed.

    Deterministic tie-break for two rows at the identical timestamp (two
    uploads in the same second): prefer a SCORED row over an unscored one,
    then the higher score. This keeps the result independent of the CLI's
    (unpinned) list order in every case.
    """
    return max(rows, key=lambda r: (
        r.date,
        r.public_score is not None,
        r.public_score if r.public_score is not None else float("-inf"),
    ))


def harvest(client, candidates: list[Candidate], ladder_path: Path,
            today: str | None = None) -> HarvestResult:
    today = today or utc_today()  # Kaggle's 5/day cap is a UTC day, not local
    rows = client.list_submissions()
    res = HarvestResult()
    matched_rows: dict[str, list[SubmissionRow]] = {}
    for row in rows:
        if row.date.startswith(today):
            res.submissions_today += 1  # ALL uploads count toward the 5/day cap
        cand = match_row(candidates, row)
        if cand is None:
            res.unmatched_descriptions.append(row.description)
            continue
        res.matched += 1  # per-row: how many ladder rows map to our candidates
        matched_rows.setdefault(cand.id, []).append(row)
    # Resolve exactly ONE authoritative row per candidate before touching
    # kaggle_score/score_history, so multiple matching rows can never produce
    # order-dependent scores or per-row junk history appends (F3 finding 1).
    for cand in candidates:
        rows_for = matched_rows.get(cand.id)
        if not rows_for:
            continue
        row = _authoritative_row(rows_for)
        if row.public_score is not None:
            if cand.kaggle_score != row.public_score:
                cand.score_history.append(
                    [dt.datetime.now().isoformat(timespec="minutes"),
                     row.public_score])
                cand.kaggle_score = row.public_score
                res.scored_updates += 1
            if cand.status is Status.SUBMITTED:
                cand.status = Status.SCORED
    write_ladder(ladder_path, candidates, rows)
    return res


def write_ladder(path: Path, candidates: list[Candidate],
                 rows: list[SubmissionRow]) -> None:
    """Full idempotent regeneration (never append-drift), atomic write."""
    submitted = [c for c in candidates if c.submitted_at is not None]
    submitted.sort(key=lambda c: c.submitted_at, reverse=True)
    counted = {c.id for c in submitted[:2]}  # Kaggle: two most recent count
    lines = [LADDER_HEADER]
    for c in submitted:
        traj = " -> ".join(f"{score:.1f}" for _, score in c.score_history) or "pending"
        verdict = "counted" if c.id in counted else "superseded"
        desc = next((r.description for r in rows
                     if r.description.startswith(f"{c.name} {c.version}")), "")
        lines.append(f"| {c.id} | {c.submitted_at} | {desc} | {c.status.value} "
                     f"| {traj} | {verdict} |\n")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    os.replace(tmp, path)


def reprioritize(candidates: list[Candidate]) -> int:
    """Score-trajectory hook (deliberately simple; spec S10 bans bandit math):
    latest scored version improved on its predecessor -> queued same-name
    candidates +0.05 priority; regressed -> -0.05. Clamped to [0, 1]."""
    changed = 0
    by_name: dict[str, list[Candidate]] = {}
    for c in candidates:
        if c.kaggle_score is not None:
            by_name.setdefault(c.name, []).append(c)
    for name, scored in by_name.items():
        if len(scored) < 2:
            continue
        scored.sort(key=lambda c: parse_version(c.version))
        delta = 0.05 if scored[-1].kaggle_score > scored[-2].kaggle_score else -0.05
        for c in candidates:
            if c.name == name and c.status is Status.QUEUED:
                c.priority = min(1.0, max(0.0, c.priority + delta))
                changed += 1
    return changed
