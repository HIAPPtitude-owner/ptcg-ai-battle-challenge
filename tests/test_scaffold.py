"""Scaffold sanity: vendored SDK imports and exposes the card database."""


def test_cg_sdk_imports_and_lists_cards():
    from cg.api import all_card_data

    cards = all_card_data()
    assert len(cards) > 100
    assert all(hasattr(c, "cardId") for c in cards[:5])


def test_sample_deck_fixture_has_60_lines():
    from pathlib import Path

    lines = Path("tests/fixtures/sample_deck.csv").read_text().strip().splitlines()
    assert len(lines) == 60
    assert all(line.strip().isdigit() for line in lines)
