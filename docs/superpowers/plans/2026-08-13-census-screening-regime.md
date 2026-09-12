# Census/Screening Regime Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Materialize per-deck composition counts (`energy_count`/`pokemon_count`) on the `decks` table, make census screening order composition-aware as a tie-break within coverage order, measure the current pool's composition/WR landscape, and — behind an explicit Brad gate — run a composition cull migration and recalibrate `FLOOR_BAR`/`ANCHOR_BAR`.

**Architecture:** A pure counting helper in `builder.py` (reusing `brad_bounds_problems`' classification) feeds three delivery paths: (1) DDL columns + stamping at every live deck-insert site (virgin DBs), (2) a one-shot `ALTER TABLE` + backfill migration for the existing production DB (go-live only — deliberately NOT in `init_db`, keeping the slice inert against the watch loop's ~15-minute `init_db` calls), and (3) a new ORDER BY in `census._CANDIDATES_QUERY` that consumes the columns through the existing canonical-deck join. Measurement (read-only, in-process games) produces the receipt that drives the Brad gate; the cull migration and bar constants are blocked until that gate passes.

**Tech Stack:** Python 3.11 / uv, SQLite (WAL, `BEGIN IMMEDIATE` discipline via `deckdb`), pytest, the vendored `cg` engine SDK (`all_card_data`), `ptcg.arena.runner.play_match` for in-process games.

**Spec:** `docs/superpowers/specs/2026-08-13-census-screening-regime-design.md` (commit `67a1d70`) — decisions LOCKED.

## Global Constraints

Copied from the spec + repo rules; every task's requirements implicitly include these.

