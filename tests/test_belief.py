"""BeliefState: hidden-zone determinization sampling."""
import random

from cg.api import AreaType, Card, Log, LogType

from ptcg.search.belief import BeliefState, Determinization
from tests.fixtures.obs import make_obs, make_player, make_pokemon, make_select

DECK = [3] * 30 + [100] * 4 + [200] * 4 + [300] * 22  # synthetic 60-card list
BASICS = frozenset({100})


def card(cid: int, serial: int = 0, player: int = 1) -> Card:
    return Card(id=cid, serial=serial, playerIndex=player)


def obs_with(me=None, opp=None, logs=None):
    from tests.fixtures.obs import make_state
    o = make_obs(make_select([]), make_state(you=me, opp=opp))
    o.logs = logs or []
    return o


def test_sample_sizes_match_zone_counts():
    me = make_player(make_pokemon(), hand=[card(3, player=0)])
    opp = make_player(make_pokemon())
    opp.handCount, opp.deckCount = 5, 38
    o = obs_with(me, opp)
    d = BeliefState(DECK, BASICS).sample(o, random.Random(0))
    assert isinstance(d, Determinization)
    assert len(d.your_deck) == me.deckCount
    assert len(d.your_prize) == len(me.prize)
    assert len(d.opponent_deck) == 38
    assert len(d.opponent_prize) == len(opp.prize)
    assert len(d.opponent_hand) == 5
    assert d.opponent_active == []  # active is face-up in fixtures


def test_our_unseen_pool_excludes_visible_cards():
    me = make_player(make_pokemon(card_id=100), hand=[card(200, player=0)])
    me.discard = [card(200, player=0)]
    me.deckCount = 50
    o = obs_with(me, make_player(make_pokemon()))
    d = BeliefState(DECK, BASICS).sample(o, random.Random(0))
    pool = d.your_deck + [p for p in d.your_prize]
    # 100 on board once, 200 in hand+discard: only 3 and 2 copies left respectively
    assert pool.count(100) <= 3 and pool.count(200) <= 2


def test_known_opponent_hand_cards_appear_in_hand_prediction():
    me, opp = make_player(make_pokemon()), make_player(make_pokemon())
    opp.handCount = 4
    logs = [Log(type=LogType.SHUFFLE, playerIndex=1, cardId=200, serial=77,
                toArea=AreaType.HAND)]
    o = obs_with(me, opp, logs)
    b = BeliefState(DECK, BASICS)
    b.update(o)
    d = b.sample(o, random.Random(0))
    assert 200 in d.opponent_hand


def test_card_leaving_hand_is_forgotten():
    me, opp = make_player(make_pokemon()), make_player(make_pokemon())
    opp.handCount = 1
    b = BeliefState([3] * 60, frozenset())
    b.update(obs_with(me, opp, [Log(type=LogType.SHUFFLE, playerIndex=1, cardId=200,
                                    serial=77, toArea=AreaType.HAND)]))
    b.update(obs_with(me, opp, [Log(type=LogType.SHUFFLE, playerIndex=1, cardId=200,
                                    serial=77, fromArea=AreaType.HAND)]))
    d = b.sample(obs_with(me, opp), random.Random(0))
    assert d.opponent_hand == [3]  # only filler remains


def test_facedown_opponent_active_predicted_as_basic():
    me, opp = make_player(make_pokemon()), make_player()
    opp.active = [None]
    o = obs_with(me, opp)
    d = BeliefState(DECK, BASICS).sample(o, random.Random(0))
    assert d.opponent_active == [100]


def test_setup_opponent_deck_contains_a_basic():
    me, opp = make_player(make_pokemon()), make_player(make_pokemon())
    o = obs_with(me, opp)
    o.current.turn = 0
    d = BeliefState(DECK, BASICS).sample(o, random.Random(1))
    assert any(c in BASICS for c in d.opponent_deck)


def _obs_with_facedown_opponent_active(my_deck):
    me = make_player(make_pokemon())
    opp = make_player()
    opp.active = [None]
    opp.deckCount = 53
    opp.handCount = 6
    opp.hand = None
    opp.prize = [None] * 6
    return obs_with(me, opp)


def test_facedown_active_prediction_not_double_counted():
    """Belief v2: the predicted face-down active must be POPPED from the pool,
    not duplicated into deck/hand/prize predictions."""
    BASIC = 901  # exactly one copy in my_deck; only basic in the prior
    my_deck = [BASIC] + [3] * 59  # 3 = filler energy, not a basic
    belief = BeliefState(my_deck, basic_ids=frozenset({BASIC}))
    # Opponent: face-down active (active=[None]), nothing else visible.
    # Build obs with opp deckCount/handCount/prizes so the whole prior is dealt out.
    obs = _obs_with_facedown_opponent_active(my_deck)
    rng = random.Random(0)
    for _ in range(20):  # any single sample could dodge the bug by luck
        det = belief.sample(obs, rng)
        assert det.opponent_active, "expected an active prediction"
        total = (det.opponent_deck + det.opponent_hand
                 + det.opponent_prize + det.opponent_active).count(BASIC)
        assert total <= my_deck.count(BASIC), (
            f"basic {BASIC} predicted {total}x but prior holds "
            f"{my_deck.count(BASIC)}")


def test_turn_regression_resets_tracking():
    me, opp = make_player(make_pokemon()), make_player(make_pokemon())
    opp.handCount = 1
    b = BeliefState([3] * 60, frozenset())
    o1 = obs_with(me, opp, [Log(type=LogType.SHUFFLE, playerIndex=1, cardId=200,
                                serial=77, toArea=AreaType.HAND)])
    o1.current.turn = 9
    b.update(o1)
    o2 = obs_with(me, opp)
    o2.current.turn = 0  # new match started
    b.update(o2)
    d = b.sample(o2, random.Random(0))
    assert 200 not in d.opponent_hand
