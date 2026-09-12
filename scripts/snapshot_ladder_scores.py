"""Read-only ladder score-snapshot logger (freeze-pair probe spec 2026-08-14).

Polls the Kaggle submissions list via the existing
`KaggleClient.list_submissions()` wrapper (kaggle_client.py:91 — reused, not
reinvented) and appends one JSON line per recent submission to
`experiments/factory/ladder_snapshots.jsonl`, stamp-gated to at most one poll
per MIN_INTERVAL_S (4h). Copies the episodes.py `load_stamp`/`save_stamp` +
`harvest_stamp.json` pattern (episodes.py:74-92), with an interval stamp
(`last_ts`, ISO-8601 UTC) instead of episodes' daily `last_day`.

Failure tolerance: a failed poll appends NOTHING, leaves the stamp untouched
(so the next firing retries), and returns "error:<msg>" without raising —
mirroring `check_and_harvest`'s never-raise contract (episodes.py:237-240).
The watch loop wraps this in a second isolation layer regardless. The error
string is formatted `f"error:{exc!r}"[:400]` (episodes.py:268 precedent):
`repr()` escapes embedded newlines to literal `\n`, and the slice caps
length, so a single failed poll can never inject an unbounded multi-line
traceback into `watch.log` — this happened for real (2026-08-14 10:17 HST,
7 untimestamped traceback lines from a Kaggle CLI SSL error) before this
formatting was tightened.

Single-writer note: appends are open-append-close per invocation; the only
automated caller is `watch_once`, which is single-instance-locked
(`instance_lock` on watch.lock), and the manual CLI is a human one-off — no
concurrent-writer guard is needed. Caveat: a manual `--force` run that
SUCCEEDS still saves the stamp to `now`, exactly like an automated poll —
this suppresses the next automated poll for up to MIN_INTERVAL_S (4h). Using
`--force` to unblock a stuck/incident state must be paired with a stamp
reset (backdate `last_ts`, see `save_stamp`) if the next automated poll
needs to fire sooner than a full 4h later; this happened for real during the
2026-08-14 freeze-pair-probe review and required a manual stamp re-reset.

Counted semantics: Kaggle counts the 2 most-recent ACTIVE submissions
(eviction by recency — see `.claude/rules/platform-mechanics-model.md`).
ASSUMPTION, not verified against Kaggle docs: an ERROR row never occupies a
counted slot, so this script's `is_counted` marks the 2 newest rows whose
status != "ERROR" on that assumption. Do not treat this as confirmed
platform behavior for a protection/gating decision without checking
`.claude/rules/platform-mechanics-model.md` first.

Testable-functions-in-scripts precedent: see factory_watch_once.py's module
docstring (`pythonpath = ["src", "."]` -> `from scripts.snapshot_ladder_scores
import ...`).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

#: Hawaii observes no DST, so a fixed UTC-10 offset is permanently exact.
#: This host has no tzdata (ZoneInfo("Pacific/Honolulu") raises) — precedent
#: src/ptcg/factory/ui_pages.py:47-50.
_HST = dt.timezone(dt.timedelta(hours=-10), "HST")

MIN_INTERVAL_S = 14400   # 4h between polls, per spec
RECENT_ROWS = 10         # newest N rows snapshotted each poll
COUNTED_SLOTS = 2        # Kaggle counts the 2 most-recent active submissions


def snapshot_path(root: Path) -> Path:
    return Path(root) / "experiments" / "factory" / "ladder_snapshots.jsonl"


def stamp_path(root: Path) -> Path:
    return Path(root) / "experiments" / "factory" / "snapshot_stamp.json"


def load_stamp(path: Path) -> str | None:
    """Return the last-polled ISO-8601 UTC timestamp or None (never polled).

    Treats a valid-JSON-but-non-dict stamp (a bare list, string, or number —
    parses fine but has no `.get()`) the same as a missing/corrupt stamp:
    self-heals to None rather than raising AttributeError, which would
    otherwise escape `snapshot_due` and permanently disable the poll step
    (see the F5 finding, 2026-08-14 freeze-pair-probe review)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data.get("last_ts")


