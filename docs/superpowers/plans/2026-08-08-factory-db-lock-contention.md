# Factory DB Lock Contention Fix + CROWN Rescope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the scheduler's ~56s `BEGIN IMMEDIATE` transaction from starving every other tournament-DB writer (runner pool crash-loop, ~95% throughput collapse), and rescope CROWN (top-K=8 by floor rating, 100 games/pair) so a champion can crown before the 2026-08-16 Kaggle deadline.

**Architecture:** All writes to `experiments/factory/tournament.db` go through `deckdb._write` (`BEGIN IMMEDIATE`, busy_timeout=30s, WAL). The scheduler tick (`loop_scheduler.loop_tick`, every 10s) calls `loop.enqueue_crown_round_robin`, which currently runs C(35,2)=595 per-pair `COUNT(*)` queries — each a full `SCAN games` (190,734 rows, no covering index) — inside ONE write transaction: 56.041s measured median, enqueuing 0. Fix = covering index + single GROUP BY + top-K cap + self-healing prune of stale pending games + retry resilience in the runner victims.

**Tech Stack:** Python 3.11 stdlib sqlite3, pytest, Windows Scheduled Tasks (PowerShell).

## Global Constraints

- Repo path contains a space — ALWAYS quote paths: `"C:\Users\Brad\Dev Folder\Product\ptcg-ai-battle-challenge-strategy"`.
- Every text write on this Windows-hosted project: explicit `encoding="utf-8"` (global CLAUDE.md Windows lesson — `write_text` without it truncates-then-crashes on non-ASCII).
- Git: stage by explicit path only (never `git add .`); conventional commits; on `index.lock` contention wait 2s and retry up to 3×.
- Implementers run ONLY their targeted test file (`uv run pytest tests/<file> -q`) — the orchestrator owns full-suite runs at sync points (`.claude/rules/dispatch-test-run-directive.md`, settled restructure). NEVER use the Monitor tool; NEVER `run_in_background` for tests.
- For any file previously read in your session, use `Grep -n . <path> -A 5000` instead of Read (PreToolUse:Read truncation hook).
- All read-decide-act sequences on the DB stay inside ONE `deckdb._write` transaction (Pattern SQLITE-TXN, `.claude/rules/single-actor-worker-tests.md`). Concurrency receipts use `tests/fixtures/race.py` (barrier + trace-callback counter + overlap assertion).
- The working tree IS production for fresh-process factory tasks (`.claude/rules/factory-resume-probe.md`). Task 1's inertness hold MUST be executed before any implementer writes code; nothing in Tasks 2–5 may be considered "not live yet" until Task 1's receipts are recorded.
- LIVE-DATA facts verified 2026-08-08 (pre-lock landmark greps + read-only DB queries): 35 eligible survivors, ALL with non-NULL `floor_checks.wr`; top-8 by wr = v0.13.30 (0.84), v0.13.4 (0.76), v0.13.19 (0.74), v0.13.1/v0.13.15/v0.13.2/v0.13.6 (0.72, id tie-break), v0.13.14 (0.68, id tie-break beats v0.13.18/v0.13.7/v0.13.8); top-8 pairs already hold 2,100 done games credited toward 28×100=2,800 (→ ~700 to play); 19,379 stale pending crown games to prune; `games.status` CHECK allows only `('pending','claimed','done')` — pruning is DELETE of pending rows (never claimed/done); PAUSE file is honored by the watch loop only (`factory_watch_once.py:117-119`), NOT by runner/scheduler.

---

### Task 1: Inertness hold (ops — ORCHESTRATOR-EXECUTED, before any code is written)

**Files:** none (filesystem flag + Scheduled Task state only)

**Why:** `ptcg-factory-runner` is currently a fresh-process-per-15-min respawner (its pool crashes every firing) — a half-written `runner_pool.py`/`deckdb.py` on disk WOULD execute in production mid-implementation. The watch loop imports `deckdb.init_db` every ~15 min, which executes `_DDL_STATEMENTS` (Task 2 adds an index there). The scheduler's live instance (running pre-merge code since 8/5 11:31) does not hot-reload and holds its `IgnoreNew` slot, but a crash mid-slice would respawn it with half-written code.

