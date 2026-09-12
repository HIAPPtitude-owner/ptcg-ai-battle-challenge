"""Spike: measure search API cost + game-length stats to size Slice-2 MCTS constants.

Run: uv run python scripts/spike_search_throughput.py
Throwaway measurement script (Slice 2 Task 1) — not imported by product code.
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cg import api  # noqa: E402
from cg.api import to_observation_class  # noqa: E402
from cg.game import battle_finish, battle_select, battle_start  # noqa: E402
from ptcg.agents.heuristic import HeuristicAgent  # noqa: E402
from ptcg.arena.runner import load_deck, play_match  # noqa: E402

DECK = load_deck(ROOT / "src/ptcg/decks/candidates/mega-lucario-fighting.csv")


def game_length_stats(n_games: int = 20) -> list[int]:
    a, b = HeuristicAgent(), HeuristicAgent()
    moves = []
    for _ in range(n_games):
        r = play_match(a, b, DECK, DECK)
        if r.error is None:
            moves.append(r.moves)
    return moves


def measure_search(turn_target: int = 6, n_begins: int = 30, steps_per_walk: int = 100):
    """Drive a live battle to mid-game, then time search_begin + search_step walks."""
    agent = HeuristicAgent()
    obs_dict, _ = battle_start(DECK, DECK)
    try:
        while True:
            obs = to_observation_class(obs_dict)
            st = obs.current
            if st.result != -1:
                raise RuntimeError("game ended before reaching target turn")
            if st.turn >= turn_target and obs.select.minCount == 1 and obs.select.maxCount == 1:
                break
            obs_dict = battle_select(agent.act(obs))

        me = st.yourIndex
        p_me, p_opp = st.players[me], st.players[1 - me]
        # Both decks known in the spike -> build oversized "everything left" predictions.
        # search_begin accepts lists LONGER than the zone count; it needs >=, not ==.
        seen_ids = [c.id for c in p_me.hand or []] + [c.id for c in p_me.discard]
        pool_me = list(DECK)
        for cid in seen_ids:
            if cid in pool_me:
                pool_me.remove(cid)
        pool_opp = list(DECK)
        for c in p_opp.discard:
            if c.id in pool_opp:
                pool_opp.remove(c.id)
        opp_active = [DECK[0]] if (p_opp.active and p_opp.active[0] is None) else []

        begin_ms, step_ms, indices_seen = [], [], set()
        for _ in range(n_begins):
            t0 = time.perf_counter()
            ss = api.search_begin(obs, pool_me, pool_me, pool_opp, pool_opp, pool_opp, opp_active)
            begin_ms.append((time.perf_counter() - t0) * 1000)
            cur = ss
            for _ in range(steps_per_walk):
                o = cur.observation
                if o.current.result != -1:
                    break
                indices_seen.add(o.current.yourIndex)
                sel = agent.act(o)
                t0 = time.perf_counter()
                cur = api.search_step(cur.searchId, sel)
                step_ms.append((time.perf_counter() - t0) * 1000)
            api.search_end()
        return begin_ms, step_ms, indices_seen
    finally:
        battle_finish()


def main() -> None:
    moves = game_length_stats()
    print(f"decisions/game over {len(moves)} games: "
          f"mean={statistics.mean(moves):.0f} p50={statistics.median(moves):.0f} "
          f"max={max(moves)}")
    begin_ms, step_ms, indices_seen = measure_search()
    for name, xs in (("search_begin ms", begin_ms), ("search_step ms", step_ms)):
        q = statistics.quantiles(xs, n=10)
        print(f"{name}: mean={statistics.mean(xs):.3f} p50={statistics.median(xs):.3f} "
              f"p90={q[8]:.3f} n={len(xs)}")
    print(f"both player indices stepped in search: {indices_seen == {0, 1}}")
    step = statistics.median(step_ms)
    begin = statistics.median(begin_ms)
    for budget_ms in (200, 1000):
        # one iteration ~ begin + max_depth(40) steps
        iters = int(budget_ms / (begin + 40 * step)) if (begin + 40 * step) > 0 else 0
        print(f"budget {budget_ms} ms -> ~{iters} iterations at depth 40")


if __name__ == "__main__":
    main()
