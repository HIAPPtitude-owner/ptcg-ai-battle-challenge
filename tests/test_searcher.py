"""Searcher against a scripted fake backend — no engine required."""
import time

from cg.api import Option, OptionType, SearchState

from ptcg.search.belief import Determinization
from ptcg.search.searcher import SearchConfig, Searcher, SearchStats, forced_selection
from ptcg.search.tree import Node, action_signature
from tests.fixtures.obs import make_obs, make_select, make_state


def opt(card_id):
    return Option(type=OptionType.CARD, cardId=card_id)


def sel1(options):
    return make_select(options, min_count=1, max_count=1)


class FakeBelief:
    def sample(self, obs, rng):
        return Determinization([], [], [], [], [], [])


class FakeBackend:
    """Root with [A(100), B(200)]; A leads to a win-for-me terminal, B to a loss.

    Alternating begins swap the option order to prove index-misalignment safety.
    """

    def __init__(self):
        self.begins = 0
        self.ended = 0

    def begin(self, obs, det):
        self.begins += 1
        swap = self.begins % 2 == 0
        options = [opt(200), opt(100)] if swap else [opt(100), opt(200)]
        o = make_obs(sel1(options), make_state())
        o.search_begin_input = None
        self._win_index = 1 if swap else 0
        return SearchState(observation=o, searchId=self.begins)

    def step(self, search_id, select):
        won = select == [self._win_index]
        st = make_state()
        st.result = 0 if won else 1  # my_index is 0 in make_state()
        o = make_obs(sel1([opt(999)]), st)
        return SearchState(observation=o, searchId=search_id)

    def end(self):
        self.ended += 1


def searcher(backend, iterations=40):
    return Searcher(FakeBelief(), backend, opponent_policy=lambda o: [0],
                    rollout_policy=lambda o: [0],
                    config=SearchConfig(max_iterations=iterations, seed=0))


def test_picks_winning_move_despite_index_swaps():
    obs = make_obs(sel1([opt(100), opt(200)]), make_state())
    s = searcher(FakeBackend())
    assert s.search(obs, deadline=time.perf_counter() + 5.0) == [0]  # A is index 0 in REAL obs


def test_backend_end_called_every_iteration():
    b = FakeBackend()
    s = searcher(b, iterations=10)
    s.search(make_obs(sel1([opt(100), opt(200)]), make_state()),
             deadline=time.perf_counter() + 5.0)
    assert b.ended == b.begins == 10


def test_deadline_respected():
    s = searcher(FakeBackend(), iterations=10_000)
    t0 = time.perf_counter()
    s.search(make_obs(sel1([opt(100), opt(200)]), make_state()), deadline=t0 + 0.05)
    assert time.perf_counter() - t0 < 1.0
    assert 0 < s.iterations_run < 10_000


def test_returns_none_when_no_iteration_completes():
    class ExplodingBackend(FakeBackend):
        def begin(self, obs, det):
            raise ValueError("bad determinization")

        def end(self):
            pass

    s = searcher(ExplodingBackend(), iterations=5)
    obs = make_obs(sel1([opt(100), opt(200)]), make_state())
    assert s.search(obs, deadline=time.perf_counter() + 1.0) is None
    assert s.begin_failures == 5


def test_step_failure_mid_iteration_does_not_abort_search():
    class FlakyBackend(FakeBackend):
        def step(self, search_id, select):
            if self.begins % 3 == 0:  # every third determinization blows up mid-walk
                raise ValueError("engine rejected selection")
            return super().step(search_id, select)

    b = FlakyBackend()
    s = searcher(b, iterations=30)
    obs = make_obs(sel1([opt(100), opt(200)]), make_state())
    result = s.search(obs, deadline=time.perf_counter() + 5.0)
    assert result == [0]  # surviving iterations still find the winning move
    assert s.step_failures > 0
    assert b.ended == b.begins  # end() ran for failed iterations too


