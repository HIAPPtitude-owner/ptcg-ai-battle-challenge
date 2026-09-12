"""End-to-end dry-run of one factory cycle (fake Kaggle, stubbed arena/bundles)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from ptcg.arena.stats import SeriesStats
from ptcg.factory.candidates import Candidate, Status, load_ledger, save_ledger
from ptcg.factory.cycle import FactoryPaths, run_cycle, write_digest
from ptcg.factory.evaluate import EvalConfig
from ptcg.factory.journal import append_journal
from ptcg.factory.kaggle_client import FakeKaggleClient, SubmissionRow


def _stats(wins_a: int, wins_b: int) -> SeriesStats:
    return SeriesStats(wins_a=wins_a, wins_b=wins_b,
                       game_seconds=[0.1] * (wins_a + wins_b))


def test_journal_appends_and_creates_header(tmp_path):
    j = tmp_path / "writeup-notes.md"
    append_journal(j, "first entry", "body one")
    append_journal(j, "second entry", "body two")
    text = j.read_text(encoding="utf-8")
    assert text.startswith("# Writeup Notes")
    assert text.count("## ") >= 2 and "body two" in text


def test_digest_renders_per_baseline_breakdown(tmp_path):
    """Change 2: dual-baseline behavior must be auditable from the digest.
    Hand-verified arithmetic: 43/75 = 0.573 (3dp), 54/75 = 0.720, pooled
    wins 43 + 54 = 97 of 75 + 75 = 150, 97/150 = 0.6467 -> 0.647 (3dp)."""
    cand = Candidate.create(name="dual", version="v1.0", deck="d.csv",
                            agent_kind="heuristic")
    cand.status = Status.SUBMITTED
    cand.local_wr = 97 / 150
    cand.local_games = 150
    cand.local_breakdown = [
        {"baseline": "mega-lucario-fighting", "wins": 43, "games": 75},
        {"baseline": "mega-starmie-water", "wins": 54, "games": 75},
    ]
    path = write_digest(tmp_path / "digests", None, [cand], [],
                        now=dt.datetime(2026, 7, 16, 2, 0, 0))
    text = path.read_text(encoding="utf-8")
    assert ("- dual-v1.0: local_wr=0.647/150 [mega-lucario-fighting 0.573 "
            "(43/75); mega-starmie-water 0.720 (54/75)] -> submitted") in text


def test_digest_pooled_only_line_when_breakdown_none(tmp_path):
    """A None-breakdown candidate (old ledger entry, or a plain-SeriesStats
    eval) renders the current pooled-only line exactly as today."""
    cand = Candidate.create(name="plain", version="v1.0", deck="d.csv",
                            agent_kind="heuristic")
    cand.status = Status.EVALUATED
    cand.local_wr = 0.5
    cand.local_games = 150
    assert cand.local_breakdown is None  # field default
    path = write_digest(tmp_path / "digests", None, [cand], [],
                        now=dt.datetime(2026, 7, 16, 2, 0, 0))
    text = path.read_text(encoding="utf-8")
    assert "- plain-v1.0: local_wr=0.500/150 -> evaluated\n" in text
    assert "[" not in text.split("## Evaluated")[1]  # no bracket segment


def test_cycle_dry_run_end_to_end(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    inc = Candidate.create(name="incumbent", version="v1.0", deck="d.csv",
                           agent_kind="heuristic")
    inc.status, inc.local_wr, inc.local_games = Status.SUBMITTED, 0.50, 150
    inc.submitted_at = "2026-07-11T04:00"
    challenger = Candidate.create(
        name="challenger", version="v1.0",
        deck="src/ptcg/decks/candidates/mega-lucario-fighting.csv",
        agent_kind="heuristic", priority=0.9)
    save_ledger(paths.ledger, [inc, challenger])

    client = FakeKaggleClient(rows=[SubmissionRow(
        "submission.tar.gz", "2026-07-11 04:00:00",
        "incumbent v1.0 | deck=d | agent=heuristic | x | factory",
        "COMPLETE", 1500.0)])

    result = run_cycle(
        paths, client, EvalConfig(games=150, experiments_md=paths.experiments_md),
        no_submit=True, series_fn=lambda c, cfg: _stats(90, 60),
        build_fn=lambda c, d: Path(d) / "submission.tar.gz",
        verify_fn=lambda *a: None, log=lambda m: None)

    cands = {c.name: c for c in load_ledger(paths.ledger)}
    assert cands["incumbent"].status is Status.SCORED  # harvest matched + scored
    assert cands["challenger"].status is Status.EVALUATED
    assert cands["challenger"].local_wr == 0.6
    assert any(a[1] == "dry-run" for a in result["actions"])  # 0.6 > 0.5 gate pass
    assert client.submitted == []  # dry-run uploads nothing
    assert paths.ladder.exists()
    assert "slice7a-factory-eval challenger-v1.0" in paths.experiments_md.read_text(
        encoding="utf-8")
    digest = Path(result["digest"]).read_text(encoding="utf-8")
    assert "challenger-v1.0" in digest
    assert "factory cycle" in paths.journal.read_text(encoding="utf-8")


def test_cycle_respects_pause_file(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    paths.pause_file.parent.mkdir(parents=True, exist_ok=True)
    paths.pause_file.write_text("paused for review", encoding="utf-8")
    result = run_cycle(paths, FakeKaggleClient(), EvalConfig(), log=lambda m: None)
    assert result == {"paused": True}


def test_cycle_skips_gate_submit_when_submit_hold_present(tmp_path, monkeypatch):
    """SUBMIT_HOLD (2026-07-22): the entire gate/submit step is skipped --
    no gate decisions, no Kaggle uploads, no counter reservations -- while
    everything else (evaluation here; harvest/evo-gate/dashboard in
    watch_once) keeps running normally. Proven directly by making
    submit_candidates raise if it is ever called."""
    import ptcg.factory.cycle as cycle_mod

    def _refuse_submit(*a, **k):
        raise AssertionError("submit_candidates must not be called under SUBMIT_HOLD")

    monkeypatch.setattr(cycle_mod, "submit_candidates", _refuse_submit)

    paths = FactoryPaths(root=tmp_path)
    paths.submit_hold_file.parent.mkdir(parents=True, exist_ok=True)
    paths.submit_hold_file.write_text(
        "held for generational-tournament redesign 2026-07-22 per Brad",
        encoding="utf-8")
    inc = Candidate.create(name="incumbent", version="v1.0", deck="d.csv",
                           agent_kind="heuristic")
    inc.status, inc.local_wr, inc.local_games = Status.SUBMITTED, 0.50, 150
    inc.submitted_at = "2026-07-11T04:00"
    challenger = Candidate.create(
        name="challenger", version="v1.0",
        deck="src/ptcg/decks/candidates/mega-lucario-fighting.csv",
        agent_kind="heuristic", priority=0.9)
    save_ledger(paths.ledger, [inc, challenger])

    result = run_cycle(
        paths, FakeKaggleClient(rows=[]),
        EvalConfig(games=150, experiments_md=paths.experiments_md),
        no_submit=True, series_fn=lambda c, cfg: _stats(90, 60),
        build_fn=lambda c, d: Path(d) / "submission.tar.gz",
        verify_fn=lambda *a: None, log=lambda m: None)

    assert result.get("error") is None  # a deliberate hold, not a crash
    assert result["submit_held"] is True
    assert result["actions"] == []

    cands = {c.name: c for c in load_ledger(paths.ledger)}
    assert cands["challenger"].status is Status.EVALUATED  # eval still ran
    assert cands["challenger"].local_wr == 0.6  # ...unaffected by the hold


def test_cycle_submit_held_false_when_hold_file_absent(tmp_path):
    """Absent hold file -> unchanged behavior (control for the test above)."""
    paths = FactoryPaths(root=tmp_path)
    assert not paths.submit_hold_file.exists()
    inc = Candidate.create(name="incumbent", version="v1.0", deck="d.csv",
                           agent_kind="heuristic")
    inc.status, inc.local_wr, inc.local_games = Status.SUBMITTED, 0.50, 150
    inc.submitted_at = "2026-07-11T04:00"
    challenger = Candidate.create(
        name="challenger", version="v1.0",
        deck="src/ptcg/decks/candidates/mega-lucario-fighting.csv",
        agent_kind="heuristic", priority=0.9)
    save_ledger(paths.ledger, [inc, challenger])

    client = FakeKaggleClient(rows=[SubmissionRow(
        "submission.tar.gz", "2026-07-11 04:00:00",
        "incumbent v1.0 | deck=d | agent=heuristic | x | factory",
        "COMPLETE", 1500.0)])

    result = run_cycle(
        paths, client, EvalConfig(games=150, experiments_md=paths.experiments_md),
        no_submit=True, series_fn=lambda c, cfg: _stats(90, 60),
        build_fn=lambda c, d: Path(d) / "submission.tar.gz",
        verify_fn=lambda *a: None, log=lambda m: None)

    assert result["submit_held"] is False
    assert any(a[1] == "dry-run" for a in result["actions"])  # gate/submit ran


def test_cycle_pause_takes_precedence_over_submit_hold(tmp_path):
    """PAUSE's existing full-stop semantics are unchanged and take
    precedence over SUBMIT_HOLD when both files are present."""
    paths = FactoryPaths(root=tmp_path)
    paths.pause_file.parent.mkdir(parents=True, exist_ok=True)
    paths.pause_file.write_text("paused for review", encoding="utf-8")
    paths.submit_hold_file.write_text("held", encoding="utf-8")
    result = run_cycle(paths, FakeKaggleClient(), EvalConfig(), log=lambda m: None)
    assert result == {"paused": True}


def test_evaluate_save_preserves_concurrent_daemon_append(tmp_path):
    """Regression test for the stale-list ledger overwrite (fixed via
    candidates.merge_save): run_cycle's evaluation phase loads `candidates`
    ONCE at the top of the cycle, then saves after every candidate
    transition, possibly hours later. If a concurrent process (the training
    daemon) appends a NEW candidate directly to disk while the cycle's
    evaluation is mid-flight, a plain locked `save_ledger(path, stale_list)`
    would silently drop that append when the cycle's next save re-serializes
    its stale in-memory snapshot. This test simulates exactly that: the
    daemon-style disk write happens from inside `series_fn`, i.e. AFTER
    run_cycle's initial load but BEFORE its post-evaluation save."""
    paths = FactoryPaths(root=tmp_path)
    cycle_cand = Candidate.create(
        name="cycle-candidate", version="v1.0",
        deck="src/ptcg/decks/candidates/mega-lucario-fighting.csv",
        agent_kind="heuristic", priority=0.9)
    save_ledger(paths.ledger, [cycle_cand])

    daemon_cand = Candidate.create(
        name="daemon-net", version="v0.1", deck="d.csv", agent_kind="search-net",
        provenance="daemon:probe:c0")

    def series_fn(cand, cfg):
        # Simulates a concurrently-running training daemon registering a
        # freshly trained candidate directly on disk WHILE run_cycle's
        # evaluation phase is still working from its earlier-loaded (now
        # stale relative to this write) in-memory candidates list.
        on_disk = load_ledger(paths.ledger)
        on_disk.append(daemon_cand)
        save_ledger(paths.ledger, on_disk)
        return _stats(90, 60)

    run_cycle(
        paths, FakeKaggleClient(),
        EvalConfig(games=150, experiments_md=paths.experiments_md),
        no_submit=True, series_fn=series_fn,
        build_fn=lambda c, d: Path(d) / "s.tar.gz", verify_fn=lambda *a: None,
        log=lambda m: None)

    ids = {c.id for c in load_ledger(paths.ledger)}
    assert cycle_cand.id in ids
    assert daemon_cand.id in ids, (
        "daemon's concurrently-registered candidate was silently dropped by "
        "a stale-list ledger save")


