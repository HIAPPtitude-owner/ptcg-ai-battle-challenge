"""SQLite deck database for the generational champion tournament (spec Locked
Decision 9). Single source of truth for concepts/decks/games/ratings/decisions/
offspring/baselines. WAL mode + busy_timeout give the 4-8 runner processes + UI
+ scheduler safe concurrent access; every write is a short BEGIN IMMEDIATE txn.

`games.winner` semantics: `0`=deck_a agent won, `1`=deck_b agent won,
`2`=draw (mirrors `MatchResult.winner`). `games.purpose` is one of
`{screening,match,confirm,crown,anchor,floor,netcheck,pair_gate}` so the loop
can query its own series (`floor` = the early anchor floor gate,
`ptcg.factory.floor`; `netcheck` = the validated-net-swap head-to-head,
`ptcg.factory.netcheck`; `pair_gate` = the counted-pair-protection
head-to-head, `ptcg.factory.pairgate`).
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Callable, TypeVar

SCHEMA_VERSION = 1

T = TypeVar("T")


def connect(path: Path) -> sqlite3.Connection:
    """Open a connection with WAL + long busy_timeout (Pattern SQLITE-CONN).

    Creates the parent directory if missing (virgin-directory rule — the DB
    file itself is a first-write artifact). `isolation_level=None` puts the
    connection in autocommit mode: callers own transaction boundaries
    explicitly via `_write`/`BEGIN IMMEDIATE` rather than relying on the
    driver's implicit deferred transactions.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # 1 writer + N readers concurrently
    conn.execute("PRAGMA busy_timeout=30000")  # wait up to 30s for a competing writer
    conn.execute("PRAGMA synchronous=NORMAL")  # WAL-safe durability, faster than FULL
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _write(conn: sqlite3.Connection, fn: Callable[[sqlite3.Connection], T]) -> T:
    """Run fn(conn) inside a BEGIN IMMEDIATE ... COMMIT (Pattern SQLITE-TXN).

    IMMEDIATE acquires the write lock up front, so a concurrent writer waits
    (busy_timeout) rather than racing a deferred lock upgrade. Rolls back on
    any exception. `fn` must not call `conn.executescript(...)` — sqlite3's
    `executescript` implicitly COMMITs any pending transaction before running,
    which would leave this wrapper's own COMMIT/ROLLBACK with nothing active.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = fn(conn)
        conn.execute("COMMIT")
        return result
    except Exception:
        conn.execute("ROLLBACK")
        raise


#: Referenced both from `_DDL_STATEMENTS` (init_db's idempotent pass) and
#: directly from `migrate_decisions`'s rebuild `_apply` (a v1->v2 rebuild
#: recreates the `decisions` table from scratch, so it must re-run this too
#: rather than relying on some LATER init_db call to backfill it).
_IX_DECISIONS_CONCEPT_SQL = "CREATE INDEX IF NOT EXISTS ix_decisions_concept ON decisions(concept_id)"

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


_DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS concepts(
      id TEXT PRIMARY KEY, cores TEXT NOT NULL, builder_version INTEGER NOT NULL DEFAULT 1,
      status TEXT NOT NULL DEFAULT 'untested'
        CHECK(status IN ('untested','active','culled','unbuildable','finalist')),
      reason TEXT DEFAULT '')
    """,
    "CREATE INDEX IF NOT EXISTS ix_concepts_status ON concepts(status)",
    """
    CREATE TABLE IF NOT EXISTS decks(
      id TEXT PRIMARY KEY, concept_id TEXT NOT NULL REFERENCES concepts(id),
      cards TEXT NOT NULL, shell_variant INTEGER NOT NULL DEFAULT 0,
      energy_count INTEGER, pokemon_count INTEGER)
    """,
    # Load-bearing, NOT cosmetic: `census.schedule_screening_games` joins/
    # filters `decks` by `concept_id` inside its BEGIN IMMEDIATE supertxn.
    # Without this index that is a full SCAN of the whole decks table (95,907
    # rows on the live DB) while holding the write lock -- measured >180s vs
    # 1.16s with the index, i.e. long past the runner workers' 30s
    # `busy_timeout`, which kills them one after another.
    "CREATE INDEX IF NOT EXISTS ix_decks_concept ON decks(concept_id)",
    """
    CREATE TABLE IF NOT EXISTS games(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      deck_a_id TEXT NOT NULL, deck_b_id TEXT NOT NULL,
      agent_version_a TEXT NOT NULL, agent_version_b TEXT NOT NULL,
      purpose TEXT NOT NULL DEFAULT 'screening',
      winner INTEGER, status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','claimed','done')),
      priority REAL NOT NULL DEFAULT 0.0, worker_pid INTEGER,
      claimed_at TEXT, timestamp TEXT)
    """,
    "CREATE INDEX IF NOT EXISTS ix_games_claim ON games(status, priority)",
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
    # Load-bearing, NOT cosmetic (Pass-2 Finding 1, 2026-08-05): every
    # restored concept in `ui_actions.apply_bulk_decision`'s single
    # BEGIN IMMEDIATE transaction runs `_restore_target`'s
    # `SELECT ... FROM decisions WHERE concept_id=?` -- one SELECT per row,
    # ALL inside the same lock-holding txn that also INSERTs into this same
    # table. Without this index each SELECT is a full SCAN of the whole
    # `decisions` table (same failure shape as `ix_decks_concept` above,
    # against `decks`); measured (solo, no lock contention) at N=20,000
    # restored concepts: 61.13s without this index vs 1.19s with it -- long
    # past the runner workers' 30s `busy_timeout`. With a concurrent writer
    # actually contending for the lock, the writer itself hits
    # `sqlite3.OperationalError: database is locked` once busy_timeout
    # expires (see test_bulk_restore_does_not_starve_concurrent_writer_at_scale
    # in tests/test_factory_ui_actions.py for the RED/GREEN receipt). Live
    # culled pool: 95,895 concepts.
    _IX_DECISIONS_CONCEPT_SQL,
    """
    CREATE TABLE IF NOT EXISTS coverage(
      concept_id TEXT PRIMARY KEY REFERENCES concepts(id),
      games_played INTEGER NOT NULL DEFAULT 0,
      distinct_opponents INTEGER NOT NULL DEFAULT 0, rating REAL)
    """,
    """
    CREATE TABLE IF NOT EXISTS offspring(
      id TEXT PRIMARY KEY, parent_baseline_version TEXT NOT NULL,
      search_config_json TEXT NOT NULL, value_net_ref TEXT, deck_id TEXT,
      status TEXT NOT NULL DEFAULT 'training'
        CHECK(status IN
          ('training','queued_for_match','matching','confirming','trashed','survivor')),
      created_at TEXT NOT NULL)
    """,
    """
    CREATE TABLE IF NOT EXISTS baselines(
      version TEXT PRIMARY KEY, offspring_id TEXT, deck_id TEXT NOT NULL, crowned_at TEXT NOT NULL)
    """,
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
    """
    CREATE TABLE IF NOT EXISTS floor_checks(
      offspring_id TEXT PRIMARY KEY,
      deck_id TEXT NOT NULL,
      games_planned INTEGER NOT NULL,
      games_done INTEGER NOT NULL DEFAULT 0,
      wins INTEGER NOT NULL DEFAULT 0,
      wr REAL,
      verdict TEXT NOT NULL DEFAULT 'pending'
        CHECK(verdict IN ('pending','pass','fail')),
      created_at TEXT NOT NULL,
      resolved_at TEXT,
      attempt INTEGER NOT NULL DEFAULT 0,
      tried_decks TEXT NOT NULL DEFAULT '[]',
      anomaly TEXT)
    """,
    """
    CREATE TABLE IF NOT EXISTS net_checks(
      offspring_id TEXT PRIMARY KEY,
      deck_id TEXT NOT NULL,
      candidate_net_ref TEXT,
      incumbent_net_ref TEXT,
      games_planned INTEGER NOT NULL,
      games_done INTEGER NOT NULL DEFAULT 0,
      wins INTEGER NOT NULL DEFAULT 0,
      wr REAL,
      verdict TEXT NOT NULL DEFAULT 'pending'
        CHECK(verdict IN ('pending','adopt','reject','auto')),
      created_at TEXT NOT NULL,
      resolved_at TEXT)
    """,
    """
    CREATE TABLE IF NOT EXISTS pair_gate_checks(
      version TEXT PRIMARY KEY,
      opp_version TEXT NOT NULL,
      opp_deck_id TEXT NOT NULL,
      opp_submitted_at TEXT NOT NULL,
      games_planned INTEGER NOT NULL,
      games_done INTEGER NOT NULL DEFAULT 0,
      wins INTEGER NOT NULL DEFAULT 0,
      wr REAL,
      verdict TEXT NOT NULL DEFAULT 'pending'
        CHECK(verdict IN ('pending','pass','fail')),
      created_at TEXT NOT NULL,
      resolved_at TEXT)
    """,
    "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
]

