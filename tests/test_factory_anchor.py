"""Tests for the anchor-check stage (submission strength gate).

Spec: docs/superpowers/specs/2026-08-01-submission-strength-gate-design.md
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from ptcg.decks.validate import validate_deck
from ptcg.factory import anchor, census, deckdb
from tests.fixtures.race import race_two


def _connect(tmp_path: Path) -> sqlite3.Connection:
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    return conn


def _legacy_connect(tmp_path: Path) -> sqlite3.Connection:
    """A DB shaped like PRODUCTION before this slice: every table EXCEPT
    anchor_checks (the live tournament.db predates the DDL addition and
    nothing re-runs init_db on it)."""
    conn = deckdb.connect(tmp_path / "legacy.db")

    def _apply(c: sqlite3.Connection) -> None:
        for ddl in deckdb._DDL_STATEMENTS:
            if "anchor_checks" not in ddl:
                c.execute(ddl)

    deckdb._write(conn, _apply)
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def test_constants_exact_values():
    assert anchor.ANCHOR_VERSION == "anchor-heuristic-v0"
    assert anchor.ANCHOR_GAMES == 200
    assert anchor.ANCHOR_BAR == 0.60


def test_init_db_creates_anchor_checks_on_virgin_db(tmp_path):
    conn = _connect(tmp_path)
    assert "anchor_checks" in _table_names(conn)


def test_ensure_schema_upgrades_legacy_db_preserving_data(tmp_path):
    conn = _legacy_connect(tmp_path)
    assert "anchor_checks" not in _table_names(conn)
    # pre-existing data that must survive the upgrade
    def _seed(c):
        c.execute("INSERT INTO concepts(id, cores) VALUES('cX', '[]')")
    deckdb._write(conn, _seed)
    anchor._ensure_schema(conn)
    assert "anchor_checks" in _table_names(conn)
    assert conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 1
    anchor._ensure_schema(conn)  # idempotent


def test_ensure_anchor_deck_registers_concept_deck_no_coverage(tmp_path):
    conn = _connect(tmp_path)
    anchor.ensure_anchor_deck(conn)
    concept = conn.execute(
        "SELECT status, reason FROM concepts WHERE id=?",
        (anchor.ANCHOR_CONCEPT_ID,)).fetchone()
    assert concept is not None
    assert concept["status"] == "finalist"  # never probe-eligible ('active' only)
    deck = conn.execute(
        "SELECT cards FROM decks WHERE id=?", (anchor.ANCHOR_DECK_ID,)).fetchone()
    assert deck is not None
    cards = json.loads(deck["cards"])
    assert len(cards) == 60 and all(isinstance(c, int) for c in cards)
    # Deliberately NO coverage row -- see ensure_anchor_deck's docstring:
    # a coverage row here would dip census.census_complete to False at
    # every go-live until ~15 anchor games play.
    cov = conn.execute(
        "SELECT games_played FROM coverage WHERE concept_id=?",
        (anchor.ANCHOR_CONCEPT_ID,)).fetchone()
    assert cov is None


def test_ensure_anchor_deck_uses_min8_winner_source(tmp_path):
    """Anchor swap (spec 2026-08-11): the anchor deck is DECOUPLED from the
    ladder identity -- it reads `ANCHOR_DECK_PATH` (the Task-7 mini-tournament
    winner, >= 8 basics), never `ptcg.agents.current.CURRENT_DECK_PATH`."""
    conn = _connect(tmp_path)
    anchor.ensure_anchor_deck(conn)
    expected = [
        int(line)
        for line in anchor.ANCHOR_DECK_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    deck = conn.execute(
        "SELECT cards FROM decks WHERE id=?", (anchor.ANCHOR_DECK_ID,)).fetchone()
    assert deck is not None
    cards = json.loads(deck["cards"])
    assert cards == expected
    assert validate_deck(cards) == []


def test_ensure_anchor_deck_refuses_noncompliant_csv(tmp_path, monkeypatch):
    """Refusal receipt: a monkeypatched ANCHOR_DECK_PATH pointing at a CSV
    that fails `validate_deck` (here, the frozen ladder deck -- 4 basics,
    below the >= 8 pool-rule floor) must raise, never register a
    non-compliant anchor deck."""
    conn = _connect(tmp_path)
    noncompliant = (
        anchor._REPO_ROOT / "src" / "ptcg" / "decks" / "candidates"
        / "mega-lucario-fighting.csv"
    )
    monkeypatch.setattr(anchor, "ANCHOR_DECK_PATH", noncompliant)
    with pytest.raises(RuntimeError, match="validate_deck"):
        anchor.ensure_anchor_deck(conn)


def test_ensure_anchor_deck_does_not_dip_census_complete(tmp_path):
    conn = _connect(tmp_path)

    def _seed(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cA', '[\"cA\"]', 'active')")
        c.execute(
            "INSERT INTO coverage(concept_id,games_played,distinct_opponents,rating) "
            "VALUES('cA', 15, 0, 0.5)"  # SCREENING_FLOOR=15 -- exactly at the bar
        )
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('d-cA','cA','[]')")

    deckdb._write(conn, _seed)
    assert census.census_complete(conn) is True  # true before the anchor registers

    anchor.ensure_anchor_deck(conn)

    assert census.census_complete(conn) is True  # still true -- no coverage row added


def test_ensure_anchor_deck_idempotent(tmp_path):
    conn = _connect(tmp_path)
    anchor.ensure_anchor_deck(conn)
    anchor.ensure_anchor_deck(conn)
    n = conn.execute("SELECT COUNT(*) FROM decks WHERE concept_id=?",
                     (anchor.ANCHOR_CONCEPT_ID,)).fetchone()[0]
    assert n == 1


def _seed_baseline(conn, version="v0.3", deck_id="dChamp"):
    """Founding-style baseline row + its deck, minimal shape (mirrors
    test_factory_subscheduler._seed_founding conventions)."""
    def _apply(c):
        c.execute("INSERT OR IGNORE INTO concepts(id, cores) VALUES('cChamp','[]')")
        c.execute(
            "INSERT OR IGNORE INTO decks(id, concept_id, cards, shell_variant) "
            "VALUES(?, 'cChamp', '[1]', 0)", (deck_id,))
        c.execute(
            "INSERT INTO baselines(version, offspring_id, deck_id, crowned_at) "
            "VALUES(?, NULL, ?, ?)", (version, deck_id, "2026-08-01T00:00:00+00:00"))
        c.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('baseline_version', ?)",
            (version,))
    deckdb._write(conn, _apply)


def _anchor_games(conn, version):
    return conn.execute(
        "SELECT * FROM games WHERE purpose='anchor' AND agent_version_a=?",
        (version,)).fetchall()


def _seed_founding(conn):
    """A founding `v0.1` baseline on `dBase`, hand-rolled to mirror
    `loop_state.set_founding_baseline`'s exact writes (baselines row +
    both meta keys: `baseline_version` and `founding_agent_config`) without
    importing `loop_state` into this test module."""
    def _apply(c):
        c.execute("INSERT OR IGNORE INTO concepts(id, cores) VALUES('cBase', '[]')")
        c.execute(
            "INSERT OR IGNORE INTO decks(id, concept_id, cards, shell_variant) "
            "VALUES('dBase', 'cBase', '[1]', 0)")
        c.execute(
            "INSERT INTO baselines(version, offspring_id, deck_id, crowned_at) "
            "VALUES('v0.1', NULL, 'dBase', ?)", ("2026-08-01T00:00:00+00:00",))
        c.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('baseline_version','v0.1')")
        c.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('founding_agent_config','{}')")
    deckdb._write(conn, _apply)


def _seed_elect(conn, oid="v0.1.1", deck_id="dElect"):
    """A champion-elect: a `survivor` offspring row plus a pending
    `anchor_checks` row keyed `version == offspring_id == oid` -- the exact
    shape `loop.resolve_crown` writes when it nominates (bf25910)."""
    def _apply(c):
        c.execute("INSERT OR IGNORE INTO concepts(id, cores) VALUES('cElect', '[]')")
        c.execute(
            "INSERT OR IGNORE INTO decks(id, concept_id, cards, shell_variant) "
            "VALUES(?, 'cElect', '[1]', 0)", (deck_id,))
        c.execute(
            "INSERT INTO offspring(id, parent_baseline_version, search_config_json, "
            "deck_id, status, created_at) VALUES(?, 'v0.1', '{}', ?, 'survivor', ?)",
            (oid, deck_id, "2026-08-01T00:00:00+00:00"))
        c.execute(
            "INSERT INTO anchor_checks(version, offspring_id, deck_id, "
            "games_planned, created_at) VALUES(?, ?, ?, ?, ?)",
            (oid, oid, deck_id, anchor.ANCHOR_GAMES, "2026-08-01T00:00:00+00:00"))
    deckdb._write(conn, _apply)


def test_enqueue_creates_check_row_and_full_series(tmp_path):
    conn = _connect(tmp_path)
    _seed_baseline(conn)
    n = anchor.enqueue_anchor_series(conn)
    assert n == anchor.ANCHOR_GAMES
    games = _anchor_games(conn, "v0.3")
    assert len(games) == anchor.ANCHOR_GAMES
    g = games[0]
    assert g["deck_a_id"] == "dChamp"
    assert g["deck_b_id"] == anchor.ANCHOR_DECK_ID
    assert g["agent_version_b"] == anchor.ANCHOR_VERSION
    assert g["priority"] == 1.0  # verdict blocks uploads -- jump the queue
    row = conn.execute(
        "SELECT verdict, games_planned FROM anchor_checks WHERE version='v0.3'"
    ).fetchone()
    assert row["verdict"] == "pending"
    assert row["games_planned"] == anchor.ANCHOR_GAMES


def test_enqueue_idempotent_and_tops_up_shortfall(tmp_path):
    conn = _connect(tmp_path)
    _seed_baseline(conn)
    anchor.enqueue_anchor_series(conn)
    assert anchor.enqueue_anchor_series(conn) == 0  # full series -> no-op
    # simulate a lost game row (e.g. operator cleanup): top-up refills
    def _drop_one(c):
        c.execute(
            "DELETE FROM games WHERE id = (SELECT id FROM games "
            "WHERE purpose='anchor' AND agent_version_a='v0.3' LIMIT 1)")
    deckdb._write(conn, _drop_one)
    assert anchor.enqueue_anchor_series(conn) == 1
    assert len(_anchor_games(conn, "v0.3")) == anchor.ANCHOR_GAMES


def test_enqueue_no_ops_without_baseline_or_after_verdict(tmp_path):
    conn = _connect(tmp_path)
    assert anchor.enqueue_anchor_series(conn) == 0  # no baseline founded
    _seed_baseline(conn)
    anchor.enqueue_anchor_series(conn)
    def _force_verdict(c):
        c.execute("UPDATE anchor_checks SET verdict='fail' WHERE version='v0.3'")
        c.execute("DELETE FROM games WHERE purpose='anchor'")
    deckdb._write(conn, _force_verdict)
    assert anchor.enqueue_anchor_series(conn) == 0  # resolved -> never re-enqueue


def test_enqueue_tops_up_elect_series(tmp_path):
    conn = _connect(tmp_path)
    _seed_founding(conn)
    _seed_elect(conn, oid="v0.1.1", deck_id="dElect")
    anchor.enqueue_anchor_series(conn)
    games = _anchor_games(conn, "v0.1.1")
    assert len(games) == anchor.ANCHOR_GAMES
    assert all(
        g["deck_a_id"] == "dElect" and g["deck_b_id"] == anchor.ANCHOR_DECK_ID
        for g in games
    )
    assert anchor.enqueue_anchor_series(conn) == 0  # both rows already full


def test_supersede_delete_spares_pending_checks(tmp_path):
    conn = _connect(tmp_path)
    _seed_founding(conn)
    anchor.enqueue_anchor_series(conn)  # backfill + top-up for v0.1
    _seed_elect(conn, oid="v0.1.1", deck_id="dElect")
    anchor.enqueue_anchor_series(conn)  # tops up the elect series too

    def _seed_stale(c):
        # a superseded version with NO anchor_checks row at all -- pure
        # queue waste; the pending one must be deleted, but a CLAIMED game
        # of the same stale version survives (runner finishes it harmlessly
        # -- games.status has no 'cancelled' value, deckdb.py:83)
        c.execute(
            "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, "
            "agent_version_b, purpose, priority, status) "
            "VALUES ('dStale', ?, 'v0.stale', ?, 'anchor', 1.0, 'pending')",
            (anchor.ANCHOR_DECK_ID, anchor.ANCHOR_VERSION))
        c.execute(
            "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, "
            "agent_version_b, purpose, priority, status) "
            "VALUES ('dStale', ?, 'v0.stale', ?, 'anchor', 1.0, 'claimed')",
            (anchor.ANCHOR_DECK_ID, anchor.ANCHOR_VERSION))
    deckdb._write(conn, _seed_stale)

    anchor.enqueue_anchor_series(conn)

    stale = _anchor_games(conn, "v0.stale")
    assert {g["status"] for g in stale} == {"claimed"}  # pending deleted, claimed survives
    assert len(_anchor_games(conn, "v0.1")) == anchor.ANCHOR_GAMES
    assert len(_anchor_games(conn, "v0.1.1")) == anchor.ANCHOR_GAMES


def test_enqueue_survives_concurrent_calls(tmp_path):
    """Interleaved-mutation (.claude/rules/single-actor-worker-tests.md):
    two racing enqueue calls must never double-fill the series."""
    conn_paths = tmp_path / "t.db"
    conn = deckdb.connect(conn_paths)
    deckdb.init_db(conn)
    _seed_baseline(conn)
    anchor.ensure_anchor_deck(conn)
    results = []

    def _race():
        c = deckdb.connect(conn_paths)
        results.append(anchor.enqueue_anchor_series(c))

    threads = [threading.Thread(target=_race) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(results) == anchor.ANCHOR_GAMES  # exactly one full series between them
    assert len(_anchor_games(conn, "v0.3")) == anchor.ANCHOR_GAMES


def _finish_games(conn, version, wins_for_champion, losses=0, draws=0):
    """Mark anchor games done with the given outcome mix (champion is side A:
    winner 0=champ win, 1=anchor win, 2=draw)."""
    def _apply(c):
        rows = c.execute(
            "SELECT id FROM games WHERE purpose='anchor' AND agent_version_a=? "
            "AND status='pending' ORDER BY id", (version,)).fetchall()
        outcomes = [0] * wins_for_champion + [1] * losses + [2] * draws
        assert len(outcomes) <= len(rows)
        for row, w in zip(rows, outcomes):
            c.execute(
                "UPDATE games SET status='done', winner=?, timestamp='t' "
                "WHERE id=?", (w, row["id"]))
    deckdb._write(conn, _apply)


def test_resolve_no_ops_before_series_complete(tmp_path):
    conn = _connect(tmp_path)
    _seed_baseline(conn)
    anchor.enqueue_anchor_series(conn)
    _finish_games(conn, "v0.3", wins_for_champion=100, losses=99)  # 199 done
    assert anchor.resolve_anchor_check(conn) is None
    verdict, done, planned, wr = anchor.anchor_status(conn, "v0.3")
    assert (verdict, done, planned, wr) == ("pending", 199, anchor.ANCHOR_GAMES, None)


def test_resolve_boundary_120_of_200_passes(tmp_path):
    conn = _connect(tmp_path)
    _seed_baseline(conn)
    anchor.enqueue_anchor_series(conn)
    _finish_games(conn, "v0.3", wins_for_champion=120, losses=80)
    assert anchor.resolve_anchor_check(conn) == "pass"
    verdict, done, planned, wr = anchor.anchor_status(conn, "v0.3")
    assert verdict == "pass" and done == 200 and wr == pytest.approx(0.60)


def test_resolve_fail_below_bar_draws_count_as_losses(tmp_path):
    conn = _connect(tmp_path)
    _seed_baseline(conn)
    anchor.enqueue_anchor_series(conn)
    # 119 wins + 1 draw + 80 losses: wr = 119/200 = 0.595 < 0.60 -> fail
    _finish_games(conn, "v0.3", wins_for_champion=119, losses=80, draws=1)
    assert anchor.resolve_anchor_check(conn) == "fail"
    verdict, _done, _planned, wr = anchor.anchor_status(conn, "v0.3")
    assert verdict == "fail" and wr == pytest.approx(0.595)


def test_resolve_settled_verdict_never_flips(tmp_path):
    conn = _connect(tmp_path)
    _seed_baseline(conn)
    anchor.enqueue_anchor_series(conn)
    _finish_games(conn, "v0.3", wins_for_champion=120, losses=80)
    assert anchor.resolve_anchor_check(conn) == "pass"
    assert anchor.resolve_anchor_check(conn) is None  # already settled -> no-op


def test_baseline_backfill_row_never_promotes(tmp_path):
    """A baseline-keyed pending row (offspring_id NULL, so never `elect`)
    resolving 'pass' records the verdict but performs no promotion side
    effects -- exactly as before this slice's promotion logic landed."""
    conn = _connect(tmp_path)
    _seed_baseline(conn, version="v0.3", deck_id="dChamp")
    anchor.enqueue_anchor_series(conn)
    _finish_games(conn, "v0.3", wins_for_champion=150, losses=50)
    assert anchor.resolve_anchor_check(conn) == "pass"
    assert conn.execute("SELECT COUNT(*) FROM baselines").fetchone()[0] == 1
    meta = conn.execute(
        "SELECT value FROM meta WHERE key='baseline_version'").fetchone()
    assert meta["value"] == "v0.3"
    row = conn.execute(
        "SELECT version, verdict FROM anchor_checks WHERE version='v0.3'").fetchone()
    assert row["version"] == "v0.3" and row["verdict"] == "pass"