def test_suppress_empty_digest_skips_digest_and_journal(tmp_path):
    """Guaranteed no-op: empty ledger (nothing to evaluate/gate) and a
    client with zero submission rows (harvest_res.scored_updates == 0)."""
    paths = FactoryPaths(root=tmp_path)
    save_ledger(paths.ledger, [])

    result = run_cycle(
        paths, FakeKaggleClient(rows=[]),
        EvalConfig(experiments_md=paths.experiments_md),
        no_submit=True, suppress_empty_digest=True, log=lambda m: None)

    assert result.get("noop") is True
    assert result["digest"] is None
    assert not list(paths.digest_dir.glob("*.md"))
    assert not paths.journal.exists()


def test_suppress_empty_digest_noop_when_harvest_offline(tmp_path):
    """Degenerate case named in the no-op definition: harvest_res is None
    (harvest failed/offline) still counts as a no-op when nothing else
    happened, so suppression must still fire."""
    paths = FactoryPaths(root=tmp_path)
    save_ledger(paths.ledger, [])

    class DeadClient:
        def list_submissions(self):
            raise RuntimeError("kaggle CLI failed (1): offline")

        def submit(self, bundle, description):
            raise AssertionError("must not upload during outage test")

    result = run_cycle(
        paths, DeadClient(), EvalConfig(experiments_md=paths.experiments_md),
        no_submit=True, suppress_empty_digest=True, log=lambda m: None)

    assert result["harvest"] is None
    assert result.get("noop") is True
    assert result["digest"] is None
    assert not list(paths.digest_dir.glob("*.md"))
    assert not paths.journal.exists()


