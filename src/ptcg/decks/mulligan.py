"""Analytic mulligan probability — no games needed, pure hypergeometric."""

from math import comb

from ptcg.decks.validate import is_basic_pokemon

_DECK = 60
_HAND = 7


def mulligan_rate_from_basic_count(basics: int) -> float:
    """P(no Basic Pokemon among 7 of 60) = C(60-b, 7) / C(60, 7)."""
    return comb(_DECK - basics, _HAND) / comb(_DECK, _HAND)


def count_basic_pokemon(deck: list[int]) -> int:
    return sum(1 for cid in deck if is_basic_pokemon(cid))


def mulligan_rate(deck: list[int]) -> float:
    return mulligan_rate_from_basic_count(count_basic_pokemon(deck))
