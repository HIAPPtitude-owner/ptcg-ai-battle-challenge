"""Freeze-day curation script tests (counted-pair-protection design 5).
Import precedent for scripts/*.py: `from scripts.X import Y`
(pyproject.toml pythonpath = ["src", "."]; e.g. scripts/factory_watch_once.py
is imported the same way by tests/test_factory_watch_cutover.py)."""
from __future__ import annotations

from pathlib import Path

import pytest

from ptcg.factory import deckdb, loop, loop_state, subscheduler
from ptcg.factory.gate import SubmissionCounter
from ptcg.factory.kaggle_client import FakeKaggleClient
from scripts.curate_counted_pair import run


def _fake_build(cand, out_dir) -> Path:
    p = Path(out_dir) / f"{cand.id}.tar.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"fake-bundle")
    return p


def _fake_verify(cand, tar_path, staging_dir) -> None:
    return None


def _seed_two_versions(tmp_path: Path):
    """Founding v0.1 + crowned v0.2, both in `baselines` -- the two
    identities a freeze-day curation picks between."""
    db_path = tmp_path / "t.db"
    conn = deckdb.connect(db_path)
    deckdb.init_db(conn)

    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cBase','[\"Z\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dBase','cBase','[101]')")
    deckdb._write(conn, _s)
    loop_state.set_founding_baseline(conn, "dBase", loop.FOUNDING_AGENT_CONFIG)

    def _s2(c):
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
    deckdb._write(conn, _s2)
    conn.close()
    return db_path


def _argv(tmp_path, db_path, extra=()):
    return ["--best", "v0.2", "--second", "v0.1",
            "--db", str(db_path),
            "--counter", str(tmp_path / "counter.json"),
            "--out-dir", str(tmp_path / "never" / "created" / "curation"),
            "--hold-file", str(tmp_path / "hold" / "SUBMIT_HOLD"),
            *extra]


def test_candidate_for_version_founding_and_crowned(tmp_path, monkeypatch):
    """Both baselines-row provenances reconstruct (provenance-shaped rule):
    founding (offspring_id NULL -> founding meta config) and crowned
    (config from the winning offspring row)."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db_path = _seed_two_versions(tmp_path)
    conn = deckdb.connect(db_path)
    for version in ("v0.1", "v0.2"):
        cand = subscheduler.candidate_for_version(conn, version)
        assert cand.version == version
        assert cand.name == "tournament-champion"
        assert cand.agent_kind == "search-net"
    with pytest.raises(ValueError):
        subscheduler.candidate_for_version(conn, "v9.9")


def test_uploads_best_last_sets_hold_only_after_both_confirmed(tmp_path, monkeypatch):
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db_path = _seed_two_versions(tmp_path)
    client = FakeKaggleClient()
    code = run(_argv(tmp_path, db_path), client=client,
               build_fn=_fake_build, verify_fn=_fake_verify)
    assert code == 0
    assert len(client.submitted) == 2
    assert client.submitted[0][1].startswith("tournament-champion v0.1")  # second FIRST
    assert client.submitted[1][1].startswith("tournament-champion v0.2")  # best LAST
    hold = tmp_path / "hold" / "SUBMIT_HOLD"
    assert hold.exists()  # virgin parent dir was created
    assert "v0.1" in hold.read_text(encoding="utf-8")
    counter = SubmissionCounter(tmp_path / "counter.json")
    from ptcg.factory.gate import utc_today
    assert counter.today_count(utc_today()) == 2


def test_partial_failure_no_hold_nonzero_exit(tmp_path, monkeypatch):
    """dryrun-is-not-the-real-thing: the real path reviewed as if it WILL
    fail. Best-upload failure after the first succeeded -> exit 6, NO
    SUBMIT_HOLD, one counter slot kept (one real upload happened)."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db_path = _seed_two_versions(tmp_path)

    class SecondUploadFails(FakeKaggleClient):
        def submit(self, bundle, description):
            if len(self.submitted) >= 1:
                raise RuntimeError("kaggle 500")
            super().submit(bundle, description)

    client = SecondUploadFails()
    code = run(_argv(tmp_path, db_path), client=client,
               build_fn=_fake_build, verify_fn=_fake_verify)
    assert code == 6
    assert not (tmp_path / "hold" / "SUBMIT_HOLD").exists()
    counter = SubmissionCounter(tmp_path / "counter.json")
    from ptcg.factory.gate import utc_today
    assert counter.today_count(utc_today()) == 1  # 2 reserved, 1 released


