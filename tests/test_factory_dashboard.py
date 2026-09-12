import datetime as dt
import json
import math
import threading
from pathlib import Path

from ptcg.factory import dashboard


def build_factory(tmp_path, *, candidates=None, counter=None, watch_lines=None,
                  digests=None, pause=False, make_dirs=True):
    """Build a fixture experiments/factory/ tree under tmp_path.

    Returns the repo-root Path (tmp_path). When make_dirs is False, NOTHING is
    created (virgin-directory case) so a first-run render can be exercised.
    """
    root = tmp_path
    fdir = root / "experiments" / "factory"
    if make_dirs:
        (fdir / "digests").mkdir(parents=True, exist_ok=True)
        (fdir / "logs").mkdir(parents=True, exist_ok=True)
    if candidates is not None:
        (fdir / "candidates.json").write_text(
            json.dumps({"version": 1, "candidates": candidates}),
            encoding="utf-8")
    if counter is not None:
        (fdir / "submission_counter.json").write_text(
            json.dumps(counter), encoding="utf-8")
    if watch_lines is not None:
        (fdir / "logs" / "watch.log").write_text(
            "".join(f"{line}\n" for line in watch_lines), encoding="utf-8")
    if digests is not None:
        for name, text in digests:
            (fdir / "digests" / name).write_text(text, encoding="utf-8")
    if pause:
        (fdir / "PAUSE").write_text("", encoding="utf-8")
    return root


def _write_heartbeat(root: Path, name: str, ts: str, detail: str = "tick"):
    fdir = root / "experiments" / "factory"
    fdir.mkdir(parents=True, exist_ok=True)
    (fdir / f"{name}_heartbeat.json").write_text(
        json.dumps({"ts": ts, "detail": detail}), encoding="utf-8")


def _write_pool(root: Path, filename: str, genomes: list):
    fdir = root / "experiments" / "factory"
    fdir.mkdir(parents=True, exist_ok=True)
    (fdir / filename).write_text(
        json.dumps({"version": 1, "genomes": genomes}), encoding="utf-8")


def _write_matrix(root: Path, pairs: dict):
    fdir = root / "experiments" / "factory"
    fdir.mkdir(parents=True, exist_ok=True)
    (fdir / "matrix.json").write_text(
        json.dumps({"meta": {}, "pairs": pairs}), encoding="utf-8")


def _agent_row(id, status, rating, born_at, lineage=None):
    return {"id": id, "kind": "search", "config": {}, "status": status,
            "lineage": lineage or [], "born_at": born_at, "seed": 0,
            "rating": rating, "games": 0, "notes": ""}


def _deck_row(id, status, rating, born_at, lineage=None):
    return {"id": id, "cards": [], "csv": "", "net_weights": None,
            "status": status, "lineage": lineage or [], "born_at": born_at,
            "seed": 0, "rating": rating, "games": 0, "notes": ""}


def test_assemble_reads_all_state_files():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        now = dt.datetime(2026, 7, 20, 12, 0)
        root = build_factory(
            Path(td),
            candidates=[{"id": "c-v0.1", "name": "c", "version": "v0.1",
                         "deck": "src/ptcg/decks/candidates/c.csv",
                         "agent_kind": "heuristic", "status": "scored",
                         "is_incumbent": True, "local_wr": 0.6, "local_games": 75,
                         "score_history": [["2026-07-19T21:45", 560.0]]}],
            counter={"date": "2026-07-20", "count": 3},
            watch_lines=["[2026-07-20T11:45] cycle: noop"],
            digests=[("cycle-20260720-114500.md", "# d\n\n## Gate actions\n- none\n")],
        )
        state = dashboard.assemble_state(root, now=now)
        assert state["paused"] is False
        assert state["counter"] == {"date": "2026-07-20", "count": 3}
        assert state["cap"] == 5
        assert state["incumbent_id"] == "c-v0.1"
        assert len(state["candidates"]) == 1
        assert state["last_log"]["summary"] == "cycle: noop"
        assert len(state["digests"]) == 1
        assert state["warnings"] == []


def test_assemble_stale_when_log_old():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        now = dt.datetime(2026, 7, 20, 12, 30)
        root = build_factory(Path(td),
                             watch_lines=["[2026-07-20T11:45] cycle: noop"])
        state = dashboard.assemble_state(root, now=now)  # 45 min gap > 35
        assert state["stale"] is True
        assert state["next_expected"] == dt.datetime(2026, 7, 20, 12, 0)


def test_assemble_poisoned_candidates_produces_warning_not_crash():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = build_factory(Path(td))
        (root / "experiments" / "factory" / "candidates.json").write_text(
            "{not valid json", encoding="utf-8")
        state = dashboard.assemble_state(root, now=dt.datetime(2026, 7, 20, 12, 0))
        assert state["candidates"] == []
        assert any("candidates.json" in w for w in state["warnings"])


def test_assemble_workers_missing_when_file_absent():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = build_factory(Path(td))
        utc_now = dt.datetime(2026, 7, 20, 22, 0, tzinfo=dt.timezone.utc)
        state = dashboard.assemble_state(
            root, now=dt.datetime(2026, 7, 20, 12, 0), utc_now=utc_now)
        workers = {w["name"]: w for w in state["workers"]}
        assert workers["matrix"] == {"name": "matrix", "ts": None,
                                     "detail": None, "stale": False,
                                     "missing": True}
        assert workers["trainer"]["missing"] is True
        assert state["warnings"] == []  # missing file is expected, no warning


