import pytest
from pathlib import Path

from ptcg.decks.validate import MIN_BASIC_CARDS, is_basic_pokemon, validate_deck

CANDIDATES = sorted(Path("src/ptcg/decks/candidates").glob("anchor-cand-*.csv"))


def test_exactly_four_candidates():
    assert len(CANDIDATES) == 4


@pytest.mark.parametrize("csv_path", CANDIDATES, ids=lambda p: p.stem)
def test_candidate_is_legal_and_min_basics(csv_path):
    cards = [int(x) for x in csv_path.read_text(encoding="utf-8").split()]
    assert len(cards) == 60
    assert validate_deck(cards) == []
    assert sum(1 for c in cards if is_basic_pokemon(c)) >= MIN_BASIC_CARDS
