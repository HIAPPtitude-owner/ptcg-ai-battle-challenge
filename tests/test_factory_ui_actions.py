"""Read-side query tests for ui_actions (ui-remove-any-deck T2)."""
from __future__ import annotations

import json

import pytest

from ptcg.factory import deckdb, ui_actions

_WATER = 3


def _seed_concept(c, concept_id, deck_id=None, *, status="untested", cores=None,
                  rating=None, games_played=None):
    c.execute("INSERT INTO concepts(id,cores,status) VALUES(?,?,?)",
              (concept_id, json.dumps(cores if cores is not None else [concept_id]), status))
    if deck_id is not None:
        c.execute("INSERT INTO decks(id,concept_id,cards,shell_variant) VALUES(?,?,?,0)",
                  (deck_id, concept_id, json.dumps([_WATER] * 3)))
    if rating is not None or games_played is not None:
        c.execute("INSERT INTO coverage(concept_id,games_played,rating) VALUES(?,?,?)",
                  (concept_id, games_played or 0, rating))


def _db(tmp_path):
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    return conn


# --- search_concepts ---------------------------------------------------

@pytest.mark.parametrize("provenance,concept_id,cores", [
    ("census-era", "c-abc123", ["Abomasnow"]),          # cores = card name list
    ("reseed-era", "reseed-mut-ff00", None),            # cores = [id] (default)
])
def test_search_matches_cores_and_id(tmp_path, provenance, concept_id, cores):
    conn = _db(tmp_path)
    deckdb._write(conn, lambda c: _seed_concept(c, concept_id, cores=cores))
    # match by id fragment
    rows, total = ui_actions.search_concepts(conn, concept_id[3:8], tab="untested")
    assert total == 1 and rows[0]["concept_id"] == concept_id
    # census-era: match by core card name fragment too
    if provenance == "census-era":
        rows, total = ui_actions.search_concepts(conn, "bomasno", tab="untested")
        assert total == 1 and rows[0]["concept_id"] == concept_id


def test_search_tab_filters_status(tmp_path):
    conn = _db(tmp_path)
    def _seed(c):
        _seed_concept(c, "x-untested", status="untested")
        _seed_concept(c, "x-culled", status="culled")
        _seed_concept(c, "x-active", status="active")
        _seed_concept(c, "x-unbuildable", status="unbuildable")
    deckdb._write(conn, _seed)
    assert ui_actions.search_concepts(conn, "x-", tab="untested")[1] == 1
    assert ui_actions.search_concepts(conn, "x-", tab="culled")[1] == 1
    assert ui_actions.search_concepts(conn, "x-", tab="active")[1] == 1
    assert ui_actions.search_concepts(conn, "x-", tab="all")[1] == 4  # incl. unbuildable
    with pytest.raises(ValueError):
        ui_actions.search_concepts(conn, "x-", tab="bogus")


def test_search_caps_rows_but_reports_total(tmp_path):
    conn = _db(tmp_path)
    def _seed(c):
        for i in range(205):
            _seed_concept(c, f"cap-{i:03d}")
    deckdb._write(conn, _seed)
    rows, total = ui_actions.search_concepts(conn, "cap-", tab="untested")
    assert len(rows) == 200 and total == 205


# --- pool_decks / anchor / champion ------------------------------------

def test_pool_lists_all_active_including_unscreened(tmp_path):
    conn = _db(tmp_path)
    def _seed(c):
        _seed_concept(c, "p-rated", "dp-rated", status="active", rating=2.0, games_played=30)
        _seed_concept(c, "p-fresh", "dp-fresh", status="active")  # no coverage row
        _seed_concept(c, "p-culled", "dp-culled", status="culled", rating=9.9, games_played=30)
    deckdb._write(conn, _seed)
    rows = ui_actions.pool_decks(conn)
    assert [r["concept_id"] for r in rows] == ["p-rated", "p-fresh"]  # DESC, NULL last
    assert rows[1]["rating"] is None


