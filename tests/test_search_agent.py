"""SearchAgent glue: shortcuts, search path, fallback."""
import json

from cg.api import Option, OptionType

from ptcg.agents.search_agent import SearchAgent
from ptcg.search.searcher import SearchConfig
from ptcg.search.timing import TimeManager
from tests.fixtures.obs import make_obs, make_select, make_state


def opt(card_id):
    return Option(type=OptionType.CARD, cardId=card_id)


class BoomBackend:
    def begin(self, obs, det):
        raise RuntimeError("engine says no")

    def step(self, search_id, select):
        raise AssertionError("unreachable")

    def end(self):
        pass


def agent(backend):
    return SearchAgent([3] * 60, backend=backend,
                       time_manager=TimeManager(max_move_s=0.05))


def test_forced_single_option_answered_without_search():
    a = agent(BoomBackend())
    obs = make_obs(make_select([opt(1)], min_count=1, max_count=1), make_state())
    assert a.act(obs) == [0]
    assert a.fallbacks == 0  # shortcut, not fallback


def test_search_failure_falls_back_to_heuristic_validly():
    a = agent(BoomBackend())
    obs = make_obs(make_select([opt(1), opt(2)], min_count=1, max_count=1), make_state())
    out = a.act(obs)
    assert len(out) == 1 and out[0] in (0, 1)
    assert a.fallbacks == 1


def test_time_is_recorded():
    a = agent(BoomBackend())
    obs = make_obs(make_select([opt(1)], min_count=1, max_count=1), make_state())
    a.act(obs)
    assert a.tm.moves_done == 1


def test_search_agent_accepts_injected_evaluator():
    calls = []

    def stub_eval(state, my_index):
        calls.append(my_index)
        return 0.5

    a = SearchAgent([3] * 60, evaluator=stub_eval)
    assert a.searcher.evaluator is stub_eval
    assert a.name == "search-net-v1"


def test_search_agent_default_evaluator_unchanged():
    from ptcg.search.evaluate import evaluate

    a = SearchAgent([3] * 60)
    assert a.searcher.evaluator is evaluate
    assert a.name == "search-v1"


def test_agent_accumulates_decision_stats_when_enabled():
    from cg.api import Option, OptionType
    from ptcg.agents.search_agent import SearchAgent
    from ptcg.search.timing import TimeManager
    from tests.fixtures.obs import make_obs, make_select, make_state

    class WinBackend:  # A at index 0 wins; deterministic
        def begin(self, obs, det):
            from cg.api import SearchState
            o = make_obs(make_select([Option(type=OptionType.CARD, cardId=100),
                                      Option(type=OptionType.CARD, cardId=200)],
                                     min_count=1, max_count=1), make_state())
            self._sid = getattr(self, "_sid", 0) + 1
            return SearchState(observation=o, searchId=self._sid)

        def step(self, search_id, select):
            from cg.api import SearchState
            st = make_state()
            st.result = 0 if select == [0] else 1
            return SearchState(observation=make_obs(
                make_select([Option(type=OptionType.CARD, cardId=999)],
                            min_count=1, max_count=1), st), searchId=search_id)

        def end(self):
            pass

    a = SearchAgent([3] * 60, backend=WinBackend(), collect_stats=True,
                    time_manager=TimeManager(total_s=1e9, max_move_s=0.05))
    obs = make_obs(make_select([Option(type=OptionType.CARD, cardId=100),
                                Option(type=OptionType.CARD, cardId=200)],
                               min_count=1, max_count=1), make_state())
    a.act(obs)
    assert len(a.decision_stats) == 1
    assert a.decision_stats[0].search_sig is not None


def test_agent_no_stats_by_default():
    from ptcg.agents.search_agent import SearchAgent
    a = SearchAgent([3] * 60)
    assert a.decision_stats == []
    assert a.searcher.config.collect_stats is False


def _policy_weights_file(tmp_path):
    spec = {"feature_version": 1, "action_feature_version": 2,
            "layers": [{"w": [[0.0]], "b": [0.0]}],
            "card_vocab": {}, "card_emb": [[0.0] * 8],
            "attack_vocab": {}, "attack_emb": [[0.0] * 4]}
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    return p


def test_policy_weights_missing_file_disables_flags_and_warns(capsys):
    cfg = SearchConfig(use_tree_prior=True, policy_opponent=True,
                       policy_rollout=True)
    a = SearchAgent([3] * 60, config=cfg, backend=BoomBackend(),
                    policy_weights="does/not/exist.json")
    assert a.policy is None
    assert a.searcher.config.use_tree_prior is False
    assert a.searcher.config.policy_opponent is False
    assert a.searcher.config.policy_rollout is False
    assert a.name == "search-v1"
    assert "policy-net load failed" in capsys.readouterr().err


def test_policy_weights_wires_injection_sites(tmp_path):
    weights_path = _policy_weights_file(tmp_path)
    cfg = SearchConfig(use_tree_prior=True, policy_opponent=True,
                       policy_rollout=True)
    a = SearchAgent([3] * 60, config=cfg, backend=BoomBackend(),
                    policy_weights=weights_path)
    assert a.policy is not None
    assert a.searcher.opponent_policy is a.policy
    assert a.searcher.rollout_policy is a.policy
    assert a.searcher.prior_fn == a.policy.priors_for
    assert a.name == "search-policy"


def test_no_flags_means_no_wiring_even_with_weights(tmp_path):
    weights_path = _policy_weights_file(tmp_path)
    a = SearchAgent([3] * 60, backend=BoomBackend(), policy_weights=weights_path)
    assert a.policy is None and a.name == "search-v1"
