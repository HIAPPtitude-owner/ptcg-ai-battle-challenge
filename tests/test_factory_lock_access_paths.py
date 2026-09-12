"""Access-path guard (.claude/rules/single-actor-worker-tests.md
access-path-analysis): EXPLAIN QUERY PLAN assertions for EVERY query that
runs INSIDE a `deckdb._write` (`BEGIN IMMEDIATE`) `_apply` closure and
touches one of the five LARGE tables (`games` ~190k rows, `decks` ~95k,
`decisions`, `concepts` ~332k, `coverage` ~95k on the live DB).

Why this file exists (factory-db-lock-contention, 2026-08-08): this repo has
now shipped TWO unindexed-SCAN-inside-BEGIN-IMMEDIATE production incidents
(61s bulk-restore, 2026-08-05; 56s crown enqueue, 2026-08-07/08) -- both
caught only at review, after the fact. This file is the standing executable
guard so a FUTURE query/schema change that regresses any of these shapes to
a table SCAN fails the suite immediately instead of starving the runner/
scheduler workers' 30s `busy_timeout` in production.

Methodology: every test builds a virgin schema via `deckdb.init_db` on a
`tmp_path` DB (never the production `tournament.db`), then asserts
`EXPLAIN QUERY PLAN` on the REAL query text (copied verbatim from the
source, not a simplified stand-in) uses an index -- `"SCAN <bigtable>"` must
NOT appear in the plan text. SQLite's query planner without `ANALYZE`
(`sqlite_stat1` is never populated by any test here) chooses execution
strategy from index availability/heuristics, not live row counts, so a
freshly-created empty-table schema faithfully answers "CAN this query use an
index" -- exactly the question that matters for a regression guard. Mirrors
the existing precedent in `test_factory_deckdb.py`
(`test_decks_concept_index_exists_and_is_used`), `test_factory_loop_crown.py`
(`test_crown_pair_count_query_uses_covering_index`), and
`test_factory_ui_actions.py` (`test_decisions_concept_index_exists_and_is_used`)
-- this file does NOT duplicate those three; see the `decisions`-table note
below.

Small-table EXEMPTION (per this task's brief): `offspring` (~88 rows live),
`baselines`, `anchor_checks`, `floor_checks`, `net_checks`,
`pair_gate_checks`, `meta`, `game_recovery` are all small, bounded, non-
scan-risk tables -- no query against them alone is asserted here, even when
it runs inside a `_write` lock (e.g. `resolve_crown`'s pending-elect COUNT
joining `anchor_checks`+`offspring`, or `_CROWN_FIELD_QUERY`'s `offspring`+
`floor_checks`+`baselines`+`anchor_checks` join). `SCAN anchor_checks` /
`SCAN game_recovery` appearing in a plan (see the two supersede-DELETE tests
below, whose `NOT IN`/list-subquery side touches these small tables) is
therefore expected and NOT asserted against.

`decisions`-table coverage: already guarded by
`test_factory_ui_actions.py::test_decisions_concept_index_exists_and_is_used`
(pins `_restore_target`'s exact SELECT). Not duplicated here.

`migrate_decisions` (deckdb.py): NOT included -- its `INSERT ... SELECT ...
FROM decisions_v1` is a one-time, idempotent v1->v2 table REBUILD that must
read every row of the table being replaced; there is no index that changes
this (a full read of the source table is the intended, inherent behavior of
a rebuild), so it is exempt as "SCAN-but-genuinely-cheap-forever" in the
sense that it runs at most once per DB and its cost is O(existing
`decisions` rows) by design, not a regression surface.

`rating._ANCHOR_SCREENING_QUERY` and `subscheduler.py`'s queries
(`_BEST_ACTIVE_DECKS_QUERY`, `_ANCHOR_SCREENING_GAMES_QUERY`, etc.) are
observationally NOT lock-held (`subscheduler.py` never calls
`deckdb._write` at all; `rating.refresh_field_ratings` runs its big read
BEFORE opening `_apply`) -- out of this task's Step-1 scope (queries inside
a `deckdb._write` `_apply`), so not asserted here even though some are hot.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ptcg.factory import deckdb, loop_scheduler


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    loop_scheduler.ensure_recovery_schema(conn)  # game_recovery, for H-shape queries
    return conn


def _eqp(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> str:
    return " | ".join(
        str(row[3]) for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params)
    )


# --- Group A: `games` WHERE purpose=? AND agent_version_a=? (COUNT/agg) --
# ix_games_crown_pair(purpose, agent_version_a, agent_version_b) covers this
# as a 2-column equality prefix.

_GROUP_A_QUERIES = [
    # (label, file:line, sql)
    (
        "anchor._enqueue: COUNT existing",
        "anchor.py:166",
        "SELECT COUNT(*) FROM games WHERE purpose='anchor' AND agent_version_a=?",
    ),
    (
        "floor._enqueue_attempt_games: COUNT existing",
        "floor.py:232",
        "SELECT COUNT(*) FROM games WHERE purpose='floor' AND agent_version_a=?",
    ),
    (
        "netcheck.enqueue_net_check: COUNT existing",
        "netcheck.py:149",
        "SELECT COUNT(*) FROM games WHERE purpose='netcheck' AND agent_version_a=?",
    ),
    (
        "loop.enqueue_confirm_series: COUNT existing",
        "loop.py:479",
        "SELECT COUNT(*) FROM games WHERE purpose = 'confirm' AND agent_version_a = ?",
    ),
]


def test_group_a_purpose_agent_a_count_shapes(tmp_path):
    conn = _db(tmp_path)
    for label, loc, sql in _GROUP_A_QUERIES:
        plan = _eqp(conn, sql, ("v0.1",))
        assert "ix_games_crown_pair" in plan, f"{label} ({loc}): {plan}"
        assert "SCAN games" not in plan, f"{label} ({loc}): {plan}"


def test_match_series_complete_agg_uses_index(tmp_path):
    """loop_scheduler._match_series_complete (loop_scheduler.py:289-294),
    plan-cited suspect. NOT itself lock-held (`_drive_offspring` calls it on
    the plain `conn`, never inside its own `_write`), but every stage
    function `_drive_offspring` calls immediately after DOES open its own
    lock -- verifying this hot per-tick query doesn't SCAN games matters for
    overall throughput even though it isn't a lock-DURATION risk per se.
    Brief: 'should be covered by ix_games_crown_pair (verify, don't
    assume)'."""
    conn = _db(tmp_path)
    sql = (
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done "
        "FROM games WHERE purpose = 'match' AND agent_version_a = ?"
    )
    plan = _eqp(conn, sql, ("v0.1.1",))
    assert "ix_games_crown_pair" in plan, plan
    assert "SCAN games" not in plan, plan


