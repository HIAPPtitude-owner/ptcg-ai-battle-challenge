"""Census slice runner + real-engine throughput smoke (tournament plan Task 9).

`run_census_slice` exercises the FULL seed -> pair-seed -> schedule -> pool
-> rating pipeline (T1-T8) end to end against a fresh DB. `census.seed_census`
and `census.seed_pair_concepts` always run against the real, complete card
pool -- no slicing at the seeding level, so every fresh-DB run exercises the
full 332,520-row concept enumeration once on real hardware, exactly as the
plan requires. `n_concepts` / `games_per_concept` instead bound how many of
the worst-covered concepts actually get screening games SCHEDULED and PLAYED
this call (via `census.schedule_screening_games`'s existing pending-aware
deficit accounting -- see that function's docstring for why the aggregate
`games_enqueued` total is capped at `n_concepts * games_per_concept` but the
per-concept distribution can shift when the candidate list and the opponent
round-robin cursor overlap in a densely-covered field).

`n_workers <= 1` runs the pool IN-PROCESS via `runner_pool.worker_loop`, so
`runner_pool.play_match` stays monkeypatchable by a fast unit test (a
`multiprocessing` spawn-context child re-imports fresh and can never see a
parent-process monkeypatch -- `runner_pool`'s own module docstring). `n_workers
> 1` uses `runner_pool.spawn_pool`'s real multiprocessing pool for genuine
concurrent throughput -- the real-engine `--smoke` path.

The `--smoke` CLI is the rung-3 real-engine throughput validation
(`.claude/rules/verify-throughput-before-hypothesis.md`): it plays REAL
ISMCTS search-net games using the ratified founding net
(`value_net_weights_v2.json` -- ISMCTS agents only, Locked Decision 1) and
prints the MEASURED games/day extrapolation from actual wall-clock time,
never a synthetic estimate. Always target a fresh, dedicated `--db` path
(virgin-directory rule; never the production `tournament.db` -- see the
plan's Slice Containment Strategy).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ptcg.factory import anchor, census, deckdb, rating, runner_pool  # noqa: E402

#: The founding v0.1 agent: real ISMCTS search-net using the ratified
#: founding net (spec Global Constraints: "Founding net RATIFIED ...
#: value_net_weights_v2.json"). ISMCTS agents only -- no HeuristicAgent
#: anywhere in the tournament (Locked Decision 1).
FOUNDING_AGENT_VERSION = "v0.1"
FOUNDING_NET_WEIGHTS = "src/ptcg/search/value_net_weights_v2.json"
AGENT_CONFIGS: dict[str, dict[str, Any]] = {
    FOUNDING_AGENT_VERSION: {
        "agent_kind": "search-net",
        "agent_config": {"net_weights": FOUNDING_NET_WEIGHTS},
    },
}

#: spec's throughput risk target (`.claude/rules/verify-throughput-before-hypothesis.md`
#: -- this is a reference band to print alongside the MEASURED number, never
#: a substitute for measuring).
SPEC_TARGET_GAMES_PER_DAY = (1500, 2500)


def _done_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM games WHERE status='done'").fetchone()[0]


def run_census_slice(
    db_path: str | Path,
    n_concepts: int,
    games_per_concept: int,
    n_workers: int,
) -> dict[str, Any]:
    """Seed the full real field (singles + pairs), schedule + play screening
    games for up to `n_concepts` worst-covered concepts (each topped up to
    `games_per_concept`), refresh field ratings, and return timing + counts.

    Returns a dict with: `concepts_seeded`, `buildable`, `pairs_seeded`,
    `games_enqueued`, `games_played`, `rated`, `seed_elapsed_s`,
    `play_elapsed_s`, `games_per_day` (measured, from `play_elapsed_s`).
    """
    conn = deckdb.connect(Path(db_path))
    deckdb.init_db(conn)

    t0 = time.perf_counter()
    seed_stats = census.seed_census(conn)
    pairs_seeded = census.seed_pair_concepts(conn)
    # T4 anchor-opponent rework: `schedule_screening_games` no-ops (enqueues
    # 0) until the anchor deck is registered -- census now measures every
    # candidate against the FIXED anchor deck, never round-robin. Idempotent
    # (INSERT OR IGNORE), mirrors the T9 scheduler's own wiring
    # (`loop_scheduler.loop_tick`'s census-incomplete branch).
    anchor.ensure_anchor_deck(conn)
    seed_elapsed_s = time.perf_counter() - t0

    batch = max(0, n_concepts * games_per_concept)
    enqueued = census.schedule_screening_games(
        conn,
        target_per_concept=games_per_concept,
        batch=batch,
        founding_agent_version=FOUNDING_AGENT_VERSION,
    )

    t1 = time.perf_counter()
    if n_workers <= 1:
        played = runner_pool.worker_loop(
            str(db_path), AGENT_CONFIGS, ROOT, stop_when_empty=True
        )
    else:
        before = _done_count(conn)
        runner_pool.spawn_pool(
            str(db_path), n_workers, AGENT_CONFIGS, ROOT, stop_when_empty=True
        )
        played = _done_count(conn) - before
    play_elapsed_s = time.perf_counter() - t1

    rated = rating.refresh_field_ratings(conn)

    games_per_day = (played / play_elapsed_s) * 86400.0 if play_elapsed_s > 0 else 0.0

    return {
        "concepts_seeded": seed_stats["concepts"],
        "buildable": seed_stats["buildable"],
        "pairs_seeded": pairs_seeded,
        "games_enqueued": enqueued,
        "games_played": played,
        "rated": rated,
        "seed_elapsed_s": seed_elapsed_s,
        "play_elapsed_s": play_elapsed_s,
        "games_per_day": games_per_day,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", required=True,
        help="Path to a fresh/dedicated smoke DB. Never the production tournament.db.",
    )
    parser.add_argument("--n-concepts", type=int, required=True, dest="n_concepts")
    parser.add_argument(
        "--games", type=int, required=True, dest="games_per_concept",
        help="Screening games per selected concept (target_per_concept).",
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--smoke", action="store_true",
        help="Print the rung-3 real-engine throughput validation report.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    result = run_census_slice(args.db, args.n_concepts, args.games_per_concept, args.workers)

    print(f"census slice: db={args.db}")
    print(
        f"  singles seeded: {result['concepts_seeded']} (buildable {result['buildable']}), "
        f"pairs seeded this run: {result['pairs_seeded']}"
    )
    print(f"  seed+pair-seed wall time: {result['seed_elapsed_s']:.2f}s")
    print(
        f"  games enqueued: {result['games_enqueued']}, "
        f"games played: {result['games_played']} in {result['play_elapsed_s']:.2f}s "
        f"({args.workers} worker(s))"
    )
    print(f"  concepts rated: {result['rated']}")

    if args.smoke:
        lo, hi = SPEC_TARGET_GAMES_PER_DAY
        print(
            f"  MEASURED throughput: {result['games_per_day']:.0f} games/day "
            f"(spec target: {lo}-{hi}/day) -- extrapolated from real wall-clock "
            "time, not a synthetic estimate "
            "(.claude/rules/verify-throughput-before-hypothesis.md)."
        )
        print(
            "  NOTE: if other CPU-bound work (test suites, factory workers) ran "
            "concurrently with this smoke, throughput is conservatively biased "
            "DOWN, not up (.claude/rules/time-budgeted-arena-contention.md)."
        )


if __name__ == "__main__":
    main()
