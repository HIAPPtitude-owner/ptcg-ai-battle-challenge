"""Tests for the submission scheduler -- 4.8h marks + 24h daily-floor probe
(tournament T17). Champion-pairing guard STAYS RETIRED: `maybe_submit`
uploads exactly ONE identity per call, never a protective re-upload pair.

No real Kaggle calls anywhere in this file -- every test injects
`kaggle_client.FakeKaggleClient` (or a local raising stub for the auth-dead
path) plus fake `build_fn`/`verify_fn` doubles, mirroring the convention
already established in `tests/test_factory_submit.py`.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from ptcg.factory import anchor, deckdb, loop, loop_state, subscheduler
from ptcg.factory.gate import SubmissionCounter
from ptcg.factory.kaggle_client import FakeKaggleClient, SubmissionRow

_UTC = dt.timezone.utc


def _fake_build(cand, out_dir) -> Path:
    p = Path(out_dir) / f"{cand.id}.tar.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"fake-bundle")
    return p


def _fake_verify(cand, tar_path, staging_dir) -> None:
    return None


def _seed_founding(tmp_path: Path, n_active: int = 1) -> object:
    """A deckdb with a founding `v0.1` baseline on `dBase` (single-card decks
    -- deck legality is not under test here), plus `n_active` OTHER active,
    rated concepts (`cProbe000`, `cProbe001`, ...) available as daily-floor
    probe candidates. Mirrors the `_seed_founding`/`_seed` fixture convention
    already established in `tests/test_factory_loop_train.py` /
    `tests/test_factory_loop_match.py`."""
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)

    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cBase','[\"Z\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dBase','cBase','[101]')")
        c.execute(
            "INSERT INTO coverage(concept_id,games_played,distinct_opponents,rating) "
            "VALUES('cBase',15,1,500.0)"
        )
        for i in range(n_active):
            cid, did = f"cProbe{i:03d}", f"dProbe{i:03d}"
            c.execute("INSERT INTO concepts(id,cores,status) VALUES(?,?,'active')", (cid, '["X"]'))
            c.execute("INSERT INTO decks(id,concept_id,cards) VALUES(?,?,?)", (did, cid, "[201]"))
            c.execute(
                "INSERT INTO coverage(concept_id,games_played,distinct_opponents,rating) "
                "VALUES(?,15,1,?)",
                (cid, float(900 - i)),  # below cBase(500)? no -- above, so ranked first
            )

    deckdb._write(db, _s)
    loop_state.set_founding_baseline(db, "dBase", loop.FOUNDING_AGENT_CONFIG)
    return db


def test_mark_index_five_per_day():
    assert subscheduler.MARK_SECONDS == 17280  # 4.8*3600
    assert 86400 // subscheduler.MARK_SECONDS == 5  # exactly 5 marks/day
    base = dt.datetime(2026, 8, 1, tzinfo=_UTC)
    assert [subscheduler.mark_index(base.replace(hour=h, minute=m))
           for h, m in [(0, 0), (4, 48), (9, 36), (14, 24), (19, 12)]] == [0, 1, 2, 3, 4]


def test_no_baseline_founded_yet_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)
    counter = SubmissionCounter(tmp_path / "counter.json")
    client = FakeKaggleClient()
    now = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)

    result = subscheduler.maybe_submit(
        db, client, counter, tmp_path / "state.json", tmp_path / "out", tmp_path, now,
        build_fn=_fake_build, verify_fn=_fake_verify)

    assert result == []
    assert client.submitted == []


def test_baseline_and_probe_candidates_use_anchor_wr_not_matrix_rating(tmp_path, monkeypatch):
    """T5 fix-round-1: `coverage.rating` (win-rate-vs-anchor, [0,1] scale)
    must land on `Candidate.anchor_wr`, never the BT-documented
    `matrix_rating`/`matrix_games` -- both call sites, both fields."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path, n_active=1)
    baseline = loop_state.current_baseline(db)

    cand = subscheduler._baseline_candidate(db, baseline)
    assert cand.anchor_wr == 500.0
    assert cand.matrix_rating is None
    assert cand.matrix_games == 0

    deck_row = subscheduler._next_unprobed_deck(db, {"cBase"})
    probe = subscheduler._probe_candidate(db, baseline, deck_row)
    assert probe.anchor_wr == 900.0
    assert probe.matrix_rating is None
    assert probe.matrix_games == 0


