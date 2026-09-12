"""MANUAL-ONLY freeze-day curation (counted-pair-protection design 5):
upload a hand-picked counted pair, best LAST (best = newest = survives one
more recency eviction), then set SUBMIT_HOLD.

NEVER called by any scheduled task -- human-triggered only. Safety
ordering per the dryrun-is-not-the-real-thing lesson (global CLAUDE.md):

* BOTH bundles are built AND verified BEFORE any upload;
* any API failure exits nonzero LOUDLY (codes below);
* SUBMIT_HOLD is set ONLY after BOTH uploads are confirmed via the
  submissions API -- partial success leaves the hold OFF so the operator
  sees the honest state. Confirmation requires a FRESH row (added since a
  pre-upload snapshot), not merely a row whose description prefix matches
  -- a re-uploaded legacy identity already has a live historical row with
  that exact prefix (see experiments/LADDER.md), so a prefix-only check
  would false-confirm a swallowed/failed upload as successful.

Exit codes: 0 ok; 2 bundle/args failure; 3 auth dead; 4 daily cap;
5 first (second-best) upload failed -- nothing uploaded; 6 best upload
failed after the first succeeded (PARTIAL: the counted pair is NOT the
intended pair -- fix manually); 7 uploads not confirmed by the API as
FRESH rows (also covers: confirmation snapshot unavailable before upload
-- see below).

Usage (freeze day, per docs/weekly-review-checklist.md convergence
freeze):
    uv run python scripts/curate_counted_pair.py --best v0.11 --second v0.13 --dry-run
    uv run python scripts/curate_counted_pair.py --best v0.11 --second v0.13

    uv run python scripts/curate_counted_pair.py \
        --second mega-starmie-water-lean-energy-up2-searchnet-v0.1 \
        --best mega-starmie-water-lean-attacker-down1-attacker-up1-searchnet-v0.1 --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ptcg.factory import candidates, deckdb, subscheduler  # noqa: E402
from ptcg.factory.bundles import (  # noqa: E402
    build_candidate_bundle, verify_candidate_bundle,
)
from ptcg.factory.gate import (  # noqa: E402
    HARD_DAILY_CAP, SubmissionCounter, utc_today,
)
from ptcg.factory.kaggle_client import KaggleClient, check_auth  # noqa: E402
from ptcg.factory.submit import git_head, submission_description  # noqa: E402


def _resolve_identity(conn, ledger_path: Path, ident: str):
    """Tournament baselines row first; legacy ledger id fallback.

    The fallback fires ONLY on ValueError (unknown version) -- any other
    exception from the tournament path (corrupt/missing DB) propagates, so
    a broken primary store is never silently laundered into a ledger
    lookup. Ledger match is exact `Candidate.id` (= "<name>-<version>",
    the LADDER.md identity string); 0 matches raises, >1 raises.
    """
    try:
        return subscheduler.candidate_for_version(conn, ident)
    except ValueError as tournament_exc:
        rows = [c for c in candidates.load_ledger(ledger_path) if c.id == ident]
        if len(rows) == 1:
            return rows[0]
        if rows:
            raise ValueError(
                f"ambiguous ledger id {ident!r}: {len(rows)} rows") from tournament_exc
        missing_note = " (ledger file missing)" if not ledger_path.exists() else ""
        raise ValueError(
            f"{ident!r} not found in tournament baselines ({tournament_exc}) "
            f"nor as a ledger id in {ledger_path}{missing_note}") from tournament_exc


def run(argv=None, client=None, build_fn=build_candidate_bundle,
        verify_fn=verify_candidate_bundle, log=print) -> int:
    p = argparse.ArgumentParser(
        description="Freeze-day counted-pair curation (manual only).")
    p.add_argument("--best", required=True,
                   help="baseline version (v0.11) or legacy ledger id "
                        "(name-vX.Y) uploaded LAST (survives longest)")
    p.add_argument("--second", required=True,
                   help="baseline version (v0.11) or legacy ledger id "
                        "(name-vX.Y) uploaded FIRST")
    p.add_argument("--db",
                   default=str(ROOT / "experiments" / "factory" / "tournament.db"),
                   help="tournament DB (default: the production DB -- this "
                        "is a production script; tests pass a tmp DB)")
    p.add_argument("--ledger",
                   default=str(ROOT / "experiments" / "factory" / "candidates.json"),
                   help="legacy candidates.json ledger -- fallback identity "
                        "store when no tournament baselines row matches")
    p.add_argument("--counter",
                   default=str(ROOT / "experiments" / "factory" / "submission_counter.json"))
    p.add_argument("--out-dir",
                   default=str(ROOT / "build" / "factory" / "curation"))
    p.add_argument("--hold-file",
                   default=str(ROOT / "experiments" / "factory" / "SUBMIT_HOLD"))
    p.add_argument("--dry-run", action="store_true",
                   help="build+verify+print plan; no auth probe, no counter "
                        "reservation, no upload, no SUBMIT_HOLD")
    args = p.parse_args(argv)

    if args.best == args.second:
        log(f"CURATION FAILED: --best and --second are the same version "
            f"({args.best!r}) -- a pair needs two identities")
        return 2

    conn = deckdb.connect(Path(args.db))
    ledger_path = Path(args.ledger)
    cands = {}
    for label, ident in (("second", args.second), ("best", args.best)):
        try:
            cands[label] = _resolve_identity(conn, ledger_path, ident)
        except Exception as exc:
            log(f"CURATION FAILED (resolve {label}={ident}): {exc!r}")
            return 2

    commit = git_head(ROOT)
    out_dir = Path(args.out_dir)
    bundles = {}
    for label in ("second", "best"):  # verify BOTH before ANY upload
        cand = cands[label]
        staging = out_dir / label
        try:
            bundle = build_fn(cand, staging)
            verify_fn(cand, bundle, staging / "submission")
        except Exception as exc:
            log(f"CURATION FAILED (bundle {label}={cand.version}): {exc!r}")
            return 2
        bundles[label] = bundle

    descs = {label: submission_description(cands[label], commit,
                                           exploratory=False)
             for label in ("second", "best")}
    if args.dry_run:
        log(f"DRY-RUN would upload FIRST : {descs['second']}")
        log(f"DRY-RUN would upload LAST  : {descs['best']}")
        log("DRY-RUN: no auth probe, no counter reservation, no upload, "
            "no SUBMIT_HOLD")
        return 0

    client = client if client is not None else KaggleClient()
    auth_detail = check_auth(client)
    if auth_detail is not None:
        log(f"CURATION FAILED (auth dead): {auth_detail}")
        return 3

    try:
        before = {(r.date, r.description) for r in client.list_submissions()}
    except Exception as exc:
        log(f"CURATION FAILED (confirmation snapshot unavailable): {exc!r} "
            "-- NOTHING uploaded, SUBMIT_HOLD NOT set")
        return 7

    today = utc_today()
    counter = SubmissionCounter(Path(args.counter))
    if not counter.try_reserve(today, 2):
        log(f"CURATION FAILED: needs 2 hard-cap slots, "
            f"{counter.today_count(today)}/{HARD_DAILY_CAP} already used")
        return 4

    try:
        client.submit(bundles["second"], descs["second"])
    except Exception as exc:
        counter.release(today, 2)  # nothing uploaded -- return both slots
        log(f"CURATION FAILED (first upload): {exc!r} -- NOTHING uploaded, "
            "SUBMIT_HOLD NOT set")
        return 5
    log(f"uploaded (first): {descs['second']}")

    try:
        client.submit(bundles["best"], descs["best"])
    except Exception as exc:
        counter.release(today, 1)  # one real upload happened; keep 1 slot
        log(f"CURATION PARTIAL (best upload failed): {exc!r} -- the counted "
            "pair is NOT the intended pair; SUBMIT_HOLD NOT set; fix "
            "manually before walking away")
        return 6
    log(f"uploaded (last): {descs['best']}")

    try:
        after = client.list_submissions()
    except Exception as exc:
        log(f"CURATION FAILED (confirmation call): {exc!r} -- SUBMIT_HOLD "
            "NOT set; verify the pair manually on Kaggle")
        return 7
    fresh = [r for r in after if (r.date, r.description) not in before]
    for label in ("second", "best"):
        cand = cands[label]
        prefix = f"{cand.name} {cand.version}"
        if not any(r.description.startswith(prefix) for r in fresh):
            log(f"CURATION FAILED (confirmation): {prefix!r} not visible as "
                "a FRESH submission in the API; SUBMIT_HOLD NOT set -- "
                "verify manually")
            return 7

    hold = Path(args.hold_file)
    hold.parent.mkdir(parents=True, exist_ok=True)  # virgin-dir rule
    hold.write_text(
        f"curate_counted_pair: pair {args.second} then {args.best} uploaded "
        f"+ confirmed at {dt.datetime.now(dt.timezone.utc).isoformat()}\n",
        encoding="utf-8")
    log(f"SUBMIT_HOLD set at {hold}")
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
