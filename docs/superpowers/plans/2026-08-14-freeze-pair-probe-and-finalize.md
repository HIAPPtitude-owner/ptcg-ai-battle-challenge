# Freeze-Pair Probe-and-Finalize Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the Brad-approved probe-then-finalize freeze strategy — probe-upload `v0.16` + `mega-starmie-water-lean-searchnet-v1.0` today, ship a stamp-gated read-only ladder score-snapshot logger into the watch loop, and hand the Aug-16 session a flag-verified final-curation runbook.

**Architecture:** Two operational tasks (dry-run probe → Brad-gated real upload) run the existing `scripts/curate_counted_pair.py` unchanged. One small new script (`scripts/snapshot_ladder_scores.py`, testable-functions-in-scripts precedent) polls `KaggleClient.list_submissions()` and appends JSONL rows, copying the `episodes.py` stamp-gating pattern with a 4h interval stamp; `watch_once()` gains a failure-isolated `snapshot:` step placed (like harvest) BEFORE the SUBMIT_HOLD check. Live-on-write inertness is enforced by pre-seeding a future-dated stamp and committing it BEFORE any watch-loop code lands; activation is an explicit post-review stamp reset verified against a real firing.

**Tech Stack:** Python 3.11 / uv, pytest, Kaggle CLI via `uvx` (wrapped by `src/ptcg/factory/kaggle_client.py`), JSONL append files, Windows Task Scheduler firing `ptcg-factory-continuous` every ~15 min.

**Spec:** `docs/superpowers/specs/2026-08-14-freeze-pair-probe-and-finalize-design.md` (commit `9a9ccfe`) — decisions LOCKED. This plan adds no scope.

## Global Constraints

Copied from the spec's Invariants + key constraints; every task's requirements implicitly include these.

- Ladder identity files `src/ptcg/submission_main.py` and `src/ptcg/agents/current.py` remain **byte-unchanged**.
- **SUBMIT_HOLD present at session close** (automated submissions stay frozen through the deadline window) — the curation script re-asserts it on success; no path in this spec removes it.
- No modifications to `tournament.db` beyond what `curate_counted_pair.py` already does by design.
- **Live-on-write inertness:** `watch_once` executes working-tree code on the next ~15-min firing. The snapshot stamp (`experiments/factory/snapshot_stamp.json`) is **pre-seeded (future-dated) and committed BEFORE the watch-loop step code exists on disk** (Task 4 step 1 precedes all Task 4 code steps); activation (stamp reset) is an explicit post-review step (Task 5), verified against a real firing in `watch.log`.
- **Deadline: Kaggle final submissions close 2026-08-16 23:59 UTC (13:59 HST).** Eviction by recency; only the 2 most-recent submissions are counted; 5 uploads/day.
- CLI failure tolerance: a failed snapshot poll appends nothing and never breaks a firing.
- Full suite green **on a quiet machine** before merge (`.claude/rules/dispatch-test-run-directive.md`; orchestrator owns full-suite runs — implementers run only their targeted test file).
- Brad approves submission descriptions BEFORE any upload (`memory/confirm-submission-description.md`).

## Pre-Lock Landmark Verification (performed 2026-08-14 before writing this plan)