def test_failed_anchor_verdict_does_not_advance_baseline(tmp_path):
    """The spec's mandatory ordering test: a failed elect verdict must NOT
    advance the baseline -- breeding keeps producing offspring against the
    incumbent."""
    conn = _connect(tmp_path)
    _seed_founding(conn)
    _seed_elect(conn, oid="v0.1.1", deck_id="dElect")
    anchor.enqueue_anchor_series(conn)
    # 80/200 = 0.40 < 0.60 -> fail
    _finish_games(conn, "v0.1.1", wins_for_champion=80, losses=120)
    assert anchor.resolve_anchor_check(conn) == "fail"

    meta = conn.execute(
        "SELECT value FROM meta WHERE key='baseline_version'").fetchone()
    assert meta["value"] == "v0.1"
    assert conn.execute("SELECT COUNT(*) FROM baselines").fetchone()[0] == 1
    offspring = conn.execute(
        "SELECT status FROM offspring WHERE id='v0.1.1'").fetchone()
    assert offspring["status"] == "trashed"
    row = conn.execute(
        "SELECT version, verdict FROM anchor_checks WHERE offspring_id='v0.1.1'"
    ).fetchone()
    assert row["version"] == "v0.1.1" and row["verdict"] == "fail"


def test_passed_anchor_verdict_crowns_in_same_call(tmp_path):
    conn = _connect(tmp_path)
    _seed_founding(conn)
    _seed_elect(conn, oid="v0.1.1", deck_id="dElect")
    anchor.enqueue_anchor_series(conn)
    # 120/200 = 0.60 -> pass
    _finish_games(conn, "v0.1.1", wins_for_champion=120, losses=80)
    assert anchor.resolve_anchor_check(conn) == "pass"

    meta = conn.execute(
        "SELECT value FROM meta WHERE key='baseline_version'").fetchone()
    assert meta["value"] == "v0.2"
    baseline = conn.execute(
        "SELECT offspring_id, deck_id FROM baselines WHERE version='v0.2'"
    ).fetchone()
    assert baseline is not None
    assert baseline["offspring_id"] == "v0.1.1"
    assert baseline["deck_id"] == "dElect"
    row = conn.execute(
        "SELECT version, verdict FROM anchor_checks WHERE offspring_id='v0.1.1'"
    ).fetchone()
    assert row["version"] == "v0.2" and row["verdict"] == "pass"
    verdict, done, planned, wr = anchor.anchor_status(conn, "v0.2")
    assert (verdict, done, planned) == ("pass", 200, 200)
    assert wr == pytest.approx(0.6)
    offspring = conn.execute(
        "SELECT status FROM offspring WHERE id='v0.1.1'").fetchone()
    assert offspring["status"] == "survivor"


