"""D4: quantify the determinization-noise floor. At sampled LIVE decision states,
rerun the full search K times with fresh determinization streams and measure how
often the picked move agrees and how much the root value estimate varies.

Complexity: O(states * reruns * iterations_per_search). At the production scale
(--states 50 --reruns 20 --budget-ms 200) that is ~50*20 = 1000 full searches; the
first real run IS the scale test — validate the runtime estimate before trusting it."""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptcg.agents.heuristic import HeuristicAgent  # noqa: E402
from ptcg.agents.search_agent import SearchAgent  # noqa: E402
from ptcg.arena.runner import load_deck, play_match  # noqa: E402
from ptcg.search.searcher import SearchConfig  # noqa: E402
from ptcg.search.timing import TimeManager  # noqa: E402


def probe_state(searcher, obs, deadline_fn, reruns: int, base_seed: int):
    """Rerun search() `reruns` times on ONE live obs with fresh determinization
    streams; return (move_agreement, root_value_std).

    The agreement denominator is the count of SUCCESSFUL reruns (len(sigs)), not
    the requested `reruns` — a rerun that fails to produce search_sig is dropped,
    so failed reruns shrink the denominator rather than counting as disagreement."""
    sigs = []
    values = []
    for k in range(reruns):
        searcher.rng = random.Random(base_seed + k)  # fresh determinization stream
        searcher.search(obs, deadline_fn())
        st = searcher.last_stats
        if st is None or st.search_sig is None:
            continue
        sigs.append(st.search_sig)
        values.append(st.top_child_value)
    if not sigs:
        return (0.0, 0.0)
    modal = max(set(sigs), key=sigs.count)
    agreement = sigs.count(modal) / len(sigs)
    std = statistics.pstdev(values) if len(values) > 1 else 0.0
    return (agreement, std)


class NoiseProbeAgent(SearchAgent):
    """SearchAgent that probes up to `max_states` of its own 1-of-1 decisions."""

    def __init__(self, deck, budget_ms, reruns, max_states):
        super().__init__(deck, config=SearchConfig(collect_stats=True),
                          time_manager=TimeManager(total_s=1e9, max_move_s=budget_ms / 1000.0),
                          collect_stats=True)
        self.reruns = reruns
        self.max_states = max_states
        self.records: list[tuple[float, float]] = []

    def act(self, obs):
        # Fire belief.update for THIS decision before the probe reruns, so probe
        # determinizations see the same revealed info the real move's search will
        # use. super().act(obs) below calls belief.update(obs) again on the same
        # obs — safe/idempotent (turn-guard + dict overwrite/pop are no-ops on a
        # repeated call with unchanged obs.logs/turn).
        self.belief.update(obs)
        sel = obs.select
        if (len(self.records) < self.max_states and sel.minCount == 1
                and sel.maxCount == 1 and len(sel.option) >= 2):
            deadline_fn = lambda: time.perf_counter() + self.tm.move_budget()  # noqa: E731
            self.records.append(
                probe_state(self.searcher, obs, deadline_fn, self.reruns, base_seed=1000))
        return super().act(obs)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--deck", default="src/ptcg/decks/candidates/mega-lucario-fighting.csv")
    p.add_argument("--states", type=int, default=50)
    p.add_argument("--reruns", type=int, default=20)
    p.add_argument("--budget-ms", type=int, default=200)
    p.add_argument("--max-games", type=int, default=20)
    args = p.parse_args()

    deck = load_deck(args.deck)
    probe_agent = NoiseProbeAgent(deck, args.budget_ms, args.reruns, args.states)
    opp = HeuristicAgent()
    t0 = time.perf_counter()
    games = 0
    while len(probe_agent.records) < args.states and games < args.max_games:
        play_match(probe_agent, opp, deck, deck)
        games += 1
    recs = probe_agent.records
    agreements = [a for a, _ in recs]
    stds = [s for _, s in recs]
    out = {
        "states_sampled": len(recs),
        "reruns_each": args.reruns,
        "budget_ms": args.budget_ms,
        "mean_move_agreement": statistics.mean(agreements) if agreements else 0.0,
        "mean_root_value_std": statistics.mean(stds) if stds else 0.0,
        "wall_seconds": round(time.perf_counter() - t0, 1),
    }
    path = ROOT / "experiments" / "instrumentation" / "slice5-D4-noise-probe.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