def test_assemble_workers_fresh_boundary_29_min():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = build_factory(Path(td))
        utc_now = dt.datetime(2026, 7, 20, 22, 0, tzinfo=dt.timezone.utc)
        ts = (utc_now - dt.timedelta(minutes=29)).isoformat()
        _write_heartbeat(root, "matrix", ts, "tick")
        state = dashboard.assemble_state(
            root, now=dt.datetime(2026, 7, 20, 12, 0), utc_now=utc_now)
        matrix = next(w for w in state["workers"] if w["name"] == "matrix")
        assert matrix["missing"] is False
        assert matrix["stale"] is False
        assert matrix["detail"] == "tick"


def test_assemble_workers_stale_boundary_31_min():
    """Matrix worker keeps the unchanged 30-min WORKER_STALE_MIN threshold
    (I1 fix only changes the TRAINER threshold to 6h) - this boundary test
    must stay on "matrix", not "trainer", or it would spuriously break once
    trainer gets its own longer-running-training-friendly threshold."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = build_factory(Path(td))
        utc_now = dt.datetime(2026, 7, 20, 22, 0, tzinfo=dt.timezone.utc)
        ts = (utc_now - dt.timedelta(minutes=31)).isoformat()
        _write_heartbeat(root, "matrix", ts, "tick")
        state = dashboard.assemble_state(
            root, now=dt.datetime(2026, 7, 20, 12, 0), utc_now=utc_now)
        matrix = next(w for w in state["workers"] if w["name"] == "matrix")
        assert matrix["missing"] is False
        assert matrix["stale"] is True


def test_assemble_workers_trainer_fresh_boundary_5h59m():
    """I1: trainer's heartbeat-stale threshold is 6h (TRAINER_STALE_MIN=360),
    not the matrix worker's 30 min - a single long training run is normal.
    At 5h59m the trainer must still read as fresh."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = build_factory(Path(td))
        utc_now = dt.datetime(2026, 7, 20, 22, 0, tzinfo=dt.timezone.utc)
        ts = (utc_now - dt.timedelta(hours=5, minutes=59)).isoformat()
        _write_heartbeat(root, "trainer", ts, "tick")
        state = dashboard.assemble_state(
            root, now=dt.datetime(2026, 7, 20, 12, 0), utc_now=utc_now)
        trainer = next(w for w in state["workers"] if w["name"] == "trainer")
        assert trainer["missing"] is False
        assert trainer["stale"] is False


