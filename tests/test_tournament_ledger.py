# tests/test_tournament_ledger.py
from pathlib import Path

from ptcg.tournament.ledger import Ledger, deck_hash


def test_deck_hash_order_independent():
    assert deck_hash([1, 2, 3]) == deck_hash([3, 1, 2])
    assert deck_hash([1, 2, 3]) != deck_hash([1, 2, 4])
    assert len(deck_hash([1, 2, 3])) == 12


def test_record_canonicalizes_pairing_order():
    led = Ledger()
    led.record("bbb", "aaa", "heuristic-v0", wins_a=7, wins_b=3, draws=0, discarded=0)
    rec = led.pairings[("aaa", "bbb")]
    assert (rec.wins_a, rec.wins_b) == (3, 7)  # swapped with the hashes
    assert rec.games == 10


def test_record_accumulates():
    led = Ledger()
    led.record("aaa", "bbb", "heuristic-v0", 3, 2, 0, 0)
    led.record("aaa", "bbb", "heuristic-v0", 1, 4, 1, 2)
    rec = led.pairings[("aaa", "bbb")]
    assert (rec.wins_a, rec.wins_b, rec.draws, rec.games, rec.discarded) == (4, 6, 1, 11, 2)


def test_sync_retires_stale_deck_and_stale_agent_rows():
    led = Ledger()
    led.record("aaa", "bbb", "heuristic-v0", 5, 5, 0, 0)
    led.record("aaa", "ccc", "old-agent", 5, 5, 0, 0)
    led.sync(active={"aaa": "a.csv", "bbb": "b.csv", "ccc": "c.csv"}, agent="heuristic-v0")
    assert ("aaa", "bbb") in led.pairings
    assert ("aaa", "ccc") not in led.pairings  # stale agent
    led.sync(active={"aaa": "a.csv", "ccc": "c.csv"}, agent="heuristic-v0")
    assert led.pairings == {}  # bbb edited/removed -> its rows retire


def test_save_load_roundtrip(tmp_path: Path):
    led = Ledger()
    led.sync(active={"aaa": "a.csv", "bbb": "b.csv"}, agent="heuristic-v0")
    led.record("aaa", "bbb", "heuristic-v0", 30, 20, 1, 0)
    p = tmp_path / "results.json"
    led.save(p)
    led2 = Ledger.load(p)
    assert led2.decks == led.decks
    assert led2.pairings[("aaa", "bbb")] == led.pairings[("aaa", "bbb")]
    assert not p.with_suffix(".json.tmp").exists()


def test_load_missing_file_gives_empty_ledger(tmp_path: Path):
    led = Ledger.load(tmp_path / "nope.json")
    assert led.pairings == {} and led.decks == {}
