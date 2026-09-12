from ptcg.tournament.ledger import Ledger, PairingRecord
from ptcg.tournament.scheduler import (
    BATCH_GAMES,
    CAP_GAMES,
    classify,
    ensure_pairings,
    next_batch,
)


def _rec(wins_a: int, wins_b: int, draws: int = 0) -> PairingRecord:
    return PairingRecord(
        deck_a="aaa", deck_b="bbb", agent="heuristic-v0",
        wins_a=wins_a, wins_b=wins_b, draws=draws,
        games=wins_a + wins_b + draws, discarded=0,
    )


def test_classify_open_at_30_of_50():
    assert classify(_rec(30, 20)) == "open"  # Wilson CI [0.4618, 0.7239] straddles 0.5


def test_classify_resolved_at_40_of_50():
    assert classify(_rec(40, 10)) == "resolved"  # CI [0.6696, 0.8876] excludes 0.5


def test_classify_capped_tie_at_400():
    assert classify(_rec(200, 200)) == "capped"


def test_classify_zero_games_is_open():
    assert classify(_rec(0, 0)) == "open"


def test_next_batch_prefers_fewest_games_and_sizes_to_cap():
    led = Ledger()
    ensure_pairings(led, ["aaa", "bbb", "ccc"], "heuristic-v0")
    led.record("aaa", "bbb", "heuristic-v0", 30, 20, 0, 0)   # open, 50 games
    led.record("aaa", "ccc", "heuristic-v0", 40, 10, 0, 0)   # resolved
    key, size = next_batch(led)
    assert key == ("bbb", "ccc") and size == BATCH_GAMES     # 0 games -> first
    led.record("bbb", "ccc", "heuristic-v0", 190, 185, 0, 0) # 375 games, still open
    led.record("aaa", "bbb", "heuristic-v0", 170, 180, 0, 0) # now 400 -> capped
    key, size = next_batch(led)
    assert key == ("bbb", "ccc") and size == CAP_GAMES - 375  # 25, not 50


def test_next_batch_none_when_all_settled():
    led = Ledger()
    ensure_pairings(led, ["aaa", "bbb"], "heuristic-v0")
    led.record("aaa", "bbb", "heuristic-v0", 40, 10, 0, 0)
    assert next_batch(led) is None