def test_pool_uses_canonical_shell_variant(tmp_path):
    conn = _db(tmp_path)
    def _seed(c):
        _seed_concept(c, "p0", "dp0-sv0", status="active", rating=1.0, games_played=20)
        c.execute("INSERT INTO decks(id,concept_id,cards,shell_variant) VALUES('dp0-sv1','p0',?,1)",
                  (json.dumps([_WATER]),))
    deckdb._write(conn, _seed)
    rows = ui_actions.pool_decks(conn)
    assert [r["deck_id"] for r in rows] == ["dp0-sv0"]


def test_anchor_and_champion(tmp_path):
    conn = _db(tmp_path)
    def _seed(c):
        _seed_concept(c, "the-anchor", "d-anchor", status="finalist")
        _seed_concept(c, "champ-concept", "d-champ", status="active", rating=3.0, games_played=40)
        c.execute("INSERT INTO baselines(version, deck_id, crowned_at) "
                  "VALUES('v0.13','d-champ','2026-08-05T00:00:00+00:00')")
        c.execute("INSERT INTO meta(key,value) VALUES('baseline_version','v0.13')")
    deckdb._write(conn, _seed)
    # MUST-FIX-5 fix (2026-08-05): narrow via an intermediate variable +
    # assert-not-None before subscripting -- pyright reportOptionalSubscript
    # on the direct `ui_actions.anchor_concept(conn)["concept_id"]` chain.
    anchor = ui_actions.anchor_concept(conn)
    assert anchor is not None
    assert anchor["concept_id"] == "the-anchor"
    assert ui_actions.champion_concept_id(conn) == "champ-concept"


def test_anchor_and_champion_absent(tmp_path):
    conn = _db(tmp_path)
    assert ui_actions.anchor_concept(conn) is None
    assert ui_actions.champion_concept_id(conn) is None


# --- apply_concept_decision --------------------------------------------

def test_remove_untested_and_active_records_prior_status(tmp_path):
    conn = _db(tmp_path)
    def _seed(c):
        _seed_concept(c, "r-untested", status="untested")
        _seed_concept(c, "r-active", "dr-active", status="active")
    deckdb._write(conn, _seed)
    assert ui_actions.apply_concept_decision(conn, "r-untested", "remove") == "culled"
    assert ui_actions.apply_concept_decision(conn, "r-active", "remove") == "culled"
    rows = conn.execute(
        "SELECT concept_id, action, prior_status, deck_id FROM decisions ORDER BY id"
    ).fetchall()
    assert [(r["concept_id"], r["action"], r["prior_status"], r["deck_id"]) for r in rows] == [
        ("r-untested", "remove", "untested", None),
        ("r-active", "remove", "active", None),
    ]
    assert conn.execute("SELECT reason FROM concepts WHERE id='r-untested'").fetchone()[0].startswith("ui-cull:")


def test_remove_finalist_refused(tmp_path):
    conn = _db(tmp_path)
    deckdb._write(conn, lambda c: _seed_concept(c, "the-anchor", status="finalist"))
    with pytest.raises(ui_actions.FinalistProtectedError):
        ui_actions.apply_concept_decision(conn, "the-anchor", "remove")
    assert conn.execute("SELECT status FROM concepts WHERE id='the-anchor'").fetchone()[0] == "finalist"
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0


def test_remove_invalid_targets(tmp_path):
    conn = _db(tmp_path)
    deckdb._write(conn, lambda c: _seed_concept(c, "already-culled", status="culled"))
    with pytest.raises(ValueError):
        ui_actions.apply_concept_decision(conn, "already-culled", "remove")
    with pytest.raises(ValueError):
        ui_actions.apply_concept_decision(conn, "no-such-concept", "remove")
    with pytest.raises(ValueError):
        ui_actions.apply_concept_decision(conn, "already-culled", "promote")  # bad action


@pytest.mark.parametrize("prior,expected", [
    ("active", "active"),      # UI-culled-while-active -> straight back to active
    ("untested", "untested"),  # UI-culled-while-untested -> re-screen
])
def test_restore_two_tier_via_audit(tmp_path, prior, expected):
    conn = _db(tmp_path)
    deckdb._write(conn, lambda c: _seed_concept(c, "rt", status=prior))
    ui_actions.apply_concept_decision(conn, "rt", "remove")
    assert ui_actions.apply_concept_decision(conn, "rt", "restore") == expected
    assert conn.execute("SELECT status FROM concepts WHERE id='rt'").fetchone()[0] == expected


