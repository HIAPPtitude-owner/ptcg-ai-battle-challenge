"""Tests for counted-pair protection (pairgate.py) -- evictee resolution,
pair-gate series, resolve/TOCTOU step functions. Provenance shapes per
`.claude/rules/provenance-shaped-optional-fields.md`; race receipts per
`.claude/rules/single-actor-worker-tests.md` via tests/fixtures/race.py."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ptcg.factory import deckdb, loop, loop_state, pairgate
from ptcg.factory.kaggle_client import SubmissionRow


def _connect(tmp_path: Path) -> sqlite3.Connection:
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    return conn


def _seed_founding(conn) -> None:
    """Founding v0.1 baseline on dBase (mirrors the _seed_founding
    convention in tests/test_factory_subscheduler.py)."""
    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cBase','[\"Z\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dBase','cBase','[101]')")
    deckdb._write(conn, _s)
    loop_state.set_founding_baseline(conn, "dBase", loop.FOUNDING_AGENT_CONFIG)


def _seed_crowned(conn) -> None:
    """Crown v0.2 on top of founding v0.1: offspring v0.1.1 (survivor)
    becomes the current baseline -- the end-state shape
    anchor.resolve_anchor_check's elect+pass branch produces."""
    def _s(c):
        c.execute(
            "INSERT INTO offspring(id,parent_baseline_version,search_config_json,"
            "value_net_ref,deck_id,status,created_at) "
            "VALUES('v0.1.1','v0.1','{\"search_budget_ms\": 200}',NULL,'dBase',"
            "'survivor','t')")
        c.execute(
            "INSERT INTO baselines(version,offspring_id,deck_id,crowned_at) "
            "VALUES('v0.2','v0.1.1','dBase','t')")
        c.execute("INSERT OR REPLACE INTO meta(key,value) "
                  "VALUES('baseline_version','v0.2')")
    deckdb._write(conn, _s)


def _row(desc: str, date: str, status: str = "COMPLETE",
         score: float | None = 500.0) -> SubmissionRow:
    return SubmissionRow("s.tar.gz", date, desc, status, score)


_CHAMP_DESC = ("tournament-champion v0.1 - deck dBase - agent search-net "
               "- anchor-wr 0.600 of 200 games - abc12345 - factory")
_NEWER_DESC = ("tournament-champion v0.2 - deck dBase - agent search-net "
               "- anchor-wr 0.700 of 200 games - abc12345 - factory")
_PROBE_DESC = ("tournament-probe-cBase v0.1 - deck dBase - agent search-net "
               "- anchor-wr 0.500 of 15 games - abc12345 - factory")
_RESCUE_DESC = ("mega-lucario-fighting-heuristic v1.0 - deck deck - agent "
                "heuristic - local_wr 0.460 of 50 - abc12345 - factory")


# --- parse_description -------------------------------------------------

def test_parse_description_roundtrip():
    assert pairgate.parse_description(_CHAMP_DESC) == (
        "tournament-champion", "v0.1", "dBase")
    assert pairgate.parse_description(_PROBE_DESC) == (
        "tournament-probe-cBase", "v0.1", "dBase")
    assert pairgate.parse_description(_RESCUE_DESC) == (
        "mega-lucario-fighting-heuristic", "v1.0", "deck")


def test_parse_description_malformed_returns_none():
    assert pairgate.parse_description("") is None
    assert pairgate.parse_description("no separators here") is None
    assert pairgate.parse_description("name-only - notdeck x - y") is None


# --- resolve_evictee: provenance shapes --------------------------------
# (.claude/rules/provenance-shaped-optional-fields.md -- every creation
# path of a counted submission is a first-class test case.)

def test_resolve_evictee_champion_provenance(tmp_path):
    conn = _connect(tmp_path)
    _seed_founding(conn)
    _seed_crowned(conn)
    rows = [_row(_NEWER_DESC, "2026-08-04 12:00:00"),
            _row(_CHAMP_DESC, "2026-08-03 12:00:00")]
    ev = pairgate.resolve_evictee(conn, rows)
    assert ev == pairgate.EvicteeRef(
        name="tournament-champion", version="v0.1", deck_id="dBase",
        submitted_at="2026-08-03 12:00:00")


