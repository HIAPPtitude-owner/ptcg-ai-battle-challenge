# UI Remove-Any-Deck Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `ptcg-factory-ui` so Brad can list/cull ANY active deck, search + bulk-cull the untested queue, and restore any culled concept (two-tier restore) — per the approved spec `docs/superpowers/specs/2026-08-05-ui-remove-any-deck-design.md`.

**Architecture:** Three-module split — `ui_actions.py` (new: queries + write transactions), `ui_pages.py` (new: pure HTML rendering), `ui_server.py` (existing: routing only; existing renderers move out with back-compat aliases). One `deckdb` migration task rebuilds the `decisions` table (SQLite CHECK constraints can't be ALTERed). *Plan-level refinement of the spec:* the spec said "ui_server keeps routing + transaction logic"; transaction logic goes to `ui_actions.py` instead so `ui_server.py` stays under the 500-line rule (432 lines today). No semantic change.

**Tech Stack:** Python stdlib only (http.server, sqlite3), pytest, `tests/fixtures/race.py` harness.

## Global Constraints

- Branch: `feature/ui-remove-any-deck`, main repo working tree (no worktree). Repo path contains a space — always quote.
- All DB writes via `deckdb._write` (single `BEGIN IMMEDIATE` txn). The split read→decide→unconditional-UPDATE shape is REJECTED in this repo (TOCTOU class, commit `178043b`).
- All timestamps UTC-aware: `dt.datetime.now(dt.timezone.utc).isoformat()`. Never naive `now()` (two shipped time-seam bugs).
- `html.escape()` every user-derived string rendered into HTML.
- Any `Path.write_text` call MUST pass `encoding="utf-8"` (Windows cp1252 truncate-then-crash lesson).
- Implementers run ONLY their targeted test file (`uv run pytest tests/<file> -v`); the orchestrator owns full-suite runs at sync points. NEVER use the Monitor tool; never background a test run. (`.claude/rules/dispatch-test-run-directive.md`)
- Ladder identity files (`src/ptcg/submission_main.py`, `src/ptcg/agents/current.py`) are OUT OF SCOPE — must show empty diff at Finish.
- `ptcg-factory-ui` is a long-lived worker: code written here is INERT until the explicit post-merge task restart (no PAUSE/hold needed during the slice).
- New `decisions` schema must be a strict superset of the old (old inserts stay valid): `action` CHECK expands, `deck_id` NOT NULL is dropped, new columns are nullable.

## Verified landmarks (grepped at plan-lock, 2026-08-05)

| Landmark | Location | Fact |
|---|---|---|
| decisions DDL | `src/ptcg/factory/deckdb.py:100-103` | `deck_id TEXT NOT NULL`, `CHECK(action IN ('remove','pass'))` — new actions/NULL deck_id violate it; rebuild required |
| `_write` | `deckdb.py:47` | `_write(conn, fn)` BEGIN IMMEDIATE wrapper |
| `init_db` | `deckdb.py:188-203` | idempotent, per-statement DDL inside one `_write`; meta schema_version insert |
| `apply_decision` | `ui_server.py:90-114` | deck-id keyed; explicit column list in INSERT (survives additive columns) |
| renderers | `ui_server.py:135-161, 315-348` | `_render_page`, `_render_status_page` — move to `ui_pages.py` with aliases |
| routing | `ui_server.py:388-430` | flat `if self.path` dispatch in `do_GET`/`do_POST`; `_send_html`; 303 to `/` |
| card names | `ui_server.py:117-132` | `_card_names` / `_card_id_to_name` |
| bottom-10 canonical-deck join | `ui_server.py:58-70` | `MIN(shell_variant)` sub-select — reuse in pool query |
| `current_baseline` | `loop_state.py:70-80` | returns `baselines` row (`version`, `deck_id`) via `meta['baseline_version']` |
| census promotion | `census.py:456-496` | `promote_proven_singles` — `UPDATE concepts SET status='active' WHERE id=? AND status='untested'` inside `_write`; the race-test counterpart |
| race harness | `tests/fixtures/race.py:31-98` | `race_two(db_path, fn, trace_pred, *, setup, timeout)` → `(results, count)`; same `fn` on both threads; trace gets `.strip().upper()`-ed SQL |
| test conventions | `tests/test_factory_ui_server.py:27-49` | `_seed_concept(c, concept_id, deck_id, *, status, rating, games_played, cards)`, `_seeded_db(tmp_path)` |
| concepts statuses | `deckdb.py:68-72` | CHECK: `untested/active/culled/unbuildable/finalist` |
| `SCREENING_FLOOR` | `census.py` import in `ui_server.py:37` | games floor constant used by bottom-10/pool |

---

### Task 1: `decisions` table v2 migration in deckdb

**Files:**
- Modify: `src/ptcg/factory/deckdb.py` (decisions DDL in `_DDL_STATEMENTS` ~line 100; new `migrate_decisions` + call at end of `init_db` ~line 203)
- Test: `tests/test_factory_deckdb_migration.py` (create)

**Interfaces:**
- Produces: `decisions` table with columns `id, deck_id (nullable), concept_id (nullable), action CHECK in ('remove','pass','restore','bulk-remove','bulk-restore'), actor, timestamp, prior_status (nullable)`; `deckdb.migrate_decisions(conn)` idempotent, auto-called by `init_db`.
- Consumes: existing `_write`, `_DDL_STATEMENTS`, `init_db`.

- [ ] **Step 1: Write the failing tests**

```python
"""Migration tests for the decisions-table v2 rebuild (ui-remove-any-deck T1)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ptcg.factory import deckdb

_OLD_DECISIONS_DDL = """
    CREATE TABLE decisions(
      id INTEGER PRIMARY KEY AUTOINCREMENT, deck_id TEXT NOT NULL,
      action TEXT NOT NULL CHECK(action IN ('remove','pass')),
      actor TEXT NOT NULL, timestamp TEXT NOT NULL)
"""


def _old_schema_db(tmp_path: Path) -> sqlite3.Connection:
    """A DB whose decisions table has the PRE-migration shape, with one row."""
    conn = deckdb.connect(tmp_path / "t.db")
    conn.execute(_OLD_DECISIONS_DDL)
    conn.execute(
        "INSERT INTO decisions(deck_id, action, actor, timestamp) "
        "VALUES('d0','remove','brad','2026-08-01T00:00:00+00:00')"
    )
    return conn


def test_migrate_rebuilds_old_table_preserving_rows(tmp_path):
    conn = _old_schema_db(tmp_path)
    deckdb.migrate_decisions(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(decisions)")}
    assert {"deck_id", "concept_id", "action", "actor", "timestamp", "prior_status"} <= cols
    row = conn.execute("SELECT deck_id, action, actor FROM decisions").fetchone()
    assert (row["deck_id"], row["action"], row["actor"]) == ("d0", "remove", "brad")
    # new action values and NULL deck_id are now legal
    conn.execute(
        "INSERT INTO decisions(concept_id, action, actor, timestamp, prior_status) "
        "VALUES('c1','restore','brad','2026-08-05T00:00:00+00:00','untested')"
    )
    # old-shape insert (strict-superset requirement) still legal
    conn.execute(
        "INSERT INTO decisions(deck_id, action, actor, timestamp) "
        "VALUES('d1','pass','brad','2026-08-05T00:00:00+00:00')"
    )


def test_migrate_is_idempotent(tmp_path):
    conn = _old_schema_db(tmp_path)
    deckdb.migrate_decisions(conn)
    before = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    deckdb.migrate_decisions(conn)  # second call: no-op, no data change
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == before


def test_init_db_fresh_creates_v2_and_runs_migration(tmp_path):
    conn = deckdb.connect(tmp_path / "fresh.db")
    deckdb.init_db(conn)
    conn.execute(
        "INSERT INTO decisions(concept_id, action, actor, timestamp, prior_status) "
        "VALUES('c1','bulk-remove','brad','2026-08-05T00:00:00+00:00','untested')"
    )


def test_init_db_on_old_db_migrates(tmp_path):
    conn = _old_schema_db(tmp_path)
    deckdb.init_db(conn)  # init_db calls migrate_decisions at the end
    conn.execute(
        "INSERT INTO decisions(concept_id, action, actor, timestamp, prior_status) "
        "VALUES('c1','bulk-restore','brad','2026-08-05T00:00:00+00:00','culled')"
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_factory_deckdb_migration.py -v`
Expected: FAIL — `AttributeError: module 'ptcg.factory.deckdb' has no attribute 'migrate_decisions'` (and CHECK violations if partially present).

- [ ] **Step 3: Implement**

In `deckdb.py`, replace the decisions entry in `_DDL_STATEMENTS` (currently lines 99-104) with:

```python
    """
    CREATE TABLE IF NOT EXISTS decisions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      deck_id TEXT,
      concept_id TEXT,
      action TEXT NOT NULL
        CHECK(action IN ('remove','pass','restore','bulk-remove','bulk-restore')),
      actor TEXT NOT NULL, timestamp TEXT NOT NULL,
      prior_status TEXT)
    """,
```

Add after `init_db`'s current body (and call it as `init_db`'s last line, AFTER the DDL `_write` completes):

