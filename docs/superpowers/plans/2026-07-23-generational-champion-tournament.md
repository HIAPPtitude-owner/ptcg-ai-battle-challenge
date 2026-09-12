# Generational Champion Tournament Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Amended 2026-07-23 (plan-review round):** Brad overrode PD-A (harvester KEPT), PD-B (dashboard REPLACED by the UI server), and expanded census scope (pair enumeration in-slice, play-singles-at-founding). Founding net ratified (`value_net_weights_v2.json`). Tasks renumbered T1–T21. The matching spec-side record is the "Plan-review overrides (Brad, 2026-07-23)" section appended to the design spec.

**Goal:** Replace the steady-state co-evolution factory with a generational champion tournament: a SQLite-backed deck field seeded by a Founding Census (all 332,520 concept rows enumerated — 815 single-core + 331,705 pair concepts — with the 815 singles PLAYED at founding), a parallel ISMCTS runner pool, a continuous Baseline-Challenge Loop (TRAIN/MATCH/CONFIRM/CROWN) that crowns a new baseline the moment a challenger proves itself, an independent submission scheduler, and a localhost deck-review UI as the sole status surface.

**Architecture:** A new SQLite database (`experiments/factory/tournament.db`, stdlib `sqlite3`, WAL mode) is the single source of truth for concepts, decks, games, ratings, decisions, offspring, and baselines. A supervisor spawns 4-8 BelowNormal game-runner processes that atomically claim pending games, play them via the existing `arena.runner`/`build_agent` plumbing, and write results back. A tournament-scheduler process enqueues census + loop games, lazily activates pair concepts post-census, and advances offspring through TRAIN/MATCH/CONFIRM/CROWN. The (narrowed) `ptcg-factory-continuous` watch loop hosts the submission scheduler (4.8h marks + 24h daily-floor probe) AND the surviving episode harvester. A `ptcg-factory-ui` localhost `http.server` serves the bottom-10 Remove/Pass review plus the live status page (the static `dashboard.html` is retired at cutover).

**Tech Stack:** Python 3.11+ (stdlib `sqlite3`, `http.server`, `multiprocessing`, `itertools`), `uv` for all invocation, the vendored `cg` engine SDK, existing `src/ptcg/` factory + search + arena code. No new third-party runtime dependency (`torch` stays dev-only for the trainer).

---

## Global Constraints

Every task's requirements implicitly include this section. Values copied verbatim from the locked spec (`docs/superpowers/specs/2026-07-22-generational-champion-tournament-design.md`, latest commit `63a241c`, plus the appended "Plan-review overrides (Brad, 2026-07-23)" section).

- **ISMCTS agents only** — no `HeuristicAgent` anywhere in the tournament (Locked Decision 1). The founding agent `v0.1` is a `search-net` SearchConfig + value net. **Founding net RATIFIED by Brad (2026-07-23): `src/ptcg/search/value_net_weights_v2.json`.**
- **SQLite deck database** via stdlib `sqlite3` (Locked Decision 9) — deliberate departure from JSON ledgers; existing JSON ledgers (`candidates.json`, `matrix.json`, `agent_pool.json`, `deck_pool.json`) are UNAFFECTED and untouched.
- **Founding Census (amended scope, Brad 2026-07-23):** ALL concept rows are enumerated into SQLite this slice — the **815 distinct attacker Pokémon NAMES** (442 basic + 373 evolution — VERIFIED against the engine; distinct *names*, not the 952 cardId printings) PLUS all **C(815,2) = 331,705 pair concepts** (total 332,520 rows). The founding census **PLAYS only the 815 single-core concepts** (~15 screening games each, `815×15 = 12,225` games ≈ 6.1 days @2,000 games/day — fits the ~7-day census box). Playing pairs at founding is INFEASIBLE — executed receipts: `331,705×15 = 4,975,575` games ≈ 2,488 days ≈ **6.8 years** @2,000 games/day (5.45 years even at the 2,500/day best case; ~220 days even at the untested 22,539/day extrapolation from 6 workers × 23 s/game — infeasible at every plausible throughput). Pair space is explored **generationally post-census** via lazy activation "as individual cores prove out" (spec Locked Decision 4, unchanged), ~40 games per activated pair.  Census is the one step exempt from any time box.
- **Deterministic builder:** pure function `concept -> deck` for BOTH single-core and two-core concepts, zero randomness, byte-identical output for the same concept. Satisfies engine legality (`validate_deck` == `[]`) AND Brad's construction bounds: ≤20 energy cards, ≥8 basic Pokémon, ≥4 supporters, ≥4 items, exactly 1 ACE SPEC.
- **Staged series budgets** (Locked Decision 3): census screening ~15 games (one-time exception), standard screening ~40 games/deck (activated pairs + shell variants), MATCH ~15 games × top-30 decks, CONFIRM ~200 games, CROWN round-robin ~200 games/pair.
- **Baseline-Challenge Loop** (supersedes discrete generations): BOOTSTRAP once, then continuous TRAIN/MATCH/CONFIRM/CROWN with no generation boundary. `<50%` CONFIRM win rate → TRASHED (permanent DB record), `>=50%` → SURVIVOR; `>=2` survivors → round-robin CROWN → version bump.
- **Versioning:** base `v0.1`; offspring while `v0.G` is baseline are `v0.G.1..v0.G.k`; each CROWN bumps minor (`v0.1 -> v0.2`).
- **Submission scheduler:** marks every 4.8 hours (5 marks/day, `24 / 4.8 == 5`, aligned to the 5/day cap); upload on baseline-change at a mark; a 24h daily-floor PROBE upload of the next-best unprobed candidate (carries current baseline version tag, does NOT bump version) when 24h elapse with no upload. Champion-pairing guard STAYS RETIRED. Kaggle counts only the **two most-recent** submissions, evicts by **recency not score** (`.claude/rules/platform-mechanics-model.md`).
- **5/day hard cap** (`HARD_DAILY_CAP = 5`, `gate.py:16`) + atomic reservation (`SubmissionCounter.try_reserve`, commit `178043b`) + pre-submit `check_auth()` (commit `9d3242e`) are UNCHANGED and remain in force.
- **Episode harvester SURVIVES cutover (Brad override, 2026-07-23):** `episodes.check_and_harvest` keeps running in the narrowed `ptcg-factory-continuous` watch loop (15-min firings; internally stamp-gated via `harvest_stamp.json`; extract-then-delete; 2GB retention cap — all unchanged). Its meta-anchor CONSUMER (`top_meta_decks` → `inject_meta_anchors` → `deck_pool.json`) is retired with the pool system; extracts are retained as a data asset with a named future hook (see Plan-Review Decisions).
- **Static `dashboard.html` RETIRED at cutover (Brad override, 2026-07-23):** `ptcg-factory-ui` is the SOLE status/review surface; all `dashboard.safe_render` wiring is removed from the watch loop at cutover. Availability covered by AtStartup + watchdog registration of the UI task.
- **`PAUSE`** (`experiments/factory/PAUSE`, full stop) and **`SUBMIT_HOLD`** (`experiments/factory/SUBMIT_HOLD`, uploads-only) govern EVERY new component exactly as they govern the current factory. `SUBMIT_HOLD` is ACTIVE for the entire slice.
- **Ladder identity files** (`src/ptcg/submission_main.py`, `src/ptcg/agents/current.py`) are NOT touched by this design (verify empty `git diff` at Finish).
- **Python defaults:** `uv run` for everything; EVERY text write carries `encoding="utf-8"`; type hints on all signatures; tests live in `tests/` next to the repo's convention (`tests/test_*.py`, importable via `pythonpath = ["src", "."]`); atomic writes via tmp-then-`os.replace` for any remaining shared JSON; keep files <500 lines where feasible.
- **Freeze protocol (operational reminder, not a coded task):** at ~2026-08-14, submit the all-time best two candidates, then set `SUBMIT_HOLD` through the 2026-08-16 final deadline (~2-day convergence buffer).

## Plan-Review Decisions (Brad, 2026-07-23 — RATIFIED at the plan-review gate)

Recorded here AND appended to the spec ("Plan-review overrides (Brad, 2026-07-23)") so spec and plan cannot silently contradict each other.