def test_resolve_evictee_probe_provenance(tmp_path):
    """A daily-floor PROBE upload: baseline agent version + alternate deck.
    Deck comes from the description stem (== decks.id for pipeline
    uploads); version resolves via the baselines chain."""
    conn = _connect(tmp_path)
    _seed_founding(conn)
    rows = [_row(_NEWER_DESC, "2026-08-04 12:00:00"),
            _row(_PROBE_DESC, "2026-08-03 12:00:00")]
    # v0.2 newer row exists on Kaggle but only v0.1 exists locally: the
    # EVICTEE (older row, the probe) is what must reconstruct.
    ev = pairgate.resolve_evictee(conn, rows)
    assert ev.version == "v0.1" and ev.deck_id == "dBase"
    assert ev.name == "tournament-probe-cBase"


def test_resolve_evictee_fewer_than_two_counted_returns_none(tmp_path):
    conn = _connect(tmp_path)
    _seed_founding(conn)
    assert pairgate.resolve_evictee(conn, []) is None
    assert pairgate.resolve_evictee(
        conn, [_row(_CHAMP_DESC, "2026-08-03 12:00:00")]) is None


def test_resolve_evictee_error_rows_are_not_counted(tmp_path):
    """ERROR submissions never become counted leaderboard rows: with 2 rows
    of which 1 is ERROR there is <2 counted (None); with 3 rows the ERROR
    row is skipped when picking the counted pair."""
    conn = _connect(tmp_path)
    _seed_founding(conn)
    _seed_crowned(conn)
    err = _row(_NEWER_DESC, "2026-08-05 12:00:00", status="ERROR", score=None)
    ok_new = _row(_NEWER_DESC, "2026-08-04 12:00:00")
    ok_old = _row(_CHAMP_DESC, "2026-08-03 12:00:00")
    assert pairgate.resolve_evictee(conn, [err, ok_old]) is None
    ev = pairgate.resolve_evictee(conn, [err, ok_new, ok_old])
    assert ev.version == "v0.1"  # ERROR row skipped; older of the 2 counted


@pytest.mark.parametrize("shape", ["rescue-legacy", "missing-genome",
                                   "missing-deck", "malformed"])
def test_resolve_evictee_fail_closed_shapes(tmp_path, shape):
    """FAIL-CLOSED (spec design 2): any unreconstructable evictee raises
    EvicteeUnreconstructable -- never a silent None."""
    conn = _connect(tmp_path)
    _seed_founding(conn)
    newer = _row(_NEWER_DESC, "2026-08-04 12:00:00")
    if shape == "rescue-legacy":
        older = _row(_RESCUE_DESC, "2026-08-03 12:00:00")
    elif shape == "missing-genome":
        # baselines row exists but its winning offspring row is missing.
        def _s(c):
            c.execute("INSERT INTO baselines(version,offspring_id,deck_id,"
                      "crowned_at) VALUES('v0.9','v0.9.9','dBase','t')")
        deckdb._write(conn, _s)
        older = _row(_CHAMP_DESC.replace("v0.1", "v0.9"), "2026-08-03 12:00:00")
    elif shape == "missing-deck":
        older = _row(_PROBE_DESC.replace("deck dBase", "deck dGone"),
                     "2026-08-03 12:00:00")
    else:  # malformed description
        older = _row("total garbage", "2026-08-03 12:00:00")
    with pytest.raises(pairgate.EvicteeUnreconstructable):
        pairgate.resolve_evictee(conn, [newer, older])


# --- schema + enqueue + status (T3) ------------------------------------

def _evictee(version="v0.1", deck="dBase", at="2026-08-03 12:00:00"):
    return pairgate.EvicteeRef(name="tournament-champion", version=version,
                               deck_id=deck, submitted_at=at)