def save_stamp(path: Path, now: dt.datetime) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)  # virgin-dir safe
    path.write_text(json.dumps({"last_ts": now.isoformat()}),
                    encoding="utf-8")


def snapshot_due(stamp_path: Path, now: dt.datetime,
                 min_interval_s: int = MIN_INTERVAL_S) -> bool:
    """True when the last poll is at least `min_interval_s` old.

    A FUTURE-dated stamp (the pre-seeded inertness stamp, see the plan's
    Task 4 step 1) yields a negative delta -> not due: that is the
    live-on-write guard, not an edge case.
    """
    last = load_stamp(stamp_path)
    if last is None:
        return True
    try:
        last_dt = dt.datetime.fromisoformat(last)
    except (ValueError, TypeError):
        # ValueError: malformed ISO string. TypeError: last_ts is a
        # non-string JSON value (e.g. {"last_ts": 12345}) -- fromisoformat
        # requires str and raises TypeError, not ValueError, for that shape.
        # Both self-heal to due (success rewrites the stamp), same as the
        # other corrupt-stamp shapes handled by load_stamp above.
        return True
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=dt.timezone.utc)
    return (now - last_dt).total_seconds() >= min_interval_s


def build_rows(subs, now: dt.datetime) -> list[dict]:
    """Newest RECENT_ROWS submissions -> JSONL dicts (newest first)."""
    newest = sorted(subs, key=lambda r: r.date, reverse=True)[:RECENT_ROWS]
    counted = set(
        [i for i, r in enumerate(newest) if r.status != "ERROR"]
        [:COUNTED_SLOTS])
    hst = now.astimezone(_HST)
    return [{
        "utc_ts": now.isoformat(),
        "hst_ts": hst.isoformat(),
        "ref": r.ref,
        "sub_date": r.date,
        "description": r.description,
        "status": r.status,
        "public_score": r.public_score,
        "is_counted": i in counted,
    } for i, r in enumerate(newest)]


def append_rows(out_path: Path, rows: list[dict]) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)  # virgin-dir safe
    with out_path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def check_and_snapshot(paths, client, *, now: dt.datetime | None = None,
                       log=print) -> str:
    """One stamp-gated poll. Returns "not-due" | "ok(N rows)" | "error:<msg>".
    Never raises. Stamp is saved only AFTER a successful append, so a failed
    poll retries on the next firing.

    An empty parsed listing (0 rows — CLI exit 0 but no submissions
    returned) is treated as a FAILURE, not a successful empty poll: this
    competition's account always has submissions, so 0 rows means the
    listing broke rather than being genuinely empty, and saving the stamp
    on 0 rows would silently burn the 4h slot as `ok(0 rows)` without ever
    recording real data (see the F8 finding, 2026-08-14 freeze-pair-probe
    review)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    sp = stamp_path(paths.root)
    try:
        if not snapshot_due(sp, now):
            return "not-due"
        rows = build_rows(client.list_submissions(), now)
        if not rows:
            return "error:empty-listing"
        append_rows(snapshot_path(paths.root), rows)
        save_stamp(sp, now)
        return f"ok({len(rows)} rows)"
    except Exception as exc:  # noqa: BLE001 - failure tolerance is the point
        log(f"ladder snapshot failed: {exc!r}")
        return f"error:{exc!r}"[:400]


def main() -> None:
    from ptcg.factory.cycle import FactoryPaths
    from ptcg.factory.kaggle_client import KaggleClient

    p = argparse.ArgumentParser(
        description="Read-only Kaggle ladder score snapshot (JSONL append).")
    p.add_argument("--force", action="store_true",
                   help="ignore the stamp gate for this one poll "
                        "(a successful poll still rewrites the stamp to now)")
    args = p.parse_args()

    paths = FactoryPaths(root=ROOT)
    if args.force:
        save_stamp(stamp_path(ROOT),
                   dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc))
    print(f"snapshot: {check_and_snapshot(paths, KaggleClient())}")


if __name__ == "__main__":
    main()