def test_assemble_workers_trainer_stale_boundary_6h01m():
    """I1: at 6h01m the trainer must read as stale."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = build_factory(Path(td))
        utc_now = dt.datetime(2026, 7, 20, 22, 0, tzinfo=dt.timezone.utc)
        ts = (utc_now - dt.timedelta(hours=6, minutes=1)).isoformat()
        _write_heartbeat(root, "trainer", ts, "tick")
        state = dashboard.assemble_state(
            root, now=dt.datetime(2026, 7, 20, 12, 0), utc_now=utc_now)
        trainer = next(w for w in state["workers"] if w["name"] == "trainer")
        assert trainer["missing"] is False
        assert trainer["stale"] is True


def test_assemble_workers_malformed_json_treated_as_missing_with_warning():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = build_factory(Path(td))
        fdir = root / "experiments" / "factory"
        (fdir / "matrix_heartbeat.json").write_text("{not valid json",
                                                     encoding="utf-8")
        state = dashboard.assemble_state(
            root, now=dt.datetime(2026, 7, 20, 12, 0),
            utc_now=dt.datetime(2026, 7, 20, 22, 0, tzinfo=dt.timezone.utc))
        matrix = next(w for w in state["workers"] if w["name"] == "matrix")
        assert matrix["missing"] is True
        assert any("matrix_heartbeat.json" in w for w in state["warnings"])


def test_assemble_workers_valid_json_missing_ts_key_treated_as_missing_with_warning():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = build_factory(Path(td))
        fdir = root / "experiments" / "factory"
        (fdir / "trainer_heartbeat.json").write_text(
            json.dumps({"detail": "tick"}), encoding="utf-8")
        state = dashboard.assemble_state(
            root, now=dt.datetime(2026, 7, 20, 12, 0),
            utc_now=dt.datetime(2026, 7, 20, 22, 0, tzinfo=dt.timezone.utc))
        trainer = next(w for w in state["workers"] if w["name"] == "trainer")
        assert trainer["missing"] is True
        assert any("trainer_heartbeat.json" in w for w in state["warnings"])


def test_assemble_evolution_not_seeded_when_pools_absent(tmp_path):
    root = build_factory(tmp_path)  # no agent_pool.json / deck_pool.json
    state = dashboard.assemble_state(root, now=dt.datetime(2026, 7, 21, 9, 0))
    evo = state["evolution"]
    assert evo["seeded"] is False
    assert evo["agent_counts"] == {}
    assert evo["deck_counts"] == {}
    assert evo["top_cells"] == []
    assert evo["recent_births"] == []
    assert evo["recent_retirements"] == []
    assert state["warnings"] == []  # missing pool files are expected, not a warning


def test_assemble_evolution_seeded_with_counts_top_cells_and_recent(tmp_path):
    root = build_factory(tmp_path)
    _write_pool(root, "agent_pool.json", [
        _agent_row("ag-1", "live", 1.0, "2026-07-19T10:00:00"),
        _agent_row("ag-2", "retired", 0.5, "2026-07-18T09:00:00", lineage=["ag-1"]),
        _agent_row("ag-3", "anchor", 0.2, "2026-07-20T08:00:00"),
    ])
    _write_pool(root, "deck_pool.json", [
        _deck_row("dk-1", "live", 0.8, "2026-07-19T11:00:00"),
        _deck_row("dk-2", "retired", 0.1, "2026-07-17T07:00:00", lineage=["dk-1"]),
    ])
    _write_matrix(root, {
        "k1": {"a": "cell~ag-1~dk-1", "b": "cell~other~other", "wins_a": 7, "wins_b": 3},
        "k2": {"a": "cell~ag-3~dk-1", "b": "cell~other2~other2", "wins_a": 2, "wins_b": 1},
    })
    state = dashboard.assemble_state(root, now=dt.datetime(2026, 7, 21, 9, 0))
    evo = state["evolution"]
    assert evo["seeded"] is True
    assert evo["agent_counts"] == {"live": 1, "retired": 1, "anchor": 1}
    assert evo["deck_counts"] == {"live": 1, "retired": 1}

    # dk-2/ag-2 are retired -- excluded from active cells entirely.
    top = evo["top_cells"]
    assert [ (c["agent_id"], c["deck_id"]) for c in top ] == [
        ("ag-1", "dk-1"), ("ag-3", "dk-1"),
    ]
    assert top[0]["strength"] > top[1]["strength"]
    assert top[0]["games"] == 10
    assert top[1]["games"] == 3

    # newest born_at first, across BOTH populations combined.
    births = evo["recent_births"]
    assert [b["id"] for b in births] == ["ag-3", "dk-1", "ag-1", "ag-2", "dk-2"]
    assert births[3]["lineage"] == ["ag-1"]  # ag-2's lineage carried through

    retirements = evo["recent_retirements"]
    assert [r["id"] for r in retirements] == ["ag-2", "dk-2"]


def test_assemble_evolution_corrupt_agent_pool_produces_warning_not_crash(tmp_path):
    root = build_factory(tmp_path)
    fdir = root / "experiments" / "factory"
    fdir.mkdir(parents=True, exist_ok=True)
    (fdir / "agent_pool.json").write_text("{not valid json", encoding="utf-8")
    state = dashboard.assemble_state(root, now=dt.datetime(2026, 7, 21, 9, 0))
    evo = state["evolution"]
    assert evo["seeded"] is True          # the file exists, even though corrupt
    assert evo["agent_counts"] == {}      # degrades to empty, does not raise
    assert evo["top_cells"] == []
    assert any("agent_pool.json" in w for w in state["warnings"])


def test_assemble_evolution_corrupt_matrix_json_produces_warning_not_crash(tmp_path):
    root = build_factory(tmp_path)
    _write_pool(root, "agent_pool.json", [_agent_row("ag-1", "live", 1.0, "2026-07-19T10:00:00")])
    _write_pool(root, "deck_pool.json", [_deck_row("dk-1", "live", 0.8, "2026-07-19T11:00:00")])
    fdir = root / "experiments" / "factory"
    (fdir / "matrix.json").write_text("{not valid json", encoding="utf-8")
    state = dashboard.assemble_state(root, now=dt.datetime(2026, 7, 21, 9, 0))
    evo = state["evolution"]
    assert evo["seeded"] is True
    # cell is still ranked (both sides rated) -- just with 0 games, since the
    # poisoned matrix ledger degrades to empty rather than raising.
    assert evo["top_cells"] == [{"agent_id": "ag-1", "deck_id": "dk-1",
                                "strength": math.exp(1.8), "games": 0}]
    assert any("matrix.json" in w for w in state["warnings"])


# --- loss/meta panel (Task 11) --------------------------------------------


def _extract_rec(day, opponent_deck_hash, our_result, *, timeout=False):
    return {"episode_id": "e", "day": day, "opponent_deck": [3] * 60,
            "our_result": our_result, "timeout": timeout, "n_turns": 10,
            "opponent_deck_hash": opponent_deck_hash}


def _write_extracts(root: Path, records: list):
    ep_dir = root / "experiments" / "factory" / "episodes"
    ep_dir.mkdir(parents=True, exist_ok=True)
    (ep_dir / "extracts.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_assemble_loss_absent_when_extracts_missing(tmp_path):
    root = build_factory(tmp_path)
    state = dashboard.assemble_state(root, now=dt.datetime(2026, 7, 21, 9, 0))
    loss = state["loss"]
    assert loss["present"] is False
    assert loss["n"] == 0
    assert loss["timeout_pct"] is None
    assert loss["clusters"] == []
    assert state["warnings"] == []  # a never-harvested repo isn't a warning


def test_assemble_loss_empty_file_is_present_but_empty(tmp_path):
    root = build_factory(tmp_path)
    _write_extracts(root, [])
    state = dashboard.assemble_state(root, now=dt.datetime(2026, 7, 21, 9, 0))
    loss = state["loss"]
    assert loss["present"] is True
    assert loss["n"] == 0
    assert loss["clusters"] == []


def test_assemble_loss_timeout_pct_and_top_clusters(tmp_path):
    root = build_factory(tmp_path)
    _write_extracts(root, [
        _extract_rec("2026-07-20", "hashA", "loss", timeout=True),
        _extract_rec("2026-07-20", "hashA", "win"),
        _extract_rec("2026-07-20", "hashA", "win"),
        _extract_rec("2026-07-20", "hashB", "win"),
    ])
    state = dashboard.assemble_state(root, now=dt.datetime(2026, 7, 21, 9, 0))
    loss = state["loss"]
    assert loss["present"] is True
    assert loss["n"] == 4
    assert loss["timeout_pct"] == 25.0  # 1/4
    clusters = {c["hash"]: c for c in loss["clusters"]}
    assert clusters["hashA"]["n"] == 3
    assert round(clusters["hashA"]["wr"], 2) == round(200 / 3, 2)  # 2/3 wins
    assert clusters["hashB"]["n"] == 1
    assert clusters["hashB"]["wr"] == 100.0


def test_assemble_loss_malformed_lines_produce_no_warning_just_skipped(tmp_path):
    root = build_factory(tmp_path)
    ep_dir = root / "experiments" / "factory" / "episodes"
    ep_dir.mkdir(parents=True, exist_ok=True)
    (ep_dir / "extracts.jsonl").write_text(
        "{not valid json\n" + json.dumps(_extract_rec("2026-07-20", "hashA", "win")) + "\n",
        encoding="utf-8")
    state = dashboard.assemble_state(root, now=dt.datetime(2026, 7, 21, 9, 0))
    loss = state["loss"]
    assert loss["present"] is True
    assert loss["n"] == 1  # malformed line silently skipped, real tolerance


def test_render_loss_missing_key_backward_compat():
    out = dashboard.render_loss({})
    assert "no harvested episodes yet" in out


def test_render_loss_empty_extracts_placeholder():
    out = dashboard.render_loss({"loss": {"present": True, "n": 0,
                                         "timeout_pct": None, "clusters": []}})
    assert "no harvested episodes yet" in out


def test_render_loss_with_data_shows_timeout_pct_and_clusters():
    state = {"loss": {"present": True, "n": 4, "timeout_pct": 25.0,
                      "clusters": [{"hash": "hashA", "n": 3, "wins": 2, "wr": 66.7},
                                   {"hash": "hashB", "n": 1, "wins": 1, "wr": 100.0}]}}
    out = dashboard.render_loss(state)
    assert "25.0%" in out
    assert "hashA" in out and "hashB" in out
    assert "66.7%" in out


def test_safe_render_with_extracts_shows_loss_panel(tmp_path):
    root = build_factory(tmp_path)
    _write_extracts(root, [_extract_rec("2026-07-20", "hashA", "loss", timeout=True)])
    out_path = dashboard.safe_render(root)
    html_str = out_path.read_text(encoding="utf-8")
    assert "Loss / meta panel" in html_str
    assert "hashA" in html_str


def test_status_strip_running_and_counter():
    state = {
        "now": dt.datetime(2026, 7, 20, 12, 0), "paused": False,
        "utc_now": dt.datetime(2026, 7, 20, 22, 0, tzinfo=dt.timezone.utc),
        "counter": {"date": "2026-07-20", "count": 3}, "cap": 5,
        "last_log": {"raw": "[2026-07-20T11:45] cycle: noop",
                     "timestamp": dt.datetime(2026, 7, 20, 11, 45),
                     "summary": "cycle: noop"},
        "stale": False, "next_expected": dt.datetime(2026, 7, 20, 12, 0),
        "auth_dead": None,
    }
    html_out = dashboard.render_status_strip(state)
    assert "RUNNING" in html_out
    assert "PAUSED" not in html_out
    assert "3/5" in html_out
    assert "cycle: noop" in html_out


def test_status_strip_counter_resets_on_utc_date_rollover():
    """gate.SubmissionCounter.today_count() returns 0 once the persisted
    counter's date != the current UTC date (gate.py:65). The display must
    mirror that exact comparison -- otherwise, right after a UTC date
    rollover, the dashboard shows yesterday's "5/5" (looks exhausted) when
    all 5 of today's slots are actually free."""
    state = {
        "now": dt.datetime(2026, 7, 20, 14, 0), "paused": False,
        "utc_now": dt.datetime(2026, 7, 21, 0, 30, tzinfo=dt.timezone.utc),
        "counter": {"date": "2026-07-20", "count": 5}, "cap": 5,
        "last_log": None, "stale": False, "next_expected": None,
        "auth_dead": None,
    }
    html_out = dashboard.render_status_strip(state)
    assert "0/5" in html_out
    assert "5/5" not in html_out