- **Locked decisions (spec):** (1) priority = TIE-BREAK within coverage order — not composition-first, not a scored blend; (2) cull = measure first, Brad-gated threshold via AskUserQuestion before the one-shot migration runs; (3) bars = percentile-anchored (FLOOR_BAR: chosen fraction of current healthy pool fails at birth; ANCHOR_BAR: baseline lineage's measured WR plus a margin); (4) count delivery = materialized columns on `decks`; (5) one slice covers ordering + bar recalibration.
- **Breeding exclusion:** the directive applies to screening order and pruning/culling only — NOT to breeding/offspring selection. Stamping breeding-adjacent insert sites is data hygiene, not prioritization.
- **Inertness:** the backfill is NEVER placed in `deckdb.init_db()` (the watch loop calls it every ~15 min against production). `census.py`/`floor.py`/`anchor.py` load only in the long-lived, restart-gated scheduler. Any DDL addition executed by `init_db` MUST be a no-op against the pre-migration production schema (see Task 2's conditional index — an unconditional `CREATE INDEX` naming the new columns would crash every watch-loop firing).
- **Freeze window:** `SUBMIT_HOLD` stays ON until 2026-08-17. Nothing in this slice uploads anything.
- **Thin disk margin:** no large artifacts. Do not copy `tournament.db` (~180MB+); the measurement script reads production READ-ONLY via a `mode=ro` URI instead. If any task ever makes a DB copy, that same task deletes it before finishing.
- **NULL semantics:** NULL composition counts sort LAST — an unstamped deck never jumps the queue.
- **Test running:** implementers run ONLY their own targeted test file(s) (`uv run pytest tests/<file> -v`). The orchestrator owns full-suite runs at sync points (`.claude/rules/dispatch-test-run-directive.md`). Never use Monitor or `run_in_background` for tests.
- **Loaded machine:** factory workers run 24/7; known load-sensitive tests (fail loaded, pass isolated): `tests/test_train_policy_export.py`, `tests/test_factory_ui_server.py::test_decision_write_atomic_under_concurrent_scheduler_read`, `tests/test_factory_tournament_ledger.py::test_adversarial_two_process_concurrency`. Re-run failures alone before calling them regressions.
- **Git:** stage AND commit by explicit pathspec, pathspec BEFORE `-m` (`git add <paths>` then `git commit <paths> -m "..."`). Never bare `git commit` / `git add .` (`.claude/rules/parallel-dispatch-commit-hygiene.md`). On `index.lock` contention, wait 2s and retry up to 3x.
- **Encoding:** every Python text write uses `encoding="utf-8"` (`write_text`, `open`) — Windows cp1252 truncate-then-crash hazard.
- **Concurrency tests:** if any task adds a concurrency receipt, use `tests/fixtures/race.py` (barrier+counter+overlap); never hold a lock across a barrier wait. This plan adds no new multi-actor invariant — both migrations are single `BEGIN IMMEDIATE` read-decide-act transactions run with workers held, and the query change lives inside the existing `schedule_screening_games` lock already guarded by `tests/test_factory_lock_access_paths.py`.
- **Plan-authored arithmetic:** every expected value below was hand-computed at plan time (the anchor-deck composition was verified by executing the count against the engine DB: 22 energy / 12 Pokémon / 26 trainers / 8 basics). Implementers still re-verify before transcribing (`.claude/rules/plan-test-arithmetic-sanity.md`).

### Task dependency / parallelism map

| Task | Files touched | Depends on | Parallel-safe with |
|---|---|---|---|
| T1 helper | `builder.py`, `tests/test_factory_builder.py` | — | — (foundation, run first) |
| T2 DDL+stamping | `deckdb.py`, `census.py`, `anchor.py`, `scripts/reseed_tournament_pool.py`, new `tests/test_factory_composition_stamping.py` | T1 | T3, T5 |
| T3 column migration | new `scripts/migrate_composition_columns.py`, new `tests/test_migrate_composition_columns.py` | T1 | T2, T5 |
| T4 query+EQP | `census.py`, `tests/test_factory_census_schedule.py`, `tests/test_factory_lock_access_paths.py` | T2 (shares `census.py` — SERIALIZED after T2) | T3, T5 |
| T5 measurement | new `scripts/measure_screening_regime.py`, new `tests/test_measure_screening_regime.py` | T1 | T2, T3 |
| T6 BRAD GATE | none (AskUserQuestion) | T5's real run (orchestrator-owned) | — |
| T7 cull migration | new `scripts/migrate_composition_cull.py`, new `tests/test_migrate_composition_cull.py` | T6 | T8 |
| T8 bars | `floor.py`, `anchor.py`, `tests/test_factory_floor.py`, `tests/test_factory_anchor.py`, `tests/test_factory_loop_scheduler.py` | T6 | T7 (note: T8 touches `anchor.py` after T2 did — sequential by phase anyway) |

### Verified landmark table (pre-lock grep, 2026-08-13 — drifts from the spec noted)

| Landmark | Verified location | Note |
|---|---|---|
| `_CANDIDATES_QUERY` | `census.py:78-87` | Spec said 74-86 — DRIFT, corrected here. Comment block :67-77. |
| `_CANONICAL_DECK_JOIN_SQL` | `census.py:60-65` | Already resolves each concept to ONE canonical deck (MIN `shell_variant`). **Aggregation pinned: none needed** — order on the canonical deck's columns directly. |
| `census.py:30-36` | imports `builder` (`BUILDER_VERSION, Concept, build_deck, concept_id, enumerate_concepts`) | ✓ |
| `SCREENING_FLOOR = 15` | `census.py:47` | ✓ |
| `promote_proven_singles` | `census.py:456` | ✓ untouched by this slice |
| Count classification | `builder.py:421-451` `brad_bounds_problems`; energy at :431-433, basics at :434-436 | ✓ energy = `BASIC_ENERGY`+`SPECIAL_ENERGY`; Pokémon = `CardType.POKEMON` |
| `FLOOR_GAMES=50` / `FLOOR_BAR=0.40` / `FLOOR_MAX_ATTEMPTS=3` | `floor.py:49` / `:50` / `:56`; sole verdict consumer `floor.py:291`; re-pick `:313-334` | ✓ |
| `ANCHOR_GAMES=200` / `ANCHOR_BAR=0.55` | `anchor.py:39` / `:40`; verdict `anchor.py:260`; display `subscheduler.py:529` (symbolic `anchor.ANCHOR_BAR` — no literal, NOT edited by T8) | ✓ |
| `decks` DDL | `deckdb.py:82-85`; `ix_decks_concept` at `:92`; `coverage` DDL `:144-148` | ✓ |
| Production deck-INSERT sites | `census.py:271` (`seed_census`), `census.py:428` (`activate_pair_concepts`), `anchor.py:115` (`ensure_anchor_deck`), `scripts/reseed_tournament_pool.py:129` (one-shot) | **DRIFT from dispatch's "builder/breeding/repair" guess**: `builder.py` returns lists and never inserts; `breeding`/`deck_repair` produce card lists consumed only by the frozen evolution pipeline and one-shot scripts; `scripts/migrate_unpayable_pool.py:229` is a historical cards-UPDATE, already executed 2026-08-13 (re-run is a no-op: violating=0 confirmed), deliberately not modified. |
| min-basics migration pattern | `scripts/migrate_min_basics_pool.py`: `--db required=True` (:204-207), `--dry-run` (:208), `BEGIN IMMEDIATE` (:145), pre-flight loud validation (:83-112); virgin test `tests/test_migrate_min_basics_pool.py:444-450` | **DRIFT**: min-basics did NOT protect champion/anchor — it deliberately culled both (its purpose). T7's protection is designed explicitly here instead (finalist status + `baselines`-lineage exclusion), matching spec §4's requirement. |
| EQP guard file | `tests/test_factory_lock_access_paths.py`; `_CANDIDATES_QUERY` guard ALREADY EXISTS at :395-421 with SQL embedded verbatim (accepted drift debt) | T4 MODIFIES it to import `census._CANDIDATES_QUERY`, closing the drift debt for this query. |
| `tests/fixtures/race.py` | exists | cited for any future concurrency receipt |
| anchor deck composition | `src/ptcg/decks/candidates/anchor-min8.csv` = 60 cards: **22 energy / 12 Pokémon / 26 trainers / 8 basics** | Executed against the engine card DB at plan time — matches the previously-claimed numbers. |
| In-process game path | `ptcg.arena.runner.play_match`; `ptcg.agents.heuristic.HeuristicAgent`; reference loop `scripts/measure_floor_distribution.py:123-172` (`play_series_vs_anchor`) | Do NOT import `measure_floor_distribution` (its module-level reseed TEMPLATES are <8 basics and fail loud) — copy the loop. |
| `loop_state.current_baseline(conn) -> sqlite3.Row \| None` | `loop_state.py:70` | row carries `deck_id`, `version` |
| `deckdb.connect` | `deckdb.py:27-44` — creates parent dirs (`:37`), Row factory, WAL, `busy_timeout=30000`, autocommit (`isolation_level=None`) | explicit `BEGIN IMMEDIATE` works |
| Bar-literal assertion sweep | see Task 8's exact file:line list | computed at plan time |

---

### Task 1: Composition-count helper (`composition_counts`)

**Files:**
- Modify: `src/ptcg/factory/builder.py` (append after `brad_bounds_problems`, i.e. after line 451)
- Test: `tests/test_factory_builder.py` (append)

**Interfaces:**
- Consumes: `cg.api.CardType`, `all_card_data` (already imported at `builder.py:42`), `functools.lru_cache` (already imported at `builder.py:40`).
- Produces: `composition_counts(deck: list[int]) -> tuple[int, int]` — `(energy_count, pokemon_count)`; raises `ValueError` on unknown card ids. `_card_by_id() -> dict[int, CardData]` (module-private, `lru_cache(maxsize=1)`). Tasks 2, 3, 5 import `composition_counts` from `ptcg.factory.builder`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_factory_builder.py` (reuse the file's existing imports where present; add the missing ones):

```python
# --- composition_counts (census/screening regime slice, spec §1) ---------
from pathlib import Path

import pytest

from ptcg.factory.builder import brad_bounds_problems, composition_counts

_ANCHOR_CSV = (
    Path(__file__).resolve().parents[1]
    / "src" / "ptcg" / "decks" / "candidates" / "anchor-min8.csv"
)


def _anchor_cards() -> list[int]:
    return [
        int(line)
        for line in _ANCHOR_CSV.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_composition_counts_anchor_min8_golden():
    """Golden pin, verified by executing the count against the engine card
    DB at plan time (2026-08-13): anchor-min8.csv is 22 energy / 12
    Pokémon / 26 trainers (60 - 22 - 12) / 8 basics."""
    cards = _anchor_cards()
    energy, pokemon = composition_counts(cards)
    assert (energy, pokemon) == (22, 12)
    assert len(cards) - energy - pokemon == 26  # trainers are derivable, not stored


def test_composition_counts_unknown_id_raises():
    with pytest.raises(ValueError, match="unknown card ids"):
        composition_counts([999_999])


def test_composition_counts_agrees_with_brad_bounds_classification():
    """Same classification as brad_bounds_problems (spec §1): the anchor
    deck's 22 energy exceeds the 20-energy construction bound, so the
    bound flags it exactly when composition_counts counts >20."""
    cards = _anchor_cards()
    energy, _ = composition_counts(cards)
    problems = brad_bounds_problems(cards)
    assert any("energy" in p for p in problems) == (energy > 20)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest "tests/test_factory_builder.py" -v -k composition_counts`
Expected: FAIL/ERROR with `ImportError: cannot import name 'composition_counts'`

- [ ] **Step 3: Implement**

Append to `src/ptcg/factory/builder.py` after `brad_bounds_problems` (after line 451):

```python
@lru_cache(maxsize=1)
def _card_by_id() -> dict[int, CardData]:
    """Every card in the pool keyed by cardId. Cached once per process —
    the composition backfill (scripts/migrate_composition_columns.py)
    visits ~95k deck rows and must not rebuild this dict per deck (the
    deck_repair._card_db lru_cache lesson, Pass 2 2026-08-13: 65.6x).
    Scheduler-side/single-threaded consumers only; the UI side keeps its
    own DLL-locked cache in deck_quality.py — do not import this from UI
    code (all_card_data is not thread-safe on a cold cache)."""
    return {c.cardId: c for c in all_card_data()}


def composition_counts(deck: list[int]) -> tuple[int, int]:
    """(energy_count, pokemon_count) for a deck card-id list (spec §1).

    Classification is EXACTLY brad_bounds_problems' (builder.py:431-436):
    energy = CardType.BASIC_ENERGY + CardType.SPECIAL_ENERGY; pokemon =
    CardType.POKEMON (all stages — `basic` is not consulted here). The
    trainer count is derivable (len(deck) - energy - pokemon) and is
    deliberately not returned/stored (spec §1).

    Raises ValueError on unknown card ids: every live insert site inserts
    validated decks, so an unknown id is corruption, not data. The T3
    backfill catches the ValueError and leaves that row's counts NULL —
    an unstamped deck sorts LAST and never jumps the queue (spec §2).
    """
    db = _card_by_id()
    unknown = sorted({cid for cid in deck if cid not in db})
    if unknown:
        raise ValueError(f"composition_counts: unknown card ids {unknown}")
    energy = sum(
        1
        for cid in deck
        if db[cid].cardType in (CardType.BASIC_ENERGY, CardType.SPECIAL_ENERGY)
    )
    pokemon = sum(1 for cid in deck if db[cid].cardType == CardType.POKEMON)
    return energy, pokemon
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest "tests/test_factory_builder.py" -v`
Expected: PASS (all, including the file's pre-existing tests)

- [ ] **Step 5: Commit**

```bash
git add "src/ptcg/factory/builder.py" "tests/test_factory_builder.py"
git commit "src/ptcg/factory/builder.py" "tests/test_factory_builder.py" -m "feat: composition_counts helper with cached card-by-id lookup (spec §1)"
```

---

### Task 2: `decks` schema columns + stamping at every live insert site

**Files:**
- Modify: `src/ptcg/factory/deckdb.py` (decks DDL at :82-85; new conditional index; `init_db`)
- Modify: `src/ptcg/factory/census.py` (import block :30-36; insert sites :270-273 and :426-430 → shared helper)
- Modify: `src/ptcg/factory/anchor.py` (imports ~:27-29; insert at :114-118)
- Modify: `scripts/reseed_tournament_pool.py` (imports ~:68-70; insert at :129)
- Create: `tests/test_factory_composition_stamping.py`

**Interfaces:**
- Consumes: `composition_counts` from Task 1 (`from ptcg.factory.builder import composition_counts` — safe: `builder.py` imports only `cg.api` / `ptcg.decks.*`, no `ptcg.factory` module, so no cycle from `anchor`/`census`).
- Produces: `decks.energy_count` / `decks.pokemon_count` (nullable INTEGER) on all VIRGIN DBs; index `ix_decks_concept_comp` on virgin DBs; `census._insert_deck_row(c: sqlite3.Connection, did: str, cid: str, cards: list[int]) -> None` (module-private choke point both census sites route through); `deckdb._IX_DECKS_COMP_SQL` constant + `deckdb._decks_has_composition_columns(conn) -> bool` (Task 3 reuses the same index SQL string semantics).

**Inertness analysis (mandatory reading before writing code):** the watch loop (`factory_watch_once.py`) calls `deckdb.init_db()` every ~15 minutes against production. `CREATE TABLE IF NOT EXISTS decks(...)` no-ops there (table exists) — inert. But an unconditional `CREATE INDEX IF NOT EXISTS ... ON decks(energy_count, ...)` in `_DDL_STATEMENTS` would EXECUTE against the pre-migration production table, hit "no such column: energy_count", and crash every firing. The index therefore goes into `init_db` behind a `PRAGMA table_info` guard, NOT into `_DDL_STATEMENTS`. The stamped INSERTs live only in scheduler-loaded modules (`census`, `anchor`) and one-shot scripts — restart-gated; the watch-loop import graph (`subscheduler`/`episodes`/`deckdb.init_db`) never executes them. Go-live runs the T3 migration BEFORE the scheduler restart, so the stamped INSERTs never run against a column-less DB.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_factory_composition_stamping.py`:

```python
"""Stamping receipts for the materialized composition columns (spec §1):
every live deck-insert site writes energy_count/pokemon_count at birth.

Live insert sites (verified 2026-08-13): census._insert_deck_row (both
census sites — seed_census census.py:271 and activate_pair_concepts
census.py:428 — route through it), anchor.ensure_anchor_deck
(anchor.py:115), and scripts/reseed_tournament_pool.py:129 (one-shot,
swept by tests/test_reseed_tournament_pool.py).
"""
from ptcg.factory import anchor, census, deckdb
from ptcg.factory.builder import build_deck, composition_counts, enumerate_concepts


def _conn(tmp_path):
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    return conn


def test_virgin_ddl_has_composition_columns_and_index(tmp_path):
    conn = _conn(tmp_path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(decks)")}
    assert {"energy_count", "pokemon_count"} <= cols
    idx = {r["name"] for r in conn.execute("PRAGMA index_list(decks)")}
    assert "ix_decks_concept_comp" in idx


def test_ensure_anchor_deck_stamps_counts(tmp_path):
    conn = _conn(tmp_path)
    anchor.ensure_anchor_deck(conn)
    row = conn.execute(
        "SELECT energy_count, pokemon_count FROM decks WHERE id=?",
        (anchor.ANCHOR_DECK_ID,),
    ).fetchone()
    # Golden, verified against the engine DB at plan time: anchor-min8.csv
    # = 22 energy / 12 Pokémon.
    assert (row["energy_count"], row["pokemon_count"]) == (22, 12)


def test_census_insert_deck_row_stamps_counts(tmp_path):
    conn = _conn(tmp_path)
    concept = enumerate_concepts()[0]
    result = build_deck(concept)
    assert result.cards is not None, "first enumerated concept must be buildable"
    conn.execute("INSERT INTO concepts(id, cores) VALUES('c-stamp', '[\"x\"]')")
    census._insert_deck_row(conn, "d-stamp", "c-stamp", result.cards)
    row = conn.execute(
        "SELECT energy_count, pokemon_count FROM decks WHERE id='d-stamp'"
    ).fetchone()
    assert row["energy_count"] is not None and row["pokemon_count"] is not None
    assert (row["energy_count"], row["pokemon_count"]) == composition_counts(result.cards)
```

(If the first enumerated concept turns out unbuildable when you run this, iterate `enumerate_concepts()` to the first concept whose `build_deck(...).cards is not None` and note the divergence in your report — do not weaken the assertions.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest "tests/test_factory_composition_stamping.py" -v`
Expected: FAIL — no `energy_count` column (DDL not yet changed), no `_insert_deck_row` attribute.

- [ ] **Step 3: Implement — deckdb.py**

Replace the decks DDL entry (`deckdb.py:82-85`, currently `id/concept_id/cards/shell_variant` only) with:

```python
    """
    CREATE TABLE IF NOT EXISTS decks(
      id TEXT PRIMARY KEY, concept_id TEXT NOT NULL REFERENCES concepts(id),
      cards TEXT NOT NULL, shell_variant INTEGER NOT NULL DEFAULT 0,
      energy_count INTEGER, pokemon_count INTEGER)
    """,
```

Add near `_IX_DECISIONS_CONCEPT_SQL` (deckdb.py:70):

```python
#: Composition covering index (spec §1 "supporting index"): serves the
#: canonical-deck lookup (concept_id, MIN shell_variant) AND the count
#: fetch for census ordering from the index alone. NOT in _DDL_STATEMENTS:
#: the pre-migration production `decks` table lacks these columns, and the
#: watch loop runs init_db every ~15 minutes — an unconditional CREATE
#: INDEX here would crash every firing ("no such column"). Created (a)
#: conditionally in init_db once the columns exist (virgin DBs: always),
#: (b) unconditionally by scripts/migrate_composition_columns.py at
#: go-live for the production DB.
_IX_DECKS_COMP_SQL = (
    "CREATE INDEX IF NOT EXISTS ix_decks_concept_comp "
    "ON decks(concept_id, shell_variant, energy_count, pokemon_count)"
)


def _decks_has_composition_columns(conn: sqlite3.Connection) -> bool:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(decks)")}
    return "energy_count" in cols and "pokemon_count" in cols
```

In `init_db` (grep `def init_db` for the exact spot), AFTER the `_DDL_STATEMENTS` execution loop (and after the existing `migrate_decisions` handling), add:

```python
    # Composition index: only once the columns exist (see _IX_DECKS_COMP_SQL
    # comment — production gets the columns from the one-shot go-live
    # migration, not from init_db).
    if _decks_has_composition_columns(conn):
        conn.execute(_IX_DECKS_COMP_SQL)
```

- [ ] **Step 4: Implement — census.py**

Extend the import block (`census.py:30-36`) with `composition_counts`:

```python
from ptcg.factory.builder import (
    BUILDER_VERSION,
    Concept,
    build_deck,
    composition_counts,
    concept_id,
    enumerate_concepts,
)
```

Add a module-level helper (place it directly above `seed_census`, census.py:237):

```python
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
```

Replace the raw INSERT at census.py:270-273 (inside `seed_census`'s `_apply`) with `_insert_deck_row(c, did, cid, result.cards)`, and the raw INSERT at census.py:426-430 (inside `activate_pair_concepts`) with `_insert_deck_row(c, did, pid, result.cards)`.

- [ ] **Step 5: Implement — anchor.py**

Add to anchor.py's imports (after `from ptcg.factory.candidates import bump_minor` at :29):

```python
from ptcg.factory.builder import composition_counts
```

Replace the INSERT at anchor.py:114-118 with:

```python
        en, pk = composition_counts(cards)
        c.execute(
            "INSERT OR IGNORE INTO decks(id, concept_id, cards, shell_variant, "
            "energy_count, pokemon_count) VALUES(?, ?, ?, 0, ?, ?)",
            (ANCHOR_DECK_ID, ANCHOR_CONCEPT_ID, json.dumps(cards), en, pk),
        )
```

- [ ] **Step 6: Implement — scripts/reseed_tournament_pool.py**

Add `from ptcg.factory.builder import composition_counts  # noqa: E402` to the import block (:68-70). At :129, extend the INSERT to the same 6-column shape, stamping from the same card list the site currently passes as the `cards` JSON parameter (match the surrounding local variable names — landmark-verify before editing):

```python
        en, pk = composition_counts(cards)
        conn.execute(
            "INSERT OR IGNORE INTO decks(id, concept_id, cards, shell_variant, "
            "energy_count, pokemon_count) VALUES(?, ?, ?, 0, ?, ?)",
            (deck_id_value, concept_id_value, json.dumps(cards), en, pk),
        )
```

(The three `?`-parameter names above describe roles; use the site's actual variables for id/concept-id/cards — the SQL shape and the two appended count parameters are the normative change.)

- [ ] **Step 7: Run targeted tests**

Run: `uv run pytest "tests/test_factory_composition_stamping.py" -v`
Expected: PASS

- [ ] **Step 8: Consumer sweep (`.claude/rules/test-coverage-sweep.md` — `build_deck` output shape is unchanged but the insert sites' observable output changed)**

Run each of these files; all must PASS (columns are nullable, so hand-seeded test INSERTs remain valid):

```
uv run pytest "tests/test_factory_census_schedule.py" "tests/test_factory_anchor.py" "tests/test_reseed_tournament_pool.py" "tests/test_factory_tournament_phase1.py" "tests/test_factory_deckdb.py" "tests/test_run_census_smoke.py" "tests/test_migrate_min_basics_pool.py" "tests/test_migrate_unpayable_pool.py" -q
```

Report pass/fail per file. Any failure is a finding, not something to patch silently.

- [ ] **Step 9: Commit**

```bash
git add "src/ptcg/factory/deckdb.py" "src/ptcg/factory/census.py" "src/ptcg/factory/anchor.py" "scripts/reseed_tournament_pool.py" "tests/test_factory_composition_stamping.py"
git commit "src/ptcg/factory/deckdb.py" "src/ptcg/factory/census.py" "src/ptcg/factory/anchor.py" "scripts/reseed_tournament_pool.py" "tests/test_factory_composition_stamping.py" -m "feat: decks composition columns + stamping at all live insert sites (spec §1)"
```

---

### Task 3: One-shot column migration + backfill for the production DB

**Files:**
- Create: `scripts/migrate_composition_columns.py`
- Test: `tests/test_migrate_composition_columns.py`

**Interfaces:**
- Consumes: `composition_counts` (Task 1); `deckdb.connect`/`deckdb.init_db`.
- Produces: `run_migration(conn: sqlite3.Connection, dry_run: bool = False) -> dict` and `main(argv: list[str] | None = None) -> dict` with keys `columns_added: list[str]`, `rows_needing_backfill: int`, `backfilled: int`, `skipped_unknown_ids: int`, `null_remaining: int`, `dry_run: bool`. Go-Live step 1 executes this against production.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_migrate_composition_columns.py`:

```python
"""One-shot composition-columns migration (spec §1): idempotent ALTER +
backfill + index, single BEGIN IMMEDIATE, --db required, virgin-path
covered (never-created parent), unknown-id rows stay NULL."""
import json
import time

import pytest

from ptcg.factory import deckdb
from scripts.migrate_composition_columns import main, run_migration

# Golden anchor deck (verified against the engine DB at plan time:
# 22 energy / 12 Pokémon). Loaded from the committed CSV, not hardcoded ids.
from ptcg.factory import anchor as _anchor


def _anchor_cards() -> list[int]:
    return [
        int(line)
        for line in _anchor.ANCHOR_DECK_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _legacy_db(path):
    """Reproduce the PRE-migration production shape: decks WITHOUT the
    composition columns. Built by hand because deckdb's current DDL now
    includes them (virgin DBs are already migrated by construction)."""
    conn = deckdb.connect(path)
    conn.execute(
        "CREATE TABLE concepts(id TEXT PRIMARY KEY, cores TEXT NOT NULL, "
        "builder_version INTEGER NOT NULL DEFAULT 1, "
        "status TEXT NOT NULL DEFAULT 'untested', reason TEXT)"
    )
    conn.execute(
        "CREATE TABLE decks(id TEXT PRIMARY KEY, "
        "concept_id TEXT NOT NULL REFERENCES concepts(id), "
        "cards TEXT NOT NULL, shell_variant INTEGER NOT NULL DEFAULT 0)"
    )
    return conn


def _seed(conn, deck_id, cards):
    cid = "c-" + deck_id
    conn.execute("INSERT INTO concepts(id, cores) VALUES(?, '[\"x\"]')", (cid,))
    conn.execute(
        "INSERT INTO decks(id, concept_id, cards) VALUES(?, ?, ?)",
        (deck_id, cid, json.dumps(cards)),
    )


def test_adds_columns_backfills_and_indexes(tmp_path):
    conn = _legacy_db(tmp_path / "t.db")
    _seed(conn, "dA", _anchor_cards())
    result = run_migration(conn)
    assert sorted(result["columns_added"]) == ["energy_count", "pokemon_count"]
    assert result["backfilled"] == 1 and result["null_remaining"] == 0
    row = conn.execute(
        "SELECT energy_count, pokemon_count FROM decks WHERE id='dA'"
    ).fetchone()
    assert (row["energy_count"], row["pokemon_count"]) == (22, 12)
    idx = {r["name"] for r in conn.execute("PRAGMA index_list(decks)")}
    assert "ix_decks_concept_comp" in idx


def test_idempotent_rerun_receipt(tmp_path):
    conn = _legacy_db(tmp_path / "t.db")
    _seed(conn, "dA", _anchor_cards())
    run_migration(conn)
    second = run_migration(conn)
    assert second["columns_added"] == []
    assert second["backfilled"] == 0
    assert second["null_remaining"] == 0  # the violating/NULL-remaining=0 receipt


def test_unknown_id_rows_stay_null_and_are_counted(tmp_path):
    conn = _legacy_db(tmp_path / "t.db")
    _seed(conn, "dBad", [999_999] * 60)
    result = run_migration(conn)
    assert result["skipped_unknown_ids"] == 1
    assert result["null_remaining"] == 1  # NULL = sorts last, never jumps queue
    row = conn.execute("SELECT energy_count FROM decks WHERE id='dBad'").fetchone()
    assert row["energy_count"] is None


def test_dry_run_persists_nothing(tmp_path):
    conn = _legacy_db(tmp_path / "t.db")
    _seed(conn, "dA", _anchor_cards())
    result = run_migration(conn, dry_run=True)
    assert result["dry_run"] is True and result["backfilled"] == 1
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(decks)")}
    assert "energy_count" not in cols  # ALTER rolled back


def test_main_virgin_db_path(tmp_path):
    """Never-created nested parent (the virgin-directory class): do NOT
    rely on tmp_path pre-creation."""
    db_path = tmp_path / "deep" / "never" / "t.db"
    assert not db_path.parent.exists()
    result = main(["--db", str(db_path)])
    assert db_path.exists()
    # Virgin DBs already carry the columns from deckdb's DDL:
    assert result["columns_added"] == []
    assert result["backfilled"] == 0 and result["null_remaining"] == 0


def test_backfill_scale_receipt(tmp_path):
    """Production-scale-N timing receipt (~95k decks live; 20k here). Soft
    bound only — this host runs loaded 24/7. Prints the measured wall time
    for the go-live sizing note."""
    conn = _legacy_db(tmp_path / "t.db")
    cards_json = json.dumps(_anchor_cards())
    conn.execute("INSERT INTO concepts(id, cores) VALUES('cS', '[\"x\"]')")
    conn.executemany(
        "INSERT INTO decks(id, concept_id, cards) VALUES(?, 'cS', ?)",
        ((f"d{i}", cards_json) for i in range(20_000)),
    )
    t0 = time.perf_counter()
    result = run_migration(conn)
    elapsed = time.perf_counter() - t0
    print(f"backfill 20k rows: {elapsed:.2f}s")
    assert result["backfilled"] == 20_000 and result["null_remaining"] == 0
    assert elapsed < 60.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest "tests/test_migrate_composition_columns.py" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.migrate_composition_columns'`

- [ ] **Step 3: Implement the script**

Create `scripts/migrate_composition_columns.py`:

```python
"""One-shot composition-columns migration for the EXISTING production
tournament DB (spec §1, go-live step 1). Adds nullable INTEGER columns
`energy_count`/`pokemon_count` to `decks` (idempotent — PRAGMA
table_info checked first), backfills every NULL row from the deck's own
cards via builder.composition_counts, and creates the supporting index
`ix_decks_concept_comp` — all inside ONE `BEGIN IMMEDIATE` transaction
(single read-decide-act, .claude/rules/single-actor-worker-tests.md).

Deliberately NOT wired into deckdb.init_db(): the watch loop calls
init_db every ~15 minutes; the backfill runs exactly once, at go-live,
with the runner/scheduler tasks held (see the plan's Go-Live section).

Unknown-card-id decks are left NULL (counted in the receipt): NULL sorts
LAST in the census ORDER BY, so an unstampable deck never jumps the
queue. Exit is nonzero if null_remaining != skipped_unknown_ids (an
unexplained NULL means the backfill did not do its job — fail loud).

Usage:
    uv run python scripts/migrate_composition_columns.py --db <path> [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ptcg.factory import deckdb  # noqa: E402
from ptcg.factory.builder import composition_counts  # noqa: E402

_IX_SQL = (
    "CREATE INDEX IF NOT EXISTS ix_decks_concept_comp "
    "ON decks(concept_id, shell_variant, energy_count, pokemon_count)"
)


def run_migration(conn, dry_run: bool = False) -> dict:
    conn.execute("BEGIN IMMEDIATE")
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(decks)")}
        added: list[str] = []
        for col in ("energy_count", "pokemon_count"):
            if col not in cols:
                conn.execute(f"ALTER TABLE decks ADD COLUMN {col} INTEGER")
                added.append(col)
        rows = conn.execute(
            "SELECT id, cards FROM decks "
            "WHERE energy_count IS NULL OR pokemon_count IS NULL"
        ).fetchall()
        backfilled = 0
        skipped_unknown = 0
        for row in rows:
            try:
                en, pk = composition_counts(json.loads(row["cards"]))
            except ValueError:
                skipped_unknown += 1  # stays NULL: sorts last, never jumps queue
                continue
            conn.execute(
                "UPDATE decks SET energy_count=?, pokemon_count=? WHERE id=?",
                (en, pk, row["id"]),
            )
            backfilled += 1
        conn.execute(_IX_SQL)
        null_remaining = conn.execute(
            "SELECT COUNT(*) FROM decks "
            "WHERE energy_count IS NULL OR pokemon_count IS NULL"
        ).fetchone()[0]
        result = {
            "columns_added": added,
            "rows_needing_backfill": len(rows),
            "backfilled": backfilled,
            "skipped_unknown_ids": skipped_unknown,
            "null_remaining": null_remaining,
            "dry_run": dry_run,
        }
        conn.execute("ROLLBACK" if dry_run else "COMMIT")
        return result
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db", required=True,
        help="Path to the tournament DB. Never the production tournament.db in tests.",
    )
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> dict:
    args = _parse_args(argv)
    conn = deckdb.connect(Path(args.db))
    deckdb.init_db(conn)
    result = run_migration(conn, args.dry_run)
    print(json.dumps(result, indent=2))
    if result["null_remaining"] != result["skipped_unknown_ids"]:
        raise SystemExit(1)  # unexplained NULLs — fail loud, never exit 0
    return result


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest "tests/test_migrate_composition_columns.py" -v`
Expected: PASS (6 tests); note the printed 20k-row timing in your report.

- [ ] **Step 5: Commit**

```bash
git add "scripts/migrate_composition_columns.py" "tests/test_migrate_composition_columns.py"
git commit "scripts/migrate_composition_columns.py" "tests/test_migrate_composition_columns.py" -m "feat: one-shot composition-columns migration + backfill script (spec §1)"
```

---

### Task 4: Screening-order change in `_CANDIDATES_QUERY` + EQP guard

**Files:**
- Modify: `src/ptcg/factory/census.py:67-87` (comment block + `_CANDIDATES_QUERY`)
- Modify: `tests/test_factory_census_schedule.py` (new ordering test)
- Modify: `tests/test_factory_lock_access_paths.py:395-421` (`test_census_candidates_query_uses_indexes` — switch to importing the real SQL)

**Interfaces:**
- Consumes: `decks.energy_count`/`pokemon_count` (Task 2), the existing `_CANONICAL_DECK_JOIN_SQL` (census.py:60-65 — already resolves each concept to exactly ONE canonical deck, MIN `shell_variant`; **aggregation decision pinned: no MIN()-aggregate needed, order on the canonical deck's columns directly**).
- Produces: the new ORDER BY, consumed unchanged by `schedule_screening_games` (census.py:109+, called from `loop_scheduler.py:461`). SELECT/params/LIMIT shape unchanged — no caller changes.

**Serialization note:** shares `census.py` with Task 2 — dispatch only after Task 2 lands.

- [ ] **Step 1: Write the failing ordering test**

Append to `tests/test_factory_census_schedule.py` (reuse its existing imports of `census`/`deckdb`; the fixture below is self-contained on purpose):

```python
def test_candidates_order_composition_tiebreak_within_coverage_order(tmp_path):
    """Spec §2 ORDER BY: rating ASC NULLS FIRST, games_played ASC,
    energy_count ASC NULLS LAST, pokemon_count ASC NULLS LAST,
    concept_id ASC. Hand-verified expected sequence (all games_played=0):
      cB (e20,p8, unrated)   lowest energy, lowest pokemon, id before cE
      cE (e20,p8, unrated)   full-count tie with cB -> concept_id tiebreak
      cA (e20,p12, unrated)  energy tie, higher pokemon
      cC (e30,p8, unrated)   higher energy
      cD (NULL,NULL, unrated) unstamped sorts LAST among the unrated
      cF (e1,p1, rating 0.2) RATED sorts after ALL unrated (NULLS FIRST):
                             composition is a tie-break WITHIN coverage
                             order, never a jump over it (Locked Decision 1)
    """
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    for cid, e, p, rating in [
        ("cA", 20, 12, None),
        ("cB", 20, 8, None),
        ("cC", 30, 8, None),
        ("cD", None, None, None),
        ("cE", 20, 8, None),
        ("cF", 1, 1, 0.2),
    ]:
        conn.execute("INSERT INTO concepts(id, cores) VALUES(?, '[\"x\"]')", (cid,))
        conn.execute(
            "INSERT INTO decks(id, concept_id, cards, shell_variant, "
            "energy_count, pokemon_count) VALUES(?, ?, '[]', 0, ?, ?)",
            ("d" + cid, cid, e, p),
        )
        conn.execute(
            "INSERT INTO coverage(concept_id, games_played, distinct_opponents, "
            "rating) VALUES(?, 0, 0, ?)",
            (cid, rating),
        )
    rows = conn.execute(census._CANDIDATES_QUERY, (15, 200)).fetchall()
    assert [r["concept_id"] for r in rows] == ["cB", "cE", "cA", "cC", "cD", "cF"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest "tests/test_factory_census_schedule.py" -v -k composition_tiebreak`
Expected: FAIL — current query orders `["cB", "cE", "cA", "cC", "cD", "cF"]` only by rating/games/concept_id, so the actual sequence is plain `concept_id` order among the unrated: `["cA", "cB", "cC", "cD", "cE", "cF"]`-with-cF-last → assertion mismatch.

- [ ] **Step 3: Implement the query change**

Replace `_CANDIDATES_QUERY` (census.py:78-87) with:

```python
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
```

Extend the comment block above it (census.py:67-77): keep the existing NULLS-FIRST rationale and append —

```python
#: Composition tie-break (spec 2026-08-13 census/screening regime, Locked
#: Decision 1): within identical (rating, games_played), leaner decks
#: screen first — energy_count then pokemon_count from the canonical
#: deck's materialized columns, NULLS LAST so an unstamped deck never
#: jumps the queue. `concept_id ASC` stays the deterministic final
#: tiebreak. With ~41.7k of 42k active concepts unrated, this makes the
#: current re-screen effectively composition-first while preserving
#: worst-first re-rating semantics once coverage builds.
```

- [ ] **Step 4: Update the EQP guard to import the real SQL**

Replace the body of `test_census_candidates_query_uses_indexes` (tests/test_factory_lock_access_paths.py:395-421) — the embedded SQL copy is deleted; the guard now imports the constant (closing this file's accepted-debt drift caveat for this one query):

```python
def test_census_candidates_query_uses_indexes(tmp_path):
    """census._CANDIDATES_QUERY (schedule_screening_games' lock-held field
    selection) — imported directly rather than embedded, so this guard
    cannot drift from the source. Spec §2: the composition tie-break must
    stay index-backed — no SCAN of concepts/coverage/decks."""
    from ptcg.factory import census

    conn = _db(tmp_path)
    plan = _eqp(conn, census._CANDIDATES_QUERY, (15, 200))
    assert "ix_concepts_status" in plan, plan
    assert ("ix_decks_concept" in plan) or ("ix_decks_concept_comp" in plan), plan
    assert "SCAN concepts" not in plan, plan
    assert "SCAN coverage" not in plan, plan
    assert "SCAN decks" not in plan, plan
```

- [ ] **Step 5: Run both test files**

Run: `uv run pytest "tests/test_factory_census_schedule.py" "tests/test_factory_lock_access_paths.py" -v`
Expected: PASS (all — the pre-existing schedule tests exercise `schedule_screening_games` end-to-end over the new ORDER BY; the EQP guard proves index backing at the virgin schema, which answers "CAN this query use an index" per that file's methodology note).

- [ ] **Step 6: Commit**

```bash
git add "src/ptcg/factory/census.py" "tests/test_factory_census_schedule.py" "tests/test_factory_lock_access_paths.py"
git commit "src/ptcg/factory/census.py" "tests/test_factory_census_schedule.py" "tests/test_factory_lock_access_paths.py" -m "feat: composition tie-break in census screening order + drift-proof EQP guard (spec §2)"
```

---

### Task 5: Measurement script (`measure_screening_regime.py`)

**Files:**
- Create: `scripts/measure_screening_regime.py`
- Test: `tests/test_measure_screening_regime.py`

**Interfaces:**
- Consumes: `composition_counts` (Task 1); `ptcg.arena.runner.play_match`; `ptcg.agents.heuristic.HeuristicAgent`; `anchor.ANCHOR_DECK_PATH` (cards read from the committed CSV, independent of the DB); `loop_state.current_baseline(conn) -> sqlite3.Row | None` (`loop_state.py:70`; row has `deck_id`, `version`).
- Produces: `experiments/screening-regime-measurement-<YYYY-MM-DD>.json` + `.md` (small receipts artifact); pure helpers `energy_quartile_bounds(energies: list[int]) -> list[int]` and `stratum_of(e: int, bounds: list[int]) -> int` (unit-tested); `main(argv) -> dict`.
- **Do NOT import `scripts.measure_floor_distribution`** — its module-level reseed TEMPLATES are `<8`-basic decks that now fail loud. Copy its game-loop shape instead (`play_series_vs_anchor`, measure_floor_distribution.py:123-172) as shown below.

**Read-only + disk discipline:** connects with `sqlite3.connect(f"file:...?mode=ro", uri=True)` — no DB copy is made (thin disk), no write lock is ever taken, and concurrent workers are unaffected. Composition is computed DIRECTLY from each deck's cards (works pre-migration, per spec §3's "direct computation" allowance) — the script does not depend on Tasks 2/3 having run against the target DB. Measurement runs against the CURRENT post-swap, post-repair pool only — never historical data (`empirical-check-against-wrong-regime`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_measure_screening_regime.py`:

```python
"""Unit tests for the screening-regime measurement helpers plus a small
end-to-end smoke on a fixture DB (real engine games, tiny counts)."""
import json

from ptcg.factory import anchor, deckdb
from scripts.measure_screening_regime import (
    energy_quartile_bounds,
    main,
    stratum_of,
)


def test_energy_quartile_bounds_hand_verified():
    """s = sorted 8 values, n=8 -> bounds [s[2], s[4], s[6]] = [14, 18, 22].
    Hand-verified: 10,12,14 -> q0; 16,18 -> q1; 20,22 -> q2; 24 -> q3."""
    energies = [24, 10, 18, 14, 22, 12, 20, 16]
    bounds = energy_quartile_bounds(energies)
    assert bounds == [14, 18, 22]
    assert [stratum_of(e, bounds) for e in [10, 12, 14, 16, 18, 20, 22, 24]] == [
        0, 0, 0, 1, 1, 2, 2, 3,
    ]


def test_energy_quartile_bounds_degenerate_all_equal():
    """Degenerate input (plan-authored-code degenerate probe): all-equal
    energies collapse every bound to that value -> everything lands in
    stratum 0, strata 1-3 are empty and must be reported as skipped, not
    crash."""
    bounds = energy_quartile_bounds([22, 22, 22, 22])
    assert bounds == [22, 22, 22]
    assert stratum_of(22, bounds) == 0


def test_end_to_end_smoke_fixture_db(tmp_path):
    """Tiny real run: 4 active concepts (anchor-composition decks), 1 deck
    per stratum, 1 game, 2 runs. Asserts both receipt files exist and the
    JSON carries BOTH runs (stochastic-gate-replication: report all runs)."""
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    cards = [
        int(line)
        for line in anchor.ANCHOR_DECK_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for i in range(4):
        cid = f"c{i}"
        conn.execute(
            "INSERT INTO concepts(id, cores, status) VALUES(?, '[\"x\"]', 'active')",
            (cid,),
        )
        conn.execute(
            "INSERT INTO decks(id, concept_id, cards, shell_variant) "
            "VALUES(?, ?, ?, 0)",
            (f"d{i}", cid, json.dumps(cards)),
        )
    out_dir = tmp_path / "out"
    result = main([
        "--db", str(tmp_path / "t.db"),
        "--games", "1",
        "--decks-per-stratum", "1",
        "--runs", "2",
        "--seed", "20260813",
        "--out-dir", str(out_dir),
    ])
    assert len(result["runs"]) == 2
    json_files = list(out_dir.glob("screening-regime-measurement-*.json"))
    md_files = list(out_dir.glob("screening-regime-measurement-*.md"))
    assert len(json_files) == 1 and len(md_files) == 1
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert len(payload["runs"]) == 2
    assert "histograms" in payload and "baseline" in payload
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest "tests/test_measure_screening_regime.py" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.measure_screening_regime'`

- [ ] **Step 3: Implement the script**

Create `scripts/measure_screening_regime.py`:

```python
"""Read-only measurement of the current pool's composition/WR landscape
(spec §3) — the input receipt for the T6 Brad gate (cull thresholds +
FLOOR_BAR/ANCHOR_BAR proposals).

Produces, into --out-dir (default experiments/):
  (a) composition histograms (energy/pokemon/trainer) of the ACTIVE pool,
      computed DIRECTLY from each canonical deck's cards (works pre- and
      post-column-migration; no dependency on the backfill);
  (b) stratified WR-vs-anchor: energy-count quartile strata, N decks per
      stratum x --games in-process games each, run --runs times
      (default 2, .claude/rules/stochastic-gate-replication.md — ALL runs
      reported, never just the friendlier one);
  (c) the current baseline lineage's WR-vs-anchor (--baseline-games per
      run) — the ANCHOR_BAR input (spec §5: baseline WR plus a margin).

Disk/lock discipline: the DB is opened READ-ONLY via a mode=ro URI — no
copy is made (thin disk margin) and no write lock is ever taken, so the
24/7 workers are unaffected. Games are played in-process
(HeuristicAgent both sides, ~0.02s/game measured 2026-08-04).

Deliberately does NOT import scripts.measure_floor_distribution (its
module-level reseed TEMPLATES are <8-basic decks that now fail loud);
the per-game loop below mirrors its play_series_vs_anchor
(measure_floor_distribution.py:123-172) instead.

Usage:
    uv run python scripts/measure_screening_regime.py --db "experiments/factory/tournament.db"
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ptcg.agents.heuristic import HeuristicAgent  # noqa: E402
from ptcg.arena.runner import play_match  # noqa: E402
from ptcg.factory import anchor, loop_state  # noqa: E402
from ptcg.factory.builder import composition_counts  # noqa: E402

_ACTIVE_DECKS_QUERY = (
    "SELECT c.id AS concept_id, d.id AS deck_id, d.cards AS cards "
    "FROM concepts c JOIN decks d ON d.concept_id = c.id "
    "AND d.shell_variant = (SELECT MIN(d2.shell_variant) FROM decks d2 "
    "WHERE d2.concept_id = c.id) "
    "WHERE c.status = 'active'"
)


def _connect_ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def energy_quartile_bounds(energies: list[int]) -> list[int]:
    """[q1, q2, q3] as sorted-list index cuts (n//4, n//2, 3n//4)."""
    s = sorted(energies)
    n = len(s)
    return [s[n // 4], s[n // 2], s[(3 * n) // 4]]


def stratum_of(e: int, bounds: list[int]) -> int:
    q1, q2, q3 = bounds
    if e <= q1:
        return 0
    if e <= q2:
        return 1
    if e <= q3:
        return 2
    return 3


def _anchor_cards() -> list[int]:
    return [
        int(line)
        for line in anchor.ANCHOR_DECK_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def play_series_vs_anchor(
    deck_cards: list[int], anchor_cards: list[int], n_games: int, label: str
) -> dict:
    """Mirrors measure_floor_distribution.play_series_vs_anchor
    (measure_floor_distribution.py:123-172): HeuristicAgent both sides,
    alternating who is player 0 each game. Returns win/draw/loss from
    deck_cards' perspective."""
    deck_agent = HeuristicAgent()
    anchor_agent = HeuristicAgent()
    wins = draws = losses = 0
    for g in range(n_games):
        if g % 2 == 0:
            r = play_match(deck_agent, anchor_agent, deck_cards, anchor_cards)
            deck_side = 0
        else:
            r = play_match(anchor_agent, deck_agent, anchor_cards, deck_cards)
            deck_side = 1
        if r.error:
            raise RuntimeError(f"{label} game {g}: {r.error}")
        if r.winner == 2:
            draws += 1
        elif r.winner == deck_side:
            wins += 1
        else:
            losses += 1
    return {"wins": wins, "draws": draws, "losses": losses, "games": n_games,
            "wr": wins / n_games if n_games else None}


def main(argv: list[str] | None = None) -> dict:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True,
                   help="Tournament DB path (opened READ-ONLY, mode=ro URI).")
    p.add_argument("--games", type=int, default=50,
                   help="games per sampled deck per run (default 50)")
    p.add_argument("--decks-per-stratum", type=int, default=10,
                   help="decks sampled per energy quartile (default 10; ~40 total)")
    p.add_argument("--runs", type=int, default=2,
                   help="independent replications (default 2; report BOTH)")
    p.add_argument("--baseline-games", type=int, default=200,
                   help="baseline-lineage games vs anchor per run (default 200)")
    p.add_argument("--seed", type=int, default=20260813)
    p.add_argument("--out-dir", default="experiments")
    args = p.parse_args(argv)

    conn = _connect_ro(Path(args.db))
    rows = conn.execute(_ACTIVE_DECKS_QUERY).fetchall()
    if not rows:
        raise SystemExit("no active concepts found — wrong DB?")

    comps = []          # (concept_id, deck_id, cards, energy, pokemon)
    unknown_skipped = 0
    for r in rows:
        cards = json.loads(r["cards"])
        try:
            en, pk = composition_counts(cards)
        except ValueError:
            unknown_skipped += 1
            continue
        comps.append((r["concept_id"], r["deck_id"], cards, en, pk))

    histograms = {
        "energy": dict(Counter(str(c[3]) for c in comps)),
        "pokemon": dict(Counter(str(c[4]) for c in comps)),
        "trainer": dict(Counter(str(60 - c[3] - c[4]) for c in comps)),
    }
    bounds = energy_quartile_bounds([c[3] for c in comps])

    strata: dict[int, list] = {0: [], 1: [], 2: [], 3: []}
    for c in comps:
        strata[stratum_of(c[3], bounds)].append(c)

    rng = random.Random(args.seed)
    sample = {
        q: rng.sample(members, min(args.decks_per_stratum, len(members)))
        for q, members in strata.items()
    }
    anchor_cards = _anchor_cards()

    runs = []
    for run_idx in range(args.runs):
        per_stratum = {}
        for q, members in sample.items():
            if not members:
                per_stratum[str(q)] = {"skipped": "empty stratum"}
                continue
            decks = []
            for concept_id_, deck_id_, cards, en, pk in members:
                series = play_series_vs_anchor(
                    cards, anchor_cards, args.games,
                    label=f"run{run_idx} q{q} {deck_id_}",
                )
                decks.append({"concept_id": concept_id_, "deck_id": deck_id_,
                              "energy": en, "pokemon": pk, **series})
            games = sum(d["games"] for d in decks)
            wins = sum(d["wins"] for d in decks)
            per_stratum[str(q)] = {
                "decks": decks,
                "pooled_wr": wins / games if games else None,
                "pooled_games": games,
            }
        runs.append({"run": run_idx, "strata": per_stratum})

    baseline_row = loop_state.current_baseline(conn)
    baseline: dict | None = None
    if baseline_row is not None:
        b_cards = json.loads(conn.execute(
            "SELECT cards FROM decks WHERE id=?", (baseline_row["deck_id"],)
        ).fetchone()["cards"])
        baseline = {
            "version": baseline_row["version"],
            "deck_id": baseline_row["deck_id"],
            "runs": [
                play_series_vs_anchor(
                    b_cards, anchor_cards, args.baseline_games,
                    label=f"baseline run{i}",
                )
                for i in range(args.runs)
            ],
        }
    else:
        print("WARNING: no current baseline found — baseline section omitted",
              file=sys.stderr)

    payload = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "db": str(args.db),
        "active_concepts": len(rows),
        "unknown_id_skipped": unknown_skipped,
        "energy_quartile_bounds": bounds,
        "histograms": histograms,
        "sample_seed": args.seed,
        "games_per_deck": args.games,
        "runs": runs,
        "baseline": baseline,
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    json_path = out_dir / f"screening-regime-measurement-{stamp}.json"
    md_path = out_dir / f"screening-regime-measurement-{stamp}.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    md_lines = [
        f"# Screening-regime measurement — {stamp}",
        "",
        f"Active concepts: {len(rows)} (unknown-id skipped: {unknown_skipped})",
        f"Energy quartile bounds: {bounds}",
        "",
        "## Energy histogram",
        "",
        "| energy | decks |", "|---|---|",
        *[f"| {k} | {v} |" for k, v in sorted(histograms["energy"].items(),
                                              key=lambda kv: int(kv[0]))],
        "",
        "## Stratified WR vs anchor (all runs reported)",
        "",
        "| run | stratum | pooled WR | games |", "|---|---|---|---|",
    ]
    for run in runs:
        for q, s in run["strata"].items():
            if "skipped" in s:
                md_lines.append(f"| {run['run']} | q{q} | skipped (empty) | 0 |")
            else:
                md_lines.append(
                    f"| {run['run']} | q{q} | {s['pooled_wr']:.3f} | {s['pooled_games']} |"
                )
    if baseline is not None:
        md_lines += ["", "## Baseline lineage vs anchor", ""]
        for i, b in enumerate(baseline["runs"]):
            md_lines.append(
                f"- run {i}: {b['wins']}/{b['games']} = {b['wr']:.3f} "
                f"({baseline['version']})"
            )
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"wrote {json_path} and {md_path}")
    return payload


if __name__ == "__main__":
    main()
```

**Landmark check before running:** confirm `play_match`'s result attribute names (`winner`, `error`, `seconds`) against `ptcg/arena/runner.py` (grep `def play_match` and its return dataclass) and against `measure_floor_distribution.py:142-151`'s usage. If the winner encoding differs from `{0, 1, 2=draw}`, mirror the real one and report the divergence — do not guess.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest "tests/test_measure_screening_regime.py" -v`
Expected: PASS (the smoke test plays ~10 real engine games; a few seconds).

- [ ] **Step 5: Commit**

```bash
git add "scripts/measure_screening_regime.py" "tests/test_measure_screening_regime.py"
git commit "scripts/measure_screening_regime.py" "tests/test_measure_screening_regime.py" -m "feat: read-only screening-regime measurement script (spec §3)"
```

- [ ] **Step 6 (ORCHESTRATOR-OWNED, after this task's review passes): real measurement run**

The orchestrator — not a subagent — runs, in a background shell:

```bash
uv run python scripts/measure_screening_regime.py --db "experiments/factory/tournament.db"
```

Expected wall time: ~4,400 games (4 strata × 10 decks × 50 games × 2 runs + 200 × 2 baseline) at ~0.02s/game ≈ 2-4 minutes plus startup. Verify BOTH receipt files exist and the `baseline` section is non-null (production has a baseline). The measurement is against the live post-swap, post-repair pool — the receipt is the sole input to Task 6. Commit the two receipt files:

```bash
git add "experiments/screening-regime-measurement-2026-08-13.json" "experiments/screening-regime-measurement-2026-08-13.md"
git commit "experiments/screening-regime-measurement-2026-08-13.json" "experiments/screening-regime-measurement-2026-08-13.md" -m "docs: screening-regime measurement receipt (spec §3)"
```

(Adjust the date in the filenames to the actual UTC run date.)

---

### Task 6: EXPLICIT BRAD GATE — thresholds and bar values (plan step, not code)

**Files:** none.

**Interfaces:**
- Consumes: the Task 5 receipt (`experiments/screening-regime-measurement-<date>.md`/`.json`).
- Produces: Brad-approved values recorded in `.claude/plan.md` via plan-manager: `CULL_MAX_ENERGY` and/or other cull thresholds; `FLOOR_BAR_NEW`; `ANCHOR_BAR_NEW`. **Tasks 7's execution (not its authoring) and Task 8's constant values are BLOCKED until this gate passes.**

- [ ] **Step 1:** The ORCHESTRATOR presents one AskUserQuestion containing, verbatim from the receipt: (a) the energy/pokemon/trainer histogram summary; (b) the stratified WR table for BOTH runs (never a pooled-only view — the variance must be visible); (c) the baseline lineage's WR for both runs.
- [ ] **Step 2:** The same AskUserQuestion proposes, as its recommended-default first option: (a) concrete cull thresholds (composition values outside which concepts are culled — derived from the histogram tails and any stratum whose WR collapses in both runs); (b) `FLOOR_BAR_NEW` = the percentile-anchored value such that the Brad-chosen fraction of the current healthy pool fails at birth (Locked Decision 3) — **must be an exact multiple of 1/FLOOR_GAMES = 0.02** so the boundary tests in Task 8 stay integer-exact; (c) `ANCHOR_BAR_NEW` = baseline lineage's measured WR (pooled across both runs) plus a margin — **must be an exact multiple of 1/ANCHOR_GAMES = 0.005**.
- [ ] **Step 3:** Record the approved values + the receipt citation in `.claude/plan.md` (plan-manager). No cull migration executes and no bar constant changes until this record exists.

---

### Task 7: Composition cull migration (threshold-parameterized; execution deferred to go-live)

**Files:**
- Create: `scripts/migrate_composition_cull.py`
- Test: `tests/test_migrate_composition_cull.py`

**Interfaces:**
- Consumes: `decks.energy_count`/`pokemon_count` (backfilled — the script judges from the COLUMNS, not by recomputing); `anchor.ANCHOR_CONCEPT_ID`; the `baselines` table (champion lineage).
- Produces: `run_migration(conn, thresholds: Thresholds, dry_run: bool = False) -> dict` with keys `examined`, `culled`, `skipped_null_counts`, `protected_skipped`, `violating_remaining`, `dry_run`; `Thresholds` dataclass (`max_energy: int | None`, `min_energy: int | None`, `min_pokemon: int | None`, `max_pokemon: int | None`); `main(argv) -> dict`. **No baked-in numbers anywhere — thresholds are CLI args supplied at go-live from the Task 6 record.**

**Protection design (explicit — the min-basics script is NOT the precedent here: it deliberately culled the champion and old anchor as its whole point; spec §4 requires the opposite):** protected = (a) every concept with `status='finalist'` (the anchor's concept — `anchor.ensure_anchor_deck` registers it as finalist precisely so "census culling … can never" touch it, anchor.py:70-72), (b) `anchor.ANCHOR_CONCEPT_ID` explicitly (belt and suspenders), (c) every concept of every deck referenced by `baselines.deck_id` (champion lineage, incl. the current baseline — `meta['baseline_version']` always names a `baselines` row, loop_state.py:70-78). Cull is a STATUS flip only: `status='culled'`, `reason='composition-rule'`, deck rows retained (pair-gate evictee reconstruction), coverage rows untouched. NULL-count decks are SKIPPED (unjudgeable — reported, never culled). No `decisions` audit rows are written (matching the min-basics/unpayable migrations); the UI restore path's no-audit-row tier already restores such concepts to `untested` with coverage reset.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_migrate_composition_cull.py`:

```python
"""Brad-gated composition cull (spec §4): threshold-parameterized, single
BEGIN IMMEDIATE, reversible status flip, champion/anchor/finalist
protected, idempotent with a violating_remaining=0 receipt."""
import json

import pytest

from ptcg.factory import deckdb
from scripts.migrate_composition_cull import Thresholds, main, run_migration


def _db(tmp_path):
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    return conn


def _concept(conn, cid, status="active", energy=20, pokemon=10):
    conn.execute(
        "INSERT INTO concepts(id, cores, status) VALUES(?, '[\"x\"]', ?)",
        (cid, status),
    )
    conn.execute(
        "INSERT INTO decks(id, concept_id, cards, shell_variant, "
        "energy_count, pokemon_count) VALUES(?, ?, '[]', 0, ?, ?)",
        ("d-" + cid, cid, energy, pokemon),
    )


def test_culls_only_violating_and_is_reversible_status_flip(tmp_path):
    conn = _db(tmp_path)
    _concept(conn, "ok", energy=20)
    _concept(conn, "fat", energy=31)
    result = run_migration(conn, Thresholds(max_energy=30))
    assert result["culled"] == 1 and result["violating_remaining"] == 0
    rows = {
        r["id"]: (r["status"], r["reason"])
        for r in conn.execute("SELECT id, status, reason FROM concepts")
    }
    assert rows["fat"] == ("culled", "composition-rule")
    assert rows["ok"][0] == "active"
    # deck rows retained (pair-gate evictee reconstruction):
    assert conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0] == 2


def test_protection_finalist_and_baseline_lineage(tmp_path):
    conn = _db(tmp_path)
    _concept(conn, "anchor-ish", status="finalist", energy=40)
    _concept(conn, "champ", status="active", energy=40)
    conn.execute(
        "INSERT INTO baselines(version, deck_id, crowned_at) "
        "VALUES('v9.9', 'd-champ', '2026-08-13T00:00:00')"
    )
    _concept(conn, "victim", status="active", energy=40)
    result = run_migration(conn, Thresholds(max_energy=30))
    assert result["culled"] == 1  # only 'victim'
    assert result["protected_skipped"] == 1  # 'champ' (finalist filtered by status query)
    statuses = {
        r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM concepts")
    }
    assert statuses["anchor-ish"] == "finalist"
    assert statuses["champ"] == "active"
    assert statuses["victim"] == "culled"


def test_null_counts_skipped_never_culled(tmp_path):
    conn = _db(tmp_path)
    _concept(conn, "mystery", energy=None, pokemon=None)
    result = run_migration(conn, Thresholds(max_energy=30))
    assert result["skipped_null_counts"] == 1 and result["culled"] == 0


def test_idempotent_rerun_receipt(tmp_path):
    conn = _db(tmp_path)
    _concept(conn, "fat", energy=31)
    run_migration(conn, Thresholds(max_energy=30))
    second = run_migration(conn, Thresholds(max_energy=30))
    assert second["culled"] == 0 and second["violating_remaining"] == 0


def test_dry_run_writes_nothing(tmp_path):
    conn = _db(tmp_path)
    _concept(conn, "fat", energy=31)
    result = run_migration(conn, Thresholds(max_energy=30), dry_run=True)
    assert result["culled"] == 1  # counted, then rolled back
    row = conn.execute("SELECT status FROM concepts WHERE id='fat'").fetchone()
    assert row["status"] == "active"


def test_main_requires_at_least_one_threshold(tmp_path):
    db = tmp_path / "t.db"
    deckdb.init_db(deckdb.connect(db))
    with pytest.raises(SystemExit):
        main(["--db", str(db)])


def test_main_virgin_db_path(tmp_path):
    db_path = tmp_path / "deep" / "never" / "t.db"
    assert not db_path.parent.exists()
    result = main(["--db", str(db_path), "--max-energy", "30"])
    assert db_path.exists() and result["culled"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest "tests/test_migrate_composition_cull.py" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.migrate_composition_cull'`

- [ ] **Step 3: Implement the script**

Create `scripts/migrate_composition_cull.py`:

```python
"""Brad-gated one-shot composition cull (spec §4, go-live step 4 — runs
ONLY after the T6 AskUserQuestion gate; thresholds arrive as CLI args,
never baked in). Single BEGIN IMMEDIATE read-decide-act transaction
(.claude/rules/single-actor-worker-tests.md). Reversible: a cull is a
STATUS flip (status='culled', reason='composition-rule'); deck rows are
retained for pair-gate evictee reconstruction; coverage rows untouched.

Protected (spec §4 — deliberately NOT the min-basics precedent, which
culled champion+anchor on purpose): status='finalist' concepts (the
anchor), anchor.ANCHOR_CONCEPT_ID explicitly, and the concept of every
deck referenced by `baselines` (champion lineage, incl. the current
baseline). NULL-count decks are skipped as unjudgeable, never culled.

Usage:
    uv run python scripts/migrate_composition_cull.py --db <path> \
        [--max-energy N] [--min-energy N] [--min-pokemon N] [--max-pokemon N] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ptcg.factory import deckdb  # noqa: E402
from ptcg.factory.anchor import ANCHOR_CONCEPT_ID  # noqa: E402

_CANDIDATES_SQL = (
    "SELECT c.id AS concept_id, d.energy_count AS e, d.pokemon_count AS p "
    "FROM concepts c JOIN decks d ON d.concept_id = c.id "
    "AND d.shell_variant = (SELECT MIN(d2.shell_variant) FROM decks d2 "
    "WHERE d2.concept_id = c.id) "
    "WHERE c.status IN ('untested','active')"
)


@dataclass(frozen=True)
class Thresholds:
    max_energy: int | None = None
    min_energy: int | None = None
    min_pokemon: int | None = None
    max_pokemon: int | None = None

    def any_set(self) -> bool:
        return any(
            v is not None
            for v in (self.max_energy, self.min_energy, self.min_pokemon, self.max_pokemon)
        )

    def violates(self, e: int, p: int) -> bool:
        return (
            (self.max_energy is not None and e > self.max_energy)
            or (self.min_energy is not None and e < self.min_energy)
            or (self.min_pokemon is not None and p < self.min_pokemon)
            or (self.max_pokemon is not None and p > self.max_pokemon)
        )


def _protected_ids(conn) -> set[str]:
    protected = {ANCHOR_CONCEPT_ID}
    protected |= {
        r["id"] for r in conn.execute("SELECT id FROM concepts WHERE status='finalist'")
    }
    protected |= {
        r["concept_id"]
        for r in conn.execute(
            "SELECT d.concept_id AS concept_id FROM baselines b "
            "JOIN decks d ON d.id = b.deck_id"
        )
    }
    return protected


def run_migration(conn, thresholds: Thresholds, dry_run: bool = False) -> dict:
    if not thresholds.any_set():
        raise SystemExit("refusing to run with no thresholds supplied")
    conn.execute("BEGIN IMMEDIATE")
    try:
        protected = _protected_ids(conn)
        rows = conn.execute(_CANDIDATES_SQL).fetchall()
        culled = skipped_null = protected_skipped = 0
        for row in rows:
            if row["e"] is None or row["p"] is None:
                skipped_null += 1
                continue
            if not thresholds.violates(row["e"], row["p"]):
                continue
            if row["concept_id"] in protected:
                protected_skipped += 1
                continue
            cur = conn.execute(
                "UPDATE concepts SET status='culled', reason='composition-rule' "
                "WHERE id=? AND status IN ('untested','active')",
                (row["concept_id"],),
            )
            culled += cur.rowcount
        violating_remaining = sum(
            1
            for row in conn.execute(_CANDIDATES_SQL)
            if row["e"] is not None
            and row["p"] is not None
            and thresholds.violates(row["e"], row["p"])
            and row["concept_id"] not in protected
        )
        result = {
            "examined": len(rows),
            "culled": culled,
            "skipped_null_counts": skipped_null,
            "protected_skipped": protected_skipped,
            "violating_remaining": violating_remaining,
            "dry_run": dry_run,
        }
        conn.execute("ROLLBACK" if dry_run else "COMMIT")
        return result
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db", required=True,
        help="Path to the tournament DB. Never the production tournament.db in tests.",
    )
    p.add_argument("--max-energy", type=int, default=None)
    p.add_argument("--min-energy", type=int, default=None)
    p.add_argument("--min-pokemon", type=int, default=None)
    p.add_argument("--max-pokemon", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> dict:
    args = _parse_args(argv)
    thresholds = Thresholds(
        max_energy=args.max_energy,
        min_energy=args.min_energy,
        min_pokemon=args.min_pokemon,
        max_pokemon=args.max_pokemon,
    )
    conn = deckdb.connect(Path(args.db))
    deckdb.init_db(conn)
    result = run_migration(conn, thresholds, args.dry_run)
    print(json.dumps(result, indent=2))
    if result["violating_remaining"] != 0:
        raise SystemExit(1)  # receipt failed — never exit 0 on a partial cull
    return result


if __name__ == "__main__":
    main()
```

Note on `test_protection_finalist_and_baseline_lineage`'s expected counts (hand-verified against this code): `anchor-ish` is `status='finalist'`, so `_CANDIDATES_SQL`'s status filter never even examines it (it is protected by exclusion, not counted in `protected_skipped`); `champ` IS examined (status `active`), violates, and is skipped via the `baselines` join → `protected_skipped == 1`; `victim` is culled → `culled == 1`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest "tests/test_migrate_composition_cull.py" -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add "scripts/migrate_composition_cull.py" "tests/test_migrate_composition_cull.py"
git commit "scripts/migrate_composition_cull.py" "tests/test_migrate_composition_cull.py" -m "feat: Brad-gated threshold-parameterized composition cull migration (spec §4)"
```

---

### Task 8: FLOOR_BAR / ANCHOR_BAR recalibration + full assertion sweep

**Files:**
- Modify: `src/ptcg/factory/floor.py:46-50` (comment + `FLOOR_BAR`)
- Modify: `src/ptcg/factory/anchor.py:39-40` (comment + `ANCHOR_BAR`)
- Modify: `tests/test_factory_floor.py`, `tests/test_factory_anchor.py`, `tests/test_factory_loop_scheduler.py` (assertion sweep — exact lines below)

**Interfaces:**
- Consumes: the Task 6 record (`FLOOR_BAR_NEW`, `ANCHOR_BAR_NEW`) and the measurement receipt path. The two constants are the ONLY sanctioned unknowables in this plan: they are literally undeterminable until the T6 gate, so they appear below as `<FLOOR_BAR_NEW>` / `<ANCHOR_BAR_NEW>` SUBSTITUTE-AT-T6-GATE tokens. Everything else in this task is concrete.
- Produces: recalibrated gates, live at the go-live scheduler restart. Consumers unchanged: `floor.py:291` (verdict), `anchor.py:260` (verdict), `subscheduler.py:529` (display — references `anchor.ANCHOR_BAR` symbolically, NO edit needed).

**Guarantees from T6:** `FLOOR_BAR_NEW` is an exact multiple of 0.02 (so `FLOOR_BAR_NEW * 50` is an integer `W_f`); `ANCHOR_BAR_NEW` is an exact multiple of 0.005 (so `ANCHOR_BAR_NEW * 200` is an integer `W_a`). Compute `W_f` and `W_a` once, by hand, and use them in every updated assertion below; show the arithmetic in each test comment exactly as the current tests do.

- [ ] **Step 1: Update the constants with provenance comments**

`floor.py:46-50` — replace the 2026-08-04 calibration comment and value:

```python
#: FLOOR_BAR recalibrated <date> (census/screening-regime slice, spec
#: 2026-08-13 §5, T6 Brad gate): percentile-anchored so the Brad-chosen
#: fraction of the current healthy pool fails at birth. Source receipt:
#: experiments/screening-regime-measurement-2026-08-13.md (both runs).
#: Exact multiple of 1/FLOOR_GAMES so boundary tests stay integer-exact.
#: (Previous: 0.40, calibrated 2026-08-04 against the pre-anchor-swap,
#: pre-repair pool — invalidated by the two regime shifts since.)
FLOOR_GAMES = 50
FLOOR_BAR = <FLOOR_BAR_NEW>   # SUBSTITUTE-AT-T6-GATE
```

`anchor.py:39-40`:

```python
ANCHOR_GAMES = 200
#: ANCHOR_BAR recalibrated <date> (census/screening-regime slice, spec
#: 2026-08-13 §5, T6 Brad gate): the baseline lineage's measured
#: WR-vs-anchor (pooled across both measurement runs) plus a margin.
#: Source receipt: experiments/screening-regime-measurement-2026-08-13.md.
#: Exact multiple of 1/ANCHOR_GAMES. (Previous: 0.55, calibrated against
#: the retired anchor.)
ANCHOR_BAR = <ANCHOR_BAR_NEW>   # SUBSTITUTE-AT-T6-GATE
```

- [ ] **Step 2: Assertion sweep — update EVERY site below (grep re-run first: `rg -n "0\.40|0\.55|FLOOR_BAR|ANCHOR_BAR" tests/` and reconcile against this list before editing)**

IN SCOPE (update to `W_f`/`W_a`-derived values; keep each test's semantic — exact-bar/one-under/comfortably-over — and update the hand-verified arithmetic comments):

- `tests/test_factory_floor.py:77` — `assert floor.FLOOR_BAR == 0.40` → `== <FLOOR_BAR_NEW>` (keep the pin-style comment, cite the receipt).
- `tests/test_factory_floor.py:159-165` — EXACT-BAR pass case: wins `20` → `W_f` (docstring arithmetic `W_f/50 == FLOOR_BAR -> pass`).
- `tests/test_factory_floor.py:174-178` — one-under fail case: wins `19` → `W_f - 1`.
- `tests/test_factory_floor.py:261` — pass case wins `23` → any wins `>= W_f` (use `W_f + 3` and update the comment arithmetic).
- `tests/test_factory_anchor.py:48` — `assert anchor.ANCHOR_BAR == 0.55` → `== <ANCHOR_BAR_NEW>`.
- `tests/test_factory_anchor.py:364` — exact-bar pass: wins `110` → `W_a`; `wr == pytest.approx(W_a / 200)`.
- `tests/test_factory_anchor.py:371` — one-under fail: `109` → `W_a - 1` (update the `109/200 = 0.545` comment).
- `tests/test_factory_anchor.py:413` — clear fail: keep `80/200` ONLY if `80 < W_a`; otherwise use `W_a - 10`; update the comment.
- `tests/test_factory_anchor.py:462` — docstring arithmetic `0.55 * 200 = 110` → `<ANCHOR_BAR_NEW> * 200 = W_a`.
- `tests/test_factory_loop_scheduler.py:386` — `23/50 = 0.46 >= FLOOR_BAR 0.40` → wins `W_f + 3`, comment updated.
- `tests/test_factory_loop_scheduler.py:419` — `10/50 = 0.20 < 0.40` fail → keep `10` ONLY if `10 < W_f`; otherwise `W_f - 5`.
- `tests/test_factory_loop_scheduler.py:431` — `23/50 = 0.46` pass → `W_f + 3`.
- `tests/test_factory_loop_scheduler.py:752` — `wr == pytest.approx(0.55)` exact-bar anchor pass → `W_a/200`.
- `tests/test_factory_loop_scheduler.py:962-975` — E2E docstring + staged win counts: recompute every bar-relative number (`23/50`, `80/200`, `120/200`, `0.55`, `0.40` mentions) against `W_f`/`W_a`; the netcheck numbers (`54/100`, `55/100` vs `0.55`) are NETCHECK_BAR and MUST NOT change.
- `tests/test_factory_loop_scheduler.py:1087` — elect-fail `80/200 = 0.40 < ANCHOR_BAR` → keep only if `80 < W_a`, else `W_a - 10`.

OUT OF SCOPE — do NOT touch (different constants that coincidentally share the literals): `tests/test_factory_netcheck.py` (NETCHECK_BAR = 0.55, a net-swap bar), `tests/test_factory_pairgate.py` (PAIR_GATE_BAR = 0.55), `tests/test_factory_gate.py` / `tests/test_factory_submit.py` / `tests/test_factory_best_cell.py` / `tests/test_factory_cap_concurrency.py` / `tests/test_factory_harvest.py` (local_wr/incumbent-margin fixtures), `tests/test_factory_loop_match.py` / `tests/test_factory_loop_crown.py` (win-share fixtures), `tests/test_anchor_minitournament.py` (head-to-head fixture values), `floor.py`'s `FLOOR_GAMES`/`FLOOR_MAX_ATTEMPTS` (unchanged), `subscheduler.py:529` (symbolic).

- [ ] **Step 3: Run the three swept test files**

Run: `uv run pytest "tests/test_factory_floor.py" "tests/test_factory_anchor.py" "tests/test_factory_loop_scheduler.py" -v`
Expected: PASS. Floor re-pick mechanics (`floor.py:313-334`, `FLOOR_MAX_ATTEMPTS=3`) are intentionally untouched — their existing coverage passing unmodified is itself a spec §7 requirement ("floor re-pick mechanics unchanged and still covered").

- [ ] **Step 4: Commit**

```bash
git add "src/ptcg/factory/floor.py" "src/ptcg/factory/anchor.py" "tests/test_factory_floor.py" "tests/test_factory_anchor.py" "tests/test_factory_loop_scheduler.py"
git commit "src/ptcg/factory/floor.py" "src/ptcg/factory/anchor.py" "tests/test_factory_floor.py" "tests/test_factory_anchor.py" "tests/test_factory_loop_scheduler.py" -m "feat: recalibrate FLOOR_BAR/ANCHOR_BAR from screening-regime measurement (spec §5, T6 gate)"
```

---

## Go-Live (post-merge rungs — NOT tasks; run in order; SUBMIT_HOLD stays ON throughout)

Every command line below MUST be reconciled against the script's real `--help` (or its `add_argument` block) at Finish, per `.claude/rules/golive-command-preflight.md` — the plan is written before the scripts exist. All commands run from the repo root: `C:\Users\Brad\Dev Folder\Product\ptcg-ai-battle-challenge-strategy` (quote paths — the path contains a space). Elevation note: `Disable-ScheduledTask`/`Enable-ScheduledTask` may prompt UAC on this machine — treat any elevation as a BLOCKING human-input request (`.claude/rules/factory-resume-probe.md`, 2026-08-10 addendum).

1. **Hold the workers (durable — `Stop-ScheduledTask` alone is not, per the watchdog-respawn lesson):**
   ```powershell
   Disable-ScheduledTask -TaskName ptcg-factory-scheduler
   Disable-ScheduledTask -TaskName ptcg-factory-runner
   Disable-ScheduledTask -TaskName ptcg-factory-continuous
   Stop-ScheduledTask -TaskName ptcg-factory-scheduler
   Stop-ScheduledTask -TaskName ptcg-factory-runner
   Stop-ScheduledTask -TaskName ptcg-factory-continuous
   ```
   Verify no runner/scheduler/continuous python processes remain before the
   migration. `ptcg-factory-continuous` also calls `deckdb.init_db()` every
   ~15 minutes (submission-scheduler + episode-harvester ticks) and would
   otherwise contend for the same write lock the migration holds — hold it
   alongside scheduler+runner for the migration window, not just those two.
   This hold is DEBT owned by this go-live: do not close Finish until the re-enable is verified `Ready` via `scripts\verify_factory_tasks.ps1`.
2. **Column migration + backfill** *(reconcile against `--help` at Finish)*:
   ```bash
   uv run python scripts/migrate_composition_columns.py --db "experiments/factory/tournament.db" --dry-run
   uv run python scripts/migrate_composition_columns.py --db "experiments/factory/tournament.db"
   uv run python scripts/migrate_composition_columns.py --db "experiments/factory/tournament.db"   # idempotent re-run receipt: backfilled=0, null_remaining stable
   ```
   Read the printed JSON receipts (never trust a piped tail — run bare). Expect `backfilled` ≈ the live decks row count on first run, `null_remaining == skipped_unknown_ids` (ideally 0).

   **Operator expectation:** the write phase (guarded `ALTER`s + a single
   batched `executemany` UPDATE + index creation) is one `BEGIN IMMEDIATE`
   transaction. A corrupt row (unparseable `cards` JSON, or any other
   unhandled exception) inside that phase aborts the WHOLE migration
   atomically — `ROLLBACK`, no partial progress, no half-backfilled table.
   The correct response to a failure is abort-and-retry after fixing the
   offending row/data, not resuming a partial run. (The per-row read +
   `composition_counts` pass that decides *what* to write runs BEFORE this
   transaction opens — see the script's `run_migration` docstring, fix
   round 1 — so a bad row found there is simply counted as
   `skipped_unknown_ids` and left NULL; it does not abort anything. Only a
   failure inside the write phase itself triggers the whole-migration
   rollback described here.)
3. **Restart workers on the merged code:**
   ```powershell
   Enable-ScheduledTask -TaskName ptcg-factory-scheduler
   Enable-ScheduledTask -TaskName ptcg-factory-runner
   Enable-ScheduledTask -TaskName ptcg-factory-continuous
   Start-ScheduledTask -TaskName ptcg-factory-scheduler
   Start-ScheduledTask -TaskName ptcg-factory-runner
   Start-ScheduledTask -TaskName ptcg-factory-continuous
   powershell -ExecutionPolicy Bypass -File scripts\verify_factory_tasks.ps1
   ```
   Bars (Task 8) go live with this restart — no separate step.
4. **Ordering-live verification receipt** — prove the new ORDER BY served a real scheduling tick, not just that the code merged. After the scheduler's next tick (~minutes), run:
   ```bash
   uv run python -c "
   import sqlite3
   conn = sqlite3.connect('file:experiments/factory/tournament.db?mode=ro', uri=True)
   conn.row_factory = sqlite3.Row
   rows = conn.execute(
       \"SELECT g.id, d.concept_id, d.energy_count, d.pokemon_count \"
       \"FROM games g JOIN decks d ON d.id = g.deck_a_id \"
       \"WHERE g.purpose='screening' ORDER BY g.id DESC LIMIT 20\").fetchall()
   for r in rows:
       print(dict(r))
   "
   ```
   PASS = the newest screening subjects show `rating IS NULL, games_played = 0`; `energy_count = 16` for all subjects (expected — the only energy value in the unrated mass; the 665 lean-energy=12 concepts are all already rated and correctly sort behind the unrated mass under the chosen worst-coverage-first tie-break semantics); `pokemon_count` front-loaded 21 then 24 ahead of the pk=27 bulk (the discriminating signal — pre-branch ordering would return pk=27 rows in concept_id order); and no NULL counts. Record the output in `experiments/EXPERIMENTS.md`.
5. **SKIPPED per T6 gate (NO CULL decision, 2026-08-13):** no cull migration exists or is needed; the T4 priority ordering carries the composition directive. T7 (`scripts/migrate_composition_cull.py`) was SKIPPED at the T6 Brad gate — the measured active pool has no composition tail to cull.
6. **SUBMIT_HOLD:** remains ON (freeze until 2026-08-17). This go-live changes screening/pruning only; nothing here uploads.

---

## Plan self-review (performed at authoring time)

1. **Spec coverage:** §1 columns/stamping/backfill-not-in-init_db → T1/T2/T3; §2 ORDER BY + NULLS LAST + pinned join (canonical-deck join, no aggregate) + EQP guard → T4; §3 measurement (histograms, ~40×50×2 stratified runs, both reported, disk-light, current regime) → T5; §4 Brad gate + cull pattern → T6/T7; §5 bars + provenance + one-task sweep → T8; §6 inertness + go-live sequence + SUBMIT_HOLD → Global Constraints + Go-Live; §7 testing items → each named test exists in T1-T8 (count pins, stamping sweep, ORDER BY fixture, migration idempotency + virgin paths, EQP, bar sweep, floor re-pick untouched-and-green).
2. **Placeholder scan:** the only non-concrete values are `<FLOOR_BAR_NEW>`/`<ANCHOR_BAR_NEW>`/`<T6-VALUE>` — sanctioned SUBSTITUTE-AT-T6-GATE tokens (unknowable until measurement, per dispatch); plus two explicitly-flagged landmark-verify points (reseed local variable names in T2 Step 6; `play_match` result attributes in T5 Step 3) where the plan gives the full code shape and requires reconciliation against the real file rather than guessing.
3. **Type consistency:** `composition_counts(list[int]) -> tuple[int, int]` used identically in T2 (stamping), T3 (backfill), T5 (direct computation); `_insert_deck_row(c, did, cid, cards)` defined and called with that exact arity; `Thresholds` fields match `_parse_args` flags; `run_migration` receipt keys match their tests; `energy_quartile_bounds`/`stratum_of` signatures match their unit tests.
4. **Single-actor walk (`.claude/rules/single-actor-worker-tests.md`) against this plan's own sketches:** both migrations are single `BEGIN IMMEDIATE` read-decide-act transactions with in-transaction re-count receipts; the cull's guarded UPDATE (`WHERE ... AND status IN (...)`) is race-safe against a concurrent status writer; the census query change stays inside the existing `schedule_screening_games` lock and is EQP-guarded at the real SQL; the measurement script takes no lock at all (mode=ro). No new always-on writer, no new shared-file read-modify-write window → no new interleaved-mutation test required; `tests/fixtures/race.py` cited in Global Constraints should that change.
5. **Arithmetic:** anchor composition (22/12/26/8) verified by execution; quartile example verified by hand; T4's expected ordering verified by hand against the exact ORDER BY; T7's protection-count expectations annotated with their derivation.
