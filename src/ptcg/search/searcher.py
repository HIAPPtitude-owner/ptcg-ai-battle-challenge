"""ISMCTS driver: shared tree over determinizations, opponent as fixed policy.

Every iteration restarts from the real root observation (the engine only allows
search_begin on it) and replays down the tree with search_step. backend.end()
runs after every iteration so the engine's search arena never grows.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from cg import api
from cg.api import Observation, SearchState, SelectData

from ptcg.search.belief import BeliefState, Determinization
from ptcg.search.evaluate import evaluate
from ptcg.search.tree import ActionSig, Node, action_signature, puct_pick, ucb_pick

Policy = Callable[[Observation], list[int]]


def forced_selection(select: SelectData) -> list[int] | None:
    n = len(select.option)
    if select.maxCount == 0:
        return []
    if select.minCount == n and select.maxCount == n:
        return list(range(n))
    if n == 1 and select.minCount >= 1:
        return [0]
    return None


class SearchBackend(Protocol):
    def begin(self, obs: Observation, det: Determinization) -> SearchState: ...
    def step(self, search_id: int, select: list[int]) -> SearchState: ...
    def end(self) -> None: ...


class EngineBackend:
    def begin(self, obs: Observation, det: Determinization) -> SearchState:
        return api.search_begin(obs, det.your_deck, det.your_prize,
                                det.opponent_deck, det.opponent_prize,
                                det.opponent_hand, det.opponent_active)

    def step(self, search_id: int, select: list[int]) -> SearchState:
        return api.search_step(search_id, select)

    def end(self) -> None:
        api.search_end()


@dataclass
class SearchStats:
    """Per-decision provenance. Read-only view of what search() already computed;
    populating it changes NO move choice (guard: test_stats_flag_does_not_change_move_sequence)."""
    iterations: int
    root_children: int
    top_child_visits: int
    top_child_value: float
    v0_sig: ActionSig | None
    search_sig: ActionSig | None
    most_visited_sig: ActionSig | None
    deviated: bool
    gate_blocked: bool
    begin_failures: int
    step_failures: int
    root_visits_by_option: dict[int, int] | None = None


@dataclass
class SearchConfig:
    c_uct: float = 1.4
    max_iterations: int = 400
    max_depth: int = 40
    seed: int = 0
    rollout_depth: int = 12  # 0 = static leaf eval; >0 = v0 rollout plies before eval
    # v0-improvement gate (value-based): keep v0's move unless a challenger is both
    # well-sampled and evaluates meaningfully higher. 0 edge + 0 visits = pure MCTS.
    deviate_min_visits: int = 20
    deviate_value_edge: float = 0.12
    collect_stats: bool = False  # populate Searcher.last_stats each search(); zero behavior change
    deviate_min_visit_frac: float = 0.0  # consumed in Slice-5 F1 (Task 10); 0.0 = disabled
    final_move_rule: str = "most_visited"  # consumed in Slice-5 F3 (Task 12)
    robust_min_visits: int = 5  # min visits to be "max_value"-eligible (Slice-5 F3)
    # Slice-6 F4: value-derived root prior
    use_root_prior: bool = False
    prior_tau: float = 0.1
    c_puct: float = 1.5
    # Slice-7B: policy-net injection. use_tree_prior is consumed here (all-node
    # PUCT via prior_fn); policy_opponent/policy_rollout are consumed by
    # SearchAgent's policy wiring, carried in config so one object describes
    # the full injection state.
    use_tree_prior: bool = False
    policy_opponent: bool = False
    policy_rollout: bool = False


class Searcher:
    def __init__(self, belief: BeliefState, backend: SearchBackend,
                 opponent_policy: Policy, rollout_policy: Policy,
                 evaluator: Callable = evaluate,
                 config: SearchConfig | None = None,
                 prior_fn: Callable[[Observation], dict | None] | None = None) -> None:
        self.belief = belief
        self.backend = backend
        self.opponent_policy = opponent_policy
        self.rollout_policy = rollout_policy
        self.evaluator = evaluator
        self.config = config or SearchConfig()
        self.prior_fn = prior_fn
        self.rng = random.Random(self.config.seed)
        self.iterations_run = 0
        self.begin_failures = 0
        self.step_failures = 0
        self._deadline = float("inf")
        self.last_stats: SearchStats | None = None

    def search(self, obs: Observation, deadline: float) -> list[int] | None:
        cfg = self.config
        my_index = obs.current.yourIndex
        root_index = {action_signature(obs.select, (i,)): i
                      for i in range(len(obs.select.option))}
        root = Node()
        self.iterations_run = 0
        self.begin_failures = 0
        self.step_failures = 0
        self._deadline = deadline
        priors: dict[ActionSig, float] | None = None
        if cfg.use_root_prior:
            priors = self._root_priors(obs, my_index)
        while time.perf_counter() < deadline and self.iterations_run < cfg.max_iterations:
            self.iterations_run += 1
            det = self.belief.sample(obs, self.rng)
            try:
                state = self.backend.begin(obs, det)
            except (ValueError, RuntimeError):
                self.begin_failures += 1
                continue
            try:
                self._simulate(state, root, my_index, priors)
            except (ValueError, RuntimeError):
                self.step_failures += 1
            finally:
                self.backend.end()
        visited = {s: n for s, n in root.children.items() if n.visits > 0}
        self.last_stats = None
        if not visited:
            return None
        most_visited = self._final_pick(visited)
        v0_sig = self._v0_sig(obs)
        chosen = self._gate(obs, visited, most_visited, v0_sig)
        if cfg.collect_stats:
            top = visited[most_visited]
            self.last_stats = SearchStats(
                iterations=self.iterations_run,
                root_children=len(visited),
                top_child_visits=top.visits,
                top_child_value=top.mean,
                v0_sig=v0_sig,
                search_sig=chosen,
                most_visited_sig=most_visited,
                deviated=(v0_sig is not None and chosen != v0_sig),
                gate_blocked=(v0_sig is not None and most_visited != v0_sig
                              and chosen == v0_sig),
                begin_failures=self.begin_failures,
                step_failures=self.step_failures,
                root_visits_by_option={root_index[s]: n.visits
                                       for s, n in visited.items()
                                       if s in root_index},
            )
        idx = root_index.get(chosen)
        return None if idx is None else [idx]

    def _root_priors(self, obs: Observation,
                     my_index: int) -> dict[ActionSig, float] | None:
        """Value-derived root prior: step each legal root action once under a single
        determinization and softmax the static evals at prior_tau. All-or-nothing:
        returns None on any engine failure or deadline hit — partial priors would
        bias exploration toward whichever actions happened to get scored."""
        cfg = self.config
        sel = obs.select
        det = self.belief.sample(obs, self.rng)
        values: dict[ActionSig, float] = {}
        for i in range(len(sel.option)):
            if time.perf_counter() >= self._deadline:
                return None
            sig = action_signature(sel, (i,))
            # Mirror the main loop's engine-safe pairing: end() runs only after a
            # SUCCESSFUL begin() — an unmatched engine SearchEnd() is unverified
            # behavior against the real cg.dll.
            try:
                state = self.backend.begin(obs, det)
            except (ValueError, RuntimeError):
                self.begin_failures += 1
                return None
            try:
                st = self.backend.step(state.searchId, [i]).observation.current
                values[sig] = (self._terminal_value(st, my_index)
                               if st.result != -1
                               else self.evaluator(st, my_index))
            except (ValueError, RuntimeError):
                self.step_failures += 1
                return None
            finally:
                self.backend.end()
        if not values:
            return None
        m = max(values.values())
        exps = {s: math.exp((v - m) / cfg.prior_tau) for s, v in values.items()}
        z = sum(exps.values())
        return {s: e / z for s, e in exps.items()}

    def _final_pick(self, visited: dict):
        """Select the root child search() ultimately proposes, before the v0-improvement
        gate runs. 'most_visited' (default) = argmax visits (original behavior).
        'max_value' = argmax mean among children with visits >= robust_min_visits,
        falling back to most-visited when none qualify (Slice-5 F3)."""
        cfg = self.config
        if cfg.final_move_rule == "max_value":
            eligible = [s for s, n in visited.items() if n.visits >= cfg.robust_min_visits]
            if eligible:
                return max(eligible, key=lambda s: visited[s].mean)
        return max(visited, key=lambda s: visited[s].visits)

    def _terminal_value(self, st, my_index: int) -> float:
        return (1.0 if st.result == my_index
                else 0.0 if st.result == 1 - my_index else 0.5)

    def _v0_sig(self, obs: Observation) -> ActionSig | None:
        """v0's chosen signature for a 1-of-1 select, or None if the gate can't apply."""
        sel = obs.select
        if sel.minCount != 1 or sel.maxCount != 1 or len(sel.option) < 2:
            return None
        try:
            choice = self.rollout_policy(obs)
        except (ValueError, RuntimeError):
            return None
        if not choice or len(choice) != 1:
            return None
        return action_signature(sel, (choice[0],))

    def _gate(self, obs: Observation, visited: dict, most_visited, v0_sig):
        """v0-improvement gate (value-based): keep v0's move unless the most-visited
        challenger is well-sampled AND evaluates meaningfully above v0's move. v0 already
        beats random 93.8%; search must EARN a deviation, not replace v0 on a noisy tie
        (argmax over few-sample estimates otherwise picks the luckiest, not the best)."""
        cfg = self.config
        if cfg.deviate_min_visits <= 0 and cfg.deviate_value_edge <= 0.0:
            return most_visited
        if v0_sig is None or most_visited == v0_sig:
            return most_visited
        best_node = visited[most_visited]
        v0_mean = visited[v0_sig].mean if v0_sig in visited else 0.5
        if (best_node.visits >= cfg.deviate_min_visits
                and best_node.mean >= v0_mean + cfg.deviate_value_edge):
            return most_visited
        return v0_sig

    def _anchor(self, obs: Observation, visited: dict, best):
        """Back-compat shim for existing unit tests; search() no longer calls this."""
        return self._gate(obs, visited, best, self._v0_sig(obs))

    def _rollout(self, state: SearchState, my_index: int) -> float:
        """Play both sides with the v0 policy from a freshly expanded leaf, then score.

        A v0-vs-v0 rollout is a far stronger value estimate than a 1-ply static eval,
        which is taken before the opponent even responds. Degrades to the static
        evaluator on the last good observation if the engine rejects a rollout step."""
        last = state.observation
        try:
            for _ in range(self.config.rollout_depth):
                if time.perf_counter() >= self._deadline:
                    break
                o = state.observation
                last = o
                st = o.current
                if st.result != -1:
                    return self._terminal_value(st, my_index)
                sel = o.select
                forced = forced_selection(sel)
                if forced is not None:
                    select = forced
                elif st.yourIndex == my_index:
                    select = self.rollout_policy(o)
                else:
                    select = self.opponent_policy(o)
                state = self.backend.step(state.searchId, select)
            last = state.observation
        except (ValueError, RuntimeError):
            self.step_failures += 1
        return self.evaluator(last.current, my_index)

    def _simulate(self, state: SearchState, root: Node, my_index: int,
                  priors: dict[ActionSig, float] | None = None) -> None:
        path = [root]
        node = root
        value: float | None = None
        for _ in range(self.config.max_depth):
            if time.perf_counter() >= self._deadline:
                break
            o = state.observation
            st = o.current
            if st.result != -1:
                value = self._terminal_value(st, my_index)
                break
            sel = o.select
            forced = forced_selection(sel)
            if st.yourIndex != my_index:
                state = self.backend.step(state.searchId,
                                          forced if forced is not None
                                          else self.opponent_policy(o))
                continue
            if forced is not None or sel.minCount != 1 or sel.maxCount != 1:
                state = self.backend.step(state.searchId,
                                          forced if forced is not None
                                          else self.rollout_policy(o))
                continue
            legal = {action_signature(sel, (i,)): i for i in range(len(sel.option))}
            if node is root and priors is not None:
                sig = puct_pick(node, list(legal), priors, self.config.c_puct)
            elif self.config.use_tree_prior and self.prior_fn is not None:
                if node.priors is None:
                    node.priors = self.prior_fn(o) or {}
                sig = (puct_pick(node, list(legal), node.priors, self.config.c_puct)
                       if node.priors
                       else ucb_pick(node, list(legal), self.config.c_uct, self.rng))
            else:
                sig = ucb_pick(node, list(legal), self.config.c_uct, self.rng)
            child = node.children.setdefault(sig, Node())
            state = self.backend.step(state.searchId, [legal[sig]])
            path.append(child)
            fresh = child.visits == 0
            node = child
            if fresh:
                value = (self._rollout(state, my_index) if self.config.rollout_depth > 0
                         else self.evaluator(state.observation.current, my_index))
                break
        if value is None:
            value = self.evaluator(state.observation.current, my_index)
        for n in path:
            n.visits += 1
            n.value_sum += value