def test_suppress_flag_still_writes_when_scores_update(tmp_path):
    """Client stub returns one row matching a SUBMITTED candidate with a new
    public_score -> scored_updates == 1 -> digest MUST be written even with
    an otherwise-empty queue (evaluated=[], actions=[])."""
    paths = FactoryPaths(root=tmp_path)
    submitted = Candidate.create(name="incumbent", version="v1.0", deck="d.csv",
                                 agent_kind="heuristic")
    submitted.status = Status.SUBMITTED
    submitted.submitted_at = "2026-07-11T04:00"
    save_ledger(paths.ledger, [submitted])

    client = FakeKaggleClient(rows=[SubmissionRow(
        "submission.tar.gz", "2026-07-11 04:00:00",
        "incumbent v1.0 | deck=d | agent=heuristic | x | factory",
        "COMPLETE", 1500.0)])

    result = run_cycle(
        paths, client, EvalConfig(experiments_md=paths.experiments_md),
        no_submit=True, suppress_empty_digest=True, log=lambda m: None)

    assert result["harvest"].scored_updates == 1
    assert result.get("noop") is not True
    assert result["digest"] is not None
    assert len(list(paths.digest_dir.glob("*.md"))) == 1
    assert paths.journal.exists()


def test_default_behavior_unchanged_writes_digest_on_noop(tmp_path):
    """suppress_empty_digest defaults to False -> today's behavior (always
    write digest + journal) stays byte-identical."""
    paths = FactoryPaths(root=tmp_path)
    save_ledger(paths.ledger, [])

    result = run_cycle(
        paths, FakeKaggleClient(rows=[]),
        EvalConfig(experiments_md=paths.experiments_md),
        no_submit=True, log=lambda m: None)

    assert result.get("noop") is None
    assert len(list(paths.digest_dir.glob("*.md"))) == 1
    assert paths.journal.exists()


