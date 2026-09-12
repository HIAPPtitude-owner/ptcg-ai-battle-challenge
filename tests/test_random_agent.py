from cg.api import AreaType, Option, OptionType

from ptcg.agents.random_agent import RandomAgent
from tests.fixtures.obs import make_obs, make_select


def _options(n: int) -> list[Option]:
    return [Option(type=OptionType.CARD, area=AreaType.HAND, index=i, playerIndex=0) for i in range(n)]


def test_random_agent_respects_counts_and_range():
    agent = RandomAgent(seed=42)
    obs = make_obs(make_select(_options(5), min_count=1, max_count=3))
    for _ in range(50):
        r = agent.act(obs)
        assert 1 <= len(r) <= 3
        assert len(set(r)) == len(r)
        assert all(0 <= i < 5 for i in r)


def test_random_agent_seeded_reproducible():
    a, b = RandomAgent(seed=7), RandomAgent(seed=7)
    obs = make_obs(make_select(_options(6), min_count=2, max_count=4))
    assert [a.act(obs) for _ in range(10)] == [b.act(obs) for _ in range(10)]