| Landmark | Grep/read performed | Verified fact |
|---|---|---|
| `curate_counted_pair.py` flags | `grep -n add_argument scripts/curate_counted_pair.py` | `--best` (required, uploaded LAST), `--second` (required, uploaded FIRST), `--db`, `--ledger`, `--counter`, `--out-dir`, `--hold-file`, `--dry-run` (`store_true`) — lines 84-106. Dry-run prints `DRY-RUN would upload FIRST/LAST : <desc>` and exits 0 with no auth probe/counter/upload/hold. Exit codes: 2 resolve/bundle failure, 3 auth-dead, 7 confirmation snapshot unavailable. |
| Submissions-listing function | read `src/ptcg/factory/kaggle_client.py` | `KaggleClient.list_submissions(self) -> list[SubmissionRow]` (line 91) → `_cli("competitions", "submissions", "-c", self.competition, "--csv")` → `parse_submissions_csv`. `FakeKaggleClient(rows=...)` test double exists (line 150). |
| **DIVERGENCE — `SubmissionRow` has NO `ref` field** | read lines 28-71 | `SubmissionRow(file_name, date, description, status, public_score)`; `parse_submissions_csv` **deliberately drops the `ref` column** ("extra ref/privateScore columns are ignored"). The spec's snapshot schema requires `ref`. Resolution (Task 3): additive trailing defaulted field `ref: str = ""` + `ref=get("ref")` in the parser. Consumer sweep (`grep -rn "SubmissionRow(" src/ scripts/ tests/`): all constructions are ≤5-positional/keyword args across `kaggle_client.py`, `tests/test_curate_counted_pair.py`, `test_factory_cycle.py`, `test_factory_harvest.py`, `test_factory_pairgate.py`, `test_factory_subscheduler.py` — a trailing defaulted field breaks none of them. `curate_counted_pair.py`'s fresh-row check keys on `(r.date, r.description)` (lines 155, 193), not ref — unaffected. |
| `episodes.py` stamp pattern | read lines 60-110, 237-244 | `load_stamp(path) -> str \| None` reads `{"last_day": ...}`; `save_stamp(path, day, now)` does `path.parent.mkdir(parents=True, exist_ok=True)` + `write_text(..., encoding="utf-8")` (virgin-dir safe); checked/written inside `check_and_harvest` (never raises, returns `"no-new" \| "harvested:<day>" \| "error:<msg>"`). Snapshot copies the pattern with an interval key `last_ts` (ISO-8601 UTC) instead of daily `last_day`. |
| `watch_once()` structure + logging idiom | read `scripts/factory_watch_once.py` in full | Signature: `watch_once(paths, client, *, no_submit=False, db_path=None, harvest_fn=episodes.check_and_harvest, submit_fn=subscheduler.maybe_submit, now_fn=_utc_now, log=print) -> dict`. Flow: throttle → PAUSE (`append_watch_log(watch_log, "paused")`) → `instance_lock` → harvest (try/except, markers `episodes: {hres}` / `episodes: error (isolated)`) → SUBMIT_HOLD check (`submit: held (SUBMIT_HOLD present)`, returns `{"harvest": hres, "submit_held": True}`) → `submit: {summary}`. Outer `except Exception` → `cycle-error:` marker. Logging idiom: `append_watch_log(watch_log, f"<step>: <result>")` where `append_watch_log(log_path, msg, now=None)` (`src/ptcg/factory/watch.py:50`, virgin-dir-safe, utf-8). Injected fn convention: `harvest_fn(paths, client, *, now, log)`. |
| Existing watch_once test files | `grep -rln "watch_once" tests/` | `tests/test_factory_watch_cutover.py` is the primary consumer (13 tests asserting step sequence/markers via substring `in log` checks; paused/busy tests use `_refuse_harvest`/`_refuse_submit` stubs). `tests/test_factory_watch.py` covers only `watch.py` helpers (its own docstring: watch_once behavior moved to the cutover file) — no changes needed there. Task 4 must update every direct `watch_once(...)` call in the cutover file to pass a `snapshot_fn` stub (hermeticity) and add `_refuse_snapshot` to paused/busy tests. |
| Fixed-UTC-10 HST precedent | `grep -rn "UTC-10\|_HST" src/ptcg/factory/ui_pages.py` | `ui_pages.py:47-50`: `_HST = dt.timezone(dt.timedelta(hours=-10), "HST")` with the "Hawaii observes no DST, fixed offset permanently exact" comment. This host has no tzdata; `ZoneInfo` raises. Copy this exact pattern. |
| Parser test fixture | read `tests/test_factory_kaggle_client.py:19-46` | Real-shape CSV fixture INCLUDES a `ref` column (`54517391`, ...); tests assert individual fields, never whole-row equality — safe to extend with `rows[0].ref == "54517391"`. |
| `FactoryPaths` | read `src/ptcg/factory/cycle.py:19-80` | `paths.root`, `paths.pause_file`, `paths.submit_hold_file`, `paths.counter`, `paths.log_dir` all exist; `watch_paths()` derives `watch_log = paths.log_dir / "watch.log"`. `check_and_snapshot` needs only `paths.root`. |
| Stamp/output gitignore status | `git check-ignore experiments/factory/snapshot_stamp.json` → exit 1 | NOT ignored — committable. `ladder_snapshots.jsonl` likewise not ignored; it follows the factory no-autocommit convention (accumulates uncommitted, like `extracts.jsonl`). |
| Identity resolvability | spec table (verified 2026-08-14 against live DB/ledger) | `v0.16` = 1 row in `tournament.db` `baselines` (deck `reseed-mega-starmie-water-density20-sv0`); exact string `mega-starmie-water-lean-searchnet-v1.0` present in `experiments/factory/candidates.json`. |

**Other divergences from the task-breakdown sketch (all minor, resolved in-plan):**
1. The sketched `snapshot_due(stamp_path, min_interval_s=14400)` signature gains an explicit `now` parameter (`snapshot_due(stamp_path, now, min_interval_s=14400)`) — required for deterministic tests, consistent with the repo's `now_fn` injection convention.
2. `episodes.py` stamps a DAY (`last_day`); the snapshot stamps an interval timestamp (`last_ts`). Pattern copied, key differs — anticipated by the spec ("min interval 4h").
3. "Counted" semantics decision (spec says "2 most recent active"): `is_counted` = the 2 newest rows whose `status != "ERROR"` (an ERROR upload never occupies a counted slot). Recorded here as the implemented rule.

---

## Task 1 — Probe upload dry-run (operational, no code changes)

**Files:** none created/modified. Read-only + one dry-run invocation.

**Interfaces:**
- Consumes: `scripts/curate_counted_pair.py` CLI (`run(argv, client, ...) -> int`, flags verified in the landmark table).
- Produces: captured dry-run output (two `DRY-RUN would upload ...` description lines + bundle build/verify results) reported to the orchestrator for the Brad gate.

**Steps:**

- [ ] 1. Disk free-space check (spec risk: disk at ~6.7GB free; bundle builds need headroom). Abort threshold **< 3GB free** — if breached, STOP and report to the orchestrator instead of proceeding:

```powershell
Get-PSDrive C | Select-Object @{n='FreeGB';e={[math]::Round($_.Free/1GB,2)}}
```

- [ ] 2. Run the probe dry-run (flags verified against `scripts/curate_counted_pair.py:84-106` — see landmark table). NO upload occurs in this task (`--dry-run`: no auth probe, no counter reservation, no upload, no SUBMIT_HOLD write):

```bash
cd "C:\Users\Brad\Dev Folder\Product\ptcg-ai-battle-challenge-strategy"
uv run python scripts/curate_counted_pair.py --best v0.16 --second mega-starmie-water-lean-searchnet-v1.0 --dry-run
```

