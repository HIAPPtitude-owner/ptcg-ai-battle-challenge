"""Founding Census seeding — single-core concepts + their built decks into the
SQLite deck database (tournament plan Task 3).

`seed_census` is idempotent: concepts and decks are content-addressed
(`builder.concept_id` / `deck_id`), so `INSERT OR IGNORE` makes re-running
the seed a no-op on rows that already exist. Every buildable concept gets
exactly one `decks` row (`shell_variant=0`) and one `coverage` row;
unbuildable concepts are recorded with `status='unbuildable'` and a reason
string — they are never silently dropped.

Pair-concept enumeration + lazy activation (tournament plan Task 8):
`seed_pair_concepts` enumerates ALL `C(815,2)=331,705` unordered pairs of
single-core NAMES and inserts them as DORMANT `concepts` rows
(`status='untested'`, no `decks`/`coverage` row) — playing all pairs at
founding screening depth was measured infeasible at every plausible
throughput (~6.8 years @2,000 games/day), so pairs are enumerated once and
activated generationally. `activate_pair_concepts` selects dormant pairs
whose both single cores have already proven out and builds their deck
lazily, only at activation time.
"""

from __future__ import annotations

import itertools
import json
import sqlite3

from ptcg.factory import deckdb, loop_state
from ptcg.factory.anchor import ANCHOR_DECK_ID
from ptcg.factory.builder import (
    BUILDER_VERSION,
    Concept,
    build_deck,
    composition_counts,
    concept_id,
    enumerate_concepts,
)


def deck_id(concept_id_value: str, shell_variant: int) -> str:
    """Stable, deterministic id for a (concept, shell_variant) deck pairing."""
    return f"{concept_id_value}-sv{shell_variant}"


#: One-time census-screening floor (spec Locked Decision 3): every buildable
#: single-core concept must reach this many `screening` games before the
#: Founding Census is considered complete.
SCREENING_FLOOR = 15

#: Post-census screening floor (spec Locked Decision 3): activated pairs and
#: shell variants use this deeper floor. Not consumed by this task's
#: functions directly -- exported here as the schedule/scope's companion
#: constant so T8+ callers have one source of truth for both floors.
STANDARD_SCREENING_FLOOR = 40

#: Every concept in the "buildable field" is represented by exactly ONE deck
#: (the lowest `shell_variant` on file for that concept -- `shell_variant=0`
#: at census time per `seed_census`/T8's lazy activation). Joining through
#: this subquery rather than hardcoding `shell_variant=0` keeps the query
#: correct if a future task ever adds additional shell variants.
_CANONICAL_DECK_JOIN_SQL = (
    "JOIN decks d ON d.concept_id = co.concept_id "
    "AND d.shell_variant = ("
    "SELECT MIN(d2.shell_variant) FROM decks d2 WHERE d2.concept_id = co.concept_id"
    ")"
)

#: Under-covered candidates, worst-rated/least-covered first. `NULLS FIRST`
#: is SQLite's ASC default, stated literally for self-documentation:
#: never-decisively-played concepts (`rating IS NULL`) are scheduled before
#: rated ones -- matching the binding interface contract that a NULL rating
#: means "no decisive game yet", not "never played" (T5 review fix).
#: `games_played ASC` then `concept_id ASC` give a fully deterministic
#: tie-break (no dict/set-order dependence). `c.status IN ('untested',
#: 'active')` excludes culled/unbuildable/finalist concepts (Task 4,
#: anchor-opponent rework) -- a culled concept must never be re-scheduled,
#: and the anchor's own concept (`status='finalist'`) must never be a
#: SUBJECT even though it always has a deck row.
#:
#: Composition tie-break (spec 2026-08-13 census/screening regime, Locked
#: Decision 1): within identical (rating, games_played), leaner decks
#: screen first -- energy_count then pokemon_count from the canonical
#: deck's materialized columns, NULLS LAST so an unstamped deck never
#: jumps the queue. `concept_id ASC` stays the deterministic final
#: tiebreak. These composition keys are tie-breaks WITHIN coverage order,
#: not a composition-first re-screen: in the CURRENT pool energy_count is
#: uniformly 16 among the unrated mass (the key is inert there), so the
#: ordering directive within that mass is carried by pokemon_count.
#: Rated lean-energy concepts sort behind the unrated mass by design,
#: preserving worst-first re-rating semantics once coverage builds.
_CANDIDATES_QUERY = (
    "SELECT co.concept_id AS concept_id, d.id AS deck_id, co.rating AS rating, "
    "co.games_played AS games_played "
    "FROM coverage co "
    "JOIN concepts c ON c.id = co.concept_id "
    f"{_CANONICAL_DECK_JOIN_SQL} "
    "WHERE co.games_played < ? AND c.status IN ('untested','active') "
    "ORDER BY co.rating ASC NULLS FIRST, co.games_played ASC, "
    "d.energy_count ASC NULLS LAST, d.pokemon_count ASC NULLS LAST, "
    "co.concept_id ASC "
    "LIMIT ?"
)