def test_status_strip_counter_same_utc_day_shows_stored_count():
    state = {
        "now": dt.datetime(2026, 7, 20, 12, 0), "paused": False,
        "utc_now": dt.datetime(2026, 7, 20, 22, 0, tzinfo=dt.timezone.utc),
        "counter": {"date": "2026-07-20", "count": 5}, "cap": 5,
        "last_log": None, "stale": False, "next_expected": None,
        "auth_dead": None,
    }
    html_out = dashboard.render_status_strip(state)
    assert "5/5" in html_out


def test_status_strip_paused_badge():
    state = {"now": dt.datetime(2026, 7, 20, 12, 0), "paused": True,
             "utc_now": dt.datetime(2026, 7, 20, 22, 0, tzinfo=dt.timezone.utc),
             "counter": None, "cap": 5, "last_log": None, "stale": False,
             "next_expected": None, "auth_dead": None}
    assert "PAUSED" in dashboard.render_status_strip(state)


def test_status_strip_stale_and_auth_dead():
    state = {"now": dt.datetime(2026, 7, 20, 12, 30), "paused": False,
             "utc_now": dt.datetime(2026, 7, 20, 22, 30, tzinfo=dt.timezone.utc),
             "counter": {"date": "2026-07-20", "count": 5}, "cap": 5,
             "last_log": {"raw": "[2026-07-20T11:45] cycle: noop",
                          "timestamp": dt.datetime(2026, 7, 20, 11, 45),
                          "summary": "cycle: noop"},
             "stale": True, "next_expected": dt.datetime(2026, 7, 20, 12, 0),
             "auth_dead": {"detail": "kaggle auth check failed",
                           "digest": "cycle-20260720-114500.md"}}
    html_out = dashboard.render_status_strip(state)
    assert "stale" in html_out.lower()
    assert "AUTH-DEAD" in html_out
    assert "kaggle auth check failed" in html_out