- [ ] 3. Capture verbatim from the output: (a) the `DRY-RUN would upload FIRST : <description>` line (second identity, `mega-starmie-water-lean-searchnet-v1.0`), (b) the `DRY-RUN would upload LAST  : <description>` line (best identity, `v0.16`), (c) exit code (must be 0 — exit 2 means identity-resolve or bundle build/verify failed; report the exact `CURATION FAILED (...)` line if so).
- [ ] 4. **STOP. Report both descriptions + bundle results to the orchestrator.** Brad must approve the descriptions before Task 2 runs (`memory/confirm-submission-description.md` standing convention). Do not proceed to Task 2 in this dispatch.

---

## Task 2 — Probe upload execution (operational; runs ONLY after orchestrator confirms Brad approval)

**Files:** none created/modified in the repo tree. Side effects: 2 Kaggle uploads, `experiments/factory/submission_counter.json` reservations, `experiments/factory/SUBMIT_HOLD` (re)set by the script on success.

**Interfaces:**
- Consumes: same CLI as Task 1, minus `--dry-run`; `uvx --from kaggle kaggle competitions submissions -c pokemon-tcg-ai-battle` for independent verification.
- Produces: two new submission refs + initial scores + timestamps, recorded in the report; the script's strict fresh-row confirmation log as the receipt.

**Steps:**

- [ ] 1. Confirm the orchestrator has relayed Brad's approval of BOTH descriptions from Task 1. If not present in the dispatch prompt, STOP and report `brad-gate-unsatisfied`.
- [ ] 2. Re-check disk free space (same command/threshold as Task 1 step 1).
- [ ] 3. Execute the real run (same command WITHOUT `--dry-run`). The script's own gates handle ordering (uploads `--second` FIRST, `--best` LAST — best survives recency-eviction longest) and strict fresh-row confirmation (2026-08-12 gate: a pre-upload snapshot of `(date, description)` pairs; only genuinely fresh rows count):

```bash
cd "C:\Users\Brad\Dev Folder\Product\ptcg-ai-battle-challenge-strategy"
uv run python scripts/curate_counted_pair.py --best v0.16 --second mega-starmie-water-lean-searchnet-v1.0
```

- [ ] 4. Read the exit code and full log. Exit 0 + the script's loud success lines = both uploads confirmed fresh. Nonzero: 3 = auth-dead (check infrastructure FIRST per `.claude/rules/factory-resume-probe.md` 2026-08-12 addendum — disk/network before token); 7 = confirmation snapshot unavailable, nothing uploaded; any failure path logs that SUBMIT_HOLD was NOT set. Per the spec's risk note, retry ONCE on a transient network failure; verification is by fresh-row listing, not exit code.
- [ ] 5. Independent verification — the two new refs must be the two most-recent rows:

```bash
uvx --from kaggle kaggle competitions submissions -c pokemon-tcg-ai-battle | head -8
```

- [ ] 6. Verify expected post-state: `experiments/factory/SUBMIT_HOLD` exists (the script re-asserts it after both uploads confirm — `ls experiments/factory/SUBMIT_HOLD`).
- [ ] 7. Record in the report: both refs, initial `publicScore` values (likely pending/blank at first), upload timestamps (UTC), and the captured fresh-row confirmation output. These refs are inputs to Task 5's PASS criteria and Task 6's decision rule.

---

## Task 3 — Snapshot script + tests

**Files:**
- Create: `scripts/snapshot_ladder_scores.py`
- Create: `tests/test_snapshot_ladder_scores.py`
- Modify: `src/ptcg/factory/kaggle_client.py` (additive `ref` field — see landmark-table divergence)
- Test (modify): `tests/test_factory_kaggle_client.py` (assert `ref` parsing on the existing real-shape fixture)

**Interfaces:**
- Consumes: `KaggleClient.list_submissions(self) -> list[SubmissionRow]` (`src/ptcg/factory/kaggle_client.py:91` — reused, NOT reinvented); `FactoryPaths.root`; the `episodes.py:74-92` stamp pattern; the `ui_pages.py:47-50` `_HST` fixed-offset pattern.
- Produces:
  - `SubmissionRow` gains trailing field `ref: str = ""` (additive; all existing constructions unaffected).
  - `snapshot_path(root: Path) -> Path` → `experiments/factory/ladder_snapshots.jsonl`
  - `stamp_path(root: Path) -> Path` → `experiments/factory/snapshot_stamp.json`
  - `load_stamp(path: Path) -> str | None`, `save_stamp(path: Path, now: dt.datetime) -> None`
  - `snapshot_due(stamp_path: Path, now: dt.datetime, min_interval_s: int = 14400) -> bool`
  - `build_rows(subs: list[SubmissionRow], now: dt.datetime) -> list[dict]`
  - `append_rows(out_path: Path, rows: list[dict]) -> None`
  - `check_and_snapshot(paths, client, *, now: dt.datetime | None = None, log=print) -> str` returning `"not-due" | "ok(N rows)" | "error:<msg>"`, never raises.

**Single-writer reasoning (stated per the task brief):** appends use open-append-close per invocation; the only automated caller is `watch_once`, which is single-instance-locked (`instance_lock` on `watch.lock`), and the manual CLI is a human-run one-off — no concurrent-writer guard is needed.

**Inertness note:** this task's script is unreachable from any live process (nothing imports it until Task 4), so it is inert-by-construction on disk. The stamp pre-seed still happens in Task 4 step 1, BEFORE the watch-loop edit.

**Steps:**

- [ ] 1. Write the failing test file `tests/test_snapshot_ladder_scores.py`:

```python
"""Tests for scripts/snapshot_ladder_scores.py (freeze-pair probe spec
2026-08-14). Covers: ref parsing reuse, counted-flag semantics, stamp gating
(incl. the pre-seeded FUTURE stamp no-op), virgin-directory append (parent NOT
pre-created — the tmp_path lesson), and CLI-failure tolerance (poll failure
appends nothing, stamp untouched, never raises)."""
from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

from ptcg.factory.kaggle_client import FakeKaggleClient, SubmissionRow
from scripts.snapshot_ladder_scores import (
    MIN_INTERVAL_S, build_rows, check_and_snapshot, load_stamp, save_stamp,
    snapshot_due, snapshot_path, stamp_path,
)

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 8, 14, 20, 0, 0, tzinfo=UTC)


def _rows():
    # Newest-first by date; the ERROR row is newest but must NOT be counted.
    return [
        SubmissionRow("s.tar.gz", "2026-08-14 19:00:00", "broken upload",
                      "ERROR", None, ref="900"),
        SubmissionRow("s.tar.gz", "2026-08-14 18:00:00", "best v0.16",
                      "COMPLETE", 510.0, ref="901"),
        SubmissionRow("s.tar.gz", "2026-08-14 17:00:00", "second lean-searchnet",
                      "PENDING", None, ref="902"),
        SubmissionRow("s.tar.gz", "2026-08-12 10:00:00", "evicted old",
                      "COMPLETE", 446.7, ref="903"),
    ]


def _paths(root):
    return SimpleNamespace(root=root)


def test_build_rows_counted_is_two_newest_non_error():
    rows = build_rows(_rows(), NOW)
    by_ref = {r["ref"]: r for r in rows}
    assert by_ref["900"]["is_counted"] is False   # ERROR never counted
    assert by_ref["901"]["is_counted"] is True
    assert by_ref["902"]["is_counted"] is True
    assert by_ref["903"]["is_counted"] is False
    assert by_ref["901"]["public_score"] == 510.0
    assert by_ref["901"]["utc_ts"] == NOW.isoformat()
    # hst_ts is the same instant at fixed UTC-10 (no tzdata on this host)
    assert by_ref["901"]["hst_ts"].endswith("-10:00")
    assert by_ref["901"]["hst_ts"].startswith("2026-08-14T10:00:00")


def test_check_and_snapshot_appends_jsonl_and_saves_stamp(tmp_path):
    paths = _paths(tmp_path)
    client = FakeKaggleClient(rows=_rows())
    res = check_and_snapshot(paths, client, now=NOW)
    assert res == "ok(4 rows)"
    lines = snapshot_path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    assert json.loads(lines[0])["ref"] == "900"
    assert load_stamp(stamp_path(tmp_path)) == NOW.isoformat()


def test_virgin_directory_parent_not_precreated(tmp_path):
    # The tmp_path lesson: point root one level BELOW tmp_path so that
    # experiments/factory (and its parents) genuinely do not exist yet.
    virgin_root = tmp_path / "never" / "created"
    res = check_and_snapshot(_paths(virgin_root),
                             FakeKaggleClient(rows=_rows()), now=NOW)
    assert res == "ok(4 rows)"
    assert snapshot_path(virgin_root).exists()
    assert stamp_path(virgin_root).exists()


def test_stamp_gates_second_poll_until_interval_elapses(tmp_path):
    paths = _paths(tmp_path)
    client = FakeKaggleClient(rows=_rows())
    assert check_and_snapshot(paths, client, now=NOW).startswith("ok(")
    soon = NOW + dt.timedelta(seconds=MIN_INTERVAL_S - 1)
    assert check_and_snapshot(paths, client, now=soon) == "not-due"
    later = NOW + dt.timedelta(seconds=MIN_INTERVAL_S)
    assert check_and_snapshot(paths, client, now=later).startswith("ok(")


def test_preseeded_future_stamp_noops(tmp_path):
    # The Task-4 inertness pre-seed: a FUTURE last_ts must yield not-due.
    sp = stamp_path(tmp_path)
    save_stamp(sp, dt.datetime(2026, 8, 20, 0, 0, 0, tzinfo=UTC))
    assert snapshot_due(sp, NOW) is False
    res = check_and_snapshot(_paths(tmp_path),
                             FakeKaggleClient(rows=_rows()), now=NOW)
    assert res == "not-due"
    assert not snapshot_path(tmp_path).exists()


def test_missing_or_corrupt_stamp_is_due(tmp_path):
    sp = stamp_path(tmp_path)
    assert snapshot_due(sp, NOW) is True                      # missing
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text("{not json", encoding="utf-8")
    assert snapshot_due(sp, NOW) is True                      # corrupt


def test_cli_failure_appends_nothing_and_never_raises(tmp_path):
    class DeadClient:
        def list_submissions(self):
            raise RuntimeError("kaggle CLI failed (1): Authentication required")

    logged = []
    res = check_and_snapshot(_paths(tmp_path), DeadClient(), now=NOW,
                             log=logged.append)
    assert res.startswith("error:")
    assert not snapshot_path(tmp_path).exists()   # appended NOTHING
    assert load_stamp(stamp_path(tmp_path)) is None  # stamp untouched -> retry next firing
    assert logged  # failure was logged, not swallowed silently
```

- [ ] 2. Run it and watch it FAIL (module does not exist yet):

```bash
uv run pytest tests/test_snapshot_ladder_scores.py -x -q
```

- [ ] 3. Additive `ref` support in `src/ptcg/factory/kaggle_client.py`. Edit the dataclass (trailing defaulted field — every existing ≤5-arg construction stays valid):