def _seed_games(db, deck_id: str, *, screening: int, other: int) -> None:
    """`screening` done anchor-screening games for `deck_id` (the rows
    `coverage.rating` is computed from) plus `other` done games of other
    purposes for the same deck (rows `coverage.games_played` counts but
    `rating` does not)."""
    def _s(c):
        c.execute("INSERT OR IGNORE INTO concepts(id,cores,status) "
                  "VALUES(?,'[\"anchor\"]','finalist')", (anchor.ANCHOR_CONCEPT_ID,))
        c.execute("INSERT OR IGNORE INTO decks(id,concept_id,cards) VALUES(?,?,'[1]')",
                  (anchor.ANCHOR_DECK_ID, anchor.ANCHOR_CONCEPT_ID))
        for _ in range(screening):
            c.execute(
                "INSERT INTO games(deck_a_id,deck_b_id,agent_version_a,agent_version_b,"
                "purpose,winner,status) VALUES(?,?,'v0.1',?,'screening',0,'done')",
                (deck_id, anchor.ANCHOR_DECK_ID, anchor.ANCHOR_VERSION))
        for _ in range(other):
            c.execute(
                "INSERT INTO games(deck_a_id,deck_b_id,agent_version_a,agent_version_b,"
                "purpose,winner,status) VALUES(?,?,'v0.1','v0.1','match',0,'done')",
                (deck_id, deck_id))
    deckdb._write(db, _s)


def test_anchor_games_counts_anchor_wr_denominator_not_all_purposes(tmp_path, monkeypatch):
    """`anchor_wr` is `coverage.rating` = wins/n over DONE anchor-SCREENING
    games only (`rating._ANCHOR_SCREENING_QUERY`), while
    `coverage.games_played` counts every done game on either side. Reporting
    the latter as `anchor_games` overstates the evidence in the submission
    description ("anchor-wr 0.480 of 1,850 games")."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path, n_active=1)
    _seed_games(db, "dBase", screening=12, other=40)
    _seed_games(db, "dProbe000", screening=7, other=99)
    # coverage.games_played (the WRONG denominator) is the seeded 15 for both.
    assert db.execute(
        "SELECT games_played FROM coverage WHERE concept_id='cBase'").fetchone()[0] == 15

    baseline = loop_state.current_baseline(db)
    assert subscheduler._baseline_candidate(db, baseline).anchor_games == 12
    deck_row = subscheduler._next_unprobed_deck(db, {"cBase"})
    assert subscheduler._probe_candidate(db, baseline, deck_row).anchor_games == 7


def test_baseline_upload_description_labels_anchor_wr_not_matrix(tmp_path, monkeypatch):
    """End-to-end: the Kaggle submission description text itself must read
    honestly as anchor-wr evidence, never "matrix" (T5 fix-round-1)."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    # 15 real anchor-screening games -- `anchor_games` is counted from these
    # rows (anchor_wr's own denominator), not from `coverage.games_played`.
    _seed_games(db, "dBase", screening=15, other=40)
    _gate_pass(db, "v0.1")
    counter = SubmissionCounter(tmp_path / "counter.json")
    client = FakeKaggleClient()
    now = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)

    subscheduler.maybe_submit(
        db, client, counter, tmp_path / "state.json", tmp_path / "out", tmp_path, now,
        build_fn=_fake_build, verify_fn=_fake_verify)

    assert len(client.submitted) == 1
    desc = client.submitted[0][1]
    assert "anchor-wr 0.600 of 200 games" in desc
    assert "matrix" not in desc


def test_baseline_candidate_prefers_settled_anchor_check_evidence(tmp_path, monkeypatch):
    """Counted-pair-protection design 6a: the description's merit evidence
    is the 200-game anchor-check verdict's own wr/denominator, not the
    15-game census screening rating (`coverage.rating`). Fallback to the
    screening figures remains only when no settled check exists (covered
    by the existing tests above, which seed no anchor_checks row)."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    _gate_pass(db, "v0.1")  # settled: wr=0.6, games_done=200
    baseline = loop_state.current_baseline(db)
    cand = subscheduler._baseline_candidate(db, baseline)
    assert cand.anchor_wr == 0.6
    assert cand.anchor_games == 200


def test_first_call_uploads_baseline_as_mark_triggered(tmp_path, monkeypatch):
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    _gate_pass(db, "v0.1")  # gate must not block what this test exercises
    counter = SubmissionCounter(tmp_path / "counter.json")
    client = FakeKaggleClient()
    state_path = tmp_path / "state.json"
    now = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)

    result = subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now,
        build_fn=_fake_build, verify_fn=_fake_verify)

    assert len(result) == 1
    cand_id, outcome, detail = result[0]
    assert outcome == "submitted"
    assert cand_id == "tournament-champion-v0.1"
    assert len(client.submitted) == 1
    assert client.submitted[0][1].startswith("tournament-champion v0.1")
    assert counter.today_count("2026-08-01") == 1

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_uploaded_identity"] == "v0.1"
    assert state["last_mark_fired"] == ["2026-08-01", 0]
    assert state["last_upload_at"] is not None


def test_mark_fires_only_on_baseline_change(tmp_path, monkeypatch):
    """Unchanged identity at a NEW mark -> no upload (per plan test name)."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    _gate_pass(db, "v0.1")  # gate must not block what this test exercises
    counter = SubmissionCounter(tmp_path / "counter.json")
    client = FakeKaggleClient()
    state_path = tmp_path / "state.json"
    now0 = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)

    first = subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now0,
        build_fn=_fake_build, verify_fn=_fake_verify)
    assert first[0][1] == "submitted"

    # Advance to the NEXT mark (idx 0 -> idx 1, same day). Baseline is still
    # v0.1 (no crown happened) -> mark-triggered must NOT re-fire, and <24h
    # has elapsed since the first upload -> daily floor must not fire either.
    now1 = now0 + dt.timedelta(hours=4.8)
    second = subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now1,
        build_fn=_fake_build, verify_fn=_fake_verify)

    assert second == []
    assert len(client.submitted) == 1  # no second upload
    assert counter.today_count("2026-08-01") == 1