#: In-flight (`pending` or `claimed`) screening games per CONCEPT, counting
#: a game toward BOTH sides' concepts -- exactly mirroring how
#: `deckdb.record_result` bumps `coverage.games_played` for both decks'
#: concepts when the game completes (UNION ALL, not UNION, so a game whose
#: two decks share one concept counts twice, matching record_result's
#: double bump). A game therefore moves from this in-flight tally into
#: `games_played` one-for-one on completion: nothing is ever double-counted
#: between the two.
_PENDING_PER_CONCEPT_QUERY = (
    "SELECT d.concept_id AS concept_id, COUNT(*) AS n FROM ("
    "SELECT deck_a_id AS did FROM games "
    "WHERE status IN ('pending','claimed') AND purpose='screening' "
    "UNION ALL "
    "SELECT deck_b_id AS did FROM games "
    "WHERE status IN ('pending','claimed') AND purpose='screening'"
    ") g JOIN decks d ON d.id = g.did "
    "GROUP BY d.concept_id"
)


def schedule_screening_games(
    conn: sqlite3.Connection,
    target_per_concept: int = SCREENING_FLOOR,
    batch: int = 200,
    founding_agent_version: str = "v0.1",
) -> int:
    """Enqueue up to `batch` screening games for under-covered concepts,
    with PENDING-AWARE deficit accounting: each candidate is topped up to
    `max(0, target_per_concept - games_played - in_flight)` games, where
    `in_flight` counts that concept's already-`pending`/`claimed` screening
    games on EITHER side (`_PENDING_PER_CONCEPT_QUERY`) plus games enqueued
    earlier in this same call. An immediate re-invocation while games are
    still pending therefore enqueues 0 extra -- T16's loop can call this
    every tick without inflating the census budget.

    The opponent is ALWAYS the anchor deck (`anchor.ANCHOR_DECK_ID`), never
    another under-covered candidate (anchor-opponent rework, Task 4) --
    census now measures each concept's strength against a single known-fixed
    reference deck rather than round-robin against the shifting field. If
    the anchor deck row is absent (caller has not yet run
    `anchor.ensure_anchor_deck` -- Task 9 wires that into the scheduler),
    this is a no-op and returns 0 rather than erroring: scheduling with no
    valid opponent would either crash or silently misattribute results.

    Only concepts WITH a `coverage` row and `status IN ('untested','active')`
    are ever selected as subjects -- dormant pair concepts (T8, enumerated
    but not yet activated) have no `decks`/`coverage` row and are invisible
    to `_CANDIDATES_QUERY`; culled/unbuildable/finalist concepts (including
    the anchor's own concept, which is `status='finalist'`) are excluded by
    the same status filter so a culled concept is never re-scheduled and the
    anchor never plays itself. Candidates are the under-covered concepts
    (`games_played < target_per_concept`), worst-rated/least-covered first
    (spec Locked Decision 7). `priority` is set inversely proportional to
    the subject's current coverage (`1 / (games_played + 1)`) so
    `claim_next_game`'s `ORDER BY priority DESC` drains the worst-covered
    concepts first. Both sides play as the CURRENT BASELINE agent version
    (`loop_state.current_baseline(c)["version"]`) once a baseline has been
    founded, else `founding_agent_version` -- equal agents on both sides
    (spec: "equal agents (current baseline agent both sides)") means census
    measures DECK strength, not agent-version asymmetry. Returns the number
    of games actually enqueued.

    The ENTIRE pass -- anchor-presence check, candidate/in-flight reads, and
    every game INSERT -- runs inside ONE `deckdb._write` (`BEGIN IMMEDIATE`)
    transaction (Pattern SQLITE-TXN), so two concurrent callers serialize:
    the loser of the write-lock race observes the winner's freshly-inserted
    pending games in its own in-flight read and enqueues only the residual
    deficit (usually 0). A split read-then-enqueue would be the same
    stale-read TOCTOU class as the 5/day submission-cap race (commit
    `178043b`, `.claude/rules/single-actor-worker-tests.md`). Game rows are
    inserted directly here (same statement shape as `deckdb.enqueue_game`)
    because `_write` transactions cannot nest.
    """

    def _apply(c: sqlite3.Connection) -> int:
        anchor_row = c.execute("SELECT id FROM decks WHERE id = ?", (ANCHOR_DECK_ID,)).fetchone()
        if anchor_row is None:
            return 0  # anchor not yet registered -- nothing to schedule against

        candidates = c.execute(_CANDIDATES_QUERY, (target_per_concept, batch)).fetchall()
        if not candidates:
            return 0

        in_flight: dict[str, int] = {
            row["concept_id"]: row["n"] for row in c.execute(_PENDING_PER_CONCEPT_QUERY)
        }

        baseline = loop_state.current_baseline(c)
        agent_version = baseline["version"] if baseline is not None else founding_agent_version

        enqueued = 0
        for row in candidates:
            if enqueued >= batch:
                break
            subject_cid = row["concept_id"]
            deficit = target_per_concept - row["games_played"] - in_flight.get(subject_cid, 0)
            games_to_enqueue = min(deficit, batch - enqueued)
            if games_to_enqueue <= 0:
                continue
            priority = 1.0 / (row["games_played"] + 1)
            for _ in range(games_to_enqueue):
                c.execute(
                    "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, "
                    "agent_version_b, purpose, priority, status) "
                    "VALUES (?, ?, ?, ?, 'screening', ?, 'pending')",
                    (
                        row["deck_id"],
                        ANCHOR_DECK_ID,
                        agent_version,
                        agent_version,
                        priority,
                    ),
                )
                in_flight[subject_cid] = in_flight.get(subject_cid, 0) + 1
                enqueued += 1

        return enqueued

    return deckdb._write(conn, _apply)


