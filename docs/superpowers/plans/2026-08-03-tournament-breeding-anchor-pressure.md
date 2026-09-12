# Tournament Breeding Anchor Pressure Implementation Plan

> **REQUIRED SUB-SKILL: superpowers:subagent-driven-development**

**Goal:** Wire real, external strength pressure (the mega-lucario-fighting + HeuristicAgent anchor) into every selection point of the generational champion tournament, so the factory stops breeding in a closed junk pool (crowned champions v0.5–v0.8 scored 0.030–0.100 vs the 0.55/200 anchor bar).

**Architecture:** Four gate changes plus one migration, all inside the existing SQLite tournament pipeline: (1) deck-stage screening games now play *vs the anchor deck* with equal agents and `coverage.rating` becomes win-rate-vs-anchor; (2) a new cheap early floor (`floor_checks`, 0.45/50, heuristic-vs-heuristic) gates entry to CONFIRM; (3) CROWN no longer promotes — it *nominates* a champion-elect whose 0.55/200 anchor verdict now performs (pass) or refuses (fail) the baseline advance, in one transaction; (4) a new `net_checks` head-to-head (0.55/100) gates adoption of each freshly trained value net. A one-time idempotent migration retires the collapsed lineage and reseeds the pool from three proven templates + validated mutations.

**Tech Stack:** Python 3.11, stdlib `sqlite3` (WAL, `BEGIN IMMEDIATE` via `deckdb._write`), pytest, existing `ptcg.factory` modules. No new dependencies.

## Global Constraints (verbatim from spec — binding on every task)

- **Deadline:** competition final-submission window closes **2026-08-16**; this slice completes in ~1–2 days so autonomous runtime remains.
- **Ladder identity files are OUT OF SCOPE / unchanged:** `src/ptcg/submission_main.py` and `src/ptcg/agents/current.py` must show an empty diff at Finish.
- **Windows encoding:** every Python text write (`write_text`, `open`) in this slice's code paths passes `encoding="utf-8"` explicitly.
- **TOCTOU:** every new status-guard read-decide-act sequence is a **single `BEGIN IMMEDIATE` transaction** (`deckdb._write`), per `.claude/rules/single-actor-worker-tests.md` and the `toctou-guard-in-step-functions` lesson.
- **PAUSE is ON** at `experiments/factory/PAUSE` for the whole implementation window (the working tree IS production for the watch loop and the runner/scheduler workers pick up code on restart — see `.claude/rules/factory-resume-probe.md`). The orchestrator touches it before Task 1 and it is removed only at the post-merge go-live rung.
- **Post-merge go-live is an explicit documented rung** (final section), executed by the orchestrator/Brad — it is NOT an implementer task.

## Dispatch shape (repo rule `.claude/rules/dispatch-test-run-directive.md` — settled default)

Implementers run **ONLY their own targeted test file** (the exact `uv run pytest tests/<file> -q` command given in their task). Never the full suite, never Monitor, never `run_in_background`. Full-suite runs are **orchestrator-owned sync points** (after task integration/review, before gates).

## Verified Landmark Table (pre-lock grep receipts — every row grepped 2026-08-03)

| # | Landmark | Location | Verified fact |
|---|----------|----------|---------------|
| 1 | `train_offspring` | `src/ptcg/factory/loop.py:135` | Inserts offspring then `loop_state.set_offspring_status(conn, offspring_id, "queued_for_match")` at **line 175** |
| 2 | `enqueue_confirm_series` | `loop.py:383` | Guard `offspring["status"] not in ("matching","confirming")` at line 445; `deck_id is None` raise at 447–451; single `deckdb._write` txn |
| 3 | `resolve_confirm` | `loop.py:486` | `CONFIRM_GAMES = 200` (line 373); wr `>= 0.50` survives |
| 4 | `CROWN_GAMES_PER_PAIR` | `loop.py:563` | `= 200` |
| 5 | `_ELIGIBLE_CROWN_SURVIVORS_QUERY` | `loop.py:573–578` | `status='survivor' AND id NOT IN (SELECT offspring_id FROM baselines WHERE offspring_id IS NOT NULL) ORDER BY id` |
| 6 | `resolve_crown` | `loop.py:668` | Crowns IMMEDIATELY today: `INSERT INTO baselines(...)` + `meta('baseline_version', ...)` at lines 756–764; losers `status='trashed'` at 766–770; returns `new_version` |
| 7 | `eligible_crown_survivors` | `loop.py:581` | Read-only helper exposing landmark 5 for the scheduler |
| 8 | `_OFFSPRING_MATCH_RESULTS_QUERY` / fixed side | `loop.py:204–211` | `agent_version_a` is ALWAYS the offspring in match/confirm games; `winner==0` = offspring won |
| 9 | offspring status domain | `src/ptcg/factory/deckdb.py:104–106` | CHECK: `('training','queued_for_match','matching','confirming','trashed','survivor')` — LOCKED, this plan adds no status value |
| 10 | `games` DDL | `deckdb.py:77–86` | `purpose TEXT NOT NULL DEFAULT 'screening'` has **NO CHECK constraint** → new purposes `'floor'`/`'netcheck'` are additive-safe; `status` CHECK is `('pending','claimed','done')` |
| 11 | `deckdb._write` | `deckdb.py:43` | `BEGIN IMMEDIATE`…COMMIT/ROLLBACK wrapper; **cannot nest** — inline statements inside `_apply`, never call another `_write`-opening helper |
| 12 | `deckdb.connect` | `deckdb.py:23–40` | `path.parent.mkdir(parents=True, exist_ok=True)` — virgin-directory-safe |
| 13 | `init_db` / `_DDL_STATEMENTS` | `deckdb.py:62–146` | `anchor_checks` already in the list (lines 113–126); new tables append here too |
| 14 | anchor constants | `src/ptcg/factory/anchor.py:27–31` | `ANCHOR_VERSION="anchor-heuristic-v0"`, `ANCHOR_GAMES=200`, `ANCHOR_BAR=0.55`, `ANCHOR_CONCEPT_ID="anchor-mega-lucario-fighting"`, `ANCHOR_DECK_ID="anchor-mega-lucario-fighting-d0"` |
| 15 | `anchor._ensure_schema` | `anchor.py:48–53` | Additive autocommit `CREATE TABLE IF NOT EXISTS anchor_checks` for live pre-slice DBs |
| 16 | `ensure_anchor_deck` | `anchor.py:56–101` | Registers `'finalist'` concept + deck from `CURRENT_DECK_PATH`; deliberately NO coverage row |
| 17 | `enqueue_anchor_series` | `anchor.py:104–166` | Currently keys on CURRENT BASELINE only; supersede-DELETE of stale pending anchor games at 134–138; priority 1.0 |
| 18 | `resolve_anchor_check` | `anchor.py:176–214` | Resolves any pending row when `n >= games_planned`; verdict UPDATE guarded `AND verdict='pending'`; draws count as champion losses |
| 19 | `anchor_status` | `anchor.py:217–239` | Returns `('absent'|'pending'|'pass'|'fail', done, planned, wr)` |
| 20 | anchor_checks DDL shape | `anchor.py:33–41` | `version TEXT PRIMARY KEY, offspring_id TEXT, deck_id, games_planned, games_done, wins, wr, verdict CHECK('pending','pass','fail'), created_at, resolved_at` |
| 21 | `_drive_offspring` step order | `src/ptcg/factory/loop_scheduler.py:298–355` | Today: resolve_crown → enqueue_crown → anchor enqueue/resolve → MATCH → (match complete → select_optimal_deck + enqueue_confirm) → CONFIRM top-up/resolve → gated TRAIN |
| 22 | `loop_tick` census branch | `loop_scheduler.py:394–404` | `census.schedule_screening_games(conn, founding_agent_version=FOUNDING_AGENT_VERSION)` + throttled rating refresh; returns early (no offspring drive during census) |
| 23 | `_IN_FLIGHT_STATUSES` | `loop_scheduler.py:86` | `("training","queued_for_match","matching","confirming")` — `'training'` already counts toward `PIPELINE_TARGET` |
| 24 | `PIPELINE_TARGET` | `loop_scheduler.py:81` | `= 4`; production scheduler runs `--pipeline-target 4` (T21 go-live) |
| 25 | `schedule_screening_games` | `src/ptcg/factory/census.py:115–222` | Round-robin opponent cursor (`meta['screening_opponent_cursor']`, line 56); both sides `founding_agent_version`; priority `1/(games_played+1)`; single `_write` txn |
| 26 | `_CANDIDATES_QUERY` | `census.py:86–93` | No `concepts.status` filter today (culled concepts would be re-scheduled — Task 4 adds the filter) |
| 27 | `census_complete` | `census.py:225–242` | Counts ALL single-core coverage rows regardless of `concepts.status` (Task 4 adds `status IN ('untested','active')`) |
| 28 | `SCREENING_FLOOR` | `census.py:46` | `= 15` |
| 29 | `promote_proven_singles` | `census.py:464–503` | Promotes `'untested'` singles with `games_played >= floor AND rating IS NOT NULL` — works unchanged once rating = wr-vs-anchor |
| 30 | `activate_pair_concepts` | `census.py:377–447` | Keys on ACTIVE singles' combined rating — post-reseed the old singles are culled, so pair activation goes naturally inert (documented, deliberate) |
| 31 | `refresh_field_ratings` | `src/ptcg/factory/rating.py:42–104` | Bradley-Terry over done `'screening'` games via `bt.fit_ratings`; writes `coverage.rating`/`distinct_opponents` only (never `games_played`) |
| 32 | `_resolve_agent_entry` | `src/ptcg/factory/runner_pool.py:149–225` | Resolution order: in-process map → `ANCHOR_VERSION` special case (line 177, `{"agent_kind":"heuristic","agent_config":{}}`) → offspring row → baselines row; caches into `agent_configs`; raises `UnresolvableAgentVersionError` (line 71) |
| 33 | `_entry_from_offspring_row` | `runner_pool.py:135–146` | offspring `search_config_json` is gene-only; `value_net_ref` folded in as `net_weights` |
| 34 | submission gate reads anchor | `src/ptcg/factory/subscheduler.py:363–372` | `anchor.anchor_status(conn, baseline["version"])`; any verdict ≠ `'pass'` blocks BOTH mark-trigger and daily-floor probe — unchanged by this plan; the pass-path re-key in Task 7 keeps it working for future crowns |
| 35 | `current_baseline` / `set_founding_baseline` | `src/ptcg/factory/loop_state.py:70 / 39` | `meta['baseline_version']` is current-ness; founding config JSON at `meta['founding_agent_config']` (incl. `net_weights`) |
| 36 | `crown_baseline` | `loop_state.py:148–178` | Pure version/baseline primitive — NOT called by `resolve_crown` (statements inlined there); untouched by this plan |
| 37 | `bump_minor` | `src/ptcg/factory/candidates.py:36–38` | `v{major}.{minor+1}` |
| 38 | `validate_deck` | `src/ptcg/decks/validate.py:22` | `def validate_deck(deck: list[int]) -> list[str]` — empty list = legal |
| 39 | `mutate_deck` | `src/ptcg/factory/breeding.py:187–199` | `mutate_deck(rng, cards) -> list | None`; every rule goes through `deck_matrix.apply_rule`, which already rejects illegal outputs (`deck_matrix.py:135–137`) — Task 10 still re-validates explicitly per spec ("existing `validate_deck` legality oracle") |
| 40 | template deck CSVs | `src/ptcg/decks/candidates/` | `mega-lucario-fighting.csv`, `mega-starmie-water.csv`, `mega-starmie-water-density20.csv` all exist (globbed) |
| 41 | `CURRENT_DECK_PATH` | `src/ptcg/agents/current.py:13` | `Path("src/ptcg/decks/candidates/mega-lucario-fighting.csv")` (repo-cwd-relative; tests run from repo root) |
| 42 | `PerDeckNetTrainer` | `src/ptcg/factory/trainers.py:31` | `prepare_data(self, cycle: int) -> Path` (line 52), `train(self, data_path: Path, cycle: int) -> Path` (line 62) |
| 43 | production trainer wiring | `scripts/factory_tournament_scheduler.py:75–81, 118–120` | `trainer_factory = lambda deck: PerDeckNetTrainer(deck=Path(deck))`; `loop_tick(conn, rng, trainer_factory, now_fn(), pipeline_target=...)` |
| 44 | test DB fixture pattern | `tests/test_factory_anchor.py:17–20` | `_connect(tmp_path)`: `deckdb.connect(tmp_path/"t.db")` + `deckdb.init_db(conn)` |
| 45 | offspring `value_net_ref` write-once | memory 2026-07-31 + `loop.py:169–174` | Confirmed write-once TODAY; Task 8's reject-path UPDATE is a **documented deliberate divergence** (single write, inside the resolution txn, before any other game type references the offspring id) |
| 46 | `write_deck_csv` | `src/ptcg/factory/deck_matrix.py:21–24` | `encoding="utf-8"`, `mkdir(parents=True)` — virgin-dir safe |
| 47 | spec file | `docs/superpowers/specs/2026-08-03-tournament-breeding-anchor-pressure-design.md` | All numbers cited below re-read from it: floor 0.45/50, crown anchor 0.55/200 (unchanged), net swap 0.55/100, templates = incumbent + starmie-water + density20 |