def test_status_strip_countdown_uses_utc_anchor_not_local():
    """Machine is HST (UTC-10). Local 2026-07-20T20:00 == UTC
    2026-07-21T06:00, so the next UTC midnight (2026-07-22T00:00Z) is 18h
    away. A countdown computed from the naive-local `now` instead of the
    real UTC instant would wrongly compute 4h (next LOCAL midnight)."""
    state = {
        "now": dt.datetime(2026, 7, 20, 20, 0),
        "utc_now": dt.datetime(2026, 7, 21, 6, 0, tzinfo=dt.timezone.utc),
        "paused": False,
        "counter": {"date": "2026-07-21", "count": 1}, "cap": 5,
        "last_log": None, "stale": False, "next_expected": None,
        "auth_dead": None,
    }
    html_out = dashboard.render_status_strip(state)
    assert "resets in 18h 0m UTC" in html_out
    assert "4h 0m" not in html_out


def _status_strip_state_base():
    return {
        "now": dt.datetime(2026, 7, 20, 12, 0), "paused": False,
        "utc_now": dt.datetime(2026, 7, 20, 22, 0, tzinfo=dt.timezone.utc),
        "counter": {"date": "2026-07-20", "count": 0}, "cap": 5,
        "last_log": None, "stale": False, "next_expected": None,
        "auth_dead": None,
    }


def test_status_strip_worker_fresh_shows_green_detail():
    state = _status_strip_state_base()
    state["workers"] = [{
        "name": "matrix",
        "ts": dt.datetime(2026, 7, 20, 21, 55, tzinfo=dt.timezone.utc),
        "detail": "tick", "stale": False, "missing": False,
    }]
    html_out = dashboard.render_status_strip(state)
    assert "matrix ✓ tick" in html_out
    assert "badge-running" in html_out
    assert "MATRIX STALE" not in html_out


def test_status_strip_worker_stale_shows_red_badge():
    state = _status_strip_state_base()
    state["workers"] = [{
        "name": "trainer",
        "ts": dt.datetime(2026, 7, 20, 21, 0, tzinfo=dt.timezone.utc),
        "detail": "tick", "stale": True, "missing": False,
    }]
    html_out = dashboard.render_status_strip(state)
    assert "TRAINER STALE 60m" in html_out
    assert 'class="badge badge-warn"' in html_out


def test_status_strip_worker_missing_shows_grey_not_registered_no_red_badge():
    state = _status_strip_state_base()
    state["workers"] = [{"name": "matrix", "ts": None, "detail": None,
                         "stale": False, "missing": True}]
    html_out = dashboard.render_status_strip(state)
    assert "matrix: not registered" in html_out
    assert "MATRIX STALE" not in html_out
    # missing worker must render as a plain item, never the red stale badge
    assert html_out.count('class="badge badge-warn"') == 0


def test_status_strip_without_workers_key_backward_compat():
    state = _status_strip_state_base()
    dashboard.render_status_strip(state)  # must not raise KeyError


def _lineage_fixture_candidates():
    base = "src/ptcg/decks/candidates/"
    return [
        {"id": "root-v1.0", "name": "mega-starmie-water-lean-heuristic",
         "version": "v1.0", "deck": base + "mega-starmie-water-lean.csv",
         "agent_kind": "heuristic", "status": "scored", "is_incumbent": False,
         "provenance": "weekly-review-2026-07-14:starmie-axis",
         "local_wr": 0.52, "local_games": 75, "local_breakdown": None,
         "kaggle_score": 560.0, "score_history": [], "notes": ""},
        {"id": "down1-v0.1",
         "name": "mega-starmie-water-lean-attacker-down1-heuristic",
         "version": "v0.1",
         "deck": base + "mega-starmie-water-lean-attacker-down1.csv",
         "agent_kind": "heuristic", "status": "submitted", "is_incumbent": True,
         "provenance": "deck-matrix:mega-starmie-water-lean:attacker-down1",
         "local_wr": 0.6, "local_games": 75,
         "local_breakdown": [{"baseline": "mega-lucario-fighting",
                              "wins": 45, "games": 75}],
         "kaggle_score": 553.0, "score_history": [],
         "notes": "deck-matrix refill - a1b2c3d4 - factory cycle"},
        {"id": "up1-v0.1",
         "name": "mega-starmie-water-lean-attacker-down1-attacker-up1-heuristic",
         "version": "v0.1",
         "deck": base + "mega-starmie-water-lean-attacker-down1-attacker-up1.csv",
         "agent_kind": "heuristic", "status": "evaluated-below-incumbent",
         "is_incumbent": False,
         "provenance":
             "deck-matrix:mega-starmie-water-lean-attacker-down1:attacker-up1",
         "local_wr": 0.48, "local_games": 75, "local_breakdown": None,
         "kaggle_score": None, "score_history": [], "notes": ""},
    ]