def test_daily_floor_probe_after_24h_idle(tmp_path, monkeypatch):
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    monkeypatch.setattr(subscheduler, "DAILY_FLOOR_PROBE_ENABLED", True)
    db = _seed_founding(tmp_path, n_active=1)
    _gate_pass(db, "v0.1")  # gate must not block what this test exercises
    counter = SubmissionCounter(tmp_path / "counter.json")
    client = FakeKaggleClient()
    state_path = tmp_path / "state.json"
    now0 = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)

    first = subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now0,
        build_fn=_fake_build, verify_fn=_fake_verify)
    assert first[0][1] == "submitted"

    # Baseline unchanged (no crown) but >=24h idle -> daily-floor PROBE fires,
    # regardless of mark-index arithmetic (mark-triggered condition requires
    # baseline_changed too, which is False here).
    now1 = now0 + dt.timedelta(hours=25)
    second = subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now1,
        build_fn=_fake_build, verify_fn=_fake_verify)

    assert len(second) == 1
    cand_id, outcome, detail = second[0]
    assert outcome == "submitted"
    assert cand_id == "tournament-probe-cProbe000-v0.1"
    assert len(client.submitted) == 2
    desc = client.submitted[1][1]
    assert desc.startswith("tournament-probe-cProbe000 v0.1")  # baseline version tag + concept id
    assert counter.today_count("2026-08-02") == 1  # new UTC day

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_uploaded_identity"] == "v0.1"  # unchanged -- NOT a crown upload
    assert state["probed_concept_ids"] == ["cProbe000"]


