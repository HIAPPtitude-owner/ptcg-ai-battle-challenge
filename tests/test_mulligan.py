from ptcg.decks.mulligan import mulligan_rate, mulligan_rate_from_basic_count


def test_rate_from_count_hand_checked_values():
    # C(45,7)/C(60,7) = 45_379_620 / 386_206_920
    assert abs(mulligan_rate_from_basic_count(15) - 0.117501) < 1e-6
    # C(50,7)/C(60,7) = 99_884_400 / 386_206_920
    assert abs(mulligan_rate_from_basic_count(10) - 0.258629) < 1e-6
    assert mulligan_rate_from_basic_count(0) == 1.0
    assert mulligan_rate_from_basic_count(60) == 0.0


def test_rate_on_real_deck_matches_its_basic_count():
    from ptcg.arena.runner import load_deck
    from ptcg.decks.mulligan import count_basic_pokemon

    deck = load_deck("tests/fixtures/sample_deck.csv")
    b = count_basic_pokemon(deck)
    assert 0 < b < 60
    assert mulligan_rate(deck) == mulligan_rate_from_basic_count(b)