def test_first_failure_releases_both_slots(tmp_path, monkeypatch):
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db_path = _seed_two_versions(tmp_path)

    class AllUploadsFail(FakeKaggleClient):
        def submit(self, bundle, description):
            raise RuntimeError("kaggle down")

    code = run(_argv(tmp_path, db_path), client=AllUploadsFail(),
               build_fn=_fake_build, verify_fn=_fake_verify)
    assert code == 5
    assert not (tmp_path / "hold" / "SUBMIT_HOLD").exists()
    counter = SubmissionCounter(tmp_path / "counter.json")
    from ptcg.factory.gate import utc_today
    assert counter.today_count(utc_today()) == 0  # both slots returned


def test_bundle_verify_failure_before_any_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db_path = _seed_two_versions(tmp_path)

    def _bad_verify(cand, tar_path, staging_dir):
        raise RuntimeError("import scan failed")

    client = FakeKaggleClient()
    code = run(_argv(tmp_path, db_path), client=client,
               build_fn=_fake_build, verify_fn=_bad_verify)
    assert code == 2
    assert client.submitted == []  # verify-BOTH-before-ANY-upload ordering


def test_dry_run_no_side_effects(tmp_path, monkeypatch):
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db_path = _seed_two_versions(tmp_path)

    class MustNotTouchNetwork(FakeKaggleClient):
        def list_submissions(self):
            raise AssertionError("dry-run must not touch the network")

        def submit(self, bundle, description):
            raise AssertionError("dry-run must not upload")

    code = run(_argv(tmp_path, db_path, extra=["--dry-run"]),
               client=MustNotTouchNetwork(),
               build_fn=_fake_build, verify_fn=_fake_verify)
    assert code == 0
    assert not (tmp_path / "hold" / "SUBMIT_HOLD").exists()
    assert not (tmp_path / "counter.json").exists()


def test_same_version_twice_is_operator_error(tmp_path, monkeypatch):
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db_path = _seed_two_versions(tmp_path)
    argv = ["--best", "v0.2", "--second", "v0.2", "--db", str(db_path),
            "--counter", str(tmp_path / "c.json"),
            "--out-dir", str(tmp_path / "out"),
            "--hold-file", str(tmp_path / "hold" / "SUBMIT_HOLD")]
    assert run(argv, client=FakeKaggleClient(),
               build_fn=_fake_build, verify_fn=_fake_verify) == 2


def test_unconfirmed_upload_no_hold(tmp_path, monkeypatch):
    """Uploads 'succeed' but never appear in the submissions API -> exit
    7, no hold (the confirmation step is load-bearing, not ceremony)."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db_path = _seed_two_versions(tmp_path)

    class SwallowingClient(FakeKaggleClient):
        def submit(self, bundle, description):
            self.submitted.append((Path(bundle), description))  # no row added

    code = run(_argv(tmp_path, db_path), client=SwallowingClient(),
               build_fn=_fake_build, verify_fn=_fake_verify)
    assert code == 7
    assert not (tmp_path / "hold" / "SUBMIT_HOLD").exists()


# ---------------------------------------------------------------------------
# Legacy-ledger resolution (freeze-curation-legacy-identities design, 2026-08-12)


def _seed_ledger(tmp_path: Path, rows) -> Path:
    from ptcg.factory import candidates as cand_mod
    path = tmp_path / "ledger" / "candidates.json"
    cand_mod.save_ledger(path, rows)
    return path


def _legacy_rows():
    from ptcg.factory.candidates import Candidate
    return [
        Candidate.create(
            name="lp-heur", version="v0.3",
            deck="src/ptcg/decks/candidates/mega-starmie-water.csv",
            agent_kind="heuristic"),
        Candidate.create(
            name="lp-searchnet", version="v0.1",
            deck="src/ptcg/decks/candidates/mega-starmie-water.csv",
            agent_kind="search-net",
            agent_config={"search_budget_ms": 200, "rollout_depth": 12,
                          "net_weights": "src/ptcg/search/value_net_weights_v2.json"}),
    ]


def test_legacy_ledger_pair_resolves_and_uploads(tmp_path, monkeypatch):
    """Both agent kinds resolve from the ledger by exact id and flow through
    the unchanged upload pipeline (provenance-shaped-optional-fields rule:
    ledger rows carry local_wr=None)."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db_path = _seed_two_versions(tmp_path)
    ledger = _seed_ledger(tmp_path, _legacy_rows())
    client = FakeKaggleClient()
    argv = ["--best", "lp-searchnet-v0.1", "--second", "lp-heur-v0.3",
            "--db", str(db_path), "--ledger", str(ledger),
            "--counter", str(tmp_path / "counter.json"),
            "--out-dir", str(tmp_path / "out"),
            "--hold-file", str(tmp_path / "hold" / "SUBMIT_HOLD")]
    code = run(argv, client=client, build_fn=_fake_build, verify_fn=_fake_verify)
    assert code == 0
    assert client.submitted[0][1].startswith("lp-heur v0.3")       # second FIRST
    assert client.submitted[1][1].startswith("lp-searchnet v0.1")  # best LAST
    assert (tmp_path / "hold" / "SUBMIT_HOLD").exists()