def test_lineage_chain_three_generations():
    cands = _lineage_fixture_candidates()
    pmap = dashboard.build_parent_map(cands)
    chain = dashboard.lineage_chain(
        "mega-starmie-water-lean-attacker-down1-attacker-up1", pmap)
    assert chain == [
        "mega-starmie-water-lean",
        "mega-starmie-water-lean-attacker-down1",
        "mega-starmie-water-lean-attacker-down1-attacker-up1",
    ]


def test_parse_commit_present_and_absent():
    assert dashboard.parse_commit("deck-matrix refill - a1b2c3d4 - factory cycle") \
        == "a1b2c3d4"
    assert dashboard.parse_commit("submitted manually 2026-07-11") is None
    assert dashboard.parse_commit("") is None


def test_pipeline_columns_incumbent_and_detail():
    state = {"candidates": _lineage_fixture_candidates(),
             "incumbent_id": "down1-v0.1"}
    html_out = dashboard.render_pipeline(state)
    # all five columns present
    for col in ["Queue", "Evaluate", "Gate", "Submit", "Harvest"]:
        assert col in html_out
    # incumbent highlight class attached to the incumbent card
    assert "card-incumbent" in html_out
    # lineage arrow (non-ASCII) rendered for the 3rd-gen candidate
    assert "→" in html_out
    # detail panel: breakdown win rate 45/75 = 0.600, and the parsed commit
    assert "0.600" in html_out
    assert "45/75" in html_out
    assert "a1b2c3d4" in html_out


def test_score_chart_empty_is_safe():
    out = dashboard.render_score_chart([])
    assert "no score history yet" in out
    assert "<svg" not in out  # no chart, no crash


def test_score_chart_single_point_no_polyline():
    out = dashboard.render_score_chart([["2026-07-19T21:45", 560.0]])
    assert "<svg" in out
    assert "chart-band" in out       # band always drawn
    assert "<polyline" not in out    # single point: no line, no div-by-zero
    assert "<circle" in out


def test_score_chart_multi_point_has_polyline_and_band():
    out = dashboard.render_score_chart(
        [["2026-07-19T21:45", 560.0], ["2026-07-20T11:45", 545.0]])
    assert "<polyline" in out
    assert "chart-band" in out


def test_score_chart_all_equal_scores_no_zero_division():
    # equal scores -> zero score-range; must not raise
    out = dashboard.render_score_chart(
        [["t1", 550.0], ["t2", 550.0], ["t3", 550.0]])
    assert "<svg" in out


def test_score_chart_dict_shaped_entry_skipped_not_crash():
    # A hand-edited/partially-written candidates.json can produce a
    # dict-shaped history entry (e.g. {"ts": "t1", "score": 550}) instead
    # of the expected (ts, score) pair/list. One bad point must not kill
    # the whole chart render.
    out = dashboard.render_score_chart(
        [["t0", 500.0], {"ts": "t1", "score": 550.0}, ["t2", 560.0]])
    assert "<svg" in out
    assert out.count("<circle") == 2  # bad entry skipped, valid points kept


def test_render_history_one_chart_per_scored_candidate():
    state = {"candidates": [
        {"id": "a-v0.1", "score_history": [["t1", 560.0], ["t2", 550.0]]},
        {"id": "b-v0.1", "score_history": []},  # skipped: no history
    ]}
    out = dashboard.render_history(state)
    assert "a-v0.1" in out
    assert out.count("<svg") == 1


def test_render_digests_markdown_and_auth_line():
    state = {"digests": [{"name": "cycle-20260720-114500.md",
                          "text": "# Factory cycle digest\n\n## Gate actions\n"
                                  "- AUTH: auth-dead - kaggle auth check failed\n"}]}
    out = dashboard.render_digests(state)
    assert "Gate actions" in out
    assert "kaggle auth check failed" in out


def test_render_evolution_missing_key_backward_compat():
    out = dashboard.render_evolution({})  # no "evolution" key at all
    assert "evolution not seeded" in out


def test_render_evolution_not_seeded_placeholder():
    out = dashboard.render_evolution({"evolution": {"seeded": False}})
    assert "evolution not seeded" in out


def test_render_evolution_seeded_shows_counts_cells_and_lineage():
    state = {"evolution": {
        "seeded": True,
        "agent_counts": {"live": 2, "retired": 1},
        "deck_counts": {"live": 1},
        "top_cells": [{"agent_id": "ag-1", "deck_id": "dk-1",
                      "strength": 6.05, "games": 10}],
        "recent_births": [{"kind": "agent", "id": "ag-1", "born_at": "2026-07-19T10:00:00",
                           "lineage": ["ag-0"]}],
        "recent_retirements": [{"kind": "agent", "id": "ag-2",
                                "born_at": "2026-07-18T09:00:00", "lineage": []}],
    }}
    out = dashboard.render_evolution(state)
    assert "evolution not seeded" not in out
    assert "live: 2" in out and "retired: 1" in out
    assert "ag-1" in out and "dk-1" in out and "10 games" in out
    assert "ag-0" in out  # lineage rendered for the birth entry
    assert "ag-2" in out  # retirement entry rendered