def test_init_db_creates_pair_gate_checks_on_virgin_db(tmp_path):
    conn = _connect(tmp_path)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "pair_gate_checks" in names


def test_ensure_schema_upgrades_legacy_db(tmp_path):
    """The live production tournament.db predates this table; the module's
    _ensure_schema must add it additively (anchor/floor/netcheck
    precedent)."""
    conn = deckdb.connect(tmp_path / "legacy.db")

    def _apply(c):
        for ddl in deckdb._DDL_STATEMENTS:
            if "pair_gate_checks" not in ddl:
                c.execute(ddl)
    deckdb._write(conn, _apply)
    pairgate._ensure_schema(conn)
    pairgate._ensure_schema(conn)  # idempotent
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "pair_gate_checks" in names


def test_enqueue_creates_row_and_200_games(tmp_path):
    conn = _connect(tmp_path)
    _seed_founding(conn)
    _seed_crowned(conn)
    assert pairgate.enqueue_pair_gate(conn, "v0.2", _evictee()) == 200
    row = conn.execute(
        "SELECT * FROM pair_gate_checks WHERE version='v0.2'").fetchone()
    assert row["opp_version"] == "v0.1"
    assert row["opp_deck_id"] == "dBase"
    assert row["opp_submitted_at"] == "2026-08-03 12:00:00"
    assert row["games_planned"] == 200 and row["verdict"] == "pending"
    game = conn.execute(
        "SELECT * FROM games WHERE purpose='pair_gate' LIMIT 1").fetchone()
    assert game["agent_version_a"] == "v0.2"   # champion side (fixed, side a)
    assert game["agent_version_b"] == "v0.1"   # evictee side
    assert game["deck_a_id"] == "dBase" and game["deck_b_id"] == "dBase"
    assert game["priority"] == pairgate.PAIR_GATE_PRIORITY
    n = conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='pair_gate'").fetchone()[0]
    assert n == 200
    # resumable top-up: second call enqueues only the shortfall (0 here)
    assert pairgate.enqueue_pair_gate(conn, "v0.2", _evictee()) == 0


def test_enqueue_survives_concurrent_calls(tmp_path):
    """Interleaved-mutation receipt: the loser's count-read runs after the
    winner's COMMIT, so the series is never doubled (netcheck
    test_enqueue_survives_concurrent_calls precedent)."""
    import threading
    db = tmp_path / "t.db"
    conn = deckdb.connect(db)
    deckdb.init_db(conn)
    _seed_founding(conn)
    _seed_crowned(conn)
    results = []

    def _call():
        c = deckdb.connect(db)
        results.append(pairgate.enqueue_pair_gate(c, "v0.2", _evictee()))
    t1 = threading.Thread(target=_call)
    t2 = threading.Thread(target=_call)
    t1.start(); t2.start(); t1.join(); t2.join()
    total = conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='pair_gate'").fetchone()[0]
    assert total == 200
    assert sorted(results) == [0, 200]


def test_pair_gate_status_shapes(tmp_path):
    """All evidence shapes first-class (provenance-shaped-optional-fields):
    absent / pending-with-progress."""
    conn = _connect(tmp_path)
    _seed_founding(conn)
    _seed_crowned(conn)
    assert pairgate.pair_gate_status(conn, "v0.2") == (
        "absent", 0, pairgate.PAIR_GATE_GAMES, None)
    pairgate.enqueue_pair_gate(conn, "v0.2", _evictee())
    _finish_pair_gate_games(conn, "v0.2", "v0.1", "dBase", wins=30, total=40)
    verdict, done, planned, wr = pairgate.pair_gate_status(conn, "v0.2")
    assert (verdict, done, planned, wr) == ("pending", 40, 200, None)