# Pass-2 Finding 2 fix (2026-08-05, hardens the prior MINOR-8 fix): match
# the `decisions` TABLE DDL by its statement PREFIX
# (`CREATE TABLE IF NOT EXISTS decisions(`), not by a bare `"decisions(" in
# s` substring test. The substring form still matches the
# `_IX_DECISIONS_CONCEPT_SQL` statement above (`...ON decisions(concept_id)`
# also contains the substring `"decisions("`) -- it only "worked" by
# accident of list order (the table entry happens to come first), and would
# silently repoint `migrate_decisions`'s rebuild at the INDEX statement
# instead of the TABLE statement the moment a future edit reorders
# `_DDL_STATEMENTS`. `.lstrip()` handles this file's triple-quoted
# statements, which all open with a leading newline + indentation.
_DECISIONS_DDL_INDEX = next(
    i
    for i, s in enumerate(_DDL_STATEMENTS)
    if s.lstrip().startswith("CREATE TABLE IF NOT EXISTS decisions(")
)


def init_db(conn: sqlite3.Connection) -> None:
    """Idempotent schema creation — safe to call repeatedly (all DDL is
    `IF NOT EXISTS`). Runs as one BEGIN IMMEDIATE transaction via individual
    `execute()` calls per statement rather than `executescript()` — see the
    `_write` docstring for why `executescript` is unsafe inside this wrapper.

    `migrate_decisions` runs FIRST, before the `_DDL_STATEMENTS` loop below
    — NOT after (found via a failing test while adding `ix_decisions_concept`
    to `_DDL_STATEMENTS`, 2026-08-05). `_DDL_STATEMENTS` includes a
    `CREATE INDEX ... ON decisions(concept_id)` entry; on a legacy v1 DB,
    `decisions` exists but has no `concept_id` column yet, so running that
    statement before the v1->v2 rebuild raises `OperationalError: no such
    column: concept_id`. Running the migration first guarantees `decisions`
    is already v2-shaped (or altogether absent, in which case
    `migrate_decisions` no-ops and the loop below creates it fresh) by the
    time any `_DDL_STATEMENTS` entry touches it.
    """
    migrate_decisions(conn)

    def _apply(c: sqlite3.Connection) -> None:
        for statement in _DDL_STATEMENTS:
            c.execute(statement)
        c.execute(
            "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version',?)",
            (str(SCHEMA_VERSION),),
        )

    _write(conn, _apply)

    # Composition index: only once the columns exist (see _IX_DECKS_COMP_SQL
    # comment — production gets the columns from the one-shot go-live
    # migration, not from init_db).
    if _decks_has_composition_columns(conn):
        conn.execute(_IX_DECKS_COMP_SQL)