def test_render_page_full_document():
    state = {
        "now": dt.datetime(2026, 7, 20, 12, 0),
        "utc_now": dt.datetime(2026, 7, 20, 22, 0, tzinfo=dt.timezone.utc),
        "paused": False,
        "counter": {"date": "2026-07-20", "count": 3}, "cap": 5,
        "candidates": [], "incumbent_id": None,
        "last_log": {"raw": "[2026-07-20T12:00] cycle: noop",
                     "timestamp": dt.datetime(2026, 7, 20, 12, 0),
                     "summary": "cycle: noop"},
        "stale": False, "next_expected": dt.datetime(2026, 7, 20, 12, 15),
        "digests": [], "auth_dead": None, "warnings": [],
    }
    out = dashboard.render_page(state)
    assert out.lstrip().lower().startswith("<!doctype html>")
    assert 'http-equiv="refresh"' in out
    assert "content=\"60\"" in out
    assert "http://" not in out and "https://" not in out  # zero external requests


def test_render_page_warning_banner():
    state = dict(_page_state_stub(), warnings=["candidates.json: boom"])
    out = dashboard.render_page(state)
    assert "warning-banner" in out
    assert "candidates.json: boom" in out


def _page_state_stub():
    return {"now": dt.datetime(2026, 7, 20, 12, 0),
            "utc_now": dt.datetime(2026, 7, 20, 22, 0, tzinfo=dt.timezone.utc),
            "paused": False,
            "counter": None, "cap": 5, "candidates": [], "incumbent_id": None,
            "last_log": None, "stale": False, "next_expected": None,
            "digests": [], "auth_dead": None, "warnings": []}


def test_write_dashboard_utf8_roundtrip(tmp_path):
    root = build_factory(
        tmp_path,
        candidates=[{"id": "x-v0.1", "name": "x", "version": "v0.1",
                     "deck": "src/ptcg/decks/candidates/x.csv",
                     "agent_kind": "heuristic", "status": "queued",
                     "is_incumbent": False, "provenance": "seed",
                     "score_history": [], "notes": ""}],
        counter={"date": "2026-07-20", "count": 0},
        watch_lines=["[2026-07-20T12:00] cycle: noop"])
    out_path = dashboard.write_dashboard(root, now=dt.datetime(2026, 7, 20, 12, 0))
    assert out_path == root / "experiments" / "factory" / "dashboard.html"
    text = out_path.read_text(encoding="utf-8")  # decodes cleanly as utf-8
    assert "<!doctype html>" in text.lower()
    # atomic tmp+replace: no leftover per-pid temp sibling after a normal write
    assert list(out_path.parent.glob("dashboard.html.*.tmp")) == []


def test_safe_render_poisoned_state_does_not_raise(tmp_path):
    root = build_factory(tmp_path,
                         counter={"date": "2026-07-20", "count": 0},
                         watch_lines=["[2026-07-20T12:00] cycle: noop"])
    (root / "experiments" / "factory" / "candidates.json").write_text(
        "{not json", encoding="utf-8")
    logged: list[str] = []
    out = dashboard.safe_render(root, log=logged.append)  # must NOT raise
    assert out is not None            # partial page still written
    text = out.read_text(encoding="utf-8")
    assert "warning-banner" in text
    assert "candidates.json" in text
    assert list(out.parent.glob("dashboard.html.*.tmp")) == []


def test_safe_render_malformed_worker_heartbeat_does_not_raise(tmp_path):
    root = build_factory(tmp_path, counter={"date": "2026-07-20", "count": 0},
                         watch_lines=["[2026-07-20T12:00] cycle: noop"])
    (root / "experiments" / "factory" / "matrix_heartbeat.json").write_text(
        "{bad json", encoding="utf-8")
    logged: list[str] = []
    out = dashboard.safe_render(root, log=logged.append)  # must NOT raise
    assert out is not None
    text = out.read_text(encoding="utf-8")
    assert "matrix_heartbeat.json" in text  # warning surfaced, not swallowed


def test_safe_render_pools_present_shows_evolution_panel(tmp_path):
    root = build_factory(tmp_path, counter={"date": "2026-07-20", "count": 0},
                         watch_lines=["[2026-07-20T12:00] cycle: noop"])
    _write_pool(root, "agent_pool.json",
               [_agent_row("ag-1", "live", 1.0, "2026-07-19T10:00:00")])
    _write_pool(root, "deck_pool.json",
               [_deck_row("dk-1", "live", 0.8, "2026-07-19T11:00:00")])
    logged: list[str] = []
    out = dashboard.safe_render(root, log=logged.append)
    assert out is not None
    text = out.read_text(encoding="utf-8")
    assert "evolution not seeded" not in text
    assert "ag-1" in text and "dk-1" in text


def test_safe_render_pools_absent_shows_not_seeded_placeholder(tmp_path):
    root = build_factory(tmp_path, counter={"date": "2026-07-20", "count": 0},
                         watch_lines=["[2026-07-20T12:00] cycle: noop"])
    logged: list[str] = []
    out = dashboard.safe_render(root, log=logged.append)
    assert out is not None
    text = out.read_text(encoding="utf-8")
    assert "evolution not seeded" in text