def test_cycle_surfaces_auth_dead_in_digest(tmp_path):
    """Pre-submit auth guard (real bug 2026-07-20). Real submit mode
    (no_submit=False) against a client whose list_submissions() raises must
    skip the submit phase entirely, still complete the cycle, and show an
    AUTH-DEAD marker in the digest - not just log it."""
    paths = FactoryPaths(root=tmp_path)
    challenger = Candidate.create(
        name="challenger", version="v1.0",
        deck="src/ptcg/decks/candidates/mega-lucario-fighting.csv",
        agent_kind="heuristic", priority=0.9)
    save_ledger(paths.ledger, [challenger])

    class DeadAuthClient:
        def list_submissions(self):
            raise RuntimeError("kaggle CLI failed (1): "
                               "Authentication required to call the Kaggle API.")

        def submit(self, bundle, description):
            raise AssertionError("must not upload when auth is dead")

    logs: list[str] = []
    result = run_cycle(
        paths, DeadAuthClient(),
        EvalConfig(games=150, experiments_md=paths.experiments_md),
        no_submit=False, series_fn=lambda c, cfg: _stats(90, 60),
        build_fn=lambda c, d: Path(d) / "submission.tar.gz",
        verify_fn=lambda *a: None, log=logs.append)

    assert any(a[0] == "AUTH" and a[1] == "auth-dead" for a in result["actions"])
    assert any(m.startswith("AUTH-DEAD:") for m in logs), logs
    digest = Path(result["digest"]).read_text(encoding="utf-8")
    assert "AUTH-DEAD" in digest or "auth-dead" in digest
    cands = {c.name: c for c in load_ledger(paths.ledger)}
    assert cands["challenger"].status is Status.EVALUATED  # never gated