**Drift found vs spec assumptions (fixed in this plan text):** none blocking. Two notes: (a) the spec's design-2 sentence "heuristic-vs-heuristic (both sides on the baseline agent)" is internally ambiguous — the baseline agent is a search-net agent; this plan implements the spec's operative words *heuristic-vs-heuristic* (both floor sides are `HeuristicAgent`, which is also what makes the 50-game check cheap, per the same spec paragraph). (b) The spec says deck-stage games use "the current baseline agent" both sides — during the census phase (incl. post-reseed re-screening) no baseline may exist yet; the founding `v0.1` config is used until one does (it IS the current baseline config at that point, resolved identically by `runner_pool`).

## Existing-test impact map (each owning task updates these; no task leaves the repo's OWN targeted file red)

| Task | Existing test files it must update |
|------|-----------------------------------|
| 4 | `tests/test_factory_census_schedule.py`, `tests/test_factory_census.py` (census_complete status filter) |
| 5 | `tests/test_factory_rating.py` |
| 6 | `tests/test_factory_loop_crown.py` |
| 7 | `tests/test_factory_anchor.py` |
| 8 | `tests/test_factory_loop_train.py`, `tests/test_factory_loop_confirm.py` |
| 9 | `tests/test_factory_loop_scheduler.py` |
| 3 | `tests/test_factory_runner_pool.py` |

Task 9's implementer additionally greps `tests/` for `schedule_screening_games\|refresh_field_ratings\|resolve_crown` to catch sibling assertion sites (constant/behavior-bump grep rule, global CLAUDE.md) and reports any file not in the table above to the orchestrator instead of silently editing it.

---

### Task 1: Early anchor floor module (`floor.py`)

**Files**
- Create: `src/ptcg/factory/floor.py`
- Create: `tests/test_factory_floor.py`
- Modify: `src/ptcg/factory/deckdb.py` (append `floor_checks` DDL to `_DDL_STATEMENTS`, after the `anchor_checks` entry ending line 126)

**Interfaces**
- Consumes: `deckdb._write(conn, fn)`, `anchor.ANCHOR_DECK_ID`, `anchor.ANCHOR_VERSION`, `offspring` table (`status`, `deck_id`), `games` table.
- Produces:
  - `FLOOR_GAMES = 50`, `FLOOR_BAR = 0.45`, `FLOOR_PRIORITY = 0.8`, `FLOOR_VERSION_PREFIX = "floor:"`
  - `floor_version(offspring_id: str) -> str`
  - `enqueue_floor_series(conn: sqlite3.Connection, offspring_id: str, n_games: int = FLOOR_GAMES) -> int`
  - `resolve_floor(conn: sqlite3.Connection, offspring_id: str) -> str`  (returns `'absent' | 'pending' | 'pass' | 'fail'`)
  - `floor_status(conn: sqlite3.Connection, offspring_id: str) -> tuple[str, int, int, float | None]`
  - `_ensure_schema(conn)` (additive, mirrors `anchor._ensure_schema`)

Boundary arithmetic (hand-verified): `0.45 * 50 = 22.5` → pass needs `wins >= 23` (`23/50 = 0.46 >= 0.45` pass; `22/50 = 0.44` fail). There is **no exactly-on-bar integer case** at n=50 — the tests pin 23-pass / 22-fail. Draws (`winner == 2`) count in the denominator only (losses), matching `anchor.py`'s conservative convention. Complexity: `enqueue_floor_series` is O(n_games)=O(50) inserts in one txn; `resolve_floor` is one aggregate over that offspring's floor games — both independent of pool size.

**Steps**

- [ ] Write `tests/test_factory_floor.py`:

```python
"""Tests for the early anchor floor gate (anchor-pressure design 2).

Spec: docs/superpowers/specs/2026-08-03-tournament-breeding-anchor-pressure-design.md
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from ptcg.factory import anchor, deckdb, floor


def _connect(tmp_path: Path) -> sqlite3.Connection:
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    return conn


def _seed_world(conn, *, oid="v0.1.1", status="matching", deck="dOff"):
    """Anchor deck row (fake cards, no CSV read), one offspring with a
    MATCH-picked deck. Founding baseline not needed by floor.py itself."""
    def _apply(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES(?,?,?)",
                  (anchor.ANCHOR_CONCEPT_ID, '["anchor"]', "finalist"))
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES(?,?,?)",
                  (anchor.ANCHOR_DECK_ID, anchor.ANCHOR_CONCEPT_ID, json.dumps([3] * 60)))
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cOff','[\"cOff\"]','active')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES(?, 'cOff', ?)",
                  (deck, json.dumps([3] * 60)))
        c.execute(
            "INSERT INTO offspring(id,parent_baseline_version,search_config_json,"
            "value_net_ref,deck_id,status,created_at) VALUES(?,?,?,?,?,?,?)",
            (oid, "v0.1", "{}", "w.json", deck, status, "2026-08-03T00:00:00+00:00"))
    deckdb._write(conn, _apply)


def _finish_floor_games(conn, oid, wins, total=floor.FLOOR_GAMES):
    """Mark the first `total` pending floor games done: `wins` candidate wins,
    the rest anchor wins."""
    version = floor.floor_version(oid)
    def _apply(c):
        rows = c.execute(
            "SELECT id FROM games WHERE purpose='floor' AND agent_version_a=? "
            "ORDER BY id LIMIT ?", (version, total)).fetchall()
        assert len(rows) == total
        for i, row in enumerate(rows):
            c.execute("UPDATE games SET status='done', winner=? WHERE id=?",
                      (0 if i < wins else 1, row["id"]))
    deckdb._write(conn, _apply)


def test_constants_exact_values():
    assert floor.FLOOR_GAMES == 50
    assert floor.FLOOR_BAR == 0.45
    assert floor.FLOOR_VERSION_PREFIX == "floor:"


def test_init_db_creates_floor_checks_on_virgin_db(tmp_path):
    conn = _connect(tmp_path)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "floor_checks" in names


def test_ensure_schema_upgrades_legacy_db(tmp_path):
    conn = deckdb.connect(tmp_path / "legacy.db")
    def _apply(c):
        for ddl in deckdb._DDL_STATEMENTS:
            if "floor_checks" not in ddl:
                c.execute(ddl)
    deckdb._write(conn, _apply)
    floor._ensure_schema(conn)
    floor._ensure_schema(conn)  # idempotent
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "floor_checks" in names


def test_enqueue_creates_row_and_50_heuristic_games(tmp_path):
    conn = _connect(tmp_path)
    _seed_world(conn)
    assert floor.enqueue_floor_series(conn, "v0.1.1") == 50
    row = conn.execute(
        "SELECT deck_id, games_planned, verdict FROM floor_checks "
        "WHERE offspring_id='v0.1.1'").fetchone()
    assert row["deck_id"] == "dOff"
    assert row["games_planned"] == 50
    assert row["verdict"] == "pending"
    game = conn.execute(
        "SELECT * FROM games WHERE purpose='floor' LIMIT 1").fetchone()
    assert game["deck_a_id"] == "dOff"
    assert game["deck_b_id"] == anchor.ANCHOR_DECK_ID
    assert game["agent_version_a"] == "floor:v0.1.1"
    assert game["agent_version_b"] == anchor.ANCHOR_VERSION
    assert game["priority"] == floor.FLOOR_PRIORITY
    # resumable top-up: a second call enqueues only the shortfall (0 here)
    assert floor.enqueue_floor_series(conn, "v0.1.1") == 0


def test_enqueue_guards(tmp_path):
    conn = _connect(tmp_path)
    _seed_world(conn, status="confirming")
    assert floor.enqueue_floor_series(conn, "v0.1.1") == 0  # wrong status -> no-op
    with pytest.raises(ValueError):
        floor.enqueue_floor_series(conn, "nope")  # missing offspring -> loud


def test_enqueue_requires_deck_id(tmp_path):
    conn = _connect(tmp_path)
    _seed_world(conn, deck="dOff")
    def _apply(c):
        c.execute("UPDATE offspring SET deck_id=NULL WHERE id='v0.1.1'")
    deckdb._write(conn, _apply)
    with pytest.raises(RuntimeError):
        floor.enqueue_floor_series(conn, "v0.1.1")


def test_resolve_pass_at_23_wins(tmp_path):
    conn = _connect(tmp_path)
    _seed_world(conn)
    floor.enqueue_floor_series(conn, "v0.1.1")
    _finish_floor_games(conn, "v0.1.1", wins=23)  # 23/50 = 0.46 >= 0.45
    assert floor.resolve_floor(conn, "v0.1.1") == "pass"
    off = conn.execute("SELECT status FROM offspring WHERE id='v0.1.1'").fetchone()
    assert off["status"] == "matching"  # pass does NOT advance status itself


def test_resolve_fail_at_22_wins_trashes_offspring(tmp_path):
    conn = _connect(tmp_path)
    _seed_world(conn)
    floor.enqueue_floor_series(conn, "v0.1.1")
    _finish_floor_games(conn, "v0.1.1", wins=22)  # 22/50 = 0.44 < 0.45
    assert floor.resolve_floor(conn, "v0.1.1") == "fail"
    off = conn.execute("SELECT status FROM offspring WHERE id='v0.1.1'").fetchone()
    assert off["status"] == "trashed"
    # settled verdict is never recomputed/flipped
    assert floor.resolve_floor(conn, "v0.1.1") == "fail"


def test_resolve_pending_and_absent_shapes(tmp_path):
    conn = _connect(tmp_path)
    _seed_world(conn)
    assert floor.resolve_floor(conn, "v0.1.1") == "absent"
    assert floor.floor_status(conn, "v0.1.1") == ("absent", 0, floor.FLOOR_GAMES, None)
    floor.enqueue_floor_series(conn, "v0.1.1")
    _finish_floor_games(conn, "v0.1.1", wins=10, total=20)  # only 20 of 50 done
    assert floor.resolve_floor(conn, "v0.1.1") == "pending"
    verdict, done, planned, wr = floor.floor_status(conn, "v0.1.1")
    assert (verdict, done, planned, wr) == ("pending", 20, 50, None)


def test_draws_count_as_losses(tmp_path):
    conn = _connect(tmp_path)
    _seed_world(conn)
    floor.enqueue_floor_series(conn, "v0.1.1")
    version = floor.floor_version("v0.1.1")
    def _apply(c):
        rows = c.execute(
            "SELECT id FROM games WHERE purpose='floor' AND agent_version_a=? "
            "ORDER BY id", (version,)).fetchall()
        for i, row in enumerate(rows):
            # 22 wins, 28 draws -> wr 0.44 -> fail (draw is not a win)
            c.execute("UPDATE games SET status='done', winner=? WHERE id=?",
                      (0 if i < 22 else 2, row["id"]))
    deckdb._write(conn, _apply)
    assert floor.resolve_floor(conn, "v0.1.1") == "fail"


def test_enqueue_survives_concurrent_calls(tmp_path):
    """Interleaved-mutation receipt (.claude/rules/single-actor-worker-tests.md):
    two threads on two connections both enqueue; the loser's count-read runs
    after the winner's COMMIT, so the series is never doubled."""
    db = tmp_path / "t.db"
    conn = deckdb.connect(db)
    deckdb.init_db(conn)
    _seed_world(conn)
    results = []
    def _call():
        c = deckdb.connect(db)
        results.append(floor.enqueue_floor_series(c, "v0.1.1"))
    t1 = threading.Thread(target=_call)
    t2 = threading.Thread(target=_call)
    t1.start(); t2.start(); t1.join(); t2.join()
    total = conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='floor'").fetchone()[0]
    assert total == 50
    # BEGIN IMMEDIATE serializes: winner enqueues 50, loser reads the
    # committed 50 and tops up 0 -- never 100 from two 50-game passes.
    assert sorted(results) == [0, 50]
    # exactly one floor_checks row either way
    n = conn.execute("SELECT COUNT(*) FROM floor_checks").fetchone()[0]
    assert n == 1


def test_resolve_survives_concurrent_calls(tmp_path):
    db = tmp_path / "t.db"
    conn = deckdb.connect(db)
    deckdb.init_db(conn)
    _seed_world(conn)
    floor.enqueue_floor_series(conn, "v0.1.1")
    _finish_floor_games(conn, "v0.1.1", wins=23)
    verdicts = []
    def _call():
        c = deckdb.connect(db)
        verdicts.append(floor.resolve_floor(c, "v0.1.1"))
    t1 = threading.Thread(target=_call)
    t2 = threading.Thread(target=_call)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert verdicts == ["pass", "pass"]
    row = conn.execute(
        "SELECT resolved_at FROM floor_checks WHERE offspring_id='v0.1.1'").fetchone()
    assert row["resolved_at"] is not None
```

