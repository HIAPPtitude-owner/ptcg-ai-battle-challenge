"""PolicyNet stdlib inference: golden softmax (features + identity embeddings),
degenerate guard, old-format rejection, wrapper fallback."""
import math
from types import SimpleNamespace

import pytest

from ptcg.search.policy_net import PolicyNet, PolicyNetPolicy

N_STATE, N_ACTION, CARD_DIM, ATTACK_DIM = 40, 18, 8, 4


def _feature_only_spec():
    """One linear layer (70->1) selecting action feature 0: logit == action_x[0].
    Empty vocabs + zero embedding rows -> embeddings contribute nothing, so the
    logit is a pure function of the action features (identity is inert here)."""
    w = ([0.0] * N_STATE + [1.0] + [0.0] * (N_ACTION - 1)
         + [0.0] * (CARD_DIM + ATTACK_DIM))
    return {"feature_version": 1, "action_feature_version": 2,
            "layers": [{"w": [w], "b": [0.0]}],
            "card_vocab": {}, "card_emb": [[0.0] * CARD_DIM],
            "attack_vocab": {}, "attack_emb": [[0.0] * ATTACK_DIM]}


def _ids(n):
    return [[-1, -1]] * n


def test_softmax_golden_feature_path():
    net = PolicyNet(_feature_only_spec())
    a = [math.log(2.0)] + [0.0] * (N_ACTION - 1)   # logit ln2
    b = [0.0] * N_ACTION                            # logit 0
    probs = net.score_options([0.0] * N_STATE, [a, b], _ids(2))
    # exp(ln2)=2, exp(0)=1 -> [2/3, 1/3]
    assert probs == pytest.approx([2 / 3, 1 / 3])
    assert sum(probs) == pytest.approx(1.0)


def test_identity_embedding_golden():
    """Logit reads card-embedding dim 0. A known cardId maps to a row with ln2
    there; an unknown/absent cardId falls to the zero unknown row (row 0)."""
    # w selects the FIRST card-embedding component (index 40+18 == 58).
    w = [0.0] * (N_STATE + N_ACTION) + [1.0] + [0.0] * (CARD_DIM - 1 + ATTACK_DIM)
    spec = {"feature_version": 1, "action_feature_version": 2,
            "layers": [{"w": [w], "b": [0.0]}],
            "card_vocab": {"100": 1},
            "card_emb": [[0.0] * CARD_DIM,                 # row 0 = unknown/rare
                         [math.log(2.0)] + [0.0] * (CARD_DIM - 1)],  # row 1
            "attack_vocab": {}, "attack_emb": [[0.0] * ATTACK_DIM]}
    net = PolicyNet(spec)
    state = [0.0] * N_STATE
    opts = [[0.0] * N_ACTION, [0.0] * N_ACTION, [0.0] * N_ACTION]
    # ids: cardId 100 (row1, logit ln2), cardId 999 (miss -> row0, logit 0),
    #      cardId -1 (miss -> row0, logit 0). softmax([ln2,0,0]) = [2/4,1/4,1/4].
    probs = net.score_options(state, opts, [[100, -1], [999, -1], [-1, -1]])
    assert probs == pytest.approx([0.5, 0.25, 0.25])
    assert sum(probs) == pytest.approx(1.0)


def test_nonfinite_logits_fall_back_to_uniform():
    spec = _feature_only_spec()
    spec["layers"][0]["b"] = [float("inf")]
    net = PolicyNet(spec)
    probs = net.score_options([0.0] * N_STATE,
                              [[0.0] * N_ACTION, [0.0] * N_ACTION], _ids(2))
    assert probs == [0.5, 0.5]


def test_version_mismatch_raises():
    spec = _feature_only_spec()
    spec["action_feature_version"] = 99
    with pytest.raises(ValueError):
        PolicyNet(spec)


@pytest.mark.parametrize("missing",
                         ["card_vocab", "card_emb", "attack_vocab", "attack_emb"])
def test_old_format_spec_missing_vocab_raises(missing):
    """A pre-v3 spec (no identity-embedding keys) is rejected so SearchAgent's
    warn-and-disable ladder handles it rather than mis-scoring."""
    spec = _feature_only_spec()
    del spec[missing]
    with pytest.raises(ValueError):
        PolicyNet(spec)


def test_wrapper_falls_back_on_non_one_of_one():
    calls = []
    fallback = lambda obs: calls.append(obs) or [0]  # noqa: E731
    pol = PolicyNetPolicy(PolicyNet(_feature_only_spec()), fallback, ({}, {}))
    obs = SimpleNamespace(select=SimpleNamespace(minCount=0, maxCount=2,
                                                 option=[1, 2, 3]),
                          current=None)
    assert pol(obs) == [0] and len(calls) == 1