def test_rollout_step_failure_counted_and_search_stays_functional():
    """A backend that blows up mid-rollout (not mid-tree-walk) is caught inside
    _rollout itself — that except block must still count the failure, not just
    the outer search()-level except that test_step_failure_mid_iteration_...
    already covers."""
    class RolloutFlakyBackend(FakeBackend):
        def begin(self, obs, det):
            self.begins += 1
            self.step_calls = 0
            o = make_obs(sel1([opt(100), opt(200)]), make_state())
            return SearchState(observation=o, searchId=self.begins)

        def step(self, search_id, select):
            self.step_calls += 1
            if self.step_calls == 1:
                # the tree-walk step into a fresh leaf: succeeds, non-terminal
                o = make_obs(sel1([opt(999)]), make_state())
                return SearchState(observation=o, searchId=search_id)
            # every rollout step from that leaf blows up
            raise ValueError("engine rejected rollout selection")

    b = RolloutFlakyBackend()
    s = searcher(b, iterations=1)
    obs = make_obs(sel1([opt(100), opt(200)]), make_state())
    result = s.search(obs, deadline=time.perf_counter() + 5.0)
    assert result is not None  # degrades to the static evaluator, search stays functional
    assert s.step_failures > 0
    assert b.ended == b.begins  # backend.end() still ran despite the rollout failure


def _anchor_fixture(config):
    """A searcher whose v0 (rollout) policy always prefers real-obs option 0, plus a
    2-option obs and a visited dict where option 1 is the most-visited challenger."""
    s = Searcher(FakeBelief(), FakeBackend(), opponent_policy=lambda o: [0],
                 rollout_policy=lambda o: [0], config=config)
    obs = make_obs(sel1([opt(100), opt(200)]), make_state())
    sig0 = action_signature(obs.select, (0,))  # v0's move
    sig1 = action_signature(obs.select, (1,))  # challenger / most-visited
    return s, obs, sig0, sig1


def test_anchor_keeps_v0_move_when_challenger_lacks_value_edge():
    s, obs, sig0, sig1 = _anchor_fixture(
        SearchConfig(deviate_min_visits=10, deviate_value_edge=0.1))
    visited = {sig0: Node(visits=15, value_sum=7.5),   # v0 mean 0.5
               sig1: Node(visits=20, value_sum=11.0)}  # best mean 0.55 < 0.5 + 0.1
    assert s._anchor(obs, visited, sig1) == sig0  # blocked → stay with v0


def test_anchor_keeps_v0_move_when_challenger_undersampled():
    s, obs, sig0, sig1 = _anchor_fixture(
        SearchConfig(deviate_min_visits=10, deviate_value_edge=0.1))
    visited = {sig0: Node(visits=15, value_sum=7.5),  # v0 mean 0.5
               sig1: Node(visits=5, value_sum=4.5)}   # best mean 0.9 but only 5 visits
    assert s._anchor(obs, visited, sig1) == sig0  # blocked → stay with v0


def test_anchor_deviates_when_challenger_is_confident_and_better():
    s, obs, sig0, sig1 = _anchor_fixture(
        SearchConfig(deviate_min_visits=10, deviate_value_edge=0.1))
    visited = {sig0: Node(visits=15, value_sum=7.5),    # v0 mean 0.5
               sig1: Node(visits=20, value_sum=14.0)}   # best mean 0.7 >= 0.5 + 0.1
    assert s._anchor(obs, visited, sig1) == sig1  # earns the deviation


def test_anchor_disabled_returns_most_visited():
    s, obs, sig0, sig1 = _anchor_fixture(
        SearchConfig(deviate_min_visits=0, deviate_value_edge=0.0))
    visited = {sig0: Node(visits=15, value_sum=7.5),
               sig1: Node(visits=1, value_sum=1.0)}
    assert s._anchor(obs, visited, sig1) == sig1  # gate off → pure most-visited


def test_deadline_respected_mid_iteration():
    """A single iteration must not overshoot the deadline by more than a step or two.

    SlowBackend always reports the opponent's turn (yourIndex != my_index) and never
    terminates, so _simulate's for-loop walks the full max_depth=40 steps every
    iteration via the opponent-step branch (no 'fresh leaf' break, no terminal break) —
    sustaining 40 sleeping steps (>= 2.0s) with no intra-iteration deadline check.
    """
    STEP_SLEEP = 0.05

    class SlowBackend(FakeBackend):
        def begin(self, obs, det):
            self.begins += 1
            o = make_obs(sel1([opt(100), opt(200)]), make_state(your_index=1))
            return SearchState(observation=o, searchId=self.begins)

        def step(self, search_id, select):
            time.sleep(STEP_SLEEP)
            o = make_obs(sel1([opt(100), opt(200)]), make_state(your_index=1))
            return SearchState(observation=o, searchId=search_id)

    s = searcher(SlowBackend(), iterations=50)
    obs = make_obs(sel1([opt(100), opt(200)]), make_state())
    t0 = time.perf_counter()
    s.search(obs, deadline=t0 + 0.12)
    elapsed = time.perf_counter() - t0
    # without intra-iteration checks a single iteration alone runs 40+ sleeping
    # steps (>= 2.0s); with them we stop within ~2 steps of the deadline.
    assert elapsed < 1.0, f"search overshot deadline: {elapsed:.2f}s"