def _finish_pair_gate_games(conn, version, opp_version, opp_deck,
                            wins, total):
    """Mark the first `total` pending pair_gate games done: `wins` champion
    wins (winner=0), the rest opponent wins (winner=1)."""
    def _apply(c):
        rows = c.execute(
            "SELECT id FROM games WHERE purpose='pair_gate' "
            "AND agent_version_a=? AND agent_version_b=? AND deck_b_id=? "
            "AND status='pending' ORDER BY id LIMIT ?",
            (version, opp_version, opp_deck, total)).fetchall()
        assert len(rows) == total
        for i, row in enumerate(rows):
            c.execute("UPDATE games SET status='done', winner=? WHERE id=?",
                      (0 if i < wins else 1, row["id"]))
    deckdb._write(conn, _apply)


def test_pair_gate_versions_resolve_via_runner_pool(tmp_path):
    """PIN: both sides of an enqueued pair-gate game resolve through the
    REAL runner resolver (runner_pool._resolve_agent_entry) with no
    pair-gate-specific branch -- the no-runner-change claim's receipt.
    Also pins pairgate._version_resolvable against the real resolver."""
    from ptcg.factory import runner_pool
    conn = _connect(tmp_path)
    _seed_founding(conn)
    _seed_crowned(conn)
    pairgate.enqueue_pair_gate(conn, "v0.2", _evictee())
    game = conn.execute(
        "SELECT * FROM games WHERE purpose='pair_gate' LIMIT 1").fetchone()
    for version in (game["agent_version_a"], game["agent_version_b"]):
        entry = runner_pool._resolve_agent_entry(conn, version, {})
        assert entry["agent_kind"] in ("search-net", "heuristic")
        assert pairgate._version_resolvable(conn, version)


# --- resolve + TOCTOU (T4) ---------------------------------------------

from tests.fixtures.race import race_two


def test_resolve_pass_at_exact_bar(tmp_path):
    """Boundary arithmetic (hand-verified): 0.55*200 = 110 -> 110/200 =
    0.550 passes (bar is >=)."""
    conn = _connect(tmp_path)
    _seed_founding(conn)
    _seed_crowned(conn)
    pairgate.enqueue_pair_gate(conn, "v0.2", _evictee())
    _finish_pair_gate_games(conn, "v0.2", "v0.1", "dBase", wins=110, total=200)
    assert pairgate.resolve_pair_gate(conn, "v0.2") == "pass"
    row = conn.execute(
        "SELECT games_done, wins, wr, verdict FROM pair_gate_checks "
        "WHERE version='v0.2'").fetchone()
    assert (row["games_done"], row["wins"], row["verdict"]) == (200, 110, "pass")
    assert abs(row["wr"] - 0.550) < 1e-9


def test_resolve_fail_one_below_bar(tmp_path):
    """109/200 = 0.545 < 0.55 -> fail (draws would count as losses too --
    winner=0-only wins aggregate, anchor.py convention)."""
    conn = _connect(tmp_path)
    _seed_founding(conn)
    _seed_crowned(conn)
    pairgate.enqueue_pair_gate(conn, "v0.2", _evictee())
    _finish_pair_gate_games(conn, "v0.2", "v0.1", "dBase", wins=109, total=200)
    assert pairgate.resolve_pair_gate(conn, "v0.2") == "fail"


def test_resolve_pending_and_absent(tmp_path):
    conn = _connect(tmp_path)
    _seed_founding(conn)
    _seed_crowned(conn)
    assert pairgate.resolve_pair_gate(conn, "v0.2") == "absent"
    pairgate.enqueue_pair_gate(conn, "v0.2", _evictee())
    _finish_pair_gate_games(conn, "v0.2", "v0.1", "dBase", wins=50, total=100)
    assert pairgate.resolve_pair_gate(conn, "v0.2") == "pending"


