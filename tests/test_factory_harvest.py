"""Tests for the ladder harvester + LADDER.md writer + reprioritization hook."""
from __future__ import annotations

from ptcg.factory.candidates import Candidate, Status
from ptcg.factory.harvest import harvest, reprioritize
from ptcg.factory.kaggle_client import FakeKaggleClient, SubmissionRow


def _submitted(name: str, version: str, at: str, wr: float = 0.5) -> Candidate:
    c = Candidate.create(name=name, version=version, deck="d.csv",
                         agent_kind="heuristic")
    c.status = Status.SUBMITTED
    c.submitted_at = at
    c.local_wr = wr
    return c


def test_harvest_scores_candidates_and_writes_ladder(tmp_path):
    cand = _submitted("lucario-heuristic", "v1.0", "2026-07-12T04:00")
    rows = [SubmissionRow("submission.tar.gz", "2026-07-12 04:00:00",
                          "lucario-heuristic v1.0 | deck=x | factory",
                          "COMPLETE", 1543.2),
            SubmissionRow("submission.tar.gz", "2026-07-12 02:00:00",
                          "someone-elses manual upload", "COMPLETE", 1400.0)]
    ladder = tmp_path / "LADDER.md"
    res = harvest(FakeKaggleClient(rows), [cand], ladder, today="2026-07-12")
    assert cand.kaggle_score == 1543.2
    assert cand.status is Status.SCORED
    assert cand.score_history[-1][1] == 1543.2
    assert res.matched == 1 and res.scored_updates == 1
    assert res.submissions_today == 2  # BOTH rows count toward the 5/day cap
    assert res.unmatched_descriptions == ["someone-elses manual upload"]
    text = ladder.read_text(encoding="utf-8")
    assert "lucario-heuristic-v1.0" in text and "counted" in text


def test_harvest_is_idempotent_on_scores(tmp_path):
    cand = _submitted("lucario-heuristic", "v1.0", "2026-07-12T04:00")
    rows = [SubmissionRow("s.tar.gz", "2026-07-12 04:00:00",
                          "lucario-heuristic v1.0 | factory", "COMPLETE", 1543.2)]
    ladder = tmp_path / "LADDER.md"
    harvest(FakeKaggleClient(rows), [cand], ladder, today="2026-07-12")
    harvest(FakeKaggleClient(rows), [cand], ladder, today="2026-07-12")
    assert len(cand.score_history) == 1  # unchanged score appends nothing


def test_ladder_marks_only_two_most_recent_as_counted(tmp_path):
    old = _submitted("a-heuristic", "v1.0", "2026-07-01T00:00")
    mid = _submitted("b-heuristic", "v1.0", "2026-07-10T00:00")
    new = _submitted("c-heuristic", "v1.0", "2026-07-11T00:00")
    ladder = tmp_path / "LADDER.md"
    harvest(FakeKaggleClient([]), [old, mid, new], ladder, today="2026-07-12")
    lines = ladder.read_text(encoding="utf-8").splitlines()
    row = {ln.split("|")[1].strip(): ln for ln in lines if ln.startswith("| ")}
    assert "counted" in row["c-heuristic-v1.0"] and "counted" in row["b-heuristic-v1.0"]
    assert "superseded" in row["a-heuristic-v1.0"]


def test_harvest_uses_most_recent_matching_row_for_score(tmp_path):
    """Finding 1: champion re-uploads produce 2+ ladder rows matching one
    candidate. The score must resolve DETERMINISTICALLY to the MOST RECENT
    matching row (the copy inside Kaggle's counted window), regardless of the
    CLI's list order, with exactly ONE score_history append per pass."""
    old_high = SubmissionRow("s.tar.gz", "2026-07-12 02:00:00",
                             "champ-net v1.0 - factory", "COMPLETE", 630.8)
    new_low = SubmissionRow("s.tar.gz", "2026-07-13 05:00:00",
                            "champ-net v1.0 - factory", "COMPLETE", 500.0)
    for rows in ([old_high, new_low], [new_low, old_high]):  # both CLI orders
        cand = _submitted("champ-net", "v1.0", "2026-07-13T05:00")
        ladder = tmp_path / "LADDER.md"
        res = harvest(FakeKaggleClient(rows), [cand], ladder, today="2026-07-13")
        assert cand.kaggle_score == 500.0            # newest row's score wins
        assert len(cand.score_history) == 1          # exactly ONE append
        assert cand.score_history[-1][1] == 500.0
        assert res.scored_updates == 1


def test_harvest_no_scored_rows_leaves_score_unchanged(tmp_path):
    """Matching rows but none carry a public score -> no update, no append."""
    cand = _submitted("champ-net", "v1.0", "2026-07-12T04:00")
    rows = [SubmissionRow("s.tar.gz", "2026-07-12 04:00:00",
                          "champ-net v1.0 - factory", "PENDING", None)]
    ladder = tmp_path / "LADDER.md"
    res = harvest(FakeKaggleClient(rows), [cand], ladder, today="2026-07-12")
    assert cand.kaggle_score is None
    assert cand.score_history == []
    assert res.matched == 1 and res.scored_updates == 0


def test_harvest_single_matching_row_regression(tmp_path):
    """One matching row still updates exactly once (unchanged behavior)."""
    cand = _submitted("champ-net", "v1.0", "2026-07-12T04:00")
    rows = [SubmissionRow("s.tar.gz", "2026-07-12 04:00:00",
                          "champ-net v1.0 - factory", "COMPLETE", 1543.2)]
    ladder = tmp_path / "LADDER.md"
    res = harvest(FakeKaggleClient(rows), [cand], ladder, today="2026-07-12")
    assert cand.kaggle_score == 1543.2 and len(cand.score_history) == 1
    assert res.scored_updates == 1


def test_harvest_tie_timestamp_prefers_scored_row(tmp_path):
    """Two matching rows at the SAME timestamp: deterministic resolution picks
    the scored row over an unscored one (documented tie-break)."""
    unscored = SubmissionRow("s.tar.gz", "2026-07-12 04:00:00",
                             "champ-net v1.0 - factory", "PENDING", None)
    scored = SubmissionRow("s.tar.gz", "2026-07-12 04:00:00",
                           "champ-net v1.0 - factory", "COMPLETE", 610.0)
    for rows in ([unscored, scored], [scored, unscored]):
        cand = _submitted("champ-net", "v1.0", "2026-07-12T04:00")
        ladder = tmp_path / "LADDER.md"
        harvest(FakeKaggleClient(rows), [cand], ladder, today="2026-07-12")
        assert cand.kaggle_score == 610.0


def test_reprioritize_bumps_queued_same_name():
    v1 = _submitted("x-net", "v1.0", "2026-07-01T00:00")
    v1.kaggle_score, v1.status = 1500.0, Status.SCORED
    v2 = _submitted("x-net", "v1.1", "2026-07-10T00:00")
    v2.kaggle_score, v2.status = 1550.0, Status.SCORED
    queued = Candidate.create(name="x-net", version="v1.2", deck="d.csv",
                              agent_kind="heuristic", priority=0.5)
    changed = reprioritize([v1, v2, queued])
    # Hand-verified 2026-07-11: improvement -> +0.05; 0.5 + 0.05 = 0.55
    assert changed == 1 and queued.priority == 0.55