# --- Group B: `games` WHERE purpose=? AND status=? AND agent_version_a=?
# (aggregate RESULTS queries) -- same 2-column index prefix, status=?
# filtered as a residual (SQLite doesn't need a 3rd index column for an
# equality residual filter on an already-narrowed SEARCH).

_GROUP_B_QUERIES = [
    (
        "anchor._ANCHOR_RESULTS_QUERY",
        "anchor.py:185-189",
        "SELECT SUM(CASE WHEN winner = 0 THEN 1 ELSE 0 END) AS wins, "
        "COUNT(*) AS n FROM games "
        "WHERE purpose='anchor' AND status='done' AND agent_version_a = ?",
    ),
    (
        "floor._FLOOR_RESULTS_QUERY",
        "floor.py:84-88",
        "SELECT SUM(CASE WHEN winner = 0 THEN 1 ELSE 0 END) AS wins, "
        "COUNT(*) AS n FROM games "
        "WHERE purpose='floor' AND status='done' AND agent_version_a = ?",
    ),
    (
        "netcheck._NETCHECK_RESULTS_QUERY",
        "netcheck.py:47-51",
        "SELECT SUM(CASE WHEN winner = 0 THEN 1 ELSE 0 END) AS wins, "
        "COUNT(*) AS n FROM games "
        "WHERE purpose='netcheck' AND status='done' AND agent_version_a = ?",
    ),
    (
        "loop._OFFSPRING_CONFIRM_RESULTS_QUERY",
        "loop.py:382-385",
        "SELECT SUM(CASE WHEN winner = 0 THEN 1 ELSE 0 END) AS wins, COUNT(*) AS n "
        "FROM games WHERE purpose = 'confirm' AND status = 'done' AND agent_version_a = ?",
    ),
]