def test_daily_floor_probe_excludes_baseline_own_deck(tmp_path, monkeypatch):
    """If the baseline's own concept happens to be the best-rated active deck,
    the probe must not re-upload it (no calibration value)."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    monkeypatch.setattr(subscheduler, "DAILY_FLOOR_PROBE_ENABLED", True)
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)

    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cBase','[\"Z\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dBase','cBase','[101]')")
        c.execute(
            "INSERT INTO coverage(concept_id,games_played,distinct_opponents,rating) "
            "VALUES('cBase',15,1,999.0)"  # best-rated -- would win the query if not excluded
        )
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cOther','[\"X\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dOther','cOther','[201]')")
        c.execute(
            "INSERT INTO coverage(concept_id,games_played,distinct_opponents,rating) "
            "VALUES('cOther',15,1,100.0)"
        )

    deckdb._write(db, _s)
    loop_state.set_founding_baseline(db, "dBase", loop.FOUNDING_AGENT_CONFIG)
    _gate_pass(db, "v0.1")  # gate must not block what this test exercises

    counter = SubmissionCounter(tmp_path / "counter.json")
    client = FakeKaggleClient()
    state_path = tmp_path / "state.json"
    now0 = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)
    subscheduler.maybe_submit(db, client, counter, state_path, tmp_path / "out", tmp_path, now0,
                              build_fn=_fake_build, verify_fn=_fake_verify)

    now1 = now0 + dt.timedelta(hours=25)
    second = subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now1,
        build_fn=_fake_build, verify_fn=_fake_verify)

    assert second[0][1] == "submitted"
    assert second[0][0] == "tournament-probe-cOther-v0.1"  # not cBase


def test_daily_floor_no_unprobed_deck_available_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    monkeypatch.setattr(subscheduler, "DAILY_FLOOR_PROBE_ENABLED", True)
    db = _seed_founding(tmp_path, n_active=0)  # only cBase exists
    _gate_pass(db, "v0.1")  # gate must not block what this test exercises
    counter = SubmissionCounter(tmp_path / "counter.json")
    client = FakeKaggleClient()
    state_path = tmp_path / "state.json"
    now0 = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)
    subscheduler.maybe_submit(db, client, counter, state_path, tmp_path / "out", tmp_path, now0,
                              build_fn=_fake_build, verify_fn=_fake_verify)

    now1 = now0 + dt.timedelta(hours=25)
    second = subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now1,
        build_fn=_fake_build, verify_fn=_fake_verify)

    assert second == []
    assert len(client.submitted) == 1  # only the first mark upload


def test_auth_dead_skips_entire_phase_no_reservation(tmp_path, monkeypatch):
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    counter = SubmissionCounter(tmp_path / "counter.json")
    state_path = tmp_path / "state.json"
    now0 = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)
    logs: list[str] = []

    class DeadAuthClient:
        def list_submissions(self):
            raise RuntimeError("kaggle CLI failed (1): "
                              "Authentication required to call the Kaggle API.")

        def submit(self, bundle, description):
            raise AssertionError("must not upload when auth is dead")

    result = subscheduler.maybe_submit(
        db, DeadAuthClient(), counter, state_path, tmp_path / "out", tmp_path, now0,
        build_fn=_fake_build, verify_fn=_fake_verify, log=logs.append)

    assert result == [("AUTH", "auth-dead", (
        "kaggle CLI failed (1): Authentication required to call the Kaggle API."))]
    assert counter.today_count("2026-08-01") == 0  # zero reservations consumed
    assert not state_path.exists()  # scheduler state untouched
    assert any(m.startswith("AUTH-DEAD:") for m in logs), logs


def test_no_submit_dry_run_never_touches_counter_or_state(tmp_path, monkeypatch):
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    _gate_pass(db, "v0.1")  # gate must not block what this test exercises
    counter = SubmissionCounter(tmp_path / "counter.json")
    state_path = tmp_path / "state.json"
    now0 = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)

    class DeadAuthClient:
        def list_submissions(self):
            raise RuntimeError("offline")

        def submit(self, bundle, description):
            raise AssertionError("dry-run must never call submit")

    result = subscheduler.maybe_submit(
        db, DeadAuthClient(), counter, state_path, tmp_path / "out", tmp_path, now0,
        no_submit=True, build_fn=_fake_build, verify_fn=_fake_verify)

    assert len(result) == 1
    assert result[0][1] == "dry-run"
    assert counter.today_count("2026-08-01") == 0  # never touched
    assert not state_path.exists()  # never touched


def test_cap_reservation_atomic_under_concurrent_entry(tmp_path, monkeypatch):
    """ADVERSARIAL (Pattern INTERLEAVED-TEST, reuse 178043b pattern): two
    maybe_submit entrants racing from count=4 (cap=5, 1 slot left) must not
    both admit. Separate connections/counters per thread, per
    `.claude/rules/single-actor-worker-tests.md` -- sqlite3 connections are
    not thread-safe to share.

    `_materialize_deck_csv` is stubbed to a fixed path with no real write:
    two threads writing the SAME real CSV path concurrently would race on
    Windows file locking (`PermissionError`) for a reason unrelated to what
    this test actually checks (`SubmissionCounter` atomicity through
    `maybe_submit`) -- the fake `build_fn`/`verify_fn` never read the deck
    path's contents, so no real file is needed here.
    """
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    monkeypatch.setattr(subscheduler, "_materialize_deck_csv",
                        lambda conn, deck_id: tmp_path / "generated" / f"{deck_id}.csv")
    db_path = tmp_path / "t.db"
    seed_db = _seed_founding(tmp_path)
    _gate_pass(seed_db, "v0.1")  # gate must not block what this test exercises
    seed_db.close()

    counter_path = tmp_path / "counter.json"
    seed_counter = SubmissionCounter(counter_path)
    now = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)
    today = now.date().isoformat()
    assert seed_counter.try_reserve(today, 4)  # pre-fill 4/5 -- 1 slot left

    state_path = tmp_path / "state.json"
    results: list[list] = []
    barrier = threading.Barrier(2)

    def _entrant():
        conn = deckdb.connect(db_path)
        counter = SubmissionCounter(counter_path)
        client = FakeKaggleClient()
        barrier.wait()
        r = subscheduler.maybe_submit(
            conn, client, counter, state_path, tmp_path / "out", tmp_path, now,
            build_fn=_fake_build, verify_fn=_fake_verify)
        results.append(r)

    threads = [threading.Thread(target=_entrant) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    submitted = [r for res in results for r in res if r[1] == "submitted"]
    assert len(submitted) <= 1  # at most 1 of the 2 entrants admitted
    final = SubmissionCounter(counter_path)
    assert final.today_count(today) <= 5  # never exceeded HARD_DAILY_CAP


def test_terminal_marker_on_upload_noop_and_auth_dead_paths(tmp_path, monkeypatch):
    """TERMINAL LOG MARKER (Phase-1-close carry-forward, terminal-marker
    section of `.claude/rules/factory-task-scheduler-liveness.md`): every
    normal exit path of `maybe_submit` -- upload, no-op, auth-dead -- must
    emit exactly one terminal `submit-scheduler:` line. Missing markers on
    invocations are the 36-hour-silent-crash-cascade signature."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    _gate_pass(db, "v0.1")  # gate must not block what this test exercises
    counter = SubmissionCounter(tmp_path / "counter.json")
    state_path = tmp_path / "state.json"
    now0 = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)

    # Path 1: upload (mark-triggered success).
    logs: list[str] = []
    subscheduler.maybe_submit(
        db, FakeKaggleClient(), counter, state_path, tmp_path / "out", tmp_path, now0,
        build_fn=_fake_build, verify_fn=_fake_verify, log=logs.append)
    markers = [m for m in logs if m.startswith("submit-scheduler:")]
    assert markers == ["submit-scheduler: tournament-champion-v0.1=submitted"], logs

    # Path 2: no-op (same mark, baseline unchanged, not idle).
    logs.clear()
    subscheduler.maybe_submit(
        db, FakeKaggleClient(), counter, state_path, tmp_path / "out", tmp_path,
        now0 + dt.timedelta(minutes=5),
        build_fn=_fake_build, verify_fn=_fake_verify, log=logs.append)
    assert [m for m in logs if m.startswith("submit-scheduler:")] == [
        "submit-scheduler: no-op"
    ], logs

    # Path 3: auth-dead short-circuit.
    class DeadAuthClient:
        def list_submissions(self):
            raise RuntimeError("auth expired")

        def submit(self, bundle, description):
            raise AssertionError("must not upload when auth is dead")

    logs.clear()
    subscheduler.maybe_submit(
        db, DeadAuthClient(), counter, state_path, tmp_path / "out", tmp_path, now0,
        build_fn=_fake_build, verify_fn=_fake_verify, log=logs.append)
    assert [m for m in logs if m.startswith("submit-scheduler:")] == [
        "submit-scheduler: AUTH=auth-dead"
    ], logs


