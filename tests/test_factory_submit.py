"""Tests for the submitter: descriptions, retry policy, dry-run, status flips."""
from __future__ import annotations

from pathlib import Path

from ptcg.factory import submit as submit_mod
from ptcg.factory.candidates import Candidate, Status
from ptcg.factory.gate import (HARD_DAILY_CAP, SubmissionCounter,
                               counted_submissions)
from ptcg.factory.kaggle_client import FakeKaggleClient
from ptcg.factory.submit import submission_description, submit_candidates


def _evaluated(name: str, wr: float, novel: bool = False) -> Candidate:
    c = Candidate.create(name=name, version="v1.0", deck="decks/mega-x.csv",
                         agent_kind="heuristic")
    c.status, c.local_wr, c.local_games = Status.EVALUATED, wr, 150
    c.novel_axis = novel
    return c


def _sub(name: str, wr: float, at: str) -> Candidate:
    c = _evaluated(name, wr)
    c.status, c.submitted_at = Status.SUBMITTED, at
    return c


def _scored(name: str, wr: float, at: str, score: float) -> Candidate:
    """A counted (recent) submission WITH a harvested kaggle_score."""
    c = _sub(name, wr, at)
    c.kaggle_score = score
    return c


def test_description_carries_versioned_identity():
    cand = _evaluated("mega-x-heuristic", 0.55)
    desc = submission_description(cand, "abcd1234", exploratory=True)
    assert desc.startswith("mega-x-heuristic v1.0 - deck mega-x - agent heuristic")
    assert "local_wr 0.550 of 150" in desc and "abcd1234" in desc
    assert desc.endswith("factory [exploratory]")
    # Kaggle's CreateSubmission API 400s on these chars in -m (2026-07-11 incident,
    # ref 54585057 succeeded only after removing them from the template).
    assert "|" not in desc and "/" not in desc


def test_description_byte_identical_for_normal_candidate():
    """Pin the exact template output for a well-formed candidate. The harvest
    matcher and live Kaggle refs (54585057/54608104/54608114) depend on this
    exact format -- the New-2 charset sanitizer must be a no-op here."""
    cand = _evaluated("mega-lucario-fighting-heuristic", 0.512)
    desc = submission_description(cand, "abcd1234", exploratory=False)
    assert desc == (
        "mega-lucario-fighting-heuristic v1.0 - deck mega-x - agent heuristic "
        "- local_wr 0.512 of 150 - abcd1234 - factory"
    )


def test_description_sanitizes_pipe_and_slash_from_candidate_name():
    """New-2: candidate.name/version are free-form and interpolated raw into
    the template. A future name containing `|`/`/` must not re-trigger the
    Kaggle 400 -- the chokepoint sanitizer strips both from the whole string."""
    cand = _evaluated("mega|x/lucario", 0.55)
    desc = submission_description(cand, "abcd1234", exploratory=False)
    assert "|" not in desc and "/" not in desc


def test_description_reupload_marker_present():
    """Change 1: a guard-driven champion re-upload is distinguishable from a
    fresh submission in Kaggle's submission list. Same identity template,
    plus a trailing `re-upload` tag; sanitizer still applies to the whole
    final string (the tag itself contains no `|`/`/`, so it survives)."""
    cand = _evaluated("mega-lucario-fighting-heuristic", 0.512)
    desc = submission_description(cand, "abcd1234", exploratory=False,
                                  reupload=True)
    assert desc == (
        "mega-lucario-fighting-heuristic v1.0 - deck mega-x - agent heuristic "
        "- local_wr 0.512 of 150 - abcd1234 - factory re-upload"
    )
    assert "|" not in desc and "/" not in desc


def test_description_no_reupload_marker_by_default():
    """Fresh submissions stay byte-identical to today: the marker is strictly
    opt-in (the pin test above already locks the exact default string; this
    locks the absence of the tag by name)."""
    cand = _evaluated("mega-lucario-fighting-heuristic", 0.512)
    desc = submission_description(cand, "abcd1234", exploratory=False)
    assert "re-upload" not in desc