def test_resolve_verdict_write_happens_exactly_once_under_race(tmp_path):
    """I1 RECEIPT (race.py doctrine): two racing resolvers must execute the
    verdict UPDATE exactly once. Return values are deliberately NOT the
    discriminator (the loser reports the settled verdict -- tautological);
    the statement counter is. RED receipt: de-transactionalize
    resolve_pair_gate (replace its `deckdb._write(conn, _apply)` tail with
    `_apply(conn)`) -- the race window this opens is real but PROBABILISTIC,
    not deterministic: measured ~60% of trials show updates == 2 (both
    racers pass the pending-read before either commits), the remaining
    trials race benignly to updates == 1 despite the missing transaction.
    Verify RED by running 10+ trials and expecting AT LEAST ONE updates == 2
    -- a single passing trial does NOT mean the test is tautological. The
    committed (transactionalized) GREEN is fully deterministic (15/15
    updates == 1 observed). Performed and recorded in this task's report,
    then reverted."""
    db = tmp_path / "t.db"
    conn = deckdb.connect(db)
    deckdb.init_db(conn)
    _seed_founding(conn)
    _seed_crowned(conn)
    pairgate.enqueue_pair_gate(conn, "v0.2", _evictee())
    _finish_pair_gate_games(conn, "v0.2", "v0.1", "dBase", wins=120, total=200)

    verdicts, updates = race_two(
        db,
        lambda c: pairgate.resolve_pair_gate(c, "v0.2"),
        lambda s: s.startswith("UPDATE PAIR_GATE_CHECKS"),
    )

    assert updates == 1, f"verdict write must execute exactly once, saw {updates}"
    assert verdicts == ["pass", "pass"]  # loser reports the settled verdict
    row = conn.execute(
        "SELECT verdict, resolved_at FROM pair_gate_checks "
        "WHERE version='v0.2'").fetchone()
    assert row["verdict"] == "pass" and row["resolved_at"] is not None


def test_toctou_current_opponent_same_config_updates_stamp(tmp_path):
    """I4: same (version, deck) config re-uploaded under a new date -- the
    evidence is vs the right CONFIG, so the verdict stands; only the
    stored submission stamp advances."""
    conn = _connect(tmp_path)
    _seed_founding(conn)
    _seed_crowned(conn)
    pairgate.enqueue_pair_gate(conn, "v0.2", _evictee())
    newer_stamp = _evictee(at="2026-08-05 09:00:00")
    assert pairgate.ensure_current_opponent(conn, "v0.2", newer_stamp) == "current"
    row = conn.execute("SELECT opp_submitted_at FROM pair_gate_checks "
                       "WHERE version='v0.2'").fetchone()
    assert row["opp_submitted_at"] == "2026-08-05 09:00:00"


def test_toctou_stale_verdict_rekeys_and_reenqueues(tmp_path):
    """I4: the evictee CHANGED between enqueue and upload -- a stale 'pass'
    must NOT pass. The row re-keys onto the new opponent (counters reset,
    verdict back to pending), the old matchup's pending games are
    superseded-DELETEd, and a fresh full series exists -- all in ONE
    transaction."""
    conn = _connect(tmp_path)
    _seed_founding(conn)
    _seed_crowned(conn)
    # A second reconstructable opponent deck: cAlt/dAlt + offspring v0.1.2.
    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cAlt','[\"A\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dAlt','cAlt','[202]')")
        c.execute(
            "INSERT INTO offspring(id,parent_baseline_version,search_config_json,"
            "value_net_ref,deck_id,status,created_at) "
            "VALUES('v0.1.2','v0.1','{}',NULL,'dAlt','trashed','t')")
    deckdb._write(conn, _s)
    pairgate.enqueue_pair_gate(conn, "v0.2", _evictee())
    _finish_pair_gate_games(conn, "v0.2", "v0.1", "dBase", wins=120, total=200)
    assert pairgate.resolve_pair_gate(conn, "v0.2") == "pass"

    new_ev = pairgate.EvicteeRef(name="tournament-champion", version="v0.1.2",
                                 deck_id="dAlt", submitted_at="2026-08-05 09:00:00")
    assert pairgate.ensure_current_opponent(conn, "v0.2", new_ev) == "rekeyed"
    row = conn.execute("SELECT * FROM pair_gate_checks WHERE version='v0.2'").fetchone()
    assert (row["opp_version"], row["opp_deck_id"]) == ("v0.1.2", "dAlt")
    assert row["verdict"] == "pending"
    assert (row["games_done"], row["wins"], row["wr"]) == (0, 0, None)
    # old matchup's PENDING games superseded (done games remain as history)
    stale_pending = conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='pair_gate' AND "
        "status='pending' AND agent_version_b='v0.1'").fetchone()[0]
    assert stale_pending == 0
    fresh = conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='pair_gate' AND "
        "agent_version_b='v0.1.2' AND deck_b_id='dAlt'").fetchone()[0]
    assert fresh == 200


