"""Phase-1 integration test + containment gate (tournament plan Task 10).

Exercises the full Founding Census pipeline end-to-end against ONE SQLite DB
and a deterministic stubbed `play_match`: seed -> pair-seed -> schedule ->
claim -> record (T1-T6) -> rate (T5) -> promote -> pair-activate (T8) ->
standard-floor screening scheduling for the newly-activated pairs. Asserts
cross-module invariants the plan's Interfaces section names explicitly: no
dropped games, ratings monotone with win share, `census_complete` reachable
with dormant pairs present, and an activated pair gets a lazily-built deck
plus 40-game screening scheduling.

CONFIRMED PLAN GAP (verified via `.claude/rules/diagnose-before-dispatch.md`
three-channel check before any code was written): no task in the plan
(T1-T21, grepped twice) ever promotes a single-core `concepts` row from
`status='untested'` to `'active'` -- only pair rows ever reach `'active'`,
and only inside `census.activate_pair_concepts` itself. Without a promotion
writer, `activate_pair_concepts`'s own `_ACTIVE_SINGLES_QUERY` gate
(`status='active' AND rating IS NOT NULL`) permanently matches zero rows no
matter how many singles finish census screening with a decisive rating, so
pair activation is a production no-op. `census.promote_proven_singles`
(this task's fix; see its docstring in `src/ptcg/factory/census.py`) closes
the gap at the natural seam this test exercises: after `rate`, before
`pair-activate`. Reproduced RED at base commit `9a3f330` two ways: (1) a
standalone repro script forcing coverage to games_played=15/rating=1.0 for 3
real singles and observing `activate_pair_concepts` return 0; (2) this very
test file, which fails with `AttributeError: module 'ptcg.factory.census'
has no attribute 'promote_proven_singles'` before the fix lands.

Containment gate (Global Constraints + Slice Containment Strategy). Three
independent checks below, INVERTED at the T20 cutover (the watch loop now
legitimately hosts the submission scheduler + surviving harvester):
  - `test_containment_step4_diff_against_master_is_empty` -- ladder-identity
    files (`submission_main.py`/`current.py`) plus unmodified legacy
    (`cycle.py`/`evolution.py`) stay byte-identical to master.
    `factory_watch_once.py` was REMOVED from this pin at T20 (it is now the
    cutover's own modify target).
  - `test_watch_loop_import_graph_pins_cutover_boundary` -- the narrowed
    loop's import graph MUST include `subscheduler`/`episodes`/`deckdb` and
    MUST NOT reach the sibling-process/census modules or the retired
    `dashboard` (fresh-subprocess probe: in-process `sys.modules` is polluted
    by every other test file in this session, so the check MUST run in an
    isolated interpreter).
  - `test_phase1_modules_never_reference_experiments_factory_state` -- the
    Phase-1 census modules still never write into `experiments/factory`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from ptcg.factory import anchor, census, deckdb, rating, runner_pool
from ptcg.factory.builder import Concept

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 4 real, engine-verified buildable single-core names -- a subset of
#: tests/test_factory_census_pairs.py's `_FIVE_CORES` (confirmed buildable
#: via `builder.build_deck` at implementation time, per
#: `.claude/rules/verify-game-data-claims.md`). Alphabetical order doubles
#: as the deterministic total-dominance rank the stub match uses below, so
#: Bradley-Terry ratings come out strictly monotone by construction.
#:
#: unpayable-attack-pool-rule (2026-08-12) T2 fix round 1: re-derived in
#: lockstep with `_FIVE_CORES` above (same 2-type-cap regression, same
#: mono-payable-chain / same-primary-type derivation) -- this test builds
#: EVERY pair among these 4 (all C(4,2)=6), so every pairwise combination,
#: not just index [0]+[1], had to be verified buildable under the new R2
#: cap. All 4 share the same single primary energy type (Psychic, type 5).
_CORES = ["Abra", "Alakazam", "Aromatisse", "Azelf"]
_RANK = {name: i for i, name in enumerate(_CORES)}  # lower index = strictly stronger

AGENT_CFGS = {"v0.1": {"agent_kind": "search-net", "agent_config": {}}}

#: Leaf module names under `ptcg.factory.` the live watch loop must NEVER
#: reach. UPDATED at the T20 cutover (CONTAINMENT INVERSION): the narrowed
#: watch loop now LEGITIMATELY imports `subscheduler` + `episodes` + `deckdb`
#: (see `_WATCH_LOOP_WANTED_LEAVES`), so `deckdb` moved out of the forbidden
#: set. What stays forbidden is the SIBLING-PROCESS entrypoints
#: (`loop_scheduler` = ptcg-factory-scheduler, `runner_pool` = the game
#: workers, `ui_server` = ptcg-factory-ui), the census/loop pipeline
#: (`census`/`builder`/`rating`/`loop` -- owned by the scheduler process,
#: never the watch loop), and `dashboard` (RETIRED from the loop per PD-B --
#: the UI `/status` page is the sole status surface). Any of these appearing
#: in a fresh interpreter's `sys.modules` after importing
#: `scripts.factory_watch_once` is a containment violation.
_TOURNAMENT_LEAVES = (
    "loop_scheduler", "runner_pool", "ui_server",
    "census", "builder", "rating", "loop", "dashboard",
)

#: Leaf modules the narrowed watch loop MUST import post-cutover (T20 positive
#: pin -- the intended new import graph, plan T20 Interfaces): the submission
#: scheduler, the surviving episode harvester, and the tournament DB module it
#: connects to for `maybe_submit`.
_WATCH_LOOP_WANTED_LEAVES = ("subscheduler", "episodes", "deckdb")

#: Phase-1's own new source files -- checked for any literal reference to
#: `experiments/` (the live factory's JSON-ledger state tree), which this
#: slice must never write into (Global Constraints: "existing JSON ledgers
#: ... are UNAFFECTED and untouched").
_TOURNAMENT_SRC_FILES = (
    "src/ptcg/factory/deckdb.py",
    "src/ptcg/factory/builder.py",
    "src/ptcg/factory/census.py",
    "src/ptcg/factory/rating.py",
    "src/ptcg/factory/runner_pool.py",
)

#: Files that must STILL be byte-identical to master after the T20 cutover.
#: `scripts/factory_watch_once.py` was REMOVED from this list at T20
#: (CONTAINMENT INVERSION): the cutover legitimately rewrites the watch loop,
#: so an empty diff on it is no longer the invariant. The ladder-identity
#: files (`submission_main.py`, `current.py`) MUST stay empty-diff (Global
#: Constraints); `cycle.py`/`evolution.py` stay empty-diff too -- the narrowed
#: loop stops CALLING them but never edits their source.
_STEP4_DIFF_PATHS = (
    "src/ptcg/submission_main.py",
    "src/ptcg/agents/current.py",
    "src/ptcg/factory/cycle.py",
    "src/ptcg/factory/evolution.py",
)


class _StubMatch:
    """Matches just enough of `MatchResult`'s shape for `run_one_game`
    (mirrors tests/test_factory_runner_pool.py's `_StubMatch`)."""

    def __init__(self, winner: int):
        self.winner = winner
        self.error = None
        self.turns = 1
        self.moves = 1


def _db(tmp_path: Path):
    d = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(d)
    return d


def _single_deck_core_map(db) -> dict[str, str]:
    """deck_id -> its single core's name, for every single-core deck."""
    out: dict[str, str] = {}
    for row in db.execute(
        "SELECT d.id AS deck_id, c.cores AS cores FROM decks d "
        "JOIN concepts c ON c.id = d.concept_id WHERE json_array_length(c.cores) = 1"
    ).fetchall():
        out[row["deck_id"]] = json.loads(row["cores"])[0]
    return out


#: Deterministic win count out of `SCREENING_FLOOR` (15) games per rank --
#: strictly decreasing, so the win-RATE-vs-anchor ratings below (T4/T5
#: anchor-opponent rework: `coverage.rating` is now plain wr-vs-anchor, not a
#: Bradley-Terry fit -- see `rating.py`'s SCALE CHANGE docstring) come out
#: strictly monotone with zero ties: 15/15=1.00, 11/15~=0.733, 7/15~=0.467,
#: 3/15=0.20.
_WINS_BY_RANK = {0: 15, 1: 11, 2: 7, 3: 3}


def _win_rate_stub_play_match(deck_core: dict[str, str]):
    """Deterministic win-rate-vs-anchor stub (T4/T9 anchor-opponent rework):
    every `screening` game now has the FIXED anchor deck on the `deck_b_id`
    side (`census.schedule_screening_games`'s own convention -- the subject
    concept is always `deck_a_id`/`agent0`), so this stub only needs to rank
    the CANDIDATE side and never looks at the anchor side at all. Each
    candidate deck's win count over its own `SCREENING_FLOOR`-game series is
    fixed per `_RANK` (`_WINS_BY_RANK`), giving a clean, tie-free ordering
    for the post-census rating assertions below. `build_agent` is
    monkeypatched to return `("agent-for", cand.deck)` so this stub can
    identify which REAL deck the candidate seat holds without touching the
    engine (winner-mapping-pin pattern, mirrors
    tests/test_factory_runner_pool.py's `test_winner_mapping_pins_deck_a_as_player_zero`).
    """
    calls: dict[str, int] = {}

    def _play(agent0, agent1, deck0, deck1, **kwargs):
        did0 = agent0[1]  # candidate side -- screening's deck_a_id convention
        rank = _RANK[deck_core[did0]]
        calls[did0] = calls.get(did0, 0) + 1
        return _StubMatch(winner=0 if calls[did0] <= _WINS_BY_RANK[rank] else 1)

    return _play


def test_phase1_pipeline_end_to_end(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    db = _db(tmp_path)

    # seed: 4 real singles, small monkeypatched enumeration (plan Step 1).
    monkeypatch.setattr(
        census, "enumerate_concepts", lambda: [Concept(cores=(n,)) for n in _CORES]
    )
    seed_res = census.seed_census(db)
    assert seed_res["buildable"] == len(_CORES)
    assert seed_res["unbuildable"] == 0

    # pair-seed: all C(4,2)=6 pairs, dormant (no deck/coverage rows).
    n_pairs = census.seed_pair_concepts(db)
    assert n_pairs == 6
    assert (
        db.execute(
            "SELECT COUNT(*) FROM decks d JOIN concepts c ON c.id = d.concept_id "
            "WHERE json_array_length(c.cores) = 2"
        ).fetchone()[0]
        == 0
    )

    deck_core = _single_deck_core_map(db)
    assert len(deck_core) == len(_CORES)

    # T4 anchor-opponent rework (T9 wiring): `schedule_screening_games`
    # no-ops until the anchor deck is registered -- census now plays every
    # candidate against the FIXED anchor deck, never round-robin. Idempotent
    # (INSERT OR IGNORE), mirrors `loop_scheduler.loop_tick`'s own wiring.
    anchor.ensure_anchor_deck(db)

    monkeypatch.setattr(runner_pool, "build_agent", lambda cand, deck: ("agent-for", cand.deck))
    monkeypatch.setattr(runner_pool, "play_match", _win_rate_stub_play_match(deck_core))

    # schedule -> claim/record, repeated until census_complete. Pending-aware
    # scheduling means an immediate re-call with games still pending enqueues
    # 0 extra (census.py:126 docstring), so this loop only makes progress
    # once games are actually played -- mirroring the real per-tick cadence
    # a future loop scheduler (T16) will drive.
    for _ in range(10):
        census.schedule_screening_games(db, target_per_concept=census.SCREENING_FLOOR, batch=1000)
        runner_pool.worker_loop(str(db_path), AGENT_CFGS, tmp_path, stop_when_empty=True)
        if census.census_complete(db, floor=census.SCREENING_FLOOR):
            break
    else:
        pytest.fail("census did not complete within 10 schedule/drain iterations")

    # no dropped games: queue fully drained (nothing pending or claimed), and
    # coverage bookkeeping matches EXACTLY the number of completed games --
    # T4's anchor-opponent rework means every screening game's `deck_b_id`
    # is the anchor, which deliberately carries NO coverage row
    # (`anchor.ensure_anchor_deck`'s own docstring), so `deckdb.record_result`
    # bumps only the CANDIDATE side's games_played (the anchor-side bump is a
    # silent no-op against the missing row) -- summed games_played over the
    # 4 real singles must equal exactly the done-game count, not 2x it (the
    # pre-anchor-opponent mirror-vs-mirror convention this test used to
    # exercise, where BOTH sides were real, coverage-bearing concepts).
    assert deckdb.pending_count(db) == 0
    assert db.execute("SELECT COUNT(*) FROM games WHERE status='claimed'").fetchone()[0] == 0
    n_done = db.execute("SELECT COUNT(*) FROM games WHERE status='done'").fetchone()[0]
    assert n_done > 0
    total_games_played = db.execute(
        "SELECT SUM(games_played) FROM coverage co "
        "JOIN concepts c ON c.id = co.concept_id WHERE json_array_length(c.cores) = 1"
    ).fetchone()[0]
    assert total_games_played == n_done

    # census_complete reachable with dormant pairs present (the 6 pair rows
    # seeded above have no coverage row at all and never block completion).
    assert census.census_complete(db, floor=census.SCREENING_FLOOR) is True

    # rate: Bradley-Terry fit over the just-played screening games.
    n_rated = rating.refresh_field_ratings(db)
    assert n_rated == len(_CORES)

    # ratings monotone with win share: the stub is a strict total-dominance
    # hierarchy over _RANK, so BT rating must come out strictly decreasing
    # in the same order (no ties -- MM iteration separates a clean
    # dominance hierarchy; bt.py's SMOOTH regularization keeps it finite).
    rating_by_core: dict[str, float] = {}
    for did, core in deck_core.items():
        cid = db.execute("SELECT concept_id FROM decks WHERE id=?", (did,)).fetchone()[0]
        row = db.execute("SELECT rating FROM coverage WHERE concept_id=?", (cid,)).fetchone()
        rating_by_core[core] = row["rating"]

    ordered_ratings = [rating_by_core[name] for name in _CORES]  # rank 0 (strongest) .. 3
    assert all(r is not None for r in ordered_ratings)
    assert ordered_ratings == sorted(ordered_ratings, reverse=True)
    assert len(set(ordered_ratings)) == len(ordered_ratings)  # strictly monotone, not just non-increasing

    # promote: THE CONFIRMED PLAN-GAP FIX (module docstring above). Without
    # this step, no single ever leaves 'untested', so activate_pair_concepts
    # below activates ZERO pairs even though every core just cleared the
    # census floor with a decisive rating.
    n_promoted = census.promote_proven_singles(db, floor=census.SCREENING_FLOOR)
    assert n_promoted == len(_CORES)
    assert (
        db.execute(
            "SELECT COUNT(*) FROM concepts WHERE status='active' AND json_array_length(cores)=1"
        ).fetchone()[0]
        == len(_CORES)
    )

    # pair-activate: every pair's both cores are now active+rated, so ALL 6
    # activate in one call, each getting a lazily-built deck.
    n_activated = census.activate_pair_concepts(db, max_new=10)
    assert n_activated == 6

    pair_rows = db.execute(
        "SELECT c.id AS cid, (SELECT COUNT(*) FROM decks d WHERE d.concept_id = c.id) AS nd "
        "FROM concepts c WHERE json_array_length(c.cores) = 2 AND c.status = 'active'"
    ).fetchall()
    assert len(pair_rows) == 6
    assert all(row["nd"] == 1 for row in pair_rows)  # exactly one lazily-built deck each

    # activated pairs get standard (40-game) screening scheduling.
    n_pair_games = census.schedule_screening_games(
        db, target_per_concept=census.STANDARD_SCREENING_FLOOR, batch=10_000
    )
    assert n_pair_games > 0
    pair_subject_counts = db.execute(
        "SELECT d.concept_id AS cid, COUNT(*) AS n FROM games g "
        "JOIN decks d ON d.id = g.deck_a_id "
        "JOIN concepts c ON c.id = d.concept_id "
        "WHERE g.status = 'pending' AND json_array_length(c.cores) = 2 "
        "GROUP BY d.concept_id"
    ).fetchall()
    assert len(pair_subject_counts) == 6  # every pair got scheduled subject-side games
    assert all(0 < row["n"] <= census.STANDARD_SCREENING_FLOOR for row in pair_subject_counts)


def test_containment_step4_diff_against_master_is_empty():
    """Plan T10 Step 4, verbatim: `git diff master...HEAD -- <5 paths>` must
    be EMPTY. A non-empty diff means a Phase-1 task violated the
    additive-only containment rule (Global Constraints + Slice Containment
    Strategy) -- covers both ladder-identity files and the not-yet-wired
    watch-loop/cycle/evolution files in one literal check."""
    result = subprocess.run(
        ["git", "diff", "master...HEAD", "--", *_STEP4_DIFF_PATHS],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"git diff failed: {result.stderr}"
    assert result.stdout == "", f"containment violated -- non-empty diff:\n{result.stdout}"


def test_watch_loop_import_graph_pins_cutover_boundary():
    """The narrowed watch loop's import graph must reach EXACTLY the T20 set:
    it MUST import `subscheduler`/`episodes`/`deckdb` (positive pin -- the
    intended cutover graph) and MUST NOT reach any sibling-process/census
    module (`loop_scheduler`/`runner_pool`/`ui_server`/`census`/`builder`/
    `rating`/`loop`) or the retired `dashboard`. Run in a FRESH subprocess,
    not an in-process import: this same pytest session has already imported
    the tournament modules directly via other test files, so in-process
    `sys.modules` is polluted and would prove nothing about
    `factory_watch_once`'s OWN import graph."""
    probe = "\n".join(
        [
            "import sys",
            "import scripts.factory_watch_once",
            f"forbidden = {_TOURNAMENT_LEAVES!r}",
            f"wanted = {_WATCH_LOOP_WANTED_LEAVES!r}",
            "leaves = {m.rsplit('.', 1)[-1] for m in sys.modules "
            "if m.startswith('ptcg.factory.')}",
            "hits = sorted(leaves & set(forbidden))",
            "missing = sorted(set(wanted) - leaves)",
            "print('FORBIDDEN_HITS=' + repr(hits))",
            "print('WANTED_MISSING=' + repr(missing))",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"probe failed: stdout={result.stdout}\nstderr={result.stderr}"
    assert "FORBIDDEN_HITS=[]" in result.stdout, result.stdout
    assert "WANTED_MISSING=[]" in result.stdout, result.stdout


def test_phase1_modules_never_reference_experiments_factory_state():
    """No writes into `experiments/factory` from any new (T1-T9) code path.
    A literal-string grep is sufficient: any write into that tree would have
    to spell the path somewhere in source, and none of these 5 modules
    import anything from the live factory (`cycle`/`evolution`/`dashboard`/
    `deck_matrix`/etc.) that could reach it indirectly either."""
    hits = [
        rel
        for rel in _TOURNAMENT_SRC_FILES
        if "experiments" in (REPO_ROOT / rel).read_text(encoding="utf-8")
    ]
    assert not hits, f"Phase-1 modules referencing experiments/: {hits}"