def census_complete(conn: sqlite3.Connection, floor: int = SCREENING_FLOOR) -> bool:
    """True once every buildable, non-culled SINGLE-CORE concept has
    `games_played >= floor`.

    Restricted to concepts with a `coverage` row -- only buildable concepts
    ever get one (`seed_census` never inserts `coverage` for an unbuildable
    concept, per `test_seed_census_unbuildable_concept_recorded_with_reason_never_dropped`)
    -- whose `cores` array has exactly one element. Dormant, not-yet-activated
    pair concepts (T8) have no `coverage` row at all and therefore never
    block completion, matching the founding-census play-singles-only scope.
    `c.status IN ('untested','active')` mirrors `_CANDIDATES_QUERY`'s filter
    (Task 4, anchor-opponent rework): a `'culled'` concept can never be
    re-scheduled by `schedule_screening_games`, so if it were still counted
    here and sat under-floor it would deadlock the census forever; excluding
    it here keeps "scheduled" and "counted toward completion" the same
    population.
    """
    row = conn.execute(
        "SELECT COUNT(*) FROM coverage co "
        "JOIN concepts c ON c.id = co.concept_id "
        "WHERE json_array_length(c.cores) = 1 AND c.status IN ('untested','active') "
        "AND co.games_played < ?",
        (floor,),
    ).fetchone()
    return row[0] == 0


