"""Canonical action signatures + UCB tree."""
import random

from cg.api import AreaType, Option, OptionType

from ptcg.search.tree import Node, action_signature, option_signature, puct_pick, ucb_pick
from tests.fixtures.obs import make_select

SIG_A, SIG_B = ("a",), ("b",)


def opt(card_id=None, attack_id=None, area=None, index=None, serial=None):
    return Option(type=OptionType.CARD if card_id else OptionType.ATTACK,
                  cardId=card_id, attackId=attack_id, area=area, index=index,
                  serial=serial)


def test_signature_ignores_list_position_and_serial():
    a1 = opt(card_id=100, area=AreaType.HAND, index=0, serial=11)
    a2 = opt(card_id=100, area=AreaType.HAND, index=4, serial=99)
    assert option_signature(a1) == option_signature(a2)


def test_signature_distinguishes_different_cards_and_attacks():
    assert option_signature(opt(card_id=100)) != option_signature(opt(card_id=200))
    assert option_signature(opt(attack_id=7)) != option_signature(opt(attack_id=8))


def test_action_signature_is_order_independent_for_multiselect():
    sel = make_select([opt(card_id=100), opt(card_id=200)])
    assert action_signature(sel, (0, 1)) == action_signature(sel, (1, 0))


def test_index_misalignment_across_determinizations_merges_stats():
    # Determinization 1 presents [A, B]; determinization 2 presents [B, A].
    det1 = make_select([opt(card_id=100), opt(card_id=200)])
    det2 = make_select([opt(card_id=200), opt(card_id=100)])
    sig_a_in_det1 = action_signature(det1, (0,))
    sig_a_in_det2 = action_signature(det2, (1,))
    assert sig_a_in_det1 == sig_a_in_det2  # same logical move -> same tree child
    node = Node()
    node.children[sig_a_in_det1] = Node(visits=3, value_sum=2.0)
    assert node.children[sig_a_in_det2].visits == 3


def test_ucb_prefers_unvisited_then_best_mean():
    rng = random.Random(0)
    node = Node(visits=10)
    s1, s2, s3 = ("a",), ("b",), ("c",)
    node.children[s1] = Node(visits=5, value_sum=1.0)
    node.children[s2] = Node(visits=5, value_sum=4.5)
    assert ucb_pick(node, [s1, s2, s3], c=1.4, rng=rng) == s3  # unvisited first
    node.children[s3] = Node(visits=5, value_sum=0.5)
    node.visits = 15
    assert ucb_pick(node, [s1, s2, s3], c=0.0, rng=rng) == s2  # pure exploit -> best mean


def test_puct_prior_outweighs_value():
    node = Node(visits=4)
    node.children[SIG_A] = Node(visits=2, value_sum=1.0)
    node.children[SIG_B] = Node(visits=2, value_sum=1.2)
    got = puct_pick(node, [SIG_A, SIG_B], {SIG_A: 0.8, SIG_B: 0.2}, 1.0)
    assert got == SIG_A  # score(A)=0.5+0.8*2/3=1.03333 > score(B)=0.6+0.2*2/3=0.73333


def test_puct_unvisited_gets_neutral_q_plus_prior():
    node = Node(visits=1)
    node.children[SIG_A] = Node(visits=1, value_sum=1.0)
    got = puct_pick(node, [SIG_A, SIG_B], {SIG_A: 0.0, SIG_B: 1.0}, 1.0)
    assert got == SIG_B  # score(A)=1.0+0=1.0 < score(B)=0.5+1.0*1/1=1.5


def test_puct_missing_prior_defaults_zero():
    node = Node(visits=1)
    got = puct_pick(node, [SIG_A, SIG_B], {SIG_A: 0.5}, 1.0)
    assert got == SIG_A  # B: q=0.5+0; A: q=0.5+0.5*1/1=1.0