def test_restore_without_audit_goes_untested(tmp_path):
    """Reseed-culled / historical rows have no UI decision -> untested tier."""
    conn = _db(tmp_path)
    deckdb._write(conn, lambda c: _seed_concept(c, "legacy-culled", status="culled"))
    assert ui_actions.apply_concept_decision(conn, "legacy-culled", "restore") == "untested"


def test_restore_null_prior_status_goes_untested(tmp_path):
    """A v1-era 'remove' decision row (prior_status NULL) -> untested tier."""
    conn = _db(tmp_path)
    def _seed(c):
        _seed_concept(c, "v1-culled", "dv1", status="culled")
        c.execute("INSERT INTO decisions(deck_id, action, actor, timestamp) "
                  "VALUES('dv1','remove','brad','2026-08-01T00:00:00+00:00')")
    deckdb._write(conn, _seed)
    assert ui_actions.apply_concept_decision(conn, "v1-culled", "restore") == "untested"


def test_restore_requires_culled(tmp_path):
    conn = _db(tmp_path)
    deckdb._write(conn, lambda c: _seed_concept(c, "still-active", status="active"))
    with pytest.raises(ValueError):
        ui_actions.apply_concept_decision(conn, "still-active", "restore")


# --- restore-mitigation coverage reset (CRITICAL, 2026-08-05) ----------
#
# CONFIRMED BUG: without this reset, a concept restored to the untested
# tier keeps its STALE coverage row. census._CANDIDATES_QUERY only
# re-schedules concepts with games_played < floor, and
# census.promote_proven_singles promotes on games_played>=floor AND
# rating IS NOT NULL with NO rating bar -- so a restored junk concept
# (e.g. wr~0.03, already at games_played>=floor from before it was
# culled) insta-promotes back to 'active' with ZERO new games played.


def test_restore_untested_tier_zeroes_coverage(tmp_path):
    conn = _db(tmp_path)
    deckdb._write(
        conn,
        lambda c: _seed_concept(c, "junk", "d-junk", status="culled", rating=0.03,
                                 games_played=60),
    )
    assert ui_actions.apply_concept_decision(conn, "junk", "restore") == "untested"
    row = conn.execute(
        "SELECT games_played, distinct_opponents, rating FROM coverage WHERE concept_id='junk'"
    ).fetchone()
    assert (row["games_played"], row["distinct_opponents"], row["rating"]) == (0, 0, None)


def test_restore_active_tier_leaves_coverage_intact(tmp_path):
    conn = _db(tmp_path)
    deckdb._write(
        conn,
        lambda c: _seed_concept(c, "keep", "d-keep", status="active", rating=1.5,
                                 games_played=40),
    )
    ui_actions.apply_concept_decision(conn, "keep", "remove")  # prior_status='active'
    assert ui_actions.apply_concept_decision(conn, "keep", "restore") == "active"
    row = conn.execute(
        "SELECT games_played, rating FROM coverage WHERE concept_id='keep'"
    ).fetchone()
    assert (row["games_played"], row["rating"]) == (40, 1.5)


def test_restore_untested_tier_no_coverage_row_is_noop(tmp_path):
    """A concept with no coverage row at all (rowcount 0 on the reset
    UPDATE) must restore cleanly, not raise."""
    conn = _db(tmp_path)
    deckdb._write(conn, lambda c: _seed_concept(c, "no-cov", status="culled"))
    assert ui_actions.apply_concept_decision(conn, "no-cov", "restore") == "untested"
    assert conn.execute(
        "SELECT COUNT(*) FROM coverage WHERE concept_id='no-cov'"
    ).fetchone()[0] == 0


def test_restore_untested_tier_reopens_census_and_kills_instapromote(tmp_path):
    """End-to-end reviewer receipt: import the REAL census functions/queries
    (not restated) to prove a restored wr~0.03 concept (a) becomes visible
    again to census's own candidate-selection query, and (b) is NOT
    promotable via census.promote_proven_singles with zero new games."""
    conn = _db(tmp_path)
    deckdb._write(
        conn,
        lambda c: _seed_concept(c, "junk2", "d-junk2", status="culled", rating=0.03,
                                 games_played=census.SCREENING_FLOOR + 45),
    )
    assert ui_actions.apply_concept_decision(conn, "junk2", "restore") == "untested"

    candidates = conn.execute(
        census._CANDIDATES_QUERY, (census.SCREENING_FLOOR, 200)
    ).fetchall()
    assert "junk2" in {r["concept_id"] for r in candidates}

    promoted = census.promote_proven_singles(conn)
    assert promoted == 0
    assert conn.execute(
        "SELECT status FROM concepts WHERE id='junk2'"
    ).fetchone()[0] == "untested"


