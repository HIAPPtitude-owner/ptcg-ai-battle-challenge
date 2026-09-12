"""CLI: one firing of the NARROWED continuous watch loop (tournament T20
cutover). Invoked every ~15 minutes by the `ptcg-factory-continuous`
scheduled task; also runnable on demand.

Post-cutover, this loop's ONLY three jobs are:
1. **Episode harvest** (`episodes.check_and_harvest`) -- SURVIVES the cutover
   per Brad's PD-A override; internally stamp-gated (`harvest_stamp.json`),
   extract-then-delete, 2GB cap, all unchanged; failure-isolated so a Kaggle
   outage never blocks submit.
2. **Ladder score snapshot** (`scripts/snapshot_ladder_scores.py`) -- read-only,
   stamp-gated 4h, freeze-pair probe spec 2026-08-14.
3. **Submission scheduler** (`subscheduler.maybe_submit`, tournament T17) --
   the independent-clock 4.8h-mark + 24h-daily-floor-probe decision, run
   against the tournament DB (`experiments/factory/tournament.db`). SUBMIT_HOLD
   is respected by THIS caller (mirrors `cycle.py:256`), never by
   `maybe_submit` itself.

RETIRED from this loop at cutover (evolution/matrix/dashboard system replaced
by the generational tournament -- plan T20 + PD-B):
  - `evolution.evo_gate_step` / `_agent_pool_path` (evolution pool births)
  - `deck_matrix.refill_queue` (legacy candidate refill)
  - `episodes.top_meta_decks` / `inject_meta_anchors` (meta-anchor consumer --
    extracts are retained as a data asset; see the future-hook note below)
  - ALL FOUR `dashboard.safe_render` call sites (`ptcg-factory-ui`'s `/status`
    page is the sole status surface now; `dashboard.py`/`render_dashboard.py`
    retired-in-place, left on disk unwired)
  - `cycle.run_cycle` (gate/submit is now the submission scheduler's job)

TERMINAL LOG MARKER on EVERY exit path (paused / busy / harvest+snapshot+submit /
error) -- the 36-hour-silent-crash-cascade fix (`.claude/rules/
factory-task-scheduler-liveness.md` terminal-marker section): a firing that
launches but writes NO terminal marker is the crash signature that rule
exists to catch, so the whole in-lock body is wrapped in try/except that
emits a `cycle-error:` marker (and returns an error result -> `main` exits 1)
on any unhandled exception.

Precedent for testable functions living directly in a `scripts/*.py` module
(imported by tests as `from scripts.X import Y`, per `pyproject.toml`'s
`pythonpath = ["src", "."]`): scripts/factory_matrix_worker.py, scripts/
factory_tournament_scheduler.py, scripts/factory_trainer_worker.py."""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ptcg.factory import deckdb, episodes, subscheduler  # noqa: E402
from ptcg.factory.cycle import FactoryPaths  # noqa: E402
from ptcg.factory.gate import SubmissionCounter  # noqa: E402
from ptcg.factory.kaggle_client import KaggleClient  # noqa: E402
from ptcg.factory.watch import (  # noqa: E402
    append_watch_log, instance_lock, throttle_below_normal,
)
from scripts.snapshot_ladder_scores import check_and_snapshot  # noqa: E402


def _utc_now() -> dt.datetime:
    """tz-aware UTC now. `subscheduler.maybe_submit`/`mark_index` bucket the
    day by UTC calendar (Kaggle's cap resets on a UTC day), so the caller MUST
    pass a tz-aware UTC datetime -- see the `_require_utc` guard below."""
    return dt.datetime.now(dt.timezone.utc)