def test_toctou_rekey_happens_exactly_once_under_race(tmp_path):
    """I4 RECEIPT: two racing upload-time verifications against the same
    stale row must re-key it exactly once (statement counter on the re-key
    UPDATE), and the loser must observe 'current' (the winner already
    re-keyed onto the evictee it was itself carrying). Non-tautological on
    BOTH observables: sorted(results) == ['current','rekeyed'] AND the
    counter == 1. RED receipt: same de-transactionalization procedure as
    the resolver race test -- both racers then read the stale row and MAY
    both execute the re-key UPDATE (counter == 2), but this is
    PROBABILISTIC, not deterministic: measured ~60% of trials show
    counter == 2, the rest race benignly to counter == 1 despite the
    missing transaction. Verify RED by running 10+ trials and expecting AT
    LEAST ONE counter == 2 -- a single passing trial does NOT mean the test
    is tautological. The committed (transactionalized) GREEN is fully
    deterministic (15/15 counter == 1 observed)."""
    db = tmp_path / "t.db"
    conn = deckdb.connect(db)
    deckdb.init_db(conn)
    _seed_founding(conn)
    _seed_crowned(conn)
    def _s(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cAlt','[\"A\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dAlt','cAlt','[202]')")
        c.execute(
            "INSERT INTO offspring(id,parent_baseline_version,search_config_json,"
            "value_net_ref,deck_id,status,created_at) "
            "VALUES('v0.1.2','v0.1','{}',NULL,'dAlt','trashed','t')")
    deckdb._write(conn, _s)
    pairgate.enqueue_pair_gate(conn, "v0.2", _evictee())
    new_ev = pairgate.EvicteeRef(name="tournament-champion", version="v0.1.2",
                                 deck_id="dAlt", submitted_at="2026-08-05 09:00:00")

    results, rekeys = race_two(
        db,
        lambda c: pairgate.ensure_current_opponent(c, "v0.2", new_ev),
        lambda s: s.startswith("UPDATE PAIR_GATE_CHECKS SET OPP_VERSION"),
    )

    assert rekeys == 1, f"re-key must execute exactly once, saw {rekeys}"
    assert sorted(results) == ["current", "rekeyed"]
    total = conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='pair_gate' AND "
        "agent_version_b='v0.1.2'").fetchone()[0]
    assert total == 200  # series enqueued once, never doubled


# --- superseded-series sweep (Fix round 1: Task 6 opus review) ---------

def _crown_next_baseline(conn, offspring_id, parent_version, new_version,
                         deck_id="dBase", crowned_at="t2"):
    """Crown `new_version` on top of `parent_version` (mirrors
    _seed_crowned's shape one generation further)."""
    def _s(c):
        c.execute(
            "INSERT INTO offspring(id,parent_baseline_version,"
            "search_config_json,value_net_ref,deck_id,status,created_at) "
            "VALUES(?,?,'{\"search_budget_ms\": 200}',NULL,?,'survivor',?)",
            (offspring_id, parent_version, deck_id, crowned_at))
        c.execute(
            "INSERT INTO baselines(version,offspring_id,deck_id,crowned_at) "
            "VALUES(?,?,?,?)", (new_version, offspring_id, deck_id, crowned_at))
    deckdb._write(conn, _s)


