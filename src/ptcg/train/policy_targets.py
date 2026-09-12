"""Per-decision policy-target recording (dev-only, never bundled).

Expert-iteration training signal: the search root's visit distribution over
options, plus state and per-option action features. One JSONL row per decision
the search actually completed (1-of-1 select, >=2 options, fresh SearchStats)."""
from __future__ import annotations

from dataclasses import dataclass

from cg.api import Observation

from ptcg.agents.base import Agent
from ptcg.search.action_features import ACTION_FEATURE_VERSION, extract_action
from ptcg.search.features import FEATURE_VERSION, extract


@dataclass
class PolicyDecisionRecord:
    seat: int
    state_features: list[float]
    option_features: list[list[float]]
    option_ids: list[list[int]]  # raw [cardId_or_-1, attackId_or_-1] per option
    visits: list[int]
    chosen: int


class PolicyRecordingAgent(Agent):
    """Wraps a collect_stats=True SearchAgent.

    Consume-once contract: `searcher.last_stats` is cleared BEFORE inner.act so
    a stale stats object from a previous decision (the v0-fallback path never
    refreshes it) can never be misattributed to this decision."""

    def __init__(self, inner, sink: list[PolicyDecisionRecord]) -> None:
        self.inner = inner
        self.sink = sink
        self.name = f"ptgt({inner.name})"

    def act(self, obs: Observation) -> list[int]:
        st = obs.current
        searcher = self.inner.searcher
        searcher.last_stats = None
        result = self.inner.act(obs)
        stats = searcher.last_stats
        if (st is not None and st.result == -1 and stats is not None
                and getattr(stats, "root_visits_by_option", None)
                and len(result) == 1):
            sel = obs.select
            seat = st.yourIndex
            cards, attacks = self.inner.cards, self.inner.attacks
            opts = [extract_action(o, cards, attacks) for o in sel.option]
            ids = [[o.cardId if o.cardId is not None else -1,
                    o.attackId if o.attackId is not None else -1]
                   for o in sel.option]
            visits = [stats.root_visits_by_option.get(i, 0)
                      for i in range(len(sel.option))]
            if sum(visits) >= 1:
                self.sink.append(PolicyDecisionRecord(
                    seat, extract(st, seat), opts, ids, visits, result[0]))
        return result


def label_policy_records(sink: list[PolicyDecisionRecord], winner: int,
                         game_id: int) -> list[dict]:
    """winner: 0/1 seat index, 2 = draw (MatchResult convention)."""
    if winner not in (0, 1, 2):
        raise ValueError(
            f"label_policy_records called on an errored match (winner={winner})")
    out = []
    for rec in sink:
        y = 0.5 if winner == 2 else (1.0 if rec.seat == winner else 0.0)
        out.append({"v": FEATURE_VERSION, "pv": ACTION_FEATURE_VERSION,
                    "g": game_id, "s": rec.seat, "y": y,
                    "x": [round(v, 4) for v in rec.state_features],
                    "opts": [[round(v, 4) for v in o]
                             for o in rec.option_features],
                    "ids": [[int(a), int(b)] for a, b in rec.option_ids],
                    "n": rec.visits, "chosen": rec.chosen})
    return out
