"""tests/test_trajectory.py"""
import json

import pytest

from cg.api import Option, OptionType
from ptcg.agents.base import Agent
from ptcg.agents.heuristic import HeuristicAgent
from ptcg.arena.runner import load_deck, play_match
from ptcg.search.features import FEATURE_NAMES, FEATURE_VERSION
from ptcg.train.trajectory import (DecisionRecord, DeviationTracker,
                                   RecordingAgent, append_jsonl, label_records)
from tests.fixtures.obs import make_obs, make_select, make_state


class FakeAgent(Agent):
    """Returns entries from `answers` in order (repeats the last once exhausted)."""

    def __init__(self, answers: list[list[int]]) -> None:
        self.answers = answers
        self.calls = 0
        self.name = "fake"

    def act(self, obs) -> list[int]:
        i = min(self.calls, len(self.answers) - 1)
        self.calls += 1
        return self.answers[i]


def _one_of_two_select():
    return make_select([Option(type=OptionType.YES), Option(type=OptionType.NO)],
                       min_count=1, max_count=1)


def test_real_game_produces_labeled_records(tmp_path):
    deck = load_deck("tests/fixtures/sample_deck.csv")
    sink0: list = []
    sink1: list = []
    a0 = RecordingAgent(HeuristicAgent(), sink0)
    a1 = RecordingAgent(HeuristicAgent(), sink1)
    result = play_match(a0, a1, deck, deck)
    assert result.error is None
    assert all(r.seat == 0 for r in sink0)
    assert all(r.seat == 1 for r in sink1)
    sink = sink0 + sink1
    assert len(sink) > 10  # a real game has dozens of decisions
    records = label_records(sink, result.winner, game_id=7)
    assert {r["s"] for r in records} == {0, 1}
    for r in records:
        assert r["v"] == FEATURE_VERSION and r["g"] == 7
        assert len(r["x"]) == len(FEATURE_NAMES)
        assert 0.0 <= r["hte"] <= 1.0
        if result.winner in (0, 1):
            assert r["y"] == (1.0 if r["s"] == result.winner else 0.0)
        else:
            assert r["y"] == 0.5
    out = tmp_path / "traj.jsonl"
    append_jsonl(records, out)
    append_jsonl(records[:3], out)  # append mode
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == len(records) + 3
    assert json.loads(lines[0])["g"] == 7


def test_label_records_rejects_errored_match():
    with pytest.raises(ValueError):
        label_records([DecisionRecord(0, [0.0], 0.5)], -1, game_id=0)


def test_deck_pairings_round_robin_with_mirrors():
    from pathlib import Path

    from scripts.generate_training_data import deck_pairings
    pairs = deck_pairings(Path("src/ptcg/decks/candidates"))
    n = len(list(Path("src/ptcg/decks/candidates").glob("*.csv")))
    assert n >= 9  # deck count grows as candidates are added; the round-robin
    # pairing invariant below is the real property under test, not the count
    assert len(pairs) == n * (n + 1) // 2  # 45: unordered pairs + mirrors
    assert all(a.suffix == ".csv" and b.suffix == ".csv" for a, b in pairs)


def test_deviation_tracker_flips_once_and_tags_later_records():
    sink: list[DecisionRecord] = []
    tracker = DeviationTracker()
    inner = FakeAgent([[1], [1]])
    rec_agent = RecordingAgent(inner, sink, v0_policy=lambda obs: [0], tracker=tracker)
    obs = make_obs(_one_of_two_select())

    rec_agent.act(obs)  # decision 1: state reached on-policy; inner picks [1], v0 picks [0]
    assert tracker.deviated is True  # flip happens AFTER this decision's record is made
    rec_agent.act(obs)  # decision 2: state is already post-deviation

    assert len(sink) == 2
    assert sink[0].post_deviation is False
    assert sink[1].post_deviation is True


def test_no_v0_policy_means_never_deviated():
    sink: list[DecisionRecord] = []
    tracker = DeviationTracker()
    inner = FakeAgent([[1], [1]])
    rec_agent = RecordingAgent(inner, sink, v0_policy=None, tracker=tracker)
    obs = make_obs(_one_of_two_select())

    rec_agent.act(obs)
    rec_agent.act(obs)

    assert tracker.deviated is False
    assert all(r.post_deviation is False for r in sink)


def test_multi_select_and_forced_do_not_flip_tracker():
    sink: list[DecisionRecord] = []
    tracker = DeviationTracker()
    inner = FakeAgent([[0], [0, 1]])
    rec_agent = RecordingAgent(inner, sink, v0_policy=lambda obs: [1], tracker=tracker)

    # len(option) < 2: engine forced a single legal option, no real choice to compare
    forced_select = make_select([Option(type=OptionType.YES)], min_count=1, max_count=1)
    rec_agent.act(make_obs(forced_select))
    assert tracker.deviated is False

    # maxCount != 1: multi-select, not a 1-of-1 decision
    multi_select = make_select(
        [Option(type=OptionType.YES), Option(type=OptionType.NO)], min_count=0, max_count=2)
    rec_agent.act(make_obs(multi_select))
    assert tracker.deviated is False
    assert all(r.post_deviation is False for r in sink)


def test_label_records_emits_d_and_src():
    sink = [
        DecisionRecord(0, [0.1], 0.5, post_deviation=False),
        DecisionRecord(1, [0.2], 0.6, post_deviation=True),
    ]
    records = label_records(sink, winner=0, game_id=3, src="search")
    assert len(records) == 2
    assert records[0]["d"] == 0 and records[0]["src"] == "search"
    assert records[1]["d"] == 1 and records[1]["src"] == "search"

    default_records = label_records(sink, winner=0, game_id=3)
    assert all(r["src"] == "v0" for r in default_records)


def test_shared_tracker_across_seats():
    sink0: list[DecisionRecord] = []
    sink1: list[DecisionRecord] = []
    tracker = DeviationTracker()
    agent0 = RecordingAgent(FakeAgent([[1]]), sink0, v0_policy=lambda obs: [0], tracker=tracker)
    agent1 = RecordingAgent(FakeAgent([[0]]), sink1, v0_policy=lambda obs: [0], tracker=tracker)

    obs0 = make_obs(_one_of_two_select(), state=make_state(your_index=0))
    obs1 = make_obs(_one_of_two_select(), state=make_state(your_index=1))

    agent0.act(obs0)  # seat 0 deviates ([1] != v0's [0]); tracker flips after its own record
    assert tracker.deviated is True

    agent1.act(obs1)  # seat 1's record must already reflect the shared flip

    assert sink0[0].post_deviation is False
    assert sink1[0].post_deviation is True
