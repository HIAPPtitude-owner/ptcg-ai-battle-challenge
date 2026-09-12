# Factory Status Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a self-contained, regenerated-on-every-firing HTML dashboard that renders all factory state (ledger, watch log, digests, submission counter, PAUSE, auth health) into one page at `experiments/factory/dashboard.html`.

**Architecture:** A new stdlib-only module `src/ptcg/factory/dashboard.py` reads the existing state files, builds one plain in-memory dict with per-file fault tolerance, and renders a single self-contained HTML string (inline CSS/SVG, no external requests). A failure-isolated render hook fires at the end of every `scripts/factory_watch_once.py` firing (including PAUSE / lock-skip / no-op), and an on-demand CLI (`scripts/render_dashboard.py`) renders the same page on demand. This is a **read-only reporting layer** — it never touches harvester, gate, submit, or any decision logic.

**Tech Stack:** Python standard library only (`pathlib`, `json`, `html`, `re`, `datetime`) — no jinja, no charting library, no CSS framework, no new runtime dependency. Tests in `pytest` (repo's fast suite).

## Global Constraints

Every task's requirements implicitly include this section.

1. **Renderer is stdlib-only** — no jinja, no new dependencies. Output is a single self-contained HTML file at `experiments/factory/dashboard.html` with all CSS/JS/SVG inline and **zero external requests** (no CDN, remote font, or remote image). Added to `.gitignore` (same class as `watch.log` — regenerated runtime output, never committed).
2. **Every file write uses explicit `encoding="utf-8"`.** Windows' cp1252 default truncates-then-crashes on non-ASCII (documented repo incident, 2x). The dashboard HTML contains non-ASCII (the `→` lineage arrow, badges), so this is load-bearing; a test pins it via a write/read round-trip on non-ASCII content plus a source grep-guard.
3. **The render hook must NEVER break a factory cycle.** The call site is wrapped in try/except (`safe_render`), emits exactly one loud log line on failure, and the cycle proceeds. The dashboard renders on EVERY firing including no-op cycles; digest suppression (`suppress_empty_digest`) stays untouched.
4. **Rendering must not shell out** — no PowerShell, no `Get-ScheduledTask`, no scheduler API. Scheduler liveness is inferred from `watch.log` recency only (last line timestamp > 35 minutes old = stale warning).
5. **Complexity glance:** all rendering is O(candidates x history) with ~40 candidates — trivial. The SVG chart code MUST handle empty and single-point `score_history` without any division by zero.

**Spec invariants (verbatim from the design spec, §Invariants):**
- A dashboard render (success or failure) never changes factory cycle outcomes: no candidate status, gate decision, submission, or ledger write may depend on whether `dashboard.py` succeeded.
- `experiments/factory/dashboard.html` is gitignored — regenerated output, never committed.
- The renderer has zero new runtime dependencies beyond the Python standard library.

---

## Grounded landmarks (verified against the real repo 2026-07-20, before lock)

These are confirmed real; implementers may rely on them without re-checking:

- **`Candidate` fields** (`src/ptcg/factory/candidates.py`): `id`, `name`, `version`, `deck` (repo-relative csv path), `agent_kind`, `agent_config`, `provenance`, `priority`, `status`, `novel_axis`, `is_incumbent`, `local_wr` (float|None), `local_games` (int), `local_breakdown` (None or list of `{"baseline": str, "wins": int, "games": int}`), `kaggle_score` (float|None), `submitted_at`, `last_resubmitted_at`, `score_history` (list of `[iso_ts, score]` pairs), `notes`.
- **`Status` enum values**: `queued`, `evaluating`, `evaluated`, `evaluated-below-incumbent`, `submitted`, `scored`, `retired`.
- **`candidates.json`** shape: `{"version": 1, "candidates": [ {candidate dict}, ... ]}`.
- **`submission_counter.json`** shape: `{"date": "YYYY-MM-DD", "count": N}` (confirmed live: `{"date": "2026-07-20", "count": 5}`).
- **`watch.log`** line format (from `append_watch_log` in `src/ptcg/factory/watch.py`): `[{iso_minutes}] {msg}` where `iso_minutes` is naive-local `dt.datetime.now().isoformat(timespec="minutes")`, e.g. `[2026-07-20T12:00] cycle: noop`. Message shapes seen: `paused`, `busy: another firing holds the lock`, `refill: enqueued N`, `training: deck=<name>`, `training failed: <repr>`, `cycle: noop`, `cycle: evaluated=N actions=N digest=<path>`.
- **Digest files** `experiments/factory/digests/cycle-YYYYMMDD-HHMMSS.md` (written by `write_digest()` in `src/ptcg/factory/cycle.py`). Header `# Factory cycle digest - <iso>`, a `Harvest: ...` line, `## Evaluated` list, `## Gate actions` list. An auth-dead cycle renders `- AUTH: auth-dead - <detail>` under `## Gate actions` (source: `submit_candidates()` in `src/ptcg/factory/submit.py` returns `[("AUTH", "auth-dead", auth_detail)]`, rendered by `write_digest` as `- {cid}: {action} - {detail}`).
- **`HARD_DAILY_CAP = 5`** in `src/ptcg/factory/gate.py` (imported for the cap, do not hardcode `5`).
- **`FactoryPaths`** (`src/ptcg/factory/cycle.py`) has `.root`, and properties `.ledger`, `.counter`, `.digest_dir`, `.pause_file`, `.log_dir`. `watch.log` is `paths.log_dir / "watch.log"`.
- **Watch hook site**: `watch_once()` in `scripts/factory_watch_once.py` has three exit points — an early PAUSE return, the normal in-`try` return, and the `except TimeoutError` busy return. All three must render (spec §1(a): "including no-op firings (lock-skip, PAUSE, nothing to do)").

**SPEC-DRIFT flagged and resolved (lineage join key):** the design spec §2 prose says "chase parents by matching a candidate's provenance parent-name against another candidate's `name` field." Verified against the live ledger: candidate `name` carries an agent-kind suffix (e.g. `mega-starmie-water-lean-attacker-down1-heuristic`), so the `deck-matrix:<parent>:<mutation>` provenance token (`mega-starmie-water-lean`) never equals a `name`. The token DOES equal the parent candidate's **deck stem** (`Path(deck).stem`), and only a deck-stem join reproduces the spec's own stated example chain `mega-starmie-water-lean -> ...-attacker-down1 -> ...-attacker-down1-attacker-up1`. This plan therefore joins lineage on **deck stem**, not `name`. This is the sole intentional divergence from the spec prose; behavior matches the spec's example.

---

## File Structure

- **Create** `src/ptcg/factory/dashboard.py` — data assembly (`assemble_state`) + all render functions + `render_dashboard` / `write_dashboard` / `safe_render`. Stdlib-only. Target < 500 lines; functions stay small and single-purpose.
- **Create** `scripts/render_dashboard.py` — on-demand CLI wrapper.
- **Create** `tests/test_factory_dashboard.py` — all unit + isolation + virgin-dir + utf-8 tests.
- **Modify** `scripts/factory_watch_once.py` — import `dashboard`, call `dashboard.safe_render(paths.root, log=log)` at all three firing exit points.
- **Modify** `.gitignore` — add `experiments/factory/dashboard.html`.
- **Modify** `docs/factory-operations.md` — one `## Status dashboard` section.

---

## Task 1: Branch + data-assembly module (`assemble_state`)

**Files:**
- Create: `src/ptcg/factory/dashboard.py`
- Create: `tests/test_factory_dashboard.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - Constants: `STALE_THRESHOLD_MIN = 35`, `FIRING_INTERVAL_MIN = 15`, `BAND_LOW = 520`, `BAND_HIGH = 575`, `COLUMNS = ["Queue", "Evaluate", "Gate", "Submit", "Harvest"]`, `STATUS_COLUMN` (dict mapping each `Status` value string to a column), `CAP` (`= HARD_DAILY_CAP`, imported from `ptcg.factory.gate`).
  - `assemble_state(root: Path, *, now: dt.datetime | None = None) -> dict` — reads all state files with per-file fault tolerance; returns a plain dict with keys: `now` (datetime), `paused` (bool), `counter` (`{"date","count"}` or `None`), `cap` (int), `candidates` (list of raw candidate dicts), `incumbent_id` (str|None), `last_log` (`{"raw","timestamp","summary"}` or `None`), `stale` (bool), `next_expected` (datetime|None), `digests` (list of `{"name","text"}`, newest first, up to 5), `auth_dead` (`{"detail","digest"}` or `None`), `warnings` (list of str).
  - `build_factory(tmp_path, **overrides)` test helper (in the test file) — used by every later test task.

- [ ] **Step 1: Create the branch**

```bash
git checkout -b feature/factory-dashboard
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_factory_dashboard.py`:

```python
import datetime as dt
import json
from pathlib import Path

from ptcg.factory import dashboard


def build_factory(tmp_path, *, candidates=None, counter=None, watch_lines=None,
                  digests=None, pause=False, make_dirs=True):
    """Build a fixture experiments/factory/ tree under tmp_path.

    Returns the repo-root Path (tmp_path). When make_dirs is False, NOTHING is
    created (virgin-directory case) so a first-run render can be exercised.
    """
    root = tmp_path
    fdir = root / "experiments" / "factory"
    if make_dirs:
        (fdir / "digests").mkdir(parents=True, exist_ok=True)
        (fdir / "logs").mkdir(parents=True, exist_ok=True)
    if candidates is not None:
        (fdir / "candidates.json").write_text(
            json.dumps({"version": 1, "candidates": candidates}),
            encoding="utf-8")
    if counter is not None:
        (fdir / "submission_counter.json").write_text(
            json.dumps(counter), encoding="utf-8")
    if watch_lines is not None:
        (fdir / "logs" / "watch.log").write_text(
            "".join(f"{line}\n" for line in watch_lines), encoding="utf-8")
    if digests is not None:
        for name, text in digests:
            (fdir / "digests" / name).write_text(text, encoding="utf-8")
    if pause:
        (fdir / "PAUSE").write_text("", encoding="utf-8")
    return root


def test_assemble_reads_all_state_files():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        now = dt.datetime(2026, 7, 20, 12, 0)
        root = build_factory(
            Path(td),
            candidates=[{"id": "c-v0.1", "name": "c", "version": "v0.1",
                         "deck": "src/ptcg/decks/candidates/c.csv",
                         "agent_kind": "heuristic", "status": "scored",
                         "is_incumbent": True, "local_wr": 0.6, "local_games": 75,
                         "score_history": [["2026-07-19T21:45", 560.0]]}],
            counter={"date": "2026-07-20", "count": 3},
            watch_lines=["[2026-07-20T11:45] cycle: noop"],
            digests=[("cycle-20260720-114500.md", "# d\n\n## Gate actions\n- none\n")],
        )
        state = dashboard.assemble_state(root, now=now)
        assert state["paused"] is False
        assert state["counter"] == {"date": "2026-07-20", "count": 3}
        assert state["cap"] == 5
        assert state["incumbent_id"] == "c-v0.1"
        assert len(state["candidates"]) == 1
        assert state["last_log"]["summary"] == "cycle: noop"
        assert len(state["digests"]) == 1
        assert state["warnings"] == []


def test_assemble_stale_when_log_old():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        now = dt.datetime(2026, 7, 20, 12, 30)
        root = build_factory(Path(td),
                             watch_lines=["[2026-07-20T11:45] cycle: noop"])
        state = dashboard.assemble_state(root, now=now)  # 45 min gap > 35
        assert state["stale"] is True
        assert state["next_expected"] == dt.datetime(2026, 7, 20, 12, 0)


def test_assemble_poisoned_candidates_produces_warning_not_crash():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = build_factory(Path(td))
        (root / "experiments" / "factory" / "candidates.json").write_text(
            "{not valid json", encoding="utf-8")
        state = dashboard.assemble_state(root, now=dt.datetime(2026, 7, 20, 12, 0))
        assert state["candidates"] == []
        assert any("candidates.json" in w for w in state["warnings"])
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_factory_dashboard.py -v`
Expected: FAIL with `AttributeError: module 'ptcg.factory.dashboard' has no attribute 'assemble_state'` (module does not exist yet -> ImportError/ModuleNotFoundError on collection).

- [ ] **Step 4: Write minimal implementation**

Create `src/ptcg/factory/dashboard.py`:

```python
"""Read-only factory status dashboard renderer (design spec 2026-07-20).

Stdlib-only. Reads the factory's existing state files, builds one plain dict
with per-file fault tolerance, and renders a single self-contained HTML page.
Never mutates factory state; a render failure never affects a cycle.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Callable

from ptcg.factory.gate import HARD_DAILY_CAP

STALE_THRESHOLD_MIN = 35
FIRING_INTERVAL_MIN = 15
BAND_LOW = 520
BAND_HIGH = 575
CAP = HARD_DAILY_CAP
COLUMNS = ["Queue", "Evaluate", "Gate", "Submit", "Harvest"]
STATUS_COLUMN = {
    "queued": "Queue",
    "evaluating": "Evaluate",
    "evaluated": "Evaluate",
    "evaluated-below-incumbent": "Gate",
    "retired": "Gate",
    "submitted": "Submit",
    "scored": "Harvest",
}

DASHBOARD_REL = ("experiments", "factory", "dashboard.html")


def _factory_dir(root: Path) -> Path:
    return Path(root) / "experiments" / "factory"


def _read_json(path: Path, warnings: list[str], label: str) -> Any | None:
    """Return parsed JSON or None; append a warning on any read/parse failure.
    A missing file is an expected state (returns None, no warning)."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - fault tolerance is the point
        warnings.append(f"{label}: {exc}")
        return None


def _parse_log_line(line: str) -> dict | None:
    line = line.strip()
    if not (line.startswith("[") and "]" in line):
        return None
    ts_str, _, summary = line[1:].partition("]")
    summary = summary.strip()
    try:
        timestamp = dt.datetime.fromisoformat(ts_str.strip())
    except ValueError:
        timestamp = None
    return {"raw": line, "timestamp": timestamp, "summary": summary}


def assemble_state(root: Path, *, now: dt.datetime | None = None) -> dict:
    root = Path(root)
    now = now or dt.datetime.now()
    fdir = _factory_dir(root)
    warnings: list[str] = []

    paused = (fdir / "PAUSE").exists()

    counter = _read_json(fdir / "submission_counter.json", warnings,
                         "submission_counter.json")

    ledger = _read_json(fdir / "candidates.json", warnings, "candidates.json")
    candidates = list(ledger.get("candidates", [])) if isinstance(ledger, dict) else []
    incumbent_id = next((c.get("id") for c in candidates if c.get("is_incumbent")),
                        None)

    last_log = None
    stale = False
    next_expected = None
    log_path = fdir / "logs" / "watch.log"
    if log_path.exists():
        try:
            lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines()
                     if ln.strip()]
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"watch.log: {exc}")
            lines = []
        if lines:
            parsed = _parse_log_line(lines[-1])
            if parsed is None:
                warnings.append(f"watch.log: unrecognized last line {lines[-1]!r}")
            else:
                last_log = parsed
                ts = parsed["timestamp"]
                if ts is not None:
                    stale = (now - ts).total_seconds() > STALE_THRESHOLD_MIN * 60
                    next_expected = ts + dt.timedelta(minutes=FIRING_INTERVAL_MIN)

    digests: list[dict] = []
    ddir = fdir / "digests"
    if ddir.exists():
        files = sorted((p for p in ddir.glob("cycle-*.md")),
                       key=lambda p: p.name, reverse=True)[:5]
        for p in files:
            try:
                digests.append({"name": p.name,
                                "text": p.read_text(encoding="utf-8")})
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{p.name}: {exc}")

    auth_dead = None
    if digests:
        newest = digests[0]
        for line in newest["text"].splitlines():
            if line.strip().startswith("- AUTH: auth-dead"):
                detail = line.split("auth-dead", 1)[1].lstrip(" -").strip()
                auth_dead = {"detail": detail, "digest": newest["name"]}
                break

    return {
        "now": now,
        "paused": paused,
        "counter": counter if isinstance(counter, dict) else None,
        "cap": CAP,
        "candidates": candidates,
        "incumbent_id": incumbent_id,
        "last_log": last_log,
        "stale": stale,
        "next_expected": next_expected,
        "digests": digests,
        "auth_dead": auth_dead,
        "warnings": warnings,
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_factory_dashboard.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/ptcg/factory/dashboard.py tests/test_factory_dashboard.py
git commit -m "feat: factory dashboard state-assembly module with per-file fault tolerance"
```

---

## Task 2: Status-strip rendering

**Files:**
- Modify: `src/ptcg/factory/dashboard.py`
- Modify: `tests/test_factory_dashboard.py`

**Interfaces:**
- Consumes: `assemble_state` output dict, `CAP`, constants (Task 1).
- Produces: `render_status_strip(state: dict) -> str` — HTML fragment for the top status strip: RUNNING/PAUSED badge, last-firing time + summary, next-expected firing, submission counter `N/5` + UTC-day reset countdown, scheduler-stale warning, persistent auth-dead badge. Uses `html.escape` on all dynamic text.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_factory_dashboard.py`:

```python
def test_status_strip_running_and_counter():
    state = {
        "now": dt.datetime(2026, 7, 20, 12, 0), "paused": False,
        "counter": {"date": "2026-07-20", "count": 3}, "cap": 5,
        "last_log": {"raw": "[2026-07-20T11:45] cycle: noop",
                     "timestamp": dt.datetime(2026, 7, 20, 11, 45),
                     "summary": "cycle: noop"},
        "stale": False, "next_expected": dt.datetime(2026, 7, 20, 12, 0),
        "auth_dead": None,
    }
    html_out = dashboard.render_status_strip(state)
    assert "RUNNING" in html_out
    assert "PAUSED" not in html_out
    assert "3/5" in html_out
    assert "cycle: noop" in html_out


def test_status_strip_paused_badge():
    state = {"now": dt.datetime(2026, 7, 20, 12, 0), "paused": True,
             "counter": None, "cap": 5, "last_log": None, "stale": False,
             "next_expected": None, "auth_dead": None}
    assert "PAUSED" in dashboard.render_status_strip(state)


def test_status_strip_stale_and_auth_dead():
    state = {"now": dt.datetime(2026, 7, 20, 12, 30), "paused": False,
             "counter": {"date": "2026-07-20", "count": 5}, "cap": 5,
             "last_log": {"raw": "[2026-07-20T11:45] cycle: noop",
                          "timestamp": dt.datetime(2026, 7, 20, 11, 45),
                          "summary": "cycle: noop"},
             "stale": True, "next_expected": dt.datetime(2026, 7, 20, 12, 0),
             "auth_dead": {"detail": "kaggle auth check failed",
                           "digest": "cycle-20260720-114500.md"}}
    html_out = dashboard.render_status_strip(state)
    assert "stale" in html_out.lower()
    assert "AUTH-DEAD" in html_out
    assert "kaggle auth check failed" in html_out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_factory_dashboard.py -k status_strip -v`
Expected: FAIL with `AttributeError: module 'ptcg.factory.dashboard' has no attribute 'render_status_strip'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/ptcg/factory/dashboard.py` (add `import html` to the top imports):

```python
def _fmt_ts(ts: dt.datetime | None) -> str:
    return ts.isoformat(timespec="minutes") if ts else "unknown"


def _reset_countdown(now: dt.datetime) -> str:
    """Advisory time until the next midnight (UTC-day cap reset). Computed from
    `now` as given; label notes it is the UTC day."""
    next_midnight = dt.datetime.combine(now.date() + dt.timedelta(days=1),
                                        dt.time.min)
    rem = next_midnight - now
    total_min = max(0, int(rem.total_seconds() // 60))
    return f"{total_min // 60}h {total_min % 60}m"


def render_status_strip(state: dict) -> str:
    parts: list[str] = ['<div class="status-strip">']

    if state["paused"]:
        parts.append('<span class="badge badge-paused">PAUSED</span>')
    else:
        parts.append('<span class="badge badge-running">RUNNING</span>')

    last = state.get("last_log")
    if last:
        parts.append('<span class="strip-item">last firing '
                     f'{html.escape(_fmt_ts(last["timestamp"]))}: '
                     f'{html.escape(last["summary"])}</span>')
    else:
        parts.append('<span class="strip-item">last firing: no data yet</span>')

    if state.get("next_expected"):
        parts.append('<span class="strip-item">next ~'
                     f'{html.escape(_fmt_ts(state["next_expected"]))}</span>')

    counter = state.get("counter")
    count = counter.get("count", 0) if counter else 0
    parts.append('<span class="strip-item">submissions '
                 f'{html.escape(str(count))}/{state["cap"]} '
                 f'(resets in {html.escape(_reset_countdown(state["now"]))} UTC)'
                 '</span>')

    if state.get("stale"):
        parts.append('<span class="badge badge-warn">scheduler stale '
                     f'(&gt;{STALE_THRESHOLD_MIN}m since last firing)</span>')

    auth = state.get("auth_dead")
    if auth:
        parts.append('<span class="badge badge-auth-dead">AUTH-DEAD: '
                     f'{html.escape(auth["detail"])}</span>')

    parts.append('</div>')
    return "".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_factory_dashboard.py -k status_strip -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/factory/dashboard.py tests/test_factory_dashboard.py
git commit -m "feat: factory dashboard status strip (paused/counter/stale/auth-dead)"
```

---

## Task 3: Pipeline-panel rendering (columns, incumbent highlight, lineage, detail)

**Files:**
- Modify: `src/ptcg/factory/dashboard.py`
- Modify: `tests/test_factory_dashboard.py`

**Interfaces:**
- Consumes: `assemble_state` output dict, `COLUMNS`, `STATUS_COLUMN` (Task 1).
- Produces:
  - `deck_stem(deck: str) -> str` — `Path(deck).stem`.
  - `provenance_parent(prov: str) -> str | None` — parent deck-stem token from `deck-matrix:<parent>:<mutation>`, else None.
  - `build_parent_map(candidates: list[dict]) -> dict[str, str]` — deck-stem -> parent-deck-stem edges (only when the parent stem exists among candidates).
  - `lineage_chain(stem: str, parent_map: dict[str, str]) -> list[str]` — root-to-`stem` chain of stems.
  - `parse_commit(notes: str) -> str | None` — 8-hex commit from the `- <hash> - factory` notes convention, else None.
  - `render_pipeline(state: dict) -> str` — 5-column pipeline; each candidate card in its status column, `is_incumbent` highlighted, lineage chain rendered, `<details>` expandable detail (local_wr/games, breakdown, kaggle_score, version, commit).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_factory_dashboard.py`:

```python
def _lineage_fixture_candidates():
    base = "src/ptcg/decks/candidates/"
    return [
        {"id": "root-v1.0", "name": "mega-starmie-water-lean-heuristic",
         "version": "v1.0", "deck": base + "mega-starmie-water-lean.csv",
         "agent_kind": "heuristic", "status": "scored", "is_incumbent": False,
         "provenance": "weekly-review-2026-07-14:starmie-axis",
         "local_wr": 0.52, "local_games": 75, "local_breakdown": None,
         "kaggle_score": 560.0, "score_history": [], "notes": ""},
        {"id": "down1-v0.1",
         "name": "mega-starmie-water-lean-attacker-down1-heuristic",
         "version": "v0.1",
         "deck": base + "mega-starmie-water-lean-attacker-down1.csv",
         "agent_kind": "heuristic", "status": "submitted", "is_incumbent": True,
         "provenance": "deck-matrix:mega-starmie-water-lean:attacker-down1",
         "local_wr": 0.6, "local_games": 75,
         "local_breakdown": [{"baseline": "mega-lucario-fighting",
                              "wins": 45, "games": 75}],
         "kaggle_score": 553.0, "score_history": [],
         "notes": "deck-matrix refill - a1b2c3d4 - factory cycle"},
        {"id": "up1-v0.1",
         "name": "mega-starmie-water-lean-attacker-down1-attacker-up1-heuristic",
         "version": "v0.1",
         "deck": base + "mega-starmie-water-lean-attacker-down1-attacker-up1.csv",
         "agent_kind": "heuristic", "status": "evaluated-below-incumbent",
         "is_incumbent": False,
         "provenance":
             "deck-matrix:mega-starmie-water-lean-attacker-down1:attacker-up1",
         "local_wr": 0.48, "local_games": 75, "local_breakdown": None,
         "kaggle_score": None, "score_history": [], "notes": ""},
    ]


def test_lineage_chain_three_generations():
    cands = _lineage_fixture_candidates()
    pmap = dashboard.build_parent_map(cands)
    chain = dashboard.lineage_chain(
        "mega-starmie-water-lean-attacker-down1-attacker-up1", pmap)
    assert chain == [
        "mega-starmie-water-lean",
        "mega-starmie-water-lean-attacker-down1",
        "mega-starmie-water-lean-attacker-down1-attacker-up1",
    ]


def test_parse_commit_present_and_absent():
    assert dashboard.parse_commit("deck-matrix refill - a1b2c3d4 - factory cycle") \
        == "a1b2c3d4"
    assert dashboard.parse_commit("submitted manually 2026-07-11") is None
    assert dashboard.parse_commit("") is None


def test_pipeline_columns_incumbent_and_detail():
    state = {"candidates": _lineage_fixture_candidates(),
             "incumbent_id": "down1-v0.1"}
    html_out = dashboard.render_pipeline(state)
    # all five columns present
    for col in ["Queue", "Evaluate", "Gate", "Submit", "Harvest"]:
        assert col in html_out
    # incumbent highlight class attached to the incumbent card
    assert "card-incumbent" in html_out
    # lineage arrow (non-ASCII) rendered for the 3rd-gen candidate
    assert "→" in html_out
    # detail panel: breakdown win rate 45/75 = 0.600, and the parsed commit
    assert "0.600" in html_out
    assert "45/75" in html_out
    assert "a1b2c3d4" in html_out
```

Arithmetic check (hand-verified before transcription): breakdown `45/75 = 0.600` exactly; the `<details>` renders win rate to 3 decimals as `0.600`. Commit token `a1b2c3d4` is 8 hex chars in the `- <hash> - factory` shape.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_factory_dashboard.py -k "lineage or parse_commit or pipeline" -v`
Expected: FAIL with `AttributeError: module 'ptcg.factory.dashboard' has no attribute 'build_parent_map'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/ptcg/factory/dashboard.py` (add `import re` to the top imports):

```python
_COMMIT_RE = re.compile(r"-\s*([0-9a-f]{8})\s*-\s*factory")


def deck_stem(deck: str) -> str:
    return Path(str(deck or "")).stem


def provenance_parent(prov: str) -> str | None:
    if prov and prov.startswith("deck-matrix:"):
        parts = prov.split(":")
        if len(parts) >= 3 and parts[1]:
            return parts[1]
    return None


def build_parent_map(candidates: list[dict]) -> dict[str, str]:
    stems = {deck_stem(c.get("deck", "")) for c in candidates}
    parent_map: dict[str, str] = {}
    for c in candidates:
        stem = deck_stem(c.get("deck", ""))
        parent = provenance_parent(c.get("provenance", ""))
        if parent and parent in stems and parent != stem:
            parent_map[stem] = parent
    return parent_map


def lineage_chain(stem: str, parent_map: dict[str, str]) -> list[str]:
    chain = [stem]
    seen = {stem}
    cur = stem
    while cur in parent_map:
        cur = parent_map[cur]
        if cur in seen:  # cycle guard
            break
        chain.append(cur)
        seen.add(cur)
    chain.reverse()
    return chain


def parse_commit(notes: str) -> str | None:
    m = _COMMIT_RE.search(notes or "")
    return m.group(1) if m else None


def _render_detail(c: dict) -> str:
    rows: list[str] = []
    wr = c.get("local_wr")
    if wr is not None:
        rows.append(f'<div>local_wr {wr:.3f} / {c.get("local_games", 0)} games</div>')
    breakdown = c.get("local_breakdown")
    if breakdown:
        for b in breakdown:
            games = b.get("games", 0)
            rate = (b.get("wins", 0) / games) if games else 0.0
            rows.append('<div class="bd">'
                        f'{html.escape(str(b.get("baseline", "?")))}: '
                        f'{rate:.3f} ({b.get("wins", 0)}/{games})</div>')
    ks = c.get("kaggle_score")
    if ks is not None:
        rows.append(f'<div>kaggle_score {html.escape(str(ks))}</div>')
    rows.append(f'<div>version {html.escape(str(c.get("version", "?")))}</div>')
    commit = parse_commit(c.get("notes", ""))
    if commit:
        rows.append(f'<div>commit {html.escape(commit)}</div>')
    return "".join(rows)


def _render_card(c: dict, incumbent_id: str | None,
                 parent_map: dict[str, str]) -> str:
    cls = "card"
    if incumbent_id and c.get("id") == incumbent_id:
        cls += " card-incumbent"
    chain = lineage_chain(deck_stem(c.get("deck", "")), parent_map)
    lineage_html = html.escape(" → ".join(chain))
    return (f'<div class="{cls}">'
            f'<div class="card-id">{html.escape(str(c.get("id", "?")))}</div>'
            f'<div class="lineage">{lineage_html}</div>'
            f'<details><summary>details</summary>{_render_detail(c)}</details>'
            '</div>')


def render_pipeline(state: dict) -> str:
    candidates = state.get("candidates", [])
    incumbent_id = state.get("incumbent_id")
    parent_map = build_parent_map(candidates)
    buckets: dict[str, list[str]] = {col: [] for col in COLUMNS}
    for c in candidates:
        col = STATUS_COLUMN.get(str(c.get("status", "")), "Queue")
        buckets[col].append(_render_card(c, incumbent_id, parent_map))
    cols_html = []
    for col in COLUMNS:
        cards = "".join(buckets[col]) or '<div class="empty">none</div>'
        cols_html.append(f'<div class="col"><h3>{col} '
                         f'({len(buckets[col])})</h3>{cards}</div>')
    return f'<div class="pipeline">{"".join(cols_html)}</div>'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_factory_dashboard.py -k "lineage or parse_commit or pipeline" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/factory/dashboard.py tests/test_factory_dashboard.py
git commit -m "feat: factory dashboard pipeline panel with lineage chain + detail"
```

---

## Task 4: History SVG chart (empty/single-point safe, 520-575 band)

**Files:**
- Modify: `src/ptcg/factory/dashboard.py`
- Modify: `tests/test_factory_dashboard.py`

**Interfaces:**
- Consumes: `BAND_LOW`, `BAND_HIGH` (Task 1).
- Produces:
  - `render_score_chart(score_history: list, *, width: int = 320, height: int = 120) -> str` — inline `<svg>` polyline of `[iso_ts, score]` points with a shaded 520-575 band; empty history returns a `no score history yet` placeholder div; single point renders band + one dot and NO polyline (no division by `n-1`).
  - `render_history(state: dict) -> str` — a `<section>` with one labelled chart per candidate that has a non-empty `score_history`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_factory_dashboard.py`:

```python
def test_score_chart_empty_is_safe():
    out = dashboard.render_score_chart([])
    assert "no score history yet" in out
    assert "<svg" not in out  # no chart, no crash


def test_score_chart_single_point_no_polyline():
    out = dashboard.render_score_chart([["2026-07-19T21:45", 560.0]])
    assert "<svg" in out
    assert "chart-band" in out       # band always drawn
    assert "<polyline" not in out    # single point: no line, no div-by-zero
    assert "<circle" in out


def test_score_chart_multi_point_has_polyline_and_band():
    out = dashboard.render_score_chart(
        [["2026-07-19T21:45", 560.0], ["2026-07-20T11:45", 545.0]])
    assert "<polyline" in out
    assert "chart-band" in out


def test_score_chart_all_equal_scores_no_zero_division():
    # equal scores -> zero score-range; must not raise
    out = dashboard.render_score_chart(
        [["t1", 550.0], ["t2", 550.0], ["t3", 550.0]])
    assert "<svg" in out


def test_render_history_one_chart_per_scored_candidate():
    state = {"candidates": [
        {"id": "a-v0.1", "score_history": [["t1", 560.0], ["t2", 550.0]]},
        {"id": "b-v0.1", "score_history": []},  # skipped: no history
    ]}
    out = dashboard.render_history(state)
    assert "a-v0.1" in out
    assert out.count("<svg") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_factory_dashboard.py -k "chart or history" -v`
Expected: FAIL with `AttributeError: module 'ptcg.factory.dashboard' has no attribute 'render_score_chart'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/ptcg/factory/dashboard.py`:

```python
def render_score_chart(score_history: list, *, width: int = 320,
                       height: int = 120) -> str:
    pad = 10
    pts: list[tuple[str, float]] = []
    for entry in score_history or []:
        try:
            ts, score = entry[0], float(entry[1])
        except (TypeError, ValueError, IndexError):
            continue
        pts.append((ts, score))
    if not pts:
        return '<div class="chart-empty">no score history yet</div>'

    scores = [s for _, s in pts]
    lo = min(scores + [BAND_LOW])
    hi = max(scores + [BAND_HIGH])
    if hi <= lo:                      # all-equal / degenerate y-range guard
        hi = lo + 1.0
    n = len(pts)

    def y_of(v: float) -> float:
        return pad + (height - 2 * pad) * (1 - (v - lo) / (hi - lo))

    def x_of(i: int) -> float:
        if n == 1:                    # single point: center, never divide by n-1
            return width / 2
        return pad + (width - 2 * pad) * (i / (n - 1))

    band_top = y_of(BAND_HIGH)
    band_bot = y_of(BAND_LOW)
    parts = [f'<svg class="chart" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="score history">']
    parts.append(f'<rect class="chart-band" x="0" y="{band_top:.1f}" '
                 f'width="{width}" height="{max(0.0, band_bot - band_top):.1f}"/>')
    if n >= 2:
        poly = " ".join(f"{x_of(i):.1f},{y_of(s):.1f}"
                        for i, (_, s) in enumerate(pts))
        parts.append(f'<polyline class="chart-line" points="{poly}"/>')
    for i, (_, s) in enumerate(pts):
        parts.append(f'<circle class="chart-dot" cx="{x_of(i):.1f}" '
                     f'cy="{y_of(s):.1f}" r="2.5"><title>'
                     f'{html.escape(str(s))}</title></circle>')
    parts.append("</svg>")
    return "".join(parts)


def render_history(state: dict) -> str:
    rows: list[str] = []
    for c in state.get("candidates", []):
        hist = c.get("score_history") or []
        if not hist:
            continue
        rows.append('<div class="hist-row">'
                    f'<div class="hist-label">{html.escape(str(c.get("id", "?")))}'
                    '</div>'
                    f'{render_score_chart(hist)}</div>')
    body = "".join(rows) or '<div class="empty">no scored candidates yet</div>'
    return f'<section class="history"><h2>Ladder score history</h2>{body}</section>'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_factory_dashboard.py -k "chart or history" -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/factory/dashboard.py tests/test_factory_dashboard.py
git commit -m "feat: factory dashboard inline SVG score-history chart (band + degenerate-safe)"
```

---

## Task 5: Digests panel + full-page assembly + render/write/safe_render

**Files:**
- Modify: `src/ptcg/factory/dashboard.py`
- Modify: `tests/test_factory_dashboard.py`

**Interfaces:**
- Consumes: `assemble_state`, `render_status_strip`, `render_pipeline`, `render_history` (Tasks 1-4), `DASHBOARD_REL`.
- Produces:
  - `render_digests(state: dict) -> str` — last ~5 digests as simple HTML (headers/lists only).
  - `render_page(state: dict) -> str` — full self-contained HTML document: `<meta http-equiv="refresh" content="60">`, inline `<style>`, a warnings banner when `state["warnings"]` is non-empty, then status strip + pipeline + history + digests.
  - `render_dashboard(root: Path, *, now: dt.datetime | None = None) -> str` — `assemble_state` then `render_page`.
  - `write_dashboard(root: Path, *, now=None, log: Callable = print) -> Path` — render and write to `experiments/factory/dashboard.html` with `encoding="utf-8"` (mkdir parents); returns the path. May raise.
  - `safe_render(root: Path, *, log: Callable = print) -> Path | None` — wraps `write_dashboard` in try/except; on failure logs exactly one loud line and returns None. The failure-isolated hook.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_factory_dashboard.py`:

```python
def test_render_digests_markdown_and_auth_line():
    state = {"digests": [{"name": "cycle-20260720-114500.md",
                          "text": "# Factory cycle digest\n\n## Gate actions\n"
                                  "- AUTH: auth-dead - kaggle auth check failed\n"}]}
    out = dashboard.render_digests(state)
    assert "Gate actions" in out
    assert "kaggle auth check failed" in out


def test_render_page_full_document():
    state = {
        "now": dt.datetime(2026, 7, 20, 12, 0), "paused": False,
        "counter": {"date": "2026-07-20", "count": 3}, "cap": 5,
        "candidates": [], "incumbent_id": None,
        "last_log": {"raw": "[2026-07-20T12:00] cycle: noop",
                     "timestamp": dt.datetime(2026, 7, 20, 12, 0),
                     "summary": "cycle: noop"},
        "stale": False, "next_expected": dt.datetime(2026, 7, 20, 12, 15),
        "digests": [], "auth_dead": None, "warnings": [],
    }
    out = dashboard.render_page(state)
    assert out.lstrip().lower().startswith("<!doctype html>")
    assert 'http-equiv="refresh"' in out
    assert "content=\"60\"" in out
    assert "http://" not in out and "https://" not in out  # zero external requests


def test_render_page_warning_banner():
    state = dict(_page_state_stub(), warnings=["candidates.json: boom"])
    out = dashboard.render_page(state)
    assert "warning-banner" in out
    assert "candidates.json: boom" in out


def _page_state_stub():
    return {"now": dt.datetime(2026, 7, 20, 12, 0), "paused": False,
            "counter": None, "cap": 5, "candidates": [], "incumbent_id": None,
            "last_log": None, "stale": False, "next_expected": None,
            "digests": [], "auth_dead": None, "warnings": []}


def test_write_dashboard_utf8_roundtrip(tmp_path):
    root = build_factory(
        tmp_path,
        candidates=[{"id": "x-v0.1", "name": "x", "version": "v0.1",
                     "deck": "src/ptcg/decks/candidates/x.csv",
                     "agent_kind": "heuristic", "status": "queued",
                     "is_incumbent": False, "provenance": "seed",
                     "score_history": [], "notes": ""}],
        counter={"date": "2026-07-20", "count": 0},
        watch_lines=["[2026-07-20T12:00] cycle: noop"])
    out_path = dashboard.write_dashboard(root, now=dt.datetime(2026, 7, 20, 12, 0))
    assert out_path == root / "experiments" / "factory" / "dashboard.html"
    text = out_path.read_text(encoding="utf-8")  # decodes cleanly as utf-8
    assert "<!doctype html>" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_factory_dashboard.py -k "digests or render_page or utf8" -v`
Expected: FAIL with `AttributeError: module 'ptcg.factory.dashboard' has no attribute 'render_digests'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/ptcg/factory/dashboard.py`:

```python
def _md_to_html(text: str) -> str:
    out: list[str] = []
    for raw in text.splitlines():
        s = raw.rstrip()
        if s.startswith("## "):
            out.append(f"<h4>{html.escape(s[3:])}</h4>")
        elif s.startswith("# "):
            out.append(f"<h3>{html.escape(s[2:])}</h3>")
        elif s.startswith("- "):
            out.append(f"<li>{html.escape(s[2:])}</li>")
        elif s.strip() == "":
            continue
        else:
            out.append(f"<p>{html.escape(s)}</p>")
    return "\n".join(out)


def render_digests(state: dict) -> str:
    digests = state.get("digests", [])
    if not digests:
        return ('<section class="digests"><h2>Recent digests</h2>'
                '<div class="empty">no digests yet</div></section>')
    blocks = []
    for d in digests:
        blocks.append('<div class="digest">'
                      f'<div class="digest-name">{html.escape(d["name"])}</div>'
                      f'{_md_to_html(d["text"])}</div>')
    return f'<section class="digests"><h2>Recent digests</h2>{"".join(blocks)}</section>'


_STYLE = """
:root { color-scheme: light dark; }
body { font-family: system-ui, sans-serif; margin: 1rem; line-height: 1.4; }
h1 { font-size: 1.3rem; }
.status-strip { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center;
  padding: .5rem; border: 1px solid #8888; border-radius: 6px; margin-bottom: 1rem; }
.badge { padding: .15rem .5rem; border-radius: 4px; font-weight: 600;
  font-size: .85rem; }
.badge-running { background: #1b7f3722; color: #1b7f37; }
.badge-paused { background: #b4530022; color: #b45300; }
.badge-warn { background: #b4000022; color: #b40000; }
.badge-auth-dead { background: #b40000; color: #fff; }
.strip-item { font-size: .85rem; opacity: .85; }
.pipeline { display: flex; gap: .75rem; overflow-x: auto; }
.col { flex: 1 1 0; min-width: 160px; border: 1px solid #8884; border-radius: 6px;
  padding: .4rem; }
.col h3 { font-size: .9rem; margin: .2rem 0 .5rem; }
.card { border: 1px solid #8884; border-radius: 4px; padding: .35rem;
  margin-bottom: .4rem; font-size: .8rem; }
.card-incumbent { border: 2px solid #1b7f37; background: #1b7f3711; }
.card-id { font-weight: 600; word-break: break-all; }
.lineage { opacity: .7; font-size: .72rem; word-break: break-all; }
.bd { opacity: .8; }
.history .hist-row { display: flex; gap: .5rem; align-items: center;
  flex-wrap: wrap; margin-bottom: .3rem; }
.hist-label { font-size: .8rem; min-width: 220px; word-break: break-all; }
.chart-band { fill: #1b7f3722; }
.chart-line { fill: none; stroke: #3b82f6; stroke-width: 1.5; }
.chart-dot { fill: #3b82f6; }
.chart-empty, .empty { opacity: .6; font-size: .8rem; }
.warning-banner { background: #b40000; color: #fff; padding: .5rem;
  border-radius: 6px; margin-bottom: 1rem; }
.digest { border-top: 1px solid #8884; padding: .4rem 0; font-size: .8rem; }
.digest-name { font-weight: 600; }
section { margin-top: 1.25rem; }
"""


def render_page(state: dict) -> str:
    warnings = state.get("warnings", [])
    banner = ""
    if warnings:
        items = "".join(f"<li>{html.escape(w)}</li>" for w in warnings)
        banner = (f'<div class="warning-banner">Parse warnings (page rendered '
                  f'from what parsed):<ul>{items}</ul></div>')
    generated = html.escape(state["now"].isoformat(timespec="minutes"))
    return (
        "<!doctype html>\n<html lang=\"en\"><head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<meta http-equiv=\"refresh\" content=\"60\">"
        "<title>Factory status dashboard</title>"
        f"<style>{_STYLE}</style></head><body>"
        f"<h1>Factory status dashboard <small>generated {generated}</small></h1>"
        f"{banner}"
        f"{render_status_strip(state)}"
        f"<section class=\"pipeline-section\"><h2>Pipeline</h2>"
        f"{render_pipeline(state)}</section>"
        f"{render_history(state)}"
        f"{render_digests(state)}"
        "</body></html>"
    )


def render_dashboard(root: Path, *, now: dt.datetime | None = None) -> str:
    return render_page(assemble_state(root, now=now))


def write_dashboard(root: Path, *, now: dt.datetime | None = None,
                    log: Callable = print) -> Path:
    html_str = render_dashboard(root, now=now)
    out_path = Path(root).joinpath(*DASHBOARD_REL)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_str, encoding="utf-8")
    return out_path


def safe_render(root: Path, *, log: Callable = print) -> Path | None:
    """Failure-isolated render hook (spec §3): never raises, logs one loud line
    on failure, returns None. A broken renderer must never fail a factory cycle."""
    try:
        return write_dashboard(root, log=log)
    except Exception as exc:  # noqa: BLE001 - isolation is the whole point
        log(f"dashboard render failed (cycle unaffected): {exc!r}")
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_factory_dashboard.py -k "digests or render_page or utf8" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/factory/dashboard.py tests/test_factory_dashboard.py
git commit -m "feat: factory dashboard full-page assembly + write/safe_render (utf-8)"
```

---

## Task 6: Integration — watch hook, .gitignore, failure-isolation + virgin-dir tests

**Files:**
- Modify: `scripts/factory_watch_once.py`
- Modify: `.gitignore`
- Modify: `tests/test_factory_dashboard.py`

**Interfaces:**
- Consumes: `dashboard.safe_render(root, *, log)` (Task 5), `FactoryPaths.root`.
- Produces: render call wired at all three `watch_once()` firing exit points.

**Landmark note (verify before editing):** `scripts/factory_watch_once.py` `watch_once()` has three returns — the PAUSE early return (`return {"paused": True}`), the normal in-`try` return (`return result`), and the busy return in `except TimeoutError` (`return {"busy": True}`). The design spec §1(a) wording says "wire at the end of `watch_once()` ... inside the same try block"; the plan intentionally wires ALL THREE sites (not just the in-`try` one) because the spec ALSO requires rendering on PAUSE and lock-skip firings ("including no-op firings (lock-skip, PAUSE, nothing to do)"). `safe_render` is the failure-isolation wrapper, so each call site stays a single safe line.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_factory_dashboard.py`:

```python
def test_safe_render_poisoned_state_does_not_raise(tmp_path):
    root = build_factory(tmp_path,
                         counter={"date": "2026-07-20", "count": 0},
                         watch_lines=["[2026-07-20T12:00] cycle: noop"])
    (root / "experiments" / "factory" / "candidates.json").write_text(
        "{not json", encoding="utf-8")
    logged: list[str] = []
    out = dashboard.safe_render(root, log=logged.append)  # must NOT raise
    assert out is not None            # partial page still written
    text = out.read_text(encoding="utf-8")
    assert "warning-banner" in text
    assert "candidates.json" in text


def test_render_virgin_directory_first_run(tmp_path):
    # NOTHING pre-created: no experiments/factory dir at all (virgin-dir rule).
    root = tmp_path / "fresh_repo"          # this dir does not exist yet
    out = dashboard.write_dashboard(root, now=dt.datetime(2026, 7, 20, 12, 0))
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "RUNNING" in text                # not paused, all "no data yet"
    assert "no data yet" in text


def test_write_uses_utf8_source_guard():
    # source grep-guard: every write in dashboard.py passes encoding="utf-8".
    src = Path(dashboard.__file__).read_text(encoding="utf-8")
    for line in src.splitlines():
        if ".write_text(" in line and "encoding=" not in line:
            raise AssertionError(f"write_text without encoding: {line.strip()}")
```

Note the virgin-directory test points `root` at `tmp_path / "fresh_repo"`, which pytest does NOT pre-create — this exercises the real first-run path (`write_dashboard` must `mkdir(parents=True)` the whole chain), per the repo's virgin-directory lesson (documented incident 2x).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_factory_dashboard.py -k "poisoned or virgin or utf8_source" -v`
Expected: PASS for `poisoned`/`virgin` (they exercise Task-5 code) but confirm `test_write_uses_utf8_source_guard` PASSES too. If any FAIL, fix the underlying code before wiring the hook. (These are regression pins for the isolation + utf-8 invariants; they should already pass against Task 5's implementation — run them to prove it, then proceed to wire the hook.)

- [ ] **Step 3: Wire the render hook into `watch_once()`**

In `scripts/factory_watch_once.py`, add the dashboard import to the existing factory imports block (after the `from ptcg.factory.watch import (...)` block):

```python
from ptcg.factory import dashboard  # noqa: E402
```

Then add `dashboard.safe_render(paths.root, log=log)` at each of the three exit points. The PAUSE branch becomes:

```python
    if paths.pause_file.exists():
        append_watch_log(wp["watch_log"], "paused")
        dashboard.safe_render(paths.root, log=log)
        return {"paused": True}
```

The normal in-`try` return becomes (immediately after the existing `result = dict(result, refilled=len(new))` line, before `return result`):

```python
            result = dict(result, refilled=len(new))
            dashboard.safe_render(paths.root, log=log)
            return result
```

The busy branch becomes:

```python
    except TimeoutError:
        append_watch_log(wp["watch_log"], "busy: another firing holds the lock")
        dashboard.safe_render(paths.root, log=log)
        return {"busy": True}
```

- [ ] **Step 4: Add the .gitignore entry**

Append to `.gitignore` (after the existing `experiments/data/` block):

```
# Factory status dashboard (regenerated runtime output, same class as watch.log)
experiments/factory/dashboard.html
```

- [ ] **Step 5: Run the integration tests + confirm gitignore**

Run: `uv run pytest tests/test_factory_dashboard.py tests/test_factory_watch.py -v`
Expected: PASS (all dashboard tests + the existing watch tests stay green — the hook is a single failure-isolated call and does not change any return value).

Run: `git check-ignore experiments/factory/dashboard.html`
Expected: prints `experiments/factory/dashboard.html` (confirms it is ignored).

- [ ] **Step 6: Commit**

```bash
git add scripts/factory_watch_once.py .gitignore tests/test_factory_dashboard.py
git commit -m "feat: wire failure-isolated dashboard render into watch loop + gitignore output"
```

---

## Task 7: On-demand CLI + docs

**Files:**
- Create: `scripts/render_dashboard.py`
- Modify: `docs/factory-operations.md`
- Modify: `tests/test_factory_dashboard.py`

**Interfaces:**
- Consumes: `dashboard.write_dashboard(root, *, log)` (Task 5), `FactoryPaths` (for the default repo root via `ROOT`).
- Produces: `scripts/render_dashboard.py` with a `main()` that renders against a `--root` (default: repo root) and prints the output path. Importable as `from scripts.render_dashboard import main` (per `pyproject.toml` `pythonpath = ["src", "."]`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_factory_dashboard.py`:

```python
def test_render_dashboard_cli_writes_file(tmp_path, capsys):
    from scripts.render_dashboard import render_for_root
    root = build_factory(tmp_path, counter={"date": "2026-07-20", "count": 0},
                         watch_lines=["[2026-07-20T12:00] cycle: noop"])
    out = render_for_root(root)
    assert out.exists()
    assert out.read_text(encoding="utf-8").lower().startswith("<!doctype html>")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_factory_dashboard.py -k cli -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.render_dashboard'`.

- [ ] **Step 3: Write the CLI**

Create `scripts/render_dashboard.py`:

```python
"""CLI: render the factory status dashboard on demand (design spec 2026-07-20).

Writes a self-contained HTML page to <root>/experiments/factory/dashboard.html.
Read-only: touches no factory decision state. Run between watch firings to
inspect current state without waiting for the next 15-minute cycle."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ptcg.factory import dashboard  # noqa: E402


def render_for_root(root: Path) -> Path:
    return dashboard.write_dashboard(Path(root))


def main() -> None:
    p = argparse.ArgumentParser(description="Render the factory status dashboard.")
    p.add_argument("--root", type=Path, default=ROOT,
                   help="repo root (defaults to this repo)")
    args = p.parse_args()
    out = render_for_root(args.root)
    print(f"dashboard written: {out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_factory_dashboard.py -k cli -v`
Expected: PASS.

- [ ] **Step 5: Add the docs section**

Append to `docs/factory-operations.md` a new section (place it after the `## Reading a cycle digest (audit trail)` section):

```markdown
## Status dashboard

The factory renders a single self-contained HTML status page to
`experiments/factory/dashboard.html` at the end of **every** watch-loop firing
(including paused / lock-skipped / no-op firings, so its "last updated"
timestamp stays honest). The page has zero external requests (inline CSS/SVG),
no runtime dependencies beyond the standard library, and is **gitignored** as
regenerated runtime output. It shows: a RUNNING/PAUSED badge, last-firing
summary and next-expected firing (inferred from `watch.log` recency — a
`>35 min` gap raises a "scheduler stale" warning without querying Task
Scheduler), the `N/5` submission counter, a persistent AUTH-DEAD badge when the
newest digest recorded a `- AUTH: auth-dead` line, a queue -> evaluate -> gate
-> submit -> harvest pipeline with the incumbent highlighted and deck-matrix
lineage chains, per-candidate ladder score-history charts (with the 520-575
convergence band shaded), and the last ~5 cycle digests. Rendering is
failure-isolated: a bug in the renderer logs one line and the cycle proceeds
untouched. Render it on demand between firings with:

    uv run python scripts/render_dashboard.py

Then open `experiments/factory/dashboard.html` in a browser (it auto-refreshes
every 60 seconds).
```

- [ ] **Step 6: Commit**

```bash
git add scripts/render_dashboard.py docs/factory-operations.md tests/test_factory_dashboard.py
git commit -m "feat: on-demand render_dashboard CLI + factory-operations docs"
```

---

## Task 8: Final gate — full suite + end-to-end render + manual smoke

**Files:**
- No new source; this task verifies the whole slice.

**Interfaces:**
- Consumes: everything above.
- Produces: a proven-green slice and a manual-smoke artifact for Brad.

- [ ] **Step 1: Run the full fast suite**

Run: `uv run pytest`
Expected: PASS, zero failures. Confirm the new `tests/test_factory_dashboard.py` tests are collected (nonzero count) and every pre-existing test still passes (the only production change outside `dashboard.py` is one failure-isolated call added to `watch_once()` at three sites).

- [ ] **Step 2: Fixture-driven end-to-end render against real repo state**

Run:

```bash
uv run python scripts/render_dashboard.py
```

Expected: prints `dashboard written: .../experiments/factory/dashboard.html`. Then verify the output is a complete, self-contained page rendered from the REAL live ledger (40 candidates, incumbent `mega-starmie-water-lean-attacker-down1-heuristic-v0.1`, counter `5/5` as of 2026-07-20):

```bash
grep -c "card-incumbent" experiments/factory/dashboard.html   # >= 1
grep -c "https://" experiments/factory/dashboard.html         # 0 (no external req)
grep -o "submissions [0-9]*/5" experiments/factory/dashboard.html | head -1
```

Expected: `card-incumbent` count >= 1, zero `https://`, and a `submissions N/5` string. Confirm the file is NOT staged: `git status --short experiments/factory/dashboard.html` shows it ignored (no entry).

- [ ] **Step 3: Manual smoke instructions for Brad**

Present to Brad (this rung's real-eyes verification per the Smoke Test Ladder):

> Dashboard is live. To eyeball it: open
> `experiments/factory/dashboard.html` in a browser (double-click or
> `start experiments\factory\dashboard.html` in PowerShell). Confirm:
> (1) the status strip shows RUNNING (or PAUSED — the `PAUSE` file is
> currently present for the weekly review, so PAUSED is expected today),
> (2) the pipeline columns are populated with the incumbent highlighted,
> (3) the incumbent's score-history chart shows the 520-575 band, and
> (4) the page auto-refreshes ~every 60s. Leave a browser tab open across
> the next watch firing to confirm the "last updated" timestamp advances.

- [ ] **Step 4: Final commit (if any residue)**

No code changes expected here. If the suite surfaced a fix, commit it by explicit path with a `fix:` message. Otherwise this task produces no commit.

---

## Self-Review (completed at plan-write time)

**1. Spec coverage (each design-spec section -> task):**
- §1 Architecture / state files read -> Task 1 (`assemble_state`, per-file fault tolerance). ✓
- §1 Output file + gitignore -> Task 5 (`write_dashboard`, utf-8) + Task 6 (.gitignore). ✓
- §1 Triggers (a) watch hook every firing incl. no-op -> Task 6 (three exit points). ✓
- §1 Triggers (b) on-demand CLI -> Task 7. ✓
- §1 Auto-refresh 60s -> Task 5 (`<meta http-equiv="refresh" content="60">`). ✓
- §2 Status strip (RUNNING/PAUSED, last firing, next expected, counter N/5, stale warning, auth-dead) -> Task 2 + Task 1 (auth-dead scan). ✓
- §2 Pipeline (status columns, incumbent highlight, lineage, click-to-expand detail incl. breakdown/kaggle_score/version/commit) -> Task 3. ✓
- §2 History (per-candidate SVG chart, 520-575 band, last ~5 digests) -> Task 4 (chart) + Task 5 (digests). ✓
- §3 Failure isolation (wrapped hook, missing files = default state, unparseable = warning banner) -> Task 1 (fault-tolerant reads), Task 5 (`safe_render`), Task 6 (isolation test). ✓
- §4 Testing (fixture markers, failure-isolation test, virgin-dir test, suite green) -> Tasks 1-8. ✓
- Open Question (score history source) -> resolved: `candidates.json.score_history` is the source; no new persistence, `LADDER.md` not parsed. Encoded in Task 4/Task 1 (charts read `score_history`). ✓
- Non-goals / Invariants -> honored (read-only, no new deps, gitignored output, three Global Constraints/invariants block). ✓

**2. Placeholder scan:** no `TBD`/`TODO`/"similar to Task N"/"add error handling" — every code step shows complete code. The one cross-task reuse (`build_factory` test helper) is defined in full in Task 1 and referenced by name thereafter (standard in-file reuse, not a code placeholder). ✓

**3. Type consistency:** `assemble_state -> dict` keys are produced in Task 1 and consumed verbatim in Tasks 2-5; `safe_render(root, *, log)` defined in Task 5 is called with `paths.root` in Task 6 and (via `write_dashboard`) in Task 7; `render_score_chart`/`render_history`/`render_pipeline`/`render_status_strip`/`render_digests`/`render_page`/`render_dashboard`/`write_dashboard` names are identical at definition and use sites. `CAP`/`STATUS_COLUMN`/`COLUMNS`/`BAND_LOW`/`BAND_HIGH` defined once in Task 1. ✓

**4. Landmark grep applied:** all cited files/fields/formats verified against the real repo (see "Grounded landmarks" above). The one divergence (lineage join key: deck stem, not `name`) is flagged and resolved in the plan text and Task 3.
