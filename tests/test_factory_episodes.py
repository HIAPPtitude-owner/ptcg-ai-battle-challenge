"""Tests for the Kaggle episode harvester (Task 10).

Fixtures mirror the REAL episode-replay schema discovered by
scripts/probe_episode_schema.py (see experiments/factory/episodes/schema-probe.txt):
  - top-level: info.TeamNames / info.Agents[].Name, rewards, statuses, steps
  - decks live at steps[0][0].visualize[0].action == [deck0(60), deck1(60)]
  - game-turn count at the last populated observation.current.turn
  - OUR episodes are the ones whose TeamNames contain "BSCode"
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from ptcg.factory import episodes as ep
from ptcg.factory.cycle import FactoryPaths
from ptcg.factory.kaggle_client import FakeKaggleClient

OURS = ep.OUR_TEAM_NAME  # "BSCode"


# --- fixture builders (real-schema-shaped) ---------------------------------

def make_episode(team_names, rewards, decks, *, statuses=("DONE", "DONE"),
                 turn=8, episode_id=1):
    return {
        "schema_version": 1, "name": "cabt",
        "info": {
            "TeamNames": list(team_names),
            "Agents": [{"Name": n, "ThumbnailUrl": None} for n in team_names],
            "EpisodeId": episode_id,
        },
        "rewards": list(rewards),
        "statuses": list(statuses),
        "steps": [
            [
                {"observation": {"current": None},
                 "visualize": [{"action": [list(decks[0]), list(decks[1])]}]},
                {"observation": {"current": None}},
            ],
            [
                {"observation": {"current": {"turn": turn}}},
                {"observation": {"current": {"turn": turn}}},
            ],
        ],
    }


def deck(v):
    return [v] * 60


def make_day_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            if isinstance(content, (dict, list)):
                content = json.dumps(content)
            z.writestr(name, content)
    return buf.getvalue()


def manifest_csv(days) -> str:
    header = ("date,daily_dataset_slug,daily_dataset_url,episode_count,"
              "total_bytes,top_avg_score,median_avg_score")
    rows = [header]
    for d in days:
        rows.append(f"{d},pokemon-tcg-ai-battle-episodes-{d},"
                    f"https://kaggle.com/x,10,1000,900.0,600.0")
    return "\n".join(rows) + "\n"


def day_ref(d):
    return ep.DAY_DATASET_FMT.format(day=d)


def day_zip_name(d):
    return f"pokemon-tcg-ai-battle-episodes-{d}.zip"


def build_client(days, day_files):
    files_by_dataset = {ep.INDEX_DATASET: {"manifest.csv": manifest_csv(days)}}
    for d, files in day_files.items():
        files_by_dataset[day_ref(d)] = {day_zip_name(d): make_day_zip(files)}
    return FakeKaggleClient(files_by_dataset=files_by_dataset)


class Now:
    def isoformat(self):
        return "2026-07-21T00:00:00"


# --- (a) stamp round-trip + virgin-dir -------------------------------------

def test_stamp_roundtrip_and_virgin_dir(tmp_path):
    stamp = tmp_path / "never" / "made" / "harvest_stamp.json"  # virgin parents
    assert ep.load_stamp(stamp) is None
    ep.save_stamp(stamp, "2026-07-19", Now())
    assert stamp.exists()
    assert ep.load_stamp(stamp) == "2026-07-19"


# --- (b) no-new short-circuit ----------------------------------------------

def test_no_new_short_circuits(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    ep_dir = tmp_path / "experiments" / "factory" / "episodes"
    ep.save_stamp(ep_dir / "harvest_stamp.json", "2026-07-20", Now())
    # manifest newest == stamp -> nothing newer
    client = build_client(["2026-07-19", "2026-07-20"], {})
    res = ep.check_and_harvest(paths, client, now=Now())
    assert res == "no-new"
    # day dataset must NOT have been downloaded
    assert all(day_ref("2026-07-20") != d for d, *_ in client.downloads)


# --- (c) harvest path: extracts written, raw deleted, stamp advanced --------

def test_harvest_extracts_and_deletes_raw_and_advances_stamp(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    ep_dir = tmp_path / "experiments" / "factory" / "episodes"
    ours = make_episode([OURS, "Rival"], [1, -1], [deck(5), deck(9)],
                        turn=7, episode_id=111)
    theirs = make_episode(["A", "B"], [1, -1], [deck(1), deck(2)], episode_id=222)
    client = build_client(["2026-07-20"],
                          {"2026-07-20": {"111.json": ours, "222.json": theirs}})
    res = ep.check_and_harvest(paths, client, now=Now())
    assert res == "harvested:2026-07-20"

    extracts = ep_dir / "extracts.jsonl"
    recs = [json.loads(x) for x in extracts.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(recs) == 1  # only OUR episode
    r = recs[0]
    assert r["episode_id"] == "111"
    assert r["our_result"] == "win"
    assert r["opponent_deck"] == deck(9)  # the rival's deck
    assert r["timeout"] is False
    assert r["n_turns"] == 7
    assert r["day"] == "2026-07-20"
    assert isinstance(r["opponent_deck_hash"], str) and r["opponent_deck_hash"]

    # raw temp dir deleted
    assert not (ep_dir / "_raw").exists()
    # stamp advanced
    assert ep.load_stamp(ep_dir / "harvest_stamp.json") == "2026-07-20"


def test_harvest_loss_result(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    ep_dir = tmp_path / "experiments" / "factory" / "episodes"
    # our team is agent index 1, reward -1 -> loss; opponent is index 0
    ours = make_episode(["Rival", OURS], [1, -1], [deck(3), deck(4)], episode_id=5)
    client = build_client(["2026-07-20"], {"2026-07-20": {"5.json": ours}})
    ep.check_and_harvest(paths, client, now=Now())
    r = json.loads((ep_dir / "extracts.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert r["our_result"] == "loss"
    assert r["opponent_deck"] == deck(3)


# --- (d) malformed episode skipped + counted, run completes -----------------

def test_malformed_episode_skipped_and_counted(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    ep_dir = tmp_path / "experiments" / "factory" / "episodes"
    ours = make_episode([OURS, "Rival"], [1, -1], [deck(5), deck(9)], episode_id=1)
    # malformed but contains our team name in bytes (forces the parse attempt)
    malformed = '{"info": {"TeamNames": ["BSCode", "X"]}, broken json'
    client = build_client(
        ["2026-07-20"],
        {"2026-07-20": {"1.json": ours, "2.json": malformed}})
    res = ep.check_and_harvest(paths, client, now=Now())
    assert res == "harvested:2026-07-20"
    recs = [json.loads(x) for x in (ep_dir / "extracts.jsonl").read_text(
        encoding="utf-8").splitlines() if x.strip()]
    assert len(recs) == 1  # malformed one skipped, valid one kept


# --- (e) cap pruning: oldest day pruned ------------------------------------

def test_cap_pruning_drops_oldest_day(tmp_path):
    extracts = tmp_path / "extracts.jsonl"
    lines = []
    for d in ("2026-07-01", "2026-07-02", "2026-07-03"):
        for i in range(50):
            lines.append(json.dumps({"episode_id": f"{d}-{i}", "day": d,
                                     "opponent_deck": deck(1), "our_result": "win",
                                     "timeout": False, "n_turns": 5,
                                     "opponent_deck_hash": "x"}))
    extracts.write_text("\n".join(lines) + "\n", encoding="utf-8")
    size = extracts.stat().st_size
    # cap below current size -> must drop the oldest day(s)
    removed = ep._prune_cap(extracts, cap_bytes=size * 2 // 3)
    assert removed >= 1
    kept_days = {json.loads(x)["day"] for x in extracts.read_text(
        encoding="utf-8").splitlines() if x.strip()}
    assert "2026-07-01" not in kept_days  # oldest pruned
    assert "2026-07-03" in kept_days      # newest retained


# --- (f) failure isolation at watch level ----------------------------------

def test_watch_isolates_harvest_failure(tmp_path):
    """Post-cutover: a raising harvester must NOT block the submission
    scheduler -- the watch loop failure-isolates the harvest step."""
    import scripts.factory_watch_once as w

    paths = FactoryPaths(root=tmp_path)
    (tmp_path / "experiments" / "factory" / "logs").mkdir(parents=True)

    client = FakeKaggleClient()
    calls = {"submit": 0, "harvest": 0}

    def fake_submit(conn, client, counter, state_path, out_dir, repo, now,
                    *, no_submit=False, log=print):
        calls["submit"] += 1
        return []

    def raising_harvest(paths, client, *, now, log=print):
        calls["harvest"] += 1
        raise RuntimeError("kaggle down")

    res = w.watch_once(paths, client, db_path=tmp_path / "t.db",
                       submit_fn=fake_submit, harvest_fn=raising_harvest,
                       log=lambda *a: None)
    # harvest WAS attempted and raised, but the submission scheduler still ran
    assert calls["harvest"] == 1
    assert calls["submit"] == 1
    assert res.get("harvest") == "error"


def test_watch_runs_harvest_before_submit(tmp_path):
    """Post-cutover: the episode harvester runs ahead of the submission
    scheduler (harvest is the loop's first in-lock step)."""
    import scripts.factory_watch_once as w

    paths = FactoryPaths(root=tmp_path)
    (tmp_path / "experiments" / "factory" / "logs").mkdir(parents=True)
    order = []

    def fake_harvest(paths, client, *, now, log=print):
        order.append("harvest")
        return "no-new"

    def fake_submit(conn, client, counter, state_path, out_dir, repo, now,
                    *, no_submit=False, log=print):
        order.append("submit")
        return []

    w.watch_once(paths, FakeKaggleClient(), db_path=tmp_path / "t.db",
                 submit_fn=fake_submit, harvest_fn=fake_harvest, log=lambda *a: None)
    assert order == ["harvest", "submit"]


def test_check_and_harvest_returns_error_on_client_raise(tmp_path):
    paths = FactoryPaths(root=tmp_path)

    class Raiser(FakeKaggleClient):
        def dataset_download(self, *a, **k):
            raise RuntimeError("boom")

    res = ep.check_and_harvest(paths, Raiser(), now=Now())
    assert res.startswith("error:")