def test_boundary_120_of_200_passes(tmp_path):
    """Boundary re-verified on the promotion (elect) path specifically --
    0.60 * 200 = 120, hand-verified -- to confirm the promotion trigger
    itself fires exactly at the bar, not just the verdict math."""
    conn = _connect(tmp_path)
    _seed_founding(conn)
    _seed_elect(conn, oid="v0.1.1", deck_id="dElect")
    anchor.enqueue_anchor_series(conn)
    _finish_games(conn, "v0.1.1", wins_for_champion=120, losses=80)
    assert anchor.resolve_anchor_check(conn) == "pass"
    meta = conn.execute(
        "SELECT value FROM meta WHERE key='baseline_version'").fetchone()
    assert meta["value"] == "v0.2"


def test_anchor_status_absent(tmp_path):
    conn = _connect(tmp_path)
    assert anchor.anchor_status(conn, "v9.9") == ("absent", 0, anchor.ANCHOR_GAMES, None)


def test_resolve_survives_concurrent_calls(tmp_path):
    db = tmp_path / "t.db"
    conn = deckdb.connect(db)
    deckdb.init_db(conn)
    _seed_baseline(conn)
    anchor.enqueue_anchor_series(conn)
    _finish_games(conn, "v0.3", wins_for_champion=120, losses=80)  # 120/200 = 0.60 -> pass
    verdicts = []

    def _race():
        c = deckdb.connect(db)
        verdicts.append(anchor.resolve_anchor_check(c))

    threads = [threading.Thread(target=_race) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(v for v in verdicts if v) == ["pass"]  # exactly one resolver wins


def test_resolve_survives_concurrent_calls_on_elect_pass(tmp_path):
    """Interleaved-mutation (.claude/rules/single-actor-worker-tests.md,
    the `toctou-guard-in-step-functions` addendum): the OTHER concurrent
    test above races a baseline-keyed (non-promoting) row -- this races
    two `resolve_anchor_check` calls against a SETTLED ELECT+PASS row, the
    highest-risk new path (baselines INSERT + meta UPDATE + re-key, all in
    the same transaction). THIS PATH GATES KAGGLE UPLOADS: a double
    promotion would advance the baseline twice off one crowned champion.

    Barrier + statement-counter (`tests/fixtures/race.py`): the two calls are
    released together, and `INSERT INTO baselines` is counted across both
    connections -- exactly one execution. Final-state assertions alone would
    be weaker here, since the loser's `WHERE verdict='pending'` guard makes
    several end states look identical whether or not the second racer got as
    far as the promotion writes."""
    db = tmp_path / "t.db"
    conn = deckdb.connect(db)
    deckdb.init_db(conn)
    _seed_founding(conn)
    _seed_elect(conn, oid="v0.1.1", deck_id="dElect")
    anchor.enqueue_anchor_series(conn)
    _finish_games(conn, "v0.1.1", wins_for_champion=120, losses=80)  # 0.60 -> pass

    verdicts, promotions = race_two(
        db,
        lambda c: anchor.resolve_anchor_check(c),
        lambda s: s.startswith("INSERT INTO BASELINES"),
    )

    assert promotions == 1, f"promotion must execute exactly once, saw {promotions}"
    assert sorted(v for v in verdicts if v) == ["pass"]  # exactly one resolver wins
    # exactly one baselines row inserted (v0.1 founding + v0.2 promoted, no v0.3)
    assert conn.execute("SELECT COUNT(*) FROM baselines").fetchone()[0] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM baselines WHERE version='v0.2'"
    ).fetchone()[0] == 1
    # meta bumped exactly once -- not double-bumped past v0.2
    meta = conn.execute(
        "SELECT value FROM meta WHERE key='baseline_version'").fetchone()
    assert meta["value"] == "v0.2"
    # re-key happened exactly once: no stray row still keyed 'v0.1.1'
    assert conn.execute(
        "SELECT COUNT(*) FROM anchor_checks WHERE version='v0.1.1'"
    ).fetchone()[0] == 0
    row = conn.execute(
        "SELECT version, verdict FROM anchor_checks WHERE offspring_id='v0.1.1'"
    ).fetchone()
    assert row["version"] == "v0.2" and row["verdict"] == "pass"