def _insert_deck_row(
    c: sqlite3.Connection, did: str, cid: str, cards: list[int]
) -> None:
    """Single choke point for census deck inserts — stamps the materialized
    composition columns (spec §1) so every new deck row carries
    energy_count/pokemon_count from birth. shell_variant is always 0 at
    both call sites (seed_census, activate_pair_concepts)."""
    en, pk = composition_counts(cards)
    c.execute(
        "INSERT OR IGNORE INTO decks(id,concept_id,cards,shell_variant,"
        "energy_count,pokemon_count) VALUES(?,?,?,0,?,?)",
        (did, cid, json.dumps(cards), en, pk),
    )


def seed_census(conn: sqlite3.Connection) -> dict[str, int]:
    """Seed every single-core concept and its `shell_variant=0` deck.

    Idempotent: content-addressed ids (`builder.concept_id`, `deck_id`) plus
    `INSERT OR IGNORE` mean a second call inserts nothing new. Runs as one
    `deckdb._write` (`BEGIN IMMEDIATE`) transaction. Returns
    `{"concepts": n, "buildable": b, "unbuildable": u}`.
    """
    concepts = enumerate_concepts()

    def _apply(c: sqlite3.Connection) -> dict[str, int]:
        buildable = 0
        unbuildable = 0
        for concept in concepts:
            cid = concept_id(concept.cores)
            result = build_deck(concept)
            cores_json = json.dumps(list(concept.cores))
            if result.cards is None:
                unbuildable += 1
                c.execute(
                    "INSERT OR IGNORE INTO concepts(id,cores,builder_version,status,reason) "
                    "VALUES(?,?,?,?,?)",
                    (cid, cores_json, BUILDER_VERSION, "unbuildable", result.unbuildable_reason or ""),
                )
                continue

            buildable += 1
            c.execute(
                "INSERT OR IGNORE INTO concepts(id,cores,builder_version,status,reason) "
                "VALUES(?,?,?,?,?)",
                (cid, cores_json, BUILDER_VERSION, "untested", ""),
            )
            did = deck_id(cid, 0)
            _insert_deck_row(c, did, cid, result.cards)
            c.execute(
                "INSERT OR IGNORE INTO coverage(concept_id,games_played,distinct_opponents) "
                "VALUES(?,0,0)",
                (cid,),
            )

        return {
            "concepts": buildable + unbuildable,
            "buildable": buildable,
            "unbuildable": unbuildable,
        }

    return deckdb._write(conn, _apply)


def _insert_pair_batch(conn: sqlite3.Connection, rows: list[tuple[str, str, int, str, str]]) -> int:
    """Insert one batch of dormant pair-concept rows (Pattern SQLITE-TXN).
    `INSERT OR IGNORE` on content-addressed ids keeps this idempotent; the
    Python sqlite3 driver's `executemany` rowcount correctly reflects only
    the rows actually inserted (conflicts silently ignored count as 0), so
    the caller can sum rowcount across batches for an accurate total."""
    cur = conn.executemany(
        "INSERT OR IGNORE INTO concepts(id,cores,builder_version,status,reason) "
        "VALUES(?,?,?,?,?)",
        rows,
    )
    return cur.rowcount


def seed_pair_concepts(conn: sqlite3.Connection, batch_size: int = 50_000) -> int:
    """Enumerate every unordered pair of single-core concept NAMES and insert
    them as DORMANT `concepts` rows: `status='untested'`, content-addressed
    id via `concept_id((a, b))` (sorted inside, same scheme as singles),
    `cores` stored as `json.dumps(sorted([a, b]))`. Deliberately inserts
    **NO `decks` row and NO `builder.build_deck` call** at seed time —
    building all 331,705 decks upfront costs real time + storage for pairs
    that mostly never activate; `activate_pair_concepts` builds a deck
    lazily, only once a pair is actually selected.

    Draws candidate names from EVERY row in `concepts` with a single-element
    `cores` array — buildable AND unbuildable single concepts alike, since
    `seed_census` always inserts a `concepts` row for both (only buildable
    ones get a `decks`/`coverage` row). This matches the founding single
    count used by both this function and the census's own arithmetic:
    `C(815,2) = 331,705` pairs from the real ~815-name card pool.

    Idempotent (`INSERT OR IGNORE` on content-addressed ids): a second call
    inserts nothing new. Batched `executemany` inserts, `batch_size` rows
    per `BEGIN IMMEDIATE` transaction (7 short transactions at full scale)
    rather than holding the write lock for one giant insert (Pattern
    SQLITE-TXN) — no game-play or network ever runs inside a transaction.
    Returns the number of NEW rows inserted (0 on a pure repeat call).
    """
    names = sorted(
        json.loads(row["cores"])[0]
        for row in conn.execute(
            "SELECT cores FROM concepts WHERE json_array_length(cores) = 1"
        ).fetchall()
    )

    def _rows() -> list[tuple[str, str, int, str, str]]:
        out = []
        for a, b in itertools.combinations(names, 2):
            cores_json = json.dumps(sorted([a, b]))
            out.append((concept_id((a, b)), cores_json, BUILDER_VERSION, "untested", ""))
        return out

    inserted = 0
    batch: list[tuple[str, str, int, str, str]] = []
    for row in _rows():
        batch.append(row)
        if len(batch) >= batch_size:
            inserted += deckdb._write(conn, lambda c, b=batch: _insert_pair_batch(c, b))
            batch = []
    if batch:
        inserted += deckdb._write(conn, lambda c, b=batch: _insert_pair_batch(c, b))

    return inserted