def test_terminal_error_marker_on_unhandled_exception(tmp_path, monkeypatch):
    """The error path must ALSO emit a terminal marker (`submit-scheduler-error:`)
    and re-raise -- guaranteed by try/except wrapping, per the dispatch's
    binding carry-forward (the legacy SUBMIT_HOLD path's missing marker is
    the class that caused the 36-hour silent crash cascade)."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    _gate_pass(db, "v0.1")  # gate must not block what this test exercises
    counter = SubmissionCounter(tmp_path / "counter.json")
    state_path = tmp_path / "state.json"
    state_path.write_text("{ not valid json", encoding="utf-8")  # corrupt state file
    now0 = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)
    logs: list[str] = []

    with pytest.raises(json.JSONDecodeError):
        subscheduler.maybe_submit(
            db, FakeKaggleClient(), counter, state_path, tmp_path / "out", tmp_path, now0,
            build_fn=_fake_build, verify_fn=_fake_verify, log=logs.append)

    error_markers = [m for m in logs if m.startswith("submit-scheduler-error:")]
    assert len(error_markers) == 1, logs
    assert not any(m.startswith("submit-scheduler:") for m in logs), logs  # error, not success


def test_state_file_created_against_never_created_parent_dir(tmp_path, monkeypatch):
    """VIRGIN-DIR-TEST (Pattern VIRGIN-DIR-TEST): the scheduler state file's
    parent directory does not exist yet -- Pattern ATOMIC-JSON's mkdir must
    create it, not crash on the first-ever write."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    _gate_pass(db, "v0.1")  # gate must not block what this test exercises
    counter = SubmissionCounter(tmp_path / "counter.json")
    client = FakeKaggleClient()
    state_path = tmp_path / "never" / "created" / "here" / "state.json"
    assert not state_path.parent.exists()
    now = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)

    result = subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now,
        build_fn=_fake_build, verify_fn=_fake_verify)

    assert result[0][1] == "submitted"
    assert state_path.exists()


# --- Anchor-gate tests (strength gate T5): every upload path (mark-trigger
# AND daily-floor probe) must block on any verdict other than 'pass'. Four
# evidence shapes are first-class per `.claude/rules/provenance-shaped-
# optional-fields.md` (absent/pending/fail/pass), plus one test proving the
# probe path is gated identically to the mark-trigger path.


def _gate_pass(conn: sqlite3.Connection, version: str) -> None:
    anchor._ensure_schema(conn)
    conn.execute(
        "INSERT INTO anchor_checks(version, offspring_id, deck_id, games_planned, "
        "games_done, wins, wr, verdict, created_at, resolved_at) "
        "VALUES(?, NULL, 'dBase', 200, 200, 120, 0.6, 'pass', 't', 't')", (version,))


def _gate_fail(conn: sqlite3.Connection, version: str) -> None:
    anchor._ensure_schema(conn)
    conn.execute(
        "INSERT INTO anchor_checks(version, offspring_id, deck_id, games_planned, "
        "games_done, wins, wr, verdict, created_at, resolved_at) "
        "VALUES(?, NULL, 'dBase', 200, 200, 80, 0.4, 'fail', 't', 't')", (version,))