def test_enqueue_sweeps_superseded_pending_games(tmp_path):
    """Reviewer receipt (Task 6 opus review, real-DB probe): v0.2 enqueues
    its 200-game pair_gate series, then v0.3 is crowned before that series
    finishes. Without a sweep, v0.2's stale pending games sit ahead of
    v0.3's fresh series in claim_next_game's (priority DESC, id ASC)
    claim order (same PAIR_GATE_PRIORITY, lower id), delaying the live
    champion's upload by a full dead series. Mirrors anchor.py's
    supersede-DELETE (anchor.py:154-158)."""
    conn = _connect(tmp_path)
    _seed_founding(conn)
    _seed_crowned(conn)  # v0.2 crowned
    assert pairgate.enqueue_pair_gate(conn, "v0.2", _evictee()) == 200
    # v0.3 crowns before v0.2's series resolves -- the live scenario.
    _crown_next_baseline(conn, "v0.2.1", "v0.2", "v0.3")

    n = pairgate.enqueue_pair_gate(
        conn, "v0.3",
        _evictee(version="v0.2", at="2026-08-04 12:00:00"))
    assert n == 200

    stale = conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='pair_gate' "
        "AND agent_version_a='v0.2' AND status='pending'").fetchone()[0]
    assert stale == 0
    fresh = conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='pair_gate' "
        "AND agent_version_a='v0.3' AND status='pending'").fetchone()[0]
    assert fresh == 200
    # the claim queue is no longer blocked by v0.2's dead series.
    claimed = deckdb.claim_next_game(conn, worker_pid=1)
    assert claimed["agent_version_a"] == "v0.3"


def test_enqueue_sweep_preserves_done_games_as_history(tmp_path):
    """The sweep must only DELETE `pending` rows -- `done` games from the
    superseded series stay as history (ensure_current_opponent's rekey
    precedent, pairgate.py:322-324 comment)."""
    conn = _connect(tmp_path)
    _seed_founding(conn)
    _seed_crowned(conn)
    pairgate.enqueue_pair_gate(conn, "v0.2", _evictee())
    _finish_pair_gate_games(conn, "v0.2", "v0.1", "dBase", wins=3, total=5)
    _crown_next_baseline(conn, "v0.2.1", "v0.2", "v0.3")

    pairgate.enqueue_pair_gate(
        conn, "v0.3", _evictee(version="v0.2", at="2026-08-04 12:00:00"))

    shapes = {r["status"]: r["c"] for r in conn.execute(
        "SELECT status, COUNT(*) c FROM games WHERE purpose='pair_gate' "
        "AND agent_version_a='v0.2' GROUP BY status")}
    assert shapes == {"done": 5}


def test_enqueue_sweep_is_idempotent_for_the_current_version(tmp_path):
    """A second enqueue call for the SAME (already-superseding) version
    must still no-op on games (resumable top-up guard unaffected by the
    new sweep)."""
    conn = _connect(tmp_path)
    _seed_founding(conn)
    _seed_crowned(conn)
    pairgate.enqueue_pair_gate(conn, "v0.2", _evictee())
    _crown_next_baseline(conn, "v0.2.1", "v0.2", "v0.3")
    ev = _evictee(version="v0.2", at="2026-08-04 12:00:00")
    assert pairgate.enqueue_pair_gate(conn, "v0.3", ev) == 200
    assert pairgate.enqueue_pair_gate(conn, "v0.3", ev) == 0
    n = conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='pair_gate' "
        "AND agent_version_a='v0.3'").fetchone()[0]
    assert n == 200


# --- min-basics invariant pin (T10): culled ex-champion evictee ---------
# Plan-drift note: the brief cites a `tmp_db` fixture that does not exist
# anywhere in this repo (verified via grep). This file's own convention --
# every test above -- is `tmp_path` (pytest builtin) + `_connect(tmp_path)`.
# Diverged to that convention rather than inventing a new fixture.


