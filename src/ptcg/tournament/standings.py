"""Markdown standings report over a tournament ledger.

Field win rate is the *unweighted* mean of a deck's per-pairing decided-game
win rates (pairings deliberately accrue different sample sizes; averaging
per-pairing rates rather than pooling games avoids a high-n blowout pairing
dominating the deck's field placement). Pairings with zero decided games are
excluded from the mean and rendered as ``?`` in the matchup matrix.
"""
from __future__ import annotations

from pathlib import Path

from ptcg.tournament.ledger import Ledger, PairingRecord
from ptcg.tournament.scheduler import classify

MULLIGAN_FLAG_THRESHOLD = 0.15


def _stem(csv_name: str) -> str:
    return Path(csv_name).stem


def _pairing_record(ledger: Ledger, a: str, b: str) -> PairingRecord | None:
    key = (a, b) if a <= b else (b, a)
    return ledger.pairings.get(key)


def _decided_win_rate(rec: PairingRecord, deck: str) -> tuple[float | None, int]:
    """Win rate for `deck` within this pairing over decided games only."""
    decided = rec.wins_a + rec.wins_b
    if decided == 0:
        return None, 0
    wins = rec.wins_a if rec.deck_a == deck else rec.wins_b
    return wins / decided, decided


def standings_markdown(ledger: Ledger, mulligan: dict[str, float]) -> str:
    deck_hashes = list(ledger.decks.keys())
    agent = next((rec.agent for rec in ledger.pairings.values()), "")
    total_games = sum(rec.games for rec in ledger.pairings.values())

    field_wr: dict[str, float] = {}
    pairing_counts: dict[str, dict[str, int]] = {}
    for deck in deck_hashes:
        rates: list[float] = []
        counts = {"resolved": 0, "capped": 0, "open": 0}
        for other in deck_hashes:
            if other == deck:
                continue
            rec = _pairing_record(ledger, deck, other)
            if rec is None:
                continue
            counts[classify(rec)] += 1
            wr, decided = _decided_win_rate(rec, deck)
            if decided > 0:
                rates.append(wr)
        field_wr[deck] = sum(rates) / len(rates) if rates else 0.0
        pairing_counts[deck] = counts

    order = sorted(deck_hashes, key=lambda d: field_wr[d], reverse=True)

    lines = [f"## Tournament standings — {agent}, {total_games} games", ""]
    lines.append("| Deck | Field WR | Mulligan | Pairings resolved/capped/open |")
    lines.append("| --- | --- | --- | --- |")
    for deck in order:
        stem = _stem(ledger.decks[deck])
        mrate = mulligan.get(deck, 0.0)
        mstr = f"{mrate:.3f}"
        if mrate > MULLIGAN_FLAG_THRESHOLD:
            mstr += " ⚠"
        counts = pairing_counts[deck]
        pstr = f"{counts['resolved']}/{counts['capped']}/{counts['open']}"
        lines.append(f"| {stem} | {field_wr[deck]:.3f} | {mstr} | {pstr} |")

    lines.append("")
    header_cells = " | ".join(_stem(ledger.decks[d]) for d in order)
    lines.append(f"| vs -> | {header_cells} |")
    lines.append("| --- | " + " | ".join("---" for _ in order) + " |")
    for row_deck in order:
        row_cells = []
        for col_deck in order:
            if row_deck == col_deck:
                row_cells.append("—")
                continue
            rec = _pairing_record(ledger, row_deck, col_deck)
            if rec is None:
                row_cells.append("?")
                continue
            wr, decided = _decided_win_rate(rec, row_deck)
            row_cells.append("?" if decided == 0 else f"{wr:.2f} ({decided})")
        row_stem = _stem(ledger.decks[row_deck])
        lines.append(f"| {row_stem} | " + " | ".join(row_cells) + " |")

    return "\n".join(lines)
