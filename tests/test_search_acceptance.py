"""Slice-2 acceptance gate (FINAL, parity close): tuned search-v1 is a
catastrophic-regression guard against heuristic-v0, not a beat-v0 bar.

History, in order:

1. Original bar (200 games, win rate >= 0.55 vs heuristic-v0, Wilson 95% LCB
   > 0.50): set on the assumption that search should dominate the heuristic
   it wraps.

2. Tuning diagnosis (E1-E9, see experiments/EXPERIMENTS.md): nine logged
   tuning experiments found the true ceiling instead. The hand-tuned leaf
   evaluator is a WEAKER move-ranking signal than heuristic-v0's crafted
   priorities, so ISMCTS deviations from v0 are net-negative unless gated.
   Search forced to always play v0 == 50% by construction (mirror match).
   Tuning lifted the untuned baseline pilot (42.0%, 21-29-0, n=50) to a
   pooled ~52-54% win rate via a v0-improvement gate + short rollout leaf,
   but no configuration approached 57%+ observed (what LCB > 0.50
   effectively requires at n=200).

3. First revision (Brad-authorized, 2026-07-09): bar lowered to observed
   >= 0.48 with Wilson LCB > 0.40 over 200 games vs heuristic-v0, framed as
   "beat the untuned baseline's measured 42.0%". Outcome: run 1 (assertion-only,
   counts not captured, 445.49s) PASSED. Run 2 (identical config, -s with
   stats visible, 481.21s) FAILED: wins_a=91, wins_b=109, draws=0, win rate
   0.455, max_move_seconds 0.242. The split result proved the 0.48 bar is a
   coin-flip at this config's true strength, not a reliable signal.

4. Final Brad-authorized close (2026-07-09): the honest pooled estimate for
   the best tuned config (eval-v2 KO-awareness + rollout_depth=12 +
   v0-improvement gate [deviate_min_visits=20, deviate_value_edge=0.12],
   c_uct=1.4) is PARITY with heuristic-v0 (~47-49% pooled across pilots and
   both 200g runs), not a genuine improvement. This test is downgraded to a
   catastrophic-regression guard: the 0.40 bar passes ~98% of the time at
   true parity and only fails hard on a genuine regression (e.g. a broken
   gate, a sign error in the evaluator, or a timing regression that starves
   the search). Beating heuristic-v0 outright is Slice-4's job — a learned
   value net replaces the hand-tuned evaluator; the KO-awareness helpers in
   ptcg.search.evaluate are its seed.

5. Slice-4 outcome (2026-07-10, GATE FAILED): trained a torch MLP value net
   on 13,500 v0-self-play games (1.33M positions, split by game) and
   exported it to pure Python for the arena. Offline, the net is decisively
   better than the hand-tuned evaluator it replaces — val AUC 0.8971 vs
   0.7640, BCE 0.3955 vs 0.6079. In the arena it did not move the needle:
   exp1 (net as terminal rollout eval) and exp2 (net as leaf eval,
   rollout_depth=0) both landed at 94-106-0, 0.470 [0.402, 0.539] over 200
   games. Per the stochastic-gate-replication rule, the acceptance run
   (bar 0.55, 300 games) was replicated: run 1 was 156-144-0, 0.520
   [0.464, 0.576]; run 2 was 143-157-0, 0.477 [0.421, 0.533]; pooled
   299/600 = 0.498, comfortably outside the [0.52, 0.58] near-bar band, so
   no third run was warranted. All runs logged in experiments/EXPERIMENTS.md
   (2026-07-10). Max move time across every run stayed <=0.387s, so this is
   not a ladder-timeout story. Conclusion: evaluator quality was NOT the
   binding bottleneck for search-v1 — a strictly better value function
   produced the same arena parity as the hand-tuned one. The search
   architecture itself (the v0-improvement gate, determinization noise in
   the belief-v1 mirror prior, or the iteration budget) is the prime
   suspect for Slice-5. The ladder submission stays on heuristic-v0.
"""
from pathlib import Path

import pytest

from ptcg.agents.heuristic import HeuristicAgent
from ptcg.agents.search_agent import SearchAgent
from ptcg.arena.runner import load_deck, run_series
from ptcg.search.timing import TimeManager

DECK = load_deck(Path(__file__).resolve().parents[1]
                 / "src/ptcg/decks/candidates/mega-lucario-fighting.csv")


@pytest.mark.slow
def test_search_no_catastrophic_regression_vs_v0():
    search = SearchAgent(DECK, time_manager=TimeManager(total_s=1e9, max_move_s=0.2))
    stats = run_series(search, HeuristicAgent(), DECK, DECK, n_games=200)
    print(f"\n{stats}")  # always capture counts, even on a passing run
    assert stats.win_rate_a >= 0.40, (
        f"win rate {stats.win_rate_a:.3f} < 0.40 — catastrophic regression vs "
        f"heuristic-v0 parity baseline ({stats})"
    )
