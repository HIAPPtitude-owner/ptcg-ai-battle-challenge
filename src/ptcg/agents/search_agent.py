"""SearchAgent: determinized-MCTS agent with heuristic-v0 safety net."""
from __future__ import annotations

import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

from cg.api import CardType, Observation, all_attack, all_card_data

from ptcg.agents.base import Agent
from ptcg.agents.heuristic import choose
from ptcg.search.belief import BeliefState
from ptcg.search.evaluate import evaluate
from ptcg.search.policy_net import PolicyNetPolicy
from ptcg.search.searcher import (EngineBackend, SearchBackend, SearchConfig,
                                  Searcher, SearchStats, forced_selection)
from ptcg.search.timing import TimeManager


class SearchAgent(Agent):
    name = "search-v1"

    def __init__(self, deck: list[int], config: SearchConfig | None = None,
                 time_manager: TimeManager | None = None,
                 backend: SearchBackend | None = None,
                 evaluator: Callable | None = None,
                 collect_stats: bool = False,
                 policy_weights: str | Path | None = None) -> None:
        self.cards = {c.cardId: c for c in all_card_data()}
        self.attacks = {a.attackId: a for a in all_attack()}
        basic_ids = frozenset(c.cardId for c in all_card_data()
                              if c.cardType == CardType.POKEMON and c.basic)
        self.belief = BeliefState(list(deck), basic_ids)
        self.tm = time_manager or TimeManager()
        cfg = config or SearchConfig()
        if collect_stats:
            cfg = replace(cfg, collect_stats=True)
        self.policy = None
        want_policy = (cfg.use_tree_prior or cfg.policy_opponent
                       or cfg.policy_rollout)
        if policy_weights is not None and want_policy:
            try:
                self.policy = PolicyNetPolicy.load(
                    policy_weights, fallback=self._policy,
                    dbs=(self.cards, self.attacks))
            except (OSError, ValueError, KeyError) as exc:
                print(f"policy-net load failed ({exc}); "
                      f"injection flags disabled", file=sys.stderr)
                cfg = replace(cfg, use_tree_prior=False,
                              policy_opponent=False, policy_rollout=False)
        policy = self._policy
        opponent = self.policy if (self.policy and cfg.policy_opponent) else policy
        rollout = self.policy if (self.policy and cfg.policy_rollout) else policy
        prior_fn = (self.policy.priors_for
                    if (self.policy and cfg.use_tree_prior) else None)
        # NOTE: Searcher._v0_sig derives the v0-improvement gate's anchor from
        # rollout_policy. With policy_rollout on, that anchor becomes the NET's
        # move, not v0's. The pre-registered search-policy operating config
        # therefore runs gate-off (deviate_min_visits=0, deviate_value_edge=0).
        self.searcher = Searcher(self.belief, backend or EngineBackend(),
                                 opponent_policy=opponent,
                                 rollout_policy=rollout,
                                 evaluator=evaluator if evaluator is not None
                                 else evaluate,
                                 config=cfg, prior_fn=prior_fn)
        self.fallbacks = 0
        self.decision_stats: list[SearchStats] = []
        if evaluator is not None:
            self.name = "search-net-v1"
        if self.policy is not None:
            self.name = "search-policy"

    def _policy(self, obs: Observation) -> list[int]:
        return choose(obs, self.cards, self.attacks)

    def act(self, obs: Observation) -> list[int]:
        t0 = time.perf_counter()
        try:
            self.belief.update(obs)
            sel = obs.select
            forced = forced_selection(sel)
            if forced is not None:
                return forced
            if sel.minCount != 1 or sel.maxCount != 1:
                return self._policy(obs)
            deadline = t0 + self.tm.move_budget()
            result = self.searcher.search(obs, deadline)
            if self.searcher.last_stats is not None:
                self.decision_stats.append(self.searcher.last_stats)
            if result is None:
                self.fallbacks += 1
                return self._policy(obs)
            return result
        except Exception:  # noqa: BLE001 — v0 fallback is the safety invariant
            self.fallbacks += 1
            return self._policy(obs)
        finally:
            self.tm.note_move(time.perf_counter() - t0)