# --- apply_bulk_decision -----------------------------------------------

def test_bulk_remove_culls_all_matching_untested(tmp_path):
    conn = _db(tmp_path)
    def _seed(c):
        for i in range(3):
            _seed_concept(c, f"abom-{i}", cores=["Abomasnow"])
        _seed_concept(c, "abom-active", cores=["Abomasnow"], status="active")  # wrong tab
        _seed_concept(c, "pika-0", cores=["Pikachu"])                          # wrong query
    deckdb._write(conn, _seed)
    n = ui_actions.apply_bulk_decision(conn, "Abomasnow", "untested", "bulk-remove")
    assert n == 3
    assert conn.execute("SELECT COUNT(*) FROM concepts WHERE status='culled'").fetchone()[0] == 3
    assert conn.execute("SELECT status FROM concepts WHERE id='abom-active'").fetchone()[0] == "active"
    assert conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE action='bulk-remove' AND prior_status='untested'"
    ).fetchone()[0] == 3


def test_bulk_restore_reverses_bulk_remove(tmp_path):
    conn = _db(tmp_path)
    def _seed(c):
        for i in range(2):
            _seed_concept(c, f"abom-{i}", cores=["Abomasnow"])
    deckdb._write(conn, _seed)
    ui_actions.apply_bulk_decision(conn, "Abomasnow", "untested", "bulk-remove")
    n = ui_actions.apply_bulk_decision(conn, "Abomasnow", "culled", "bulk-restore")
    assert n == 2
    assert conn.execute("SELECT COUNT(*) FROM concepts WHERE status='untested'").fetchone()[0] == 2


def test_bulk_scoping_rules(tmp_path):
    conn = _db(tmp_path)
    deckdb._write(conn, lambda c: _seed_concept(c, "abom-0", cores=["Abomasnow"]))
    with pytest.raises(ValueError):
        ui_actions.apply_bulk_decision(conn, "Abomasnow", "culled", "bulk-remove")   # wrong tab
    with pytest.raises(ValueError):
        ui_actions.apply_bulk_decision(conn, "Abomasnow", "untested", "bulk-restore")
    with pytest.raises(ValueError):
        ui_actions.apply_bulk_decision(conn, "   ", "untested", "bulk-remove")       # empty q
    with pytest.raises(ValueError):
        ui_actions.apply_bulk_decision(conn, "Abomasnow", "all", "bulk-remove")      # no bulk on all


def test_bulk_count_is_recomputed_inside_txn_not_preview(tmp_path):
    """The count returned reflects rows matched AT EXECUTION TIME — a concept
    added after any 'preview' count is included; one flipped away is not."""
    conn = _db(tmp_path)
    deckdb._write(conn, lambda c: _seed_concept(c, "abom-0", cores=["Abomasnow"]))
    _preview_total = ui_actions.search_concepts(conn, "Abomasnow", "untested")[1]
    assert _preview_total == 1
    deckdb._write(conn, lambda c: _seed_concept(c, "abom-late", cores=["Abomasnow"]))
    n = ui_actions.apply_bulk_decision(conn, "Abomasnow", "untested", "bulk-remove")
    assert n == 2  # includes the post-preview row


def test_bulk_zero_matches_is_noop_zero(tmp_path):
    conn = _db(tmp_path)
    assert ui_actions.apply_bulk_decision(conn, "NoSuchCore", "untested", "bulk-remove") == 0
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0


# --- interleaved receipt: bulk cull vs census promotion (INTERLEAVED) ---

import itertools
import threading

from ptcg.factory import census
from tests.fixtures.race import race_two