def test_description_matrix_promoted_candidate_no_local_wr():
    """T-crash: matrix-promoted candidates (QUEUED->EVALUATED via
    tournament.refresh_ratings, or evolution.snapshot_cell) legitimately carry
    local_wr=None. Formatting None with :.3f is the TypeError that silently
    killed every watch firing from 2026-07-21 ~07:00 (96+ crashes)."""
    cand = Candidate.create(name="mega-x-searchnet", version="v0.1",
                            deck="decks/mega-x.csv", agent_kind="search")
    cand.status = Status.EVALUATED
    cand.local_wr, cand.local_games = None, 0
    cand.matrix_rating, cand.matrix_games = 1.7835663229115948, 1130
    desc = submission_description(cand, "abcd1234", exploratory=False)
    assert "matrix 1.784 of 1130 games" in desc
    assert "|" not in desc and "/" not in desc


def test_description_reupload_with_none_local_wr():
    """Champion re-upload path (:266) must also survive a None local_wr --
    covers the champion-path shape, not just the challenger path (:207)."""
    cand = Candidate.create(name="mega-x-searchnet", version="v0.1",
                            deck="decks/mega-x.csv", agent_kind="search")
    cand.status = Status.EVALUATED
    cand.local_wr, cand.local_games = None, 0
    cand.matrix_rating, cand.matrix_games = 1.7835663229115948, 1130
    desc = submission_description(cand, "abcd1234", exploratory=False,
                                  reupload=True)
    assert desc.endswith(" re-upload")
    assert "|" not in desc and "/" not in desc


def test_description_never_evaluated_candidate():
    """No local eval and no matrix rating at all (freshly queued, never
    played a tournament block): falls through to a no-signal marker."""
    cand = Candidate.create(name="mega-x-fresh", version="v0.1",
                            deck="decks/mega-x.csv", agent_kind="heuristic")
    desc = submission_description(cand, "abcd1234", exploratory=False)
    assert "local_wr n-a of 0" in desc
    assert "|" not in desc and "/" not in desc


def test_description_anchor_wr_candidate():
    """T5 fix-round-1: tournament-scheduler baseline/probe candidates carry
    win-rate-vs-anchor on `Candidate.anchor_wr` (T5's `coverage.rating`,
    [0,1] scale) -- NOT `matrix_rating` (documented Bradley-Terry scale).
    The description must read honestly as anchor-wr evidence, never as
    "matrix" (which would misrepresent it as Bradley-Terry)."""
    cand = Candidate.create(name="tournament-champion", version="v1.0",
                            deck="decks/mega-x.csv", agent_kind="search-net")
    cand.anchor_wr, cand.anchor_games = 0.7, 10
    desc = submission_description(cand, "abcd1234", exploratory=False)
    assert "anchor-wr 0.700 of 10 games" in desc
    assert "matrix" not in desc
    assert "|" not in desc and "/" not in desc


def test_description_anchor_wr_yields_to_local_wr_and_matrix_rating():
    """anchor_wr is the LAST fallback: a candidate that also carries
    local_wr or matrix_rating must keep using those (pre-existing
    precedence, unchanged by this fix) -- anchor_wr only surfaces when
    neither of the other two scales is set, matching the tournament
    scheduler's ephemeral candidates (which never set the other two)."""
    with_local = Candidate.create(name="x", version="v1.0", deck="d.csv",
                                  agent_kind="heuristic")
    with_local.local_wr, with_local.local_games = 0.5, 150
    with_local.anchor_wr, with_local.anchor_games = 0.7, 10
    assert "local_wr 0.500 of 150" in submission_description(
        with_local, "abcd1234", exploratory=False)

    with_matrix = Candidate.create(name="y", version="v1.0", deck="d.csv",
                                   agent_kind="search-net")
    with_matrix.matrix_rating, with_matrix.matrix_games = 1.2, 500
    with_matrix.anchor_wr, with_matrix.anchor_games = 0.7, 10
    assert "matrix 1.200 of 500 games" in submission_description(
        with_matrix, "abcd1234", exploratory=False)


def _no_bundle(cand, out_dir):
    return Path(out_dir) / "submission.tar.gz"


def test_submit_default_today_uses_utc_anchor(tmp_path, monkeypatch):
    """When `today` isn't injected, submit_candidates must key the counter on
    gate.utc_today() (UTC calendar day), never a naive local date."""
    monkeypatch.setattr(submit_mod.gate_mod, "utc_today", lambda: "2099-01-01")
    cand = _evaluated("only", 0.55)
    client = FakeKaggleClient()
    counter = SubmissionCounter(tmp_path / "c.json")

    actions = submit_candidates([cand], client, counter, tmp_path, tmp_path,
                                today=None, build_fn=_no_bundle,
                                verify_fn=lambda *a: None, log=lambda m: None)

    assert actions[0][1] == "submitted"
    assert counter.today_count("2099-01-01") == 1  # keyed on the UTC helper's date