def test_final_pick_most_visited_default():
    s, obs, sig0, sig1 = _anchor_fixture(SearchConfig(final_move_rule="most_visited"))
    visited = {sig0: Node(visits=30, value_sum=15.0),  # mean 0.5, most visits
               sig1: Node(visits=8, value_sum=6.4)}    # mean 0.8
    assert s._final_pick(visited) == sig0


def test_final_pick_max_value_prefers_higher_mean_above_floor():
    s, obs, sig0, sig1 = _anchor_fixture(
        SearchConfig(final_move_rule="max_value", robust_min_visits=5))
    visited = {sig0: Node(visits=30, value_sum=15.0),  # mean 0.5
               sig1: Node(visits=8, value_sum=6.4)}    # mean 0.8, >=5 visits
    assert s._final_pick(visited) == sig1


def test_final_pick_max_value_falls_back_when_none_qualify():
    s, obs, sig0, sig1 = _anchor_fixture(
        SearchConfig(final_move_rule="max_value", robust_min_visits=10))
    visited = {sig0: Node(visits=30, value_sum=15.0),  # only A clears 10 visits
               sig1: Node(visits=8, value_sum=6.4)}
    assert s._final_pick(visited) == sig0


def test_rollout_deadline_respected_mid_iteration():
    """The _rollout loop must honor the deadline mid-loop, not just _simulate's.

    Every observation reports MY turn (yourIndex == my_index == 0) with a free
    1-of-2 select and never terminates, so _simulate expands a fresh leaf on its
    very first step and enters _rollout; with rollout_depth=40 an unchecked
    rollout runs 40 sleeping steps (>= 2.0s) inside a single iteration.
    """
    STEP_SLEEP = 0.05

    class SlowRolloutBackend(FakeBackend):
        def begin(self, obs, det):
            self.begins += 1
            o = make_obs(sel1([opt(100), opt(200)]), make_state())
            return SearchState(observation=o, searchId=self.begins)

        def step(self, search_id, select):
            time.sleep(STEP_SLEEP)
            o = make_obs(sel1([opt(100), opt(200)]), make_state())
            return SearchState(observation=o, searchId=search_id)

    s = Searcher(FakeBelief(), SlowRolloutBackend(), opponent_policy=lambda o: [0],
                 rollout_policy=lambda o: [0],
                 config=SearchConfig(rollout_depth=40, max_depth=40,
                                     max_iterations=50, seed=0))
    obs = make_obs(sel1([opt(100), opt(200)]), make_state())
    t0 = time.perf_counter()
    s.search(obs, deadline=t0 + 0.12)
    elapsed = time.perf_counter() - t0
    # without the _rollout intra-loop check a single rollout alone runs 40
    # sleeping steps (>= 2.0s); with it we stop within ~2 steps of the deadline.
    assert elapsed < 1.0, f"rollout overshot deadline: {elapsed:.2f}s"


def test_forced_selection():
    assert forced_selection(sel1([opt(1)])) == [0]
    both = make_select([opt(1), opt(2)], min_count=2, max_count=2)
    assert forced_selection(both) == [0, 1]
    none_allowed = make_select([opt(1)], min_count=0, max_count=0)
    assert forced_selection(none_allowed) == []
    free = make_select([opt(1), opt(2)], min_count=1, max_count=1)
    assert forced_selection(free) is None


def test_gate_matches_legacy_anchor_across_cases():
    """_gate(obs, visited, most_visited, v0_sig) reproduces _anchor's decisions."""
    s, obs, sig0, sig1 = _anchor_fixture(
        SearchConfig(deviate_min_visits=10, deviate_value_edge=0.1))
    # undersampled challenger -> stay with v0
    visited = {sig0: Node(visits=15, value_sum=7.5),
               sig1: Node(visits=5, value_sum=4.5)}
    assert s._gate(obs, visited, sig1, s._v0_sig(obs)) == sig0
    # confident + better challenger -> deviate
    visited = {sig0: Node(visits=15, value_sum=7.5),
               sig1: Node(visits=20, value_sum=14.0)}
    assert s._gate(obs, visited, sig1, s._v0_sig(obs)) == sig1