#: Query for single-core concepts that have already "proven out": marked
#: `status='active'` AND carrying a decisive Bradley-Terry rating (`rating
#: IS NOT NULL` — a single with no decisive game yet has NOT cleared its own
#: census screening even if some external process already flipped its
#: status). Restricted to `json_array_length(cores)=1` and filtered through
#: the indexed `status` column first (`ix_concepts_status`), so this never
#: scans the full 332,520-row `concepts` table — only the (typically small,
#: generation-by-generation growing) set of active singles.
_ACTIVE_SINGLES_QUERY = (
    "SELECT c.cores AS cores, co.rating AS rating "
    "FROM concepts c JOIN coverage co ON co.concept_id = c.id "
    "WHERE c.status = 'active' AND json_array_length(c.cores) = 1 AND co.rating IS NOT NULL"
)


def activate_pair_concepts(conn: sqlite3.Connection, max_new: int = 10) -> int:
    """Activate up to `max_new` DORMANT two-core `concepts` rows whose BOTH
    single cores have already proven out (per `_ACTIVE_SINGLES_QUERY`),
    prioritized by combined single-core rating (best combined rating first,
    concept id as a deterministic tiebreak).

    For each selected pair: `build_deck` (two-core). On failure, records
    `status='unbuildable'` + reason — never silently dropped, and never
    re-attempted on a later call since the row is no longer `'untested'`.
    On success, inserts the `shell_variant=0` deck row + a zeroed `coverage`
    row and flips `status='active'` — the deck is built LAZILY here, never
    at `seed_pair_concepts` time. Both the unbuildable and the activating
    UPDATE are guarded (`WHERE id=? AND status='untested'`), so a second,
    concurrent caller racing the exact same pair sees its own UPDATE affect
    zero rows and correctly does not double-count or re-run `build_deck`'s
    result (Pattern SQLITE-TXN + `.claude/rules/single-actor-worker-tests.md`).
    Returns the number of pairs ACTUALLY activated (excludes unbuildable
    attempts and excludes any pair a concurrent caller already claimed).

    Complexity: reads only the active-singles set (small, indexed via
    `ix_concepts_status`) rather than scanning the full `concepts` table;
    each candidate pair's dormancy check is a single primary-key lookup on
    `concepts.id`, not a table scan.
    """
    active_singles = conn.execute(_ACTIVE_SINGLES_QUERY).fetchall()
    singles = sorted((json.loads(row["cores"])[0], row["rating"]) for row in active_singles)

    candidates: list[tuple[str, str, str, float]] = []
    for (name_a, rating_a), (name_b, rating_b) in itertools.combinations(singles, 2):
        pid = concept_id((name_a, name_b))
        row = conn.execute("SELECT status FROM concepts WHERE id = ?", (pid,)).fetchone()
        if row is None or row["status"] != "untested":
            continue  # not seeded yet, or already activated/attempted by an earlier call
        candidates.append((pid, name_a, name_b, rating_a + rating_b))

    candidates.sort(key=lambda t: (-t[3], t[0]))  # best combined rating first, deterministic
    selected = candidates[:max_new]

    activated = 0

    def _apply(c: sqlite3.Connection) -> None:
        nonlocal activated
        for pid, name_a, name_b, _combined in selected:
            result = build_deck(Concept(cores=(name_a, name_b)))
            if result.cards is None:
                c.execute(
                    "UPDATE concepts SET status='unbuildable', reason=? "
                    "WHERE id=? AND status='untested'",
                    (result.unbuildable_reason or "", pid),
                )
                continue
            cur = c.execute(
                "UPDATE concepts SET status='active' WHERE id=? AND status='untested'",
                (pid,),
            )
            if cur.rowcount != 1:
                continue  # a concurrent caller already claimed this pair — no double count
            did = deck_id(pid, 0)
            _insert_deck_row(c, did, pid, result.cards)
            c.execute(
                "INSERT OR IGNORE INTO coverage(concept_id,games_played,distinct_opponents) "
                "VALUES(?,0,0)",
                (pid,),
            )
            activated += 1

    deckdb._write(conn, _apply)
    return activated