def test_submit_flow_dry_run_and_real(tmp_path):
    # No counted submissions -> edge (a): the sole challenger submits alone.
    cand = _evaluated("better", 0.55)
    client = FakeKaggleClient()
    counter = SubmissionCounter(tmp_path / "c.json")

    actions = submit_candidates([cand], client, counter, tmp_path, tmp_path,
                                no_submit=True, today="2026-07-12",
                                build_fn=_no_bundle, verify_fn=lambda *a: None,
                                log=lambda m: None)
    assert actions[0][1] == "dry-run"
    assert client.submitted == [] and cand.status is Status.EVALUATED

    actions = submit_candidates([cand], client, counter, tmp_path, tmp_path,
                                today="2026-07-12", build_fn=_no_bundle,
                                verify_fn=lambda *a: None, log=lambda m: None)
    assert actions[0][1] == "submitted"
    assert cand.status is Status.SUBMITTED and cand.submitted_at is not None
    assert counter.today_count("2026-07-12") == 1
    assert client.submitted[0][1].startswith("better v1.0")


def test_upload_retries_once_then_defers(tmp_path):
    # Edge (a) (no counted submissions) so the challenger-alone retry mechanic
    # is exercised without champion-pairing in the way.
    cand = _evaluated("better", 0.55)
    counter = SubmissionCounter(tmp_path / "c.json")

    class FlakyClient(FakeKaggleClient):
        def __init__(self, fail_times: int):
            super().__init__()
            self.fail_times = fail_times

        def submit(self, bundle, description):
            if self.fail_times > 0:
                self.fail_times -= 1
                raise RuntimeError("network")
            super().submit(bundle, description)

    ok_after_one = FlakyClient(1)
    actions = submit_candidates([cand], ok_after_one, counter, tmp_path,
                                tmp_path, today="2026-07-12", build_fn=_no_bundle,
                                verify_fn=lambda *a: None, log=lambda m: None)
    assert actions[0][1] == "submitted"  # first retry succeeded

    cand2 = _evaluated("better2", 0.60)
    always_fails = FlakyClient(99)
    actions = submit_candidates([cand2], always_fails, counter, tmp_path,
                                tmp_path, today="2026-07-12", build_fn=_no_bundle,
                                verify_fn=lambda *a: None, log=lambda m: None)
    assert actions[0][1] == "upload-failed"
    assert cand2.status is Status.EVALUATED  # deferred to next cycle, not lost
    assert "upload-failed" in cand2.notes


def test_bundle_build_failure_isolated_and_one_challenger_per_cycle(tmp_path):
    """A build_fn/verify_fn failure for the selected challenger must not crash
    the cycle -- it records a 'bundle-failed' action, status preserved. And the
    F1 one-challenger-per-cycle rule means the next-best candidate is DEFERRED
    (not promoted this cycle), so a build failure does not fall through to a
    second submission."""
    bad = _evaluated("bad-cand", 0.60)   # highest local_wr -> selected challenger
    good = _evaluated("good-cand", 0.55)
    client = FakeKaggleClient()
    counter = SubmissionCounter(tmp_path / "c.json")

    def flaky_build(cand, out_dir):
        if cand.name == "bad-cand":
            raise RuntimeError("bundle smoke failed")
        return Path(out_dir) / "submission.tar.gz"

    actions = submit_candidates([bad, good], client, counter, tmp_path,
                                tmp_path, today="2026-07-12",
                                build_fn=flaky_build,
                                verify_fn=lambda *a: None, log=lambda m: None)
    by_id = {cid: (act, detail) for cid, act, detail in actions}

    assert by_id[good.id][0] == "skip"           # deferred: one challenger/cycle
    assert "one challenger per cycle" in by_id[good.id][1]
    assert by_id[bad.id][0] == "bundle-failed"   # isolated, not crashed
    assert "bundle smoke failed" in by_id[bad.id][1]
    assert bad.status is Status.EVALUATED and good.status is Status.EVALUATED
    assert "bundle-failed" in bad.notes
    assert client.submitted == []                # nothing uploaded