def test_v0_sig_none_when_not_single_select():
    s, obs, _, _ = _anchor_fixture(SearchConfig())
    multi = make_obs(make_select([opt(100), opt(200)], min_count=1, max_count=2),
                     make_state())
    assert s._v0_sig(multi) is None  # gate cannot apply to a multi-count select


def test_stats_off_by_default():
    obs = make_obs(sel1([opt(100), opt(200)]), make_state())
    s = searcher(FakeBackend())  # config default collect_stats=False
    s.search(obs, deadline=time.perf_counter() + 5.0)
    assert s.last_stats is None


def test_stats_populated_when_enabled():
    obs = make_obs(sel1([opt(100), opt(200)]), make_state())
    s = Searcher(FakeBelief(), FakeBackend(), opponent_policy=lambda o: [0],
                 rollout_policy=lambda o: [0],
                 config=SearchConfig(max_iterations=40, seed=0, collect_stats=True))
    s.search(obs, deadline=time.perf_counter() + 5.0)
    st = s.last_stats
    assert isinstance(st, SearchStats)
    assert st.iterations == s.iterations_run
    assert st.root_children >= 1
    assert st.top_child_visits >= 1
    assert st.search_sig is not None
    # FakeBackend: option A(100) at real index 0 wins; v0 (rollout_policy->[0]) also
    # picks A, so search agrees with v0 -> no deviation, not gate-blocked.
    assert st.deviated is False
    assert st.gate_blocked is False


def test_stats_include_root_visit_distribution():
    """root_visits_by_option: option index -> visit count, for the policy-net
    training target (Slice 7B Task 4). FakeBackend never raises on begin/step,
    so every completed iteration contributes exactly one root-child visit and
    the distribution's total equals iterations exactly."""
    obs = make_obs(sel1([opt(100), opt(200)]), make_state())
    s = Searcher(FakeBelief(), FakeBackend(), opponent_policy=lambda o: [0],
                 rollout_policy=lambda o: [0],
                 config=SearchConfig(max_iterations=40, seed=0, collect_stats=True))
    s.search(obs, deadline=time.perf_counter() + 5.0)
    st = s.last_stats
    assert st is not None and st.root_visits_by_option
    n_options = len(obs.select.option)
    assert all(0 <= i < n_options for i in st.root_visits_by_option)
    assert all(v >= 1 for v in st.root_visits_by_option.values())
    assert sum(st.root_visits_by_option.values()) == st.iterations
    assert max(st.root_visits_by_option.values()) == st.top_child_visits


def test_stats_flag_does_not_change_move_sequence():
    """Seed-determinism guard: identical returned moves with stats on vs off.
    NOT a self-comparison of one code path — two independently constructed
    searchers (fresh FakeBackend each) driven by the same seed."""
    def run(collect):
        moves = []
        for _ in range(6):
            s = Searcher(FakeBelief(), FakeBackend(), opponent_policy=lambda o: [0],
                         rollout_policy=lambda o: [0],
                         config=SearchConfig(max_iterations=25, seed=0,
                                             collect_stats=collect))
            obs = make_obs(sel1([opt(100), opt(200)]), make_state())
            moves.append(s.search(obs, deadline=time.perf_counter() + 5.0))
        return moves
    assert run(collect=True) == run(collect=False)


def test_root_prior_off_is_bit_identical():
    """Guard: use_root_prior absent (default False) vs explicit False produce
    identical move sequences across repeated seeded runs — same pattern as
    test_stats_flag_does_not_change_move_sequence."""
    def run(explicit_false):
        moves = []
        for _ in range(6):
            kwargs = dict(max_iterations=25, seed=0)
            if explicit_false:
                kwargs["use_root_prior"] = False
            s = Searcher(FakeBelief(), FakeBackend(), opponent_policy=lambda o: [0],
                         rollout_policy=lambda o: [0],
                         config=SearchConfig(**kwargs))
            obs = make_obs(sel1([opt(100), opt(200)]), make_state())
            moves.append(s.search(obs, deadline=time.perf_counter() + 5.0))
        return moves
    assert run(explicit_false=False) == run(explicit_false=True)