def _gate_pending(conn: sqlite3.Connection, version: str, done: int = 57) -> None:
    anchor._ensure_schema(conn)
    conn.execute(
        "INSERT INTO anchor_checks(version, offspring_id, deck_id, games_planned, "
        "created_at) VALUES(?, NULL, 'dBase', 200, 't')", (version,))
    for _ in range(done):
        conn.execute(
            "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, "
            "agent_version_b, purpose, winner, status) "
            "VALUES('dBase','dA',?, 'anchor-heuristic-v0','anchor',0,'done')",
            (version,))


def test_gate_absent_blocks_upload(tmp_path, monkeypatch):
    """No anchor_checks row at all -> anchor_status treats it as 'absent'
    -- the gate must block, not treat absence as an implicit pass."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    counter = SubmissionCounter(tmp_path / "counter.json")
    client = FakeKaggleClient()
    state_path = tmp_path / "state.json"
    now = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)

    result = subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now,
        build_fn=_fake_build, verify_fn=_fake_verify)

    assert len(result) == 1
    cand_id, outcome, detail = result[0]
    assert (cand_id, outcome) == ("GATE", "anchor-absent")
    assert client.submitted == []
    assert not state_path.exists()  # scheduler state never touched
    assert counter.today_count("2026-08-01") == 0  # zero reservations consumed


def test_gate_pending_blocks_with_progress_detail(tmp_path, monkeypatch):
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    _gate_pending(db, "v0.1", done=57)
    counter = SubmissionCounter(tmp_path / "counter.json")
    client = FakeKaggleClient()
    state_path = tmp_path / "state.json"
    now = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)

    result = subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now,
        build_fn=_fake_build, verify_fn=_fake_verify)

    assert len(result) == 1
    cand_id, outcome, detail = result[0]
    assert (cand_id, outcome) == ("GATE", "anchor-pending")
    assert "57/200" in detail
    assert client.submitted == []
    assert not state_path.exists()


def test_gate_fail_blocks_permanently(tmp_path, monkeypatch):
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    _gate_fail(db, "v0.1")
    counter = SubmissionCounter(tmp_path / "counter.json")
    client = FakeKaggleClient()
    state_path = tmp_path / "state.json"
    now = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)

    result = subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now,
        build_fn=_fake_build, verify_fn=_fake_verify)

    assert len(result) == 1
    cand_id, outcome, detail = result[0]
    assert (cand_id, outcome) == ("GATE", "anchor-fail")
    assert client.submitted == []
    assert not state_path.exists()


def test_gate_pass_upload_proceeds(tmp_path, monkeypatch):
    """A resolved 'pass' verdict must let the existing mark-trigger flow run
    end-to-end -- mirrors test_first_call_uploads_baseline_as_mark_triggered
    verbatim, plus the pass-verdict seed."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    _gate_pass(db, "v0.1")
    counter = SubmissionCounter(tmp_path / "counter.json")
    client = FakeKaggleClient()
    state_path = tmp_path / "state.json"
    now = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)

    result = subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now,
        build_fn=_fake_build, verify_fn=_fake_verify)

    assert len(result) == 1
    cand_id, outcome, detail = result[0]
    assert outcome == "submitted"
    assert cand_id == "tournament-champion-v0.1"
    assert len(client.submitted) == 1
    assert client.submitted[0][1].startswith("tournament-champion v0.1")
    assert counter.today_count("2026-08-01") == 1

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_uploaded_identity"] == "v0.1"
    assert state["last_mark_fired"] == ["2026-08-01", 0]
    assert state["last_upload_at"] is not None