```python
def migrate_decisions(conn: sqlite3.Connection) -> None:
    """Idempotently rebuild a v1 `decisions` table to the v2 shape (nullable
    deck_id, concept_id + prior_status columns, expanded action CHECK).
    SQLite cannot ALTER a CHECK constraint or drop NOT NULL, so this is a
    rename -> create-v2 -> copy -> drop rebuild inside ONE transaction.
    Detection keys on the stored DDL text: a table whose sql already names
    'restore' is v2 — nothing to do. Strict superset: every v1 row/insert
    shape remains valid in v2.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='decisions'"
    ).fetchone()
    if row is None or "'restore'" in row["sql"]:
        return

    def _apply(c: sqlite3.Connection) -> None:
        c.execute("ALTER TABLE decisions RENAME TO decisions_v1")
        c.execute(_DDL_STATEMENTS[_DECISIONS_DDL_INDEX])
        c.execute(
            "INSERT INTO decisions(id, deck_id, action, actor, timestamp) "
            "SELECT id, deck_id, action, actor, timestamp FROM decisions_v1"
        )
        c.execute("DROP TABLE decisions_v1")

    _write(conn, _apply)
```

Where `_DECISIONS_DDL_INDEX` is a module constant set to the decisions entry's index in `_DDL_STATEMENTS` (compute it once next to the list, e.g. `_DECISIONS_DDL_INDEX = next(i for i, s in enumerate(_DDL_STATEMENTS) if "decisions" in s)` — or hardcode with a comment; implementer's choice, keep it self-checking). In `init_db`, add `migrate_decisions(conn)` as the final statement of the function (outside the DDL `_write`, its own transaction).

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_factory_deckdb_migration.py -v` — Expected: 4 PASS.
Also run the neighbor file the schema change most directly touches: `uv run pytest tests/test_factory_ui_server.py -v` — Expected: all PASS (old-shape inserts remain valid).

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/factory/deckdb.py tests/test_factory_deckdb_migration.py
git commit -m "feat: decisions table v2 (restore/bulk actions, concept_id, prior_status) via idempotent rebuild"
```