- [ ] Run it and watch it fail (module doesn't exist): `uv run pytest tests/test_factory_floor.py -q`
- [ ] Implement `src/ptcg/factory/floor.py`:

```python
"""Early anchor floor gate (anchor-pressure design 2).

A candidate offspring must reach FLOOR_BAR win rate over FLOOR_GAMES games
against the anchor deck BEFORE it may enter CONFIRM (and therefore playoffs/
CROWN). Floor games are heuristic-vs-heuristic -- `agent_version_a` is the
sentinel `floor:<offspring_id>` and `agent_version_b` is the anchor version,
BOTH resolved to `HeuristicAgent` by `runner_pool._resolve_agent_entry` --
so the 50-game check costs heuristic-game time, not search-game time, and
measures the DECK (D3: deck quality is the primary failure mode).

Draws (`winner == 2`) count as candidate losses (anchor.py convention).
Boundary: 23/50 = 0.46 passes, 22/50 = 0.44 fails (no exact-bar case at n=50).

Every read-decide-act sequence here is ONE `deckdb._write` (`BEGIN
IMMEDIATE`) transaction (Pattern SQLITE-TXN,
`.claude/rules/single-actor-worker-tests.md`).
"""
from __future__ import annotations

import datetime as dt
import sqlite3

from ptcg.factory import anchor, deckdb

FLOOR_GAMES = 50
FLOOR_BAR = 0.45
FLOOR_PRIORITY = 0.8  # below anchor's 1.0, above match/confirm/crown's 0.0
FLOOR_VERSION_PREFIX = "floor:"

_FLOOR_CHECKS_DDL = (
    "CREATE TABLE IF NOT EXISTS floor_checks("
    "offspring_id TEXT PRIMARY KEY, deck_id TEXT NOT NULL, "
    "games_planned INTEGER NOT NULL, games_done INTEGER NOT NULL DEFAULT 0, "
    "wins INTEGER NOT NULL DEFAULT 0, wr REAL, "
    "verdict TEXT NOT NULL DEFAULT 'pending' "
    "CHECK(verdict IN ('pending','pass','fail')), "
    "created_at TEXT NOT NULL, resolved_at TEXT)"
)

_FLOOR_RESULTS_QUERY = (
    "SELECT SUM(CASE WHEN winner = 0 THEN 1 ELSE 0 END) AS wins, "
    "COUNT(*) AS n FROM games "
    "WHERE purpose='floor' AND status='done' AND agent_version_a = ?"
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Additive upgrade for a live pre-slice DB (mirrors anchor._ensure_schema).
    Idempotent; virgin DBs get the table from init_db."""
    conn.execute(_FLOOR_CHECKS_DDL)


def floor_version(offspring_id: str) -> str:
    """The sentinel agent-version string floor games carry on the candidate
    side. Resolved to HeuristicAgent by runner_pool; also the key the results
    aggregate filters on."""
    return f"{FLOOR_VERSION_PREFIX}{offspring_id}"


def enqueue_floor_series(
    conn: sqlite3.Connection, offspring_id: str, n_games: int = FLOOR_GAMES
) -> int:
    """Ensure a floor_checks row + a full n_games series exists for this
    offspring; returns games enqueued this call. Resumable top-up (counts the
    shortfall, mirrors anchor.enqueue_anchor_series). No-ops unless the
    offspring is at status 'matching' with a MATCH-picked deck_id; raises on
    a missing offspring or missing deck_id (caller-ordering bug)."""
    _ensure_schema(conn)

    def _apply(c: sqlite3.Connection) -> int:
        off = c.execute(
            "SELECT status, deck_id FROM offspring WHERE id=?", (offspring_id,)
        ).fetchone()
        if off is None:
            raise ValueError(
                f"enqueue_floor_series: no offspring row with id={offspring_id!r}"
            )
        if off["status"] != "matching":
            return 0
        if off["deck_id"] is None:
            raise RuntimeError(
                f"enqueue_floor_series: offspring {offspring_id!r} has no deck_id "
                "set -- run select_optimal_deck first"
            )
        row = c.execute(
            "SELECT verdict FROM floor_checks WHERE offspring_id=?", (offspring_id,)
        ).fetchone()
        if row is not None and row["verdict"] != "pending":
            return 0
        if row is None:
            c.execute(
                "INSERT INTO floor_checks(offspring_id, deck_id, games_planned, "
                "created_at) VALUES(?,?,?,?)",
                (offspring_id, off["deck_id"], n_games, _now()),
            )
        version = floor_version(offspring_id)
        existing = c.execute(
            "SELECT COUNT(*) FROM games WHERE purpose='floor' AND agent_version_a=?",
            (version,),
        ).fetchone()[0]
        remaining = max(0, n_games - existing)
        for _ in range(remaining):
            c.execute(
                "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, "
                "agent_version_b, purpose, priority, status) "
                "VALUES (?, ?, ?, ?, 'floor', ?, 'pending')",
                (off["deck_id"], anchor.ANCHOR_DECK_ID, version,
                 anchor.ANCHOR_VERSION, FLOOR_PRIORITY),
            )
        return remaining

    return deckdb._write(conn, _apply)


def resolve_floor(conn: sqlite3.Connection, offspring_id: str) -> str:
    """Resolve the floor verdict once the series is fully done. Returns
    'absent' (no check row), 'pending' (series incomplete or already pending),
    'pass', or 'fail'. On a fresh 'fail' the offspring is trashed in the SAME
    transaction (the read-decide-act atomicity the spec mandates); a settled
    verdict is never recomputed."""
    _ensure_schema(conn)

    def _apply(c: sqlite3.Connection) -> str:
        row = c.execute(
            "SELECT games_planned, verdict FROM floor_checks WHERE offspring_id=?",
            (offspring_id,),
        ).fetchone()
        if row is None:
            return "absent"
        if row["verdict"] != "pending":
            return row["verdict"]
        agg = c.execute(_FLOOR_RESULTS_QUERY, (floor_version(offspring_id),)).fetchone()
        n = agg["n"] or 0
        if n < row["games_planned"]:
            return "pending"
        wins = agg["wins"] or 0
        wr = wins / n
        verdict = "pass" if wr >= FLOOR_BAR else "fail"
        cur = c.execute(
            "UPDATE floor_checks SET games_done=?, wins=?, wr=?, verdict=?, "
            "resolved_at=? WHERE offspring_id=? AND verdict='pending'",
            (n, wins, wr, verdict, _now(), offspring_id),
        )
        if cur.rowcount == 1 and verdict == "fail":
            c.execute(
                "UPDATE offspring SET status='trashed' WHERE id=? AND status='matching'",
                (offspring_id,),
            )
        return verdict

    return deckdb._write(conn, _apply)


def floor_status(
    conn: sqlite3.Connection, offspring_id: str
) -> tuple[str, int, int, float | None]:
    """Read-only gate evidence: (verdict, done, planned, wr). 'absent' when no
    row exists. All shapes first-class (provenance-shaped-optional-fields)."""
    _ensure_schema(conn)
    row = conn.execute(
        "SELECT games_planned, games_done, wr, verdict FROM floor_checks "
        "WHERE offspring_id=?",
        (offspring_id,),
    ).fetchone()
    if row is None:
        return ("absent", 0, FLOOR_GAMES, None)
    if row["verdict"] != "pending":
        return (row["verdict"], row["games_done"], row["games_planned"], row["wr"])
    done = conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='floor' AND status='done' "
        "AND agent_version_a=?",
        (floor_version(offspring_id),),
    ).fetchone()[0]
    return ("pending", done, row["games_planned"], None)
```

- [ ] Append the same DDL string to `deckdb._DDL_STATEMENTS` (a new triple-quoted entry after the `anchor_checks` one, identical column list — keep the two copies textually equivalent the way `anchor.py` does).
- [ ] Targeted test to green: `uv run pytest tests/test_factory_floor.py -q`
- [ ] Quick regression of the DDL-touching neighbor: `uv run pytest tests/test_factory_deckdb.py -q`
- [ ] Commit: `git add src/ptcg/factory/floor.py tests/test_factory_floor.py src/ptcg/factory/deckdb.py` then `git commit -m "feat(factory): early anchor floor gate module (0.45/50, heuristic-vs-heuristic)"`

---

### Task 2: Validated net-swap module (`netcheck.py`)

**Files**
- Create: `src/ptcg/factory/netcheck.py`
- Create: `tests/test_factory_netcheck.py`
- Modify: `src/ptcg/factory/deckdb.py` (append `net_checks` DDL to `_DDL_STATEMENTS`, after Task 1's `floor_checks` entry)

**Interfaces**
- Consumes: `deckdb._write`, `loop_state.current_baseline`, `offspring` table, `meta['founding_agent_config']`, `games` table.
- Produces:
  - `NETCHECK_GAMES = 100`, `NETCHECK_BAR = 0.55`, `NETCHECK_PRIORITY = 0.6`
  - `NETCHECK_CAND_PREFIX = "netcheck-cand:"`, `NETCHECK_INC_PREFIX = "netcheck-inc:"`
  - `cand_version(offspring_id) -> str`, `inc_version(offspring_id) -> str`
  - `enqueue_net_check(conn, offspring_id: str, n_games: int = NETCHECK_GAMES) -> int`
  - `resolve_net_check(conn, offspring_id: str) -> str` (returns `'absent' | 'pending' | 'adopt' | 'reject' | 'auto'`)
  - `net_status(conn, offspring_id) -> tuple[str, int, int, float | None]`
  - `_ensure_schema(conn)`

Design notes (binding):
- Head-to-head isolates the NET: both sides play the offspring's OWN `search_config_json` on the BASELINE's deck (the deck the net was trained for), mirrored; only `net_weights` differs (candidate = offspring `value_net_ref`, incumbent = current baseline's net). Sentinel versions `netcheck-cand:<oid>` / `netcheck-inc:<oid>` are used so the offspring id itself is **never resolved (and therefore never cached by runner workers) before the adoption decision** — this is what makes the reject-path `value_net_ref` UPDATE safe against runner_pool's per-process config cache (landmark 32/45).
- Incumbent derivation: founding baseline → `json.loads(meta['founding_agent_config']).get("net_weights")`; crowned baseline → the winning offspring row's `value_net_ref`.
- Provenance shapes (`.claude/rules/provenance-shaped-optional-fields.md`): if incumbent is `None`, candidate is `None`, or the two refs are equal, there is nothing to compare → verdict `'auto'` immediately (no games), offspring advances to `'queued_for_match'`, and the row still records both refs so the audit trail is complete.
- On `'reject'`: `UPDATE offspring SET value_net_ref = <incumbent>, status='queued_for_match'` in the SAME resolution transaction — the rejection is "logged" as the settled `net_checks` row (identity, wr, verdict, resolved_at). This is the one deliberate divergence from the observed write-once property of `value_net_ref` (landmark 45); it happens exactly once, atomically, before any other game type references the offspring id.
- Boundary arithmetic (hand-verified): `0.55 * 100 = 55` → `55/100 = 0.55` adopts (>=), `54/100 = 0.54` rejects. Draws count as candidate losses.
- Complexity: O(100) inserts per offspring per generation; the aggregate is per-offspring, independent of pool size. ~4–6 offspring/day (PIPELINE_TARGET-bounded) → ≤600 extra search games/day.

**Steps**

- [ ] Write `tests/test_factory_netcheck.py` (same fixture idioms as Task 1: `_connect`, a `_seed_world` that inserts a FOUNDING baseline — `baselines('v0.1', NULL, 'dBase', ...)`, `meta['baseline_version']='v0.1'`, `meta['founding_agent_config']` JSON with `"net_weights": "w_inc.json"` — plus an offspring at `status='training'` with `value_net_ref='w_new.json'`, and a `_finish_netcheck_games(conn, oid, wins, total=100)` helper marking games done with `agent_version_a = netcheck.cand_version(oid)`). Test list (write each fully in the implementer's file; assertions shown are the load-bearing ones):
  - `test_constants_exact_values` — `NETCHECK_GAMES == 100`, `NETCHECK_BAR == 0.55`, both prefixes.
  - `test_init_db_creates_net_checks_on_virgin_db` and `test_ensure_schema_upgrades_legacy_db` (mirror Task 1's).
  - `test_enqueue_creates_row_and_100_mirror_games` — row has `candidate_net_ref='w_new.json'`, `incumbent_net_ref='w_inc.json'`, `deck_id='dBase'`; games are `deck_a_id == deck_b_id == 'dBase'`, `agent_version_a == 'netcheck-cand:v0.1.1'`, `agent_version_b == 'netcheck-inc:v0.1.1'`, `purpose='netcheck'`, `priority == 0.6`; second call returns 0 (top-up).
  - `test_enqueue_noop_unless_training` — offspring at `'matching'` → 0 enqueued, no row.
  - `test_auto_adopt_when_incumbent_missing` — founding config WITHOUT `net_weights` key → `enqueue_net_check` returns 0, row verdict `'auto'`, offspring status `'queued_for_match'`, `value_net_ref` still `'w_new.json'`.
  - `test_auto_adopt_when_refs_equal` — offspring `value_net_ref == 'w_inc.json'` → verdict `'auto'`, advances.
  - `test_resolve_adopt_at_55_wins` — 55/100 → `'adopt'`; offspring status `'queued_for_match'`, `value_net_ref == 'w_new.json'`.
  - `test_resolve_reject_at_54_wins_keeps_incumbent` — 54/100 → `'reject'`; offspring status `'queued_for_match'`, `value_net_ref == 'w_inc.json'` (swapped in the same txn); repeat call returns `'reject'` and never flips.
  - `test_resolve_pending_and_absent_shapes` — mirrors Task 1.
  - `test_draws_count_as_losses` — 54 wins + 46 draws → `'reject'`.
  - `test_enqueue_survives_concurrent_calls` / `test_resolve_survives_concurrent_calls` — same two-thread shape as Task 1; totals: exactly 100 games, exactly one `net_checks` row, exactly one adoption decision (`value_net_ref` deterministic afterwards).
- [ ] Run to fail: `uv run pytest tests/test_factory_netcheck.py -q`
- [ ] Implement `src/ptcg/factory/netcheck.py`:

```python
"""Validated net swaps (anchor-pressure design 4).

A freshly trained value net is adopted for an offspring only if it beats the
incumbent net (the current baseline's net) head-to-head: wr >= NETCHECK_BAR
over NETCHECK_GAMES mirrored games on the baseline's deck, same SearchConfig
on both sides, only `net_weights` differing. Otherwise the incumbent net is
kept and the rejection is recorded in `net_checks` (candidate identity,
head-to-head result, verdict) so the training pipeline stays auditable.

Sentinel versions `netcheck-cand:<oid>` / `netcheck-inc:<oid>` keep the
offspring id itself un-resolved (hence un-cached by runner workers) until the
adoption decision has settled -- see runner_pool._resolve_agent_entry.

Draws count as candidate losses. Boundary: 55/100 = 0.55 adopts, 54/100
rejects. Every read-decide-act sequence is one `deckdb._write` transaction.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

from ptcg.factory import deckdb, loop_state

NETCHECK_GAMES = 100
NETCHECK_BAR = 0.55
NETCHECK_PRIORITY = 0.6
NETCHECK_CAND_PREFIX = "netcheck-cand:"
NETCHECK_INC_PREFIX = "netcheck-inc:"

_NET_CHECKS_DDL = (
    "CREATE TABLE IF NOT EXISTS net_checks("
    "offspring_id TEXT PRIMARY KEY, deck_id TEXT NOT NULL, "
    "candidate_net_ref TEXT, incumbent_net_ref TEXT, "
    "games_planned INTEGER NOT NULL, games_done INTEGER NOT NULL DEFAULT 0, "
    "wins INTEGER NOT NULL DEFAULT 0, wr REAL, "
    "verdict TEXT NOT NULL DEFAULT 'pending' "
    "CHECK(verdict IN ('pending','adopt','reject','auto')), "
    "created_at TEXT NOT NULL, resolved_at TEXT)"
)

_NETCHECK_RESULTS_QUERY = (
    "SELECT SUM(CASE WHEN winner = 0 THEN 1 ELSE 0 END) AS wins, "
    "COUNT(*) AS n FROM games "
    "WHERE purpose='netcheck' AND status='done' AND agent_version_a = ?"
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_NET_CHECKS_DDL)


def cand_version(offspring_id: str) -> str:
    return f"{NETCHECK_CAND_PREFIX}{offspring_id}"


def inc_version(offspring_id: str) -> str:
    return f"{NETCHECK_INC_PREFIX}{offspring_id}"


def _incumbent_net_ref(c: sqlite3.Connection, baseline: sqlite3.Row) -> str | None:
    """Current baseline's net, covering both provenances
    (provenance-shaped-optional-fields): founding -> founding_agent_config's
    net_weights key (may be absent -> None); crowned -> winning offspring's
    value_net_ref (nullable column -> None)."""
    if baseline["offspring_id"] is None:
        row = c.execute(
            "SELECT value FROM meta WHERE key='founding_agent_config'"
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["value"]).get("net_weights")
    off = c.execute(
        "SELECT value_net_ref FROM offspring WHERE id=?", (baseline["offspring_id"],)
    ).fetchone()
    return off["value_net_ref"] if off is not None else None


def enqueue_net_check(
    conn: sqlite3.Connection, offspring_id: str, n_games: int = NETCHECK_GAMES
) -> int:
    """Ensure a net_checks row + series exists for a 'training' offspring;
    returns games enqueued this call (resumable top-up). Degenerate shapes
    (no incumbent net, no candidate net, identical refs) short-circuit to
    verdict 'auto' and advance the offspring -- no games played."""
    _ensure_schema(conn)

    def _apply(c: sqlite3.Connection) -> int:
        off = c.execute(
            "SELECT status, value_net_ref FROM offspring WHERE id=?", (offspring_id,)
        ).fetchone()
        if off is None:
            raise ValueError(
                f"enqueue_net_check: no offspring row with id={offspring_id!r}"
            )
        if off["status"] != "training":
            return 0
        row = c.execute(
            "SELECT verdict, deck_id FROM net_checks WHERE offspring_id=?",
            (offspring_id,),
        ).fetchone()
        if row is not None and row["verdict"] != "pending":
            return 0
        if row is None:
            baseline = loop_state.current_baseline(c)
            if baseline is None:
                raise RuntimeError("enqueue_net_check: no baseline founded yet")
            incumbent = _incumbent_net_ref(c, baseline)
            candidate = off["value_net_ref"]
            if incumbent is None or candidate is None or incumbent == candidate:
                c.execute(
                    "INSERT INTO net_checks(offspring_id, deck_id, candidate_net_ref, "
                    "incumbent_net_ref, games_planned, verdict, created_at, resolved_at) "
                    "VALUES(?,?,?,?,0,'auto',?,?)",
                    (offspring_id, baseline["deck_id"], candidate, incumbent,
                     _now(), _now()),
                )
                c.execute(
                    "UPDATE offspring SET status='queued_for_match' "
                    "WHERE id=? AND status='training'",
                    (offspring_id,),
                )
                return 0
            c.execute(
                "INSERT INTO net_checks(offspring_id, deck_id, candidate_net_ref, "
                "incumbent_net_ref, games_planned, created_at) VALUES(?,?,?,?,?,?)",
                (offspring_id, baseline["deck_id"], candidate, incumbent,
                 n_games, _now()),
            )
            deck_id = baseline["deck_id"]
            planned = n_games
        else:
            deck_id = row["deck_id"]
            planned = c.execute(
                "SELECT games_planned FROM net_checks WHERE offspring_id=?",
                (offspring_id,),
            ).fetchone()[0]
        version_a = cand_version(offspring_id)
        existing = c.execute(
            "SELECT COUNT(*) FROM games WHERE purpose='netcheck' AND agent_version_a=?",
            (version_a,),
        ).fetchone()[0]
        remaining = max(0, planned - existing)
        for _ in range(remaining):
            c.execute(
                "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, "
                "agent_version_b, purpose, priority, status) "
                "VALUES (?, ?, ?, ?, 'netcheck', ?, 'pending')",
                (deck_id, deck_id, version_a, inc_version(offspring_id),
                 NETCHECK_PRIORITY),
            )
        return remaining

    return deckdb._write(conn, _apply)


def resolve_net_check(conn: sqlite3.Connection, offspring_id: str) -> str:
    """Resolve once the series is fully done: wr >= NETCHECK_BAR -> 'adopt'
    (keep the new net), else 'reject' (swap value_net_ref back to the
    incumbent). Either way the offspring advances to 'queued_for_match' in
    the SAME transaction. Settled verdicts never flip."""
    _ensure_schema(conn)

    def _apply(c: sqlite3.Connection) -> str:
        row = c.execute(
            "SELECT games_planned, verdict, incumbent_net_ref FROM net_checks "
            "WHERE offspring_id=?",
            (offspring_id,),
        ).fetchone()
        if row is None:
            return "absent"
        if row["verdict"] != "pending":
            return row["verdict"]
        agg = c.execute(_NETCHECK_RESULTS_QUERY, (cand_version(offspring_id),)).fetchone()
        n = agg["n"] or 0
        if n < row["games_planned"]:
            return "pending"
        wins = agg["wins"] or 0
        wr = wins / n
        verdict = "adopt" if wr >= NETCHECK_BAR else "reject"
        cur = c.execute(
            "UPDATE net_checks SET games_done=?, wins=?, wr=?, verdict=?, "
            "resolved_at=? WHERE offspring_id=? AND verdict='pending'",
            (n, wins, wr, verdict, _now(), offspring_id),
        )
        if cur.rowcount == 1:
            if verdict == "reject":
                c.execute(
                    "UPDATE offspring SET value_net_ref=?, status='queued_for_match' "
                    "WHERE id=? AND status='training'",
                    (row["incumbent_net_ref"], offspring_id),
                )
            else:
                c.execute(
                    "UPDATE offspring SET status='queued_for_match' "
                    "WHERE id=? AND status='training'",
                    (offspring_id,),
                )
        return verdict

    return deckdb._write(conn, _apply)


def net_status(
    conn: sqlite3.Connection, offspring_id: str
) -> tuple[str, int, int, float | None]:
    """Read-only evidence: (verdict, done, planned, wr); 'absent' if no row."""
    _ensure_schema(conn)
    row = conn.execute(
        "SELECT games_planned, games_done, wr, verdict FROM net_checks "
        "WHERE offspring_id=?",
        (offspring_id,),
    ).fetchone()
    if row is None:
        return ("absent", 0, NETCHECK_GAMES, None)
    if row["verdict"] != "pending":
        return (row["verdict"], row["games_done"], row["games_planned"], row["wr"])
    done = conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='netcheck' AND status='done' "
        "AND agent_version_a=?",
        (cand_version(offspring_id),),
    ).fetchone()[0]
    return ("pending", done, row["games_planned"], None)
```

- [ ] Append the same `net_checks` DDL to `deckdb._DDL_STATEMENTS`.
- [ ] Targeted test to green: `uv run pytest tests/test_factory_netcheck.py -q`
- [ ] Quick neighbor regression: `uv run pytest tests/test_factory_deckdb.py -q`
- [ ] Commit: `git add src/ptcg/factory/netcheck.py tests/test_factory_netcheck.py src/ptcg/factory/deckdb.py` then `git commit -m "feat(factory): validated net-swap gate module (0.55/100 head-to-head)"`

---

### Task 3: Runner agent-resolution for floor/netcheck sentinel versions

**Files**
- Modify: `src/ptcg/factory/runner_pool.py` (`_resolve_agent_entry`, lines 149–225; module imports line 60)
- Modify: `tests/test_factory_runner_pool.py` (add resolution tests)

**Interfaces**
- Consumes: `floor.FLOOR_VERSION_PREFIX`, `netcheck.NETCHECK_CAND_PREFIX`, `netcheck.NETCHECK_INC_PREFIX`, `net_checks` + `offspring` tables.
- Produces: `_resolve_agent_entry` handles three new version shapes, inserted immediately AFTER the existing `ANCHOR_VERSION` special case (line 177–184) and BEFORE the offspring-table lookup:

```python
    if version.startswith(floor.FLOOR_VERSION_PREFIX):
        # Early-floor candidate side: heuristic-vs-heuristic by design
        # (anchor-pressure design 2) -- same shape as the anchor entry.
        entry = {"agent_kind": "heuristic", "agent_config": {}}
        agent_configs[version] = entry
        return entry

    if version.startswith(netcheck.NETCHECK_CAND_PREFIX) or version.startswith(
        netcheck.NETCHECK_INC_PREFIX
    ):
        cand_side = version.startswith(netcheck.NETCHECK_CAND_PREFIX)
        prefix = (netcheck.NETCHECK_CAND_PREFIX if cand_side
                  else netcheck.NETCHECK_INC_PREFIX)
        oid = version[len(prefix):]
        off = conn.execute(
            "SELECT search_config_json FROM offspring WHERE id=?", (oid,)
        ).fetchone()
        nc = conn.execute(
            "SELECT candidate_net_ref, incumbent_net_ref FROM net_checks "
            "WHERE offspring_id=?", (oid,)
        ).fetchone()
        if off is None or nc is None:
            raise UnresolvableAgentVersionError(
                f"net-check version {version!r}: missing offspring or net_checks row"
            )
        net_ref = nc["candidate_net_ref"] if cand_side else nc["incumbent_net_ref"]
        config = json.loads(off["search_config_json"])
        if net_ref:
            config = {**config, "net_weights": net_ref}
        entry = {"agent_kind": "search-net", "agent_config": config}
        agent_configs[version] = entry
        return entry
```

Add `floor, netcheck` to the existing `from ptcg.factory import anchor, deckdb` import (line 60). No import cycle: neither new module imports `runner_pool`.

**Steps**

- [ ] Add three tests to `tests/test_factory_runner_pool.py` (reuse that file's existing DB fixture helpers; seed an offspring row + a `net_checks` row via `netcheck._ensure_schema` + direct INSERT):
  - `test_resolve_floor_version_is_heuristic` — `_resolve_agent_entry(conn, "floor:v0.1.1", {})` → `{"agent_kind": "heuristic", "agent_config": {}}`; result cached in the map.
  - `test_resolve_netcheck_sides_differ_only_in_net` — cand side entry config `net_weights == "w_new.json"`, inc side `== "w_inc.json"`, all other config keys identical; both `agent_kind == "search-net"`.
  - `test_resolve_netcheck_missing_row_raises_unresolvable` — no `net_checks` row → `UnresolvableAgentVersionError` (rides the reclaim/poison path, never kills the worker — same contract as landmark 32).
- [ ] Run to fail: `uv run pytest tests/test_factory_runner_pool.py -q`
- [ ] Implement the resolution block above; targeted test to green: `uv run pytest tests/test_factory_runner_pool.py -q`
- [ ] Commit: `git add src/ptcg/factory/runner_pool.py tests/test_factory_runner_pool.py` then `git commit -m "feat(factory): resolve floor:/netcheck-*: sentinel agent versions in runner pool"`

---

### Task 4: Anchor-opponent deck screening (census rework)

**Files**
- Modify: `src/ptcg/factory/census.py` (`schedule_screening_games` lines 115–222, `_CANDIDATES_QUERY` lines 86–93, `census_complete` lines 225–242; DELETE `_FIELD_QUERY` lines 73–77, `_OPPONENT_CURSOR_KEY` line 56, and `_PENDING_PER_CONCEPT_QUERY` stays)
- Modify: `tests/test_factory_census_schedule.py` (rewrite opponent expectations), `tests/test_factory_census.py` (census_complete status filter)

**Interfaces**
- Consumes: `anchor.ANCHOR_DECK_ID`, `anchor.ANCHOR_CONCEPT_ID`, `loop_state.current_baseline`. New imports at top of `census.py`: `from ptcg.factory import deckdb, loop_state` and `from ptcg.factory.anchor import ANCHOR_DECK_ID` (no cycle: `anchor` imports only `deckdb`/`loop_state`/`ptcg.agents.current`).
- Produces: `schedule_screening_games(conn, target_per_concept=SCREENING_FLOOR, batch=200, founding_agent_version="v0.1") -> int` — signature UNCHANGED (loop_scheduler call site untouched), behavior changed:
  1. Opponent is ALWAYS the anchor deck (`deck_b_id = ANCHOR_DECK_ID`); the round-robin cursor and `_FIELD_QUERY` are deleted. If the anchor deck row is absent, return 0 (caller runs `anchor.ensure_anchor_deck` first — Task 9 wires that).
  2. Both sides play the CURRENT BASELINE version when a baseline exists (`loop_state.current_baseline(c)["version"]`), else `founding_agent_version` — both resolvable by `runner_pool` (landmark 32); the spec's "equal agents (current baseline agent both sides)".
  3. `_CANDIDATES_QUERY` gains a `concepts.status` filter so culled/unbuildable/finalist concepts are never scheduled:

```python
_CANDIDATES_QUERY = (
    "SELECT co.concept_id AS concept_id, d.id AS deck_id, co.rating AS rating, "
    "co.games_played AS games_played "
    "FROM coverage co "
    "JOIN concepts c ON c.id = co.concept_id "
    f"{_CANONICAL_DECK_JOIN_SQL} "
    "WHERE co.games_played < ? AND c.status IN ('untested','active') "
    "ORDER BY co.rating ASC NULLS FIRST, co.games_played ASC, co.concept_id ASC "
    "LIMIT ?"
)
```

  4. `census_complete` gains the same `AND c.status IN ('untested','active')` filter — a culled concept must neither be scheduled nor block completion (post-reseed the culled lineage would otherwise deadlock the census if any culled row were under-floor).
  5. Pending-aware deficit accounting, priority `1/(games_played+1)`, and the single-`_write`-txn shape are all preserved verbatim. New body of the loop: for each candidate, `deficit = target - games_played - in_flight.get(cid, 0)`; insert `min(deficit, batch - enqueued)` games `(row["deck_id"], ANCHOR_DECK_ID, agent_version, agent_version, 'screening', priority, 'pending')`.

Complexity: unchanged O(candidates × deficit) inserts per call, bounded by `batch=200`; the candidate query is one indexed scan with LIMIT, never a full 332k-row concepts scan (the status filter uses `ix_concepts_status`).

**Steps**

- [ ] Update `tests/test_factory_census_schedule.py` FIRST (red): every opponent assertion becomes `deck_b_id == anchor.ANCHOR_DECK_ID`; add:
  - `test_screening_opponent_is_always_anchor` (seed 3 buildable concepts + anchor deck row; all enqueued games have `deck_b_id == ANCHOR_DECK_ID` and equal `agent_version_a == agent_version_b`).
  - `test_screening_noop_without_anchor_deck` (no anchor row → returns 0, zero games).
  - `test_screening_uses_baseline_version_when_founded` (seed founding baseline `v0.1` via direct INSERTs → `agent_version_a == "v0.1"`; then seed a crowned `v0.2` baseline row + `meta['baseline_version']='v0.2'` → new games carry `"v0.2"`).
  - `test_culled_concepts_never_scheduled_and_never_block_census` (a `'culled'` concept with `games_played=0` gets no games; `census_complete` is True when only culled rows are under-floor).
  - Keep/adapt the existing pending-aware-deficit and batch-cap tests (mechanics unchanged).
- [ ] Run to fail: `uv run pytest tests/test_factory_census_schedule.py -q`
- [ ] Implement the census.py changes; delete dead cursor code and its tests.
- [ ] Targeted green: `uv run pytest tests/test_factory_census_schedule.py -q` and `uv run pytest tests/test_factory_census.py -q`
- [ ] Commit: `git add src/ptcg/factory/census.py tests/test_factory_census_schedule.py tests/test_factory_census.py` then `git commit -m "feat(factory): deck screening plays vs anchor with equal agents; culled concepts excluded"`

---

### Task 5: `coverage.rating` becomes win-rate-vs-anchor

**Files**
- Modify: `src/ptcg/factory/rating.py` (full-body rework of `refresh_field_ratings`; drop the `bt` import — `bt.py` itself is untouched)
- Modify: `tests/test_factory_rating.py` (rewrite for wr semantics)

**Interfaces**
- Produces: `refresh_field_ratings(conn, scope_concept_ids: list[str] | None = None) -> int` — signature UNCHANGED (loop_scheduler's two call sites untouched). New semantics: for every concept with ≥1 done `'screening'` game whose `deck_b_id == ANCHOR_DECK_ID`, write `coverage.rating = wins / n` (winner==0 only; draws in denominator) and `distinct_opponents = 1`. Returns the number of concepts written. Games not against the anchor (the entire pre-slice mirror history) are excluded by the `deck_b_id` filter, so stale BT-scale numbers are never re-derived — and the reseed migration culls the concepts that carry them (Task 10).

```python
_ANCHOR_SCREENING_QUERY = (
    "SELECT g.winner, deck_a.concept_id AS ca "
    "FROM games g JOIN decks deck_a ON g.deck_a_id = deck_a.id "
    "WHERE g.status = 'done' AND g.purpose = 'screening' AND g.deck_b_id = ?"
)

def refresh_field_ratings(conn, scope_concept_ids=None):
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

    def _apply(c):
        for cid, total in n.items():
            c.execute(
                "UPDATE coverage SET rating = ?, distinct_opponents = 1 "
                "WHERE concept_id = ?",
                (wins.get(cid, 0) / total, cid),
            )

    deckdb._write(conn, _apply)
    return len(n)
```

Imports: `from ptcg.factory.anchor import ANCHOR_CONCEPT_ID, ANCHOR_DECK_ID`. Docstring must state the SCALE CHANGE loudly: `coverage.rating` is now in [0,1] (wr-vs-anchor); every ordering consumer (`loop._TOP_FIELD_QUERY`, `loop_scheduler._BEST_CENSUS_DECK_QUERY`, `census.activate_pair_concepts`, `subscheduler._BEST_ACTIVE_DECKS_QUERY`, `census.promote_proven_singles`'s `rating IS NOT NULL` gate) is scale-agnostic (pure ORDER BY / NULL checks) — verified by grep, no other consumer does arithmetic on it except `activate_pair_concepts`' `rating_a + rating_b` sum, which remains a valid ordering key on [0,2]. Note a draw-only concept now gets `rating = 0.0` (not NULL) — a wr of zero is a real, decisive "does not beat the anchor" measurement, unlike BT where draws carried no signal. Complexity: O(done anchor-screening games) tally + O(concepts written) UPDATEs, one txn.

**Steps**

- [ ] Rewrite `tests/test_factory_rating.py` (red first): `test_rating_is_wr_vs_anchor` (7 wins + 2 losses + 1 draw over 10 games → `rating == 0.7`); `test_non_anchor_games_excluded` (legacy mirror game rows don't move the rating); `test_scope_filter_respected`; `test_draw_only_concept_rates_zero`; `test_rating_write_is_column_scoped` (keep the existing field-by-field-merge test shape: `games_played` never touched).
- [ ] Run to fail: `uv run pytest tests/test_factory_rating.py -q`
- [ ] Implement; targeted green: `uv run pytest tests/test_factory_rating.py -q`
- [ ] Commit: `git add src/ptcg/factory/rating.py tests/test_factory_rating.py` then `git commit -m "feat(factory): coverage.rating = win-rate-vs-anchor (replaces internal Bradley-Terry)"`

---

### Task 6: CROWN nominates a champion-elect (no immediate promotion)

**Files**
- Modify: `src/ptcg/factory/loop.py` (`resolve_crown` lines 668–774, `_ELIGIBLE_CROWN_SURVIVORS_QUERY` lines 573–578; add `from ptcg.factory import anchor` to the imports at line 25)
- Modify: `tests/test_factory_loop_crown.py`

**Interfaces**
- `_ELIGIBLE_CROWN_SURVIVORS_QUERY` gains one exclusion line (after the baselines NOT-IN):

```sql
AND id NOT IN (SELECT offspring_id FROM anchor_checks WHERE offspring_id IS NOT NULL)
```

  so a nominated elect (pending, passed, or failed) never re-enters a round-robin. `eligible_crown_survivors`, `enqueue_crown_round_robin`, and `resolve_crown` each call `anchor._ensure_schema(conn)` before touching the query (legacy-DB safety; production already has the table).
- `resolve_crown(conn) -> str | None` — return value is now the **champion-elect offspring id** (was: the new baseline version). Two body changes, everything else (round-robin aggregation, tie-break, loser-trashing) verbatim:
  1. New guard right after the `len(eligible) < 2` check, inside the same txn — at most ONE pending elect at a time (prevents two concurrent elects double-crowning):

```python
        pending_elect = c.execute(
            "SELECT COUNT(*) FROM anchor_checks ac "
            "JOIN offspring o ON o.id = ac.version WHERE ac.verdict='pending'"
        ).fetchone()[0]
        if pending_elect:
            return None
```

  2. Replace the `INSERT INTO baselines(...)` + `meta('baseline_version')` block (old lines 756–764) with a pending anchor-check NOMINATION, keyed by the offspring id on BOTH `version` and `offspring_id` (the elect-row signature Task 7 keys on — baseline versions are `v0.G`, offspring ids `v0.G.k`, so the two column values are equal ONLY on elect rows):

```python
        c.execute(
            "INSERT INTO anchor_checks(version, offspring_id, deck_id, "
            "games_planned, created_at) VALUES(?,?,?,?,?)",
            (best_id, best_id, deck_by_id[best_id], anchor.ANCHOR_GAMES, _now()),
        )
```

  Losers are still trashed in the same txn; the winner stays `'survivor'` and is excluded from future eligibility by the new anchor_checks join. The `current`/`bump_minor` lookup moves to Task 7's resolution (delete it here). Docstring rewritten to state: *CROWN nominates; the anchor verdict promotes (design 3).*

**Steps**

- [ ] Update `tests/test_factory_loop_crown.py` (red first):
  - Existing "crowns the best survivor" tests become "nominates": assert `resolve_crown` returns the best offspring id, `baselines` has NO new row, `meta['baseline_version']` unchanged, a pending `anchor_checks` row exists with `version == offspring_id == best_id`, `deck_id == the winner's optimal deck`, `games_planned == 200`; losers `'trashed'`; winner still `'survivor'`.
  - `test_nominated_elect_excluded_from_next_round_robin` — after nomination, insert two fresh survivors; `eligible_crown_survivors` excludes the elect.
  - `test_no_second_nomination_while_elect_pending` — with a pending elect and 2 fresh survivors, `resolve_crown` returns None and enqueues nothing.
  - `test_resolve_crown_survives_concurrent_calls` — keep, but the "exactly one caller performs the transition" assertion now counts `anchor_checks` elect rows (== 1), not baselines rows.
- [ ] Run to fail: `uv run pytest tests/test_factory_loop_crown.py -q`
- [ ] Implement; targeted green: `uv run pytest tests/test_factory_loop_crown.py -q`
- [ ] Commit: `git add src/ptcg/factory/loop.py tests/test_factory_loop_crown.py` then `git commit -m "feat(factory): CROWN nominates a champion-elect; promotion deferred to anchor verdict"`

---

### Task 7: Anchor verdict performs (or refuses) the promotion

**Files**
- Modify: `src/ptcg/factory/anchor.py` (`enqueue_anchor_series` lines 104–166, `resolve_anchor_check` lines 176–214; add `from ptcg.factory.candidates import bump_minor` import)
- Modify: `tests/test_factory_anchor.py`

**Interfaces**
- `enqueue_anchor_series(conn) -> int` — generalized from "current baseline only" to "every pending check", one `_write` txn:
  1. Backfill (unchanged intent): if the CURRENT baseline has no `anchor_checks` row, insert one keyed by its version (covers go-live/founding baselines exactly as today).
  2. Supersede-DELETE reworked: `DELETE FROM games WHERE purpose='anchor' AND status='pending' AND agent_version_a NOT IN (SELECT version FROM anchor_checks WHERE verdict='pending')` — elect series are never collateral damage.
  3. Top-up EVERY pending row's series to `games_planned`: `INSERT ... VALUES (row["deck_id"], ANCHOR_DECK_ID, row["version"], ANCHOR_VERSION, 'anchor', 1.0, 'pending')`. For an elect row `agent_version_a` is the offspring id — resolvable via the offspring table (landmark 32); for a baseline row it is the baseline version — resolvable via baselines/meta. Complexity: O(pending rows × shortfall), pending rows ≤ 2 in practice (one baseline backfill + one elect).
- `resolve_anchor_check(conn) -> str | None` — same aggregate/verdict math (110/200 = 0.55 passes; draws are losses), plus the promotion logic inside the SAME `_write` txn (this is the design-3 gate — spec: single BEGIN IMMEDIATE):

```python
        for row in pending:  # SELECT version, offspring_id, deck_id, games_planned ...
            agg = c.execute(_ANCHOR_RESULTS_QUERY, (row["version"],)).fetchone()
            n = agg["n"] or 0
            if n < row["games_planned"]:
                continue
            wins = agg["wins"] or 0
            wr = wins / n
            verdict = "pass" if wr >= ANCHOR_BAR else "fail"
            elect = (row["offspring_id"] is not None
                     and row["version"] == row["offspring_id"])
            if elect and verdict == "pass":
                current = loop_state.current_baseline(c)
                if current is None:
                    raise RuntimeError("resolve_anchor_check: no baseline founded yet")
                new_version = bump_minor(current["version"])
                c.execute(
                    "INSERT INTO baselines(version, offspring_id, deck_id, crowned_at) "
                    "VALUES (?, ?, ?, ?)",
                    (new_version, row["offspring_id"], row["deck_id"], _now()),
                )
                c.execute(
                    "INSERT OR REPLACE INTO meta(key, value) "
                    "VALUES ('baseline_version', ?)",
                    (new_version,),
                )
                cur = c.execute(
                    "UPDATE anchor_checks SET games_done=?, wins=?, wr=?, "
                    "verdict='pass', resolved_at=?, version=? "
                    "WHERE version=? AND verdict='pending'",
                    (n, wins, wr, _now(), new_version, row["version"]),
                )
            else:
                cur = c.execute(
                    "UPDATE anchor_checks SET games_done=?, wins=?, wr=?, "
                    "verdict=?, resolved_at=? WHERE version=? AND verdict='pending'",
                    (n, wins, wr, verdict, _now(), row["version"]),
                )
                if cur.rowcount == 1 and elect and verdict == "fail":
                    c.execute(
                        "UPDATE offspring SET status='trashed' WHERE id=?",
                        (row["offspring_id"],),
                    )
            if cur.rowcount == 1:
                resolved = verdict
```

  The pass-path **re-keys the row to the new baseline version** so `subscheduler`'s existing `anchor_status(conn, baseline["version"])` gate (landmark 34) reads `'pass'` for the freshly crowned baseline with zero subscheduler changes. The fail path leaves the row keyed by the offspring id — no baseline row, no version bump, no `meta` change; the generation keeps breeding from the incumbent baseline (design 3). Baseline-keyed rows (backfill) behave exactly as today: verdict recorded, no promotion side effects.

**Steps**

- [ ] Update `tests/test_factory_anchor.py` (red first). Keep all still-valid tests (constants, schema upgrade, ensure_anchor_deck family, draws-as-losses, concurrency shapes — re-point the concurrency tests at the new bodies). Add, using a `_seed_founding(conn)` helper (baselines `('v0.1', NULL, 'dBase', ...)` + both meta keys) and `_seed_elect(conn, oid='v0.1.1')` (offspring row `'survivor'` + pending anchor_checks row `version=offspring_id=oid`):
  - `test_enqueue_tops_up_elect_series` — 200 games with `agent_version_a == 'v0.1.1'`, `deck_a_id == elect deck`, `deck_b_id == ANCHOR_DECK_ID`; second call → 0.
  - `test_supersede_delete_spares_pending_checks` — pending games for a version with NO pending check are deleted; the elect's and pending baseline's games survive.
  - **`test_failed_anchor_verdict_does_not_advance_baseline`** (the spec's mandatory ordering test): elect series done at 80/200 wins (0.40 < 0.55) → `resolve_anchor_check` returns `'fail'`; assert `meta['baseline_version'] == 'v0.1'`, `SELECT COUNT(*) FROM baselines == 1`, offspring status `'trashed'`, check row verdict `'fail'` still keyed `'v0.1.1'`.
  - `test_passed_anchor_verdict_crowns_in_same_call` — 120/200 (0.60) → `'pass'`; `meta['baseline_version'] == 'v0.2'`; new baselines row `(offspring_id='v0.1.1', deck_id=elect deck)`; check row re-keyed to `'v0.2'`; `anchor_status(conn, 'v0.2') == ('pass', 200, 200, 0.6)`; offspring status still `'survivor'`.
  - `test_boundary_110_of_200_passes` — 110/200 = 0.55 exactly → pass (hand-verified: `0.55 * 200 = 110`).
  - `test_baseline_backfill_row_never_promotes` — a baseline-keyed pending row resolving `'pass'` records the verdict but inserts no baselines row and leaves `meta` untouched.
- [ ] Run to fail: `uv run pytest tests/test_factory_anchor.py -q`
- [ ] Implement; targeted green: `uv run pytest tests/test_factory_anchor.py -q`
- [ ] Quick neighbor regression (gate consumer): `uv run pytest tests/test_factory_subscheduler.py -q`
- [ ] Commit: `git add src/ptcg/factory/anchor.py tests/test_factory_anchor.py` then `git commit -m "feat(factory): anchor verdict gates promotion -- pass crowns, fail refuses baseline advance"`

---

### Task 8: TRAIN wires the net check; CONFIRM requires a floor pass

**Files**
- Modify: `src/ptcg/factory/loop.py` (`train_offspring` lines 135–176; `enqueue_confirm_series` `_apply` body lines 437–481; add `from ptcg.factory import floor as floor_mod, netcheck` imports)
- Modify: `tests/test_factory_loop_train.py`, `tests/test_factory_loop_confirm.py`

**Interfaces**
- `train_offspring` (2-line change): delete the `loop_state.set_offspring_status(conn, offspring_id, "queued_for_match")` call (line 175 — the offspring now RESTS at the table-default `'training'` while its net check plays; `'training'` already counts toward `PIPELINE_TARGET`, landmark 23) and append `netcheck.enqueue_net_check(conn, offspring_id)` before the return. A crash between insert and enqueue is safe: the scheduler re-drives `enqueue_net_check` for every `'training'` offspring each tick (Task 9), and the row-creation path is idempotent.
- `enqueue_confirm_series` gains the floor gate INSIDE its existing `_write` txn (after the `deck_id is None` raise, before the baseline lookup) — this is the "candidate may not enter confirm without the floor" invariant enforced at the step function per `toctou-guard-in-step-functions`, not only at the scheduler:

```python
        floor_row = c.execute(
            "SELECT verdict FROM floor_checks WHERE offspring_id=?", (offspring_id,)
        ).fetchone()
        if floor_row is None or floor_row["verdict"] != "pass":
            return 0
```

  plus `floor_mod._ensure_schema(conn)` at function top (before the `_write`). CROWN eligibility is transitively floored: `'survivor'` requires CONFIRM, CONFIRM requires the floor pass.

**Steps**

- [ ] Update `tests/test_factory_loop_train.py` (red first): the "sets queued_for_match" assertion becomes "stays `'training'` with a pending `net_checks` row and 100 enqueued netcheck games"; add `test_train_offspring_auto_advances_when_founding_config_has_no_net` (founding config without `net_weights` → offspring lands at `'queued_for_match'` immediately, verdict `'auto'`).
- [ ] Update `tests/test_factory_loop_confirm.py` (red first): add a module-level helper and use it in every existing enqueue test:

```python
def _pass_floor(conn, offspring_id, deck_id="dOff"):
    from ptcg.factory import floor
    floor._ensure_schema(conn)
    def _apply(c):
        c.execute(
            "INSERT INTO floor_checks(offspring_id, deck_id, games_planned, "
            "games_done, wins, wr, verdict, created_at, resolved_at) "
            "VALUES(?,?,50,50,25,0.5,'pass',?,?)",
            (offspring_id, deck_id,
             "2026-08-03T00:00:00+00:00", "2026-08-03T00:00:00+00:00"))
    deckdb._write(conn, _apply)
```

  Add `test_confirm_refused_without_floor_pass` (no row → 0 games, status stays `'matching'`) and `test_confirm_refused_on_floor_fail` (verdict `'fail'` row → 0 games).
- [ ] Run both to fail: `uv run pytest tests/test_factory_loop_train.py tests/test_factory_loop_confirm.py -q`
- [ ] Implement; targeted green: `uv run pytest tests/test_factory_loop_train.py tests/test_factory_loop_confirm.py -q`
- [ ] Commit: `git add src/ptcg/factory/loop.py tests/test_factory_loop_train.py tests/test_factory_loop_confirm.py` then `git commit -m "feat(factory): TRAIN gates on net check; CONFIRM entry requires floor pass"`

---

### Task 9: Scheduler wiring + end-to-end ordering test

**Files**
- Modify: `src/ptcg/factory/loop_scheduler.py` (`_drive_offspring` lines 298–355; `loop_tick` census branch lines 394–404; imports line 45 gain `floor, netcheck`)
- Modify: `tests/test_factory_loop_scheduler.py`

**Interfaces**
- `loop_tick` census branch: insert `anchor.ensure_anchor_deck(conn)` as the first statement inside the `if not census.census_complete(conn):` block (screening now needs the anchor deck registered; idempotent per landmark 16).
- `_drive_offspring` new body (every step delegates to an individually-atomic stage function; the scheduler still owns no new read-modify-write window):

```python
    elect = loop.resolve_crown(conn)
    crown_enqueued = loop.enqueue_crown_round_robin(conn)

    anchor_enqueued = anchor.enqueue_anchor_series(conn)
    anchor_resolved = anchor.resolve_anchor_check(conn)

    net_resolved = 0
    for off in loop_state.list_offspring(conn, "training"):
        netcheck.enqueue_net_check(conn, off["id"])
        if netcheck.resolve_net_check(conn, off["id"]) in ("adopt", "reject", "auto"):
            net_resolved += 1

    matched = 0
    for off in loop_state.list_offspring(conn, "queued_for_match"):
        if loop.enqueue_match_games(conn, off["id"]) > 0:
            matched += 1

    confirm_started = 0
    floor_failed = 0
    for off in loop_state.list_offspring(conn, "matching"):
        if not _match_series_complete(conn, off["id"]):
            continue
        if off["deck_id"] is None:
            loop.select_optimal_deck(conn, off["id"])
        floor.enqueue_floor_series(conn, off["id"])
        verdict = floor.resolve_floor(conn, off["id"])
        if verdict == "pass":
            if loop.enqueue_confirm_series(conn, off["id"]) > 0:
                confirm_started += 1
        elif verdict == "fail":
            floor_failed += 1

    resolved: list[str] = []
    for off in loop_state.list_offspring(conn, "confirming"):
        loop.enqueue_confirm_series(conn, off["id"])
        verdict = loop.resolve_confirm(conn, off["id"])
        if verdict in ("trashed", "survivor"):
            resolved.append(verdict)

    bred = None
    if _in_flight_offspring_count(conn) < pipeline_target:
        bred = loop.train_offspring(conn, rng, trainer_factory, now)

    return {"elect": elect, "crown_enqueued": crown_enqueued,
            "anchor_enqueued": anchor_enqueued, "anchor_resolved": anchor_resolved,
            "net_resolved": net_resolved, "matched": matched,
            "confirm_started": confirm_started, "floor_failed": floor_failed,
            "resolved": resolved, "bred": bred}
```

  (`"crowned"` key is renamed `"elect"` — a nomination, not a promotion.)

**Steps**

- [ ] Update `tests/test_factory_loop_scheduler.py` (red first): re-point dict-key assertions (`crowned` → `elect`, new keys), and add the slice's END-TO-END ORDERING TEST — a single test driving `loop_tick` repeatedly on a seeded post-census DB with a stub trainer, completing games by direct UPDATE between ticks, asserting the full lifecycle in order: TRAIN → `'training'` + netcheck series → (reject at 54/100 → incumbent net kept) → `'queued_for_match'` → MATCH → floor series → (23/50 pass) → CONFIRM → `'survivor'` ×2 → CROWN nominates (no baselines row) → elect anchor series → **fail at 0.40 → `meta['baseline_version']` unchanged and breeding continues (a subsequent tick still breeds from the OLD baseline)** → second elect passes at 0.60 → baseline advances exactly once. This is the integration receipt for spec designs 2+3+4 composing.
- [ ] Run to fail: `uv run pytest tests/test_factory_loop_scheduler.py -q`
- [ ] Implement; targeted green: `uv run pytest tests/test_factory_loop_scheduler.py -q`
- [ ] Grep sweep for sibling assertion sites (constant/behavior-bump rule): `Grep -n "schedule_screening_games\|refresh_field_ratings\|resolve_crown" tests/` — report any file outside the impact map to the orchestrator.
- [ ] Commit: `git add src/ptcg/factory/loop_scheduler.py tests/test_factory_loop_scheduler.py` then `git commit -m "feat(factory): scheduler drives netcheck/floor/elect-anchor pipeline order"`

---

### Task 10: Pool reseed migration (`scripts/reseed_tournament_pool.py`)

**Files**
- Create: `scripts/reseed_tournament_pool.py`
- Create: `tests/test_reseed_tournament_pool.py`

**Interfaces**
- Produces: `run_reseed(conn, rng, log=print) -> dict` plus a CLI `uv run python scripts/reseed_tournament_pool.py --db <path> [--seed 20260803]`. Never invoked by any worker — a one-time, idempotent migration run at the post-merge rung.
- Behavior (all inside ONE `deckdb._write` txn after `init_db` + `ensure_anchor_deck`):
  1. **Cull, never delete** (spec design 5): `UPDATE concepts SET status='culled', reason=<CULL_REASON> WHERE status IN ('active','untested') AND id IN (SELECT concept_id FROM coverage) AND id NOT LIKE 'reseed-%'` — retires the entire old buildable field (actives AND played-but-unpromoted singles) with provenance preserved; dormant pairs (no coverage row) stay `'untested'` but are permanently inert (their cores are culled, and `activate_pair_concepts` requires ACTIVE singles — landmark 30); the anchor (`'finalist'`, no coverage) is untouched by both filters.
  2. **Seed 3 templates** as fresh single-core concepts `('reseed-mega-lucario-fighting', 'reseed-mega-starmie-water', 'reseed-mega-starmie-water-density20')` from the three verified CSVs (landmarks 40/41), each `INSERT OR IGNORE` into `concepts` (`cores=json.dumps([cid])`, `status='untested'`) + `decks` (`id=f"{cid}-sv0"`, `shell_variant=0`) + zeroed `coverage`. Every CSV read passes `encoding="utf-8"`; every deck asserts `len == 60` and `validate_deck(cards) == []` (landmark 38) before insert, raising loudly otherwise.
  3. **Seed mutations**: per template, `MUTATIONS_PER_TEMPLATE = 5` slots, each trying `breeding.mutate_deck(rng, list(cards))` up to `MUTATION_ATTEMPTS = 8` times until `validate_deck(child) == []` (explicit re-validation even though `apply_rule` pre-validates — the spec names `validate_deck` as the oracle, landmark 39); concept id `f"reseed-mut-{sha1(','.join(map(str, sorted(child))))[:12]}"` (content-addressed → same-seed re-runs are pure no-ops via `INSERT OR IGNORE`); a slot with no legal mutation logs and continues (never raises). Pool = 3 + ≤15 decks.
  4. Returns `{"culled": n, "templates_seeded": n, "mutations_seeded": n}` and logs it.
- Idempotency: run 2 with the SAME default `--seed` culls 0 (old rows already `'culled'`, reseed rows excluded by `NOT LIKE`), inserts 0 (content-addressed ids + OR IGNORE). The old `coverage.rating` values on culled rows are deliberately LEFT in place (harmless: every rating consumer filters `status='active'`/`'untested'`, which culled rows no longer match after Task 4's filters) — provenance stays inspectable.
- Post-reseed system behavior (document in the module docstring): new concepts have `games_played=0` → `census_complete` goes False → the scheduler re-enters the census phase and screens the reseeded pool vs the anchor (Task 4) at ≤ 18 concepts × 15 games = **270 games** (hand-verified: 3 + 3×5 = 18; 18 × 15 = 270), then `promote_proven_singles` promotes them with wr ratings and the loop resumes. Complexity: mutation generation is O(3 × 5 × 8) `mutate_deck` calls, each O(60)-ish — trivial.
- Virgin path: `deckdb.connect` mkdirs the parent (landmark 12); `init_db` creates all tables — the script must work against a nonexistent DB path (first-run-bug rule: tested WITHOUT pre-creating the parent).

**Steps**

- [ ] Write `tests/test_reseed_tournament_pool.py` (red first):
  - `test_reseed_on_copy_of_populated_db` — build a mini "production-shaped" DB in `tmp_path` (init_db + 2 active concepts with coverage/decks + 1 untested-with-coverage single + 1 dormant pair without coverage + anchor rows), `shutil.copy` it to `tmp_path/"copy.db"` (**the test's own enforcement of the smoke-against-a-COPY rule — the original file's bytes are asserted unchanged after the run** via `hashlib.sha256(original.read_bytes())` before/after), run `run_reseed` on the copy: old actives + played single → `'culled'` with the reason string; dormant pair untouched; 3 template concepts + decks + coverage exist; ≥1 mutation concept exists; every reseeded deck has 60 cards and `validate_deck == []`.
  - `test_reseed_idempotent_same_seed` — second `run_reseed` with `random.Random(20260803)` again: culled count 0, inserted counts 0, row counts identical.
  - `test_reseed_virgin_db_path` — point the CLI `main(["--db", str(tmp_path / "deep" / "never" / "t.db")])` at a path whose parents DO NOT exist (do not pre-create; the nested-below-tmp_path form from the first-run-bug rule) — script succeeds, tables + reseed rows present.
  - `test_reseed_never_touches_anchor_or_finalists` — anchor concept still `'finalist'`, its deck intact.
  - `test_culled_pool_not_reschedulable` — after reseed, `census.schedule_screening_games` enqueues games ONLY for `reseed-*` concepts (integration receipt with Task 4).
- [ ] Run to fail: `uv run pytest tests/test_reseed_tournament_pool.py -q`
- [ ] Implement `scripts/reseed_tournament_pool.py` (module skeleton — `ROOT`/`sys.path` header copied from `scripts/factory_tournament_scheduler.py:39–41`; imports `argparse, hashlib, json, random, sys`, `from ptcg.decks.validate import validate_deck`, `from ptcg.factory import anchor, deckdb`, `from ptcg.factory.breeding import mutate_deck`; constants `RESEED_PREFIX = "reseed-"`, `CULL_REASON = "reseed-2026-08-03: collapsed lineage culled (D1-D3 anchor diagnostics)"`, `TEMPLATES` list of the three `(cid, ROOT / "src/ptcg/decks/candidates/<name>.csv")` pairs, `MUTATIONS_PER_TEMPLATE = 5`, `MUTATION_ATTEMPTS = 8`; functions `_read_deck_csv(path)` (utf-8, 60-card assert), `_mutation_concept_id(cards)`, `_seed_concept(c, cid, cards)` (validate + 3 OR-IGNORE inserts), `run_reseed(conn, rng, log)` per the behavior spec above, `main(argv=None)` with `--db` required + `--seed` default 20260803).
- [ ] Targeted green: `uv run pytest tests/test_reseed_tournament_pool.py -q`
- [ ] Commit: `git add scripts/reseed_tournament_pool.py tests/test_reseed_tournament_pool.py` then `git commit -m "feat(factory): one-time pool reseed migration -- cull collapsed lineage, seed proven templates"`

---

## Post-merge go-live rung (orchestrator/Brad — NOT an implementer task)

Pre-conditions: all 10 tasks merged to master; full suite green at the merge commit (orchestrator-owned run); `experiments/factory/PAUSE` still present from implementation start.

1. **Restart the long-lived workers** (they never hot-reload; `Running` ≠ current code — `.claude/rules/factory-resume-probe.md`):
   `Stop-ScheduledTask -TaskName ptcg-factory-scheduler; Start-ScheduledTask -TaskName ptcg-factory-scheduler`
   `Stop-ScheduledTask -TaskName ptcg-factory-runner; Start-ScheduledTask -TaskName ptcg-factory-runner`
   (`ptcg-factory-continuous` spawns fresh per firing — no restart needed; `ptcg-factory-ui` untouched by this slice.)
2. **Run the reseed migration against the live DB** (the ONLY time it targets production; PAUSE is still on, workers idle):
   `uv run python scripts/reseed_tournament_pool.py --db "experiments/factory/tournament.db"`
   Verify its printed summary: `culled > 0`, `templates_seeded == 3` (first run), `mutations_seeded >= 1`.
3. **Verify post-merge provenance before unpausing** — the workers must demonstrably be on the merge commit: check `experiments/factory/logs/runner.log` / `watch.log` first lines after restart, and
   `Get-ScheduledTaskInfo -TaskName ptcg-factory-scheduler | Select LastRunTime, LastTaskResult` (expect fresh LastRunTime, result 0 or running).
4. **Remove PAUSE**: delete `experiments/factory/PAUSE`.
5. **Verify next firings healthy** (~30 min window):
   - `watch.log`: paired terminal markers on every firing (strict baseline — `.claude/rules/factory-task-scheduler-liveness.md`).
   - Tournament DB shows the new pipeline moving: `SELECT COUNT(*) FROM games WHERE purpose='screening' AND deck_b_id='anchor-mega-lucario-fighting-d0';` climbing (anchor-opponent screening live), and later `SELECT * FROM floor_checks; SELECT * FROM net_checks;` acquiring rows as offspring flow.
   - No Event-322 wedge fingerprint; `LastTaskResult` clean on all four tasks.
6. **Expectation setting** (spec §7, honest framing): the submission gate stays closed until some future champion-elect passes 0.55/200 — the rescue pair (starmie-searchnet 544.1 / lucario-heuristic 481.7) remains the counted Kaggle pair, and that is an acceptable outcome of this design. The early floor's compute savings are the guaranteed win.

## Spec coverage mapping (self-review receipt)

| Spec section | Plan task(s) |
|---|---|
| Design 1 — anchor-opponent rating (deck stage keys on wr-vs-anchor; offspring MATCH retained) | Tasks 4, 5 (MATCH untouched by design — landmark 8) |
| Design 2 — early anchor floor 0.45/50, heuristic-vs-heuristic, BEGIN IMMEDIATE | Tasks 1, 3, 8 (step-function guard), 9 (drive) |
| Design 3 — anchor-verdict-before-promotion (no bump/advance on fail; generation keeps breeding) | Tasks 6, 7, 9 (ordering integration test) |
| Design 4 — validated net swaps 0.55/100, keep old net + log rejection | Tasks 2, 3, 8, 9 |
| Design 5 — pool reseed: retire-not-delete, 3 templates + validate_deck mutations, idempotent, copy-only tests, virgin path | Task 10 |
| Design 6 — live-exposure management / PAUSE / explicit go-live | Global Constraints + Post-merge rung |
| Spec Constraints — deadline, ladder identity untouched, utf-8, TOCTOU txns, test coverage (interleaved-mutation, provenance-shape, ordering test) | Global Constraints; concurrency tests in Tasks 1/2/6/7; provenance tests in Tasks 2/8; ordering tests in Tasks 7/9 |
| Stochastic-gate note (floor bar must be calibrated against the pool it will actually gate; confirm empirically) | **RESOLVED — measurement complete, bar recalibrated 0.45 -> 0.40.** The empirical check was run against the **RESEEDED** pool (the 0.03–0.10 figures originally cited here are the *collapsed* lineage's — the decks Task 10's reseed CULLS — and could not calibrate a bar applied only to reseeded offspring). `uv run python scripts/measure_floor_distribution.py --games 100`, 11 decks x 100 games heuristic-vs-heuristic vs anchor (1100 games): **pooled wr 0.471 (518/1100), min 0.360, max 0.560** — near parity with the anchor, so the original 0.45 bar sat inside the pool's own distribution (false-fail 0.13–0.72 per deck, ~0.39–0.45 for parity decks). At **0.40**: junk class (<=0.10, the floor's actual target) still fails ~always; parity decks false-fail only 0.04–0.20 per attempt (pass 84–96%), and with 3 re-pick attempts a wrongful trash is negligible. `FLOOR_BAR`/`FLOOR_GAMES` (`src/ptcg/factory/floor.py`) are module constants with no literal duplicates in production code (grep-verified, Pass 2), so the change was two lines plus test/doc updates — no second sweep needed. Boundary now: 20/50 = 0.400 passes (`>=`), 19/50 = 0.38 fails. Post-merge rung step 5 still observes the first real floor rows. |