```python
@dataclass
class SubmissionRow:
    file_name: str
    date: str
    description: str
    status: str
    public_score: float | None
    #: Kaggle's submission ref/id column (additive 2026-08-14, freeze-pair
    #: snapshot logger). Previously dropped by the parser; defaulted so all
    #: pre-existing positional constructions remain valid.
    ref: str = ""
```

  and in `parse_submissions_csv`, extend the row construction:

```python
        rows.append(SubmissionRow(
            file_name=get("fileName", "file_name", "fileNameNullable"),
            date=get("date"),
            description=get("description", "descriptionNullable"),
            status=_parse_status(get("status")),
            public_score=_parse_score(get("publicScore", "public_score",
                                          "publicScoreNullable")),
            ref=get("ref"),
        ))
```

  Also update the module docstring's sentence "extra `ref`/`privateScore` columns are ignored" to "the extra `privateScore` column is ignored; `ref` is captured (2026-08-14) for the ladder snapshot logger".

- [ ] 4. Extend `tests/test_factory_kaggle_client.py` (the existing real-shape fixture already carries a `ref` column). Add to `test_parse_submissions_csv_rows_and_scores`:

```python
    assert rows[0].ref == "54517391"  # ref captured (2026-08-14 additive field)
```

  and add to `test_parse_submissions_csv_tolerates_literal_none_string` (fixture has NO ref column):

```python
    assert rows[0].ref == ""  # absent ref column -> default
```

- [ ] 5. Create `scripts/snapshot_ladder_scores.py`:

```python
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
The watch loop wraps this in a second isolation layer regardless.

Single-writer note: appends are open-append-close per invocation; the only
automated caller is `watch_once`, which is single-instance-locked
(`instance_lock` on watch.lock), and the manual CLI is a human one-off — no
concurrent-writer guard is needed.

Counted semantics: Kaggle counts the 2 most-recent ACTIVE submissions
(eviction by recency); an ERROR row never occupies a counted slot, so
`is_counted` marks the 2 newest rows whose status != "ERROR".

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
    """Return the last-polled ISO-8601 UTC timestamp or None (never polled)."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")).get("last_ts")
    except (FileNotFoundError, ValueError, OSError):
        return None


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
    except ValueError:
        return True  # corrupt stamp -> poll now (success rewrites it)
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
    poll retries on the next firing."""
    now = now or dt.datetime.now(dt.timezone.utc)
    sp = stamp_path(paths.root)
    try:
        if not snapshot_due(sp, now):
            return "not-due"
        rows = build_rows(client.list_submissions(), now)
        append_rows(snapshot_path(paths.root), rows)
        save_stamp(sp, now)
        return f"ok({len(rows)} rows)"
    except Exception as exc:  # noqa: BLE001 - failure tolerance is the point
        log(f"ladder snapshot failed: {exc!r}")
        return f"error:{type(exc).__name__}: {exc}"


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
```

- [ ] 6. Run the targeted tests to PASS (implementer runs ONLY these two files per `.claude/rules/dispatch-test-run-directive.md` — the orchestrator owns full-suite runs):

```bash
uv run pytest tests/test_snapshot_ladder_scores.py tests/test_factory_kaggle_client.py -q
```

- [ ] 7. Commit with explicit pathspec on BOTH add and commit (sibling-agent staging-race rule):

```bash
git add scripts/snapshot_ladder_scores.py tests/test_snapshot_ladder_scores.py src/ptcg/factory/kaggle_client.py tests/test_factory_kaggle_client.py
git commit scripts/snapshot_ladder_scores.py tests/test_snapshot_ladder_scores.py src/ptcg/factory/kaggle_client.py tests/test_factory_kaggle_client.py -m "feat: stamp-gated ladder score-snapshot logger + additive SubmissionRow.ref

Claude-Session: https://claude.ai/code/session_01N3mdFqfZPmV68ZgQXhodiN"
```

---

## Task 4 — Stamp pre-seed, THEN watch-loop integration

**ORDER IS LOAD-BEARING** (live-on-write rule, `.claude/rules/factory-resume-probe.md` during-slice-exposure section): `ptcg-factory-continuous` executes working-tree code every ~15 minutes. Step 1 commits the pre-seeded (future-dated) stamp BEFORE any watch-loop code exists on disk, so the first live firing that runs the new step evaluates `snapshot_due` → `False` and no-ops until the explicit Task-5 activation.

**Files:**
- Create (data): `experiments/factory/snapshot_stamp.json` (pre-seeded, committed FIRST)
- Modify: `scripts/factory_watch_once.py`
- Test (modify): `tests/test_factory_watch_cutover.py`

**Interfaces:**
- Consumes: `check_and_snapshot(paths, client, *, now, log)` from Task 3; `append_watch_log(log_path, msg)` (`src/ptcg/factory/watch.py:50`).
- Produces: `watch_once(...)` gains kwarg `snapshot_fn=check_and_snapshot`; new watch-log markers `snapshot: ok(N rows)` / `snapshot: not-due` / `snapshot: error:<...>` / `snapshot: error (isolated)`; return dicts gain a `"snapshot"` key on all in-lock paths.