- [ ] **Step 1:** Create the PAUSE file: `New-Item -ItemType File "experiments\factory\PAUSE"` (stops watch-loop firings → `init_db` no longer runs against production).
- [ ] **Step 2:** `Disable-ScheduledTask -TaskName ptcg-factory-runner` and `Disable-ScheduledTask -TaskName ptcg-factory-scheduler` (Disable, not Stop — Stop is not durable under 15-min watchdog triggers, watchdog-respawn lesson 2026-07-30). Leave the RUNNING scheduler process alone (it keeps executing old in-memory code — unchanged production behavior). Leave `ptcg-factory-ui` and `ptcg-factory-continuous` registered/enabled (the PAUSE file makes the watch loop no-op).
- [ ] **Step 3:** Record receipts in plan.md: timestamp of PAUSE creation; next watch.log line shows `paused` marker; `Get-ScheduledTask ptcg-factory-runner, ptcg-factory-scheduler | Select TaskName, State` shows `Disabled` for both. Hold-set must precede first code write (timestamp-ordered receipt, counted-pair-protection template).

---

### Task 2: `enqueue_crown_round_robin` overhaul — covering index + single GROUP BY + self-healing prune

**Files:**
- Modify: `src/ptcg/factory/deckdb.py` (add index to `_DDL_STATEMENTS`, after the `ix_games_claim` line at :104)
- Modify: `src/ptcg/factory/loop.py:625-692` (`enqueue_crown_round_robin`)
- Test: `tests/test_factory_loop_crown.py` (existing file — ADD tests; per `.claude/rules/test-coverage-sweep.md`, if you REPLACE any existing test, diff old assertions vs new and report dropped coverage)

**Interfaces:**
- Consumes: `deckdb._write(conn, fn)`, `_ELIGIBLE_CROWN_SURVIVORS_QUERY` (unchanged in this task — Task 3 swaps the eligibility source).
- Produces: index `ix_games_crown_pair` on `games(purpose, agent_version_a, agent_version_b)`; rewritten `enqueue_crown_round_robin(conn, n_games_per_pair=CROWN_GAMES_PER_PAIR)` with identical signature/return (count of newly enqueued games) plus a new documented invariant: after any call, pending `crown` games exist ONLY for currently-eligible pairs and never exceed each pair's shortfall.