def test_tournament_row_shadows_ledger_id(tmp_path, monkeypatch):
    """Precedence: a baselines row wins over a ledger row with the same id."""
    from ptcg.factory.candidates import Candidate
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db_path = _seed_two_versions(tmp_path)
    shadow = Candidate(id="v0.2", name="ledger-shadow", version="v9.9",
                       deck="src/ptcg/decks/candidates/mega-starmie-water.csv",
                       agent_kind="heuristic")
    ledger = _seed_ledger(tmp_path, [shadow, *_legacy_rows()])
    client = FakeKaggleClient()
    argv = ["--best", "v0.2", "--second", "lp-heur-v0.3",
            "--db", str(db_path), "--ledger", str(ledger),
            "--counter", str(tmp_path / "counter.json"),
            "--out-dir", str(tmp_path / "out"),
            "--hold-file", str(tmp_path / "hold" / "SUBMIT_HOLD")]
    code = run(argv, client=client, build_fn=_fake_build, verify_fn=_fake_verify)
    assert code == 0
    assert client.submitted[1][1].startswith("tournament-champion v0.2")


def test_unknown_identity_in_both_stores_exit2(tmp_path, monkeypatch):
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db_path = _seed_two_versions(tmp_path)
    ledger = _seed_ledger(tmp_path, _legacy_rows())
    argv = ["--best", "nope-v9.9", "--second", "v0.1",
            "--db", str(db_path), "--ledger", str(ledger),
            "--counter", str(tmp_path / "counter.json"),
            "--out-dir", str(tmp_path / "out"),
            "--hold-file", str(tmp_path / "hold" / "SUBMIT_HOLD")]
    assert run(argv, client=FakeKaggleClient(),
               build_fn=_fake_build, verify_fn=_fake_verify) == 2


def test_ambiguous_ledger_id_exit2(tmp_path, monkeypatch):
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db_path = _seed_two_versions(tmp_path)
    rows = _legacy_rows()
    ledger = _seed_ledger(tmp_path, [rows[0], rows[0]])  # duplicate id
    argv = ["--best", "lp-heur-v0.3", "--second", "v0.1",
            "--db", str(db_path), "--ledger", str(ledger),
            "--counter", str(tmp_path / "counter.json"),
            "--out-dir", str(tmp_path / "out"),
            "--hold-file", str(tmp_path / "hold" / "SUBMIT_HOLD")]
    assert run(argv, client=FakeKaggleClient(),
               build_fn=_fake_build, verify_fn=_fake_verify) == 2


def test_legacy_missing_artifact_fails_before_upload(tmp_path, monkeypatch):
    """REAL build path: a ledger row pointing at a nonexistent deck must
    fail the build (exit 2) before any upload -- fail-closed shape."""
    from ptcg.factory.candidates import Candidate
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db_path = _seed_two_versions(tmp_path)
    ghost = Candidate.create(
        name="lp-ghost", version="v0.1",
        deck="src/ptcg/decks/candidates/does-not-exist.csv",
        agent_kind="heuristic")
    ledger = _seed_ledger(tmp_path, [ghost, *_legacy_rows()])
    client = FakeKaggleClient()
    argv = ["--best", "lp-ghost-v0.1", "--second", "v0.1",
            "--db", str(db_path), "--ledger", str(ledger),
            "--counter", str(tmp_path / "counter.json"),
            "--out-dir", str(tmp_path / "out"),
            "--hold-file", str(tmp_path / "hold" / "SUBMIT_HOLD")]
    code = run(argv, client=client, verify_fn=_fake_verify)  # REAL build_fn default
    assert code == 2
    assert client.submitted == []


