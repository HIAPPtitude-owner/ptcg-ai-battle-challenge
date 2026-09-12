# Submission Strength Gate (anchor-confirm) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Crowned tournament champions must pass an absolute strength bar (WR ≥ 0.55 over 200 games vs the known-strength ladder anchor) before the submission scheduler uploads anything to Kaggle.

**Architecture:** A new `src/ptcg/factory/anchor.py` module owns the anchor-check stage: it registers the anchor deck in the tournament DB, enqueues a post-crown `purpose='anchor'` series (champion cell vs `HeuristicAgent` + mega-lucario-fighting), and resolves a persistent PASS/FAIL verdict in a new additive `anchor_checks` table. The runner resolves the anchor agent via an explicit special-case; the scheduler tick drives enqueue/resolve; the subscheduler blocks ALL upload paths (champion mark-trigger AND daily-floor probe) unless the current baseline's verdict is `pass`.

**Tech Stack:** Python 3.11, stdlib sqlite3 (WAL, `BEGIN IMMEDIATE` via `deckdb._write`), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-01-submission-strength-gate-design.md` (as corrected in the same commit as this plan — fixed-side convention, DELETE-based supersede, TEXT offspring_id, probe-path gating).

## Global Constraints

- Constants (single definition in `anchor.py`, exact values): `ANCHOR_VERSION = "anchor-heuristic-v0"`, `ANCHOR_GAMES = 200`, `ANCHOR_BAR = 0.55`, `ANCHOR_CONCEPT_ID = "anchor-mega-lucario-fighting"`, `ANCHOR_DECK_ID = "anchor-mega-lucario-fighting-d0"`.
- Verdict boundary: `wr >= ANCHOR_BAR` passes; 110 wins / 200 games = 0.550 → **pass**. Draws (`winner == 2`) count as champion losses (champion is ALWAYS `agent_version_a`; only `winner == 0` is a champion win).
- Every new step function runs its read-decide-act sequence inside ONE `deckdb._write` (`BEGIN IMMEDIATE`) transaction (`.claude/rules/single-actor-worker-tests.md`, toctou-guard-in-step-functions). Never call helpers that open their own `_write` from inside `_apply` — inline their statements (established `resolve_confirm`/`resolve_crown` convention).
- Schema changes are additive only: `anchor_checks` via `CREATE TABLE IF NOT EXISTS`, added BOTH to `deckdb._DDL_STATEMENTS` and ensured by `anchor._ensure_schema` (live production DB predates the DDL addition; nothing re-runs `init_db` on it).
- All new subscheduler skip paths keep the terminal-marker invariant: interior `log(...)` lines only; the `submit-scheduler:` terminal line stays emitted by the `maybe_submit` wrapper on every exit (unchanged).
- Ladder identity files untouched: `src/ptcg/submission_main.py`, `src/ptcg/agents/current.py` (T7 verifies empty diff). `anchor.py` IMPORTS from `current.py` (read-only dependency; that is the point — the anchor IS the ladder identity).
- Fast suite green after every task: `uv run pytest -x -q` (repo path contains a space — always quote paths in shell commands).
- SUBMIT_HOLD stays ON for the entire slice. Do not delete `experiments/factory/SUBMIT_HOLD`; do not restart any scheduled task mid-slice. The working tree is live for the watch loop — subscheduler edits are inert only because the hold short-circuits in the caller.
- Do not touch, clean, or commit the live factory drift files: `experiments/factory/**`, `src/ptcg/search/value_net_weights_c-*.json`, `.claude/plan.md` (plan-manager owns it). Stage by explicit path only.

## Verified landmarks (read at HEAD, 2026-08-01)

| Landmark | Location | Fact |
|---|---|---|
| `_write` txn wrapper | `deckdb.py:43-59` | `BEGIN IMMEDIATE`/COMMIT/ROLLBACK; `fn` must not nest `_write` |
| `games` DDL | `deckdb.py:77-86` | `purpose` has NO CHECK constraint ('anchor' needs no schema change); `status` CHECK is `('pending','claimed','done')` — no 'cancelled' (supersede must DELETE) |
| winner semantics | `deckdb.py:6-7` | 0=deck_a agent won, 1=deck_b, 2=draw |
| claim ordering | `deckdb.py:178` | `ORDER BY priority DESC, id` — priority 1.0 jumps the queue |
| `baselines` DDL | `deckdb.py:110-111` | `offspring_id` is TEXT (offspring.id TEXT PK), NULL for founding |
| step-function template | `loop.py:383-483` (`enqueue_confirm_series`), `loop.py:486-542` (`resolve_confirm`) | guard+count+insert+update in one `_write`; results query shape `loop.py:377-380` |
| tick wiring point | `loop_scheduler.py:314-315` | `loop.resolve_crown(conn)` then `loop.enqueue_crown_round_robin(conn)` |
| runner resolution | `runner_pool.py:149-216` | order: in-process map → offspring → founding meta; raises `UnresolvableAgentVersionError`; successful resolutions cached into `agent_configs` |
| heuristic agent support | `evaluate.py:55` (`build_agent`), used at `runner_pool.py:274-277` | `agent_kind == "heuristic"` → `HeuristicAgent()`; `play_match(agent_a, agent_b, deck_a, deck_b)` |
| gate hook | `subscheduler.py:358-371` | baseline loaded at 358; mark-trigger branch at 371; daily-floor probe branch at 383-408 (ALSO uploads baseline's agent — must be gated) |
| anchor identity | `agents/current.py:12-13` | `CURRENT_DECK_PATH = Path("src/ptcg/decks/candidates/mega-lucario-fighting.csv")` |
| test conventions | `tests/test_factory_subscheduler.py:26-41` | `_fake_build`/`_fake_verify`/`_seed_founding` fixture helpers; `FakeKaggleClient`; `threading` already used for concurrency tests |

---

### Task 1: `anchor.py` — constants, schema, anchor-deck registration

**Files:**
- Create: `src/ptcg/factory/anchor.py`
- Modify: `src/ptcg/factory/deckdb.py` (append one DDL string to `_DDL_STATEMENTS`; update module docstring purpose set to `{screening,match,confirm,crown,anchor}`)
- Test: `tests/test_factory_anchor.py` (new)

**Interfaces:**
- Consumes: `deckdb.connect`, `deckdb._write`, `ptcg.agents.current.CURRENT_DECK_PATH`
- Produces: `ANCHOR_VERSION: str`, `ANCHOR_GAMES: int`, `ANCHOR_BAR: float`, `ANCHOR_CONCEPT_ID: str`, `ANCHOR_DECK_ID: str`, `_ensure_schema(conn) -> None`, `ensure_anchor_deck(conn) -> None` (both idempotent). Later tasks import these exact names.

- [ ] **Step 1: Write failing tests**

```python
"""Tests for the anchor-check stage (submission strength gate).

Spec: docs/superpowers/specs/2026-08-01-submission-strength-gate-design.md
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from ptcg.factory import anchor, deckdb, loop_state


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
    assert anchor.ANCHOR_BAR == 0.55


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


def test_ensure_anchor_deck_registers_concept_deck_coverage(tmp_path):
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
    cov = conn.execute(
        "SELECT games_played FROM coverage WHERE concept_id=?",
        (anchor.ANCHOR_CONCEPT_ID,)).fetchone()
    assert cov is not None and cov["games_played"] == 0


def test_ensure_anchor_deck_idempotent(tmp_path):
    conn = _connect(tmp_path)
    anchor.ensure_anchor_deck(conn)
    anchor.ensure_anchor_deck(conn)
    n = conn.execute("SELECT COUNT(*) FROM decks WHERE concept_id=?",
                     (anchor.ANCHOR_CONCEPT_ID,)).fetchone()[0]
    assert n == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest "tests/test_factory_anchor.py" -q`
Expected: FAIL — `ModuleNotFoundError`/`ImportError` for `ptcg.factory.anchor` (and the virgin-DB test fails on the missing DDL).

- [ ] **Step 3: Implement**

Append to `deckdb._DDL_STATEMENTS` (before the final `meta` entry or after it — order is irrelevant, all `IF NOT EXISTS`):

```python
    """
    CREATE TABLE IF NOT EXISTS anchor_checks(
      version TEXT PRIMARY KEY,
      offspring_id TEXT,
      deck_id TEXT NOT NULL,
      games_planned INTEGER NOT NULL,
      games_done INTEGER NOT NULL DEFAULT 0,
      wins INTEGER NOT NULL DEFAULT 0,
      wr REAL,
      verdict TEXT NOT NULL DEFAULT 'pending'
        CHECK(verdict IN ('pending','pass','fail')),
      created_at TEXT NOT NULL,
      resolved_at TEXT)
    """,
```

Also update `deckdb.py`'s module docstring line 8: `` `{screening,match,confirm,crown,anchor}` ``.

Create `src/ptcg/factory/anchor.py`:

```python
"""Anchor-check stage: absolute strength evidence for crowned champions
(submission strength gate, spec 2026-08-01).

CROWN is purely RELATIVE (best aggregate win% among sibling survivors), so a
uniformly weak cohort still crowns a "champion" -- that is how the 325.1 junk
submission (Kaggle ref 55125891) shipped. This module wires the factory's
known-strength anchor -- the live ladder identity, `HeuristicAgent` +
mega-lucario-fighting (`ptcg.agents.current`) -- into the tournament DB as a
post-crown series and persists an absolute pass/fail verdict that
`subscheduler` reads before ANY upload (champion mark-trigger and daily-floor
probe alike -- both ship the current baseline's agent to the ladder).

The champion is ALWAYS `agent_version_a` in anchor games (MATCH/CONFIRM
fixed-side convention -- crown-style side mixing buys nothing here and would
complicate win counting). Draws (`winner == 2`) count as champion losses:
only `winner == 0` is a champion win, the conservative reading.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

from ptcg.agents.current import CURRENT_DECK_PATH
from ptcg.factory import deckdb, loop_state

ANCHOR_VERSION = "anchor-heuristic-v0"
ANCHOR_GAMES = 200
ANCHOR_BAR = 0.55
ANCHOR_CONCEPT_ID = "anchor-mega-lucario-fighting"
ANCHOR_DECK_ID = "anchor-mega-lucario-fighting-d0"

_ANCHOR_CHECKS_DDL = (
    "CREATE TABLE IF NOT EXISTS anchor_checks("
    "version TEXT PRIMARY KEY, offspring_id TEXT, deck_id TEXT NOT NULL, "
    "games_planned INTEGER NOT NULL, games_done INTEGER NOT NULL DEFAULT 0, "
    "wins INTEGER NOT NULL DEFAULT 0, wr REAL, "
    "verdict TEXT NOT NULL DEFAULT 'pending' "
    "CHECK(verdict IN ('pending','pass','fail')), "
    "created_at TEXT NOT NULL, resolved_at TEXT)"
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Additive upgrade for a LIVE pre-slice DB (production tournament.db
    predates the `deckdb._DDL_STATEMENTS` addition and nothing re-runs
    `init_db` on it). Idempotent; virgin DBs get the table from `init_db`
    and this is a no-op. Autocommit single statement -- no `_write` needed."""
    conn.execute(_ANCHOR_CHECKS_DDL)


def ensure_anchor_deck(conn: sqlite3.Connection) -> None:
    """Register the anchor's concept/deck/coverage rows (idempotent).

    Status is `'finalist'`, NOT `'active'`: the daily-floor probe query
    (`subscheduler._BEST_ACTIVE_DECKS_QUERY`) and census culling both key on
    `'active'`, so the anchor deck can never be probed, culled, or bred.
    Cards come from the ladder identity CSV (`ptcg.agents.current`), one
    card id per line -- the exact deck the anchor plays on Kaggle."""
    _ensure_schema(conn)
    cards = [
        int(line)
        for line in CURRENT_DECK_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(cards) != 60:
        raise RuntimeError(
            f"ensure_anchor_deck: {CURRENT_DECK_PATH} has {len(cards)} cards, "
            "expected exactly 60 -- refusing to register a malformed anchor deck"
        )

    def _apply(c: sqlite3.Connection) -> None:
        c.execute(
            "INSERT OR IGNORE INTO concepts(id, cores, status, reason) "
            "VALUES(?, '[\"anchor\"]', 'finalist', "
            "'ladder-identity anchor deck (strength gate); never cull')",
            (ANCHOR_CONCEPT_ID,),
        )
        c.execute(
            "INSERT OR IGNORE INTO decks(id, concept_id, cards, shell_variant) "
            "VALUES(?, ?, ?, 0)",
            (ANCHOR_DECK_ID, ANCHOR_CONCEPT_ID, json.dumps(cards)),
        )
        c.execute(
            "INSERT OR IGNORE INTO coverage(concept_id, games_played, "
            "distinct_opponents) VALUES(?, 0, 0)",
            (ANCHOR_CONCEPT_ID,),
        )

    deckdb._write(conn, _apply)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest "tests/test_factory_anchor.py" -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Verification extras**
  - Grep `src/ptcg/factory/tournament.py` for the `refresh_ratings` query and confirm anchor-purpose games cannot corrupt it (it fits BT on done games; a new deck node with games is legitimate data — report what you find, one line, in your report).
  - Run: `uv run pytest -x -q` — full fast suite green.

- [ ] **Step 6: Commit**

```bash
git add "src/ptcg/factory/anchor.py" "src/ptcg/factory/deckdb.py" "tests/test_factory_anchor.py"
git commit -m "feat: anchor-check schema + anchor deck registration (strength gate T1)"
```

---

### Task 2: `enqueue_anchor_series` — backfill, top-up, supersede

**Files:**
- Modify: `src/ptcg/factory/anchor.py`
- Test: `tests/test_factory_anchor.py`

**Interfaces:**
- Consumes: T1's constants/`ensure_anchor_deck`; `loop_state.current_baseline(c)` (safe to call inside `_apply` — pure read, no nested `_write`).
- Produces: `enqueue_anchor_series(conn) -> int` (games enqueued this call). Called every scheduler tick (T6); covers fresh-crown AND go-live backfill with one code path: "ensure the CURRENT baseline has a check row and a full series."

- [ ] **Step 1: Write failing tests** (append to `tests/test_factory_anchor.py`)

```python
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


def test_enqueue_supersede_deletes_stale_pending_only(tmp_path):
    conn = _connect(tmp_path)
    _seed_baseline(conn, version="v0.3")
    anchor.enqueue_anchor_series(conn)
    # one old game claimed, rest pending; then v0.4 is crowned
    def _advance(c):
        c.execute(
            "UPDATE games SET status='claimed' WHERE id = (SELECT id FROM games "
            "WHERE purpose='anchor' AND agent_version_a='v0.3' LIMIT 1)")
        c.execute(
            "INSERT INTO baselines(version, offspring_id, deck_id, crowned_at) "
            "VALUES('v0.4', NULL, 'dChamp', '2026-08-01T01:00:00+00:00')")
        c.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('baseline_version','v0.4')")
    deckdb._write(conn, _advance)
    anchor.enqueue_anchor_series(conn)
    stale = _anchor_games(conn, "v0.3")
    # claimed survives (runner will finish it harmlessly); pending deleted
    assert {g["status"] for g in stale} == {"claimed"}
    assert len(_anchor_games(conn, "v0.4")) == anchor.ANCHOR_GAMES


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest "tests/test_factory_anchor.py" -q`
Expected: FAIL — `AttributeError: module 'ptcg.factory.anchor' has no attribute 'enqueue_anchor_series'`.

- [ ] **Step 3: Implement** (append to `anchor.py`)

```python
def enqueue_anchor_series(conn: sqlite3.Connection) -> int:
    """Ensure the CURRENT baseline has an anchor_checks row and a full
    `ANCHOR_GAMES` series enqueued; returns games enqueued this call.

    One code path covers three cases: fresh crown (no check row yet),
    go-live backfill (current baseline predates the gate), and resumable
    top-up after a partial loss (mirrors `enqueue_confirm_series`'s
    count-the-shortfall design). No-ops when no baseline is founded or the
    current baseline's verdict is already resolved. Also DELETEs stale
    `pending` anchor games of superseded versions -- `games.status` has no
    'cancelled' value (CHECK constraint, deckdb.py:83), and a never-claimed
    pending row is pure queue waste; `claimed` stale games are left to
    finish harmlessly. Champion is ALWAYS `agent_version_a`; priority 1.0
    jumps the claim queue (`ORDER BY priority DESC`) because the verdict
    blocks uploads.

    Race-safe (Pattern SQLITE-TXN): guard reads, supersede DELETE, check-row
    INSERT, and every shortfall INSERT run inside ONE `deckdb._write`
    transaction; a concurrent caller's count read runs strictly after the
    winner's COMMIT and computes shortfall 0 (mirrors
    `enqueue_confirm_series`, verified by
    `test_enqueue_survives_concurrent_calls`)."""
    _ensure_schema(conn)
    ensure_anchor_deck(conn)

    def _apply(c: sqlite3.Connection) -> int:
        baseline = loop_state.current_baseline(c)
        if baseline is None:
            return 0
        version = baseline["version"]
        c.execute(
            "DELETE FROM games WHERE purpose='anchor' AND status='pending' "
            "AND agent_version_a != ?",
            (version,),
        )
        row = c.execute(
            "SELECT verdict FROM anchor_checks WHERE version=?", (version,)
        ).fetchone()
        if row is not None and row["verdict"] != "pending":
            return 0
        if row is None:
            c.execute(
                "INSERT INTO anchor_checks(version, offspring_id, deck_id, "
                "games_planned, created_at) VALUES(?,?,?,?,?)",
                (version, baseline["offspring_id"], baseline["deck_id"],
                 ANCHOR_GAMES, _now()),
            )
        existing = c.execute(
            "SELECT COUNT(*) FROM games WHERE purpose='anchor' "
            "AND agent_version_a=?",
            (version,),
        ).fetchone()[0]
        remaining = max(0, ANCHOR_GAMES - existing)
        for _ in range(remaining):
            c.execute(
                "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, "
                "agent_version_b, purpose, priority, status) "
                "VALUES (?, ?, ?, ?, 'anchor', 1.0, 'pending')",
                (baseline["deck_id"], ANCHOR_DECK_ID, version, ANCHOR_VERSION),
            )
        return remaining

    return deckdb._write(conn, _apply)
```

Note: `ensure_anchor_deck` runs OUTSIDE `_apply` (it opens its own `_write`; `_write` cannot nest — deckdb.py:43-59).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest "tests/test_factory_anchor.py" -q` → PASS. Then `uv run pytest -x -q` → full fast suite green.

- [ ] **Step 5: Commit**

```bash
git add "src/ptcg/factory/anchor.py" "tests/test_factory_anchor.py"
git commit -m "feat: enqueue_anchor_series with backfill/top-up/supersede (strength gate T2)"
```

---

### Task 3: `resolve_anchor_check` + `anchor_status` read helper

**Files:**
- Modify: `src/ptcg/factory/anchor.py`
- Test: `tests/test_factory_anchor.py`

**Interfaces:**
- Consumes: T1/T2.
- Produces: `resolve_anchor_check(conn) -> str | None` (verdict resolved this call, else None); `anchor_status(conn, version) -> tuple[str, int, int, float | None]` — `(verdict, done_games, planned, wr)` where verdict ∈ `{'absent','pending','pass','fail'}`; for resolved rows `done_games`/`wr` come from the persisted row, for pending rows `done_games` is the live count and `wr` is None. T5 (subscheduler) consumes `anchor_status` with exactly this signature.

- [ ] **Step 1: Write failing tests** (append; helper plays out games directly)

```python
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


def test_resolve_boundary_110_of_200_passes(tmp_path):
    conn = _connect(tmp_path)
    _seed_baseline(conn)
    anchor.enqueue_anchor_series(conn)
    _finish_games(conn, "v0.3", wins_for_champion=110, losses=90)
    assert anchor.resolve_anchor_check(conn) == "pass"
    verdict, done, planned, wr = anchor.anchor_status(conn, "v0.3")
    assert verdict == "pass" and done == 200 and wr == pytest.approx(0.55)


def test_resolve_fail_below_bar_draws_count_as_losses(tmp_path):
    conn = _connect(tmp_path)
    _seed_baseline(conn)
    anchor.enqueue_anchor_series(conn)
    # 109 wins + 1 draw + 90 losses: wr = 109/200 = 0.545 < 0.55 -> fail
    _finish_games(conn, "v0.3", wins_for_champion=109, losses=90, draws=1)
    assert anchor.resolve_anchor_check(conn) == "fail"
    verdict, _done, _planned, wr = anchor.anchor_status(conn, "v0.3")
    assert verdict == "fail" and wr == pytest.approx(0.545)


def test_resolve_settled_verdict_never_flips(tmp_path):
    conn = _connect(tmp_path)
    _seed_baseline(conn)
    anchor.enqueue_anchor_series(conn)
    _finish_games(conn, "v0.3", wins_for_champion=110, losses=90)
    assert anchor.resolve_anchor_check(conn) == "pass"
    assert anchor.resolve_anchor_check(conn) is None  # already settled -> no-op


def test_anchor_status_absent(tmp_path):
    conn = _connect(tmp_path)
    assert anchor.anchor_status(conn, "v9.9") == ("absent", 0, anchor.ANCHOR_GAMES, None)


def test_resolve_survives_concurrent_calls(tmp_path):
    db = tmp_path / "t.db"
    conn = deckdb.connect(db)
    deckdb.init_db(conn)
    _seed_baseline(conn)
    anchor.enqueue_anchor_series(conn)
    _finish_games(conn, "v0.3", wins_for_champion=110, losses=90)
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest "tests/test_factory_anchor.py" -q`
Expected: FAIL — missing `resolve_anchor_check`/`anchor_status`.

- [ ] **Step 3: Implement** (append to `anchor.py`)

```python
_ANCHOR_RESULTS_QUERY = (
    "SELECT SUM(CASE WHEN winner = 0 THEN 1 ELSE 0 END) AS wins, "
    "COUNT(*) AS n FROM games "
    "WHERE purpose='anchor' AND status='done' AND agent_version_a = ?"
)


def resolve_anchor_check(conn: sqlite3.Connection) -> str | None:
    """Resolve any pending anchor check whose series is fully done
    (>= games_planned completed games): wr >= ANCHOR_BAR -> 'pass', else
    'fail' (boundary: 110/200 = 0.550 -> pass). Draws already count as
    losses via the winner=0-only wins aggregate. Returns the verdict
    resolved this call (the current baseline's, in practice), else None.

    Race-safe (Pattern SQLITE-TXN): the pending-row read, per-version
    aggregate, and verdict UPDATE run in ONE `deckdb._write` transaction;
    the UPDATE is guarded `AND verdict='pending'` so a concurrent resolver
    that lost the lock race observes the settled row and no-ops
    (`test_resolve_survives_concurrent_calls`). A settled verdict is never
    recomputed or flipped."""
    _ensure_schema(conn)

    def _apply(c: sqlite3.Connection) -> str | None:
        resolved: str | None = None
        pending = c.execute(
            "SELECT version, games_planned FROM anchor_checks "
            "WHERE verdict='pending'"
        ).fetchall()
        for row in pending:
            agg = c.execute(_ANCHOR_RESULTS_QUERY, (row["version"],)).fetchone()
            n = agg["n"] or 0
            if n < row["games_planned"]:
                continue
            wins = agg["wins"] or 0
            wr = wins / n
            verdict = "pass" if wr >= ANCHOR_BAR else "fail"
            cur = c.execute(
                "UPDATE anchor_checks SET games_done=?, wins=?, wr=?, "
                "verdict=?, resolved_at=? WHERE version=? AND verdict='pending'",
                (n, wins, wr, verdict, _now(), row["version"]),
            )
            if cur.rowcount == 1:
                resolved = verdict
        return resolved

    return deckdb._write(conn, _apply)


def anchor_status(
    conn: sqlite3.Connection, version: str
) -> tuple[str, int, int, float | None]:
    """Read-only gate evidence for `version`: (verdict, done_games, planned,
    wr). verdict is 'absent' when no check row exists (treated as pending by
    the gate -- the scheduler's backfill will create it). All four evidence
    shapes are first-class (`provenance-shaped-optional-fields` rule)."""
    _ensure_schema(conn)
    row = conn.execute(
        "SELECT games_planned, games_done, wr, verdict FROM anchor_checks "
        "WHERE version=?",
        (version,),
    ).fetchone()
    if row is None:
        return ("absent", 0, ANCHOR_GAMES, None)
    if row["verdict"] != "pending":
        return (row["verdict"], row["games_done"], row["games_planned"], row["wr"])
    done = conn.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='anchor' AND status='done' "
        "AND agent_version_a=?",
        (version,),
    ).fetchone()[0]
    return ("pending", done, row["games_planned"], None)
```

- [ ] **Step 4: Run tests** → PASS; then `uv run pytest -x -q` → green.

- [ ] **Step 5: Commit**

```bash
git add "src/ptcg/factory/anchor.py" "tests/test_factory_anchor.py"
git commit -m "feat: resolve_anchor_check verdict + anchor_status reader (strength gate T3)"
```

---

### Task 4: Runner anchor-agent resolution

**Files:**
- Modify: `src/ptcg/factory/runner_pool.py` (`_resolve_agent_entry`, `runner_pool.py:149-216`)
- Test: `tests/test_factory_runner_pool.py` (append)

**Interfaces:**
- Consumes: `anchor.ANCHOR_VERSION` (import `from ptcg.factory import anchor` — no cycle: anchor does not import runner_pool).
- Produces: `_resolve_agent_entry(conn, ANCHOR_VERSION, configs)` → `{"agent_kind": "heuristic", "agent_config": {}}`, resolved BEFORE any DB lookup and cached like other resolutions. `build_agent` (`evaluate.py:55`) already maps `"heuristic"` → `HeuristicAgent()` — no play-path changes.

- [ ] **Step 1: Write failing tests** (append to `tests/test_factory_runner_pool.py`, following its existing fixture conventions — read the file's existing `_resolve_agent_entry` tests first and mirror their setup):

```python
def test_resolve_anchor_version_no_db_row_needed(tmp_path):
    """ANCHOR_VERSION resolves from the named special-case, never the DB --
    the 22634c5 KeyError crash class (a version resolvable nowhere) must not
    apply to anchor games."""
    from ptcg.factory import anchor, deckdb, runner_pool
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)  # NO offspring/baseline rows at all
    configs: dict = {}
    entry = runner_pool._resolve_agent_entry(conn, anchor.ANCHOR_VERSION, configs)
    assert entry == {"agent_kind": "heuristic", "agent_config": {}}
    assert configs[anchor.ANCHOR_VERSION] == entry  # cached like other resolutions


def test_resolve_unknown_version_still_raises(tmp_path):
    """The special-case must not weaken the loud-failure contract for
    genuinely unknown versions."""
    from ptcg.factory import deckdb, runner_pool
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    with pytest.raises(runner_pool.UnresolvableAgentVersionError):
        runner_pool._resolve_agent_entry(conn, "v9.9-nonexistent", {})


def test_build_agent_supports_anchor_entry(tmp_path):
    """End-to-end: an anchor Candidate builds a real HeuristicAgent via the
    runner's own build path (evaluate.build_agent heuristic branch)."""
    from ptcg.agents.heuristic import HeuristicAgent
    from ptcg.factory import anchor, deckdb, runner_pool
    from ptcg.factory.evaluate import build_agent
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    cand = runner_pool._build_candidate(conn, anchor.ANCHOR_VERSION, {}, "dAnchor")
    agent = build_agent(cand, [3] * 60)
    assert isinstance(agent, HeuristicAgent)
```

(Adjust `build_agent`'s exact call signature to what `evaluate.py` actually defines — grep it first; if it takes different arguments, keep the assertion `isinstance(agent, HeuristicAgent)` and adapt the call. Report any divergence.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest "tests/test_factory_runner_pool.py" -q`
Expected: first test FAILS with `UnresolvableAgentVersionError` (anchor version resolvable nowhere today).

- [ ] **Step 3: Implement** — in `_resolve_agent_entry`, immediately after the in-process-map check (the `entry = agent_configs.get(version)` / early-return block at `runner_pool.py:173-175`), insert:

```python
    if version == anchor.ANCHOR_VERSION:
        # Strength-gate anchor: the ladder identity (HeuristicAgent). Named
        # special-case BEFORE any DB lookup -- anchor has no offspring row by
        # design, and must never ride the unresolvable-version dead-letter
        # path (22634c5 crash class).
        entry = {"agent_kind": "heuristic", "agent_config": {}}
        agent_configs[version] = entry
        return entry
```

Add the import at the top of `runner_pool.py`: `from ptcg.factory import anchor`.

- [ ] **Step 4: Run tests** → PASS; then `uv run pytest -x -q` → green.

- [ ] **Step 5: Commit**

```bash
git add "src/ptcg/factory/runner_pool.py" "tests/test_factory_runner_pool.py"
git commit -m "feat: runner resolves anchor-heuristic-v0 via named special-case (strength gate T4)"
```

---

### Task 5: Subscheduler gate — block ALL uploads without a pass verdict

**Files:**
- Modify: `src/ptcg/factory/subscheduler.py` (`_decide_and_submit`, insert after the baseline-load block at `subscheduler.py:358-361`)
- Test: `tests/test_factory_subscheduler.py` (append)

**Interfaces:**
- Consumes: `anchor.anchor_status(conn, version)` (T3 signature), `anchor.ANCHOR_BAR`.
- Produces: gate behavior — when the current baseline's verdict is not `pass`, `_decide_and_submit` returns `[("GATE", "anchor-<verdict>", <detail>)]` BEFORE the mark/probe branches, uploading nothing and leaving state/counter untouched. The wrapper's terminal marker then reads `submit-scheduler: GATE=anchor-pending` (etc.) — invariant preserved with zero wrapper changes.

- [ ] **Step 1: Write failing tests** (append; reuse the file's existing `_seed_founding`, `_fake_build`, `_fake_verify`, `FakeKaggleClient`, and its established `maybe_submit` call shape — mirror an existing mark-trigger test's argument construction exactly):

```python
def _gate_pass(conn, version):
    from ptcg.factory import anchor
    anchor._ensure_schema(conn)
    conn.execute(
        "INSERT INTO anchor_checks(version, offspring_id, deck_id, games_planned, "
        "games_done, wins, wr, verdict, created_at, resolved_at) "
        "VALUES(?, NULL, 'dBase', 200, 200, 120, 0.6, 'pass', 't', 't')", (version,))


def _gate_fail(conn, version):
    from ptcg.factory import anchor
    anchor._ensure_schema(conn)
    conn.execute(
        "INSERT INTO anchor_checks(version, offspring_id, deck_id, games_planned, "
        "games_done, wins, wr, verdict, created_at, resolved_at) "
        "VALUES(?, NULL, 'dBase', 200, 200, 80, 0.4, 'fail', 't', 't')", (version,))


def _gate_pending(conn, version, done=57):
    from ptcg.factory import anchor
    anchor._ensure_schema(conn)
    conn.execute(
        "INSERT INTO anchor_checks(version, offspring_id, deck_id, games_planned, "
        "created_at) VALUES(?, NULL, 'dBase', 200, 't')", (version,))
    for _ in range(done):
        conn.execute(
            "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, "
            "agent_version_b, purpose, winner, status) "
            "VALUES('dBase','dA',?, 'anchor-heuristic-v0','anchor',0,'done')",
            (version,))


# Four evidence shapes (provenance-shaped-optional-fields rule):

def test_gate_absent_blocks_upload(tmp_path):
    # no anchor_checks row at all -> treated as pending, nothing uploads
    ...  # build conn/state/client exactly as the existing mark-trigger test does,
    # at a mark boundary where an upload WOULD fire, then:
    # result = subscheduler.maybe_submit(...)
    # assert result == [("GATE", "anchor-absent", ANY-detail)]
    # assert client.submissions == []  (no upload attempted)
    # assert state file untouched (last_mark_fired unchanged)


def test_gate_pending_blocks_with_progress_detail(tmp_path):
    ...  # _gate_pending(conn, "v0.1"); expect ("GATE", "anchor-pending", detail)
    # and detail contains "57/200"


def test_gate_fail_blocks_permanently(tmp_path):
    ...  # _gate_fail(conn, "v0.1"); expect ("GATE", "anchor-fail", ...) and no upload


def test_gate_pass_upload_proceeds(tmp_path):
    ...  # _gate_pass(conn, "v0.1"); expect the EXISTING mark-trigger flow to run
    # end-to-end: outcome "submitted", state updated -- copy the existing
    # happy-path test's assertions verbatim


def test_gate_blocks_daily_floor_probe_too(tmp_path):
    ...  # construct the existing daily-floor-probe test scenario (idle >= 24h,
    # mark NOT changed) but with _gate_pending -- expect ("GATE", ...) and
    # NO probe upload: the probe ships the baseline's agent and is gated
    # by the same verdict
```

The `...` bodies MUST be filled by copying the setup of the neighboring existing tests in this file (they already construct conn/state/client/now for both trigger paths) — the new tests differ ONLY in gate seeding and expected result. This is deliberate: the gate must be tested through the real `maybe_submit` entry point, not a synthetic harness. If the existing tests' setup does not match this sketch (e.g. different state-seeding helpers), follow the file, not this sketch, and report the divergence.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest "tests/test_factory_subscheduler.py" -q`
Expected: new tests FAIL — uploads proceed despite missing/failed verdicts (`("GATE", ...)` never returned).

- [ ] **Step 3: Implement** — in `_decide_and_submit`, immediately after the `baseline is None` early-return (`subscheduler.py:358-361`), insert:

```python
    verdict, done, planned, wr = anchor.anchor_status(conn, baseline["version"])
    if verdict != "pass":
        if verdict == "fail":
            detail = (f"{baseline['version']} wr={wr:.3f} bar={anchor.ANCHOR_BAR}"
                      if wr is not None else baseline["version"])
            log(f"anchor-gate: FAIL {detail} -- upload blocked")
        else:  # 'pending' or 'absent' -- series not yet resolved
            detail = f"{baseline['version']} {done}/{planned}"
            log(f"anchor-gate: awaiting verdict {detail}")
        return [("GATE", f"anchor-{verdict}", detail)]
```

Add the import: `from ptcg.factory import anchor` (alongside the existing `from ptcg.factory import ...` block at `subscheduler.py:50`). Placement note: this sits BEFORE both the mark-trigger branch and the daily-floor branch — one check gates every upload path, and a blocked tick never consumes the mark (`state` untouched), so the first tick after a later PASS still fires within its window. The `no_submit` dry-run path is gated identically (the check is a pure read).

- [ ] **Step 4: Run tests** → PASS; then `uv run pytest -x -q` → green.

- [ ] **Step 5: Commit**

```bash
git add "src/ptcg/factory/subscheduler.py" "tests/test_factory_subscheduler.py"
git commit -m "feat: anchor-gate blocks all subscheduler uploads without a pass verdict (strength gate T5)"
```

---

### Task 6: Scheduler tick wiring

**Files:**
- Modify: `src/ptcg/factory/loop_scheduler.py` (after `loop.enqueue_crown_round_robin(conn)` at `loop_scheduler.py:315`)
- Test: `tests/test_factory_loop_scheduler.py` (append; follow the file's existing tick-test conventions)

**Interfaces:**
- Consumes: `anchor.enqueue_anchor_series(conn)`, `anchor.resolve_anchor_check(conn)` (both idempotent/no-op-safe every tick).
- Produces: every scheduler tick drives the anchor stage; whatever per-tick summary/log the tick function already emits gains the anchor counters in its existing style.

- [ ] **Step 1: Write failing test**

```python
def test_tick_drives_anchor_stage(tmp_path):
    """A tick against a DB with a baseline but no anchor series must enqueue
    the full series; a later tick with the series done must resolve it."""
    from ptcg.factory import anchor
    ...  # construct conn + tick exactly as the file's existing tick tests do
    # tick once -> assert 200 purpose='anchor' games exist and
    #   anchor_checks has a 'pending' row for the current baseline
    # mark 110 wins / 90 losses done (reuse test_factory_anchor's
    #   _finish_games shape inline)
    # tick again -> assert anchor_checks verdict == 'pass'
```

Fill `...` by mirroring the existing scheduler tick tests in the file (they already build a seeded DB and invoke the tick body); report divergence if the file's conventions differ.

- [ ] **Step 2: Run to verify failure** — the tick never touches anchor tables today.

- [ ] **Step 3: Implement** — in the tick body right after `crown_enqueued = loop.enqueue_crown_round_robin(conn)` (`loop_scheduler.py:315`):

```python
    anchor_enqueued = anchor.enqueue_anchor_series(conn)
    anchor_resolved = anchor.resolve_anchor_check(conn)
```

Add `from ptcg.factory import anchor` to the imports, and fold `anchor_enqueued`/`anchor_resolved` into the tick's existing summary/log/return in the same style as `crowned`/`crown_enqueued` (read the surrounding lines; keep the shape the file already uses).

- [ ] **Step 4: Run tests** → PASS; then `uv run pytest -x -q` → green.

- [ ] **Step 5: Commit**

```bash
git add "src/ptcg/factory/loop_scheduler.py" "tests/test_factory_loop_scheduler.py"
git commit -m "feat: scheduler tick drives anchor enqueue/resolve (strength gate T6)"
```

---

### Task 7: Docs, full-suite regression, ladder-identity empty-diff

**Files:**
- Modify: `docs/factory-operations.md` (add a "Submission strength gate" subsection: what the gate is, the constants, how to read `anchor_checks`, what `anchor-gate:` log lines mean, and that relaxing the bar = editing `ANCHOR_BAR` in `anchor.py`)
- Modify: `docs/weekly-review-checklist.md` (add one step: "Check `anchor_checks` verdicts for recent champions — a string of FAILs is expected under the 0.55 bar; investigate only if a series is stuck pending far beyond ~1h of pool throughput")

**Steps:**

- [ ] **Step 1: Write both doc sections** (plain prose, cite `src/ptcg/factory/anchor.py` and the spec path).
- [ ] **Step 2: Full suite** — run: `uv run pytest -q` (NOT just the fast subset; slow marks excluded by default is fine). Expected: green, count strictly greater than the pre-slice 836.
- [ ] **Step 3: Ladder-identity empty-diff receipt** — run and paste output into the task report:

```bash
git diff master...HEAD -- "src/ptcg/submission_main.py" "src/ptcg/agents/current.py"
```

Expected: empty output.

- [ ] **Step 4: Commit**

```bash
git add "docs/factory-operations.md" "docs/weekly-review-checklist.md"
git commit -m "docs: strength-gate operations + weekly-review step (strength gate T7)"
```

---

### Post-merge go-live rung (pre-authorized; NOT a mid-slice task)

Executed by the orchestrator after Pass 2 APPROVED and merge to master, per spec §5:

1. Merge `feature/submission-strength-gate` → master.
2. `Stop-ScheduledTask` + `Start-ScheduledTask` for `ptcg-factory-scheduler` and `ptcg-factory-runner`; verify the next log/provenance stamp carries a post-merge commit (a `Running` state is not evidence — factory-resume-probe rule).
3. Verify backfill: `anchor_checks` row for the current baseline + 200 `purpose='anchor'` games (DB SELECT).
4. Wait for series completion (~35–60 min at observed pool throughput; anchor games run at priority 1.0). Verify the verdict row.
5. Delete `experiments/factory/SUBMIT_HOLD`.
6. Verify the next watch-loop firing's submit tick: terminal marker present AND either `GATE=anchor-fail`/`anchor-pending` (held-with-verdict — gate exercised, rung complete) or a PASS followed by a real upload verified end-to-end (counter, Kaggle listing).