def _decisions_is_v2(c: sqlite3.Connection) -> bool:
    row = c.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='decisions'"
    ).fetchone()
    return row is None or "'restore'" in row["sql"]


def migrate_decisions(conn: sqlite3.Connection) -> None:
    """Idempotently rebuild a v1 `decisions` table to the v2 shape (nullable
    deck_id, concept_id + prior_status columns, expanded action CHECK).
    SQLite cannot ALTER a CHECK constraint or drop NOT NULL, so this is a
    rename -> create-v2 -> copy -> drop rebuild inside ONE transaction.
    Detection keys on the stored DDL text: a table whose sql already names
    'restore' is v2 — nothing to do. Strict superset: every v1 row/insert
    shape remains valid in v2.

    TOCTOU guard (Pattern SQLITE-TXN): the cheap pre-check below runs OUTSIDE
    the write lock purely to skip the common already-migrated case without
    paying for BEGIN IMMEDIATE. It is NOT authoritative — a concurrent
    migrator can rebuild the table between this pre-check and `_apply`
    acquiring the lock. The AUTHORITATIVE decision is the re-check repeated
    inside `_apply`, under BEGIN IMMEDIATE: a loser that loses the race sees
    the already-migrated (v2) schema there and no-ops instead of rebuilding
    from the (already-migrated) table and silently dropping any concept_id/
    prior_status values written into the gap.
    """
    if _decisions_is_v2(conn):
        return

    def _apply(c: sqlite3.Connection) -> None:
        if _decisions_is_v2(c):  # authoritative re-check under the write lock
            return
        c.execute("ALTER TABLE decisions RENAME TO decisions_v1")
        c.execute(_DDL_STATEMENTS[_DECISIONS_DDL_INDEX])
        c.execute(
            "INSERT INTO decisions(id, deck_id, action, actor, timestamp) "
            "SELECT id, deck_id, action, actor, timestamp FROM decisions_v1"
        )
        # AUTOINCREMENT continuity: the copy above only transfers EXISTING
        # rows, so if decisions_v1's max-ever-assigned id (tracked in its own
        # sqlite_sequence row, preserved by the RENAME above) exceeds the max
        # COPIED id (e.g. rows were deleted before migration), carry the
        # higher value forward — otherwise a previously-used, now-deleted id
        # gets reused by the next AUTOINCREMENT insert into `decisions`.
        c.execute(
            "INSERT INTO sqlite_sequence(name, seq) "
            "SELECT 'decisions', seq FROM sqlite_sequence WHERE name='decisions_v1' "
            "AND NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name='decisions')"
        )
        c.execute(
            "UPDATE sqlite_sequence SET seq = "
            "(SELECT COALESCE(MAX(seq),0) FROM sqlite_sequence WHERE name='decisions_v1') "
            "WHERE name='decisions' AND seq < "
            "(SELECT COALESCE(MAX(seq),0) FROM sqlite_sequence WHERE name='decisions_v1')"
        )
        c.execute("DROP TABLE decisions_v1")
        # Finding 1 fix (2026-08-05): the rebuild above recreates `decisions`
        # from scratch (RENAME the old table away, CREATE a fresh v2 table),
        # so any index on the OLD table is gone -- this rebuild's own _apply
        # must recreate it in the SAME transaction rather than relying on
        # some LATER init_db() call to backfill it (a DB that migrates and
        # is never re-init'd would otherwise run unindexed indefinitely).
        c.execute(_IX_DECISIONS_CONCEPT_SQL)

    _write(conn, _apply)


