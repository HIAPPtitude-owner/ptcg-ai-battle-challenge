"""Loop scheduler + crash-safe resume for the generational champion
tournament (tournament T16).

`loop_tick` is ONE idempotent advance of the Baseline-Challenge Loop, driven
PURELY by DB state (no in-memory state -- spec Resumability), so calling it on
a FRESH connection after a crash resumes exactly where the last committed
transaction left off. Each tick, in order:

1. Reclaim orphaned `claimed` games (a worker died mid-game) back to
   `pending`, capping repeat reclaims so a poison game that keeps crashing
   workers is DEAD-LETTERED (with a DB-persisted error) instead of requeued
   forever (Phase-1-review carry-forward: reclaim + poison cap).
2. If the Founding Census is INCOMPLETE: top up screening games
   (`census.schedule_screening_games`) + a throttled Bradley-Terry refresh.
3. Else (post-census), in the order fixed by the Phase-1 carry-forward:
   throttled `rating.refresh_field_ratings` -> `census.promote_proven_singles`
   -> `census.activate_pair_concepts`. WITHOUT the promote step in between,
   pair activation is a production no-op (singles never leave `'untested'`, so
   `_ACTIVE_SINGLES_QUERY` -- pair activation's own gate -- matches nothing).
   Then BOOTSTRAP the founding `v0.1` baseline on the best census deck if none
   exists (spec BOOTSTRAP), then drive NETCHECK/MATCH/floor-gate/CONFIRM/CROWN
   by offspring status, ANCHOR (baseline + any CROWN-nominated elect), and
   (gated) TRAIN a new offspring -- see `_drive_offspring`'s own docstring
   (T9) for the full per-stage ordering.

Every state transition is DELEGATED to the committed, individually-atomic
stage functions in `loop.py` (each its own `BEGIN IMMEDIATE` transaction that
no-ops for the loser of a scheduler-tick race), so two scheduler ticks racing
never double-enqueue a series -- the scheduler only ORCHESTRATES; it owns no
new non-atomic read-modify-write window EXCEPT `reclaim_orphaned_games`, which
is itself one atomic transaction with its own interleaved-race receipt
(`.claude/rules/single-actor-worker-tests.md`).

Module placement note (JUDGMENT CALL): the plan lists `loop.py` as T16's
modify target for `loop_tick`, but `loop.py` is already 763 lines (over the
500-line cap). Per the orchestrator's steer, the scheduler lives in this NEW
sub-cap module and reuses `loop.py`'s stage functions + the
`eligible_crown_survivors` exclusion helper added there. `loop.py` gains only
that small helper, not `loop_tick`'s bulk.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Callable

from ptcg.factory import anchor, census, deckdb, floor, loop, loop_state, netcheck, rating

#: A `claimed` game older than this is treated as ORPHANED (its worker died
#: before recording OR requeuing it). Set well above the 10-minute (600s)
#: per-match cap: a game exceeding the match cap records a timeout LOSS and
#: releases its claim, so a still-`claimed` game older than this can only mean
#: a dead worker, never a slow-but-live game -- no false reclaim of in-flight
#: games.
RECLAIM_STALE_SECONDS = 900

#: A game reclaimed this many times without ever completing is POISON (it
#: keeps crashing whatever worker claims it). On the Nth reclaim it is
#: DEAD-LETTERED rather than requeued: left `claimed` (workers only claim
#: `pending`, and this reclaim scan excludes dead rows) and flagged `dead=1`
#: in `game_recovery` with the error persisted -- quarantined without a
#: `games.status` migration. Covers the worker-CRASH poison path (the game
#: sticks at `claimed` because the worker died mid-game); the caught-error
#: (`winner == -1`) requeue path is `runner_pool._requeue_game`'s concern and
#: out of T16's file scope.
POISON_MAX_RECLAIMS = 5

#: Throttle: refit field ratings at most this often. Bradley-Terry MM over all
#: done screening games is not free and `rating.py` requires callers to
#: throttle; the last-refresh timestamp is persisted in `meta` so the throttle
#: survives a scheduler restart (crash-safe, no in-memory state).
RATING_REFRESH_INTERVAL_S = 900

#: Pairs lazily activated per post-census tick (`activate_pair_concepts`
#: `max_new`) -- opens pair space generationally as cores prove out.
PAIR_ACTIVATION_PER_TICK = 10

#: TRAIN faucet gate: breed a new offspring only while the number of in-flight
#: challengers (`training`/`queued_for_match`/`matching`/`confirming`) is below
#: this, so the pipeline stays fed but bounded (~4-6 offspring/day is
#: throughput-bounded by the GPU trainer, not this cap -- spec). See the
#: `loop_tick` docstring on the TRAIN ownership JUDGMENT CALL.
PIPELINE_TARGET = 4

FOUNDING_AGENT_VERSION = "v0.1"
_LAST_REFRESH_KEY = "last_rating_refresh_at"

_IN_FLIGHT_STATUSES = ("training", "queued_for_match", "matching", "confirming")

#: NEW table (additive -- never a change to the Phase-1-locked `games`
#: schema): poison-cap / dead-letter bookkeeping with a DB-persisted error.
_RECOVERY_DDL = (
    "CREATE TABLE IF NOT EXISTS game_recovery("
    "game_id INTEGER PRIMARY KEY REFERENCES games(id), "
    "reclaims INTEGER NOT NULL DEFAULT 0, "
    "dead INTEGER NOT NULL DEFAULT 0, "
    "last_error TEXT, "
    "updated_at TEXT NOT NULL)"
)

#: The founding `v0.1` baseline deck D* = the best-rated ACTIVE single-core
#: concept's canonical (lowest-`shell_variant`) deck (spec BOOTSTRAP: "fixing
#: the first baseline (v0.1, D*) where D* is the best census deck"). Mirrors
#: `loop._TOP_FIELD_QUERY`'s active+rated gate and canonical-deck join,
#: restricted to single-core concepts, `LIMIT 1`.
_BEST_CENSUS_DECK_QUERY = (
    "SELECT d.id AS deck_id "
    "FROM concepts c "
    "JOIN coverage co ON co.concept_id = c.id "
    "JOIN decks d ON d.concept_id = c.id "
    "AND d.shell_variant = (SELECT MIN(shell_variant) FROM decks WHERE concept_id = c.id) "
    "WHERE c.status = 'active' AND json_array_length(c.cores) = 1 AND co.rating IS NOT NULL "
    "ORDER BY co.rating DESC, c.id ASC "
    "LIMIT 1"
)


def ensure_recovery_schema(conn: sqlite3.Connection) -> None:
    """Idempotently create the `game_recovery` table (poison-cap / dead-letter
    bookkeeping). Additive-only -- a NEW table, never a change to the
    Phase-1-locked `games` schema, so the dead-letter store needs no
    CHECK-constraint migration of the locked `games.status` domain."""
    deckdb._write(conn, lambda c: c.execute(_RECOVERY_DDL))


def _read_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else None


def _write_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    deckdb._write(
        conn,
        lambda c: c.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        ),
    )


def _maybe_refresh_ratings(
    conn: sqlite3.Connection, now: dt.datetime, interval_s: float
) -> int | None:
    """Throttled `rating.refresh_field_ratings`: refit only if at least
    `interval_s` have elapsed since the last refresh (timestamp persisted in
    `meta`, so the throttle survives a restart). Returns the number of concepts
    rated, or None when the refresh was throttled/skipped this tick."""
    last = _read_meta(conn, _LAST_REFRESH_KEY)
    if last is not None:
        try:
            elapsed = (now - dt.datetime.fromisoformat(last)).total_seconds()
        except ValueError:
            elapsed = interval_s  # unparseable timestamp -> refresh
        if elapsed < interval_s:
            return None
    rated = rating.refresh_field_ratings(conn)
    _write_meta(conn, _LAST_REFRESH_KEY, now.isoformat())
    return rated


def reclaim_orphaned_games(
    conn: sqlite3.Connection,
    now: dt.datetime,
    *,
    stale_seconds: float = RECLAIM_STALE_SECONDS,
    max_reclaims: int = POISON_MAX_RECLAIMS,
) -> dict:
    """Return orphaned `claimed` games (claimed by a since-dead worker -- still
    `claimed` longer than `stale_seconds`, above the 10-min match cap, so never
    a live game) to `pending` for retry, and DEAD-LETTER a poison game that has
    already been reclaimed `max_reclaims` times without ever completing.

    The whole thing is ONE `BEGIN IMMEDIATE` transaction (Pattern SQLITE-TXN),
    so two concurrent scheduler reclaims serialize: the loser's own
    `claimed`-scan runs strictly AFTER the winner commits and no longer sees
    the requeued game, never double-reclaiming or double-counting it
    (`test_reclaim_survives_concurrent_calls`, RED against a scan-outside-txn
    shape, GREEN here).

    Staleness is computed by PARSING each `claimed_at` in Python and comparing
    datetimes rather than lexicographically comparing ISO strings
    (`datetime.isoformat()` omits a zero microsecond field, so a raw string
    `<` compare is fragile within microseconds of the cutoff). The `claimed`
    set is tiny (<= worker count), so the per-row parse is cheap.

    Dead-letter design (JUDGMENT CALL): a dead-lettered game is LEFT `claimed`
    (never `pending`, so no worker re-claims it -- they only take `pending`;
    never `done`, so no rating/aggregate query counts it) and flagged `dead=1`
    in `game_recovery` with the error persisted. This reclaim scan excludes
    `dead=1` rows, so a dead game is quarantined without adding a new value to
    the Phase-1-locked `games.status` CHECK domain.

    Returns `{"reclaimed": int, "dead_lettered": int}`.
    """
    cutoff = now - dt.timedelta(seconds=stale_seconds)

    def _apply(c: sqlite3.Connection) -> dict:
        rows = c.execute(
            "SELECT id, worker_pid, claimed_at FROM games "
            "WHERE status = 'claimed' AND claimed_at IS NOT NULL "
            "AND id NOT IN (SELECT game_id FROM game_recovery WHERE dead = 1)"
        ).fetchall()
        reclaimed = 0
        dead = 0
        for row in rows:
            try:
                claimed_dt = dt.datetime.fromisoformat(row["claimed_at"])
            except ValueError:
                continue  # unparseable claim time -- leave it (defensive)
            if claimed_dt > cutoff:
                continue  # still fresh -- a live worker may hold it
            gid = row["id"]
            prev = c.execute(
                "SELECT reclaims FROM game_recovery WHERE game_id = ?", (gid,)
            ).fetchone()
            reclaims = (prev["reclaims"] if prev is not None else 0) + 1
            if reclaims >= max_reclaims:
                c.execute(
                    "INSERT INTO game_recovery(game_id, reclaims, dead, last_error, updated_at) "
                    "VALUES (?, ?, 1, ?, ?) "
                    "ON CONFLICT(game_id) DO UPDATE SET reclaims = excluded.reclaims, "
                    "dead = 1, last_error = excluded.last_error, updated_at = excluded.updated_at",
                    (
                        gid,
                        reclaims,
                        f"poison: reclaimed {reclaims} times without completing "
                        f"(last worker_pid={row['worker_pid']}, claimed_at={row['claimed_at']})",
                        now.isoformat(),
                    ),
                )
                dead += 1
            else:
                cur = c.execute(
                    "UPDATE games SET status = 'pending', worker_pid = NULL, claimed_at = NULL "
                    "WHERE id = ? AND status = 'claimed'",
                    (gid,),
                )
                if cur.rowcount != 1:
                    continue  # raced away by another writer -- do not count
                c.execute(
                    "INSERT INTO game_recovery(game_id, reclaims, dead, last_error, updated_at) "
                    "VALUES (?, ?, 0, ?, ?) "
                    "ON CONFLICT(game_id) DO UPDATE SET reclaims = excluded.reclaims, "
                    "last_error = excluded.last_error, updated_at = excluded.updated_at",
                    (
                        gid,
                        reclaims,
                        f"reclaimed orphaned claim (worker_pid={row['worker_pid']})",
                        now.isoformat(),
                    ),
                )
                reclaimed += 1
        return {"reclaimed": reclaimed, "dead_lettered": dead}

    return deckdb._write(conn, _apply)


def _best_census_deck(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(_BEST_CENSUS_DECK_QUERY).fetchone()
    return row["deck_id"] if row is not None else None


def _maybe_bootstrap(conn: sqlite3.Connection) -> str | None:
    """Found the `v0.1` baseline on the best census deck if no baseline exists
    yet (spec BOOTSTRAP). Returns `'v0.1'` if it founded this call, else None
    (already founded, or no rated active deck to found on yet)."""
    if loop_state.current_baseline(conn) is not None:
        return None
    deck_id = _best_census_deck(conn)
    if deck_id is None:
        return None
    loop_state.set_founding_baseline(conn, deck_id, loop.FOUNDING_AGENT_CONFIG)
    return FOUNDING_AGENT_VERSION


def _in_flight_offspring_count(conn: sqlite3.Connection) -> int:
    placeholders = ",".join("?" * len(_IN_FLIGHT_STATUSES))
    return conn.execute(
        f"SELECT COUNT(*) FROM offspring WHERE status IN ({placeholders})",
        _IN_FLIGHT_STATUSES,
    ).fetchone()[0]


def _match_series_complete(conn: sqlite3.Connection, offspring_id: str) -> bool:
    """True once EVERY `purpose='match'` game for this offspring is `done`
    (and at least one exists) -- the signal that MATCH has finished and
    `select_optimal_deck` can pick the winning deck. Read-only."""
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done "
        "FROM games WHERE purpose = 'match' AND agent_version_a = ?",
        (offspring_id,),
    ).fetchone()
    total = row["total"] or 0
    done = row["done"] or 0
    return total > 0 and done == total


def _drive_offspring(
    conn: sqlite3.Connection,
    rng,
    trainer_factory: Callable[..., object],
    now: dt.datetime,
    *,
    pipeline_target: int,
) -> dict:
    """Advance every offspring one status step by pure DB state, plus a gated
    TRAIN. Each step delegates to an individually-atomic `loop.py`/`floor.py`/
    `netcheck.py` stage function that no-ops for a racing tick, so the whole
    drive is resume-safe and race-safe without its own lock.

    T9 wiring (anchor-pressure designs 2+3+4 composing): the pipeline now
    threads NETCHECK (design 4, `'training'` offspring) before MATCH, and the
    early anchor FLOOR gate (design 2) between MATCH and CONFIRM -- a
    'matching' offspring only reaches CONFIRM once its OWN 50-game floor
    series against the anchor deck passes; `loop.enqueue_confirm_series`
    already double-gates on `floor_checks.verdict='pass'` internally (T8), so
    the `verdict == "pass"` check here is belt-and-suspenders, not the sole
    guard. A failing floor blames the DECK, so the offspring re-picks its
    next-best MATCH deck and re-floors it (verdict `'repick'`, up to
    `floor.FLOOR_MAX_ATTEMPTS` decks) before it is finally trashed. CROWN now only NOMINATES a champion-elect (`elect`, renamed from
    the pre-T9 `crowned` key -- design 3); the elect's own anchor series
    (enqueued by the SAME `anchor.enqueue_anchor_series` call that backfills
    the current baseline's series, since `resolve_crown`'s nomination is an
    `anchor_checks` row) is what actually promotes it to the new baseline, in
    `anchor.resolve_anchor_check`."""
    # CROWN: resolve a completed round-robin FIRST (which shrinks the eligible
    # set), then start/top-up one if >=2 survivors remain eligible. Both reuse
    # loop.py's baselines-join exclusion, so an already-crowned ex-survivor
    # never re-enters a round-robin (T15 review carry-forward). CROWN only
    # NOMINATES (design 3) -- `elect` is a pending offspring id, not a
    # promoted baseline version.
    elect = loop.resolve_crown(conn)
    crown_enqueued = loop.enqueue_crown_round_robin(conn)

    # ANCHOR: absolute-strength evidence for the current baseline AND (once
    # CROWN nominates one) the pending champion-elect -- design 3, the elect
    # promotes here, not in `resolve_crown`. Both calls are idempotent/no-op
    # safe every tick -- see `anchor.enqueue_anchor_series`/
    # `anchor.resolve_anchor_check` docstrings.
    anchor_enqueued = anchor.enqueue_anchor_series(conn)
    anchor_resolved = anchor.resolve_anchor_check(conn)

    # NETCHECK (design 4): a freshly trained value net is adopted only if it
    # beats the incumbent net head-to-head; either way the offspring advances
    # to 'queued_for_match' once its check settles.
    net_resolved = 0
    for off in loop_state.list_offspring(conn, "training"):
        netcheck.enqueue_net_check(conn, off["id"])
        if netcheck.resolve_net_check(conn, off["id"]) in ("adopt", "reject", "auto"):
            net_resolved += 1

    matched = 0
    for off in loop_state.list_offspring(conn, "queued_for_match"):
        if loop.enqueue_match_games(conn, off["id"]) > 0:
            matched += 1

    # early anchor FLOOR gate (design 2): MATCH-complete picks the optimal
    # deck, but CONFIRM does not start until a separate 50-game floor series
    # against the anchor deck passes. A failing deck does NOT terminally trash
    # the offspring: `floor.resolve_floor` re-picks the next-best untried MATCH
    # deck and enqueues that attempt's series inside the same transaction as
    # the failing verdict ('repick'), trashing only once the attempt budget or
    # the untried-deck supply runs out ('fail'). Both terminal writes happen
    # inside `resolve_floor` itself (mirrors `resolve_confirm`'s own
    # read-decide-act atomicity), so this loop only counts them.
    #
    # No-wedge trace: a 'repick' leaves the offspring at 'matching' with
    # `deck_id` re-pointed and a full pending series already enqueued, so the
    # NEXT tick's `enqueue_floor_series` tops up 0 and `resolve_floor` returns
    # 'pending' until the runners finish that series -- every state reached
    # here has an advancing step (games pending -> verdict -> pass/repick/fail).
    confirm_started = 0
    floor_failed = 0
    floor_repicked = 0
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
        elif verdict == "repick":
            floor_repicked += 1
        elif verdict == "fail":
            floor_failed += 1

    resolved: list[str] = []
    for off in loop_state.list_offspring(conn, "confirming"):
        loop.enqueue_confirm_series(conn, off["id"])  # resume/top-up shortfall
        verdict = loop.resolve_confirm(conn, off["id"])
        if verdict in ("trashed", "survivor"):
            resolved.append(verdict)

    bred = None
    if _in_flight_offspring_count(conn) < pipeline_target:
        bred = loop.train_offspring(conn, rng, trainer_factory, now)

    return {
        "elect": elect,
        "crown_enqueued": crown_enqueued,
        "anchor_enqueued": anchor_enqueued,
        "anchor_resolved": anchor_resolved,
        "net_resolved": net_resolved,
        "matched": matched,
        "confirm_started": confirm_started,
        "floor_failed": floor_failed,
        "floor_repicked": floor_repicked,
        "resolved": resolved,
        "bred": bred,
    }


def loop_tick(
    conn: sqlite3.Connection,
    rng,
    trainer_factory: Callable[..., object] | None,
    now: dt.datetime,
    *,
    pipeline_target: int = PIPELINE_TARGET,
    pair_per_tick: int = PAIR_ACTIVATION_PER_TICK,
    refresh_interval_s: float = RATING_REFRESH_INTERVAL_S,
    reclaim_stale_seconds: float = RECLAIM_STALE_SECONDS,
    poison_max_reclaims: int = POISON_MAX_RECLAIMS,
) -> dict:
    """ONE idempotent, crash-safe advance of the Baseline-Challenge Loop
    (see the module docstring for the ordered contract). `now` (a tz-aware
    UTC `datetime`) is threaded for determinism. `trainer_factory` is only
    invoked when TRAIN actually breeds (post-census, baseline founded,
    in-flight < `pipeline_target`); pass `pipeline_target=0` to disable
    TRAIN entirely (e.g. when a separate trainer worker owns the faucet).

    TRAIN-ownership JUDGMENT CALL: the `loop_tick(conn, rng, trainer_factory,
    now)` signature carries `train_offspring`'s exact params, so this drives
    TRAIN as the plan's Interface states; but T20 also retargets a SEPARATE
    `ptcg-factory-trainer` worker to "the offspring-faucet entrypoint". If that
    separate worker is the sole faucet, the scheduler entrypoint should run
    with `pipeline_target=0`. The in-flight gate bounds over-breeding under
    either interpretation. Flagged for the orchestrator to settle at T20.

    Returns a dict describing the advance (`phase` in
    `census`/`bootstrap`/`bootstrap_wait`/`loop`, plus per-step counts and the
    reclaim/dead-letter tallies).
    """
    ensure_recovery_schema(conn)
    recovery = reclaim_orphaned_games(
        conn, now, stale_seconds=reclaim_stale_seconds, max_reclaims=poison_max_reclaims
    )

    if not census.census_complete(conn):
        # T9 wiring: `schedule_screening_games` no-ops (returns 0) until the
        # anchor deck is registered (Task 4, anchor-opponent rework -- census
        # screening now plays every candidate against the FIXED anchor deck,
        # never round-robin). Idempotent (INSERT OR IGNORE) -- safe to call
        # every tick even once the anchor is already registered.
        anchor.ensure_anchor_deck(conn)
        screening_enqueued = census.schedule_screening_games(
            conn, founding_agent_version=FOUNDING_AGENT_VERSION
        )
        rated = _maybe_refresh_ratings(conn, now, refresh_interval_s)
        return {
            "phase": "census",
            "screening_enqueued": screening_enqueued,
            "rated": rated,
            **recovery,
        }

    # Post-census maintenance, in the carry-forward-mandated order:
    # refresh ratings -> promote proven singles -> activate pairs. Skipping
    # promote makes pair activation a production no-op (carry-forward #1).
    rated = _maybe_refresh_ratings(conn, now, refresh_interval_s)
    promoted = census.promote_proven_singles(conn, floor=census.SCREENING_FLOOR)
    activated = census.activate_pair_concepts(conn, max_new=pair_per_tick)

    founded = _maybe_bootstrap(conn)
    if founded is not None:
        return {
            "phase": "bootstrap",
            "founded": founded,
            "rated": rated,
            "promoted": promoted,
            "activated": activated,
            **recovery,
        }
    if loop_state.current_baseline(conn) is None:
        # Census complete but no rated active deck to found on yet -- wait.
        return {
            "phase": "bootstrap_wait",
            "rated": rated,
            "promoted": promoted,
            "activated": activated,
            **recovery,
        }

    drive = _drive_offspring(conn, rng, trainer_factory, now, pipeline_target=pipeline_target)
    return {
        "phase": "loop",
        "rated": rated,
        "promoted": promoted,
        "activated": activated,
        **drive,
        **recovery,
    }
