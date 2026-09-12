"""Policy-target recording: schema, consume-once staleness guard, labeling."""
from types import SimpleNamespace

import pytest

from ptcg.train.policy_targets import (PolicyDecisionRecord,
                                       PolicyRecordingAgent,
                                       label_policy_records)


class FakeSearcher:
    def __init__(self):
        self.last_stats = None


class FakeInner:
    """Stands in for SearchAgent: name/cards/attacks/searcher/act."""
    name = "search-v1"

    def __init__(self, result, stats_after):
        self.cards, self.attacks = {}, {}
        self.searcher = FakeSearcher()
        self._result = result
        self._stats_after = stats_after

    def act(self, obs):
        self.searcher.last_stats = self._stats_after
        return self._result


def _obs(n_options=3, options=None):
    def _opt(cardId=None, attackId=None):
        return SimpleNamespace(type=1, cardId=cardId, attackId=attackId,
                               number=None, area=None, playerIndex=None,
                               toolIndex=None, energyIndex=None, count=None,
                               inPlayArea=None, inPlayIndex=None,
                               specialConditionType=None)
    opts = ([_opt(**o) for o in options] if options is not None
            else [_opt()] * n_options)
    return SimpleNamespace(
        select=SimpleNamespace(minCount=1, maxCount=1, option=opts),
        current=SimpleNamespace(result=-1, yourIndex=0,
                                # extract() is monkeypatched below; content unused
                                ))


def test_records_fresh_stats(monkeypatch):
    import ptcg.train.policy_targets as pt
    monkeypatch.setattr(pt, "extract", lambda st, seat, dbs=None: [0.0] * 40)
    stats = SimpleNamespace(root_visits_by_option={0: 7, 2: 3})
    sink = []
    agent = PolicyRecordingAgent(FakeInner([0], stats), sink)
    assert agent.act(_obs()) == [0]
    assert len(sink) == 1
    rec = sink[0]
    assert rec.visits == [7, 0, 3] and rec.chosen == 0
    assert len(rec.option_features) == 3
    # None cardId/attackId record as -1 (the "no card / no attack" sentinel).
    assert rec.option_ids == [[-1, -1], [-1, -1], [-1, -1]]


def test_records_raw_option_ids(monkeypatch):
    """Raw [cardId, attackId] are stored verbatim per option for the v3
    identity-embedding trainer; None -> -1."""
    import ptcg.train.policy_targets as pt
    monkeypatch.setattr(pt, "extract", lambda st, seat, dbs=None: [0.0] * 40)
    stats = SimpleNamespace(root_visits_by_option={0: 5, 1: 5})
    sink = []
    agent = PolicyRecordingAgent(FakeInner([0], stats), sink)
    obs = _obs(options=[{"cardId": 100, "attackId": 3},
                        {"cardId": 200, "attackId": None}])
    assert agent.act(obs) == [0]
    assert sink[0].option_ids == [[100, 3], [200, -1]]


def test_stale_stats_not_recorded(monkeypatch):
    """Inner falls back to v0 (does NOT write last_stats); pre-existing stale
    stats must be cleared, not recorded."""
    import ptcg.train.policy_targets as pt
    monkeypatch.setattr(pt, "extract", lambda st, seat, dbs=None: [0.0] * 40)
    inner = FakeInner([1], stats_after=None)
    inner.searcher.last_stats = SimpleNamespace(
        root_visits_by_option={0: 99})  # stale, from a previous decision
    sink = []
    agent = PolicyRecordingAgent(inner, sink)

    def act_without_stats(obs):  # fallback path: act() never touches last_stats
        return [1]
    inner.act = act_without_stats
    assert agent.act(_obs()) == [1]
    assert sink == []


def test_label_policy_records_outcomes():
    rec = PolicyDecisionRecord(seat=1, state_features=[0.0] * 40,
                               option_features=[[0.0] * 18] * 2,
                               option_ids=[[100, 3], [-1, -1]],
                               visits=[5, 5], chosen=1)
    rows = label_policy_records([rec], winner=1, game_id=3)
    assert rows[0]["y"] == 1.0 and rows[0]["g"] == 3 and rows[0]["pv"] == 2
    assert rows[0]["ids"] == [[100, 3], [-1, -1]]
    assert label_policy_records([rec], winner=0, game_id=3)[0]["y"] == 0.0
    assert label_policy_records([rec], winner=2, game_id=3)[0]["y"] == 0.5
    with pytest.raises(ValueError):
        label_policy_records([rec], winner=5, game_id=0)
