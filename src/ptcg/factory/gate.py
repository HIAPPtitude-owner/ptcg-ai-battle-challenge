"""Submission gate (spec S4): better-than-incumbent, exploration exception,
cadence rhythm with merit override, persistent hard 5/day cap."""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path

from ptcg.factory.bt import head_to_head_p
from ptcg.factory.candidates import Candidate, Status, ledger_lock
from ptcg.factory.tournament import (INCUMBENT_MARGIN_P, MIN_COVERAGE_GAMES,
                                     MIN_COVERAGE_OPPONENTS)

HARD_DAILY_CAP = 5  # Kaggle rule; never configurable upward


def utc_today() -> str:
    """Kaggle's 5/day submission cap resets on a UTC calendar day, not host-
    local time. On a UTC-10 machine (HST) local and UTC days overlap by ~10
    hours, so a naive `date.today()`/`datetime.now()` default can let 5
    submissions after 14:00 local plus 5 more after local midnight both land
    in the same UTC day - a cap violation. Every day-anchor in the gate/
    submit/harvest path must derive `today` from this helper, never from
    local wall-clock time."""
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


@dataclass
class GateDecision:
    submit: bool
    reason: str
    exploratory: bool = False
    mark_below_incumbent: bool = False


class SubmissionCounter:
    """Persistent daily submission counter; survives restarts (spec S9)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._refresh()

    def _refresh(self) -> None:
        if self.path.exists():
            doc = json.loads(self.path.read_text(encoding="utf-8"))
            self.date, self.count = doc.get("date"), int(doc.get("count", 0))
        else:
            self.date, self.count = None, 0

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps({"date": self.date, "count": self.count}),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    def refresh(self) -> None:
        """Public: reload the persisted count. Another entry point (a manual
        `scripts/factory_cycle.py` run racing the scheduled watch) may have
        written the file since this instance was constructed, so callers that
        made an admission decision from a construction-time snapshot must
        refresh before trusting it."""
        self._refresh()

    def today_count(self, today: str) -> int:
        return self.count if self.date == today else 0

    def try_reserve(self, today: str, n: int = 1) -> bool:
        """Atomically claim `n` of the day's submission slots against the HARD
        5/day Kaggle cap. Under the counter lock: reload the persisted count
        (so a stale in-memory snapshot can never cause an over-count), then
        ONLY if `used + n <= HARD_DAILY_CAP` increment and persist. Returns
        True iff the slots were claimed; False (no state change) otherwise.

        This is the single admission chokepoint that makes the 5/day cap hold
        across concurrent processes / entry points: the cap re-check and the
        increment are one atomic step under the lock, so there is no
        check-then-act window a stale second instance could slip through.
        HARD_DAILY_CAP is fixed and never raised (never accept a cap override).
        """
        with ledger_lock(self.path):
            self._refresh()
            used = self.today_count(today)
            if used + n > HARD_DAILY_CAP:
                return False
            self.count = used + n
            self.date = today
            self._save()
        return True

    def release(self, today: str, n: int = 1) -> None:
        """Give back `n` previously-reserved slots (the reserve-before-upload
        failure path): under the lock, refresh and decrement, flooring at 0 so
        a release can never drive the persisted count negative."""
        with ledger_lock(self.path):
            self._refresh()
            self.count = max(0, self.today_count(today) - n)
            self.date = today
            self._save()

    def record(self, today: str) -> bool:
        """Cap-aware single-slot increment (delegates to `try_reserve`). Returns
        False without changing state when the day is already at the hard cap, so
        even a caller acting on a stale in-memory snapshot can never push the
        persisted count past HARD_DAILY_CAP - the increment re-validates the cap
        under the lock. Backward-compatible for every sub-cap caller."""
        return self.try_reserve(today, 1)

    def reconcile(self, today: str, observed_today: int) -> None:
        """Floor at what the harvested ladder shows (manual uploads count too);
        never decreases within a day."""
        with ledger_lock(self.path):
            self._refresh()
            self.count = max(self.today_count(today), observed_today)
            self.date = today
            self._save()


def counted_submissions(candidates: list[Candidate]) -> list[Candidate]:
    """The (up to two) most-recently-submitted candidates - the pair Kaggle
    currently counts (spec S4: only the two most recent submissions count, and
    they are evicted by RECENCY, not by score). Ordered most-recent first.

    Shared by `incumbent()` (the beat-this bar) and `champion()` (the identity
    the F1 champion-pairing guard protects from recency-eviction).
    """
    submitted = [c for c in candidates
                 if c.status in (Status.SUBMITTED, Status.SCORED) and c.submitted_at]
    submitted.sort(key=lambda c: c.submitted_at, reverse=True)
    return submitted[:2]


def incumbent(candidates: list[Candidate]) -> Candidate | None:
    """Weaker of the two most recently submitted/scored candidates - i.e. the
    Kaggle-counted pair (spec S4: only the two most recent submissions count).

    Ranked by kaggle_score (real ladder evidence) whenever BOTH counted
    candidates have been harvested - local_wr and kaggle_score are different
    scales (win-rate-vs-heuristic-v0 vs. Gaussian ladder rating against the
    real field), so mixing them (kaggle_score for one, local_wr for the
    other) would compare apples to oranges. Falls back to local_wr, unchanged
    from the original behavior, when a ladder score isn't available for both
    (e.g. one was just submitted and hasn't been harvested yet) - local_wr is
    the only metric guaranteed to exist pre-harvest.

    This only decides which of the two counted candidates the gate uses as
    the beat-this bar; it does NOT control which slot Kaggle itself evicts
    on the next submission (Kaggle evicts by recency, not by score) - see
    spec S4 discussion for that distinction.

    MANUAL OVERRIDE (weekly-review re-key): if any candidate carries the
    `is_incumbent` flag (and has a local_wr), that designated line pins the
    beat-this bar directly, decoupled from the volatile counted pair. This
    exists because local eval and the ladder can invert (a high-local_wr line
    converges LOW on the ladder) - the auto counted-pair rule would then key
    the bar to the ladder-worst (high-local_wr) line, over-strict for the
    lines that actually climb the ladder. When several are flagged the
    strictest (highest local_wr) bar wins, so a stray flag can never silently
    lower the bar. The flag only affects this beat-this bar; champion() and
    the champion-pairing recency guard are untouched.

    A flagged candidate whose status is RETIRED is excluded from the override
    set - the pin is a manual, weekly-review-only marker in candidates.json
    that never auto-reassigns, so a retired-but-still-flagged line would
    otherwise keep pinning a stale bar forever. When the only flagged
    candidate(s) are retired (or none are flagged), this falls back to the
    dynamic weaker-of-counted-pair rule below.
    """
    designated = [c for c in candidates
                  if getattr(c, "is_incumbent", False)
                  and c.status != Status.RETIRED
                  and c.local_wr is not None]
    if designated:
        return max(designated, key=lambda c: c.local_wr)
    counted = counted_submissions(candidates)
    if not counted:
        return None
    with_score = [c for c in counted if c.kaggle_score is not None]
    if len(with_score) == len(counted):
        return min(with_score, key=lambda c: c.kaggle_score)
    with_wr = [c for c in counted if c.local_wr is not None]
    return min(with_wr, key=lambda c: c.local_wr) if with_wr else counted[-1]


def champion(candidates: list[Candidate]) -> Candidate | None:
    """The STRONGER of the currently-counted pair by harvested kaggle_score -
    the identity worth protecting from recency-eviction (F1 champion-pairing
    guard). Ranked by kaggle_score ONLY (real ladder evidence); local_wr is a
    documented ladder-INVERTED proxy (see `incumbent()`'s scale-semantics
    note) and must never be used to pick the champion.

    Returns None in two distinct situations the caller must tell apart via
    `counted_submissions()`: (a) nothing is counted yet -> nothing to protect;
    (b) counted slots exist but NONE has a harvested kaggle_score yet
    (just-submitted, pre-harvest) -> champion not yet identifiable, so the
    caller conservatively defers rather than risk evicting an unknown champion.
    """
    with_score = [c for c in counted_submissions(candidates)
                  if c.kaggle_score is not None]
    if not with_score:
        return None
    return max(with_score, key=lambda c: c.kaggle_score)


def has_matrix_coverage(c: Candidate) -> bool:
    """True once a candidate's Bradley-Terry matrix rating (compute-saturation
    tournament, tournament.py) rests on enough games/opponents to trust -
    mirrors the exact QUEUED->EVALUATED promotion threshold in
    `tournament.refresh_ratings` (MIN_COVERAGE_GAMES/MIN_COVERAGE_OPPONENTS,
    imported here rather than re-literaled)."""
    return (c.matrix_games >= MIN_COVERAGE_GAMES
            and c.matrix_opponents >= MIN_COVERAGE_OPPONENTS)


def effective_score(c: Candidate) -> float:
    """The merit score used for ranking/gating a single candidate: the
    matrix_rating once coverage is reached (falling back to local_wr if
    matrix_rating is somehow still unset despite coverage - defensive, see
    tournament.refresh_ratings which normally sets both together), otherwise
    local_wr (0.0 if never locally evaluated).

    NOTE: matrix_rating (Bradley-Terry, geometric-mean-normalized around 1.0)
    and local_wr (a 0-1 win rate) are DIFFERENT SCALES and must never be
    compared against each other directly - this function returns whichever
    scale a single candidate is on, not a normalized cross-scale value. See
    `decide()`: the matrix path only fires when BOTH candidates being compared
    have coverage (same scale on both sides); ranking in `submit.py` sorts by
    this per-candidate value, which can mix scales across the full candidate
    list (documented, not a decision-affecting bug - see
    tests/test_factory_submit.py's scale-mismatch documentation test)."""
    if has_matrix_coverage(c) and c.matrix_rating is not None:
        return c.matrix_rating
    return c.local_wr or 0.0


def decide(candidate: Candidate, candidates: list[Candidate],
           counter: SubmissionCounter, today: str, cadence_per_day: int = 5,
           merit_margin: float = 0.05, parity_band: float = 0.02) -> GateDecision:
    """Merit gate for a single EVALUATED candidate: better-than-incumbent with
    a cadence rhythm, a novel-axis exploration exception, and the persistent
    hard daily cap. The beat-this bar comes from `incumbent()` - see that
    function's docstring for the kaggle_score-vs-local_wr scale semantics (the
    two metrics live on different scales and must not be mixed).

    Note this gate decides only whether a candidate has the MERIT to submit; it
    does not enforce the champion-pairing recency-eviction protection - that
    lives in `submit.submit_candidates` (F1), which consumes this decision.
    """
    if candidate.status is not Status.EVALUATED:
        return GateDecision(False, f"status={candidate.status.value}, not evaluated")
    used = counter.today_count(today)
    if used >= HARD_DAILY_CAP:
        return GateDecision(False, f"hard cap reached ({used}/{HARD_DAILY_CAP})")
    inc = incumbent(candidates)
    # Bootstrap-submit only when the incumbent has NEITHER signal (no local
    # WR AND no matrix coverage) - i.e. there is truly nothing to compare
    # against. A matrix-only incumbent (local_wr=None but matrix-covered,
    # e.g. under the compute-saturation matrix-tournament regime) must NOT
    # bypass the gate: it still has a real comparable signal via the matrix
    # path below. Fixes I2 (whole-branch review): the old `inc.local_wr is
    # None` check alone disabled the gate entirely for every challenger
    # once an incumbent went matrix-only.
    if inc is None or (inc.local_wr is None and not has_matrix_coverage(inc)):
        return GateDecision(True, "no incumbent with local WR or matrix "
                                  "coverage - bootstrap submit")

    if has_matrix_coverage(candidate) and has_matrix_coverage(inc):
        p = head_to_head_p({candidate.id: effective_score(candidate),
                            inc.id: effective_score(inc)}, candidate.id, inc.id)
        if p >= INCUMBENT_MARGIN_P:
            return GateDecision(True, f"beats incumbent {inc.id} on matrix "
                                      f"P={p:.3f} (>= {INCUMBENT_MARGIN_P})")
        if candidate.novel_axis and p >= 0.5 - parity_band:
            if used >= cadence_per_day:
                return GateDecision(False, "novel axis at matrix parity but cadence used")
            return GateDecision(True, f"exploration exception: novel axis at "
                                      f"matrix parity (P={p:.3f})", exploratory=True)
        return GateDecision(False, f"below incumbent {inc.id} on matrix P={p:.3f}",
                            mark_below_incumbent=True)

    if inc.local_wr is None:
        # Reached only when the incumbent is matrix-only (no local WR) AND
        # the two candidates could NOT both be compared on the matrix scale
        # above (the challenger lacks matrix coverage). There is no shared
        # scale left to compare on - never fall through to the local_wr
        # arithmetic below, which would crash on `None - inc.local_wr`.
        return GateDecision(False, f"incumbent {inc.id} has matrix coverage "
                                  f"but no local WR, and candidate lacks "
                                  f"matrix coverage - coverage mismatch, "
                                  f"cannot compare")

    edge = (candidate.local_wr or 0.0) - inc.local_wr
    if edge > 0:
        if used >= cadence_per_day and edge < merit_margin:
            return GateDecision(False, f"cadence {cadence_per_day}/day used and edge "
                                       f"{edge:+.3f} < merit margin {merit_margin}")
        return GateDecision(True, f"beats incumbent {inc.id} by {edge:+.3f}")
    if candidate.novel_axis and edge >= -parity_band:
        if used >= cadence_per_day:
            return GateDecision(False, "novel axis at parity but cadence used")
        return GateDecision(True, f"exploration exception: novel axis at parity "
                                  f"({edge:+.3f})", exploratory=True)
    return GateDecision(False, f"below incumbent {inc.id} by {edge:+.3f}",
                        mark_below_incumbent=True)