- **PD-A OVERRIDDEN — episode harvester KEPT.** New home: the narrowed **`ptcg-factory-continuous` watch loop** — it already fires every ~15 minutes and already hosts `check_and_harvest` today, so the harvester simply SURVIVES the T20 narrowing rather than being dropped with the evolution paths. Cadence: every firing, internally stamp-gated (`harvest_stamp.json`, `episodes.py:74`) so real downloads stay ~daily; retention semantics unchanged (extract-then-delete, 2GB cap, `episodes.py` `_prune_cap`). Inertness during the slice: NO change to harvester code/wiring until T20, whose edit happens inside the PAUSE window. Consumer: **JUDGMENT CALL — minimal spec-consistent default:** extracts (`extracts.jsonl`) are retained as a growing data asset with a NAMED future hook reserved — `census.inject_meta_concepts(paths, decks)` (meta-deck → tournament census-concept injection: a future task would map harvested meta decks to their core attacker names and seed/prioritize those concepts) — NOT built this slice; the spec's tournament model defines no episode consumer, so building one now would be unsanctioned scope. The retired `top_meta_decks`/`inject_meta_anchors` path stays on disk unused (its `deck_pool.json` consumer is retired).
- **PD-B OVERRIDDEN — static dashboard REPLACED.** `ptcg-factory-ui` is the sole surface: it serves the deck-review page AND a live status page (T19). At cutover (T20), ALL `dashboard.safe_render` call sites are removed from `scripts/factory_watch_once.py` (all four: paused-return, in-lock return, busy-return, error-return); `src/ptcg/factory/dashboard.py` and `scripts/render_dashboard.py` are retired-in-place (left on disk, unwired — repo convention for superseded systems); the last-written `experiments/factory/dashboard.html` remains on disk as an inert stale artifact (go-live verifies its mtime stops advancing). Inertness lever: dashboard wiring keeps running master behavior until the T20 PAUSE-window edit. **Availability (JUDGMENT CALL — spec-consistent default): register `ptcg-factory-ui` as a Windows Scheduled Task with an AtStartup trigger + 15-minute watchdog repetition (`-MultipleInstances IgnoreNew` + in-process single-instance lock)** — identical to the existing worker-watchdog pattern in `register_factory_task.ps1` — so the sole surface auto-starts at boot and auto-restarts within ≤15 min if it dies. Same register-loudly/create-before-retire/post-verify discipline as every other task (Pattern PS1-REGISTER). The manual-start alternative was rejected: with the fallback file gone, an unattended UI outage would leave NO status surface.
- **CENSUS SCOPE EXPANDED — pair enumeration THIS slice (enumerate-all, play-singles).** Feasibility verdict with executed receipts (see Global Constraints): all 332,520 concept rows are enumerated into SQLite in-slice (~36.5 MB at ~110 B/row — trivial); the founding census PLAYS only the 815 singles (12,225 games ≈ 6.1 days @2k/day); playing pairs at founding would take years at any plausible throughput and is NOT planned; pair space is explored generationally post-census via lazy activation gated on both cores proving out (spec LD4's "lazily... as individual cores prove out", unchanged). Implemented in T2 (two-core builder), T8 (pair enumeration + activation + DB scale checks), T16 (scheduler activation hook).
- **Founding net RATIFIED:** `src/ptcg/search/value_net_weights_v2.json` is the founding `v0.1` agent's evaluator (T12). No longer a judgment call.

## Slice Containment Strategy (LIVE-ON-WRITE hazard — plan-level decision)

`ptcg-factory-continuous` spawns a **fresh process every ~15 minutes that imports the working tree**. Factory dev happens on a feature branch checked out in that same main working tree (Python project, no worktree per the Windows-pnpm MAX_PATH precheck). **Checking out the feature branch SWITCHES what the live watch loop imports — this is a stated, managed decision, not an accident.** Containment for the whole slice:

1. **`SUBMIT_HOLD` stays ACTIVE for the entire slice.** It is already present (`experiments/factory/SUBMIT_HOLD`, since 2026-07-22 12:36 HST). No new upload can fire until the explicit POST-MERGE go-live rung (T21) lifts it. Existing `run_cycle`/submit paths already honor it (`cycle.py:256`).
2. **Phase 1 is PURELY ADDITIVE.** Every Phase-1 task creates NEW modules (`deckdb.py`, `builder.py`, `census.py`, `rating.py`, `runner_pool.py`) and NEW scripts/tests only. **No Phase-1 task may modify any module in the live watch loop's import graph** (`scripts/factory_watch_once.py` and everything it imports: `cycle.py`, `evolution.py`, `deck_matrix.py`, `episodes.py`, `candidates.py`, `gate.py`, `submit.py`, `bt.py`, `tournament.py`, `dashboard.py`, `evaluate.py`, `kaggle_client.py`, `watch.py`). New modules are unreachable from `factory_watch_once.py`, so a 15-minute firing keeps running current (master) behavior. **Per-task inertness lever:** each Phase-1 task's DoD includes "grep confirms `factory_watch_once.py` and its import graph are unmodified by this task" and "the next `watch.log` line after this task's commit is a normal `cycle:`/`refill:` line, not an error."
3. **New Windows Scheduled Tasks are NOT registered until T21 (post-merge).** The runner pool, tournament scheduler, and UI never fire on a schedule during development. Every dev/smoke run is a MANUAL, foreground invocation against an EXPLICIT temp DB path (never the production `tournament.db`).
4. **Phase 2's cutover task (T20) DOES modify `factory_watch_once.py` (submission-scheduler rewire, harvester survival, dashboard removal) and is a scheduler/cutover task.** For the T20 implementation window AND the T21 go-live window, **touch the `PAUSE` file first** (`experiments/factory/PAUSE`) so all three current workers stop, land the change, verify, then remove `PAUSE`. Named in each task.
5. **Production `tournament.db` is created only at go-live (T21).** Dev + tests use temp DBs; virgin-directory tests + a rung-3 smoke against a genuinely fresh temp location (T9, T21) cover the first-write path.

---

## Pattern Library

Referenced by name from tasks (DRY). Repeat the pattern's shape; do not re-explain it per task.

### Pattern SQLITE-CONN — connection discipline (every DB open)
Every process/thread that touches the DB opens its own connection through the single factory `deckdb.connect(path)`, which applies WAL + a long busy timeout so concurrent writers WAIT instead of raising `database is locked`:
```python
def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)  # virgin-dir rule (global CLAUDE.md)
    conn = sqlite3.connect(str(path), timeout=30.0, isolation_level=None)  # autocommit; we manage txns explicitly
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")       # 1 writer + N readers concurrently
    conn.execute("PRAGMA busy_timeout=30000")     # wait up to 30s for a competing writer
    conn.execute("PRAGMA synchronous=NORMAL")     # WAL-safe durability, faster than FULL
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
```
Rationale: WAL gives single-writer/multi-reader concurrency for the 4-8 runners + UI + scheduler. `isolation_level=None` (autocommit) means WE own transaction boundaries via explicit `BEGIN IMMEDIATE`/`COMMIT` (Pattern SQLITE-TXN); the Python driver never opens an implicit deferred transaction that would surprise us.

### Pattern SQLITE-TXN — every write is a short IMMEDIATE transaction
A write that reads-then-writes (claim a game, apply a decision, update coverage) MUST take the write lock up front so no two writers interleave a read-modify-write:
```python
def _write(conn, fn):
    """Run fn(conn) inside a BEGIN IMMEDIATE ... COMMIT. IMMEDIATE acquires the
    write lock before any statement, so a concurrent writer waits (busy_timeout)
    rather than racing a deferred lock upgrade. Rolls back on any exception."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = fn(conn)
        conn.execute("COMMIT")
        return result
    except Exception:
        conn.execute("ROLLBACK")
        raise
```
Complexity: each write is O(1) or O(rows-touched); `BEGIN IMMEDIATE` serializes writers but keeps every write short (no game-play or network inside a transaction — ever).

### Pattern ATOMIC-JSON — atomic write for any remaining shared JSON
For any small JSON state that stays JSON (submission-scheduler state, stamps):
```python
def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")  # utf-8 mandatory (Windows cp1252)
    os.replace(tmp, path)
```

### Pattern INTERLEAVED-TEST — adversarial multi-actor test (per `.claude/rules/single-actor-worker-tests.md`)
For EVERY shared-state read-modify-write window, author a test that injects a concurrent write mid-operation and asserts no write is silently lost. Shape:
```python
def test_<op>_survives_concurrent_write(tmp_path):
    db = deckdb.connect(tmp_path / "t.db"); deckdb.init_db(db)
    # ... seed state ...
    # Actor A opens its own connection and BEGIN IMMEDIATE (holds the write lock)
    a = deckdb.connect(tmp_path / "t.db"); a.execute("BEGIN IMMEDIATE"); a.execute(<A's write>)
    # Actor B (fresh connection) attempts the same-record write; must WAIT then merge/reject, never clobber
    b = deckdb.connect(tmp_path / "t.db")
    import threading
    done = []
    t = threading.Thread(target=lambda: done.append(<B op>(b)))
    t.start(); a.execute("COMMIT"); t.join(timeout=5)
    # assert A's write survived AND B's write either merged or was cleanly rejected (no lost update)
```
Use real separate `sqlite3` connections (WAL cross-connection locking is real in-process). For runner-claim double-claim, use a barrier + two threads each calling `claim_next_game` and assert they return DISTINCT game ids (or one returns None).

### Pattern VIRGIN-DIR-TEST — first-write against a never-created parent
For any first-write path (the DB file, UI decision log, stamp/lock files), a test that points at a nested path whose parent does NOT exist and asserts the write succeeds (creates the parent):
```python
def test_<writer>_creates_missing_parent(tmp_path):
    fresh = tmp_path / "never" / "created" / "here" / "t.db"  # parent chain absent
    assert not fresh.parent.exists()
    db = deckdb.connect(fresh); deckdb.init_db(db)   # must not raise FileNotFoundError
    assert fresh.exists()
```

### Pattern PS1-REGISTER — scheduled-task registration (models `scripts/register_factory_task.ps1`)
Register-NEW-before-retire-OLD; every real cmdlet in `try { ... -ErrorAction Stop } catch { Write-Output "ERROR..."; exit 1 }`; verify each with `Get-ScheduledTask` immediately after its `Register-ScheduledTask`; resolve `uv.exe` by explicit `-UvPath`/probe (never PATH inheritance in an elevated shell); a `-DryRun` branch that prints the plan and NEVER touches the real Task Scheduler; `Start-Transcript` capture for elevated runs. See `scripts/register_factory_task.ps1` verbatim as the reference.

---

# PHASE 1 — Foundation: Database, Builder, Census (singles + pair enumeration), Runner Pool, Field Rating

Phase 1 produces working, testable software on its own: a Founding Census that enumerates all 332,520 concepts (815 singles played, 331,705 pairs enumerated-but-dormant), builds legal decks, plays screening games in a parallel runner pool, and rates the deck field via Bradley-Terry — all persisted to `tournament.db`, all runnable manually against a temp DB with `SUBMIT_HOLD` active and no scheduled task registered. Tasks T1-T10.

## Task 1: SQLite database module — schema + connection discipline

**Files:**
- Create: `src/ptcg/factory/deckdb.py`
- Test: `tests/test_factory_deckdb.py`

**Interfaces:**
- Produces: `connect(path: Path) -> sqlite3.Connection` (Pattern SQLITE-CONN); `init_db(conn: sqlite3.Connection) -> None` (idempotent DDL, `CREATE TABLE IF NOT EXISTS`); `SCHEMA_VERSION: int = 1`; module-level `_write(conn, fn)` (Pattern SQLITE-TXN). Tables per spec Locked Decision 9: `concepts(id TEXT PK, cores TEXT, status TEXT, builder_version INT)`, `decks(id TEXT PK, concept_id TEXT, cards TEXT, shell_variant INT)`, `games(id INTEGER PK AUTOINCREMENT, deck_a_id, deck_b_id, agent_version_a, agent_version_b, winner INT, status TEXT, priority REAL, worker_pid INT, claimed_at TEXT, timestamp TEXT)`, `decisions(id INTEGER PK, deck_id, action, actor, timestamp)`, `coverage(concept_id TEXT PK, games_played INT, distinct_opponents INT, rating REAL)`, `offspring(id TEXT PK, parent_baseline_version, search_config_json, value_net_ref, status, created_at)`, `baselines(version TEXT PK, offspring_id, deck_id, crowned_at)`. `status` domains enforced via `CHECK` constraints: concepts `{untested,active,culled,unbuildable,finalist}`, games `{pending,claimed,done}`, offspring `{training,queued_for_match,matching,confirming,trashed,survivor}`, decisions `{remove,pass}`. **Index `ix_concepts_status` on `concepts(status)`** — required because the table holds 332,520 rows (pair enumeration, T8) and the census scheduler + UI + pair-activation queries all filter on status; without it every such query is a full 332k-row scan.
- Consumes: nothing (foundation).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_factory_deckdb.py
from pathlib import Path
import sqlite3
import pytest
from ptcg.factory import deckdb

def test_init_db_creates_all_tables(tmp_path):
    db = deckdb.connect(tmp_path / "t.db"); deckdb.init_db(db)
    names = {r["name"] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"concepts","decks","games","decisions","coverage","offspring","baselines"} <= names

def test_wal_mode_and_busy_timeout(tmp_path):
    db = deckdb.connect(tmp_path / "t.db")
    assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 30000

def test_init_db_is_idempotent(tmp_path):
    p = tmp_path / "t.db"
    db = deckdb.connect(p); deckdb.init_db(db); deckdb.init_db(db)  # second call must not raise
    assert db.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 0

def test_status_check_constraint_rejects_bad_concept_status(tmp_path):
    db = deckdb.connect(tmp_path / "t.db"); deckdb.init_db(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO concepts(id,cores,status,builder_version) VALUES('c1','[1]','bogus',1)")

def test_concepts_status_index_exists(tmp_path):
    db = deckdb.connect(tmp_path / "t.db"); deckdb.init_db(db)
    idx = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ix_concepts_status" in idx and "ix_games_claim" in idx

def test_connect_creates_missing_parent(tmp_path):  # Pattern VIRGIN-DIR-TEST
    fresh = tmp_path / "never" / "here" / "t.db"
    assert not fresh.parent.exists()
    deckdb.init_db(deckdb.connect(fresh))
    assert fresh.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_factory_deckdb.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ptcg.factory.deckdb'`.

- [ ] **Step 3: Write minimal implementation**

Implement `src/ptcg/factory/deckdb.py` with Pattern SQLITE-CONN `connect`, `_write` (Pattern SQLITE-TXN), `SCHEMA_VERSION = 1`, and `init_db(conn)` running the DDL below inside one transaction:
```python
"""SQLite deck database for the generational champion tournament (spec Locked
Decision 9). Single source of truth for concepts/decks/games/ratings/decisions/
offspring/baselines. WAL mode + busy_timeout give the 4-8 runner processes + UI
+ scheduler safe concurrent access; every write is a short BEGIN IMMEDIATE txn."""
from __future__ import annotations
import json, os, sqlite3
from pathlib import Path
from typing import Callable

SCHEMA_VERSION = 1

def connect(path: Path) -> sqlite3.Connection:
    # ... Pattern SQLITE-CONN verbatim ...

def _write(conn: sqlite3.Connection, fn: Callable[[sqlite3.Connection], object]):
    # ... Pattern SQLITE-TXN verbatim ...

_DDL = """
CREATE TABLE IF NOT EXISTS concepts(
  id TEXT PRIMARY KEY, cores TEXT NOT NULL, builder_version INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'untested'
    CHECK(status IN ('untested','active','culled','unbuildable','finalist')),
  reason TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_concepts_status ON concepts(status);
CREATE TABLE IF NOT EXISTS decks(
  id TEXT PRIMARY KEY, concept_id TEXT NOT NULL REFERENCES concepts(id),
  cards TEXT NOT NULL, shell_variant INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS games(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  deck_a_id TEXT NOT NULL, deck_b_id TEXT NOT NULL,
  agent_version_a TEXT NOT NULL, agent_version_b TEXT NOT NULL,
  purpose TEXT NOT NULL DEFAULT 'screening',
  winner INTEGER, status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','claimed','done')),
  priority REAL NOT NULL DEFAULT 0.0, worker_pid INTEGER,
  claimed_at TEXT, timestamp TEXT);
CREATE INDEX IF NOT EXISTS ix_games_claim ON games(status, priority);
CREATE TABLE IF NOT EXISTS decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, deck_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('remove','pass')),
  actor TEXT NOT NULL, timestamp TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS coverage(
  concept_id TEXT PRIMARY KEY REFERENCES concepts(id),
  games_played INTEGER NOT NULL DEFAULT 0,
  distinct_opponents INTEGER NOT NULL DEFAULT 0, rating REAL);
CREATE TABLE IF NOT EXISTS offspring(
  id TEXT PRIMARY KEY, parent_baseline_version TEXT NOT NULL,
  search_config_json TEXT NOT NULL, value_net_ref TEXT, deck_id TEXT,
  status TEXT NOT NULL DEFAULT 'training'
    CHECK(status IN ('training','queued_for_match','matching','confirming','trashed','survivor')),
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS baselines(
  version TEXT PRIMARY KEY, offspring_id TEXT, deck_id TEXT NOT NULL, crowned_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

def init_db(conn: sqlite3.Connection) -> None:
    def _apply(c):
        c.executescript(_DDL)
        c.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version',?)",
                  (str(SCHEMA_VERSION),))
    _write(conn, _apply)
```
Note: `games.winner` semantics — `0`=deck_a agent won, `1`=deck_b agent won, `2`=draw (mirrors `MatchResult.winner`). Store `purpose` in `{screening,match,confirm,crown}` so the loop can query its own series.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_factory_deckdb.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/factory/deckdb.py tests/test_factory_deckdb.py
git commit -m "feat: SQLite deck database schema + WAL connection discipline (tournament T1)"
```

## Task 2: Deterministic concept enumeration + deck builder (single-core AND two-core)

**Files:**
- Create: `src/ptcg/factory/builder.py`
- Create: `tests/fixtures/` golden decks (generated in Step 3, committed)
- Test: `tests/test_factory_builder.py`

**Interfaces:**
- Consumes: `ptcg.decks.analysis.pool_summary()` (attacker enumeration), `ptcg.decks.validate.validate_deck(deck) -> list[str]`, `cg.api.all_card_data`/`all_attack`.
- Produces: `BUILDER_VERSION: int = 1`; `enumerate_concepts() -> list[Concept]` where `@dataclass(frozen=True) Concept(cores: tuple[str, ...])` — cores are attacker Pokémon NAMES (deduped, sorted) — for single cores `len(cores)==1`; `concept_id(cores) -> str` (stable, e.g. `"c-" + sha1("|".join(sorted(cores)))[:12]`); `build_deck(concept: Concept) -> BuildResult` where `@dataclass BuildResult(cards: list[int] | None, unbuildable_reason: str | None)` — **handles BOTH `len(cores)==1` and `len(cores)==2`** (two-core: both cores' evolution lines, energy split cost-matched across both cores' attack costs within the shared 20-energy ceiling, one shared trainer skeleton; unbuildable reasons include "combined energy demands exceed 20-energy ceiling"); `brad_bounds_problems(deck: list[int]) -> list[str]` (the ≤20 energy / ≥8 basic / ≥4 supporter / ≥4 item / ==1 ACE SPEC checks not covered by `validate_deck`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_factory_builder.py
from pathlib import Path
from ptcg.factory import builder
from ptcg.decks.validate import validate_deck

def test_enumerate_dedups_by_name_815_basic442_evo373():
    concepts = builder.enumerate_concepts()
    # 815 distinct attacker NAMES verified against the engine (442 basic + 373 evo).
    # Assert a stable band + the dedup property rather than a brittle == (the pool
    # may gain cards mid-competition per CLAUDE.md); log the exact count.
    names = [c.cores[0] for c in concepts if len(c.cores) == 1]
    assert len(names) == len(set(names)), "single-core concepts must be name-deduped"
    assert 800 <= len(names) <= 900, f"expected ~815 single-core concepts, got {len(names)}"
    print(f"SINGLE-CORE CONCEPTS: {len(names)}")

def test_build_deck_is_deterministic_byte_identical():
    concepts = builder.enumerate_concepts()
    c = next(x for x in concepts if len(x.cores) == 1)
    a = builder.build_deck(c); b = builder.build_deck(c)
    assert a.cards == b.cards and a.cards is not None

def test_built_deck_is_engine_legal_and_meets_brad_bounds():
    concepts = builder.enumerate_concepts()
    built = 0
    for c in concepts[:40]:
        r = builder.build_deck(c)
        if r.cards is None:
            assert r.unbuildable_reason  # never silently None
            continue
        assert validate_deck(r.cards) == [], f"{c.cores}: {validate_deck(r.cards)}"
        assert builder.brad_bounds_problems(r.cards) == [], f"{c.cores}: {builder.brad_bounds_problems(r.cards)}"
        built += 1
    assert built > 0

def test_two_core_build_deterministic_legal_or_reasoned():
    # Pair concepts (census-scope expansion, Brad 2026-07-23): the builder must
    # handle len(cores)==2 with the same determinism + legality + bounds contract.
    concepts = builder.enumerate_concepts()
    singles = [c.cores[0] for c in concepts if len(c.cores) == 1]
    pair = builder.Concept(cores=tuple(sorted((singles[0], singles[1]))))
    a = builder.build_deck(pair); b = builder.build_deck(pair)
    assert a.cards == b.cards  # deterministic (including deterministic unbuildable)
    if a.cards is not None:
        assert validate_deck(a.cards) == []
        assert builder.brad_bounds_problems(a.cards) == []
    else:
        assert a.unbuildable_reason

def test_golden_deck_pins_known_concept():
    # Golden-vector discipline: pin a byte-identical known-good output (global
    # CLAUDE.md serialization-guard rule). Fixture generated once in Step 3.
    fx = Path(__file__).parent / "fixtures" / "golden_deck_pikachu-ex.csv"
    cores = ("Pikachu ex",)  # replace with a confirmed buildable single core at impl time
    r = builder.build_deck(builder.Concept(cores=cores))
    expected = [int(x) for x in fx.read_text(encoding="utf-8").split()]
    assert r.cards == expected
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest tests/test_factory_builder.py -v`
Expected: FAIL (`ModuleNotFoundError` / missing fixture).

- [ ] **Step 3: Write minimal implementation + generate the golden fixture**

Implement `enumerate_concepts` from `pool_summary()`: attacker = a `CardData` in `pool_summary().pokemon` with ≥1 attack whose `all_attack()[attackId].damage > 0` (looked up via the attackId dict convention, `.claude/rules/verify-game-data-claims.md`); **dedup by `card.name`**; return sorted single-core `Concept`s. `build_deck` single-core fills: the core's evolution line (from `pool_summary().evolution_lines`), cost-matched basic energy for the core's attack energy types (≤20 energy total), and a fixed trainer skeleton (≥4 supporters, ≥4 items, exactly 1 ACE SPEC) padding to 60 with ≥8 basic Pokémon. Two-core: both evolution lines (fewer copies each), energy allocation covering both cores' attack costs within the shared 20-energy ceiling (deterministic split — e.g. proportional to summed attack costs, ties broken by sorted name), the same trainer skeleton; if the combined demands cannot fit, return a reasoned unbuildable. Zero RNG anywhere. `brad_bounds_problems` counts energy cards / basic Pokémon / supporters / items / ACE SPEC via the engine card DB (`validate._card_db()`), returning a list of human-readable violations (empty == ok).

Then generate + commit the golden fixture:
```bash
uv run python -c "from ptcg.factory import builder; r=builder.build_deck(builder.Concept(cores=('Pikachu ex',))); print(' '.join(map(str,r.cards)))" > tests/fixtures/golden_deck_pikachu-ex.csv
# implementer: pick an actually-buildable single core (verify build_deck(...).cards is not None
# via the engine BEFORE pinning) and name the fixture + test to match it.
```
Per `.claude/rules/verify-game-data-claims.md`: confirm the chosen golden core is buildable by an executable check against the engine before pinning — do not assume "Pikachu ex" exists/builds.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_factory_builder.py -v`
Expected: PASS. The `SINGLE-CORE CONCEPTS:` print should read ~815.

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/factory/builder.py tests/test_factory_builder.py tests/fixtures/golden_deck_*.csv
git commit -m "feat: deterministic concept enumeration + single/two-core deck builder (tournament T2)"
```

## Task 3: Census seeding — single-core concepts + decks into the DB

**Files:**
- Create: `src/ptcg/factory/census.py`
- Test: `tests/test_factory_census.py`

**Interfaces:**
- Consumes: `deckdb.connect/init_db/_write` (T1), `builder.enumerate_concepts/build_deck/concept_id/BUILDER_VERSION` (T2).
- Produces: `seed_census(conn) -> dict` (idempotent: inserts each SINGLE-CORE concept once with `status='untested'` or `'unbuildable'`+reason, and one `decks` row per buildable concept with `shell_variant=0`; returns `{"concepts": n, "buildable": b, "unbuildable": u}`); `deck_id(concept_id, shell_variant) -> str`. (Pair-concept seeding is T8's `seed_pair_concepts` — kept separate because pairs are enumerated WITHOUT deck rows.)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_factory_census.py
from ptcg.factory import deckdb, census
def _db(tmp): d = deckdb.connect(tmp / "t.db"); deckdb.init_db(d); return d

def test_seed_census_inserts_concepts_and_decks(tmp_path):
    db = _db(tmp_path)
    res = census.seed_census(db)
    n_concepts = db.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
    n_decks = db.execute("SELECT COUNT(*) FROM decks").fetchone()[0]
    assert n_concepts == res["concepts"] and n_concepts >= 800
    assert n_decks == res["buildable"] >= 1
    # unbuildable concepts are recorded, never dropped
    assert res["unbuildable"] == n_concepts - res["buildable"]

def test_seed_census_is_idempotent(tmp_path):
    db = _db(tmp_path)
    a = census.seed_census(db); b = census.seed_census(db)
    assert a["concepts"] == b["concepts"]
    assert db.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == a["concepts"]

def test_seed_census_virgin_dir(tmp_path):  # Pattern VIRGIN-DIR-TEST
    fresh = tmp_path / "no" / "dir" / "t.db"
    db = deckdb.connect(fresh); deckdb.init_db(db)
    assert census.seed_census(db)["concepts"] >= 800
```

- [ ] **Step 2: Run to verify fail.** `uv run pytest tests/test_factory_census.py -v` → FAIL (missing module).

- [ ] **Step 3: Implement `seed_census`.** One `_write` txn: for each `enumerate_concepts()` concept, `INSERT OR IGNORE` into `concepts`; call `build_deck`; on `cards is None` set `status='unbuildable', reason=...`; else `INSERT OR IGNORE` a `decks` row with `cards = json.dumps(cards)` and `INSERT OR IGNORE` a `coverage` row. Idempotency via `INSERT OR IGNORE` + content-addressed ids. `cores` stored as `json.dumps(list(concept.cores))`.

- [ ] **Step 4: Run to verify pass.** `uv run pytest tests/test_factory_census.py -v` → PASS.

- [ ] **Step 5: Commit.**
```bash
git add src/ptcg/factory/census.py tests/test_factory_census.py
git commit -m "feat: Founding Census seeds single-core concepts+decks into SQLite (tournament T3)"
```

## Task 4: Game queue — enqueue + ATOMIC claim + record result

**Files:**
- Modify: `src/ptcg/factory/deckdb.py` (add queue functions)
- Test: `tests/test_factory_deckdb_queue.py`

**Interfaces:**
- Produces: `enqueue_game(conn, deck_a_id, deck_b_id, agent_version_a, agent_version_b, purpose, priority) -> int` (returns game id); `claim_next_game(conn, worker_pid) -> sqlite3.Row | None` (ATOMIC: highest-priority `pending` game → `claimed`, returns it, or None); `record_result(conn, game_id, winner) -> None` (`claimed`→`done`, sets winner+timestamp, bumps `coverage.games_played` for BOTH decks' concepts); `pending_count(conn, purpose=None) -> int`.

- [ ] **Step 1: Write failing tests — INCLUDING the adversarial double-claim test (Pattern INTERLEAVED-TEST)**

```python
# tests/test_factory_deckdb_queue.py
import threading
from ptcg.factory import deckdb
def _seed(tmp):
    db = deckdb.connect(tmp / "t.db"); deckdb.init_db(db)
    def _s(c):
        for cid in ("cA","cB"):
            c.execute("INSERT INTO concepts(id,cores,status) VALUES(?,?, 'active')",(cid,'["X"]'))
            c.execute("INSERT INTO coverage(concept_id,games_played,distinct_opponents) VALUES(?,0,0)",(cid,))
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dA','cA','[]')")
        c.execute("INSERT INTO decks(id,concept_id,cards) VALUES('dB','cB','[]')")
    deckdb._write(db, _s)
    return db

def test_enqueue_and_claim_and_record(tmp_path):
    db = _seed(tmp_path)
    gid = deckdb.enqueue_game(db,"dA","dB","v0.1","v0.1","screening",0.9)
    row = deckdb.claim_next_game(db, worker_pid=111)
    assert row["id"] == gid and row["status"] == "claimed" and row["worker_pid"] == 111
    assert deckdb.claim_next_game(db, worker_pid=222) is None  # nothing left pending
    deckdb.record_result(db, gid, winner=0)
    assert db.execute("SELECT status,winner FROM games WHERE id=?",(gid,)).fetchone()["status"] == "done"
    assert db.execute("SELECT games_played FROM coverage WHERE concept_id='cA'").fetchone()[0] == 1

def test_two_workers_never_double_claim(tmp_path):  # ADVERSARIAL multi-actor
    db = _seed(tmp_path)
    for i in range(1):  # exactly ONE pending game; two racers
        deckdb.enqueue_game(db,"dA","dB","v0.1","v0.1","screening",0.5)
    results = {}
    barrier = threading.Barrier(2)
    def worker(pid):
        conn = deckdb.connect(tmp_path / "t.db")
        barrier.wait()
        results[pid] = deckdb.claim_next_game(conn, worker_pid=pid)
    t1 = threading.Thread(target=worker, args=(1,)); t2 = threading.Thread(target=worker, args=(2,))
    t1.start(); t2.start(); t1.join(); t2.join()
    claimed = [r for r in results.values() if r is not None]
    assert len(claimed) == 1, "exactly one worker may claim the single pending game"
```

- [ ] **Step 2: Run to verify fail.** `uv run pytest tests/test_factory_deckdb_queue.py -v` → FAIL.

- [ ] **Step 3: Implement.** `claim_next_game` uses Pattern SQLITE-TXN so the `SELECT ... WHERE status='pending' ORDER BY priority DESC, id LIMIT 1` and the `UPDATE ... SET status='claimed'` happen under one `BEGIN IMMEDIATE` write lock — two racers serialize; the loser's re-SELECT finds no pending row and returns None. `record_result` updates the game and, in the same txn, `UPDATE coverage SET games_played = games_played + 1` for both decks' `concept_id`s (subquery `SELECT concept_id FROM decks WHERE id=?`). Complexity: claim is O(log n) via `ix_games_claim`; record is O(1).

- [ ] **Step 4: Run to verify pass.** `uv run pytest tests/test_factory_deckdb_queue.py -v` → PASS (2 tests, including the double-claim race).

- [ ] **Step 5: Commit.**
```bash
git add src/ptcg/factory/deckdb.py tests/test_factory_deckdb_queue.py
git commit -m "feat: atomic game-queue claim/record with double-claim race test (tournament T4)"
```

## Task 5: Bradley-Terry field rating over the games table

**Files:**
- Create: `src/ptcg/factory/rating.py`
- Test: `tests/test_factory_rating.py`

**Interfaces:**
- Consumes: `ptcg.factory.bt.fit_ratings(wins: dict[tuple[str,str],int]) -> dict[str,float]` (existing, `bt.py:28`), `deckdb` (T1/T4).
- Produces: `refresh_field_ratings(conn, scope_concept_ids: list[str] | None = None) -> int` — reads `done` `screening` games, builds the `wins` dict keyed by CONCEPT id (`wins[(winner_concept, loser_concept)] += 1`), calls `bt.fit_ratings`, writes each `coverage.rating` and `distinct_opponents`; returns number of concepts rated.

- [ ] **Step 1: Write failing tests (including a concurrent coverage-write interleaved test)**

```python
# tests/test_factory_rating.py
from ptcg.factory import deckdb, rating
# ... helper seeds two concepts + decks + a handful of done screening games ...
def test_refresh_writes_ratings_for_played_concepts(tmp_path):
    db = _seed_with_games(tmp_path, a_wins=8, b_wins=2)
    n = rating.refresh_field_ratings(db)
    assert n == 2
    ra = db.execute("SELECT rating FROM coverage WHERE concept_id='cA'").fetchone()[0]
    rb = db.execute("SELECT rating FROM coverage WHERE concept_id='cB'").fetchone()[0]
    assert ra > rb  # cA won 8 of 10

def test_refresh_scoped_to_subset(tmp_path):
    db = _seed_with_games(tmp_path, a_wins=5, b_wins=5)
    assert rating.refresh_field_ratings(db, scope_concept_ids=["cA","cB"]) == 2

def test_refresh_concurrent_with_result_write_no_lost_update(tmp_path):  # INTERLEAVED
    # A rating refresh (updates coverage.rating) must not clobber a concurrent
    # record_result's games_played bump on the same coverage row.
    db = _seed_with_games(tmp_path, a_wins=5, b_wins=5)
    # inject a concurrent record_result via a second connection mid-refresh; assert
    # BOTH the new rating AND the incremented games_played survive.
```

- [ ] **Step 2: Run to verify fail.** → FAIL.

- [ ] **Step 3: Implement.** Build `wins` from `SELECT deck_a_id,deck_b_id,winner FROM games WHERE status='done' AND purpose='screening'`, mapping deck→concept via a `decks` lookup, incrementing `wins[(wc, lc)]`. Call `bt.fit_ratings`. Write ratings + `distinct_opponents` per concept inside Pattern SQLITE-TXN (only touch `rating`/`distinct_opponents`, never `games_played`, so a concurrent `record_result` merges field-by-field). **Complexity glance (global CLAUDE.md O(n²)-AUC lesson):** `bt.fit_ratings`'s inner loop is O(ids × pairs) per iteration; over the full 815-concept played field with dense pairs this is expensive — and the concepts TABLE now holds 332,520 rows (T8), so the fit must NEVER be keyed to table size, only to concepts that actually have `done` games (the played census field, ≤815 + activated pairs). `refresh_field_ratings` MUST be called on a throttle (see T7 — every N completed games or once per UI cadence), and `scope_concept_ids` lets the loop restrict the fit to active+recently-played concepts. Never call it per-game.

- [ ] **Step 4: Run to verify pass.** → PASS (3 tests).

- [ ] **Step 5: Commit.**
```bash
git add src/ptcg/factory/rating.py tests/test_factory_rating.py
git commit -m "feat: Bradley-Terry field rating over games table (tournament T5)"
```

## Task 6: Parallel game-runner pool

**Files:**
- Create: `src/ptcg/factory/runner_pool.py`
- Create: `scripts/factory_runner_pool.py`
- Test: `tests/test_factory_runner_pool.py`

**Interfaces:**
- Consumes: `deckdb.claim_next_game/record_result` (T4), `ptcg.factory.evaluate.build_agent(candidate, deck)` (`evaluate.py:53`), `ptcg.arena.runner.play_match` (`runner.py:50`), `ptcg.factory.candidates.Candidate` (for `build_agent`'s config carrier).
- Produces: `run_one_game(conn, row, agent_configs, root) -> int | None` (build both agents from the game's `agent_version_*` config + decks, `play_match`, `record_result`, return winner); `worker_loop(db_path, agent_configs, root, stop_when_empty=False, max_games=None) -> int` (claim→play→record until no pending or stop); `spawn_pool(db_path, n_workers, ...)` (supervisor: `multiprocessing` spawns N `worker_loop` children at BelowNormal priority). `agent_configs: dict[str, dict]` maps agent_version → `{"agent_kind","agent_config"}` (baseline v0.1 + offspring configs).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_factory_runner_pool.py
from ptcg.factory import deckdb, runner_pool
class _StubMatch:  # inject a deterministic play_match so tests don't run the real engine
    def __init__(self, winner): self.winner = winner; self.error=None; self.turns=1; self.moves=1
    # ... matches MatchResult shape enough for run_one_game ...

def test_run_one_game_records_winner(tmp_path, monkeypatch):
    db = _seed_one_pending(tmp_path)  # deckA vs deckB, both agent v0.1
    monkeypatch.setattr(runner_pool, "play_match", lambda *a, **k: _StubMatch(winner=0))
    monkeypatch.setattr(runner_pool, "build_agent", lambda cand, deck: object())
    row = deckdb.claim_next_game(db, worker_pid=1)
    assert runner_pool.run_one_game(db, row, AGENT_CFGS, tmp_path) == 0
    assert db.execute("SELECT winner FROM games WHERE id=?", (row["id"],)).fetchone()[0] == 0

def test_worker_loop_drains_queue_without_double_count(tmp_path, monkeypatch):
    db = _seed_n_pending(tmp_path, n=6)
    monkeypatch.setattr(runner_pool, "play_match", lambda *a, **k: _StubMatch(winner=1))
    monkeypatch.setattr(runner_pool, "build_agent", lambda cand, deck: object())
    played = runner_pool.worker_loop(str(tmp_path/"t.db"), AGENT_CFGS, tmp_path, stop_when_empty=True)
    assert played == 6
    assert deckdb.pending_count(db) == 0
    assert db.execute("SELECT COUNT(*) FROM games WHERE status='done'").fetchone()[0] == 6
```

- [ ] **Step 2: Run to verify fail.** → FAIL.

- [ ] **Step 3: Implement.** `run_one_game`: look up both decks' `cards` (json) + build a throwaway `Candidate` per side from `agent_configs[version]` (`Candidate.create(name=version, version=version, deck=..., agent_kind=..., agent_config=...)`), `build_agent`, `play_match(agent_a, agent_b, deck_a, deck_b)`, map `MatchResult.winner` (skip/requeue on `winner == -1` error — set status back to `pending` and log), `record_result`. `worker_loop`: `claim_next_game(pid=os.getpid())` in a loop; break when None (if `stop_when_empty`) else sleep briefly and retry; count games. `spawn_pool`: `multiprocessing.get_context("spawn")` (the cg engine's global native state means each game MUST be in a distinct process — verified: `arena.runner` uses module-global `battle_start/select/finish`), set each child to BelowNormal via `psutil`-free stdlib (`os` on POSIX; on Windows use `subprocess`/`SetPriorityClass` through `ctypes` — reuse the existing `watch.throttle_below_normal` helper). **DoD note (containment §2):** this task creates NEW files only; it must NOT import from or edit `factory_watch_once.py`'s graph.

- [ ] **Step 4: Run to verify pass.** → PASS.

- [ ] **Step 5: Commit.**
```bash
git add src/ptcg/factory/runner_pool.py scripts/factory_runner_pool.py tests/test_factory_runner_pool.py
git commit -m "feat: parallel ISMCTS game-runner pool over the SQLite queue (tournament T6)"
```

## Task 7: Census scheduling — priority (worst-rated first) + completion detection

**Files:**
- Modify: `src/ptcg/factory/census.py`
- Test: `tests/test_factory_census_schedule.py`

**Interfaces:**
- Consumes: `deckdb.enqueue_game/pending_count` (T4), `rating.refresh_field_ratings` (T5), `builder`/`census` (T2/T3).
- Produces: `schedule_screening_games(conn, target_per_concept=15, batch=200, founding_agent_version="v0.1") -> int` (enqueue up to `batch` screening games, pairing each under-covered concept against a rotating opponent, prioritizing WORST-rated/least-covered concepts so review surfaces them fast — spec Locked Decision 7); `census_complete(conn, floor=15) -> bool` (True when every buildable SINGLE-CORE concept has `games_played >= floor`); `SCREENING_FLOOR = 15`; `STANDARD_SCREENING_FLOOR = 40` (activated pairs / post-census screening — spec Locked Decision 3).

- [ ] **Step 1: Write failing tests.** Assert: `schedule_screening_games` enqueues games only for concepts below the floor; priority is higher for lower-covered concepts; `census_complete` flips True once all buildable single-core concepts hit the floor (pair rows with `status='untested'` must NOT block census completion — they are enumerated-but-dormant until activation, T8); a concept with 0 opponents available (degenerate — single buildable concept) does not deadlock (enqueue 0, `census_complete` still reachable).
  - Degenerate-input probe (`.claude/rules/plan-test-arithmetic-sanity.md` Slice-6 refinement): explicitly test the 1-buildable-concept case and the all-covered case (empty enqueue).

- [ ] **Step 2: Run to verify fail.** → FAIL.

- [ ] **Step 3: Implement.** Select under-covered concepts `ORDER BY coverage.rating ASC NULLS FIRST, games_played ASC` (worst/least-tested first) — restricted to concepts WITH deck rows (singles at census time; activated pairs later), never the dormant pair rows; pair each with a rotating opponent from the buildable field (round-robin cursor stored in `meta`); enqueue with `priority` inversely proportional to current coverage. Both sides use `founding_agent_version` (census rates DECKS with a fixed agent). `census_complete` checks only single-core concepts (`json_array_length(cores) == 1` or an equivalent cores-length predicate). Complexity: O(played-concepts) scan per batch via `ix_concepts_status` — never O(332,520).

- [ ] **Step 4: Run to verify pass.** → PASS.

- [ ] **Step 5: Commit.**
```bash
git add src/ptcg/factory/census.py tests/test_factory_census_schedule.py
git commit -m "feat: census scheduling (worst-rated-first) + completion detection (tournament T7)"
```

## Task 8: Pair-concept enumeration + lazy activation + DB scale checks

**Files:**
- Modify: `src/ptcg/factory/census.py` (pair seeding + activation)
- Test: `tests/test_factory_census_pairs.py`

**Interfaces:**
- Consumes: `builder.enumerate_concepts/build_deck/concept_id/Concept` (T2), `deckdb` (T1/T4), `census.seed_census/schedule_screening_games/STANDARD_SCREENING_FLOOR` (T3/T7).
- Produces: `seed_pair_concepts(conn, batch_size=50_000) -> int` — enumerates ALL `C(815,2) = 331,705` unordered pairs of single-core names via `itertools.combinations(sorted(names), 2)` and inserts them as `concepts` rows with `status='untested'`, **NO deck rows and NO builder call at seed time** (decks are built lazily at activation — building 331,705 decks upfront costs real time + ~100MB for rows that mostly never activate); batched `executemany` inserts, `INSERT OR IGNORE` idempotent; returns rows inserted. `activate_pair_concepts(conn, max_new=10) -> int` — selects dormant pair concepts whose BOTH single cores have proven out (both singles `status='active'` AND census-complete), prioritized by combined single-core rating (best first); for each: `build_deck` (two-core, T2), record unbuildable with reason OR insert the deck row + coverage row and flip `status='active'`; screening for activated pairs uses `STANDARD_SCREENING_FLOOR` (40 games), scheduled by the same `schedule_screening_games` machinery. Called post-census from `loop_tick` (T16).

**Feasibility receipts (executed 2026-07-23, basis for the enumerate-all/play-singles scope):**
- `C(815,2) = 815*814/2 = 331,705`; total concept rows `815 + 331,705 = 332,520` (matches spec decision 9's "332,520+" figure).
- Founding census PLAYS singles only: `815 × 15 = 12,225` games ≈ **6.1 days** @2,000 games/day — fits the ~7-day census box.
- Playing ALL pairs at founding @15 games: `331,705 × 15 = 4,975,575` games ≈ 2,488 days ≈ **6.8 years** @2,000/day; 5.45 years @2,500/day; ~220 days even at the untested 22,539 games/day extrapolation (6 workers × 86,400s / 23 s/game) — **infeasible at every plausible throughput**, hence generational exploration.
- @40 games/pair it is `13,268,200` games ≈ 18.2 years @2,000/day — worse.
- Row storage: ~110 B/row × 331,705 ≈ **36.5 MB** — trivial for SQLite.

**Complexity glance:** enumeration is O(n²) in cores = 331,705 rows, inserted in batched transactions (`executemany`, `batch_size=50_000` per txn — 7 txns); activation query is O(log n + k) via `ix_concepts_status`; NO code path may scan all 332,520 rows per tick (the scale test below asserts the activation query stays fast).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_factory_census_pairs.py
import json, time
from ptcg.factory import deckdb, census

def test_seed_pair_concepts_full_scale_and_indexed_query(tmp_path):
    # FULL-SCALE check (global CLAUDE.md scale-blind-plan-code lesson): run the
    # real 331,705-row enumeration against a temp DB, not a toy subset.
    db = deckdb.connect(tmp_path / "t.db"); deckdb.init_db(db)
    census.seed_census(db)                      # singles first (pair gating reads them)
    t0 = time.perf_counter()
    n = census.seed_pair_concepts(db)
    elapsed = time.perf_counter() - t0
    total = db.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
    singles = db.execute(
        "SELECT COUNT(*) FROM concepts WHERE json_array_length(cores)=1").fetchone()[0]
    assert n == total - singles == singles * (singles - 1) // 2  # C(n,2) exactly
    assert elapsed < 120, f"pair seeding took {elapsed:.1f}s (budget 120s)"
    t0 = time.perf_counter()
    db.execute("SELECT COUNT(*) FROM concepts WHERE status='untested'").fetchone()
    assert time.perf_counter() - t0 < 1.0  # indexed status scan stays sub-second

def test_seed_pair_concepts_idempotent(tmp_path):
    db = _small_seeded(tmp_path)   # helper: monkeypatched enumerate -> 5 cores
    a = census.seed_pair_concepts(db); b = census.seed_pair_concepts(db)
    assert b == 0 and a == 10      # C(5,2)=10, second run inserts nothing

def test_pairs_seeded_without_deck_rows(tmp_path):
    db = _small_seeded(tmp_path)
    census.seed_pair_concepts(db)
    # dormant pairs have no decks and do not block census_complete (T7)
    assert db.execute(
        "SELECT COUNT(*) FROM decks d JOIN concepts c ON d.concept_id=c.id "
        "WHERE json_array_length(c.cores)=2").fetchone()[0] == 0

def test_activate_pairs_gated_on_both_cores_proven(tmp_path):
    db = _small_seeded_with_ratings(tmp_path)  # 2 singles active+rated, rest untested
    n = census.activate_pair_concepts(db, max_new=5)
    assert n == 1  # only the pair whose BOTH cores are active activates
    row = db.execute(
        "SELECT c.status, (SELECT COUNT(*) FROM decks d WHERE d.concept_id=c.id) AS nd "
        "FROM concepts c WHERE json_array_length(c.cores)=2 AND c.status='active'").fetchone()
    assert row is not None and row["nd"] == 1  # deck built lazily at activation
```
(Arithmetic hand-checked: `815*814//2 = 331,705`; `C(5,2) = 10`.)

- [ ] **Step 2: Run to verify fail.** `uv run pytest tests/test_factory_census_pairs.py -v` → FAIL.

- [ ] **Step 3: Implement** `seed_pair_concepts` + `activate_pair_concepts` per the Interfaces block. Pair `cores` stored as `json.dumps(sorted([a, b]))`; id via `concept_id((a, b))` (sorted inside — same content-addressing as singles). Activation gate: both cores' single-core concept rows are `status='active'`; order candidates by `(rating_a + rating_b)` descending; cap per call via `max_new`.

- [ ] **Step 4: Run to verify pass.** `uv run pytest tests/test_factory_census_pairs.py -v` → PASS (4 tests; the full-scale test runs against the real 331,705-row enumeration).

- [ ] **Step 5: Commit.**
```bash
git add src/ptcg/factory/census.py tests/test_factory_census_pairs.py
git commit -m "feat: pair-concept enumeration (331,705 rows) + lazy activation + scale checks (tournament T8)"
```

## Task 9: Census end-to-end smoke + throughput validation (rung-3, real engine)

**Files:**
- Create: `scripts/run_census.py`
- Test: `tests/test_run_census_smoke.py`

**Interfaces:**
- Consumes: everything T1-T8. Produces: `run_census_slice(db_path, n_concepts, games_per_concept, n_workers) -> dict` (seed a SLICE of concepts, schedule, run real games via the pool, refresh ratings, return timing + counts); a `--smoke` CLI. The smoke also calls `seed_pair_concepts` so the fresh-DB run exercises the full 332,520-row enumeration once on real hardware.

- [ ] **Step 1: Write a fast unit test** that runs `run_census_slice` with `n_concepts=2, games_per_concept=2, n_workers=1` against a temp DB with a STUBBED `play_match` (mark `slow`-free) and asserts the DB ends with rated concepts + `done` games + a virgin-dir parent.

- [ ] **Step 2: Run to verify fail.** → FAIL.

- [ ] **Step 3: Implement `run_census.py`** wiring seed→pair-seed→schedule→pool→rating with an explicit `--db` temp path, `--n-concepts`, `--games`, `--workers`, `--smoke`. Log games/day extrapolation.

- [ ] **Step 4: Run the unit test (PASS) AND the rung-3 real-engine smoke.**
  - Automated: `uv run pytest tests/test_run_census_smoke.py -v` → PASS.
  - **Rung 3 (spec "Census smoke" + throughput risk):** run a REAL (non-stubbed) slice end-to-end against a genuinely fresh temp DB path and eyeball output + measure throughput:
    ```bash
    uv run python scripts/run_census.py --db "$TEMP/ptcg_census_smoke/fresh.db" --n-concepts 6 --games 4 --workers 4 --smoke
    ```
    Record real games/day against the spec's ~1,500-2,500 target (spec risk: "throughput needs day-1 hardware validation"). This is a `slow`-class manual step; record the measured number in the plan's verification section — do NOT trust the Slice-2 synthetic extrapolation (`.claude/rules/verify-throughput-before-hypothesis.md`), and note whether the smoke ran under CPU contention (`.claude/rules/time-budgeted-arena-contention.md`). Also record the measured full pair-seed wall time on this hardware.

- [ ] **Step 5: Commit.**
```bash
git add scripts/run_census.py tests/test_run_census_smoke.py
git commit -m "feat: census slice runner + real-engine throughput smoke (tournament T9)"
```

## Task 10: Phase-1 integration test + containment gate

**Files:**
- Test: `tests/test_factory_tournament_phase1.py`

**Interfaces:** Consumes all of T1-T9. Produces: no new prod code — a Phase-1 acceptance test that exercises seed→pair-seed→schedule→claim→record→rate→pair-activate as one flow against a stubbed `play_match`, asserting invariants across module boundaries (no dropped games, ratings monotone with win share, `census_complete` reachable with dormant pairs present, an activated pair gets a lazily-built deck and 40-game screening scheduling).

- [ ] **Step 1: Write the integration test** covering the full Phase-1 pipeline with a deterministic stub match and 3-4 single cores + their pairs (monkeypatched small enumeration).
- [ ] **Step 2: Run to verify it exercises real code** → PASS expected once T1-T9 are in; if it fails, fix the surfaced integration gap.
- [ ] **Step 3: Run the FULL suite** to confirm Phase 1 added zero regressions:
  Run: `uv run pytest -q` → expect all prior tests green + new ones. Report count delta.
- [ ] **Step 4: Confirm ladder-identity + watch-loop untouched (containment §2):**
  ```bash
  git diff master...HEAD -- src/ptcg/submission_main.py src/ptcg/agents/current.py scripts/factory_watch_once.py src/ptcg/factory/cycle.py src/ptcg/factory/evolution.py
  ```
  Expected: EMPTY. If non-empty, a Phase-1 task violated the additive-only rule — STOP and revert that edit.
- [ ] **Step 5: Commit.**
```bash
git add tests/test_factory_tournament_phase1.py
git commit -m "test: Phase-1 tournament integration + containment gate (tournament T10)"
```

---

# PHASE 2 — Baseline-Challenge Loop, Submission Scheduler, UI, Cutover

Phase 2 builds the continuous TRAIN/MATCH/CONFIRM/CROWN loop over Phase-1's foundation, the independent submission scheduler, the localhost review UI (sole surface), and the cutover. Tasks T11-T21. **Containment escalates in T20/T21 (PAUSE window).**

## Task 11: Baseline + offspring state helpers

**Files:**
- Create: `src/ptcg/factory/loop_state.py`
- Test: `tests/test_factory_loop_state.py`

**Interfaces:**
- Consumes: `deckdb` (T1), `ptcg.factory.candidates.parse_version/bump_minor` (`candidates.py:29/36`).
- Produces: `set_founding_baseline(conn, deck_id, agent_config: dict) -> None` (insert `baselines('v0.1', NULL, deck_id, now)` + `meta['baseline_version']='v0.1'`); `current_baseline(conn) -> sqlite3.Row | None`; `next_offspring_version(conn) -> str` (e.g. `v0.1.3`); `insert_offspring(conn, offspring_id, search_config_json, value_net_ref) -> None`; `set_offspring_status(conn, offspring_id, status) -> None`; `list_offspring(conn, status) -> list[Row]`; `crown_baseline(conn, offspring_id, deck_id) -> str` (bump minor, insert `baselines` row, update `meta`, return new version).

- [ ] **Step 1: Write failing tests** including arithmetic-verified version math:
```python
def test_version_progression(tmp_path):
    db = _db(tmp_path)
    loop_state.set_founding_baseline(db, "dA", {"agent_kind":"search-net"})
    assert loop_state.current_baseline(db)["version"] == "v0.1"
    assert loop_state.next_offspring_version(db) == "v0.1.1"   # first offspring under v0.1
    # crown bumps minor: v0.1 -> v0.2  (parse_version/bump_minor verified at candidates.py)
    assert loop_state.crown_baseline(db, "off-1", "dB") == "v0.2"
    assert loop_state.next_offspring_version(db) == "v0.2.1"   # offspring numbering resets under v0.2
```
(Arithmetic hand-check: `bump_minor("v0.1") == "v0.2"` per `candidates.py:36`; offspring patch numbering counts `offspring` rows whose `parent_baseline_version == current`.)

- [ ] **Step 2-4:** Run→fail, implement, run→pass.
- [ ] **Step 5: Commit** `feat: baseline+offspring state helpers over SQLite (tournament T11)`.

## Task 12: TRAIN step — offspring faucet (reuse breeding + trainer)

**Files:**
- Create: `src/ptcg/factory/loop.py` (TRAIN portion)
- Test: `tests/test_factory_loop_train.py`

**Interfaces:**
- Consumes: `ptcg.factory.breeding.mutate_agent/crossover_agent/breed_agent` (`breeding.py:79/90/120`), `ptcg.factory.genomes.GENE_SPEC/CATEGORICAL_GENES/agent_genome_id` (`genomes.py`), `ptcg.factory.trainers.PerDeckNetTrainer` (`trainers.py:31`), `loop_state` (T11). **Founding agent config constant** `FOUNDING_AGENT_CONFIG` — a `search-net` SearchConfig dict + `net_weights="src/ptcg/search/value_net_weights_v2.json"` (**RATIFIED by Brad, 2026-07-23** — the current best net, `run_arena.py:44`).
- Produces: `train_offspring(conn, rng, trainer_factory, now) -> str | None` — breed a `SearchConfig` mutation off the current baseline's agent config via the reused operators, retrain a value net (`PerDeckNetTrainer`), insert an `offspring` row (`status='queued_for_match'`), return offspring id. Target ~4-6/day (throughput-bounded, not a hard cap here).

- [ ] **Step 1: Write failing tests** with a stub `trainer_factory` (no real GPU): assert an offspring row is created with `status='queued_for_match'`, a mutated `search_config_json` that DIFFERS from the parent, and a `value_net_ref`. Reuse-verification: assert the mutated config keys ⊆ `GENE_SPEC ∪ CATEGORICAL_GENES` (breeding operators unchanged).
- [ ] **Step 2-4:** Run→fail, implement (reuse operators verbatim — do NOT re-author breeding), run→pass.
- [ ] **Step 5: Commit** `feat: TRAIN offspring faucet reusing breeding operators (tournament T12)`.

## Task 13: MATCH step — offspring vs top-30 field, pick optimal deck

**Files:**
- Modify: `src/ptcg/factory/loop.py`
- Test: `tests/test_factory_loop_match.py`

**Interfaces:**
- Consumes: `deckdb.enqueue_game`, `rating` (T5), `loop_state` (T11). Produces: `enqueue_match_games(conn, offspring_id, top_n=30, games_per_deck=15) -> int` (select the field's top-30 `active` decks by `coverage.rating`, enqueue `games_per_deck` games each of the offspring agent on that deck vs the baseline agent, `purpose='match'`); `select_optimal_deck(conn, offspring_id) -> str` (after match games are done, pick the deck with the best offspring win% and record it on the offspring row via `set_offspring_deck`).

- [ ] **Step 1: Write failing tests.** Arithmetic: top-30 × 15 = 450 games enqueued (assert exactly `min(30, n_active) * 15`). `select_optimal_deck` returns the deck with the highest offspring win share from `done` `match` games. Degenerate probe: fewer than 30 active decks → enqueue `n_active * 15`, no crash.
- [ ] **Step 2-4:** Run→fail, implement, run→pass. Complexity: top-30 select is O(active) scan / heap; O(1) enqueue per game.
- [ ] **Step 5: Commit** `feat: MATCH step — top-30 opponent pool + optimal-deck pick (tournament T13)`.

## Task 14: CONFIRM step — 200-game series vs baseline → TRASHED/SURVIVOR

**Files:**
- Modify: `src/ptcg/factory/loop.py`
- Test: `tests/test_factory_loop_confirm.py`

**Interfaces:**
- Consumes: `deckdb`, `loop_state`. Produces: `enqueue_confirm_series(conn, offspring_id, n_games=200) -> int` (`purpose='confirm'`, offspring's optimal deck+agent vs baseline); `resolve_confirm(conn, offspring_id) -> str` (when the 200-game series is `done`: measured win rate `< 0.50` → `set_offspring_status('trashed')`, `>= 0.50` → `'survivor'`; returns the status). CONFIRM_GAMES = 200.

- [ ] **Step 1: Write failing tests** — arithmetic hand-checked:
```python
def test_confirm_below_half_trashes(tmp_path):
    db = _offspring_with_confirm(tmp_path, wins=99, games=200)   # 99/200 = 0.495 < 0.50
    assert loop.resolve_confirm(db, "off-1") == "trashed"
def test_confirm_at_half_survives(tmp_path):
    db = _offspring_with_confirm(tmp_path, wins=100, games=200)  # 100/200 = 0.500 >= 0.50
    assert loop.resolve_confirm(db, "off-1") == "survivor"
```
(Hand-verified: `99/200 = 0.495`, `100/200 = 0.500`; the boundary is `>= 0.50` per spec.) **Stochastic note (`.claude/rules/stochastic-gate-replication.md`):** the CONFIRM verdict is a single 200-game series carrying ~±7%-class noise per the spec's accepted trade; this is a per-offspring gate, not the slice acceptance gate, and the spec explicitly accepts single-series noise here backstopped by continuous dethronement — do NOT add replication to CONFIRM (it would change the designed mechanic). Replication applies only to the T21 go-live acceptance smoke.
- [ ] **Step 2-4:** Run→fail, implement, run→pass. Only counts `done` `confirm` games for THIS offspring.
- [ ] **Step 5: Commit** `feat: CONFIRM series → TRASHED/SURVIVOR verdict (tournament T14)`.

## Task 15: CROWN step — survivor round-robin → new baseline + version bump

**Files:**
- Modify: `src/ptcg/factory/loop.py`
- Test: `tests/test_factory_loop_crown.py`

**Interfaces:**
- Consumes: `deckdb`, `loop_state.crown_baseline` (T11), `bt.fit_ratings` (aggregate win% ranking). Produces: `enqueue_crown_round_robin(conn, n_games_per_pair=200) -> int` (when `>=2` survivors exist, enqueue `purpose='crown'` games for every survivor pair, each on its own optimal deck); `resolve_crown(conn) -> str | None` (best aggregate win% survivor becomes new baseline via `crown_baseline`, bump `v0.G -> v0.(G+1)`, mark others back or retire; returns new version or None if <2 survivors or games incomplete).

- [ ] **Step 1: Write failing tests.** With 2 survivors, 1 pair × 200 games; the higher aggregate win% becomes the baseline; `current_baseline` version bumps by one minor. **Complexity glance:** round-robin over K survivors enqueues `C(K,2) = K*(K-1)/2` pairs × 200 games — O(K²) in survivors. K is small (≥2, typically 2-5); assert `enqueue_crown_round_robin` for K=3 enqueues `3 * 200 = 600` games (`C(3,2)=3`). Hand-check: `C(3,2)=3`, `3*200=600`. ✓
- [ ] **Step 2-4:** Run→fail, implement, run→pass.
- [ ] **Step 5: Commit** `feat: CROWN round-robin → new baseline + version bump (tournament T15)`.

## Task 16: Loop scheduler + crash-safe resume + tournament-scheduler entrypoint

**Files:**
- Modify: `src/ptcg/factory/loop.py`
- Create: `scripts/factory_tournament_scheduler.py`
- Test: `tests/test_factory_loop_scheduler.py`

**Interfaces:**
- Consumes: T7 (census scheduling), T8 (`census.activate_pair_concepts` — pair activation), T12-T15 (loop steps), `rating` throttle. Produces: `loop_tick(conn, rng, trainer_factory, now) -> dict` — one idempotent advance: if census incomplete → `schedule_screening_games` + throttled `refresh_field_ratings`; else drive TRAIN/MATCH/CONFIRM/CROWN by querying offspring statuses (pure DB state, no in-memory state — spec Resumability) **plus a per-tick `census.activate_pair_concepts(conn, max_new=...)` call post-census** so pair space opens generationally as cores prove out. A `scripts/factory_tournament_scheduler.py` main loops `loop_tick` on a cadence, honoring PAUSE.

- [ ] **Step 1: Write failing tests INCLUDING a resume test** (spec "Loop resume tests"): drive an offspring to mid-CONFIRM (100 of 200 games done), then call `loop_tick` on a FRESH connection with no in-memory state and assert it resumes CONFIRM from game 100 (enqueues only the remaining 100, does not restart the series). Plus an interleaved test: two scheduler ticks racing must not double-enqueue a series (guard via offspring status transition under Pattern SQLITE-TXN). Plus: post-census tick calls pair activation (assert via sentinel/monkeypatch); pre-census tick does NOT.
- [ ] **Step 2-4:** Run→fail, implement (every stage transition + enqueue guarded so a second tick sees the advanced status and no-ops), run→pass.
- [ ] **Step 5: Commit** `feat: crash-safe loop scheduler + resume + pair-activation hook (tournament T16)`.

**AMENDMENT 2026-07-24 (T10 phase-gate carry-forward):** loop_tick's post-census branch MUST call `census.promote_proven_singles(conn, floor=SCREENING_FLOOR)` after `rating.refresh_field_ratings` and before `census.activate_pair_concepts` — Phase 1 shipped the function (T10 gap-fix, commit 0403a61) but its production caller is T16; without this call, singles never leave 'untested' and pair activation is a production no-op. Also owed by T16 per Phase-1 reviews: orphaned-'claimed' reclaim + poison-game requeue cap/dead-letter with DB-persisted error.

## Task 17: Submission scheduler — 4.8h marks + 24h daily-floor probe

**Files:**
- Create: `src/ptcg/factory/subscheduler.py`
- Test: `tests/test_factory_subscheduler.py`

**Interfaces:**
- Consumes: `ptcg.factory.gate.SubmissionCounter/HARD_DAILY_CAP` (`gate.py:38/16`), `ptcg.factory.submit._build_and_upload/submission_description` (`submit.py:95/40`), `ptcg.factory.kaggle_client.check_auth` (`kaggle_client.py:129`), `ptcg.factory.bundles.build_candidate_bundle/verify_candidate_bundle`, `loop_state.current_baseline`, `deckdb`, Pattern ATOMIC-JSON for scheduler state.
- Produces: `MARK_SECONDS = 17280` (4.8h); `mark_index(now_utc) -> int` (`int(seconds_since_utc_midnight // MARK_SECONDS)`, range 0-4); `maybe_submit(conn, client, counter, state_path, out_dir, repo, now, no_submit) -> list[tuple]` — the independent-clock submission decision (see logic below). **Champion-pairing guard STAYS RETIRED — submit exactly ONE identity, no re-upload** (spec Locked Decision 2 replacement + Retired Systems).

Logic (cite `.claude/rules/platform-mechanics-model.md`: Kaggle counts the two most-recent, evicts by recency not score):
1. If `no_submit` → dry-run (never touch counter/network).
2. `check_auth(client)`; on failure log `AUTH-DEAD` and return (skip entire phase — no reservations).
3. Load scheduler state (`last_upload_at`, `last_uploaded_identity`, `last_mark_fired`) via Pattern ATOMIC-JSON.
4. **Mark-triggered:** if `(utc_date, mark_index(now))` differs from `last_mark_fired` AND `current_baseline`'s identity differs from `last_uploaded_identity`: reserve 1 slot atomically (`counter.try_reserve(today, 1)`); `_build_and_upload` the baseline; on success record identity + `last_upload_at` + version tag (crown bump already happened in T15). Exactly one new identity per changed mark.
5. **Daily floor:** else if `now - last_upload_at >= 86400`: pick the next-best UNPROBED candidate (best-rated `active` deck not yet ladder-probed), reserve 1 slot, upload as a **PROBE** — description carries the CURRENT baseline version tag + the probe deck's concept id, does NOT bump version; record `last_upload_at` (but NOT `last_uploaded_identity`, so it doesn't count as a crown upload).
6. Never split; never exceed `HARD_DAILY_CAP`; `SUBMIT_HOLD` respected by the CALLER (T20 checks `submit_hold_file` before invoking — mirror `cycle.py:256`).

- [ ] **Step 1: Write failing tests** — arithmetic + adversarial:
```python
def test_mark_index_five_per_day():
    assert subscheduler.MARK_SECONDS == 17280            # 4.8*3600
    assert 86400 // subscheduler.MARK_SECONDS == 5        # exactly 5 marks/day
    # midnight, +4.8h, +9.6h, +14.4h, +19.2h -> indices 0..4
    from datetime import datetime, timezone
    base = datetime(2026,8,1,tzinfo=timezone.utc)
    assert [subscheduler.mark_index(base.replace(hour=h,minute=m))
            for h,m in [(0,0),(4,48),(9,36),(14,24),(19,12)]] == [0,1,2,3,4]

def test_mark_fires_only_on_baseline_change(...):  # unchanged identity at a new mark -> no upload
def test_daily_floor_probe_after_24h_idle(...):    # probe fires, does NOT change last_uploaded_identity
def test_auth_dead_skips_entire_phase_no_reservation(...):
def test_cap_reservation_atomic_under_concurrent_entry(tmp_path):  # ADVERSARIAL (reuse 178043b pattern)
    # two maybe_submit entrants from count=4 must not both admit (cap=5); assert
    # total reserved <= HARD_DAILY_CAP via SubmissionCounter.try_reserve serialization
```
(Hand-verified: `4.8*3600 = 17280`; `86400 // 17280 = 5`; `(4,48)→(4*3600+48*60)=17280→idx 1`; `(19,12)→69120→idx 4`. ✓)
- [ ] **Step 2-4:** Run→fail, implement, run→pass. Reuse `SubmissionCounter` (already atomic) — do NOT reimplement reservation.
- [ ] **Step 5: Commit** `feat: submission scheduler (4.8h marks + 24h probe), champion-pairing retired (tournament T17)`.

## Task 18: Localhost UI server — bottom-10 review + Remove/Pass

**Files:**
- Create: `src/ptcg/factory/ui_server.py`
- Create: `scripts/factory_ui.py`
- Test: `tests/test_factory_ui_server.py`

**Interfaces:**
- Consumes: `deckdb` (T1), `builder` (deck→names for expandable 60-card view). Produces: `bottom_ten(conn, min_games=15) -> list[dict]` (10 lowest-rated `active` decks meeting the min-games floor, each with its full card list); `apply_decision(conn, deck_id, action, actor="brad") -> None` (atomic: insert `decisions` row + on `remove` flip the concept `status='culled'`, on `pass` leave `active`); `make_app(conn_factory)` → a stdlib `http.server.BaseHTTPRequestHandler` bound to `127.0.0.1` ONLY (no auth — spec Risks); `GET /` renders the review page, `POST /decision` applies Remove/Pass. `scripts/factory_ui.py` carries an in-process single-instance lock (reuse `watch.instance_lock`'s sidecar-lock shape against a UI-owned lock path) so the T21 watchdog trigger can fire `IgnoreNew`-style without stacking servers.

- [ ] **Step 1: Write failing tests** — including decision atomicity under concurrent read (Pattern INTERLEAVED-TEST):
```python
def test_bottom_ten_lowest_rated_with_min_games(...):
def test_apply_remove_culls_concept_and_logs_decision(...):
def test_apply_pass_keeps_active_and_logs(...):
def test_decision_write_atomic_under_concurrent_scheduler_read(tmp_path):  # INTERLEAVED
    # a POST /decision (remove) concurrent with a scheduler SELECT of active decks
    # must be all-or-nothing: the reader sees either fully-active or fully-culled,
    # never a half-applied state (decision row present but status still active).
def test_ui_decision_log_virgin_dir(tmp_path):  # VIRGIN-DIR-TEST for any UI-owned file
def test_server_binds_localhost_only():          # assert server_address host == '127.0.0.1'
```
- [ ] **Step 2-4:** Run→fail, implement (`HTTPServer(("127.0.0.1", port), Handler)`; `apply_decision` inside Pattern SQLITE-TXN so decision-insert + status-flip are atomic; HTML rendered with `encoding="utf-8"`; expandable rows show card NAMES via `builder`/engine DB), run→pass.
- [ ] **Step 5: Commit** `feat: localhost bottom-10 Remove/Pass review UI (tournament T18)`.

## Task 19: UI status page — sole-surface replacement for dashboard.html (PD-B override)

**Files:**
- Modify: `src/ptcg/factory/ui_server.py` (add the live status page)
- Test: `tests/test_factory_ui_dashboard.py`

**Interfaces:** Produces: a `GET /status` route on the UI server rendering the live status content the static dashboard covered — status strip (paused / SUBMIT_HOLD / submission-counter / auth state), current baseline + version, offspring pipeline counts by status, census/coverage progress (singles played, pairs activated), recent games throughput — all read-only queries against `deckdb` + the existing state files. This page REPLACES `experiments/factory/dashboard.html` as the sole status surface (Brad override, 2026-07-23).

**Inertness lever (live-on-write):** this task only ADDS a route to the not-yet-registered UI server — nothing in the live watch loop changes; `dashboard.safe_render` keeps firing on master behavior until the T20 PAUSE-window cutover removes it. The static renderer's retirement is T20's edit, deliberately NOT this task's.

- [ ] **Step 1: Write failing tests:** `GET /status` returns 200 with baseline version + offspring counts + census progress; the handler never writes to the DB (read-only assertion via a write-forbidding connection wrapper or `PRAGMA query_only=ON` on the handler's connection).
- [ ] **Step 2-4:** Run→fail, implement, run→pass.
- [ ] **Step 5: Commit** `feat: UI live status page — sole-surface dashboard replacement (tournament T19)`.

## Task 20: Cutover — rewire watch loop (submission scheduler + surviving harvester, dashboard removed), new registration script

**Files:**
- Modify: `scripts/factory_watch_once.py` (narrow: gate/submit → `subscheduler.maybe_submit`; KEEP `episodes.check_and_harvest`; drop `evolution_tick`/`deck_matrix` refill/meta-anchor injection/`dashboard.safe_render`)
- Create: `scripts/register_tournament_tasks.ps1`
- Test: `tests/test_factory_watch_cutover.py`, `tests/test_register_tournament_tasks.py`

**INERTNESS / CONTAINMENT (this task edits the LIVE watch-loop import graph):** Before implementing, **touch `experiments/factory/PAUSE`** so all three current workers stop importing/executing the mutating tree; implement + review; verify via `watch.log` that firings during this window log `paused`, not new tournament behavior; remove `PAUSE` only after the change is reviewed AND `SUBMIT_HOLD` is confirmed still present. Name this in the task. The new tournament tasks are still NOT registered here — registration is T21 (post-merge).

**Interfaces:**
- Consumes: `subscheduler.maybe_submit` (T17), `loop_state`, `deckdb`, `episodes.check_and_harvest` (`episodes.py:237` — SURVIVES per Brad's PD-A override). Produces: a rewired `watch_once` whose per-firing job is: PAUSE guard → instance lock → **episode harvest** (`check_and_harvest`, failure-isolated exactly as today, stamp-gated internally — its new permanent home per the Plan-Review Decisions section) → `subscheduler.maybe_submit` (caller checks `submit_hold_file` first, mirroring `cycle.py:256`). REMOVED from the loop: `evolution.evo_gate_step`, `deck_matrix.refill_queue`, `episodes.top_meta_decks`/`inject_meta_anchors` (meta-anchor consumer retired with the pool system), and ALL FOUR `dashboard.safe_render` call sites (PD-B override — `dashboard.py`/`render_dashboard.py` retired-in-place, left on disk unwired). **Future-hook note (PD-A):** the reserved consumer hook name `census.inject_meta_concepts(paths, decks)` is documented in a comment at the harvest call site — NOT implemented this slice.
- `register_tournament_tasks.ps1` (Pattern PS1-REGISTER): register NEW `ptcg-factory-runner`, `ptcg-factory-scheduler`, `ptcg-factory-ui` (UI: **AtStartup trigger + 15-minute watchdog repetition + `-MultipleInstances IgnoreNew`** — the availability decision from the Plan-Review Decisions section, same shape as the existing worker watchdogs) BEFORE retiring `ptcg-factory-matrix` (`Unregister`); retarget `ptcg-factory-trainer` (updates its action to the offspring-faucet entrypoint); keep `ptcg-factory-continuous` (narrowed). Register-before-retire, loud `exit 1` on failure, `Get-ScheduledTask` verify each, `-UvPath`/probe uv resolution, `-DryRun` branch, `Start-Transcript` for the elevated real run.

- [ ] **Step 1: Write failing tests.** Python side: `watch_once` no longer calls `evolution`/`deck_matrix`/`inject_meta_anchors`/`dashboard.safe_render` (assert via monkeypatched sentinels that they are NOT invoked), DOES still call `episodes.check_and_harvest` (harvester survival — sentinel asserts it IS invoked, failure-isolated), and DOES call `subscheduler.maybe_submit`; SUBMIT_HOLD present → no submit. PS1 side (mirror `tests/test_register_factory_task.py`): `-DryRun` prints register-before-retire ordering for the 3 new tasks (UI with AtStartup + watchdog trigger lines) + matrix retire + trainer retarget and NEVER touches the real scheduler; the script's real branch uses `-ErrorAction Stop` + `exit 1`.
  - **Constant/output-format grep (global CLAUDE.md rule):** `grep -rn "ptcg-factory-matrix\|ptcg-factory-nightly\|safe_render\|render_dashboard\|inject_meta_anchors\|evo_gate_step\|refill_queue" tests/ scripts/ src/ .claude/rules/` and update EVERY assertion site this cutover changes — known existing sites: `tests/test_factory_watch.py` (asserts safe_render/harvest/evo-gate wiring), `tests/test_factory_dashboard.py`, `tests/test_factory_episodes.py`, `tests/test_register_factory_task.py`, plus the task-name references in `.claude/rules/factory-resume-probe.md` / `factory-task-scheduler-liveness.md` (rules-file updates happen at T21 Rung G, but enumerate them here). Grep tests/ for the SCRIPT names, not just constants. List all touched sites in the task DoD.
- [ ] **Step 2-4:** Run→fail, implement, run→pass. Verify `watch.log` shows a normal narrowed line (`episodes: ...` + `submit: held (SUBMIT_HOLD present)`) after the change with `PAUSE` removed and `SUBMIT_HOLD` present — and NO new `dashboard.html` write (mtime frozen).
- [ ] **Step 5: Commit** `feat: cutover — narrowed watch loop (subscheduler + surviving harvester, dashboard retired) + tournament task registration script (tournament T20)`.

## Task 21: POST-MERGE GO-LIVE rungs (production scheduler + SUBMIT_HOLD lift)

**Files:** none (operational). This task is explicitly a **POST-MERGE go-live sequence** (master = production), NOT a Finish-blocking gate. Do NOT gate the branch's Finish on it; run it after merge.

**Go-live sequence (spec Locked Decision 12), each rung logged:**
- [ ] **Rung A — PAUSE + stop old workers.** Touch `experiments/factory/PAUSE`. `Stop-ScheduledTask ptcg-factory-matrix`, `ptcg-factory-trainer` (they are LONG-LIVED and do NOT hot-reload — `.claude/rules/factory-resume-probe.md`). Confirm `SUBMIT_HOLD` still present.
- [ ] **Rung B — register new tasks (ELEVATED, capture output).** Registration needs elevation (harness PowerShell is non-elevated). Run via `Start-Process -Verb RunAs` with `Start-Transcript` capturing to a file (the elevated console closes on exit):
  ```powershell
  # in an elevated shell, transcript-captured:
  powershell -ExecutionPolicy Bypass -File scripts\register_tournament_tasks.ps1 -UvPath "<explicit uv.exe path>"
  ```
  Resolve `uv.exe` by explicit `-UvPath` (per-user install is invisible on the elevated PATH — commit `225686c` lesson).
- [ ] **Rung C — independent verification.** In a SEPARATE query (not the script's own output — `.claude/rules/factory-task-scheduler-liveness.md`):
  ```powershell
  foreach ($t in "ptcg-factory-runner","ptcg-factory-scheduler","ptcg-factory-ui","ptcg-factory-continuous","ptcg-factory-trainer") {
    Get-ScheduledTask -TaskName $t | Select-Object TaskName, State
    Get-ScheduledTaskInfo -TaskName $t | Select-Object LastRunTime, LastTaskResult
  }
  Get-ScheduledTask -TaskName "ptcg-factory-matrix" -ErrorAction SilentlyContinue  # expect ABSENT (retired)
  ```
- [ ] **Rung D — Start + confirm CURRENT code (not stale process).** `Start-ScheduledTask` the new + retargeted workers. Confirm liveness by NEW block/output provenance carrying a commit at/after the merge (not just `State=Running` — long-lived-worker-staleness rule): check the runner pool's first `done` game timestamp and the scheduler's first `loop_tick` log line land AFTER the merge commit.
- [ ] **Rung E — census + UI rung-3 smoke on the REAL fresh production DB.** Confirm `tournament.db` is created virgin at go-live, the census begins (**all 332,520 concept rows seeded — 815 singles with decks, 331,705 dormant pairs**, first screening games `done`), AND the sole status surface is up: `ptcg-factory-ui` reachable at `http://127.0.0.1:<port>/` and `/status` (200, shows baseline v0.1 + census progress), while `experiments/factory/dashboard.html`'s mtime has STOPPED advancing (static dashboard confirmed retired). This is the genuinely-fresh-location smoke (global CLAUDE.md virgin-dir rung-3).
- [ ] **Rung F — lift SUBMIT_HOLD + first real submission (REPLICATED verification).** Remove `experiments/factory/SUBMIT_HOLD`. Remove `PAUSE`. Watch the FIRST submission-scheduler mark fire a real upload; verify it went through (ref id in `LADDER.md`/Kaggle list) and the counter incremented atomically. **Stochastic acceptance (`.claude/rules/stochastic-gate-replication.md`):** confirm the end-to-end submit path across at least TWO firings (one mark-triggered on baseline change AND one daily-floor probe path if reachable), reporting both, before declaring go-live healthy. Also confirm one post-cutover `episodes:` harvest line in `watch.log` (harvester survival verified live). Note: `uv run kaggle` CLI is NOT resolvable on this machine — use the factory's internal `kaggle_client`, not the CLI, for any verification (repo ground truth).
- [ ] **Rung G — record go-live** in `experiments/EXPERIMENTS.md` + memory; update `.claude/rules/factory-resume-probe.md` + `.claude/rules/factory-task-scheduler-liveness.md` task-name lists to the new tournament tasks (runner/scheduler/ui + narrowed continuous + retargeted trainer; matrix retired; dashboard.html no longer a heartbeat surface — the UI `/status` page replaces it in the probe checklist).

---

## Self-Review

**1. Spec coverage** (each spec section → task; includes the 2026-07-23 plan-review overrides):
- Locked Decision 1 (ISMCTS only) → Global Constraints + T12 `FOUNDING_AGENT_CONFIG` (no HeuristicAgent). ✓
- LD2 (submission model, champion-pairing retired) → T17. ✓
- LD3 (staged budgets) → T7 (15 census / 40 standard screening), T13 (15×top-30), T14 (200 CONFIRM), T15 (200/pair CROWN). ✓
- LD4 (Founding Census / 815 singles / pair concepts / deterministic builder / unbuildable recorded) → T2, T3, T7, **T8 (pair enumeration in-slice per Brad's 2026-07-23 scope expansion: enumerate-all 332,520, play-singles, lazy generational activation)**. ✓
- LD5 (versioning v0.1→v0.2, offspring v0.G.k) → T11, T17. ✓
- LD6 (4-8 runner pool, BelowNormal) → T6; throughput validation → T9. ✓
- LD7 (UI bottom-10, Remove/Pass, advisory pace) → T18. ✓
- LD8 (no generation_days — superseded) → not implemented (correctly absent); loop is continuous → T16. ✓
- LD9 (SQLite schema incl. offspring/baselines; 332,520-row scale) → T1 (+`ix_concepts_status`), T8 (scale test), T11. ✓
- LD10 (trainer as offspring faucet) → T12. ✓
- LD11 (Baseline-Challenge Loop, crash-safe) → T12-T16. ✓
- LD12 (workers & cutover, go-live, rollback) → T20, T21. ✓
- LD13 (calendar/phasing) → Phase 1 (T1-10) / Phase 2 (T11-21). ✓
- Baseline-Challenge Loop section (BOOTSTRAP/TRAIN/MATCH/CONFIRM/CROWN) → T3/T7, T12, T13, T14, T15. ✓
- Submission Scheduler (marks, daily floor, freeze protocol) → T17 (freeze protocol = the ~2026-08-14 auto-hold, an operational standing reminder in Global Constraints, not a coded task — matches the spec's "locked" operational framing). ✓
- **Plan-review overrides (2026-07-23):** harvester KEPT → T20 (survives the narrowing, sentinel-tested) + Rung F live check; dashboard REPLACED → T19 (`/status` sole surface) + T20 (safe_render removal + grep sweep) + Rung E mtime check; pair enumeration → T2/T8/T16; founding net ratified → T12. ✓
- Testing Strategy (interleaved, golden, resume, runner concurrency, UI atomicity, virgin-dir, census smoke) → T4/T5/T16/T17/T18 interleaved; T2 golden; T16 resume; T6 concurrency; T18 UI atomicity; T1/T3/T9/T18 virgin-dir; T9 census smoke; **T8 full-scale pair-enumeration test (the first-production-scale-run-as-test rule)**. ✓
- Risks (throughput, local≠ladder, census noise, builder quality, UI attack surface) → T9 throughput; T17 accepts local≠ladder; T7 Pass option; T2 shell variants noted; T18/T19 localhost-only binding. ✓ with note below.

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N" — each task carries real code or exact signatures + the golden-fixture generation command. The golden core name `"Pikachu ex"` is explicitly flagged as "verify buildable at impl time" (not a silent placeholder). Amended sections (T8, T19, T20, T21, Plan-Review Decisions) re-scanned clean.

**3. Type consistency:** `Concept(cores: tuple[str,...])` consistent T2→T3→T8→T12; `winner` int semantics (0/1/2) consistent T1/T4/T6; `status` domains consistent with CHECK constraints (dormant pairs use `untested`, activation flips to `active` — same domain, no new states); version strings via `parse_version`/`bump_minor` consistent T11/T17; `agent_configs`/`agent_config` dict shape consistent T6/T12; `STANDARD_SCREENING_FLOOR` defined T7, consumed T8/T16.

**Deferred/flagged for Brad (not silent scope drift):**
- **Shell variants (3-5 per surviving core)** — spec LD4 lists shell variants as post-census continuous seeding. Pair concepts are now IN-slice (Brad 2026-07-23); shell-variant enumeration remains the one deferred LD4 element — a Phase-3 fast follow (the `decks.shell_variant` column and `deck_id(concept_id, shell_variant)` are already forward-compatible). Named here, not dropped.
- **JUDGMENT CALL (PD-A consumer):** extracts retained as data asset + reserved hook name `census.inject_meta_concepts`, no consumer built this slice — the spec's tournament model defines no episode consumer, so building one would be unsanctioned scope.
- **JUDGMENT CALL (PD-B availability):** `ptcg-factory-ui` registered AtStartup + 15-min watchdog (auto-restart) rather than manual start, since the sole surface must survive unattended operation.

## Landmark Verification

Every cited landmark grepped against the real repo before lock (global CLAUDE.md pre-lock landmark grep). `file:line → confirmed`:

| Landmark | Location | Confirmed |
|---|---|---|
| `pool_summary()` attacker enumeration | `src/ptcg/decks/analysis.py:57` | ✓ returns `pokemon`; attacker=damaging-attack filter; **815 distinct names / 442 basic / 373 evo VERIFIED by executable engine query** (952 cardIds → 815 names) |
| `validate_deck(deck)->list[str]` | `src/ptcg/decks/validate.py:22` | ✓ checks 60/≤4-copies/≥1-basic/≤1-ACE — NOT Brad's energy/supporter/item bounds (builder's job, T2) |
| `SearchConfig` (15 fields) | `src/ptcg/search/searcher.py:74` | ✓ fields incl. rollout_depth/deviate_*/c_puct/final_move_rule confirmed |
| `build_agent(candidate, deck)` | `src/ptcg/factory/evaluate.py:53` | ✓ constructs heuristic/search-net/search-policy from `candidate.agent_config` |
| `play_match` / `run_series` / `MatchResult` | `src/ptcg/arena/runner.py:50/89/41` | ✓ winner 0/1/2/-1; module-global engine (per-process requirement) |
| `SubmissionCounter.try_reserve/release` + `HARD_DAILY_CAP=5` | `src/ptcg/factory/gate.py:70/93/16` | ✓ atomic reserve, cap never raised |
| `_build_and_upload` / `submission_description` | `src/ptcg/factory/submit.py:95/40` | ✓ reusable low-level upload (bypasses champion-pairing wrapper `submit_candidates:130`) |
| `check_auth(client)->str\|None` | `src/ptcg/factory/kaggle_client.py:129` | ✓ pre-submit auth probe |
| `bt.fit_ratings(wins)->dict` | `src/ptcg/factory/bt.py:28` | ✓ iterative MM fit, MAX_ITERS+TOL, tested (`test_factory_bt.py`) — reused, not re-authored |
| `mutate_agent/crossover_agent/breed_agent`, `GENE_SPEC`, `CATEGORICAL_GENES` | `src/ptcg/factory/breeding.py:79/90/120`, `genomes.py:27/42` | ✓ SearchConfig gene operators reused for TRAIN |
| `PerDeckNetTrainer.prepare_data/train/export_and_register` | `src/ptcg/factory/trainers.py:31/52/62/70` | ✓ trainer reused for offspring faucet |
| `Candidate`/`Status`/`create`/`parse_version`/`bump_minor`/`ledger_lock` | `src/ptcg/factory/candidates.py:46/19/84/29/36/127` | ✓ config carrier + version math |
| `FactoryPaths`/`run_cycle`/`submit_hold_file`(→`experiments/factory/SUBMIT_HOLD`)/`pause_file` | `src/ptcg/factory/cycle.py:19/164/51/47` | ✓ SUBMIT_HOLD/PAUSE handling to mirror |
| `watch_once` (live watch loop) | `scripts/factory_watch_once.py` | ✓ narrowed in T20; import graph frozen in Phase 1 |
| `episodes.check_and_harvest` (harvester SURVIVES, PD-A) | `src/ptcg/factory/episodes.py:237` | ✓ exists; failure-isolated call site in `watch_once` retained |
| `episodes._stamp_path` → `harvest_stamp.json` (stamp gating) | `src/ptcg/factory/episodes.py:74-75` | ✓ internal stamp gate unchanged |
| `episodes._extracts_path` / `top_meta_decks` / `inject_meta_anchors` | `src/ptcg/factory/episodes.py:78/290/350` | ✓ extracts retained; meta-anchor consumer retired at T20 |
| `dashboard.safe_render` (retired at cutover, PD-B) | `src/ptcg/factory/dashboard.py:853` | ✓ exists; all four `watch_once` call sites removed in T20 |
| `scripts/render_dashboard.py` (retired-in-place) | `scripts/render_dashboard.py` | ✓ exists; unwired at T20, left on disk |
| Cutover-grep assertion sites | `tests/test_factory_watch.py`, `tests/test_factory_dashboard.py`, `tests/test_factory_episodes.py`, `tests/test_register_factory_task.py` | ✓ all reference safe_render/dashboard/harvest wiring — enumerated in T20's grep sweep |
| `watch.instance_lock` (UI single-instance shape) | `src/ptcg/factory/watch.py:13` | ✓ sidecar-lock helper reused by `scripts/factory_ui.py` |
| `register_factory_task.ps1` (PS1-REGISTER reference) | `scripts/register_factory_task.ps1` | ✓ register-before-retire + loud fail + uv probe + DryRun + AtStartup/watchdog worker-trigger shape (reused for the UI task) |
| Scheduled task names | (from `register_factory_task.ps1`) | ✓ `ptcg-factory-continuous`/`-matrix`/`-trainer` (retire matrix, retarget trainer, add runner/scheduler/ui) |
| `value_net_weights_v2.json` (founding net — RATIFIED 2026-07-23) | `src/ptcg/search/value_net_weights_v2.json`, `scripts/run_arena.py:44` | ✓ exists |
| `sqlite3` / `http.server` usage | (grep `src/`,`scripts/`) | ✓ NONE — both genuinely new for this slice |
| `POOL_CAP=24`, `evolution_tick`, `deck_matrix.refill_queue` | `tournament.py:32`, `evolution.py`, `deck_matrix.py` | ✓ exist; retired/bypassed at cutover (T20) |

**Arithmetic verified (executed 2026-07-23):** `4.8*3600=17280`; `86400//17280=5`; `24/4.8=5`; census singles `815*15=12,225` games ≈ 6.11 days @2,000/day; `C(815,2)=815*814/2=331,705`; total rows `815+331,705=332,520`; pairs-at-founding `331,705*15=4,975,575` games ≈ 2,487.8 days ≈ 6.82 yr @2,000/day (5.45 yr @2,500/day; ~220 days @22,539/day extrapolation); pairs @40 `13,268,200` ≈ 18.2 yr @2,000/day; pair-row storage ≈ 36.5 MB @110 B/row; MATCH `30*15=450`; CONFIRM `99/200=0.495<0.5`, `100/200=0.5`; CROWN `C(3,2)=3`, `3*200=600`; `C(5,2)=10` (small-pool test constant).

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-23-generational-champion-tournament.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks. Given the LIVE-ON-WRITE containment and multi-actor invariants, this is the safer choice. Split at the Phase 1/2 boundary (T10) into two sessions per the one-lifecycle-per-session budget.

**2. Inline Execution** — batch with checkpoints via `superpowers:executing-plans`.

**Which approach?**
