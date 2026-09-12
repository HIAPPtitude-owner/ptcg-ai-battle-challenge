"""Persistent tournament results ledger.

Pairings are keyed by content-hash of the two decks involved (order-
independent — the engine shuffles, so deck order never affects gameplay).
Writes are atomic: results are written to a `.tmp` sibling then swapped in
with `os.replace` so a crash mid-write never corrupts the on-disk ledger.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def deck_hash(deck: list[int]) -> str:
    """Order-independent content hash of a deck, first 12 hex chars of sha256."""
    canonical = ",".join(map(str, sorted(deck)))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


@dataclass
class PairingRecord:
    deck_a: str
    deck_b: str
    agent: str
    wins_a: int
    wins_b: int
    draws: int
    games: int
    discarded: int


class Ledger:
    def __init__(self) -> None:
        self.decks: dict[str, str] = {}
        self.pairings: dict[tuple[str, str], PairingRecord] = {}

    def sync(self, active: dict[str, str], agent: str) -> None:
        """Register the active deck set; retire any pairing whose either deck
        is no longer active, or whose agent no longer matches."""
        self.decks = dict(active)
        self.pairings = {
            key: rec
            for key, rec in self.pairings.items()
            if rec.deck_a in self.decks and rec.deck_b in self.decks and rec.agent == agent
        }

    def record(
        self,
        deck_a: str,
        deck_b: str,
        agent: str,
        wins_a: int,
        wins_b: int,
        draws: int,
        discarded: int,
    ) -> None:
        if deck_a > deck_b:
            deck_a, deck_b, wins_a, wins_b = deck_b, deck_a, wins_b, wins_a
        key = (deck_a, deck_b)
        games = wins_a + wins_b + draws
        existing = self.pairings.get(key)
        if existing is None:
            self.pairings[key] = PairingRecord(
                deck_a=deck_a,
                deck_b=deck_b,
                agent=agent,
                wins_a=wins_a,
                wins_b=wins_b,
                draws=draws,
                games=games,
                discarded=discarded,
            )
        else:
            existing.agent = agent
            existing.wins_a += wins_a
            existing.wins_b += wins_b
            existing.draws += draws
            existing.games += games
            existing.discarded += discarded

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "decks": self.decks,
            "pairings": [asdict(rec) for rec in self.pairings.values()],
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path) -> "Ledger":
        path = Path(path)
        led = cls()
        if not path.exists():
            return led
        payload = json.loads(path.read_text())
        led.decks = dict(payload.get("decks", {}))
        for rec_dict in payload.get("pairings", []):
            rec = PairingRecord(**rec_dict)
            led.pairings[(rec.deck_a, rec.deck_b)] = rec
        return led
