"""Per-decision trajectory recording for value-net training (dev-only, never bundled)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from cg.api import Observation

from ptcg.agents.base import Agent
from ptcg.search.evaluate import evaluate
from ptcg.search.features import FEATURE_VERSION, extract


@dataclass
class DeviationTracker:
    """Game-level flag: set once EITHER seat deviates from v0 on a 1-of-1 select."""
    deviated: bool = False


@dataclass
class DecisionRecord:
    seat: int
    features: list[float]
    hte: float  # hand-tuned evaluator's score, the offline baseline column
    post_deviation: bool = False


class RecordingAgent(Agent):
    """Wraps any Agent; records features from the mover's perspective per decision.

    With v0_policy + tracker set, also maintains the game-level deviation flag:
    the record for the deviating decision itself stays d=0 (its state was reached
    on-policy); everything after is d=1."""

    def __init__(self, inner: Agent, sink: list[DecisionRecord],
                 v0_policy=None, tracker: DeviationTracker | None = None) -> None:
        self.inner = inner
        self.sink = sink
        self.v0_policy = v0_policy
        self.tracker = tracker
        self.name = f"rec({inner.name})"

    def act(self, obs: Observation) -> list[int]:
        st = obs.current
        live = st is not None and st.result == -1
        if live:
            seat = st.yourIndex
            self.sink.append(DecisionRecord(
                seat, extract(st, seat), evaluate(st, seat),
                post_deviation=bool(self.tracker and self.tracker.deviated)))
        result = self.inner.act(obs)
        if (live and self.tracker is not None and not self.tracker.deviated
                and self.v0_policy is not None):
            sel = obs.select
            if sel.minCount == 1 and sel.maxCount == 1 and len(sel.option) >= 2:
                try:
                    v0 = self.v0_policy(obs)
                except (ValueError, RuntimeError):
                    v0 = None
                if v0 is not None and sorted(result) != sorted(v0):
                    self.tracker.deviated = True
        return result


def label_records(sink: list[DecisionRecord], winner: int,
                  game_id: int, src: str = "v0") -> list[dict]:
    """winner: 0/1 seat index, 2 = draw (MatchResult convention)."""
    if winner not in (0, 1, 2):
        raise ValueError(
            f"label_records called on an errored/unknown match (winner={winner})")
    out = []
    for rec in sink:
        if winner in (0, 1):
            y = 1.0 if rec.seat == winner else 0.0
        else:
            y = 0.5
        out.append({"v": FEATURE_VERSION, "g": game_id, "s": rec.seat, "y": y,
                    "hte": round(rec.hte, 4), "d": int(rec.post_deviation),
                    "src": src, "x": [round(v, 4) for v in rec.features]})
    return out


def append_jsonl(records: list[dict], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
