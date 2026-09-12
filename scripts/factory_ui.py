"""CLI: the localhost bottom-10 Remove/Pass review server -- `ptcg-factory-ui`'s
entrypoint (tournament T18).

Serves `ui_server.make_app`'s handler on `127.0.0.1` only (spec Risks: no
auth, localhost-only binding is the entire mitigation), under the same
single-instance-lock discipline as every other factory worker
(`watch.instance_lock`) so a second concurrently-started server exits
cleanly rather than stacking -- a sidecar `tournament_ui.lock` file,
mirroring `factory_tournament_scheduler.py`'s `SCHEDULER_LOCK` convention
(a dedicated module-level constant, not a new `FactoryPaths` property).

Registered as the `ptcg-factory-ui` Windows Scheduled Task since the T21
go-live (2026-07-30, `scripts/register_tournament_tasks.ps1`): an
`AtStartup` trigger plus a 15-minute `MultipleInstances=IgnoreNew` watchdog,
running `--db experiments/factory/tournament.db` against the live
production DB and serving `127.0.0.1:8765`. Like the other long-lived
tournament workers, it does not hot-reload -- a merge touching this module
or its imports needs an explicit `Stop-ScheduledTask`/`Start-ScheduledTask`
restart to take effect (`.claude/rules/factory-resume-probe.md`). Local
dev/smoke runs still point at an EXPLICIT temp `--db` path rather than the
production file. This module is unreachable from
`scripts/factory_watch_once.py`'s import graph, so a watch-loop firing
never runs this code.

Precedent for testable functions living directly in a `scripts/*.py` module
(imported by tests as `from scripts.X import Y`, per `pyproject.toml`'s
`pythonpath = ["src", "."]`): `scripts/factory_tournament_scheduler.py`,
`scripts/factory_matrix_worker.py`, `scripts/factory_watch_once.py`.

Serves via ThreadingHTTPServer (daemon threads) since the pool-pruning
slice so a slow DB write no longer freezes concurrent page loads.
"""
from __future__ import annotations

import argparse
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ptcg.factory import deck_quality, deckdb, ui_pages, ui_server  # noqa: E402
from ptcg.factory.watch import instance_lock, throttle_below_normal  # noqa: E402

#: Default localhost port for the review server.
DEFAULT_PORT = 8765

#: Single-instance lock for the UI server (sibling of the matrix/trainer/
#: scheduler worker locks under experiments/factory/).
UI_LOCK = ROOT / "experiments" / "factory" / "tournament_ui.lock"


def run_server(
    db_path: str | Path,
    *,
    port: int = DEFAULT_PORT,
    lock_path: Path = UI_LOCK,
    log=print,
) -> str:
    """Serve the review UI under the single-instance lock. Returns
    `"stopped"` on a clean `serve_forever` exit (`KeyboardInterrupt`), or
    `"busy"` if another UI server already holds the lock (no server started
    in that case)."""
    throttle_below_normal(log)

    # Warm up every engine-DLL-backed cache BEFORE serve_forever spins up
    # request-handling threads. All three routes are lru_cache(maxsize=1)
    # and funnel through deck_quality._DLL_LOCK, but the lock only
    # serializes concurrent callers -- it does not prevent the very first
    # request thread from racing a cold miss against a sibling request
    # thread. Populating the caches here, single-threaded, means every
    # request thread hits a warm cache and never touches the DLL directly
    # (whole-branch review Finding 1).
    ui_pages._card_id_to_name()
    deck_quality._card_db()
    deck_quality._attack_db()

    def _conn_factory():
        return deckdb.connect(Path(db_path))

    handler = ui_server.make_app(_conn_factory)
    try:
        with instance_lock(lock_path):
            httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
            log(f"factory_ui: serving on http://127.0.0.1:{httpd.server_address[1]}/")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                httpd.server_close()
            return "stopped"
    except TimeoutError:
        log("factory_ui: busy -- another UI server holds the lock")
        return "busy"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db", required=True,
        help="Path to the tournament DB. Never the production tournament.db in dev/smoke.",
    )
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    conn = deckdb.connect(Path(args.db))
    deckdb.init_db(conn)
    conn.close()

    result = run_server(args.db, port=args.port)
    print(f"factory_ui result: {result}")


if __name__ == "__main__":
    main()
