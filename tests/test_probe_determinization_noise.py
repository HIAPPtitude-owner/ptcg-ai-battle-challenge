import sys
import time
from pathlib import Path

from cg.api import Option, OptionType, SearchState
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import probe_determinization_noise as probe  # noqa: E402

from ptcg.search.belief import Determinization  # noqa: E402
from ptcg.search.searcher import SearchConfig, Searcher  # noqa: E402
from tests.fixtures.obs import make_obs, make_select, make_state  # noqa: E402


def opt(cid):
    return Option(type=OptionType.CARD, cardId=cid)


class FakeBelief:
    def sample(self, obs, rng):
        return Determinization([], [], [], [], [], [])


class WinBackend:
    def __init__(self):
        self.begins = 0

    def begin(self, obs, det):
        self.begins += 1
        o = make_obs(make_select([opt(100), opt(200)], min_count=1, max_count=1),
                     make_state())
        return SearchState(observation=o, searchId=self.begins)

    def step(self, search_id, select):
        st = make_state()
        st.result = 0 if select == [0] else 1
        return SearchState(observation=make_obs(
            make_select([opt(999)], min_count=1, max_count=1), st), searchId=search_id)

    def end(self):
        pass


def test_probe_state_deterministic_backend_full_agreement():
    s = Searcher(FakeBelief(), WinBackend(), opponent_policy=lambda o: [0],
                 rollout_policy=lambda o: [0],
                 config=SearchConfig(max_iterations=30, seed=0, collect_stats=True))
    obs = make_obs(make_select([opt(100), opt(200)], min_count=1, max_count=1),
                    make_state())
    agreement, std = probe.probe_state(
        s, obs, deadline_fn=lambda: time.perf_counter() + 5.0, reruns=5, base_seed=0)
    assert agreement == 1.0
    assert std == 0.0


def test_noise_probe_agent_updates_belief_before_probe_reruns():
    """Regression: probe reruns must see THIS decision's belief.update before
    sampling, not the stale belief from the previous decision — otherwise the
    probe determinizations miss newly-revealed info the real move's search has,
    systematically overstating the noise floor."""
    from ptcg.search.timing import TimeManager

    agent = probe.NoiseProbeAgent([3] * 60, budget_ms=50, reruns=2, max_states=1)
    agent.searcher.backend = WinBackend()
    agent.tm = TimeManager(total_s=1e9, max_move_s=0.05)

    calls: list[str] = []
    orig_update = agent.belief.update

    def spy_update(obs):
        calls.append("update")
        return orig_update(obs)

    agent.belief.update = spy_update

    orig_search = agent.searcher.search

    def spy_search(obs, deadline):
        calls.append("search")
        return orig_search(obs, deadline)

    agent.searcher.search = spy_search

    obs = make_obs(make_select([opt(100), opt(200)], min_count=1, max_count=1),
                    make_state())
    agent.act(obs)

    assert calls, "expected belief.update/search calls to be recorded"
    assert calls[0] == "update", (
        f"belief.update(obs) must fire before the first probe search call; "
        f"got order {calls}")
    assert "search" in calls