def test_champion_paired_first_and_stays_counted(tmp_path):
    """Real 2026-07-14 ledger shape: two counted subs at the SAME timestamp,
    scores 630.8 (champion) and 522.1. A passing challenger must not evict the
    champion -- the champion is re-uploaded FIRST, then the challenger, so the
    champion identity stays inside the two-most-recent window."""
    champ = _scored("mega-starmie-water-searchnet", 0.4733,
                    "2026-07-12T02:42:00", 630.8)
    weak = _scored("mega-lucario-net", 0.5333, "2026-07-12T02:42:00", 522.1)
    challenger = _evaluated("challenger", 0.60)
    client = FakeKaggleClient()
    counter = SubmissionCounter(tmp_path / "c.json")

    actions = submit_candidates([champ, weak, challenger], client, counter,
                                tmp_path, tmp_path, today="2026-07-12",
                                build_fn=_no_bundle, verify_fn=lambda *a: None,
                                log=lambda m: None)

    # exactly two uploads, CHAMPION FIRST (the load-bearing ordering invariant)
    assert len(client.submitted) == 2
    assert client.submitted[0][1].startswith("mega-starmie-water-searchnet v1.0")
    assert client.submitted[1][1].startswith("challenger v1.0")
    # champion identity remains within the two-most-recent window; the weaker
    # counted slot (522.1) is the one that gets superseded.
    counted_ids = {c.id for c in counted_submissions([champ, weak, challenger])}
    assert champ.id in counted_ids and challenger.id in counted_ids
    assert weak.id not in counted_ids
    # re-submission traceable as the SAME identity, not a fake new version
    assert champ.version == "v1.0" and champ.kaggle_score == 630.8
    assert champ.last_resubmitted_at is not None  # re-submission trace recorded
    by_id = {cid: act for cid, act, _ in actions}
    assert by_id[champ.id] == "resubmitted-champion"
    assert by_id[challenger.id] == "submitted"
    assert counter.today_count("2026-07-12") == 2


def test_champion_reupload_description_carries_marker(tmp_path):
    """The champion re-upload path (submit.py champion-pairing guard) passes
    reupload=True; the challenger's fresh description does not. Setup mirrors
    test_champion_paired_first_and_stays_counted."""
    champ = _scored("mega-starmie-water-searchnet", 0.4733,
                    "2026-07-12T02:42:00", 630.8)
    weak = _scored("mega-lucario-net", 0.5333, "2026-07-12T02:42:00", 522.1)
    challenger = _evaluated("challenger", 0.60)
    client = FakeKaggleClient()
    counter = SubmissionCounter(tmp_path / "c.json")

    submit_candidates([champ, weak, challenger], client, counter,
                      tmp_path, tmp_path, today="2026-07-12",
                      build_fn=_no_bundle, verify_fn=lambda *a: None,
                      log=lambda m: None)

    assert len(client.submitted) == 2  # champion first, then challenger
    champ_desc = client.submitted[0][1]
    chall_desc = client.submitted[1][1]
    assert champ_desc.startswith("mega-starmie-water-searchnet v1.0")
    assert champ_desc.endswith("- factory re-upload")
    assert "re-upload" not in chall_desc


def test_champion_resubmit_preserves_authored_and_eval_notes(tmp_path):
    """Finding 2: the champion-protection re-upload must NOT wholesale-clobber
    the champion's authored notes + eval segment (the convention commit
    4cce492 deliberately preserves). The re-submission trace lives in the
    dedicated `last_resubmitted_at` field, not by overwriting notes."""
    champ = _scored("champ", 0.50, "2026-07-12T00:00:00", 600.0)
    champ.notes = "deck rationale + smoke ok | eval: mega-x: 90/150 (0.600)"
    weak = _scored("weak", 0.40, "2026-07-12T00:01:00", 500.0)
    challenger = _evaluated("challenger", 0.70)
    client = FakeKaggleClient()
    counter = SubmissionCounter(tmp_path / "c.json")

    submit_candidates([champ, weak, challenger], client, counter, tmp_path,
                      tmp_path, today="2026-07-12", build_fn=_no_bundle,
                      verify_fn=lambda *a: None, log=lambda m: None)

    # authored notes + eval segment survive byte-for-byte
    assert champ.notes == "deck rationale + smoke ok | eval: mega-x: 90/150 (0.600)"
    # the re-submission trace is recorded in its own field (present, ISO-ish)
    assert champ.last_resubmitted_at == champ.submitted_at
    assert champ.last_resubmitted_at is not None


