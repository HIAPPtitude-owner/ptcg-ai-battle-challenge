"""Parallel ISMCTS game-runner pool over the SQLite queue (tournament T6).

Claim -> play -> record, looped across N OS worker processes. The cg engine
holds one battle's native state per process (module-global
`battle_start`/`battle_select`/`battle_finish` in `ptcg.arena.runner`), so
games CANNOT share a process -- `spawn_pool` therefore uses
`multiprocessing.get_context("spawn")` with N independent worker processes.
Windows spawn semantics mean each child re-imports fresh: nothing (least of
all a `sqlite3.Connection`, which isn't picklable) is inherited across the
process boundary, so every worker opens its OWN `deckdb` connection via
`deckdb.connect` -- never shared.

A `winner == -1` (errored) match is requeued to `pending` (not recorded as a
result) so another worker retries it; `_requeue_game` is scoped to the
single claimed row the calling worker itself holds, so unlike a general
read-modify-write window it is never contended by another worker (a game
stays `claimed`, hence unclaimable by anyone else, until this worker either
records or requeues it) -- no adversarial multi-actor test is needed for
that path (`.claude/rules/single-actor-worker-tests.md`); the genuinely
shared queue operations (`claim_next_game`/`record_result`) already carry
their adversarial receipts in `tests/test_factory_deckdb_queue.py` (T4).

Agent-version resolution (D1 crash-loop fix, 2026-07-31): the in-memory
`agent_configs` map (CLI `--agent-configs`, default only the founding
`v0.1`) is a CACHE seeded with the CLI-supplied entries, not the source of
truth. Match/confirm games reference OFFSPRING versions (`v0.1.1`...) whose
configs live only in the tournament DB (`offspring.search_config_json` +
`value_net_ref`, written by `loop_state.insert_offspring`), and future
crowned baselines (`v0.2`+) live in `baselines` + their winning offspring
row -- `_resolve_agent_entry` falls back to the DB for any version missing
from the map and caches the result in-process. Before this fix,
`_build_candidate`'s bare `agent_configs[version]` raised
`KeyError: 'v0.1.1'` on the first match game, killing the worker, then the
pool (`spawn_pool: 1/1 workers exited nonzero`), then the scheduled task --
a silent ~15-minute crash-loop with no on-disk evidence.

File logging (the other half of the D1 fix): the scheduled task has no
stdout redirect, so every worker appends timestamped lines (same
`append_watch_log` format as the watch loop's log) to `logs/runner.log`
in a `logs/` directory SIBLING TO THE GIVEN DB FILE -- for the production
tournament DB that is the factory's existing logs directory, next to the
watch loop's own log. The location is caller-controlled (derived from
`--db`), never hardcoded: this module stays free of any literal reference
to the live factory state tree (pinned by the Phase-1 containment test in
`tests/test_factory_tournament_phase1.py`, whose literal-string grep this
docstring must itself not trip).
"""
from __future__ import annotations

import json
import multiprocessing
import os
import random
import sqlite3
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from ptcg.arena.runner import play_match
from ptcg.factory import anchor, deckdb, floor, netcheck
from ptcg.factory.candidates import Candidate
from ptcg.factory.evaluate import build_agent
from ptcg.factory.watch import append_watch_log, throttle_below_normal

#: Seconds to wait before re-polling an empty queue in continuous mode
#: (`stop_when_empty=False`). Irrelevant to `stop_when_empty=True` callers
#: (tests, one-shot dev/smoke runs), which never reach the sleep branch.
POLL_INTERVAL_S = 1.0

#: Bounded retry for transient write-lock starvation (Task 4 -- runners
#: survive lock spikes rather than dying wholesale; the root cause is fixed
#: separately in Tasks 2-3). Each attempt already waits the connection's
#: 30s busy_timeout, so 5 attempts bound total wait at ~3.5 min before
#: failing loudly -- the pool's external ~15-min respawn remains the
#: backstop.
_LOCKED_RETRIES = 5
_LOCKED_BACKOFF_S = 3.0


