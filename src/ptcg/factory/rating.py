"""`coverage.rating` = win-rate-vs-anchor (tournament plan Task 5, Anchor
Pressure slice).

SCALE CHANGE, stated loudly: `coverage.rating` is now a plain win rate in
[0, 1] against the fixed anchor deck (`ANCHOR_DECK_ID`), NOT a Bradley-Terry
log-strength. Internal Bradley-Terry field rating is DROPPED -- `bt.py`
itself is untouched (other callers, e.g. `evolution.py`'s two-factor agent/
deck fit, still use it), but `refresh_field_ratings` no longer imports or
calls it. This is possible because T4's anchor-opponent rework made every
`screening` game's `deck_b_id == ANCHOR_DECK_ID`: mirror-vs-mirror games (the
entire pre-slice history) no longer occur going forward, and the
`deck_b_id = ?` filter below excludes any stale mirror rows from the tally
outright, so old BT-scale numbers already on disk are never re-derived (the
reseed migration in T10 culls the concepts that still carry them).

Every ordering consumer of `coverage.rating` is scale-agnostic (pure
ORDER BY / NULL check), verified by grep across `src/` and `scripts/`:
`loop._TOP_FIELD_QUERY`, `loop_scheduler._BEST_CENSUS_DECK_QUERY`,
`census._CANDIDATES_QUERY`/`_ACTIVE_SINGLES_QUERY`/`_PROMOTABLE_SINGLES_QUERY`
(`activate_pair_concepts` / `promote_proven_singles`), `subscheduler.
_BEST_ACTIVE_DECKS_QUERY`, and `ui_server`'s review-queue query. The one
consumer that does arithmetic is `census.activate_pair_concepts`'s
`rating_a + rating_b` combined-score sum, which remains a valid ordering key
on the new [0, 2] range (higher combined wr-vs-anchor still means "more
promising pair"). None of these needed to change for this task.

A draw-only concept now rates `0.0` (NOT NULL): a wr of zero is a real,
decisive "does not beat the anchor" measurement, unlike the dropped BT model
where a draw-only concept carried no directional signal and legitimately
stayed NULL. `distinct_opponents` is always `1` once any anchor-screening
game exists for a concept (there is exactly one opponent -- the anchor).

Complexity: a single query over `done`/`screening`/`deck_b_id=ANCHOR_DECK_ID`
games is O(anchor-screening games); the Python-side tally + write-back is
O(concepts written), one transaction. `scope_concept_ids` still lets a
caller (T7's throttled scheduler) restrict which concepts get a write
without changing the query's underlying row scan.

Write-back only touches the `rating` and `distinct_opponents` columns of
`coverage` (never `games_played`, which `deckdb.record_result` owns), so a
concurrent result write and a concurrent rating refresh merge field-by-field
under SQLite's WAL write-lock serialization rather than one clobbering the
other (`.claude/rules/single-actor-worker-tests.md`).
"""

from __future__ import annotations

import sqlite3

from ptcg.factory import deckdb
from ptcg.factory.anchor import ANCHOR_CONCEPT_ID, ANCHOR_DECK_ID

_ANCHOR_SCREENING_QUERY = (
    "SELECT g.winner, deck_a.concept_id AS ca "
    "FROM games g JOIN decks deck_a ON g.deck_a_id = deck_a.id "
    "WHERE g.status = 'done' AND g.purpose = 'screening' AND g.deck_b_id = ?"
)


def refresh_field_ratings(
    conn: sqlite3.Connection,
    scope_concept_ids: list[str] | None = None,
) -> int:
    """Rebuild win-rate-vs-anchor ratings from `done` `screening` games
    (`deck_b_id == ANCHOR_DECK_ID`) and write them back to
    `coverage.rating` / `coverage.distinct_opponents`.

    When `scope_concept_ids` is given, only challenger concepts in the scope
    set are counted/written -- lets a caller (T7's throttled scheduler)
    restrict the refresh to an active/recently-played subset.

    `rating` = wins / n for every concept with >= 1 done anchor-screening
    game (`winner == 0` only counts as a win; `winner == 2` draws count in
    the denominator but not the numerator; see the module docstring for why
    a draw-only concept rates `0.0`, not NULL). `distinct_opponents` is `1`
    for every concept written (the anchor is the sole opponent). Returns the
    number of concepts written.
    """
    scope = set(scope_concept_ids) if scope_concept_ids is not None else None
    wins: dict[str, int] = {}
    n: dict[str, int] = {}
    for row in conn.execute(_ANCHOR_SCREENING_QUERY, (ANCHOR_DECK_ID,)):
        ca = row["ca"]
        if ca == ANCHOR_CONCEPT_ID:
            continue  # defensive: the anchor never rates itself
        if scope is not None and ca not in scope:
            continue
        n[ca] = n.get(ca, 0) + 1
        if row["winner"] == 0:
            wins[ca] = wins.get(ca, 0) + 1

    def _apply(c: sqlite3.Connection) -> None:
        for cid, total in n.items():
            c.execute(
                "UPDATE coverage SET rating = ?, distinct_opponents = 1 "
                "WHERE concept_id = ?",
                (wins.get(cid, 0) / total, cid),
            )

    deckdb._write(conn, _apply)
    return len(n)
