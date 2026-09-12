"""Adaptive-sampling policy: which pairing plays next, and when to stop."""

from itertools import combinations

from ptcg.arena.stats import wilson_ci
from ptcg.tournament.ledger import Ledger, PairingRecord

BATCH_GAMES = 50
CAP_GAMES = 400


def classify(rec: PairingRecord) -> str:
    decided = rec.wins_a + rec.wins_b
    if decided > 0:
        lo, hi = wilson_ci(rec.wins_a, decided)
        if lo > 0.5 or hi < 0.5:
            return "resolved"
    if rec.games >= CAP_GAMES:
        return "capped"
    return "open"


def expected_pairings(deck_hashes: list[str]) -> list[tuple[str, str]]:
    return [tuple(sorted(p)) for p in combinations(sorted(deck_hashes), 2)]


def ensure_pairings(ledger: Ledger, deck_hashes: list[str], agent: str) -> None:
    for a, b in expected_pairings(deck_hashes):
        if (a, b) not in ledger.pairings:
            ledger.record(a, b, agent, 0, 0, 0, 0)


def next_batch(ledger: Ledger) -> tuple[tuple[str, str], int] | None:
    open_recs = [
        (key, rec) for key, rec in ledger.pairings.items() if classify(rec) == "open"
    ]
    if not open_recs:
        return None
    key, rec = min(open_recs, key=lambda kr: (kr[1].games, kr[0]))
    return key, min(BATCH_GAMES, CAP_GAMES - rec.games)