def _with_locked_retry(fn, *args, _log=print, _what: str = "db-op", **kwargs):
    """Bounded retry for transient write-lock starvation. Each attempt already
    waits the connection's 30s busy_timeout, so 5 attempts bound total wait at
    ~3.5 min before failing LOUDLY (the pool's external 15-min respawn remains
    the backstop). Only the literal 'database is locked' OperationalError is
    retried -- anything else propagates immediately.

    The retry-logger/label params are named `_log`/`_what` (leading
    underscore, keyword-only) specifically so they cannot collide with a
    wrapped callee's own kwargs -- `_requeue_game(conn, game_id, reason,
    log=print)` has its OWN `log` parameter, and a same-named `log=` here
    would have shadowed and swallowed the caller's logger for `fn` (reviewer
    finding, fix round 1/5: `_requeue_game` silently fell back to `print()`
    instead of the injected file logger). `**kwargs` -- including a plain
    `log=`/`what=` meant for the callee -- passes through to `fn` untouched."""
    for attempt in range(1, _LOCKED_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except sqlite3.OperationalError as e:
            if "database is locked" not in str(e) or attempt == _LOCKED_RETRIES:
                raise
            delay = _LOCKED_BACKOFF_S * attempt + random.uniform(0.0, 1.0)
            _log(f"{_what}: database is locked (attempt {attempt}/{_LOCKED_RETRIES}), "
                 f"retrying in {delay:.1f}s")
            time.sleep(delay)


class UnresolvableAgentVersionError(RuntimeError):
    """An agent version string resolvable neither from the in-process config
    map nor from the tournament DB (offspring row, baselines row + meta).
    The loud, per-game replacement for the old `KeyError` that killed the
    whole worker (D1 crash-loop)."""


def _runner_log_path(db_path: str | Path) -> Path:
    """`<db-dir>/logs/runner.log` -- a `logs/` directory sibling to the given
    DB file. The production tournament DB lives in the factory state dir, so
    this lands in that dir's existing `logs/` subdirectory next to the watch
    loop's log; a test/dev DB under `tmp_path` gets its own scratch
    `logs/runner.log`. Caller-controlled by construction (follows the DB the
    pool was pointed at), so this module never names the live factory tree."""
    return Path(db_path).resolve().parent / "logs" / "runner.log"


def _log(log_path: Path, msg: str) -> None:
    """Append a timestamped line to `runner.log` (same `append_watch_log`
    format/encoding as the watch loop's log) AND echo to stdout for
    foreground dev runs. The scheduled task discards stdout (D1 finding: the
    v0.1.1 crash-loop left zero on-disk evidence), so the file append is the
    load-bearing sink. Logging is failure-isolated -- it must never itself
    take down a game or worker."""
    line = f"runner_pool[pid={os.getpid()}]: {msg}"
    try:
        print(line)
    except Exception:  # noqa: BLE001 - a broken stdout must not kill a worker
        pass
    try:
        append_watch_log(log_path, line)
    except Exception:  # noqa: BLE001 - isolation is the point
        pass


def _load_deck_cards(conn: sqlite3.Connection, deck_id: str) -> list[int]:
    row = conn.execute("SELECT cards FROM decks WHERE id=?", (deck_id,)).fetchone()
    if row is None:
        raise ValueError(f"_load_deck_cards: no deck with id={deck_id!r}")
    return json.loads(row["cards"])


def _requeue_game(conn: sqlite3.Connection, game_id: int, reason: str, log=print) -> None:
    """Return an errored game to 'pending' so another worker can retry it
    (Pattern SQLITE-TXN, mirrors `deckdb.record_result`'s claimed-only
    guard). Only legal from 'claimed' status -- refuses loudly otherwise
    rather than silently reverting an already-done/never-claimed row."""

    def _apply(c: sqlite3.Connection) -> None:
        cur = c.execute(
            "UPDATE games SET status='pending', worker_pid=NULL, claimed_at=NULL "
            "WHERE id=? AND status='claimed'",
            (game_id,),
        )
        if cur.rowcount != 1:
            raise ValueError(
                f"_requeue_game: game id={game_id!r} is not in 'claimed' status "
                f"(rowcount={cur.rowcount}) -- refusing to requeue"
            )

    deckdb._write(conn, _apply)
    log(f"requeued game {game_id} ({reason})")


def _entry_from_offspring_row(off: sqlite3.Row) -> dict[str, Any]:
    """Build an `{agent_kind, agent_config}` map entry from an `offspring`
    row. `search_config_json` is gene-only (T12 `train_offspring` never folds
    `net_weights` into it -- the net lives in the separate `value_net_ref`
    column), so the two are combined here; mirrors
    `subscheduler._current_baseline_agent_config`. `agent_kind` is always
    `search-net`: the tournament is ISMCTS-only (spec Locked Decision 1),
    the same hardcoding as `subscheduler._baseline_candidate`."""
    config = json.loads(off["search_config_json"])
    if off["value_net_ref"]:
        config = {**config, "net_weights": off["value_net_ref"]}
    return {"agent_kind": "search-net", "agent_config": config}


def _resolve_agent_entry(
    conn: sqlite3.Connection,
    version: str,
    agent_configs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Resolve `version` -> `{agent_kind, agent_config}`, checking in order:

    1. the in-process map (CLI-supplied configs + previously-resolved cache);
    2. the `offspring` table -- offspring ids ARE their `v0.G.k` version
       strings (T11/T12), and match/confirm games carry the offspring id as
       `agent_version_a` (`loop.py`'s fixed side convention);
    3. the `baselines` table -- a FOUNDING baseline's config lives in
       `meta['founding_agent_config']` (T11 `set_founding_baseline`); a
       CROWNED baseline's config is its winning offspring's own row.

    A successful DB resolution is cached back into `agent_configs`, so each
    worker process pays the lookup once per version, and no BEGIN IMMEDIATE
    transaction is needed -- there is no read-decide-act write-back here
    (`.claude/rules/single-actor-worker-tests.md`).

    Cache-validity invariant (NOT "config columns never change": an
    offspring's `value_net_ref` IS rewritten by `netcheck.resolve_net_check`
    when a candidate net is rejected). What holds instead is that no game
    references the RAW offspring id until its net check has settled: while an
    offspring is at status `'training'` the only games carrying it are
    net-check games, and those resolve through the sentinel
    `netcheck.NETCHECK_CAND_PREFIX`/`NETCHECK_INC_PREFIX` versions above --
    which read `net_checks`' own frozen refs, not the offspring's mutable
    column. MATCH/CONFIRM/CROWN games (the ones carrying the raw id) are only
    enqueued from `'queued_for_match'` onward, i.e. strictly after
    `value_net_ref` has stopped changing -- so a cached entry can never go
    stale under a live rewrite.

    Raises `UnresolvableAgentVersionError` when the version exists nowhere.
    """
    entry = agent_configs.get(version)
    if entry is not None:
        return entry

    if version == anchor.ANCHOR_VERSION:
        # Strength-gate anchor: the ladder identity (HeuristicAgent). Named
        # special-case BEFORE any DB lookup -- anchor has no offspring row by
        # design, and must never ride the unresolvable-version dead-letter
        # path (22634c5 crash class).
        entry = {"agent_kind": "heuristic", "agent_config": {}}
        agent_configs[version] = entry
        return entry

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

    off = conn.execute(
        "SELECT search_config_json, value_net_ref FROM offspring WHERE id=?",
        (version,),
    ).fetchone()
    if off is not None:
        entry = _entry_from_offspring_row(off)
    else:
        base = conn.execute(
            "SELECT offspring_id FROM baselines WHERE version=?", (version,)
        ).fetchone()
        if base is None:
            raise UnresolvableAgentVersionError(
                f"agent version {version!r} not found in the supplied config map, "
                "the offspring table, or the baselines table"
            )
        if base["offspring_id"] is None:
            meta = conn.execute(
                "SELECT value FROM meta WHERE key='founding_agent_config'"
            ).fetchone()
            if meta is None:
                raise UnresolvableAgentVersionError(
                    f"baseline {version!r} is founding (offspring_id IS NULL) but "
                    "meta['founding_agent_config'] is missing -- was it founded "
                    "via loop_state.set_founding_baseline?"
                )
            entry = {"agent_kind": "search-net", "agent_config": json.loads(meta["value"])}
        else:
            base_off = conn.execute(
                "SELECT search_config_json, value_net_ref FROM offspring WHERE id=?",
                (base["offspring_id"],),
            ).fetchone()
            if base_off is None:
                raise UnresolvableAgentVersionError(
                    f"crowned baseline {version!r} references offspring_id="
                    f"{base['offspring_id']!r}, which has no offspring row"
                )
            entry = _entry_from_offspring_row(base_off)

    agent_configs[version] = entry
    return entry


def _build_candidate(conn: sqlite3.Connection, version: str,
                     agent_configs: dict[str, dict[str, Any]],
                     deck_id: str) -> Candidate:
    cfg = _resolve_agent_entry(conn, version, agent_configs)
    return Candidate.create(
        name=version,
        version=version,
        deck=deck_id,
        agent_kind=cfg["agent_kind"],
        agent_config=cfg.get("agent_config", {}),
    )


def run_one_game(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    agent_configs: dict[str, dict[str, Any]],
    root: Path,
    log: Callable[[str], None] = print,
) -> int | None:
    """Play one claimed game row and record the result.

    Returns the winner (0/1/2, per `MatchResult.winner` semantics) on
    success, or `None` if the game could not be completed: an errored match
    (`winner == -1`) is requeued to `pending` for another worker to retry,
    while a game whose agent version is resolvable NOWHERE (map + DB, see
    `_resolve_agent_entry`) is deliberately LEFT `claimed` -- NOT requeued --
    so it rides the scheduler's existing orphan-reclaim machinery
    (`loop_scheduler.reclaim_orphaned_games`): a paced retry after
    `RECLAIM_STALE_SECONDS`, then a `game_recovery` dead-letter after
    `POISON_MAX_RECLAIMS`. An immediate `_requeue_game` would hot-loop the
    same failure forever without ever hitting the poison cap (`pending`
    bounces are invisible to the reclaim counter). Either way the WORKER
    survives -- a config-resolution failure must never kill the worker or
    the pool again (D1 crash-loop).

    `root` is accepted for interface parity with the rest of the factory's
    per-firing helpers; nothing in this function currently needs it since
    `build_agent` resolves its own weight paths against `evaluate.ROOT`
    internally. `log` follows `_requeue_game`'s log-callable convention --
    `worker_loop` injects the file logger so failures land in `runner.log`.
    """
    deck_a = _load_deck_cards(conn, row["deck_a_id"])
    deck_b = _load_deck_cards(conn, row["deck_b_id"])

    try:
        cand_a = _build_candidate(conn, row["agent_version_a"], agent_configs,
                                  row["deck_a_id"])
        cand_b = _build_candidate(conn, row["agent_version_b"], agent_configs,
                                  row["deck_b_id"])
    except UnresolvableAgentVersionError as exc:
        log(f"config-resolution failed for game {row['id']}: {exc} -- "
            "leaving game claimed for scheduler reclaim/poison cap")
        return None

    agent_a = build_agent(cand_a, deck_a)
    agent_b = build_agent(cand_b, deck_b)

    result = play_match(agent_a, agent_b, deck_a, deck_b)
    if result.winner == -1:
        _with_locked_retry(_requeue_game, conn, row["id"],
                            reason=result.error or "unknown match error",
                            log=log, _log=log, _what="requeue")
        return None

    _with_locked_retry(deckdb.record_result, conn, row["id"], result.winner,
                        _log=log, _what="record_result")
    return result.winner


def worker_loop(
    db_path: str,
    agent_configs: dict[str, dict[str, Any]],
    root: Path,
    stop_when_empty: bool = False,
    max_games: int | None = None,
) -> int:
    """Claim -> play -> record in a loop over ONE worker's own connection.

    Opens its own `deckdb` connection (never shared/inherited -- see module
    docstring). Returns the count of games this worker completed
    successfully; a requeued (errored) or config-unresolvable (left-claimed)
    game does not count and does not stop the loop.
    """
    conn = deckdb.connect(Path(db_path))
    pid = os.getpid()
    played = 0
    log_path = _runner_log_path(db_path)
    log = lambda m: _log(log_path, m)  # noqa: E731 - _requeue_game-style injection
    log(f"worker start db={db_path} stop_when_empty={stop_when_empty} "
        f"max_games={max_games}")
    while True:
        if max_games is not None and played >= max_games:
            break
        row = _with_locked_retry(deckdb.claim_next_game, conn, worker_pid=pid,
                                  _log=log, _what="claim_next_game")
        if row is None:
            if stop_when_empty:
                break
            time.sleep(POLL_INTERVAL_S)
            continue
        winner = run_one_game(conn, row, agent_configs, root, log=log)
        if winner is not None:
            played += 1
    log(f"worker exiting cleanly (played={played})")
    return played


def _worker_entrypoint(
    db_path: str,
    agent_configs: dict[str, dict[str, Any]],
    root: Path,
    stop_when_empty: bool,
    max_games: int | None,
) -> None:
    """Module-level (picklable) child-process entrypoint for `spawn_pool`.
    Must stay a plain module-level function -- `multiprocessing`'s spawn
    context pickles the target by qualified name, so a lambda or a closure
    would fail to pickle on Windows.

    Any escaping exception is logged (with traceback) to `runner.log` before
    re-raising, so a future crash-loop leaves on-disk evidence even though
    the scheduled task discards stdout/stderr (the D1 logging gap)."""
    throttle_below_normal()
    try:
        worker_loop(db_path, agent_configs, root, stop_when_empty=stop_when_empty,
                    max_games=max_games)
    except BaseException as exc:
        _log(_runner_log_path(db_path),
             f"worker CRASHED: {type(exc).__name__}: {exc}\n"
             f"{traceback.format_exc()}")
        raise


def spawn_pool(
    db_path: str,
    n_workers: int,
    agent_configs: dict[str, dict[str, Any]],
    root: Path,
    stop_when_empty: bool = False,
    max_games: int | None = None,
) -> None:
    """Supervisor: spawn `n_workers` independent OS processes, each running
    `worker_loop`, via `multiprocessing.get_context("spawn")` -- the cg
    engine's module-global native battle state means each game MUST run in
    its own process (verified: `ptcg.arena.runner` uses module-global
    `battle_start`/`battle_select`/`battle_finish`), so workers cannot be
    threads sharing one interpreter.

    A crashed worker must not silently strand the pool: every process is
    joined regardless of any prior failure (so a crash never abandons
    still-running siblings), and if ANY worker exited nonzero this raises
    `RuntimeError` naming every failing pid/exitcode -- callers must not
    treat a `spawn_pool` return as proof every worker succeeded.
    """
    log_path = _runner_log_path(db_path)
    _log(log_path, f"pool start: {n_workers} workers, db={db_path}")
    ctx = multiprocessing.get_context("spawn")
    procs = [
        ctx.Process(
            target=_worker_entrypoint,
            args=(db_path, agent_configs, root, stop_when_empty, max_games),
        )
        for _ in range(n_workers)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join()

    failed = [p for p in procs if p.exitcode != 0]
    if failed:
        detail = ", ".join(f"pid={p.pid} exitcode={p.exitcode}" for p in failed)
        _log(log_path,
             f"pool FAILED: {len(failed)}/{n_workers} workers exited nonzero: {detail}")
        raise RuntimeError(
            f"spawn_pool: {len(failed)}/{n_workers} workers exited nonzero: {detail}"
        )
    _log(log_path, f"pool finished cleanly: {n_workers} workers, db={db_path}")