def test_safe_render_corrupt_pool_json_does_not_break_page(tmp_path):
    root = build_factory(tmp_path, counter={"date": "2026-07-20", "count": 0},
                         watch_lines=["[2026-07-20T12:00] cycle: noop"])
    (root / "experiments" / "factory" / "agent_pool.json").write_text(
        "{not valid json", encoding="utf-8")
    logged: list[str] = []
    out = dashboard.safe_render(root, log=logged.append)  # must NOT raise
    assert out is not None
    text = out.read_text(encoding="utf-8")
    assert "<!doctype html>" in text.lower()
    assert "warning-banner" in text
    assert "agent_pool.json" in text


def test_render_virgin_directory_first_run(tmp_path):
    # NOTHING pre-created: no experiments/factory dir at all (virgin-dir rule).
    root = tmp_path / "fresh_repo"          # this dir does not exist yet
    out = dashboard.write_dashboard(root, now=dt.datetime(2026, 7, 20, 12, 0))
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "RUNNING" in text                # not paused, all "no data yet"
    assert "no data yet" in text
    assert list(out.parent.glob("dashboard.html.*.tmp")) == []


def test_write_dashboard_concurrent_writers_use_distinct_temp_names(
        tmp_path, monkeypatch):
    """Two writer PROCESSES can race write_dashboard (the watch loop's
    busy-branch render fires outside the instance lock while the lock-holder
    also renders -- see instance_lock in factory_watch_once.py). Simulate
    that with two threads pinned to distinct fake os.getpid() values and a
    barrier that forces BOTH threads to have created+written their OWN temp
    file before EITHER is allowed to proceed to os.replace. If the temp name
    were shared (not per-pid) this step would have one thread's write clobber
    the other's in-flight temp content -- the exact race this fix closes.

    The two os.replace calls are then serialized with a lock (not left to
    race the OS syscall against each other): Windows' MoveFileEx can throw a
    transient PermissionError when two threads truly call replace() on the
    same destination at the same instant, but that kernel-level rename race
    is orthogonal to what this fix guarantees (distinct temp paths so writes
    never tear/collide) -- and every existing tmp+replace call site in this
    repo (gate.py, candidates.py, daemon.py, harvest.py, tournament/ledger.py)
    has the same unserialized-replace property, so it is not this fix's job
    to add cross-process replace serialization."""
    root = build_factory(tmp_path, counter={"date": "2026-07-20", "count": 0},
                         watch_lines=["[2026-07-20T12:00] cycle: noop"])
    out_path = root / "experiments" / "factory" / "dashboard.html"

    fake_pids = {"writer-a": 11111, "writer-b": 22222}
    write_barrier = threading.Barrier(2, timeout=5)
    replace_lock = threading.Lock()
    seen_tmp_names: list[str] = []
    real_write_text = Path.write_text
    real_replace = dashboard.os.replace

    def synced_write_text(self, data, *args, **kwargs):
        seen_tmp_names.append(self.name)
        result = real_write_text(self, data, *args, **kwargs)
        write_barrier.wait()  # hold here until BOTH temp files are written
        return result

    def serialized_replace(src, dst):
        with replace_lock:
            return real_replace(src, dst)

    monkeypatch.setattr(
        dashboard.os, "getpid",
        lambda: fake_pids[threading.current_thread().name])
    monkeypatch.setattr(Path, "write_text", synced_write_text)
    monkeypatch.setattr(dashboard.os, "replace", serialized_replace)

    results: dict[str, Path] = {}
    errors: list[BaseException] = []

    def run():
        try:
            results[threading.current_thread().name] = dashboard.write_dashboard(
                root, now=dt.datetime(2026, 7, 20, 12, 0))
        except BaseException as exc:  # noqa: BLE001 - surface in main thread
            errors.append(exc)

    threads = [threading.Thread(target=run, name=n) for n in fake_pids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, errors
    # both writers' temp filenames were captured while BOTH were live
    # (pre-replace) -- prove they never collided on the same path
    assert len(seen_tmp_names) == 2
    assert len(set(seen_tmp_names)) == 2, seen_tmp_names
    assert any("11111" in n for n in seen_tmp_names)
    assert any("22222" in n for n in seen_tmp_names)
    assert results["writer-a"] == out_path
    assert results["writer-b"] == out_path
    assert out_path.exists()
    text = out_path.read_text(encoding="utf-8")
    assert "<!doctype html>" in text.lower()  # final content intact, not torn
    assert list(out_path.parent.glob("dashboard.html.*.tmp")) == []


def test_write_uses_utf8_source_guard():
    # source grep-guard: every write in dashboard.py passes encoding="utf-8".
    src = Path(dashboard.__file__).read_text(encoding="utf-8")
    for line in src.splitlines():
        if ".write_text(" in line and "encoding=" not in line:
            raise AssertionError(f"write_text without encoding: {line.strip()}")


def test_render_dashboard_cli_writes_file(tmp_path, capsys):
    from scripts.render_dashboard import render_for_root
    root = build_factory(tmp_path, counter={"date": "2026-07-20", "count": 0},
                         watch_lines=["[2026-07-20T12:00] cycle: noop"])
    out = render_for_root(root)
    assert out.exists()
    assert out.read_text(encoding="utf-8").lower().startswith("<!doctype html>")