def enqueue_game(
    conn: sqlite3.Connection,
    deck_a_id: str,
    deck_b_id: str,
    agent_version_a: str,
    agent_version_b: str,
    purpose: str,
    priority: float = 0.0,
) -> int:
    """Insert a pending game into the queue (Pattern SQLITE-TXN). Returns the
    new game's id."""

    def _insert(c: sqlite3.Connection) -> int:
        cur = c.execute(
            "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, agent_version_b, "
            "purpose, priority, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (deck_a_id, deck_b_id, agent_version_a, agent_version_b, purpose, priority),
        )
        game_id = cur.lastrowid
        if game_id is None:  # narrow int | None: a successful INSERT always sets lastrowid
            raise RuntimeError("enqueue_game: INSERT produced no rowid")
        return game_id

    return _write(conn, _insert)


def claim_next_game(conn: sqlite3.Connection, worker_pid: int) -> sqlite3.Row | None:
    """Atomically claim the highest-priority pending game (Pattern SQLITE-TXN).

    The SELECT and UPDATE run inside ONE `BEGIN IMMEDIATE` transaction, which
    acquires the write lock before either statement executes. A concurrent
    claimant's own `claim_next_game` call either waits (`busy_timeout`) for
    this transaction to COMMIT, or — if it wins the race for the write lock
    first — claims the row before this call even starts its SELECT, in which
    case this call's SELECT finds no pending row and returns None. Two
    racers can therefore never both claim the same game id (Pattern
    INTERLEAVED-TEST, `.claude/rules/single-actor-worker-tests.md`) — this is
    a single-UPDATE-equivalent claim-by-transaction, never a bare
    SELECT-then-UPDATE split across separate autocommit statements.
    """

    def _claim(c: sqlite3.Connection) -> sqlite3.Row | None:
        pending = c.execute(
            "SELECT * FROM games WHERE status='pending' ORDER BY priority DESC, id LIMIT 1"
        ).fetchone()
        if pending is None:
            return None
        cur = c.execute(
            "UPDATE games SET status='claimed', worker_pid=?, claimed_at=? "
            "WHERE id=? AND status='pending'",
            (worker_pid, dt.datetime.now(dt.timezone.utc).isoformat(), pending["id"]),
        )
        if cur.rowcount != 1:  # unreachable under BEGIN IMMEDIATE; loud guard vs regression
            raise RuntimeError(
                f"claim_next_game: claim UPDATE matched {cur.rowcount} rows "
                f"for game id={pending['id']!r} (expected exactly 1)"
            )
        return c.execute("SELECT * FROM games WHERE id=?", (pending["id"],)).fetchone()

    return _write(conn, _claim)