def test_culled_ex_champion_still_reconstructs_and_gates(tmp_path):
    """min-basics invariant (spec Section 4): cull-is-status-change must
    keep the evictee reconstructable, or every upload silently stops
    (fail-closed). Fixture mirrors the post-migration live shape: the
    champion deck's concept is 'culled', its offspring row is 'trashed',
    the deck ROW itself is retained (pairgate.py:129-169's reconstruction
    chain -- baselines row -> _version_resolvable -> a bare
    `SELECT 1 FROM decks WHERE id=?` -- has no status filter anywhere)."""
    conn = _connect(tmp_path)
    conn.execute("INSERT INTO concepts(id, cores, status, reason) "
                 "VALUES('cX', '[\"x\"]', 'culled', 'min-basics-rule: 4 basics < 8')")
    conn.execute("INSERT INTO decks(id, concept_id, cards, shell_variant) "
                 "VALUES('dX', 'cX', '[3]', 0)")
    conn.execute("INSERT INTO offspring(id, parent_baseline_version, search_config_json, "
                 "status, created_at) VALUES('v9.8.1', 'v9.8', '{}', 'trashed', 'now')")
    conn.execute("INSERT INTO baselines(version, offspring_id, deck_id, crowned_at) "
                 "VALUES('v9.9', 'v9.8.1', 'dX', 'now')")
    rows = [_row("tournament-champion v9.10 - deck dY - x", "2026-08-11 09:00:00"),
            _row("tournament-champion v9.9 - deck dX - x", "2026-08-10 09:00:00")]
    ref = pairgate.resolve_evictee(conn, rows)
    assert ref is not None and ref.version == "v9.9" and ref.deck_id == "dX"

    # Enqueue leg: the gating version (v9.10, the newer counted row) needs
    # its own baselines row -- `decks` has no FK constraint on
    # `baselines.deck_id`, so 'dY' need not exist as a real deck row.
    conn.execute("INSERT INTO baselines(version, offspring_id, deck_id, crowned_at) "
                 "VALUES('v9.10', NULL, 'dY', 'now')")
    n = pairgate.enqueue_pair_gate(conn, "v9.10", ref)
    assert n == pairgate.PAIR_GATE_GAMES
    games = conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='pair_gate' "
        "AND deck_b_id='dX'").fetchone()[0]
    assert games == pairgate.PAIR_GATE_GAMES


def test_culled_ex_champion_deck_row_deleted_fails_closed(tmp_path):
    """Negative pin: deleting the deck ROW itself (the thing the
    min-basics cull migration must never do -- only concepts/offspring
    STATUS changes) raises EvicteeUnreconstructable. Proves the positive
    test above discriminates on deck-row presence rather than always
    succeeding regardless of DB state (RED-capable receipt, not a
    tautology)."""
    conn = _connect(tmp_path)
    conn.execute("INSERT INTO concepts(id, cores, status, reason) "
                 "VALUES('cX', '[\"x\"]', 'culled', 'min-basics-rule: 4 basics < 8')")
    conn.execute("INSERT INTO decks(id, concept_id, cards, shell_variant) "
                 "VALUES('dX', 'cX', '[3]', 0)")
    conn.execute("INSERT INTO offspring(id, parent_baseline_version, search_config_json, "
                 "status, created_at) VALUES('v9.8.1', 'v9.8', '{}', 'trashed', 'now')")
    conn.execute("INSERT INTO baselines(version, offspring_id, deck_id, crowned_at) "
                 "VALUES('v9.9', 'v9.8.1', 'dX', 'now')")
    conn.execute("DELETE FROM decks WHERE id='dX'")
    rows = [_row("tournament-champion v9.10 - deck dY - x", "2026-08-11 09:00:00"),
            _row("tournament-champion v9.9 - deck dX - x", "2026-08-10 09:00:00")]
    with pytest.raises(pairgate.EvicteeUnreconstructable):
        pairgate.resolve_evictee(conn, rows)