def test_bulk_cull_races_census_promotion(tmp_path):
    """Two serialized outcomes are legal for a concept that is simultaneously
    a bulk-cull match ('Abomasnow', untested) and a census-promotion
    candidate (untested, single core, games_played>=SCREENING_FLOOR, decisive
    rating). Either serialized outcome is legal; torn states rejected.

    Statement-count arithmetic hand-verified against the real code (not
    transcribed from the plan) via a throwaway probe script run against both
    orderings directly (no threads): `_PROMOTABLE_SINGLES_QUERY` itself
    filters `status='untested'` inside promote_proven_singles's own
    BEGIN IMMEDIATE transaction, so once cull commits first, promote's SELECT
    matches zero rows and its guarded UPDATE never executes at all -- giving
    update_count=1 in BOTH orderings, not 2 in the cull-first case as a naive
    reading of the UPDATE's own `AND status='untested'` guard might suggest.
    Confirmed: cull-first -> (bulk_n=1, promoted=0, status=culled,
    n_decisions=1, update_count=1); promote-first -> (bulk_n=0, promoted=1,
    status=active, n_decisions=0, update_count=1).
    """
    db_path = tmp_path / "race.db"
    conn = deckdb.connect(db_path)
    deckdb.init_db(conn)

    def _seed(c):
        _seed_concept(c, "abom-race", "d-race", cores=["Abomasnow"],
                      status="untested", rating=1.0,
                      games_played=census.SCREENING_FLOOR)
    deckdb._write(conn, _seed)
    conn.close()

    role = itertools.count()
    role_lock = threading.Lock()

    def _actor(c):
        with role_lock:
            me = next(role)
        if me == 0:
            return ("bulk", ui_actions.apply_bulk_decision(c, "Abomasnow", "untested", "bulk-remove"))
        return ("promote", census.promote_proven_singles(c))

    results, update_count = race_two(
        db_path, _actor, lambda sql: sql.startswith("UPDATE CONCEPTS")
    )

    check = deckdb.connect(db_path)
    status = check.execute("SELECT status FROM concepts WHERE id='abom-race'").fetchone()[0]
    n_decisions = check.execute(
        "SELECT COUNT(*) FROM decisions WHERE concept_id='abom-race'"
    ).fetchone()[0]
    bulk_n = dict(results)["bulk"]

    if status == "culled":     # bulk won the write lock first
        assert (bulk_n, n_decisions, update_count) == (1, 1, 1)
    elif status == "active":   # promotion won; bulk matched nothing
        assert (bulk_n, n_decisions, update_count) == (0, 0, 1)
    else:
        raise AssertionError(f"torn state: status={status!r}, decisions={n_decisions}")


# --- ix_decisions_concept: lock-hold budget + query-plan guard (Pass-2) ----
#
# FIX FINDING 1 (Critical, O(N^2) lock starvation, 2026-08-05):
# `_restore_target`'s `SELECT ... FROM decisions WHERE concept_id=?` runs
# once PER RESTORED CONCEPT inside `apply_bulk_decision`'s single
# BEGIN IMMEDIATE transaction, which also INSERTs into `decisions` per row.
# Without an index on `decisions(concept_id)` each SELECT is a full SCAN of
# the whole table. Solo (no lock contention), N=20,000 restored concepts
# measured 61.13s pre-fix vs 1.19s post-fix (see the fix-wave report for
# the throwaway repro); with a concurrent writer actually contending, the
# writer itself hits `sqlite3.OperationalError: database is locked` once
# the 30s `busy_timeout` expires. Live culled pool: 95,895 concepts.

import time  # noqa: E402 - grouped with this section's tests, not module-top


def test_decisions_concept_index_exists_and_is_used(tmp_path):
    """Mirrors `test_decks_concept_index_exists_and_is_used`
    (test_factory_deckdb.py): asserting the PLAN uses the index (not just
    that it exists) is what actually pins the behaviour. Query shape is
    `_restore_target`'s real SELECT, not a simplified stand-in."""
    conn = _db(tmp_path)
    idx = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ix_decisions_concept" in idx

    plan = " ".join(
        row[3] for row in conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT prior_status FROM decisions "
            "WHERE concept_id='c1' AND action IN ('remove','bulk-remove') "
            "ORDER BY id DESC LIMIT 1"
        )
    )
    assert "ix_decisions_concept" in plan and "SCAN decisions" not in plan