def record_result(conn: sqlite3.Connection, game_id: int, winner: int) -> None:
    """Mark a game `done`, record the winner, and bump `coverage.games_played`
    for both decks' concepts (Pattern SQLITE-TXN). `winner` follows
    `MatchResult.winner` semantics: `0`=deck_a agent won, `1`=deck_b agent
    won, `2`=draw.
    """

    def _record(c: sqlite3.Connection) -> None:
        game = c.execute(
            "SELECT deck_a_id, deck_b_id, status FROM games WHERE id=?", (game_id,)
        ).fetchone()
        if game is None:
            raise ValueError(f"record_result: no game with id={game_id!r}")
        cur = c.execute(
            "UPDATE games SET status='done', winner=?, timestamp=? "
            "WHERE id=? AND status='claimed'",
            (winner, dt.datetime.now(dt.timezone.utc).isoformat(), game_id),
        )
        if cur.rowcount != 1:
            # Only claimed->done is legal; a pending (never-claimed) or done
            # (already-recorded) game must fail LOUDLY, and the raise rolls
            # back the whole txn so coverage is never bumped.
            raise ValueError(
                f"record_result: game id={game_id!r} is in status "
                f"{game['status']!r}, not 'claimed' — refusing to record "
                "(never claimed, or result already recorded)"
            )
        for deck_id in (game["deck_a_id"], game["deck_b_id"]):
            c.execute(
                "UPDATE coverage SET games_played = games_played + 1 "
                "WHERE concept_id = (SELECT concept_id FROM decks WHERE id=?)",
                (deck_id,),
            )

    _write(conn, _record)


def pending_count(conn: sqlite3.Connection, purpose: str | None = None) -> int:
    """Count `pending` games, optionally filtered by `purpose`. Read-only —
    no write-lock transaction needed."""
    if purpose is None:
        row = conn.execute("SELECT COUNT(*) FROM games WHERE status='pending'").fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM games WHERE status='pending' AND purpose=?", (purpose,)
        ).fetchone()
    return row[0]