def test_group_b_purpose_status_agent_a_results_agg(tmp_path):
    conn = _db(tmp_path)
    for label, loc, sql in _GROUP_B_QUERIES:
        plan = _eqp(conn, sql, ("v0.1",))
        assert "ix_games_crown_pair" in plan, f"{label} ({loc}): {plan}"
        assert "SCAN games" not in plan, f"{label} ({loc}): {plan}"


def test_match_results_query_groupby_uses_index(tmp_path):
    """floor._MATCH_RESULTS_QUERY (floor.py:95-102, deliberately inlined
    copy of loop._OFFSPRING_MATCH_RESULTS_QUERY, loop.py:209-216 -- see
    floor.py's own comment on why it can't import loop.py). Adds
    `GROUP BY deck_a_id` on top of the Group-B shape."""
    conn = _db(tmp_path)
    sql = (
        "SELECT deck_a_id AS deck_id, "
        "SUM(CASE WHEN winner = 0 THEN 1 ELSE 0 END) AS wins, "
        "COUNT(*) AS n "
        "FROM games "
        "WHERE purpose = 'match' AND status = 'done' AND agent_version_a = ? "
        "GROUP BY deck_a_id"
    )
    plan = _eqp(conn, sql, ("v0.1.1",))
    assert "ix_games_crown_pair" in plan, plan
    assert "SCAN games" not in plan, plan


# --- Group C: `games` WHERE purpose=? AND status=? AND agent_version_a=?
# AND agent_version_b=? -- full 3-column ix_games_crown_pair equality match,
# status filtered as residual. This is the exact shape the 56s crown-enqueue
# incident replaced (deckdb.py's ix_games_crown_pair load-bearing comment).

_GROUP_C_QUERIES = [
    (
        "loop.resolve_crown: per-pair done-games SELECT winner (the O(K^2)-called query)",
        "loop.py:947-950",
        "SELECT winner FROM games WHERE purpose = 'crown' AND status = 'done' "
        "AND agent_version_a = ? AND agent_version_b = ?",
    ),
    (
        "loop.enqueue_crown_round_robin: stale-pair prune DELETE (<2-eligible branch)",
        "loop.py:767-770",
        "DELETE FROM games WHERE purpose = 'crown' AND status = 'pending' "
        "AND agent_version_a = ? AND agent_version_b = ?",
    ),
    (
        "loop.enqueue_crown_round_robin: stale-pair prune DELETE (self-healing branch)",
        "loop.py:826-830",
        "DELETE FROM games WHERE purpose = 'crown' AND status = 'pending' "
        "AND agent_version_a = ? AND agent_version_b = ?",
    ),
    (
        "pairgate.ensure_current_opponent: rekey supersede DELETE",
        "pairgate.py:378-382",
        "DELETE FROM games WHERE purpose='pair_gate' AND "
        "status='pending' AND agent_version_a=? AND agent_version_b=? "
        "AND deck_b_id=?",
    ),
]


def test_group_c_purpose_status_a_b_shapes(tmp_path):
    conn = _db(tmp_path)
    for label, loc, sql in _GROUP_C_QUERIES:
        params = ("v0.1.1", "v0.1.2") if sql.count("?") == 2 else ("v0.1.1", "v0.1.2", "d1")
        plan = _eqp(conn, sql, params)
        assert "ix_games_crown_pair" in plan, f"{label} ({loc}): {plan}"
        assert "SCAN games" not in plan, f"{label} ({loc}): {plan}"