def test_cycle_survives_harvest_outage(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    cand = Candidate.create(
        name="only", version="v1.0",
        deck="src/ptcg/decks/candidates/mega-lucario-fighting.csv",
        agent_kind="heuristic", priority=0.9)
    save_ledger(paths.ledger, [cand])

    class DeadClient:
        def list_submissions(self):
            raise RuntimeError("kaggle CLI failed (1): offline")

        def submit(self, bundle, description):
            raise AssertionError("must not upload during outage test")

    result = run_cycle(
        paths, DeadClient(), EvalConfig(experiments_md=paths.experiments_md),
        no_submit=True, series_fn=lambda c, cfg: _stats(90, 60),
        build_fn=lambda c, d: Path(d) / "s.tar.gz", verify_fn=lambda *a: None,
        log=lambda m: None)
    # harvest failure never blocks evaluation (spec S9)
    assert result["harvest"] is None
    assert load_ledger(paths.ledger)[0].status is Status.EVALUATED


def test_skip_eval_bypasses_evaluate_queued_but_gate_submit_harvest_still_run(tmp_path):
    """Task 9: watch_once's factory cycle now passes skip_eval=True -- the
    matrix worker (Task 4) owns QUEUED->EVALUATED promotion, so run_cycle
    must never call evaluate_queued/series_fn in that mode. A candidate
    already sitting at EVALUATED (as the matrix worker would leave it) must
    still be gated/submitted, and harvest must still run."""
    paths = FactoryPaths(root=tmp_path)
    pre_evaluated = Candidate.create(
        name="challenger", version="v1.0",
        deck="src/ptcg/decks/candidates/mega-lucario-fighting.csv",
        agent_kind="heuristic", priority=0.9)
    pre_evaluated.status = Status.EVALUATED
    pre_evaluated.local_wr = 0.6
    pre_evaluated.local_games = 150
    save_ledger(paths.ledger, [pre_evaluated])

    def _refuse_series(cand, cfg):
        raise AssertionError("series_fn must not be called when skip_eval=True")

    client = FakeKaggleClient(rows=[])
    result = run_cycle(
        paths, client, EvalConfig(games=150, experiments_md=paths.experiments_md),
        no_submit=True, skip_eval=True, series_fn=_refuse_series,
        build_fn=lambda c, d: Path(d) / "submission.tar.gz",
        verify_fn=lambda *a: None, log=lambda m: None)

    assert result["evaluated"] == []
    assert result["harvest"] is not None  # harvest still ran
    assert any(a[1] == "dry-run" for a in result["actions"])  # gate/submit still ran
    cands = {c.name: c for c in load_ledger(paths.ledger)}
    assert cands["challenger"].status is Status.EVALUATED  # dry-run: unchanged