def test_gate_blocks_daily_floor_probe_too(tmp_path, monkeypatch):
    """The gate check runs BEFORE the mark/idle branch split, so a call
    shaped exactly like the daily-floor-probe trigger (idle >= 24h) is
    still blocked on a non-pass verdict -- the probe ships the current
    baseline's own agent to an alternate deck, so it must be gated by the
    same verdict as the mark-trigger path, not exempted."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path, n_active=1)
    _gate_pending(db, "v0.1", done=57)
    counter = SubmissionCounter(tmp_path / "counter.json")
    client = FakeKaggleClient()
    state_path = tmp_path / "state.json"
    now = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC) + dt.timedelta(hours=25)

    result = subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now,
        build_fn=_fake_build, verify_fn=_fake_verify)

    assert len(result) == 1
    cand_id, outcome, detail = result[0]
    assert (cand_id, outcome) == ("GATE", "anchor-pending")
    assert client.submitted == []
    assert not state_path.exists()


# --- Pair-gate tests (counted-pair-protection designs 1-3): the mark-
# triggered upload must additionally clear the pair gate. FakeKaggleClient
# with no rows (the default in every test above) means <2 counted
# submissions -> nothing to evict -> clear, which is why all pre-slice
# tests in this file still pass unchanged.

_PG_EVICTEE_DESC = ("tournament-champion v0.1 - deck dBase - agent search-net "
                    "- anchor-wr 0.600 of 200 games - abc12345 - factory")
_PG_NEWER_DESC = ("tournament-champion v0.2 - deck dBase - agent search-net "
                  "- anchor-wr 0.700 of 200 games - abc12345 - factory")
_PG_RESCUE_DESC = ("mega-lucario-fighting-heuristic v1.0 - deck deck - agent "
                   "heuristic - local_wr 0.460 of 50 - abc12345 - factory")


def _counted_rows(older_desc=_PG_EVICTEE_DESC):
    return [
        SubmissionRow("s2.tar.gz", "2026-08-04 12:00:00", _PG_NEWER_DESC,
                     "COMPLETE", 486.4),
        SubmissionRow("s1.tar.gz", "2026-08-03 12:00:00", older_desc,
                     "COMPLETE", 558.5),
    ]


def _seed_crowned_v02(db) -> None:
    """Crown v0.2 over founding v0.1 (offspring v0.1.1 survivor), with a
    settled anchor 'pass' for v0.2 so only the PAIR gate is under test."""
    def _s(c):
        c.execute(
            "INSERT INTO offspring(id,parent_baseline_version,"
            "search_config_json,value_net_ref,deck_id,status,created_at) "
            "VALUES('v0.1.1','v0.1','{\"search_budget_ms\": 200}',NULL,"
            "'dBase','survivor','t')")
        c.execute(
            "INSERT INTO baselines(version,offspring_id,deck_id,crowned_at) "
            "VALUES('v0.2','v0.1.1','dBase','t')")
        c.execute("INSERT OR REPLACE INTO meta(key,value) "
                  "VALUES('baseline_version','v0.2')")
    deckdb._write(db, _s)
    _gate_pass(db, "v0.2")


def _finish_pg_games(db, wins, total):
    def _apply(c):
        rows = c.execute(
            "SELECT id FROM games WHERE purpose='pair_gate' "
            "AND agent_version_a='v0.2' AND agent_version_b='v0.1' "
            "AND status='pending' ORDER BY id LIMIT ?", (total,)).fetchall()
        assert len(rows) == total
        for i, row in enumerate(rows):
            c.execute("UPDATE games SET status='done', winner=? WHERE id=?",
                      (0 if i < wins else 1, row["id"]))
    deckdb._write(db, _apply)


def test_pair_gate_clear_when_fewer_than_two_counted(tmp_path, monkeypatch):
    """FakeKaggleClient() with no rows -> nothing to evict -> upload
    proceeds, and the description says so honestly."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    _gate_pass(db, "v0.1")
    counter = SubmissionCounter(tmp_path / "counter.json")
    client = FakeKaggleClient()
    now = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)
    result = subscheduler.maybe_submit(
        db, client, counter, tmp_path / "state.json", tmp_path / "out",
        tmp_path, now, build_fn=_fake_build, verify_fn=_fake_verify)
    assert result[0][1] == "submitted"
    assert "pair-gate n-a (no evictee)" in client.submitted[0][1]


def test_pair_gate_enqueues_then_skips_mark_then_retries_to_upload(tmp_path, monkeypatch):
    """The full skip/retry loop: firing 1 enqueues + skips the mark
    (scheduler state UNTOUCHED -- last_uploaded_identity is only written
    at subscheduler.py:417-421 on a submitted outcome); games finish;
    firing 2 resolves pass + TOCTOU-current + uploads with the pair-gate
    evidence in the description."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    _seed_crowned_v02(db)
    counter = SubmissionCounter(tmp_path / "counter.json")
    client = FakeKaggleClient(rows=_counted_rows())
    state_path = tmp_path / "state.json"
    now = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)

    first = subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now,
        build_fn=_fake_build, verify_fn=_fake_verify)
    assert first == [("GATE", "pair-gate-pending", "v0.2 0/200")]
    assert client.submitted == []
    assert not state_path.exists()  # mark NOT consumed -- retry is free
    assert counter.today_count("2026-08-01") == 0

    _finish_pg_games(db, wins=120, total=200)  # 0.60 >= 0.55

    second = subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now,
        build_fn=_fake_build, verify_fn=_fake_verify)
    assert second[0][1] == "submitted"
    desc = client.submitted[0][1]
    assert "pair-gate 0.600 vs v0.1" in desc
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_uploaded_identity"] == "v0.2"


def test_pair_gate_fail_blocks_upload_but_not_promotion(tmp_path, monkeypatch):
    """I3: a pair-gate FAIL blocks the upload ONLY. The baseline row,
    meta['baseline_version'], and the offspring's survivor status are all
    untouched -- the failing champion remains baseline and parents the
    next generation."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    _seed_crowned_v02(db)
    counter = SubmissionCounter(tmp_path / "counter.json")
    client = FakeKaggleClient(rows=_counted_rows())
    state_path = tmp_path / "state.json"
    now = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)

    subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now,
        build_fn=_fake_build, verify_fn=_fake_verify)  # enqueues
    _finish_pg_games(db, wins=109, total=200)  # 0.545 < 0.55 -> fail

    result = subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now,
        build_fn=_fake_build, verify_fn=_fake_verify)
    assert result[0][:2] == ("GATE", "pair-gate-fail")
    assert client.submitted == []
    assert loop_state.current_baseline(db)["version"] == "v0.2"  # I3
    off = db.execute("SELECT status FROM offspring WHERE id='v0.1.1'").fetchone()
    assert off["status"] == "survivor"  # I3: breeding lineage untouched