class BiasedFakeBackend(FakeBackend):
    """Root [A(100), B(200)]; every step returns a non-terminal leaf tagged (via
    turnActionCount) with which root option produced it, so a custom evaluator
    can score B >> A without relying on terminal results."""

    def begin(self, obs, det):
        self.begins += 1
        o = make_obs(sel1([opt(100), opt(200)]), make_state())
        return SearchState(observation=o, searchId=self.begins)

    def step(self, search_id, select):
        tag = select[0]  # 0 -> A, 1 -> B
        st = make_state(turn_action_count=tag)
        o = make_obs(sel1([opt(999)]), st)
        return SearchState(observation=o, searchId=search_id)


def _biased_evaluator(state, my_index):
    return 0.9 if state.turnActionCount == 1 else 0.1  # B >> A


def test_root_prior_steers_first_visits():
    b = BiasedFakeBackend()
    s = Searcher(FakeBelief(), b, opponent_policy=lambda o: [0],
                 rollout_policy=lambda o: [0], evaluator=_biased_evaluator,
                 config=SearchConfig(seed=0, rollout_depth=0, use_root_prior=True))
    obs = make_obs(sel1([opt(100), opt(200)]), make_state())
    sig_a = action_signature(obs.select, (0,))
    sig_b = action_signature(obs.select, (1,))

    priors = s._root_priors(obs, obs.current.yourIndex)
    assert priors[sig_b] > priors[sig_a]  # softmax ordering matches evaluator ordering

    root = Node()
    for _ in range(4):
        state = b.begin(obs, None)
        s._simulate(state, root, obs.current.yourIndex, priors)
        b.end()
    a_visits = root.children[sig_a].visits if sig_a in root.children else 0
    b_visits = root.children[sig_b].visits if sig_b in root.children else 0
    assert b_visits > a_visits


def test_root_prior_failure_falls_back_to_ucb():
    """begin() raises exactly once, during the prior-computation probe (count-based:
    only the very first-ever begin() call fails); search() still returns a move via
    plain UCB and begin_failures is incremented. Engine-safe pairing: end() must
    NOT run for the failed begin — the main loop never ends an unbegun search,
    and an unmatched SearchEnd() is unverified behavior against the real cg.dll."""
    class PriorFailBackend(FakeBackend):
        def begin(self, obs, det):
            if self.begins == 0:
                self.begins += 1
                raise ValueError("prior probe failed")
            return super().begin(obs, det)

    b = PriorFailBackend()
    s = Searcher(FakeBelief(), b, opponent_policy=lambda o: [0],
                 rollout_policy=lambda o: [0],
                 config=SearchConfig(max_iterations=25, seed=0, use_root_prior=True))
    obs = make_obs(sel1([opt(100), opt(200)]), make_state())
    result = s.search(obs, deadline=time.perf_counter() + 5.0)
    assert result == [0]  # A is index 0 in the real obs; UCB path still finds it
    assert s.begin_failures >= 1
    # exactly one begin (the failed probe) must have no matching end
    assert b.ended == b.begins - 1


def test_root_prior_step_failure_still_ends_search():
    """step() raises during the prior-computation probe: the begin SUCCEEDED, so
    end() MUST still run for it (finally); step_failures (not begin_failures) is
    incremented, and search() falls back to plain UCB for the whole move."""
    class PriorStepFailBackend(FakeBackend):
        step_calls = 0

        def step(self, search_id, select):
            self.step_calls += 1
            if self.step_calls == 1:  # only the first-ever step (the probe) fails
                raise ValueError("prior probe step failed")
            return super().step(search_id, select)

    b = PriorStepFailBackend()
    s = Searcher(FakeBelief(), b, opponent_policy=lambda o: [0],
                 rollout_policy=lambda o: [0],
                 config=SearchConfig(max_iterations=25, seed=0, use_root_prior=True))
    obs = make_obs(sel1([opt(100), opt(200)]), make_state())
    result = s.search(obs, deadline=time.perf_counter() + 5.0)
    assert result == [0]  # UCB path still finds the winning move
    assert s.step_failures >= 1
    assert s.begin_failures == 0
    assert b.ended == b.begins  # every successful begin got its end, probe included