def test_crown_excess_delete_in_limit_uses_index(tmp_path):
    """loop.enqueue_crown_round_robin's shrink-target excess-row DELETE
    (loop.py:804-810) -- a DELETE ... WHERE id IN (subquery) LIMIT ?, the
    one shape with a nested subquery, verified separately."""
    conn = _db(tmp_path)
    sql = (
        "DELETE FROM games WHERE id IN ("
        "SELECT id FROM games WHERE purpose = 'crown' "
        "AND status = 'pending' AND agent_version_a = ? "
        "AND agent_version_b = ? LIMIT ?)"
    )
    plan = _eqp(conn, sql, ("v0.1.1", "v0.1.2", 5))
    assert "ix_games_crown_pair" in plan, plan
    assert "SCAN games" not in plan, plan


# --- Group D: pairgate purpose+agent_a+agent_b[+deck_b_id] shapes --------

_GROUP_D_QUERIES = [
    (
        "pairgate._enqueue_series: COUNT existing",
        "pairgate.py:188-191",
        "SELECT COUNT(*) FROM games WHERE purpose='pair_gate' "
        "AND agent_version_a=? AND agent_version_b=? AND deck_b_id=?",
    ),
    (
        "pairgate._PAIR_GATE_RESULTS_QUERY",
        "pairgate.py:172-177",
        "SELECT SUM(CASE WHEN winner = 0 THEN 1 ELSE 0 END) AS wins, "
        "COUNT(*) AS n FROM games "
        "WHERE purpose='pair_gate' AND status='done' "
        "AND agent_version_a = ? AND agent_version_b = ? AND deck_b_id = ?",
    ),
]


def test_group_d_pairgate_shapes(tmp_path):
    conn = _db(tmp_path)
    for label, loc, sql in _GROUP_D_QUERIES:
        plan = _eqp(conn, sql, ("v0.1", "opp", "d1"))
        assert "ix_games_crown_pair" in plan, f"{label} ({loc}): {plan}"
        assert "SCAN games" not in plan, f"{label} ({loc}): {plan}"


# --- Group E: supersede DELETEs with NOT IN / != (no agent_version_a
# equality bound) -- verify these still SEARCH via purpose or status, never
# a full games scan, even though they can't use the full index prefix.

def test_anchor_supersede_delete_notin_uses_index(tmp_path):
    """anchor.enqueue_anchor_series's supersede DELETE (anchor.py:154-158).
    No agent_version_a equality (NOT IN a subquery), so ix_games_crown_pair's
    2nd column can't bind -- verify SQLite still SEARCHes via status (or
    purpose) rather than falling back to SCAN games. `SCAN anchor_checks` is
    expected (small table, the NOT-IN subquery side) and not asserted
    against."""
    conn = _db(tmp_path)
    sql = (
        "DELETE FROM games WHERE purpose='anchor' AND status='pending' "
        "AND agent_version_a NOT IN "
        "(SELECT version FROM anchor_checks WHERE verdict='pending')"
    )
    plan = _eqp(conn, sql)
    assert "SCAN games" not in plan, plan
    assert "games USING INDEX" in plan or "games USING COVERING INDEX" in plan, plan


def test_pairgate_supersede_delete_neq_uses_index(tmp_path):
    """pairgate.enqueue_pair_gate's supersede DELETE (pairgate.py:229-231).
    `agent_version_a != ?` -- same no-equality-bind concern as the anchor
    supersede DELETE above."""
    conn = _db(tmp_path)
    sql = (
        "DELETE FROM games WHERE purpose='pair_gate' AND "
        "status='pending' AND agent_version_a != ?"
    )
    plan = _eqp(conn, sql, ("v0.1",))
    assert "SCAN games" not in plan, plan
    assert "games USING INDEX" in plan or "games USING COVERING INDEX" in plan, plan