def test_champion_repeat_resubmit_replaces_not_stacks_trace(tmp_path):
    """Re-submitting the champion across two cycles must REPLACE the trace, not
    stack it -- notes stay clean and last_resubmitted_at reflects the latest."""
    champ = _scored("champ", 0.50, "2026-07-12T00:00:00", 600.0)
    champ.notes = "authored only"
    weak = _scored("weak", 0.40, "2026-07-12T00:01:00", 500.0)
    client = FakeKaggleClient()
    counter = SubmissionCounter(tmp_path / "c.json")

    submit_candidates([champ, weak, _evaluated("chal1", 0.70)], client, counter,
                      tmp_path, tmp_path, today="2026-07-12", build_fn=_no_bundle,
                      verify_fn=lambda *a: None, log=lambda m: None)
    first_trace = champ.last_resubmitted_at
    submit_candidates([champ, weak, _evaluated("chal2", 0.70)], client, counter,
                      tmp_path, tmp_path, today="2026-07-12", build_fn=_no_bundle,
                      verify_fn=lambda *a: None, log=lambda m: None)

    assert champ.notes == "authored only"           # no accumulation
    assert first_trace is not None
    assert champ.last_resubmitted_at is not None     # latest trace present


def test_failure_path_preserves_authored_notes(tmp_path):
    """bundle-failed/upload-failed note assignments must preserve authored
    notes (same clobber class, F3 finding 2 cleanup)."""
    cand = _evaluated("solo", 0.55)  # edge (a): no counted subs -> alone
    cand.notes = "hand-authored rationale"
    client = FakeKaggleClient()
    counter = SubmissionCounter(tmp_path / "c.json")

    def bad_build(c, out_dir):
        raise RuntimeError("bundle smoke failed")

    submit_candidates([cand], client, counter, tmp_path, tmp_path,
                      today="2026-07-12", build_fn=bad_build,
                      verify_fn=lambda *a: None, log=lambda m: None)

    assert cand.notes.startswith("hand-authored rationale")
    assert "bundle-failed" in cand.notes


def test_novel_axis_parity_challenger_is_paired_not_unpaired(tmp_path):
    """A novel-axis parity-band challenger clears the gate via the exploration
    exception; with a champion present it must still be PAIRED (champion first),
    never submitted alone -> it cannot evict the champion."""
    champ = _scored("champ", 0.50, "2026-07-12T00:00:00", 600.0)
    weak = _scored("weak", 0.50, "2026-07-12T00:01:00", 500.0)
    novel = _evaluated("novel", 0.49, novel=True)  # -0.01 within 0.02 parity band
    client = FakeKaggleClient()
    counter = SubmissionCounter(tmp_path / "c.json")

    submit_candidates([champ, weak, novel], client, counter, tmp_path, tmp_path,
                      today="2026-07-12", build_fn=_no_bundle,
                      verify_fn=lambda *a: None, log=lambda m: None)

    assert len(client.submitted) == 2                       # paired, not lone
    assert client.submitted[0][1].startswith("champ v1.0")  # champion first
    assert client.submitted[1][1].startswith("novel v1.0")
    counted_ids = {c.id for c in counted_submissions([champ, weak, novel])}
    assert champ.id in counted_ids                          # champion protected


def test_budget_boundary_defers_when_pair_would_split(tmp_path):
    """remaining daily budget = 1 and a champion exists -> zero submissions.
    A pair needs 2 slots and must never be split across the hard-cap boundary."""
    champ = _scored("champ", 0.50, "2026-07-12T00:00:00", 600.0)
    weak = _scored("weak", 0.40, "2026-07-12T00:01:00", 500.0)
    challenger = _evaluated("challenger", 0.70)  # big edge -> clears cadence
    client = FakeKaggleClient()
    counter = SubmissionCounter(tmp_path / "c.json")
    for _ in range(HARD_DAILY_CAP - 1):          # used=4 -> remaining=1
        counter.record("2026-07-12")

    actions = submit_candidates([champ, weak, challenger], client, counter,
                                tmp_path, tmp_path, today="2026-07-12",
                                build_fn=_no_bundle, verify_fn=lambda *a: None,
                                log=lambda m: None)

    assert client.submitted == []                            # zero submissions
    assert counter.today_count("2026-07-12") == 4            # counter unchanged
    skip_reasons = [d for _, a, d in actions if a == "skip"]
    assert any("champion-pairing needs 2 slots" in r for r in skip_reasons)


