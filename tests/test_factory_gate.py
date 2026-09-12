"""Tests for the submission gate: incumbent, cadence, cap, exploration exception."""
from __future__ import annotations

import datetime as real_dt

from ptcg.factory import gate as gate_mod
from ptcg.factory.candidates import Candidate, Status
from ptcg.factory.gate import (HARD_DAILY_CAP, SubmissionCounter, decide,
                               effective_score, has_matrix_coverage,
                               incumbent, utc_today)
from ptcg.factory.tournament import MIN_COVERAGE_GAMES, MIN_COVERAGE_OPPONENTS


def test_utc_today_anchors_on_utc_not_local(monkeypatch):
    """Kaggle's 5/day cap resets on a UTC calendar day; on HST (UTC-10) local
    and UTC days can disagree for ~10h/day. Pin utc_today() to (a) request
    tz=utc explicitly (never a naive/local call) and (b) return the UTC
    calendar day even when a naive local read would say something else."""
    calls: list[object] = []

    class FakeDatetime(real_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            calls.append(tz)
            if tz is real_dt.timezone.utc:
                return real_dt.datetime(2026, 7, 12, 3, 0, tzinfo=real_dt.timezone.utc)
            # what a naive local `datetime.now()`/`date.today()` call would see
            return real_dt.datetime(2026, 7, 11, 17, 0)

    monkeypatch.setattr(gate_mod.dt, "datetime", FakeDatetime)

    assert utc_today() == "2026-07-12"
    assert calls == [real_dt.timezone.utc]  # must request UTC explicitly, not naive


def _sub(name: str, wr: float, at: str) -> Candidate:
    c = Candidate.create(name=name, version="v1.0", deck="d.csv",
                         agent_kind="heuristic")
    c.status, c.local_wr, c.submitted_at = Status.SUBMITTED, wr, at
    return c


def _evaluated(name: str, wr: float, novel: bool = False) -> Candidate:
    c = Candidate.create(name=name, version="v1.0", deck="d.csv",
                         agent_kind="heuristic", novel_axis=novel)
    c.status, c.local_wr, c.local_games = Status.EVALUATED, wr, 150
    return c


def test_counter_persists_across_restart_and_day_rollover(tmp_path):
    p = tmp_path / "counter.json"
    c1 = SubmissionCounter(p)
    assert c1.today_count("2026-07-12") == 0
    c1.record("2026-07-12")
    c1.record("2026-07-12")
    c2 = SubmissionCounter(p)  # simulated restart: state reloaded from disk
    assert c2.today_count("2026-07-12") == 2
    assert c2.today_count("2026-07-13") == 0  # day rollover resets


def test_counter_reconcile_floors_at_observed(tmp_path):
    c = SubmissionCounter(tmp_path / "counter.json")
    c.record("2026-07-12")
    c.reconcile("2026-07-12", observed_today=4)
    assert c.today_count("2026-07-12") == 4
    c.reconcile("2026-07-12", observed_today=1)  # never decreases
    assert c.today_count("2026-07-12") == 4


def test_counter_record_rereads_disk_under_lock(tmp_path):
    """Two counter instances (two processes) must serialize: each record()
    must observe the other's persisted count, never clobber it."""
    path = tmp_path / "sub" / "counter.json"  # virgin parent (sub/ not created)
    a = SubmissionCounter(path)
    b = SubmissionCounter(path)  # stale second instance
    a.record("2026-07-17")
    b.record("2026-07-17")  # must land at 2, not clobber back to 1
    fresh = SubmissionCounter(path)
    assert fresh.today_count("2026-07-17") == 2


def test_counter_reconcile_floors_not_clobbers(tmp_path):
    path = tmp_path / "counter.json"
    a = SubmissionCounter(path)
    b = SubmissionCounter(path)
    a.record("2026-07-17")
    a.record("2026-07-17")
    b.reconcile("2026-07-17", 1)  # observed 1 on ladder, disk already at 2
    fresh = SubmissionCounter(path)
    assert fresh.today_count("2026-07-17") == 2  # max(disk 2, observed 1)


def test_incumbent_is_weaker_of_two_most_recent():
    old = _sub("old", 0.90, "2026-07-01T00:00")
    a = _sub("a", 0.55, "2026-07-10T00:00")
    b = _sub("b", 0.48, "2026-07-11T00:00")
    assert incumbent([old, a, b]).name == "b"  # 0.48 < 0.55; old's 0.90 not counted
    assert incumbent([]) is None


def test_incumbent_prefers_ladder_score_over_local_wr_when_both_scored():
    """Real production case (2026-07-14 ledger): the higher-local_wr counted
    candidate has the LOWER kaggle_score. local_wr and kaggle_score disagree
    on which is "weaker" - the ladder score must win, since that's the real
    evidence the incumbent-protection requirement cares about."""
    weak_ladder = _sub("weak-ladder-strong-local", 0.5333, "2026-07-12T02:42:00")
    weak_ladder.kaggle_score = 522.1
    strong_ladder = _sub("strong-ladder-weak-local", 0.4733, "2026-07-12T02:42:01")
    strong_ladder.kaggle_score = 630.8
    inc = incumbent([weak_ladder, strong_ladder])
    assert inc.name == "weak-ladder-strong-local"  # 522.1 < 630.8 wins despite lower local_wr


def test_incumbent_falls_back_to_local_wr_when_ladder_score_not_fully_harvested():
    scored = _sub("scored", 0.60, "2026-07-12T00:00")
    scored.kaggle_score = 500.0
    pending = _sub("pending", 0.40, "2026-07-12T01:00")  # not yet harvested, no kaggle_score
    inc = incumbent([scored, pending])
    assert inc.name == "pending"  # can't compare scores fairly (only one harvested) -> local_wr fallback


def test_is_incumbent_designation_overrides_counted_pair():
    """Weekly-review re-key (2026-07-20): local eval and ladder invert - the
    high-local_wr density line converges LOW on the ladder (524.7) while the
    lower-local_wr lean-attacker-down1 line converges highest (571.6). The auto
    counted-pair rule keys the bar to the ladder-worst (high-local_wr) line;
    an explicit is_incumbent flag re-keys it to the chosen line's local_wr."""
    dense = _sub("dense", 0.647, "2026-07-19T21:31")
    dense.kaggle_score = 524.7
    lean = _sub("lean", 0.553, "2026-07-19T21:31")
    lean.kaggle_score = 571.6
    # auto rule alone: incumbent = weaker-by-ladder = dense, bar 0.647
    assert incumbent([dense, lean]).name == "dense"
    # designating lean re-keys the bar to lean's 0.553
    lean.is_incumbent = True
    inc = incumbent([dense, lean])
    assert inc.name == "lean" and inc.local_wr == 0.553


def test_retired_is_incumbent_flag_is_ignored():
    """The is_incumbent pin is manual and never auto-reassigns - if the
    flagged line is later RETIRED, incumbent() must not keep pinning the
    stale bar forever. Retiring the flagged candidate should fall back to
    the dynamic weaker-of-counted-pair rule instead."""
    dense = _sub("dense", 0.647, "2026-07-19T21:31")
    dense.kaggle_score = 524.7
    lean = _sub("lean", 0.553, "2026-07-19T21:31")
    lean.kaggle_score = 571.6
    lean.is_incumbent = True
    # still flagged and active: pin wins, as in the test above
    assert incumbent([dense, lean]).name == "lean"
    # retire the flagged line: the pin must be ignored, dynamic rule applies
    lean.status = Status.RETIRED
    inc = incumbent([dense, lean])
    assert inc.name == "dense"  # weaker-by-ladder of the (now unpinned) pair


def test_designated_incumbent_flag_lowers_effective_gate_bar(tmp_path):
    """A candidate whose local_wr sits between the designated bar (0.553) and the
    old auto bar (0.647) now clears the gate, where it would have been blocked
    below-incumbent under the counted-pair rule."""
    counter = SubmissionCounter(tmp_path / "c.json")
    dense = _sub("dense", 0.647, "2026-07-19T21:31")
    dense.kaggle_score = 524.7
    lean = _sub("lean", 0.553, "2026-07-19T21:31")
    lean.kaggle_score = 571.6
    lean.is_incumbent = True
    mid = _evaluated("mid", 0.60)  # beats 0.553 bar; below the old 0.647 bar
    d = decide(mid, [dense, lean, mid], counter, "2026-07-20")
    assert d.submit and "beats incumbent" in d.reason


def test_gate_paths(tmp_path):
    counter = SubmissionCounter(tmp_path / "c.json")
    inc = _sub("inc", 0.48, "2026-07-11T00:00")

    better = _evaluated("better", 0.55)
    d = decide(better, [inc, better], counter, "2026-07-12")
    assert d.submit and "beats incumbent" in d.reason

    # hard cap blocks everything
    for _ in range(HARD_DAILY_CAP):
        counter.record("2026-07-12")
    d = decide(better, [inc, better], counter, "2026-07-12")
    assert not d.submit and "hard cap" in d.reason

    # cadence defers a small edge, merit overrides it (verified 2026-07-11:
    # 0.50-0.48=0.02 < 0.05 defers; 0.55-0.48=0.07 >= 0.05 overrides).
    # cadence_per_day pinned explicitly to 2 (default raised to 5) so this
    # "2/day used up" scenario's arithmetic stays intact.
    counter2 = SubmissionCounter(tmp_path / "c2.json")
    counter2.record("2026-07-12")
    counter2.record("2026-07-12")  # cadence 2/day used up
    small = _evaluated("small", 0.50)
    assert not decide(small, [inc, small], counter2, "2026-07-12",
                      cadence_per_day=2).submit
    assert decide(better, [inc, better], counter2, "2026-07-12",
                  cadence_per_day=2).submit

    # exploration exception: novel axis at parity submits, flagged exploratory
    counter3 = SubmissionCounter(tmp_path / "c3.json")
    novel = _evaluated("novel", 0.47, novel=True)  # -0.01 within 0.02 parity band
    d = decide(novel, [inc, novel], counter3, "2026-07-12")
    assert d.submit and d.exploratory

    # same WR without the novel axis -> below incumbent
    plain = _evaluated("plain", 0.47)
    d = decide(plain, [inc, plain], counter3, "2026-07-12")
    assert not d.submit and d.mark_below_incumbent


def test_bootstrap_submits_when_no_incumbent(tmp_path):
    counter = SubmissionCounter(tmp_path / "c.json")
    cand = _evaluated("first", 0.52)
    d = decide(cand, [cand], counter, "2026-07-12")
    assert d.submit and "bootstrap" in d.reason


# --- Task 8: gate re-key to matrix rating with coverage fallback ---------

def _with_matrix(c: Candidate, rating: float, games: int = MIN_COVERAGE_GAMES,
                 opponents: int = MIN_COVERAGE_OPPONENTS) -> Candidate:
    c.matrix_rating, c.matrix_games, c.matrix_opponents = rating, games, opponents
    return c


def test_has_matrix_coverage_requires_both_thresholds():
    c = _evaluated("c", 0.50)
    assert not has_matrix_coverage(c)
    c.matrix_games, c.matrix_opponents = MIN_COVERAGE_GAMES, MIN_COVERAGE_OPPONENTS - 1
    assert not has_matrix_coverage(c)
    c.matrix_games, c.matrix_opponents = MIN_COVERAGE_GAMES - 1, MIN_COVERAGE_OPPONENTS
    assert not has_matrix_coverage(c)
    c.matrix_games, c.matrix_opponents = MIN_COVERAGE_GAMES, MIN_COVERAGE_OPPONENTS
    assert has_matrix_coverage(c)


def test_effective_score_prefers_matrix_rating_when_covered():
    c = _evaluated("c", 0.30)
    assert effective_score(c) == 0.30  # no coverage yet -> local_wr
    c.matrix_rating, c.matrix_games, c.matrix_opponents = 0.90, MIN_COVERAGE_GAMES, MIN_COVERAGE_OPPONENTS
    assert effective_score(c) == 0.90  # covered -> matrix_rating


def test_matrix_path_boundary_exact_p_beats_incumbent(tmp_path):
    """Hand-verified (r_c + r_inc = 1 identity, so P = r_c directly):
    0.55 / (0.55 + 0.45) = 0.55 exactly, meeting INCUMBENT_MARGIN_P (>=).
    local_wr is tied (0.50 == 0.50) on both sides so a fallback-to-local_wr
    bug (edge=0) would NOT submit here - only a correctly-wired matrix path
    submits."""
    counter = SubmissionCounter(tmp_path / "c.json")
    inc = _with_matrix(_sub("inc", 0.50, "2026-07-11T00:00"), rating=0.45)
    cand = _with_matrix(_evaluated("cand", 0.50), rating=0.55)
    d = decide(cand, [inc, cand], counter, "2026-07-12")
    assert d.submit and "matrix" in d.reason.lower()


def test_matrix_path_just_below_boundary_stays_below_incumbent(tmp_path):
    """Hand-verified: 0.549 / (0.549 + 0.451) = 0.549 < 0.55 margin."""
    counter = SubmissionCounter(tmp_path / "c.json")
    inc = _with_matrix(_sub("inc", 0.50, "2026-07-11T00:00"), rating=0.451)
    cand = _with_matrix(_evaluated("cand", 0.50), rating=0.549)
    d = decide(cand, [inc, cand], counter, "2026-07-12")
    assert not d.submit and d.mark_below_incumbent


def test_matrix_path_just_above_boundary_submits(tmp_path):
    """Hand-verified: 0.551 / (0.551 + 0.449) = 0.551 >= 0.55 margin."""
    counter = SubmissionCounter(tmp_path / "c.json")
    inc = _with_matrix(_sub("inc", 0.50, "2026-07-11T00:00"), rating=0.449)
    cand = _with_matrix(_evaluated("cand", 0.50), rating=0.551)
    d = decide(cand, [inc, cand], counter, "2026-07-12")
    assert d.submit and "matrix" in d.reason.lower()


def test_mixed_coverage_falls_back_to_local_wr_path(tmp_path):
    """Incumbent lacks matrix coverage (default matrix_games=0) - even though
    the candidate's matrix_rating (0.90) would crush on a matrix comparison,
    the gate must use the existing local_wr edge (0.40 - 0.60 < 0), which is
    negative -> below incumbent."""
    counter = SubmissionCounter(tmp_path / "c.json")
    inc = _sub("inc", 0.60, "2026-07-11T00:00")  # no matrix coverage
    cand = _with_matrix(_evaluated("cand", 0.40), rating=0.90)
    d = decide(cand, [inc, cand], counter, "2026-07-12")
    assert not d.submit and d.mark_below_incumbent
    assert "matrix" not in d.reason.lower()


def test_matrix_path_novel_axis_at_parity_boundary_submits_exploratory(tmp_path):
    """Hand-verified: 0.48 / (0.48 + 0.52) = 0.48 == 0.5 - parity_band(0.02)."""
    counter = SubmissionCounter(tmp_path / "c.json")
    inc = _with_matrix(_sub("inc", 0.50, "2026-07-11T00:00"), rating=0.52)
    cand = _with_matrix(_evaluated("cand", 0.50, novel=True), rating=0.48)
    d = decide(cand, [inc, cand], counter, "2026-07-12")
    assert d.submit and d.exploratory


def test_matrix_path_same_parity_without_novel_axis_stays_below(tmp_path):
    counter = SubmissionCounter(tmp_path / "c.json")
    inc = _with_matrix(_sub("inc", 0.50, "2026-07-11T00:00"), rating=0.52)
    cand = _with_matrix(_evaluated("cand", 0.50), rating=0.48)  # no novel axis
    d = decide(cand, [inc, cand], counter, "2026-07-12")
    assert not d.submit and d.mark_below_incumbent


def test_matrix_path_below_parity_band_stays_below_even_with_novel_axis(tmp_path):
    """Hand-verified: 0.47 / (0.47 + 0.53) = 0.47 < 0.48 parity boundary."""
    counter = SubmissionCounter(tmp_path / "c.json")
    inc = _with_matrix(_sub("inc", 0.50, "2026-07-11T00:00"), rating=0.53)
    cand = _with_matrix(_evaluated("cand", 0.50, novel=True), rating=0.47)
    d = decide(cand, [inc, cand], counter, "2026-07-12")
    assert not d.submit and d.mark_below_incumbent


def _sub_matrix_only(name: str, at: str) -> Candidate:
    """A submitted incumbent with NO local_wr signal at all - only matrix
    coverage (mirrors a matrix-only-evaluated candidate that later got
    submitted/promoted). Distinct from `_sub`, which always sets local_wr."""
    c = Candidate.create(name=name, version="v1.0", deck="d.csv",
                         agent_kind="heuristic")
    c.status, c.submitted_at = Status.SUBMITTED, at
    return c


def test_matrix_only_incumbent_does_not_bootstrap_bypass_gate(tmp_path):
    """I2: a matrix-only incumbent (local_wr=None, matrix-covered) must NOT
    trigger the bootstrap-submit branch and disable the gate entirely - a
    covered-but-weaker challenger must be rejected via the matrix
    comparison, exactly as if the incumbent had a local_wr."""
    counter = SubmissionCounter(tmp_path / "c.json")
    inc = _with_matrix(_sub_matrix_only("inc", "2026-07-11T00:00"), rating=0.55)
    cand = _with_matrix(_evaluated("cand", 0.50), rating=0.45)
    d = decide(cand, [inc, cand], counter, "2026-07-12")
    assert not d.submit
    assert "bootstrap" not in d.reason.lower()
    assert d.mark_below_incumbent


def test_matrix_only_incumbent_with_uncovered_challenger_rejects_without_crash(tmp_path):
    """I2 corollary: when the incumbent is matrix-only (local_wr=None) AND
    the CHALLENGER lacks matrix coverage, the two sides cannot be compared
    on either scale (no shared local_wr, challenger not matrix-covered).
    The safe behavior is no submit with a coverage-mismatch reason - never
    a bootstrap submit, and never a crash from None-arithmetic on
    `inc.local_wr`."""
    counter = SubmissionCounter(tmp_path / "c.json")
    inc = _with_matrix(_sub_matrix_only("inc", "2026-07-11T00:00"), rating=0.55)
    cand = _evaluated("cand", 0.50)  # no matrix coverage at all
    d = decide(cand, [inc, cand], counter, "2026-07-12")
    assert not d.submit
    assert "bootstrap" not in d.reason.lower()
    assert "coverage" in d.reason.lower()