def test_pair_gate_fail_closed_on_unreconstructable_evictee(tmp_path, monkeypatch):
    """Spec design 2 FAIL-CLOSED: a legacy/rescue evictee blocks the upload
    with a loud log line -- never uploads ungated."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    _seed_crowned_v02(db)
    counter = SubmissionCounter(tmp_path / "counter.json")
    client = FakeKaggleClient(rows=_counted_rows(older_desc=_PG_RESCUE_DESC))
    logs: list[str] = []
    now = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)

    result = subscheduler.maybe_submit(
        db, client, counter, tmp_path / "state.json", tmp_path / "out",
        tmp_path, now, build_fn=_fake_build, verify_fn=_fake_verify,
        log=logs.append)
    assert result[0][:2] == ("GATE", "pair-gate-fail-closed")
    assert client.submitted == []
    assert any("FAIL-CLOSED" in m for m in logs), logs


def test_pair_gate_stale_pass_rekeys_and_defers(tmp_path, monkeypatch):
    """I4 through the scheduler: a settled pass whose evictee changed is
    re-keyed (fresh series vs the new opponent) and the upload deferred."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path)
    _seed_crowned_v02(db)
    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cAlt','[\"A\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dAlt','cAlt','[202]')")
        c.execute(
            "INSERT INTO offspring(id,parent_baseline_version,"
            "search_config_json,value_net_ref,deck_id,status,created_at) "
            "VALUES('v0.1.2','v0.1','{}',NULL,'dAlt','trashed','t')")
    deckdb._write(db, _s)
    counter = SubmissionCounter(tmp_path / "counter.json")
    client = FakeKaggleClient(rows=_counted_rows())
    state_path = tmp_path / "state.json"
    now = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)

    subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now,
        build_fn=_fake_build, verify_fn=_fake_verify)  # enqueue vs v0.1
    _finish_pg_games(db, wins=120, total=200)

    # The counted pair changes under us: the older row is now a DIFFERENT
    # reconstructable identity (v0.1.2 on dAlt). Probe-shaped NAME on
    # purpose: `tournament-champion` reconstructs deck via `baselines`
    # (v0.1.2 has no baselines row -> would fail-close), while a
    # non-champion name takes deck from the stem (dAlt exists) and the
    # version resolves via its offspring row.
    stale_desc = ("tournament-probe-cAlt v0.1.2 - deck dAlt - agent search-net "
                 "- anchor-wr 0.500 of 200 games - abc12345 - factory")
    client.rows = _counted_rows(older_desc=stale_desc)

    result = subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now,
        build_fn=_fake_build, verify_fn=_fake_verify)
    assert result[0][:2] == ("GATE", "pair-gate-rekeyed")
    assert client.submitted == []
    row = db.execute(
        "SELECT opp_version, verdict FROM pair_gate_checks "
        "WHERE version='v0.2'").fetchone()
    assert (row["opp_version"], row["verdict"]) == ("v0.1.2", "pending")


def test_daily_floor_probe_disabled_by_default_until_deadline(tmp_path, monkeypatch):
    """Counted-pair-protection design 4: the probe is an ungated upload
    path that can evict a counted submission -- disabled behind
    DAILY_FLOOR_PROBE_ENABLED=False until after 2026-08-16. The exact
    idle-24h shape that used to fire now no-ops with a loud log line."""
    assert subscheduler.DAILY_FLOOR_PROBE_ENABLED is False  # default
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db = _seed_founding(tmp_path, n_active=1)
    _gate_pass(db, "v0.1")
    counter = SubmissionCounter(tmp_path / "counter.json")
    client = FakeKaggleClient()
    state_path = tmp_path / "state.json"
    logs: list[str] = []
    now0 = dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_UTC)
    subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now0,
        build_fn=_fake_build, verify_fn=_fake_verify)
    assert len(client.submitted) == 1  # the mark upload

    now1 = now0 + dt.timedelta(hours=25)
    second = subscheduler.maybe_submit(
        db, client, counter, state_path, tmp_path / "out", tmp_path, now1,
        build_fn=_fake_build, verify_fn=_fake_verify, log=logs.append)
    assert second == []
    assert len(client.submitted) == 1  # NO probe upload
    assert any("daily-floor probe: disabled until after 2026-08-16" in m
               for m in logs), logs