- [ ] **Step 1: Write the failing tests** (add to `tests/test_factory_loop_crown.py`, following that file's existing fixture conventions — grep its top for the db fixture in use):

```python
def _eqp_text(conn, sql, params=()):
    return " | ".join(str(r[3]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params))


def test_crown_pair_count_query_uses_covering_index(db):
    """Access-path guard (.claude/rules/single-actor-worker-tests.md
    access-path-analysis): the per-pair crown COUNT must never SCAN games
    inside the scheduler's BEGIN IMMEDIATE. RED before ix_games_crown_pair
    exists; GREEN after."""
    plan = _eqp_text(
        db,
        "SELECT COUNT(*) FROM games WHERE purpose = 'crown' "
        "AND agent_version_a = ? AND agent_version_b = ?",
        ("v0.13.1", "v0.13.2"),
    )
    assert "ix_games_crown_pair" in plan
    assert "SCAN games" not in plan


def test_enqueue_prunes_pending_for_stale_pairs(db):
    """A pending crown game whose pair is no longer eligible (offspring
    trashed) is DELETEd on the next enqueue call; done/claimed rows survive."""
    _mk_survivor(db, "v0.13.1")   # use the file's existing offspring-seeding
    _mk_survivor(db, "v0.13.2")   # helper; if named differently, adapt —
    _mk_survivor(db, "v0.13.9")   # report the actual helper name used.
    loop.enqueue_crown_round_robin(db, n_games_per_pair=4)
    # 3 pairs x 4 = 12 pending
    assert db.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='crown' AND status='pending'"
    ).fetchone()[0] == 12
    # v0.13.9 drops out of eligibility
    db.execute("UPDATE offspring SET status='trashed' WHERE id='v0.13.9'")
    loop.enqueue_crown_round_robin(db, n_games_per_pair=4)
    rows = db.execute(
        "SELECT DISTINCT agent_version_a, agent_version_b FROM games "
        "WHERE purpose='crown' AND status='pending'"
    ).fetchall()
    assert {(r[0], r[1]) for r in rows} == {("v0.13.1", "v0.13.2")}


def test_enqueue_prunes_excess_pending_when_target_shrinks(db):
    """Lowering n_games_per_pair deletes excess PENDING rows only; done rows
    are untouched and still credit toward the target."""
    _mk_survivor(db, "v0.13.1")
    _mk_survivor(db, "v0.13.2")
    loop.enqueue_crown_round_robin(db, n_games_per_pair=10)
    # simulate 3 played: claim+done 3 rows via the file's existing helpers
    ids = [r[0] for r in db.execute(
        "SELECT id FROM games WHERE purpose='crown' AND status='pending' LIMIT 3")]
    for gid in ids:
        db.execute("UPDATE games SET status='done', winner=0 WHERE id=?", (gid,))
    loop.enqueue_crown_round_robin(db, n_games_per_pair=5)
    pending = db.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='crown' AND status='pending'"
    ).fetchone()[0]
    done = db.execute(
        "SELECT COUNT(*) FROM games WHERE purpose='crown' AND status='done'"
    ).fetchone()[0]
    assert done == 3
    assert pending == 2  # 5 target - 3 done = 2 needed; excess 7 deleted
```

- [ ] **Step 2: Run to verify they fail:** `uv run pytest tests/test_factory_loop_crown.py -q -k "covering_index or prunes"` — expect FAIL (`ix_games_crown_pair` absent; prune not implemented).

- [ ] **Step 3: Add the index to `deckdb._DDL_STATEMENTS`** immediately after the `ix_games_claim` entry (`deckdb.py:104`), with a load-bearing comment mirroring the `ix_decks_concept` precedent:

```python
    # Load-bearing, NOT cosmetic (factory-db-lock-contention, 2026-08-08):
    # `loop.enqueue_crown_round_robin` + `loop.resolve_crown` +
    # `loop_scheduler._match_series_complete` filter games by
    # (purpose, agent_version_a, agent_version_b) INSIDE the scheduler's
    # BEGIN IMMEDIATE tick. Without this index each such query is a full
    # SCAN of games (190,734 rows on the live DB, ~94ms each); the crown
    # enqueue ran C(35,2)=595 of them in ONE transaction -- measured
    # 56.041s median lock hold vs the 30s busy_timeout of every other
    # writer, killing the 4-worker runner pool every firing (~95%
    # throughput collapse, 2026-08-07/08).
    "CREATE INDEX IF NOT EXISTS ix_games_crown_pair "
    "ON games(purpose, agent_version_a, agent_version_b)",
```

- [ ] **Step 4: Rewrite `enqueue_crown_round_robin`'s `_apply`** (keep signature, docstring updated to describe the new single-query + prune shape; keep the whole read-decide-act inside the ONE `deckdb._write` txn):

```python
    def _apply(c: sqlite3.Connection) -> int:
        eligible = c.execute(_ELIGIBLE_CROWN_SURVIVORS_QUERY).fetchall()
        if len(eligible) < 2:
            return 0
        for row in eligible:
            if row["deck_id"] is None:
                raise RuntimeError(
                    f"enqueue_crown_round_robin: eligible survivor {row['id']!r} "
                    "has no deck_id set -- run select_optimal_deck first"
                )

        # ONE indexed aggregate instead of C(K,2) per-pair COUNT scans
        # (the 56s lock-hold fix). Counts include pending+claimed+done.
        counts: dict[tuple[str, str], int] = {
            (r["agent_version_a"], r["agent_version_b"]): r["cnt"]
            for r in c.execute(
                "SELECT agent_version_a, agent_version_b, COUNT(*) AS cnt "
                "FROM games WHERE purpose = 'crown' "
                "GROUP BY agent_version_a, agent_version_b"
            )
        }
        pending_pairs = {
            (r["agent_version_a"], r["agent_version_b"])
            for r in c.execute(
                "SELECT DISTINCT agent_version_a, agent_version_b FROM games "
                "WHERE purpose = 'crown' AND status = 'pending'"
            )
        }

        pair_set: set[tuple[str, str]] = set()
        enqueued = 0
        for i in range(len(eligible)):
            a = eligible[i]
            for j in range(i + 1, len(eligible)):
                b = eligible[j]
                pair = (a["id"], b["id"])
                pair_set.add(pair)
                existing = counts.get(pair, 0)
                if existing > n_games_per_pair:
                    # target shrank (e.g. 200 -> 100): drop excess PENDING
                    # rows only -- done/claimed rows are results/in-flight.
                    c.execute(
                        "DELETE FROM games WHERE id IN ("
                        "SELECT id FROM games WHERE purpose = 'crown' "
                        "AND status = 'pending' AND agent_version_a = ? "
                        "AND agent_version_b = ? LIMIT ?)",
                        (pair[0], pair[1], existing - n_games_per_pair),
                    )
                for _ in range(max(0, n_games_per_pair - existing)):
                    c.execute(
                        "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, "
                        "agent_version_b, purpose, priority, status) "
                        "VALUES (?, ?, ?, ?, 'crown', 0.0, 'pending')",
                        (a["deck_id"], b["deck_id"], a["id"], b["id"]),
                    )
                    enqueued += 1

        # Self-healing prune: pending crown games for pairs no longer in the
        # eligible field (trashed/crowned offspring, or -- after Task 3 --
        # pairs outside the top-K) would otherwise sit claimable forever and
        # waste runner compute. done/claimed rows are never touched.
        for pair in pending_pairs - pair_set:
            c.execute(
                "DELETE FROM games WHERE purpose = 'crown' AND status = 'pending' "
                "AND agent_version_a = ? AND agent_version_b = ?",
                pair,
            )
        return enqueued
```

- [ ] **Step 5: Run the new tests + the file's existing crown tests:** `uv run pytest tests/test_factory_loop_crown.py -q` — ALL pass, including the pre-existing `test_enqueue_crown_round_robin_survives_concurrent_calls` race receipt (the rewrite must not weaken it — same single-txn shape).
- [ ] **Step 6: Commit:** `git add src/ptcg/factory/deckdb.py src/ptcg/factory/loop.py tests/test_factory_loop_crown.py` → `fix: single indexed aggregate + pending-prune in enqueue_crown_round_robin (56s lock hold -> ms)`

---

### Task 3: CROWN top-K cap + games/pair rescope

**Files:**
- Modify: `src/ptcg/factory/loop.py` (`CROWN_GAMES_PER_PAIR` at :584; new `CROWN_TOP_K`; new `_CROWN_FIELD_QUERY` + `crown_field()`; `enqueue_crown_round_robin` eligibility source; `resolve_crown` at :695-818)
- Test: `tests/test_factory_loop_crown.py`

**Interfaces:**
- Consumes: Task 2's rewritten `_apply` shape; `floor_checks(offspring_id PRIMARY KEY, ..., wr REAL)` (`deckdb.py:165-178`).
- Produces: `CROWN_TOP_K: int = 8`; `CROWN_GAMES_PER_PAIR = 100`; `crown_field(conn) -> list[sqlite3.Row]` (the capped, floor-wr-ranked field used by BOTH crown steps); `eligible_crown_survivors()` keeps its existing UNCAPPED contract (the scheduler and any other caller keep full-set semantics — verify with `Grep -n "eligible_crown_survivors" src/ scripts/` and report every call site in your task report).

**Semantics (Brad-approved 2026-08-08):** the round-robin field is the top `CROWN_TOP_K` eligible survivors ranked by `floor_checks.wr` DESC (anchor-grounded, comparable across generations — every floor series plays the same anchor opponent), NULL wr ranks last, ties broken by `id` ASC (repo convention). `resolve_crown` requires all C(K,2) field pairs `done >= CROWN_GAMES_PER_PAIR`, nominates the best aggregate win% WITHIN the field, and — deliberate cohort-clear decision — trashes every OTHER eligible survivor from the FULL uncapped eligible set (not just the field), preserving the generational reset so sub-top-8 survivors don't linger as zombies into the next generation. Accepted noise (document in docstring): pairs holding >100 done games from the 200/pair era weigh more in the aggregate win%; the anchor gate + pair-gate remain the real quality gates downstream.

- [ ] **Step 1: Write the failing tests:**

```python
def test_crown_field_ranks_by_floor_wr_capped(db):
    for k, wr in [("v0.13.1", 0.50), ("v0.13.2", 0.90), ("v0.13.3", None),
                  ("v0.13.4", 0.70), ("v0.13.5", 0.70)]:
        _mk_survivor(db, k)
        if wr is not None:
            _mk_floor_row(db, k, wr=wr)  # adapt to the file's floor-seeding helper
    field = loop.crown_field(db)
    ids = [r["id"] for r in field]
    # wr DESC, tie by id ASC, NULL last; capped at CROWN_TOP_K
    assert ids[:4] == ["v0.13.2", "v0.13.4", "v0.13.5", "v0.13.1"]
    assert ids[-1] == "v0.13.3"  # NULL wr ranks last (field of 5 < K=8)


def test_crown_field_caps_at_top_k(db):
    for i in range(12):
        oid = f"v0.13.{i + 1}"
        _mk_survivor(db, oid)
        _mk_floor_row(db, oid, wr=0.50 + i * 0.01)
    field = loop.crown_field(db)
    assert len(field) == loop.CROWN_TOP_K == 8
    assert field[0]["id"] == "v0.13.12"  # highest wr


def test_resolve_crown_trashes_full_cohort_not_just_field(db):
    """Cohort-clear: nomination happens within the top-K field, but ALL other
    eligible survivors (including sub-top-K) are trashed."""
    # seed K+2 survivors, complete the field's round-robin per the file's
    # existing done-game seeding helper, then:
    elect = loop.resolve_crown(db)
    assert elect is not None
    statuses = {r[0]: r[1] for r in db.execute(
        "SELECT id, status FROM offspring")}
    assert statuses[elect] == "survivor"
    assert all(s == "trashed" for oid, s in statuses.items() if oid != elect)


def test_crown_games_per_pair_is_100():
    assert loop.CROWN_GAMES_PER_PAIR == 100
```

- [ ] **Step 2: Run to verify failure:** `uv run pytest tests/test_factory_loop_crown.py -q -k "crown_field or full_cohort or per_pair_is_100"` — FAIL (`crown_field` undefined; constant still 200).

- [ ] **Step 3: Implement.** Constants + query near `_ELIGIBLE_CROWN_SURVIVORS_QUERY` (:594):

```python
CROWN_TOP_K = 8

_CROWN_FIELD_QUERY = (
    "SELECT o.id, o.deck_id FROM offspring o "
    "LEFT JOIN floor_checks f ON f.offspring_id = o.id "
    "WHERE o.status = 'survivor' "
    "AND o.id NOT IN (SELECT offspring_id FROM baselines WHERE offspring_id IS NOT NULL) "
    "AND o.id NOT IN (SELECT offspring_id FROM anchor_checks WHERE offspring_id IS NOT NULL) "
    "ORDER BY (f.wr IS NULL), f.wr DESC, o.id "
    f"LIMIT {CROWN_TOP_K}"
)


def crown_field(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """The CROWN round-robin field: top CROWN_TOP_K eligible survivors ranked
    by floor_checks.wr (anchor-grounded and therefore comparable across
    generations), NULL wr last, id-ASC tie-break. Permanent cap -- prevents
    the C(K,2) x table-scan lock-hold cliff of 2026-08-07/08 from recurring
    as the survivor pool grows. Read-only."""
    anchor._ensure_schema(conn)
    return conn.execute(_CROWN_FIELD_QUERY).fetchall()
```

In `enqueue_crown_round_robin._apply` and `resolve_crown._apply`: replace `c.execute(_ELIGIBLE_CROWN_SURVIVORS_QUERY).fetchall()` with `c.execute(_CROWN_FIELD_QUERY).fetchall()` for the field; in `resolve_crown`, the loser-trash loop (:810-814) changes to trash the FULL eligible set minus the winner:

```python
        full_eligible = [r["id"] for r in c.execute(_ELIGIBLE_CROWN_SURVIVORS_QUERY)]
        for i in full_eligible:
            if i != best_id:
                cur = c.execute("UPDATE offspring SET status = 'trashed' WHERE id = ?", (i,))
                if cur.rowcount != 1:
                    raise ValueError(f"resolve_crown: no offspring row with id={i!r}")
```

Set `CROWN_GAMES_PER_PAIR = 100` at :584 with a one-line comment citing the 2026-08-08 decision. Update the docstrings of both functions (field semantics, cohort-clear, accepted >100-done-games weighting noise). `_ELIGIBLE_CROWN_SURVIVORS_QUERY` and `eligible_crown_survivors()` stay as-is.

- [ ] **Step 4: Consumer sweep (`.claude/rules/test-coverage-sweep.md`):** `Grep -n "CROWN_GAMES_PER_PAIR\|eligible_crown_survivors\|crown_field" src/ scripts/ tests/` — confirm `tests/test_factory_loop_scheduler.py:1073,1118` use the constant symbolically (they do — auto-adjusts), and report any other consumer whose assumptions the cap changes.
- [ ] **Step 5: Run:** `uv run pytest tests/test_factory_loop_crown.py -q` — ALL pass.
- [ ] **Step 6: Commit:** `git add src/ptcg/factory/loop.py tests/test_factory_loop_crown.py` → `feat: CROWN top-K=8 field by floor rating + 100 games/pair (deadline rescope, 2026-08-08 decision)`

---

### Task 4: Runner resilience — bounded retry on `database is locked`

**Files:**
- Modify: `src/ptcg/factory/runner_pool.py` (call sites :332-336 `_requeue_game`/`record_result`, :364 `claim_next_game`; new helper near the module's other helpers)
- Test: `tests/test_factory_runner_pool.py` if it exists (Grep `tests/` for `runner_pool` and use the file that imports it; create `tests/test_factory_runner_retry.py` only if none exists)

**Interfaces:**
- Consumes: `deckdb.claim_next_game`, `deckdb.record_result`, `_requeue_game` (signatures unchanged).
- Produces: `_with_locked_retry(fn, *args, log=..., what=..., **kwargs)` — runner-local; deckdb._write semantics are deliberately NOT changed (a global retry would mask lock-budget regressions elsewhere; the runner is the designated victim-turned-survivor).

**Rationale:** with Tasks 2–3 the root cause is gone, but the runner should degrade gracefully (retry ~3.5 min bounded, loudly logged) instead of 4-worker wipeout on any future transient lock spike. Each underlying attempt already waits busy_timeout=30s.

- [ ] **Step 1: Write the failing tests:**

```python
import sqlite3
import pytest
from ptcg.factory import runner_pool


def test_locked_retry_retries_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(runner_pool.time, "sleep", sleeps.append)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert runner_pool._with_locked_retry(flaky, log=lambda m: None, what="t") == "ok"
    assert calls["n"] == 3 and len(sleeps) == 2


def test_locked_retry_gives_up_loudly(monkeypatch):
    monkeypatch.setattr(runner_pool.time, "sleep", lambda s: None)

    def always_locked():
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError):
        runner_pool._with_locked_retry(always_locked, log=lambda m: None, what="t")


def test_locked_retry_passes_other_errors_through_immediately(monkeypatch):
    def other():
        raise sqlite3.OperationalError("no such table: games")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        runner_pool._with_locked_retry(other, log=lambda m: None, what="t")
```

- [ ] **Step 2: Run to verify failure** (helper undefined).
- [ ] **Step 3: Implement** (module needs `import random` if absent):

```python
_LOCKED_RETRIES = 5
_LOCKED_BACKOFF_S = 3.0


def _with_locked_retry(fn, *args, log=print, what: str = "db-op", **kwargs):
    """Bounded retry for transient write-lock starvation. Each attempt already
    waits the connection's 30s busy_timeout, so 5 attempts bound total wait at
    ~3.5 min before failing LOUDLY (the pool's external 15-min respawn remains
    the backstop). Only the literal 'database is locked' OperationalError is
    retried -- anything else propagates immediately."""
    for attempt in range(1, _LOCKED_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except sqlite3.OperationalError as e:
            if "database is locked" not in str(e) or attempt == _LOCKED_RETRIES:
                raise
            delay = _LOCKED_BACKOFF_S * attempt + random.uniform(0.0, 1.0)
            log(f"{what}: database is locked (attempt {attempt}/{_LOCKED_RETRIES}), "
                f"retrying in {delay:.1f}s")
            time.sleep(delay)
```

Wrap the three call sites: `_with_locked_retry(deckdb.claim_next_game, conn, worker_pid=pid, log=log, what="claim_next_game")`; `_with_locked_retry(deckdb.record_result, conn, row["id"], result.winner, log=log, what="record_result")`; `_with_locked_retry(_requeue_game, conn, row["id"], reason=..., log=log, what="requeue")` — check each real signature at the call site before wrapping (landmark-verify, don't transcribe blindly).

- [ ] **Step 4: Run the targeted test file** — PASS.
- [ ] **Step 5: Commit:** `git add src/ptcg/factory/runner_pool.py tests/<test-file>` → `fix: bounded locked-retry in runner pool (victims survive transient lock spikes)`

---

### Task 5: EQP sweep of ALL lock-held queries (guard against the class, not just the instance)

**Files:**
- Create: `tests/test_factory_lock_access_paths.py`
- Modify: `src/ptcg/factory/deckdb.py` ONLY if the sweep finds another SCAN-inside-lock against a large table that needs an index (expected candidates verified benign in diagnosis: `promote_proven_singles` 0.412s indexed, `reclaim_orphaned_games` ≤0.01s; `loop_scheduler._match_series_complete`'s SCAN should be covered by `ix_games_crown_pair` — verify, don't assume: its filter shape must actually match the index prefix)

**Why:** the access-path-analysis rule has now caught this class twice AFTER shipping (61s bulk-restore, 56s crown enqueue). This task encodes the sweep as a standing executable guard: every hot lock-held query shape gets an EQP assertion, so a future query/schema change that regresses to SCAN fails the suite instead of starving production.

- [ ] **Step 1:** Enumerate every query executed inside a `deckdb._write` `_apply` across `src/ptcg/factory/` (`Grep -n "_write(" src/ptcg/factory/*.py` then read each `_apply`). Build the test file with one EQP assertion per query-shape that touches `games`, `decks`, `decisions`, `concepts`, or `coverage` (the large tables), asserting the expected index (`ix_games_claim`, `ix_games_crown_pair`, `ix_decks_concept`, `ix_decisions_concept`, `ix_concepts_status`) appears and `SCAN <bigtable>` does not. Small tables (`offspring` ~88 rows, `baselines`, `anchor_checks`, `floor_checks`) are exempt — assert nothing for them, note the exemption in a comment.
- [ ] **Step 2:** Run the file: any RED assertion = a real finding; add the missing index to `_DDL_STATEMENTS` with a load-bearing comment (same pattern as Task 2) and re-run to GREEN. If everything is already GREEN, the file is still the deliverable (regression guard).
- [ ] **Step 3:** In the task report, list the full sweep table: query | file:line | index used | verdict.
- [ ] **Step 4: Commit:** `git add tests/test_factory_lock_access_paths.py [src/ptcg/factory/deckdb.py]` → `test: EQP access-path guards for every lock-held query on large tables`

---

### Task 6: Go-live (POST-MERGE rung — orchestrator-executed, explicitly not gating Finish's merge step)

Pre-flight per `.claude/rules/golive-command-preflight.md`: before executing, re-verify every command below against the real script/task interfaces (`Get-ScheduledTask` names, file paths).

- [ ] **Step 1: Merge** `fix/factory-db-lock-contention` → master (fast-forward or merge commit per finishing-a-development-branch).
- [ ] **Step 2: Restart the long-lived workers on new code** (long-lived-worker-code-staleness rule): kill the stale scheduler process tree (running pre-merge code since 8/5 — `Get-CimInstance Win32_Process` to find the `factory_tournament_scheduler` python PIDs, `taskkill /T /F /PID <pid>`), then `Enable-ScheduledTask ptcg-factory-scheduler; Start-ScheduledTask ptcg-factory-scheduler` and `Enable-ScheduledTask ptcg-factory-runner; Start-ScheduledTask ptcg-factory-runner`. Restart `ptcg-factory-ui` (`Stop-ScheduledTask`+`Start-ScheduledTask` — it imports deckdb; rule applies even though behavior is unchanged).
- [ ] **Step 3: Lift the hold:** delete `experiments\factory\PAUSE`.
- [ ] **Step 4: Verify with receipts (Smoke Test Ladder rung 3 for this slice):**
  - Next watch.log firing shows a paired non-paused terminal marker.
  - Scheduler tick health: within ~2 min of scheduler start, `pending` crown games pruned from ~19,379 to ≤ 700-ish (28 top-8 pairs × shortfall) — query the live DB read-only; record before/after counts.
  - `EXPLAIN QUERY PLAN` of the pair-count query against the LIVE DB shows `ix_games_crown_pair`.
  - Runner survival: all 4 workers alive ≥30 min (two watchdog intervals) with zero `database is locked` crashes in runner.log; done-games/hr visibly recovering.
  - Crown field sanity: the enqueued pair set == the top-8 preview from the Global Constraints (v0.13.30, v0.13.4, v0.13.19, v0.13.1, v0.13.15, v0.13.2, v0.13.6, v0.13.14) — if floor rows changed since plan-write, re-derive and record the actual field.
- [ ] **Step 5:** Record all receipts in plan.md + EXPERIMENTS.md (go-live entry, matching the repo's dated-entry convention).

---

## Sync points (orchestrator-owned, per dispatch-test-run-directive)

- After Task 3: full suite `uv run pytest -q` (Tasks 2+3 both touch `loop.py`/`test_factory_loop_crown.py` — sequential, single-writer).
- After Task 5: full suite again (final pre-review gate). Suite baseline: 1039 passing at branch point.
- Review: per-task reviewers on concurrency-touching tasks (2, 3, 5) run at opus tier per `.claude/rules/diagnose-before-dispatch.md` (settled 3-for-3 practice); reviewers of Tasks 2/3/5 MUST run probes against a backup-API copy of the live DB and state findings as executable receipts (RED/GREEN), per the same rule. Reviewer prompts include the git-verification directive and the test-replacement assertion-diff directive.
- Pass 2: `blocking-pr-critic`, `model: opus` first dispatch (settled default).

## Self-review notes (writing-plans checklist)

- Spec coverage: lock fix (T2 index+query, T4 retry, T5 sweep), CROWN rescope (T3), inertness (T1), go-live+verification (T6) — all Brad-approved scope items covered.
- Landmarks verified against real files 2026-08-08 (see Global Constraints); helper names in test code marked "adapt to the file's existing helper" where the exact fixture name was not verified — implementers must report the actual names used (plan-drift protocol applies).
- Type consistency: `crown_field` returns `list[sqlite3.Row]` (same as `eligible_crown_survivors`); `_with_locked_retry` generic passthrough; `CROWN_TOP_K` int; signatures of rewritten functions unchanged.