# ---------------------------------------------------------------------------
# Pre-upload confirmation snapshot (Pass-2 Critical fix, 2026-08-12): the
# confirmation gate must require a FRESH row, not just any row matching the
# prefix -- real re-upload identities (see experiments/LADDER.md:7-8, the
# CURRENT counted pair) already have live historical rows with matching
# "{name} {version}" prefixes, so a prefix-only check false-confirms even
# when nothing new was actually uploaded.


def _historical_row(prefix: str, date: str = "2020-01-01 00:00:00"):
    from ptcg.factory.kaggle_client import SubmissionRow
    return SubmissionRow("historical.tar.gz", date,
                         f"{prefix} - deck x - agent y - historical", "SCORED", 500.0)


def test_legacy_reupload_historical_row_does_not_false_confirm_swallowed_upload(
        tmp_path, monkeypatch):
    """Both legacy identities already have a historical row with a matching
    prefix (mimics experiments/LADDER.md's real counted-pair rows). Both
    submit() calls silently swallow (no new row added). Pre-fix, the
    confirmation loop matches the STALE historical rows and wrongly exits 0
    with SUBMIT_HOLD written; post-fix it must require a FRESH row and
    exit 7 with no hold."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db_path = _seed_two_versions(tmp_path)
    ledger = _seed_ledger(tmp_path, _legacy_rows())
    history = [_historical_row("lp-heur v0.3"), _historical_row("lp-searchnet v0.1")]

    class SwallowingClient(FakeKaggleClient):
        def submit(self, bundle, description):
            self.submitted.append((Path(bundle), description))  # no row added

    client = SwallowingClient(rows=history)
    argv = ["--best", "lp-searchnet-v0.1", "--second", "lp-heur-v0.3",
            "--db", str(db_path), "--ledger", str(ledger),
            "--counter", str(tmp_path / "counter.json"),
            "--out-dir", str(tmp_path / "out"),
            "--hold-file", str(tmp_path / "hold" / "SUBMIT_HOLD")]
    code = run(argv, client=client, build_fn=_fake_build, verify_fn=_fake_verify)
    assert code == 7
    assert not (tmp_path / "hold" / "SUBMIT_HOLD").exists()


def test_legacy_partial_reupload_with_historical_rows_no_false_confirm(
        tmp_path, monkeypatch):
    """First upload registers a real new row; second silently swallows.
    Both identities already have historical rows with matching prefixes, so
    pre-fix the stale historical row for the swallowed upload wrongly
    confirms it; post-fix only the genuinely-fresh first row confirms, the
    second doesn't, and the run must exit 7 with no hold."""
    monkeypatch.setattr(subscheduler, "GENERATED_DIR", tmp_path / "generated")
    db_path = _seed_two_versions(tmp_path)
    ledger = _seed_ledger(tmp_path, _legacy_rows())
    history = [_historical_row("lp-heur v0.3"), _historical_row("lp-searchnet v0.1")]

    class SecondUploadSwallows(FakeKaggleClient):
        def submit(self, bundle, description):
            if len(self.submitted) >= 1:
                self.submitted.append((Path(bundle), description))  # swallow
                return
            super().submit(bundle, description)

    client = SecondUploadSwallows(rows=history)
    argv = ["--best", "lp-searchnet-v0.1", "--second", "lp-heur-v0.3",
            "--db", str(db_path), "--ledger", str(ledger),
            "--counter", str(tmp_path / "counter.json"),
            "--out-dir", str(tmp_path / "out"),
            "--hold-file", str(tmp_path / "hold" / "SUBMIT_HOLD")]
    code = run(argv, client=client, build_fn=_fake_build, verify_fn=_fake_verify)
    assert code == 7
    assert not (tmp_path / "hold" / "SUBMIT_HOLD").exists()