def _require_utc(now: dt.datetime) -> dt.datetime:
    """Cheap runtime guard (tournament T17 review carry-forward): a naive
    datetime into `maybe_submit`/`mark_index` silently mis-buckets the mark
    index. `maybe_submit` has no guard of its own, so enforce it here."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(
            "watch_once: `now` must be a timezone-aware UTC datetime "
            f"(got naive {now!r}) -- subscheduler.mark_index buckets by UTC day"
        )
    return now


def watch_paths(paths: FactoryPaths) -> dict[str, Path]:
    """Watch-owned paths, kept off `FactoryPaths` itself but centralized here
    so prod code and tests reference the exact same locations."""
    factory_dir = paths.root / "experiments" / "factory"
    return {
        "watch_lock": factory_dir / "watch.lock",
        "watch_log": paths.log_dir / "watch.log",
        "tournament_db": factory_dir / "tournament.db",
        "subscheduler_state": factory_dir / "subscheduler_state.json",
    }


def watch_once(paths: FactoryPaths, client, *,
               no_submit: bool = False,
               db_path: Path | None = None,
               harvest_fn=episodes.check_and_harvest,
               snapshot_fn=check_and_snapshot,
               submit_fn=subscheduler.maybe_submit,
               now_fn=_utc_now, log=print) -> dict:
    """One per-firing pass of the NARROWED watch loop. Locked flow:
    1. throttle_below_normal.
    2. PAUSE file -> {"paused": True} (terminal marker "paused"); harvest,
       snapshot, and submit never run.
    3. instance_lock (fail-fast) -> {"busy": True} on contention (terminal
       marker "busy: ..."); every remaining step runs inside the lock.
    4. episode harvest (`check_and_harvest`) -- failure-isolated (terminal
       marker "episodes: ...").
    5. ladder score snapshot (`check_and_snapshot`) -- read-only, stamp-gated
       4h, failure-isolated (terminal marker "snapshot: ..."); runs BEFORE
       the SUBMIT_HOLD check so scoring history keeps accumulating while
       submissions are held.
    6. SUBMIT_HOLD present -> log "submit: held (SUBMIT_HOLD present)" and skip
       the submission scheduler entirely (mirror `cycle.py:256`).
       Else -> `subscheduler.maybe_submit` against the tournament DB with a
       tz-aware UTC `now` (terminal marker "submit: ...").
    7. Any unhandled exception inside the lock -> "cycle-error: ..." marker +
       {"error": ...} (main exits 1). EVERY path writes exactly one terminal
       marker.
    """
    throttle_below_normal(log)

    wp = watch_paths(paths)
    watch_log = wp["watch_log"]
    if paths.pause_file.exists():
        append_watch_log(watch_log, "paused")
        return {"paused": True}

    try:
        with instance_lock(wp["watch_lock"]):
            # 1. Episode harvest (Brad PD-A: SURVIVES cutover). Failure-isolated
            # -- a Kaggle outage or malformed corpus must never block submit.
            # check_and_harvest already returns "error:..." rather than raising,
            # but the try/except is a hard second layer in case a future edit
            # lets something escape. (naive local `now` is check_and_harvest's
            # existing stamp-gating convention -- unchanged by this cutover.)
            #
            # FUTURE HOOK (PD-A, NOT implemented this slice): harvested
            # extracts (`extracts.jsonl`) are retained as a growing data asset;
            # the reserved consumer is `census.inject_meta_concepts(paths,
            # decks)` (map harvested meta decks -> tournament census concepts).
            try:
                hres = harvest_fn(paths, client, now=dt.datetime.now(), log=log)
                append_watch_log(watch_log, f"episodes: {hres}")
            except Exception as exc:  # noqa: BLE001 - isolation is the point
                log(f"episode harvest failed (submit unaffected): {exc!r}")
                append_watch_log(watch_log, "episodes: error (isolated)")
                hres = "error"

            # 1b. Ladder score snapshot (freeze-pair probe spec 2026-08-14).
            # Read-only + stamp-gated (snapshot_stamp.json, 4h min interval);
            # runs BEFORE the SUBMIT_HOLD check (like harvest) so scoring
            # history keeps accumulating while submissions are held.
            # check_and_snapshot already never raises; this try/except is the
            # hard second isolation layer, mirroring the harvest step.
            try:
                sres = snapshot_fn(paths, client, now=now_fn(), log=log)
                append_watch_log(watch_log, f"snapshot: {sres}")
            except Exception as exc:  # noqa: BLE001 - isolation is the point
                log(f"ladder snapshot failed (submit unaffected): {exc!r}")
                append_watch_log(watch_log, "snapshot: error (isolated)")
                sres = "error"

            # 2. Submission scheduler. SUBMIT_HOLD is the CALLER's gate
            # (mirror cycle.py:256) -- maybe_submit has no opinion about it.
            if paths.submit_hold_file.exists():
                append_watch_log(watch_log, "submit: held (SUBMIT_HOLD present)")
                return {"harvest": hres, "snapshot": sres, "submit_held": True}

            conn = deckdb.connect(db_path or wp["tournament_db"])
            deckdb.init_db(conn)  # idempotent CREATE IF NOT EXISTS -- defensive
            counter = SubmissionCounter(paths.counter)
            now = _require_utc(now_fn())
            results = submit_fn(
                conn, client, counter, wp["subscheduler_state"],
                paths.bundle_dir, paths.root, now, no_submit=no_submit, log=log)
            summary = ",".join(f"{ident}={outcome}"
                               for ident, outcome, _ in results) or "no-op"
            append_watch_log(watch_log, f"submit: {summary}")
            return {"harvest": hres, "snapshot": sres, "submit": results}
    except TimeoutError:
        append_watch_log(watch_log, "busy: another firing holds the lock")
        return {"busy": True}
    except Exception as exc:  # noqa: BLE001 - last-resort crash isolation
        # The `with instance_lock(...)` block has already released the lock by
        # the time control reaches here (context-manager unwind on propagation).
        tb = traceback.format_exc()
        append_watch_log(watch_log,
                         f"cycle-error: {type(exc).__name__}: {exc}\n{tb}")
        return {"error": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--no-submit", action="store_true",
                   help="dry-run: build+verify+gate but never upload")
    p.add_argument("--db", default=None,
                   help="tournament DB path (default experiments/factory/tournament.db)")
    args = p.parse_args()

    paths = FactoryPaths(root=ROOT)
    result = watch_once(paths, KaggleClient(), no_submit=args.no_submit,
                        db_path=Path(args.db) if args.db else None)
    print(f"watch_once result: { {k: v for k, v in result.items() if k not in ('harvest', 'snapshot')} }")
    if result.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