def test_pre_harvest_counted_without_score_defers(tmp_path):
    """Counted slots exist but none has a harvested kaggle_score yet -> champion
    unidentifiable -> submit NOTHING, reason logged."""
    inc1 = _sub("inc1", 0.50, "2026-07-12T00:00:00")  # SUBMITTED, no kaggle_score
    inc2 = _sub("inc2", 0.48, "2026-07-12T00:01:00")
    challenger = _evaluated("challenger", 0.60)
    client = FakeKaggleClient()
    counter = SubmissionCounter(tmp_path / "c.json")
    logs: list[str] = []

    actions = submit_candidates([inc1, inc2, challenger], client, counter,
                                tmp_path, tmp_path, today="2026-07-12",
                                build_fn=_no_bundle, verify_fn=lambda *a: None,
                                log=logs.append)

    assert client.submitted == []
    assert challenger.status is Status.EVALUATED
    reason = next(d for cid, a, d in actions
                  if cid == challenger.id and a == "skip")
    assert "champion unidentifiable" in reason
    assert any("champion unidentifiable" in m for m in logs)


def test_no_counted_submissions_challenger_submits_alone(tmp_path):
    """Edge (a): nothing counted -> the sole (highest-WR) challenger submits
    alone (max 1); a second gate-passer is deferred, not submitted."""
    a = _evaluated("a", 0.60)
    b = _evaluated("b", 0.55)
    client = FakeKaggleClient()
    counter = SubmissionCounter(tmp_path / "c.json")

    actions = submit_candidates([a, b], client, counter, tmp_path, tmp_path,
                                today="2026-07-12", build_fn=_no_bundle,
                                verify_fn=lambda *a: None, log=lambda m: None)

    assert len(client.submitted) == 1
    assert client.submitted[0][1].startswith("a v1.0")   # highest local_wr
    assert a.status is Status.SUBMITTED
    assert b.status is Status.EVALUATED                  # deferred (one/cycle)
    assert counter.today_count("2026-07-12") == 1
    by_id = {cid: act for cid, act, _ in actions}
    assert by_id[a.id] == "submitted" and by_id[b.id] == "skip"


def test_auth_dead_skips_submit_phase_and_reserves_nothing(tmp_path):
    """Pre-submit auth guard (real bug 2026-07-20: Kaggle OAuth token expired
    mid-cycle, submit crashed with a cryptic RuntimeError at upload time).
    A dead-auth probe must skip the ENTIRE submit phase before any counter
    reservation, never call submit(), and log a loud AUTH-DEAD line."""
    cand = _evaluated("challenger", 0.95)  # would clearly pass the gate
    counter = SubmissionCounter(tmp_path / "c.json")
    logs: list[str] = []

    class DeadAuthClient:
        def list_submissions(self):
            raise RuntimeError("kaggle CLI failed (1): "
                               "Authentication required to call the Kaggle API.")

        def submit(self, bundle, description):
            raise AssertionError("must not upload when auth is dead")

    actions = submit_candidates(
        [cand], DeadAuthClient(), counter, tmp_path, tmp_path,
        today="2026-07-12", build_fn=lambda c, o: Path(o) / "b.tar.gz",
        verify_fn=lambda *a: None, log=logs.append)

    assert counter.today_count("2026-07-12") == 0  # zero reservations consumed
    assert cand.status is Status.EVALUATED  # untouched - never gated at all
    assert any(m.startswith("AUTH-DEAD:") for m in logs), logs
    assert any(a[0] == "AUTH" and a[1] == "auth-dead" for a in actions), actions


def test_auth_healthy_behavior_unchanged(tmp_path):
    """Auth-alive path: the probe succeeds (FakeKaggleClient.list_submissions
    works) and submission proceeds exactly as before - bit-identical."""
    cand = _evaluated("solo", 0.55)
    counter = SubmissionCounter(tmp_path / "c.json")
    logs: list[str] = []

    actions = submit_candidates(
        [cand], FakeKaggleClient(), counter, tmp_path, tmp_path,
        today="2026-07-12", build_fn=lambda c, o: Path(o) / "b.tar.gz",
        verify_fn=lambda *a: None, log=logs.append)

    assert counter.today_count("2026-07-12") == 1
    assert cand.status is Status.SUBMITTED
    assert not any(m.startswith("AUTH-DEAD:") for m in logs), logs
    assert any(a[1] == "submitted" for a in actions), actions