**Consumer sweep (test files asserting watch_once's step sequence/output):** `tests/test_factory_watch_cutover.py` is the ONLY watch_once consumer test file (verified by grep; `tests/test_factory_watch.py` covers `watch.py` helpers only and needs no changes). Its 13 tests use substring `in log` assertions and injected `harvest_fn`/`submit_fn` stubs. **Every direct `watch_once(...)` call in that file must gain a `snapshot_fn` argument in THIS task** (a `_snapshot_stub([])` for normal-flow tests; `_refuse_snapshot` for the paused/busy short-circuit tests) so no test silently exercises the real Kaggle-polling default.

**Steps:**

- [ ] 1. **Pre-seed the stamp and commit it BEFORE touching `factory_watch_once.py`.** The seed is future-dated (2026-08-20, past the deadline window) so inertness holds regardless of how long review takes — not merely 4h:

```bash
cd "C:\Users\Brad\Dev Folder\Product\ptcg-ai-battle-challenge-strategy"
uv run python -c "import json, pathlib; p = pathlib.Path('experiments/factory/snapshot_stamp.json'); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps({'last_ts': '2026-08-20T00:00:00+00:00'}), encoding='utf-8'); print(p.read_text(encoding='utf-8'))"
git add experiments/factory/snapshot_stamp.json
git commit experiments/factory/snapshot_stamp.json -m "chore: pre-seed snapshot_stamp.json future-dated (live-on-write inertness; activation is an explicit post-review step)

Claude-Session: https://claude.ai/code/session_01N3mdFqfZPmV68ZgQXhodiN"
```

- [ ] 2. Write the failing tests — add to `tests/test_factory_watch_cutover.py` (helpers mirror the file's existing `_harvest_stub`/`_refuse_harvest` conventions):

```python
def _snapshot_stub(calls, ret="ok(3 rows)", raises=False):
    def snapshot_fn(paths, client, *, now, log=print):
        if raises:
            raise RuntimeError("snapshot boom")
        calls.append(now)
        return ret
    return snapshot_fn


def _refuse_snapshot(paths, client, *, now, log=print):
    raise AssertionError("snapshot_fn must not be called")


def test_snapshot_runs_after_harvest_and_logs_marker(tmp_path):
    paths = _paths(tmp_path)
    scalls = []
    result = watch_once(paths, object(),
                        harvest_fn=_harvest_stub([], ret="no-new"),
                        snapshot_fn=_snapshot_stub(scalls),
                        submit_fn=_submit_stub([], ret=[]))
    assert scalls, "snapshot step never ran"
    log = _log_text(paths)
    assert "snapshot: ok(3 rows)" in log
    assert log.index("episodes:") < log.index("snapshot:") < log.index("submit:")
    assert result["snapshot"] == "ok(3 rows)"


def test_snapshot_failure_isolated_submit_still_runs(tmp_path):
    paths = _paths(tmp_path)
    scalls = []
    result = watch_once(paths, object(),
                        harvest_fn=_harvest_stub([]),
                        snapshot_fn=_snapshot_stub([], raises=True),
                        submit_fn=_submit_stub(scalls, ret=[]))
    assert scalls, "submit must run despite snapshot exception"
    assert "snapshot: error (isolated)" in _log_text(paths)
    assert result["snapshot"] == "error"


def test_snapshot_runs_even_when_submit_hold_present(tmp_path):
    paths = _paths(tmp_path)
    paths.submit_hold_file.parent.mkdir(parents=True, exist_ok=True)
    paths.submit_hold_file.write_text("", encoding="utf-8")
    scalls = []
    result = watch_once(paths, object(),
                        harvest_fn=_harvest_stub([]),
                        snapshot_fn=_snapshot_stub(scalls),
                        submit_fn=_refuse_submit)
    assert scalls, "snapshot is read-only and must run under SUBMIT_HOLD"
    log = _log_text(paths)
    assert "snapshot: ok(3 rows)" in log
    assert "submit: held (SUBMIT_HOLD present)" in log
    assert result == {"harvest": scalls and result["harvest"],
                      "snapshot": "ok(3 rows)", "submit_held": True} or (
        result["snapshot"] == "ok(3 rows)" and result["submit_held"] is True)


def test_snapshot_not_due_marker_logged(tmp_path):
    paths = _paths(tmp_path)
    watch_once(paths, object(),
               harvest_fn=_harvest_stub([]),
               snapshot_fn=_snapshot_stub([], ret="not-due"),
               submit_fn=_submit_stub([], ret=[]))
    assert "snapshot: not-due" in _log_text(paths)
```

  NOTE for the implementer: `test_snapshot_runs_even_when_submit_hold_present`'s final assertion as sketched is awkward — simplify to the two-line form when transcribing (this is the plan flagging its own sketch, per plan-test-arithmetic-sanity discipline):

```python
    assert result["snapshot"] == "ok(3 rows)"
    assert result["submit_held"] is True
```

  Adapt helper/fixture names (`_paths`, `_log_text`, `_harvest_stub`, `_submit_stub`, `_refuse_submit`, and how the existing tests construct paths/hold files) to the file's ACTUAL conventions — read the file first; the sketches above show intent, the file's own idiom wins.

- [ ] 3. Also in `tests/test_factory_watch_cutover.py`, update EVERY existing direct `watch_once(...)` call: pass `snapshot_fn=_snapshot_stub([])` in the normal-flow tests and `snapshot_fn=_refuse_snapshot` in `test_paused_short_circuits_with_terminal_marker` and `test_busy_when_lock_held_with_terminal_marker` (paused/busy short-circuit BEFORE the lock body, so the snapshot must never run there — same guarantee the file already pins for harvest/submit).
- [ ] 4. Run to FAIL (new tests fail on the missing `snapshot_fn` kwarg):

```bash
uv run pytest tests/test_factory_watch_cutover.py -x -q
```

- [ ] 5. Modify `scripts/factory_watch_once.py`. (a) Add the import alongside the other `# noqa: E402` imports:

```python
from scripts.snapshot_ladder_scores import check_and_snapshot  # noqa: E402
```

  (b) Extend the `watch_once` signature:

```python
def watch_once(paths: FactoryPaths, client, *,
               no_submit: bool = False,
               db_path: Path | None = None,
               harvest_fn=episodes.check_and_harvest,
               snapshot_fn=check_and_snapshot,
               submit_fn=subscheduler.maybe_submit,
               now_fn=_utc_now, log=print) -> dict:
```

  (c) Insert the snapshot step between the harvest block and the SUBMIT_HOLD check (inside the `instance_lock` body):

```python
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
```

  (d) Thread `sres` into both in-lock return values:

```python
                return {"harvest": hres, "snapshot": sres, "submit_held": True}
```

```python
            return {"harvest": hres, "snapshot": sres, "submit": results}
```

  (e) Update the module docstring's "ONLY two jobs" list to three (add: "Ladder score snapshot (`scripts/snapshot_ladder_scores.py`) — read-only, stamp-gated 4h, freeze-pair probe spec 2026-08-14") and extend the `watch_once` docstring's numbered flow with the new step between harvest and SUBMIT_HOLD.
  (f) Update `main()`'s result print, which filters `harvest` out of the printed dict, to also filter `snapshot`:

```python
    print(f"watch_once result: { {k: v for k, v in result.items() if k not in ('harvest', 'snapshot')} }")
```

- [ ] 6. Run the targeted tests to PASS:

```bash
uv run pytest tests/test_factory_watch_cutover.py tests/test_snapshot_ladder_scores.py -q
```

- [ ] 7. Sanity-check inertness against the REAL stamp (read-only, no live firing needed): confirm the committed future-dated stamp gates the step —

```bash
uv run python -c "import datetime as dt, pathlib; from scripts.snapshot_ladder_scores import snapshot_due; print('due now?', snapshot_due(pathlib.Path('experiments/factory/snapshot_stamp.json'), dt.datetime.now(dt.timezone.utc)))"
# MUST print: due now? False
```

- [ ] 8. Commit with explicit pathspec:

```bash
git add scripts/factory_watch_once.py tests/test_factory_watch_cutover.py
git commit scripts/factory_watch_once.py tests/test_factory_watch_cutover.py -m "feat: failure-isolated stamp-gated snapshot step in watch_once (inert: stamp pre-seeded future-dated)

Claude-Session: https://claude.ai/code/session_01N3mdFqfZPmV68ZgQXhodiN"
```

---

## Task 5 — Post-review activation + live receipt (go-live rung; runs AFTER per-task reviews complete)

**Files:** Modify (data): `experiments/factory/snapshot_stamp.json` (reset/backdate). No code changes.

**Interfaces:** Consumes the live `ptcg-factory-continuous` firing (~15-min cadence), `experiments/factory/logs/watch.log`, `experiments/factory/ladder_snapshots.jsonl`, and the two probe refs recorded by Task 2.

**Steps:**

- [ ] 1. Preconditions: Task 3 + Task 4 per-task reviews are APPROVED, and Task 2's probe refs are recorded. If either is missing, STOP and report.
- [ ] 2. Reset the stamp so the next firing is due (backdate to a past instant; a successful poll rewrites it to now):

```bash
cd "C:\Users\Brad\Dev Folder\Product\ptcg-ai-battle-challenge-strategy"
uv run python -c "import json, pathlib; p = pathlib.Path('experiments/factory/snapshot_stamp.json'); p.write_text(json.dumps({'last_ts': '2026-08-14T00:00:00+00:00'}), encoding='utf-8'); print('stamp reset:', p.read_text(encoding='utf-8'))"
```

  Record the reset wall-clock time (HST and UTC) in the report — the PASS criteria below are keyed on markers stamped AFTER this instant.

- [ ] 3. Wait for the next real firing (≤ ~15 min; do NOT run `factory_watch_once.py` by hand — the receipt must prove the SCHEDULED path executed the step). Then check:

```bash
tail -8 experiments/factory/logs/watch.log
tail -12 experiments/factory/ladder_snapshots.jsonl
```

- [ ] 4. **PASS criteria (each observable and discriminating — a pre-change state cannot satisfy them, per `.claude/rules/golive-command-preflight.md`):**
  - (a) `watch.log` contains a `snapshot: ok(N rows)` line (N ≥ 1) timestamped AFTER the step-2 reset — a marker string that existed nowhere in `watch.log` before this slice. Record this line's timestamp as `T_A`; criteria (b) and (d) below are keyed on `T_A`, not on the step-2 reset instant, because a pre-existing MANUAL (`--force`) poll earlier in this slice already wrote rows to `ladder_snapshots.jsonl` and a `not-due` line to `watch.log` before this task ran — those pre-existing artifacts must not be allowed to satisfy (b)/(d).
  - (b) `experiments/factory/ladder_snapshots.jsonl` exists and contains rows whose `utc_ts` is at/after `T_A` (i.e. written by the SCHEDULED firing that produced (a), not by the earlier manual run) carrying `ref` values including BOTH probe refs from Task 2, with `is_counted: true` on exactly those two among that firing's rows.
  - (c) The firing that produced (a) also wrote its normal terminal marker (`submit: held (SUBMIT_HOLD present)` — the hold is still on), proving the snapshot step did not disturb the firing.
  - (d) Discriminating stamp-gate check: a SECOND firing timestamped strictly AFTER `T_A` logs `snapshot: not-due` (wait one more ~15-min firing and re-tail the log) — a `not-due` line timestamped before `T_A` (e.g. inherited from the pre-seeded inert stamp period, or from the earlier manual run's own stamp write) does not satisfy this criterion.
- [ ] 5. If (a) is absent after two firing intervals, run the standard liveness triage (`.claude/rules/factory-task-scheduler-liveness.md`: `Get-ScheduledTaskInfo -TaskName ptcg-factory-continuous`, Event-322 check, `watch.lock` age) BEFORE touching code — a missing marker here is more likely a scheduler pathology than a code bug.
- [ ] 6. Report the receipts (verbatim log lines + the two matching JSONL rows). The `ladder_snapshots.jsonl` data file follows the factory no-autocommit convention (left uncommitted, like `extracts.jsonl`); the reset `snapshot_stamp.json` may be committed at the next routine drift reconciliation — do not commit `watch.log`.

---

## Task 6 — Aug-16 final-curation runbook (plan section, no code)

This section IS the deliverable — the Aug-16 session executes it as its go-live rung. **Finish-phase of THAT session must re-verify every command line below against the real script interfaces per `.claude/rules/golive-command-preflight.md`** (`grep -n "add_argument" -A 3 scripts/curate_counted_pair.py`) — flags below verified 2026-08-14; two days of drift are possible.

**Timing:** Aug 16 ~09:00–11:00 HST (deadline 13:59 HST / 23:59 UTC — leaves headroom for one retry cycle).

**Steps for the Aug-16 session:**

- [ ] 1. **Snapshot review** (read-only):

```bash
cd "C:\Users\Brad\Dev Folder\Product\ptcg-ai-battle-challenge-strategy"
tail -50 experiments/factory/ladder_snapshots.jsonl
```

- [ ] 2. **Apply the decision rule (from the spec, verbatim intent):** pick the best two identities by **current-regime evidence** — probe trajectories at ~40h vs. v0.24 frozen 533.3@26h vs. the legacy pair 446/430@46h, **age-adjusted** (later ages read lower; compare nearest-age readings where possible). Freeze-day displayed scores are irrelevant; the goal is the two truly-strongest identities in the counted slots at deadline. Probe readings at ~40h are still pre-convergence — acknowledge the age-confounding explicitly in the recommendation.
- [ ] 3. **Brad gate 1 — final pair:** present the decision-rule table (identity, ref, score@age, nearest-age comparator) via AskUserQuestion; Brad approves the final pair AND which of the two is `--best`.
- [ ] 4. Optional but recommended dry-run first (builds + verifies + prints descriptions, no upload):

```bash
uv run python scripts/curate_counted_pair.py --best <approved-best-id> --second <approved-second-id> --dry-run
```

- [ ] 5. **Brad gate 2 — descriptions:** Brad approves both printed descriptions (`memory/confirm-submission-description.md`).
- [ ] 6. **Final upload** — `--second` uploads FIRST, `--best` uploads LAST (**upload-best-last convention**: the best identity must be the more recent of the counted pair, surviving recency-eviction longest):

```bash
uv run python scripts/curate_counted_pair.py --best <approved-best-id> --second <approved-second-id>
```

  The script's strict fresh-row confirmation (pre-upload snapshot; only genuinely fresh rows count) is the upload receipt; on any failure path it logs loudly and SUBMIT_HOLD is NOT set.
- [ ] 7. **Post-upload verification** (PASS criteria, each observable):

```bash
uvx --from kaggle kaggle competitions submissions -c pokemon-tcg-ai-battle | head -8
```

  - The two intended refs are the two most-recent submissions (CLI listing).
  - The script's fresh-row confirmation passed for both uploads (its own loud success log).
  - `experiments/factory/ladder_snapshots.jsonl` contains a post-upload snapshot row for each new ref (wait for the next due snapshot, or run `uv run python scripts/snapshot_ladder_scores.py --force` for an immediate read-only poll).
  - `experiments/factory/SUBMIT_HOLD` was REWRITTEN by this run, not merely present (the file already exists from today's earlier probe — presence alone is satisfiable by the pre-existing file and has no fail-power per `.claude/rules/golive-command-preflight.md`). PASS requires BOTH: (a) its mtime is later than the first (`--second`) upload's start timestamp recorded in step 6's log, AND (b) its content's embedded UTC timestamp (`curate_counted_pair.py:205-208` writes `... confirmed at <iso-utc>`) matches this run, not an earlier one — `Get-Item experiments/factory/SUBMIT_HOLD | Select LastWriteTime` plus `cat experiments/factory/SUBMIT_HOLD`. This is the session-close invariant.
- [ ] 8. Retry policy (spec risk): on a transient network failure, retry the upload once; trust the fresh-row listing, never the exit code alone.

---

## Self-Review (performed before commit)

- **Spec coverage:** spec §Decision-1 (probe uploads) → Tasks 1–2; §Decision-2 (snapshot logger + watch integration + inertness) → Tasks 3–5; §Decision-3 (probe window, no mid-window decisions) → no task, by design; §Decision-4 + §Go-Live Runbook → Task 6; §Invariants + §Testing + §Risks → Global Constraints and in-task steps. No scope beyond the spec.
- **Placeholder scan:** no TBDs; every code step carries complete code; Task 6's `<approved-*-id>` tokens are deliberate Brad-gate outputs, not placeholders.
- **Signature consistency:** `check_and_snapshot(paths, client, *, now=None, log=print) -> str` is identical in Task 3 (definition), Task 4 (injection as `snapshot_fn`, called `snapshot_fn(paths, client, now=now_fn(), log=log)`), and matches the `harvest_fn(paths, client, *, now, log)` convention. `SubmissionRow.ref` is trailing/defaulted everywhere it appears.