---

### Task 2: `ui_actions.py` — read-side queries (search, pool, champion/anchor)

**Files:**
- Create: `src/ptcg/factory/ui_actions.py`
- Test: `tests/test_factory_ui_actions.py` (create)

**Interfaces:**
- Produces:
  - `search_concepts(conn, q: str, tab: str = "untested", limit: int = 200) -> tuple[list[dict], int]` — rows `{concept_id, cores, status, reason, rating, games_played}`, plus TOTAL match count. `tab` in `("untested","culled","active","all")`; raises `ValueError` otherwise.
  - `pool_decks(conn) -> list[dict]` — every `status='active'` concept with canonical deck: `{concept_id, deck_id, cards, rating, games_played}`; rating/games None when no coverage row (unscreened). Sorted rating DESC, NULLs last.
  - `anchor_concept(conn) -> dict | None` — the `status='finalist'` row `{concept_id, cores}`.
  - `champion_concept_id(conn) -> str | None` — concept of the current baseline champion's deck.
- Consumes: `deckdb`, `loop_state.current_baseline` (T1's migration irrelevant here — read-only).

- [ ] **Step 1: Write the failing tests**

```python
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
    assert ui_actions.anchor_concept(conn)["concept_id"] == "the-anchor"
    assert ui_actions.champion_concept_id(conn) == "champ-concept"


def test_anchor_and_champion_absent(tmp_path):
    conn = _db(tmp_path)
    assert ui_actions.anchor_concept(conn) is None
    assert ui_actions.champion_concept_id(conn) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_factory_ui_actions.py -v`
Expected: FAIL — `ModuleNotFoundError`/`ImportError` for `ptcg.factory.ui_actions`.

- [ ] **Step 3: Implement `src/ptcg/factory/ui_actions.py` (read side)**

```python
"""Query + write actions for the pool/search/restore UI (ui-remove-any-deck).

Write functions all go through `deckdb._write` (BEGIN IMMEDIATE) — the split
read->decide->UPDATE shape is a REJECTED pattern in this repo (TOCTOU class,
commit 178043b). Read functions are plain SELECTs.
"""
from __future__ import annotations

import datetime as dt
import sqlite3

from ptcg.factory import deckdb, loop_state

_TABS = ("untested", "culled", "active", "all")
SEARCH_ROW_CAP = 200

#: Canonical-deck sub-select, mirroring ui_server._BOTTOM_TEN_QUERY's
#: MIN(shell_variant) rationale (one row per concept).
_POOL_QUERY = (
    "SELECT c.id AS concept_id, d.id AS deck_id, d.cards AS cards, "
    "co.rating AS rating, co.games_played AS games_played "
    "FROM concepts c "
    "JOIN decks d ON d.concept_id = c.id AND d.shell_variant = ("
    "SELECT MIN(d2.shell_variant) FROM decks d2 WHERE d2.concept_id = c.id) "
    "LEFT JOIN coverage co ON co.concept_id = c.id "
    "WHERE c.status = 'active' "
    "ORDER BY co.rating DESC, c.id ASC"
)


def search_concepts(
    conn: sqlite3.Connection, q: str, tab: str = "untested", limit: int = SEARCH_ROW_CAP
) -> tuple[list[dict], int]:
    if tab not in _TABS:
        raise ValueError(f"search_concepts: tab must be one of {_TABS}, got {tab!r}")
    like = f"%{q}%"
    where = "(c.id LIKE ? OR c.cores LIKE ?)"
    params: list = [like, like]
    if tab != "all":
        where += " AND c.status = ?"
        params.append(tab)
    total = conn.execute(
        f"SELECT COUNT(*) FROM concepts c WHERE {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT c.id AS concept_id, c.cores AS cores, c.status AS status, "
        "c.reason AS reason, co.rating AS rating, co.games_played AS games_played "
        f"FROM concepts c LEFT JOIN coverage co ON co.concept_id = c.id WHERE {where} "
        "ORDER BY c.id ASC LIMIT ?",
        (*params, limit),
    ).fetchall()
    return [dict(r) for r in rows], total


def pool_decks(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(_POOL_QUERY).fetchall()]


def anchor_concept(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT id AS concept_id, cores FROM concepts WHERE status='finalist'"
    ).fetchone()
    return dict(row) if row is not None else None


def champion_concept_id(conn: sqlite3.Connection) -> str | None:
    baseline = loop_state.current_baseline(conn)
    if baseline is None:
        return None
    row = conn.execute(
        "SELECT concept_id FROM decks WHERE id=?", (baseline["deck_id"],)
    ).fetchone()
    return row["concept_id"] if row is not None else None
```

(SQLite sorts NULL smaller than any value, so `co.rating DESC` puts unscreened NULLs last — pinned by `test_pool_lists_all_active_including_unscreened`.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_factory_ui_actions.py -v` — Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/factory/ui_actions.py tests/test_factory_ui_actions.py
git commit -m "feat: ui_actions read side — concept search, full active pool, anchor/champion lookups"
```

---

### Task 3: `ui_actions.py` — single-concept remove/restore (two-tier) + finalist guard

**Files:**
- Modify: `src/ptcg/factory/ui_actions.py`
- Test: `tests/test_factory_ui_actions.py` (append)

**Interfaces:**
- Produces:
  - `class FinalistProtectedError(ValueError)`
  - `apply_concept_decision(conn, concept_id: str, action: str, actor: str = "brad") -> str` — returns the NEW status. `action` in `("remove","restore")`.
  - `_restore_target(c, concept_id) -> str` (module-internal, reused by T4 bulk).
- Consumes: T1's v2 `decisions` schema (`concept_id`, `prior_status`, expanded CHECK).

- [ ] **Step 1: Write the failing tests (append to `tests/test_factory_ui_actions.py`)**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_factory_ui_actions.py -v`
Expected: new tests FAIL (`AttributeError: apply_concept_decision`); T2 tests still PASS.

- [ ] **Step 3: Implement (append to `ui_actions.py`)**

```python
class FinalistProtectedError(ValueError):
    """Culling the anchor (status='finalist') is refused."""


def _restore_target(c: sqlite3.Connection, concept_id: str) -> str:
    """Two-tier restore: back to 'active' ONLY when the most recent UI cull
    of this concept recorded prior_status='active'; every other case —
    no audit row (reseed-culled), v1 rows (NULL prior_status), culled-while-
    untested — restores to 'untested' so the census screening queue and the
    0.40 early floor re-gate it before it can enter the pool."""
    last = c.execute(
        "SELECT prior_status FROM decisions "
        "WHERE concept_id=? AND action IN ('remove','bulk-remove') "
        "ORDER BY id DESC LIMIT 1",
        (concept_id,),
    ).fetchone()
    return "active" if last is not None and last["prior_status"] == "active" else "untested"


def _decide_one(c: sqlite3.Connection, concept_id: str, action: str, actor: str) -> str:
    """Shared per-concept core for single + bulk paths. Runs INSIDE a
    deckdb._write transaction owned by the caller. Returns the new status."""
    row = c.execute("SELECT status FROM concepts WHERE id=?", (concept_id,)).fetchone()
    if row is None:
        raise ValueError(f"no concept with id={concept_id!r}")
    status = row["status"]
    if action in ("remove", "bulk-remove"):
        if status == "finalist":
            raise FinalistProtectedError("anchor is protected")
        if status not in ("untested", "active"):
            raise ValueError(f"cannot remove a {status!r} concept")
        new_status = "culled"
        reason = f"ui-cull: {dt.datetime.now(dt.timezone.utc).isoformat()}"
    elif action in ("restore", "bulk-restore"):
        if status != "culled":
            raise ValueError(f"restore requires a culled concept, got {status!r}")
        new_status = _restore_target(c, concept_id)
        reason = f"ui-restore: {dt.datetime.now(dt.timezone.utc).isoformat()}"
    else:
        raise ValueError(f"action must be remove/restore, got {action!r}")
    c.execute(
        "INSERT INTO decisions(concept_id, action, actor, timestamp, prior_status) "
        "VALUES(?,?,?,?,?)",
        (concept_id, action, actor, dt.datetime.now(dt.timezone.utc).isoformat(), status),
    )
    c.execute("UPDATE concepts SET status=?, reason=? WHERE id=?", (new_status, reason, concept_id))
    return new_status


def apply_concept_decision(
    conn: sqlite3.Connection, concept_id: str, action: str, actor: str = "brad"
) -> str:
    if action not in ("remove", "restore"):
        raise ValueError(f"action must be 'remove' or 'restore', got {action!r}")
    return deckdb._write(conn, lambda c: _decide_one(c, concept_id, action, actor))
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_factory_ui_actions.py -v` — Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/factory/ui_actions.py tests/test_factory_ui_actions.py
git commit -m "feat: concept-level remove/restore with two-tier restore and finalist guard"
```

---

### Task 4: `ui_actions.py` — bulk cull/restore (single txn, count recomputed inside)

**Files:**
- Modify: `src/ptcg/factory/ui_actions.py`
- Test: `tests/test_factory_ui_actions.py` (append)

**Interfaces:**
- Produces: `apply_bulk_decision(conn, q: str, tab: str, action: str, actor: str = "brad") -> int` — returns the ACTUAL count acted on. Constraints: `action='bulk-remove'` requires `tab='untested'`; `action='bulk-restore'` requires `tab='culled'`; empty/whitespace `q` raises `ValueError` (no "cull everything" footgun).
- Consumes: T3's `_decide_one`.

- [ ] **Step 1: Write the failing tests (append)**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_factory_ui_actions.py -v` — Expected: new tests FAIL (`AttributeError: apply_bulk_decision`).

- [ ] **Step 3: Implement (append to `ui_actions.py`)**

```python
_BULK_TAB_FOR_ACTION = {"bulk-remove": "untested", "bulk-restore": "culled"}


def apply_bulk_decision(
    conn: sqlite3.Connection, q: str, tab: str, action: str, actor: str = "brad"
) -> int:
    """Cull/restore EVERY concept matching (q, tab) in ONE BEGIN IMMEDIATE
    transaction: the match list is computed INSIDE the txn (never trusted
    from a preview page), then each concept goes through the same
    `_decide_one` core as the single-row path. Returns the actual count."""
    required_tab = _BULK_TAB_FOR_ACTION.get(action)
    if required_tab is None:
        raise ValueError(f"bulk action must be bulk-remove/bulk-restore, got {action!r}")
    if tab != required_tab:
        raise ValueError(f"{action} requires tab={required_tab!r}, got {tab!r}")
    if not q.strip():
        raise ValueError("bulk actions require a non-empty search query")

    def _apply(c: sqlite3.Connection) -> int:
        like = f"%{q}%"
        ids = [
            r["id"]
            for r in c.execute(
                "SELECT id FROM concepts WHERE status=? AND (id LIKE ? OR cores LIKE ?) "
                "ORDER BY id",
                (required_tab, like, like),
            ).fetchall()
        ]
        for cid in ids:
            _decide_one(c, cid, action, actor)
        return len(ids)

    return deckdb._write(conn, _apply)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_factory_ui_actions.py -v` — Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/factory/ui_actions.py tests/test_factory_ui_actions.py
git commit -m "feat: bulk cull/restore scoped to query+tab, count recomputed inside one txn"
```

---

### Task 5: `ui_pages.py` — rendering (nav, pool, search, bulk-confirm; move existing renderers)

**Files:**
- Create: `src/ptcg/factory/ui_pages.py`
- Modify: `src/ptcg/factory/ui_server.py` (delete `_render_page`/`_render_status_page` bodies; import + alias from `ui_pages`; `_card_names` moves too)
- Test: `tests/test_factory_ui_pages.py` (create)

**Interfaces:**
- Produces (all pure: data in → HTML `str` out; every dynamic string `html.escape`d):
  - `render_review_page(rows) -> str` (moved `_render_page`, byte-identical body content plus the nav header)
  - `render_status_page(status) -> str` (moved `_render_status_page` + nav)
  - `render_pool_page(rows, anchor, champion_concept_id) -> str`
  - `render_search_page(rows, total, q, tab) -> str`
  - `render_bulk_confirm_page(q, tab, action, preview_count) -> str`
  - `render_bulk_result_page(n, action, q, tab) -> str`
  - `NAV_HTML` constant: links `/` (Review), `/pool`, `/search`, `/status`
- Consumes: T2 row shapes; `ui_server._card_names` MOVES here (it's rendering-only); `ui_server` re-imports for back-compat: `from ptcg.factory.ui_pages import render_review_page as _render_page` etc.
- **Coverage-sweep note (`.claude/rules/test-coverage-sweep.md`):** before moving, grep consumers: `rg -n "_render_page|_render_status_page|_card_names" src/ scripts/ tests/` and keep aliases for every hit so no consumer or test breaks.

- [ ] **Step 1: Write the failing tests**

```python
"""Render smoke tests for ui_pages (ui-remove-any-deck T5)."""
from __future__ import annotations

from ptcg.factory import ui_pages


def test_pool_page_pins_anchor_and_badges_champion():
    rows = [
        {"concept_id": "champ-c", "deck_id": "d1", "cards": [3, 3], "rating": 2.0, "games_played": 40},
        {"concept_id": "fresh-c", "deck_id": "d2", "cards": [3], "rating": None, "games_played": None},
    ]
    anchor = {"concept_id": "the-anchor", "cores": '["anchor"]'}
    page = ui_pages.render_pool_page(rows, anchor, "champ-c")
    assert "anchor — protected" in page and "the-anchor" in page
    assert "current champion" in page          # badge on champ-c
    assert "unscreened" in page                # fresh-c marker
    assert page.count('action="/concept-decision"') == 2  # anchor row has NO form
    assert "<nav>" in page


def test_search_page_shows_cap_note_and_row_actions():
    rows = [
        {"concept_id": "u1", "cores": '["Abomasnow"]', "status": "untested",
         "reason": "", "rating": None, "games_played": None},
        {"concept_id": "k1", "cores": '["Krabby"]', "status": "culled",
         "reason": "reseed", "rating": None, "games_played": None},
    ]
    page = ui_pages.render_search_page(rows, 483, "Abo", "untested")
    assert "showing 2 of 483" in page
    assert 'value="remove"' in page            # untested row -> Remove
    page2 = ui_pages.render_search_page(rows, 2, "Kra", "culled")
    assert 'value="restore"' in page2          # culled tab -> Restore
    assert 'action="/bulk-confirm"' in page2   # bulk entry point on culled tab
    page3 = ui_pages.render_search_page(rows, 2, "x", "all")
    assert 'action="/bulk-confirm"' not in page3  # no bulk on 'all'


def test_bulk_confirm_and_result_pages():
    page = ui_pages.render_bulk_confirm_page("Abomasnow", "untested", "bulk-remove", 483)
    assert "483" in page and "Abomasnow" in page and 'action="/bulk-decision"' in page
    result = ui_pages.render_bulk_result_page(7, "bulk-remove", "Abomasnow", "untested")
    assert "7" in page or "7" in result


def test_moved_renderers_still_render_and_escape():
    page = ui_pages.render_review_page([])
    assert "No active decks meet the review floor yet." in page and "<nav>" in page
    evil = [{"concept_id": "<script>", "cores": "x", "status": "untested",
             "reason": "", "rating": None, "games_played": None}]
    assert "<script>" not in ui_pages.render_search_page(evil, 1, "<b>", "untested")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_factory_ui_pages.py -v` — Expected: FAIL — `ModuleNotFoundError: ptcg.factory.ui_pages`.

- [ ] **Step 3: Implement**

`ui_pages.py`: module docstring ("pure rendering, no DB access, no side effects"); `NAV_HTML = '<nav><a href="/">Review</a> · <a href="/pool">Pool</a> · <a href="/search">Search</a> · <a href="/status">Status</a></nav>'`; a `_shell(title, body)` helper producing the `<!doctype html>...<nav>...` wrapper; MOVE `_card_names`/`_card_id_to_name` and the two existing renderer bodies here (rename to `render_review_page`/`render_status_page`, wrap bodies in `_shell` so they gain the nav; keep their inner HTML otherwise unchanged). New renderers per the tests: pool rows each carry a `<form method="post" action="/concept-decision">` with hidden `concept_id`, `from=pool`, and a `remove` button; anchor row is a pinned `<li>` with the badge and no form; search rows carry hidden `concept_id`, `from=search`, `q`, `status` fields and a `remove` OR `restore` button by row status; bulk button renders only on `untested`/`culled` tabs as a form to `/bulk-confirm` with hidden `q`/`tab`/`action`; confirm page renders the preview count and a form to `/bulk-decision` with the same hidden fields; result page states "Acted on N concepts (action, q, tab)" with a link back to `/search?...`. Every dynamic value passes through `html.escape` (match the existing `_render_page` conventions at old `ui_server.py:135-161`).

In `ui_server.py`: delete the moved bodies; add
```python
from ptcg.factory.ui_pages import (  # back-compat aliases (tests/import sweep)
    render_review_page as _render_page,
    render_status_page as _render_status_page,
)
```
and keep `bottom_ten`/`apply_decision`/`status_snapshot` where they are. Run the coverage-sweep grep from the Interfaces block and alias anything else it surfaces.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_factory_ui_pages.py tests/test_factory_ui_server.py -v` — Expected: all PASS (moved renderers keep existing HTTP tests green via aliases).

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/factory/ui_pages.py src/ptcg/factory/ui_server.py tests/test_factory_ui_pages.py
git commit -m "feat: ui_pages rendering module — nav, pool/search/bulk pages; move existing renderers"
```

---

### Task 6: `ui_server.py` routing — /pool, /search, /concept-decision, /bulk-confirm, /bulk-decision

**Files:**
- Modify: `src/ptcg/factory/ui_server.py` (`do_GET` ~line 388, `do_POST` ~line 406)
- Test: `tests/test_factory_ui_server.py` (append HTTP-level tests, reusing the file's existing `HTTPServer` + urllib pattern)

**Interfaces:**
- Consumes: `ui_actions.search_concepts/pool_decks/anchor_concept/champion_concept_id/apply_concept_decision/apply_bulk_decision/FinalistProtectedError`; `ui_pages.render_*`.
- Produces routes:
  - `GET /pool` → 200 pool page
  - `GET /search?q=<text>&status=<tab>` → 200 search page (defaults `q=""`, `status=untested`; unknown tab → 400)
  - `POST /concept-decision` (`concept_id`, `action` in remove/restore, `from` + optional `q`/`status`) → 303 back to `/pool` or `/search?q=..&status=..`; `FinalistProtectedError` → 409; `ValueError` → 400
  - `POST /bulk-confirm` (`q`,`tab`,`action`) → 200 interstitial (preview count via `search_concepts` total); invalid combos → 400
  - `POST /bulk-decision` (`q`,`tab`,`action`) → 200 result page with ACTUAL count; `ValueError` → 400

- [ ] **Step 1: Write the failing tests (append; follow the file's existing live-server fixture style — start `HTTPServer(("127.0.0.1", 0), ui_server.make_app(conn_factory))` on a thread, request with urllib, `redirect` disabled where asserting 303)**

Cases (each its own test, seeded via the existing `_seed_concept` helper):
```python
def test_pool_route_lists_active_and_protects_anchor(...)      # GET /pool: 200, active rows present, anchor badge, no anchor form
def test_search_route_defaults_and_tab(...)                    # GET /search?q=abom: 200; &status=bogus -> 400
def test_concept_decision_remove_redirects_to_origin(...)      # POST remove from=search&q=a&status=untested -> 303 Location /search?q=a&status=untested; DB flipped
def test_concept_decision_finalist_409(...)                    # POST remove on finalist concept -> 409; status unchanged
def test_concept_decision_unknown_concept_400(...)             # -> 400
def test_bulk_confirm_shows_preview_count(...)                 # POST /bulk-confirm -> 200 body contains count and hidden fields
def test_bulk_decision_executes_and_reports_actual(...)        # POST /bulk-decision -> 200 body contains actual N; DB culled
def test_bulk_decision_rejects_bad_combo(...)                  # bulk-remove on culled tab -> 400
```
Write each fully in the test file (assert on both HTTP status and DB state — HTTP-only assertions are not receipts).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_factory_ui_server.py -v` — Expected: new tests FAIL with 404s (routes absent); pre-existing tests PASS.

- [ ] **Step 3: Implement routing**

In `do_GET`, add branches BEFORE the 404 fallback, using `urllib.parse.urlsplit(self.path)` so `/search?...` matches on path not raw string:

```python
parsed = urllib.parse.urlsplit(self.path)
...
elif parsed.path == "/pool":
    conn = conn_factory()
    try:
        page = ui_pages.render_pool_page(
            ui_actions.pool_decks(conn),
            ui_actions.anchor_concept(conn),
            ui_actions.champion_concept_id(conn),
        )
    finally:
        conn.close()
    self._send_html(200, page)
elif parsed.path == "/search":
    qs = urllib.parse.parse_qs(parsed.query)
    q = qs.get("q", [""])[0]
    tab = qs.get("status", ["untested"])[0]
    conn = conn_factory()
    try:
        rows, total = ui_actions.search_concepts(conn, q, tab)
    except ValueError as exc:
        self._send_html(400, f"<h1>400 Bad Request</h1><p>{html.escape(str(exc))}</p>")
        return
    finally:
        conn.close()
    self._send_html(200, ui_pages.render_search_page(rows, total, q, tab))
```

In `do_POST`, add `/concept-decision` (parse form; call `apply_concept_decision`; `except FinalistProtectedError` → 409 page; `except ValueError` → 400; else 303 with `Location` rebuilt from `from`/`q`/`status` via `urllib.parse.urlencode`), `/bulk-confirm` (validate the (tab, action) combo by consulting `ui_actions._BULK_TAB_FOR_ACTION`, get preview total via `search_concepts`, render interstitial), `/bulk-decision` (call `apply_bulk_decision`; render result page; `ValueError` → 400). NOTE the exception-ordering bug class in the existing handler: put `except` clauses BEFORE `finally: conn.close()` in the same try, matching the existing `/decision` structure at old lines 416-425. `FinalistProtectedError` must be caught before its parent `ValueError`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_factory_ui_server.py -v` — Expected: all PASS.
Check the 500-line rule: `wc -l src/ptcg/factory/ui_server.py` — must be < 500; if over, move remaining helpers to `ui_pages.py`/`ui_actions.py` (do NOT exceed the rule "because close").

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/factory/ui_server.py tests/test_factory_ui_server.py
git commit -m "feat: wire pool/search/concept-decision/bulk routes into the UI server"
```

---

### Task 7: Concurrency receipt — bulk-cull vs census promotion race

**Files:**
- Test: `tests/test_factory_ui_actions.py` (append)

**Interfaces:**
- Consumes: `tests/fixtures/race.py::race_two`, `ui_actions.apply_bulk_decision`, `census.promote_proven_singles`.

**Race semantics (spelled out so the assertion is NOT tautological — `.claude/rules/single-actor-worker-tests.md` recurrence #4):** seed ONE promotable concept (untested, coverage `games_played >= SCREENING_FLOOR`, decisive rating, core "Abomasnow"). Thread A bulk-culls `q="Abomasnow", tab="untested"`; thread B runs `promote_proven_singles`. Serialized outcomes are exactly two — **cull-first:** concept `culled`, promote's guarded UPDATE (`AND status='untested'`) executes but rowcount 0 → final `culled`, ONE decision row, TWO `UPDATE concepts` statement executions. **Promote-first:** concept `active`, bulk's match list (computed inside its txn) finds nothing → final `active`, ZERO decision rows, ONE `UPDATE concepts` execution. Any OTHER combination (e.g. `active` WITH a decision row = lost update; `culled` with zero decision rows) is a torn state the assertion must reject.

- [ ] **Step 1: Write the test**

```python
# --- interleaved receipt: bulk cull vs census promotion (INTERLEAVED) ---

import itertools
import threading

from ptcg.factory import census
from tests.fixtures.race import race_two


def test_bulk_cull_races_census_promotion(tmp_path):
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
        assert (bulk_n, n_decisions, update_count) == (1, 1, 2)
    elif status == "active":   # promotion won; bulk matched nothing
        assert (bulk_n, n_decisions, update_count) == (0, 0, 1)
    else:
        raise AssertionError(f"torn state: status={status!r}, decisions={n_decisions}")
```

- [ ] **Step 2: Run and verify GREEN (either branch may be taken)**

Run: `uv run pytest tests/test_factory_ui_actions.py::test_bulk_cull_races_census_promotion -v` — Expected: PASS. Run it 5 times (`-p no:randomly --count` not available: just loop the command 5x) and note in the report which branch(es) were observed; do NOT claim determinism in the docstring — state "either serialized outcome is legal; torn states rejected" (race-docstring-honesty caution, `.claude/rules/single-actor-worker-tests.md`).

- [ ] **Step 3: RED receipt against de-transactionalized code (one-off run, recorded in the task report, NOT committed)**

Temporarily monkeypatch `deckdb._write` in a scratch copy of the test (or via `monkeypatch.setattr` in a throwaway test variant) to a non-atomic executor:

```python
def _nonatomic_write(conn, fn):
    return fn(conn)  # autocommit per-statement; no BEGIN IMMEDIATE
```

Re-run the race test ~10x under this patch. Expected: at least one run FAILS (torn state or wrong counts) — paste the failing output into the task report as the RED receipt, then delete the scratch variant. If 10 runs never fail, widen the window (a `time.sleep(0.01)` between the bulk SELECT and its first `_decide_one`) for the RED demonstration only, and say so in the report.

- [ ] **Step 4: Commit**

```bash
git add tests/test_factory_ui_actions.py
git commit -m "test: interleaved receipt — bulk cull vs census promotion serialize without lost writes"
```

---

### Task 8: Docs + stale-docstring cleanup

**Files:**
- Modify: `scripts/factory_ui.py` (docstring lines ~12-17: still says "Never registered as a Windows Scheduled Task until T21… never the production tournament.db" — stale since the 2026-07-30 go-live; rewrite to describe the live `ptcg-factory-ui` task pointing at production `tournament.db`)
- Modify: `docs/factory-operations.md` (add a "Pool curation UI" subsection: the four pages, cull/restore semantics incl. the two-tier restore rule and the freeze-reopening caveat, bulk workflow, the finalist guard, and the fact that restore-to-untested routes through the census floor)
- Test: none (docs-only; `uv run pytest tests/test_factory_ui_server.py -v` as a no-regression spot check only if `scripts/factory_ui.py` is imported by tests — grep first: `rg -l "factory_ui" tests/`)

- [ ] **Step 1: Grep for consumers** (`rg -l "factory_ui" tests/ docs/`) — update every stale description found, not just the two named files.
- [ ] **Step 2: Make the edits** (keep `scripts/factory_ui.py` code untouched — docstring only).
- [ ] **Step 3: Commit**

```bash
git add scripts/factory_ui.py docs/factory-operations.md
git commit -m "docs: pool-curation UI operations; fix stale factory_ui docstring"
```

---

## Orchestrator sync points (not implementer tasks)

- After T4, after T6, and after T8: orchestrator runs the FULL suite (`uv run pytest`) + `uv run pyright src/ptcg/factory/ui_server.py src/ptcg/factory/ui_actions.py src/ptcg/factory/ui_pages.py src/ptcg/factory/deckdb.py`. Implementers never run the full suite (`.claude/rules/dispatch-test-run-directive.md`).
- Suite baseline at branch: 985 tests. Expect growth; zero regressions tolerated.

## Post-merge go-live rung (explicitly post-merge — do NOT gate Finish on it)

Pre-flight per `.claude/rules/golive-command-preflight.md`: re-verify the task name (`Get-ScheduledTask -TaskName ptcg-factory-ui`) before running.

1. Merge to master.
2. `Stop-ScheduledTask -TaskName ptcg-factory-ui; Start-ScheduledTask -TaskName ptcg-factory-ui` (non-elevated OK for stop/start; the 15-min watchdog would also eventually start it, but restart explicitly — a `Running` task is not proof it runs current code).
3. Rung-3 manual smoke at `http://127.0.0.1:8765/`: `/pool` lists ~11 active + pinned anchor; `/search?q=<core>` returns rows; cull ONE junk untested concept; restore it; verify both flips + 2 `decisions` rows via a read-only sqlite query; `/` and `/status` still serve.
4. Confirm the migration ran against production `tournament.db` (decisions table sql contains `'restore'`) — the runner/scheduler workers also call `init_db` and will migrate on THEIR next restart, but the UI restart alone is sufficient to migrate the shared DB.

## Self-review (done at plan-write)

- Spec coverage: pages/routes (T5/T6), semantics+guards (T3/T4), migration (T1, upgraded to rebuild after the CHECK-constraint landmark grep), search shapes (T2), race receipt (T7), docs (T8), go-live (rung above). Spec's "restore reaches any culled deck" honored in T3 (no scope restriction on restore targets).
- Placeholders: none — all test/impl code written out.
- Type consistency: `search_concepts` returns `(rows, total)` everywhere; `_decide_one` shared by T3/T4; `FinalistProtectedError ⊂ ValueError` with catch-order noted in T6.
- Known deliberate deviation from spec: transaction logic in `ui_actions.py` (recorded in header).