def test_auth_check_skipped_entirely_in_dry_run(tmp_path):
    """no_submit=True never attempts a real submission, so the probe must
    not fire even against a client whose list_submissions() raises - dry-run
    behavior stays byte-identical (existing tests rely on this)."""
    cand = _evaluated("challenger", 0.95)
    counter = SubmissionCounter(tmp_path / "c.json")

    class DeadAuthClient:
        def list_submissions(self):
            raise RuntimeError("offline")

        def submit(self, bundle, description):
            raise AssertionError("dry-run must never call submit")

    actions = submit_candidates(
        [cand], DeadAuthClient(), counter, tmp_path, tmp_path,
        no_submit=True, today="2026-07-12",
        build_fn=lambda c, o: Path(o) / "b.tar.gz",
        verify_fn=lambda *a: None, log=lambda m: None)

    assert any(a[1] == "dry-run" for a in actions), actions  # proceeded normally


def test_ranking_uses_effective_score_matrix_and_local_wr_are_different_scales(tmp_path):
    """Documents a known scale-mismatch in the ranking order (Task 8 brief,
    .superpowers/sdd/task-8-brief.md interfaces note: 'the two scales never
    compare against each other, see decide rule'). Bradley-Terry matrix_rating
    is geometric-mean-normalized around 1.0 (tournament.fit_ratings), NOT the
    0-1 win-rate scale local_wr lives on - so a matrix-covered candidate with
    rating > 1.0 outranks a local_wr-only candidate here even when that
    candidate's local_wr is far higher (0.95). This is safe in practice
    because the actual submit/no-submit DECISION (gate.decide) only ever
    compares two candidates pairwise and requires BOTH to have matrix
    coverage before using matrix_rating - the cross-scale mixing visible in
    this total ranking order never by itself flips a submit outcome; it only
    controls which EVALUATED candidate is offered to the gate first."""
    matrix_cand = _evaluated("matrix-cand", wr=0.10)  # low local_wr
    matrix_cand.matrix_rating = 1.30  # BT-scale, normalized around 1.0
    matrix_cand.matrix_games = 15
    matrix_cand.matrix_opponents = 8
    local_cand = _evaluated("local-cand", wr=0.95)  # high local_wr, no matrix coverage
    client = FakeKaggleClient()
    counter = SubmissionCounter(tmp_path / "c.json")

    actions = submit_candidates([local_cand, matrix_cand], client, counter,
                                tmp_path, tmp_path, today="2026-07-12",
                                build_fn=_no_bundle, verify_fn=lambda *a: None,
                                log=lambda m: None)

    assert len(client.submitted) == 1
    # matrix_cand ranks first (1.30 > 0.95) despite local_wr 0.10 < 0.95.
    assert client.submitted[0][1].startswith("matrix-cand v1.0")
    assert matrix_cand.status is Status.SUBMITTED
    assert local_cand.status is Status.EVALUATED  # deferred (one/cycle)


def test_submission_description_extra_segment_and_none_default():
    """Counted-pair-protection design 6a: `extra` (default None) appends one
    additional evidence segment after the merit segment. Default-None stays
    byte-identical to the pre-slice format; the sanitization chokepoint
    covers the extra segment's own text too."""
    cand = Candidate.create(name="tournament-champion", version="v0.2",
                            deck="build/d.csv", agent_kind="search-net")
    cand.anchor_wr, cand.anchor_games = 0.61, 200
    base = submission_description(cand, "abc12345", exploratory=False)
    with_extra = submission_description(cand, "abc12345", exploratory=False,
                                        extra="pair-gate 0.610 vs v0.1")
    # extra=None stays byte-identical to the pre-slice format
    assert " - pair-gate" not in base
    assert " - anchor-wr 0.610 of 200 games - pair-gate 0.610 vs v0.1 - " \
        in with_extra
    # sanitization chokepoint covers the extra segment too
    dirty = submission_description(cand, "abc12345", exploratory=False,
                                   extra="pair-gate 0.610 vs a|b/c")
    assert "|" not in dirty and "/" not in dirty