def test_gate_blocked_flag_set_when_gate_vetoes_deviation():
    """most-visited != v0 but thresholds veto -> chosen==v0, gate_blocked=True.
    Drive it through the stats path with a hand-built visited dict via _make_stats
    is not exposed; instead assert the flag semantics on _gate + a direct build."""
    s, obs, sig0, sig1 = _anchor_fixture(
        SearchConfig(deviate_min_visits=10, deviate_value_edge=0.1, collect_stats=True))
    visited = {sig0: Node(visits=15, value_sum=7.5),
               sig1: Node(visits=5, value_sum=4.5)}  # challenger undersampled
    v0_sig = s._v0_sig(obs)
    chosen = s._gate(obs, visited, sig1, v0_sig)
    assert chosen == sig0 and v0_sig == sig0  # vetoed back to v0
    # most_visited (sig1) != v0 (sig0) and chosen == v0 -> gate_blocked semantics
    assert (sig1 != v0_sig) and (chosen == v0_sig)


def test_flags_off_bit_identical_with_prior_fn_present():
    """prior_fn set but use_tree_prior=False must not change ANYTHING: same
    returned move, same iterations, same root visit distribution. Guards the
    Slice-7B all-node PUCT tree prior — merely wiring a prior_fn without
    flipping the flag must be a complete no-op."""
    obs = make_obs(sel1([opt(100), opt(200)]), make_state())

    s_a = Searcher(FakeBelief(), FakeBackend(), opponent_policy=lambda o: [0],
                   rollout_policy=lambda o: [0],
                   config=SearchConfig(max_iterations=25, seed=0, collect_stats=True))
    move_a = s_a.search(obs, deadline=time.perf_counter() + 5.0)
    stats_a = s_a.last_stats

    s_b = Searcher(FakeBelief(), FakeBackend(), opponent_policy=lambda o: [0],
                   rollout_policy=lambda o: [0],
                   config=SearchConfig(max_iterations=25, seed=0, collect_stats=True,
                                       use_tree_prior=False),
                   prior_fn=lambda o: {})
    move_b = s_b.search(obs, deadline=time.perf_counter() + 5.0)
    stats_b = s_b.last_stats

    assert move_a == move_b
    assert stats_a.iterations == stats_b.iterations
    assert stats_a.root_visits_by_option == stats_b.root_visits_by_option


def test_tree_prior_used_when_flag_on():
    """use_tree_prior=True routes selection through prior_fn: prior_fn is
    CALLED (recorded via closure) and search still returns a legal option
    index."""
    calls = []

    def prior_fn(o):
        calls.append(o)
        sel = o.select
        sigs = [action_signature(sel, (i,)) for i in range(len(sel.option))]
        return {sig: 1.0 / len(sigs) for sig in sigs} if sigs else {}

    b = FakeBackend()
    s = Searcher(FakeBelief(), b, opponent_policy=lambda o: [0],
                 rollout_policy=lambda o: [0],
                 config=SearchConfig(max_iterations=10, seed=0, use_tree_prior=True),
                 prior_fn=prior_fn)
    obs = make_obs(sel1([opt(100), opt(200)]), make_state())
    result = s.search(obs, deadline=time.perf_counter() + 5.0)

    assert calls, "prior_fn should have been invoked when use_tree_prior=True"
    assert result is not None
    assert 0 <= result[0] < len(obs.select.option)


def test_prior_fn_failure_falls_back_to_ucb():
    """prior_fn returning None caches {} on the node (never retried for that
    node's lifetime) and the search completes normally via UCB fallback — no
    exception, legal move returned."""
    calls = []

    def prior_fn(o):
        calls.append(o)
        return None

    obs = make_obs(sel1([opt(100), opt(200)]), make_state())

    # Direct _simulate calls against a shared root Node prove the {} cache:
    # exactly one prior_fn invocation across 4 iterations touching the same node.
    b = FakeBackend()
    root = Node()
    s = Searcher(FakeBelief(), b, opponent_policy=lambda o: [0],
                 rollout_policy=lambda o: [0],
                 config=SearchConfig(max_iterations=25, seed=0, use_tree_prior=True),
                 prior_fn=prior_fn)
    for _ in range(4):
        state = b.begin(obs, None)
        s._simulate(state, root, obs.current.yourIndex)
        b.end()
    assert root.priors == {}
    assert len(calls) == 1  # cached after first failure, never retried

    # Full search() completes without exception and still finds the winning move.
    b2 = FakeBackend()
    s2 = Searcher(FakeBelief(), b2, opponent_policy=lambda o: [0],
                  rollout_policy=lambda o: [0],
                  config=SearchConfig(max_iterations=25, seed=0, use_tree_prior=True),
                  prior_fn=prior_fn)
    result = s2.search(obs, deadline=time.perf_counter() + 5.0)
    assert result == [0]