# --- Group F: covering-index-only reads over the crown purpose -----------

def test_crown_pending_pairs_distinct_uses_covering_index(tmp_path):
    """loop.enqueue_crown_round_robin's stale-pair prune source
    (loop.py:756-759). All 3 selected/filtered columns
    (purpose, agent_version_a, agent_version_b) are in ix_games_crown_pair,
    so this can be satisfied without ever touching the games table."""
    conn = _db(tmp_path)
    sql = (
        "SELECT DISTINCT agent_version_a, agent_version_b FROM games "
        "WHERE purpose = 'crown' AND status = 'pending'"
    )
    plan = _eqp(conn, sql)
    assert "ix_games_crown_pair" in plan, plan
    assert "SCAN games" not in plan, plan


def test_crown_pair_counts_groupby_uses_covering_index(tmp_path):
    """loop.enqueue_crown_round_robin's ONE-indexed-aggregate replacement
    for the old C(K,2) per-pair COUNT scans (loop.py:785-789) -- this IS the
    56s-lock-hold fix itself; `test_factory_loop_crown.py`'s existing
    `test_crown_pair_count_query_uses_covering_index` covers a narrower
    3-equality-bound COUNT variant, this covers the actual GROUP BY shape
    shipped in `enqueue_crown_round_robin`."""
    conn = _db(tmp_path)
    sql = (
        "SELECT agent_version_a, agent_version_b, COUNT(*) AS cnt "
        "FROM games WHERE purpose = 'crown' "
        "GROUP BY agent_version_a, agent_version_b"
    )
    plan = _eqp(conn, sql)
    assert "COVERING INDEX ix_games_crown_pair" in plan, plan
    assert "SCAN games" not in plan, plan


# --- Group G: claim_next_game -- the single hottest query in the factory -

def test_claim_next_game_uses_index(tmp_path):
    """deckdb.claim_next_game's claim SELECT (deckdb.py:382-384) -- called
    once per game claimed by every runner worker. `ix_games_claim(status,
    priority)` satisfies the equality + the leading ORDER BY term directly;
    only the final `id` tie-break needs a small temp b-tree over the
    already-narrowed row set (expected, not a SCAN)."""
    conn = _db(tmp_path)
    sql = "SELECT * FROM games WHERE status='pending' ORDER BY priority DESC, id LIMIT 1"
    plan = _eqp(conn, sql)
    assert "ix_games_claim" in plan, plan
    assert "SCAN games" not in plan, plan


# --- Group H: reclaim_orphaned_games (verified benign in diagnosis,
# <=0.01s -- still a regression guard) ------------------------------------

def test_reclaim_orphaned_games_claimed_scan_uses_index(tmp_path):
    """loop_scheduler.reclaim_orphaned_games (loop_scheduler.py:199-203).
    Brief: verified benign in diagnosis (<=0.01s). `SCAN game_recovery` is
    expected (small table, NOT-IN subquery side) and not asserted against."""
    conn = _db(tmp_path)
    sql = (
        "SELECT id, worker_pid, claimed_at FROM games "
        "WHERE status = 'claimed' AND claimed_at IS NOT NULL "
        "AND id NOT IN (SELECT game_id FROM game_recovery WHERE dead = 1)"
    )
    plan = _eqp(conn, sql)
    assert "ix_games_claim" in plan, plan
    assert "SCAN games" not in plan, plan


# --- Group I: concepts/coverage/decks join queries ------------------------

def test_census_candidates_query_uses_indexes(tmp_path):
    """census._CANDIDATES_QUERY (schedule_screening_games' lock-held field
    selection) -- imported directly rather than embedded, so this guard
    cannot drift from the source. Spec §2: the composition tie-break must
    stay index-backed -- no SCAN of concepts/coverage/decks."""
    from ptcg.factory import census

    conn = _db(tmp_path)
    plan = _eqp(conn, census._CANDIDATES_QUERY, (15, 200))
    assert "ix_concepts_status" in plan, plan
    assert ("ix_decks_concept" in plan) or ("ix_decks_concept_comp" in plan), plan
    assert "SCAN " not in plan, plan