def test_bulk_restore_does_not_starve_concurrent_writer_at_scale(tmp_path):
    """Seed N culled concepts each with a real decisions audit row (so
    `_restore_target`'s SELECT has real rows to scan), run the bulk restore
    on a background thread, and assert a concurrent small write from a
    SEPARATE connection completes without raising (busy_timeout would raise
    `sqlite3.OperationalError: database is locked` on starvation) and within
    a sane wall-clock bound. This is the receipt for Finding 1's fix; see
    the section docstring above for the pre-fix measurement."""
    db_path = tmp_path / "scale.db"
    conn = deckdb.connect(db_path)
    deckdb.init_db(conn)

    n = 20_000
    concept_rows = [
        (f"scale-{i:06d}", json.dumps([f"scale-{i:06d}"]), "culled") for i in range(n)
    ]
    decision_rows = [
        (f"scale-{i:06d}", "bulk-remove", "brad", "2026-08-01T00:00:00+00:00", "untested")
        for i in range(n)
    ]

    def _seed(c):
        c.executemany("INSERT INTO concepts(id,cores,status) VALUES(?,?,?)", concept_rows)
        c.executemany(
            "INSERT INTO decisions(concept_id, action, actor, timestamp, prior_status) "
            "VALUES(?,?,?,?,?)",
            decision_rows,
        )

    deckdb._write(conn, _seed)
    conn.close()

    outcome: dict[str, float] = {}

    def _run_bulk_restore():
        c = deckdb.connect(db_path)
        t0 = time.monotonic()
        outcome["n"] = ui_actions.apply_bulk_decision(c, "scale-", "culled", "bulk-restore")
        outcome["elapsed"] = time.monotonic() - t0
        c.close()

    bulk_thread = threading.Thread(target=_run_bulk_restore)
    bulk_thread.start()
    time.sleep(0.05)  # let the bulk txn's BEGIN IMMEDIATE win the write lock first

    writer = deckdb.connect(db_path)
    t_writer = time.monotonic()
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO meta(key,value) VALUES('probe','1')")
    writer.execute("COMMIT")
    writer_elapsed = time.monotonic() - t_writer
    writer.close()

    bulk_thread.join(timeout=60)
    assert not bulk_thread.is_alive(), "bulk-restore thread did not finish within 60s"
    assert outcome["n"] == n
    assert writer_elapsed < 30.0, (
        f"concurrent writer took {writer_elapsed:.2f}s -- past the 30s busy_timeout, "
        "meaning the bulk restore was starving it (Finding 1 regression)"
    )
    assert outcome["elapsed"] < 30.0, f"bulk restore itself took {outcome['elapsed']:.2f}s"


# --- canonical_decks (pool-pruning slice) --------------------------------


def test_canonical_decks_returns_min_shell_variant_per_concept(tmp_path):
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)

    def _seed(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cA','[]','active')")
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cB','[]','untested')")
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cNoDeck','[]','untested')")
        c.execute("INSERT INTO decks(id,concept_id,cards,shell_variant) "
                  "VALUES('dA1','cA','[1, 2]',1)")
        c.execute("INSERT INTO decks(id,concept_id,cards,shell_variant) "
                  "VALUES('dA0','cA','[3, 4]',0)")   # canonical: min shell_variant
        c.execute("INSERT INTO decks(id,concept_id,cards,shell_variant) "
                  "VALUES('dB0','cB','[5]',0)")

    deckdb._write(db, _seed)

    out = ui_actions.canonical_decks(db, ["cA", "cB", "cNoDeck", "cMissing"])
    assert out == {"cA": [3, 4], "cB": [5]}
    assert ui_actions.canonical_decks(db, []) == {}


def test_canonical_decks_query_uses_concept_index(tmp_path):
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)
    plan_rows = db.execute(
        "EXPLAIN QUERY PLAN SELECT concept_id, cards, shell_variant FROM decks "
        "WHERE concept_id IN (?,?) ORDER BY concept_id ASC, shell_variant ASC",
        ("a", "b"),
    ).fetchall()
    detail = " ".join(str(tuple(r)) for r in plan_rows)
    assert "ix_decks_concept" in detail  # indexed lookup, never a table SCAN