def test_installed_anchor_matches_crowned_winner_source():
    """`anchor.ANCHOR_DECK_PATH` (the deck the census/floor/anchor-gate
    opponent actually plays) must stay byte-identical to the source-of-truth
    candidate CSV recorded as the min-basics mini-tournament's crowned
    winner (`experiments/EXPERIMENTS.md`, 2026-08-11 entry: WINNER
    anchor-cand-a-lucario-min8, pooled 0.932 across the round-robin). A
    future candidate-CSV regeneration, or a hand-edit of the installed
    anchor file, could silently diverge the two -- pin them together so any
    drift fails loudly instead of quietly changing what the factory's
    submission-strength gate plays against."""
    winner_path = (
        Path(__file__).resolve().parents[1] / "src" / "ptcg" / "decks"
        / "candidates" / "anchor-cand-a-lucario-min8.csv"
    )
    assert winner_path.is_file(), f"crowned-winner source CSV missing: {winner_path}"
    assert anchor.ANCHOR_DECK_PATH.is_file(), (
        f"installed anchor missing: {anchor.ANCHOR_DECK_PATH}"
    )
    assert anchor.ANCHOR_DECK_PATH.read_bytes() == winner_path.read_bytes(), (
        "installed anchor (anchor.ANCHOR_DECK_PATH) has diverged from the "
        "crowned min-basics mini-tournament winner it was installed from -- "
        "regenerate anchor-min8.csv from anchor-cand-a-lucario-min8.csv, or "
        "re-run the anchor mini-tournament if the winner has legitimately "
        "changed."
    )