#: Single-core concepts eligible for promotion: still dormant (`'untested'`),
#: cleared the census screening floor, and carry a decisive Bradley-Terry
#: rating. Mirrors `_ACTIVE_SINGLES_QUERY`'s own readiness gate
#: (`status='active' AND rating IS NOT NULL`) one step upstream -- this is
#: the query that actually PRODUCES the `status='active'` rows
#: `_ACTIVE_SINGLES_QUERY` reads.
_PROMOTABLE_SINGLES_QUERY = (
    "SELECT c.id AS concept_id "
    "FROM concepts c JOIN coverage co ON co.concept_id = c.id "
    "WHERE c.status = 'untested' AND json_array_length(c.cores) = 1 "
    "AND co.games_played >= ? AND co.rating IS NOT NULL"
)


def promote_proven_singles(conn: sqlite3.Connection, floor: int = SCREENING_FLOOR) -> int:
    """Promote single-core concepts from `'untested'` to `'active'` once
    they've cleared the census screening floor with a decisive Bradley-Terry
    rating (`games_played >= floor AND rating IS NOT NULL`).

    CONFIRMED PLAN GAP (tournament plan Task 10 integration test / containment
    gate): no task in the plan (T1-T21) ever promotes a single-core concept
    out of `'untested'` -- only pair rows ever reach `'active'`, and only
    inside `activate_pair_concepts` itself. Without this function,
    `_ACTIVE_SINGLES_QUERY` -- `activate_pair_concepts`'s own gate, which
    requires `status='active' AND rating IS NOT NULL` -- permanently matches
    zero rows no matter how many singles finish census screening with a
    decisive rating, making pair activation a production no-op. Derived from
    the plan's own design language: T8's pair gate already requires
    `status='active' AND rating IS NOT NULL` for EACH core: this function is
    the missing writer that makes a single core actually reach that state.
    Natural seam: called wherever a census refresh tick completes (after
    `rating.refresh_field_ratings`, before `activate_pair_concepts`) -- see
    `tests/test_factory_tournament_phase1.py`.

    Guarded column-scoped UPDATE (`WHERE id=? AND status='untested'`), so a
    concurrent caller racing the same concept sees its own UPDATE affect zero
    rows -- no double-count (Pattern SQLITE-TXN +
    `.claude/rules/single-actor-worker-tests.md`). Idempotent: a concept
    already `'active'` is invisible to `_PROMOTABLE_SINGLES_QUERY` (its
    `status` column no longer matches `'untested'`), so a repeat call over
    the same state promotes nothing further. Returns the number of concepts
    actually promoted.
    """

    def _apply(c: sqlite3.Connection) -> int:
        promoted = 0
        for row in c.execute(_PROMOTABLE_SINGLES_QUERY, (floor,)).fetchall():
            cur = c.execute(
                "UPDATE concepts SET status='active' WHERE id=? AND status='untested'",
                (row["concept_id"],),
            )
            promoted += cur.rowcount
        return promoted

    return deckdb._write(conn, _apply)