def test_census_pending_per_concept_query_uses_indexes(tmp_path):
    """census._PENDING_PER_CONCEPT_QUERY (census.py:97-106), the in-flight
    read inside `schedule_screening_games`'s lock. Plan-cited suspect
    (touches `games` via a UNION ALL, then `decks`). `SCAN g` (the UNION
    ALL's own derived-table alias, not a real table) is expected and not
    asserted against."""
    conn = _db(tmp_path)
    sql = (
        "SELECT d.concept_id AS concept_id, COUNT(*) AS n FROM ("
        "SELECT deck_a_id AS did FROM games "
        "WHERE status IN ('pending','claimed') AND purpose='screening' "
        "UNION ALL "
        "SELECT deck_b_id AS did FROM games "
        "WHERE status IN ('pending','claimed') AND purpose='screening'"
        ") g JOIN decks d ON d.id = g.did "
        "GROUP BY d.concept_id"
    )
    plan = _eqp(conn, sql)
    assert "ix_games_crown_pair" in plan, plan
    assert "SCAN games" not in plan, plan
    assert "SCAN decks" not in plan, plan


def test_loop_top_field_query_uses_indexes(tmp_path):
    """loop._TOP_FIELD_QUERY (loop.py:195-204), the MATCH field-selection
    read inside `enqueue_match_games`'s lock. Same shape as
    `census._CANDIDATES_QUERY` (rated `active` field, canonical-deck join),
    without the `games_played` deficit filter."""
    conn = _db(tmp_path)
    sql = (
        "SELECT c.id AS concept_id, d.id AS deck_id, co.rating AS rating "
        "FROM concepts c "
        "JOIN coverage co ON co.concept_id = c.id "
        "JOIN decks d ON d.concept_id = c.id "
        "AND d.shell_variant = (SELECT MIN(shell_variant) FROM decks WHERE concept_id = c.id) "
        "WHERE c.status = 'active' AND co.rating IS NOT NULL "
        "ORDER BY co.rating DESC, c.id ASC "
        "LIMIT ?"
    )
    plan = _eqp(conn, sql, (30,))
    assert "ix_concepts_status" in plan, plan
    assert "ix_decks_concept" in plan, plan
    assert "SCAN concepts" not in plan, plan
    assert "SCAN coverage" not in plan, plan
    assert "SCAN decks" not in plan, plan


def test_ui_actions_bulk_decision_search_uses_index(tmp_path):
    """ui_actions.apply_bulk_decision's match-list SELECT
    (ui_actions.py:199-203) -- the query that decides which concepts a
    bulk cull/restore touches, inside its own `_write` lock."""
    conn = _db(tmp_path)
    sql = (
        "SELECT id FROM concepts WHERE status=? AND (id LIKE ? OR cores LIKE ?) "
        "ORDER BY id"
    )
    plan = _eqp(conn, sql, ("untested", "%x%", "%x%"))
    assert "ix_concepts_status" in plan, plan
    assert "SCAN concepts" not in plan, plan


def test_census_promotable_singles_query_uses_indexes(tmp_path):
    """census._PROMOTABLE_SINGLES_QUERY (census.py:448-453), inside
    `promote_proven_singles`'s lock. Brief: verified benign in diagnosis
    (0.412s indexed) -- still a regression guard."""
    conn = _db(tmp_path)
    sql = (
        "SELECT c.id AS concept_id "
        "FROM concepts c JOIN coverage co ON co.concept_id = c.id "
        "WHERE c.status = 'untested' AND json_array_length(c.cores) = 1 "
        "AND co.games_played >= ? AND co.rating IS NOT NULL"
    )
    plan = _eqp(conn, sql, (15,))
    assert "ix_concepts_status" in plan, plan
    assert "SCAN concepts" not in plan, plan
    assert "SCAN coverage" not in plan, plan
