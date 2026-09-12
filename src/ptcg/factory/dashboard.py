"""Read-only factory status dashboard renderer (design spec 2026-07-20).

Stdlib-only. Reads the factory's existing state files, builds one plain dict
with per-file fault tolerance, and renders a single self-contained HTML page.
Never mutates factory state; a render failure never affects a cycle.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Callable

from ptcg.factory.cycle import FactoryPaths
from ptcg.factory.episodes import _extracts_path
from ptcg.factory.evolution import _agent_pool_path, _deck_pool_path
from ptcg.factory.gate import HARD_DAILY_CAP
from ptcg.factory.genomes import cell_id
from ptcg.factory.tournament import MatrixLedger, TRAINER_STALE_MIN, WORKER_STALE_MIN

LOSS_CLUSTERS_N = 5

STALE_THRESHOLD_MIN = 35
FIRING_INTERVAL_MIN = 15
BAND_LOW = 520
BAND_HIGH = 575
CAP = HARD_DAILY_CAP
TOP_CELLS_N = 5
RECENT_GENOMES_N = 5
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


WORKER_NAMES = ("matrix", "trainer")

# Per-worker heartbeat-stale threshold. The trainer's is much longer than
# the matrix worker's - a single training run is normal and can legitimately
# run for hours between heartbeat-worthy progress ticks, whereas the matrix
# worker plays short blocks continuously (spec compute-saturation design
# doc, 2026-07-20). Falls back to WORKER_STALE_MIN for any unrecognized
# worker name.
STALE_MIN_BY_WORKER = {"matrix": WORKER_STALE_MIN, "trainer": TRAINER_STALE_MIN}


def _worker_entry(name: str, path: Path, utc_now: dt.datetime,
                  warnings: list[str]) -> dict:
    """Parse one worker's heartbeat file (`{"ts": iso-utc, "detail": str}`,
    written by tournament.py/trainer_worker.py's `_write_heartbeat`) into a
    display-ready entry. A missing file is expected pre-go-live (no
    warning); malformed JSON or an unexpected shape (missing `ts`/`detail`,
    non-parseable `ts`) is treated as missing too, but WITH a warning --
    `safe_render` isolation must hold, so this never raises. Staleness
    threshold is per-worker (`STALE_MIN_BY_WORKER`) -- the trainer tolerates
    a much longer gap than the matrix worker."""
    label = f"{name}_heartbeat.json"
    payload = _read_json(path, warnings, label)
    missing_entry = {"name": name, "ts": None, "detail": None,
                     "stale": False, "missing": True}
    if payload is None:
        return missing_entry
    try:
        ts = dt.datetime.fromisoformat(payload["ts"])
        detail = payload["detail"]
    except (KeyError, TypeError, ValueError) as exc:
        warnings.append(f"{label}: {exc!r}")
        return missing_entry
    stale_min = STALE_MIN_BY_WORKER.get(name, WORKER_STALE_MIN)
    stale = (utc_now - ts).total_seconds() > stale_min * 60
    return {"name": name, "ts": ts, "detail": detail, "stale": stale,
            "missing": False}


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


# ============================================================
# EVOLUTIONARY POPULATION PANEL (Task 9) -- read-only over agent_pool.json /
# deck_pool.json (genomes.py) + matrix.json (tournament.MatrixLedger's own
# ledger, distinct from the candidate ledger). Every helper below works on
# RAW dicts (never genomes.AgentGenome/DeckGenome dataclasses) so a row
# missing/mistyping a field degrades via .get() instead of raising --
# consistent with how `assemble_state` already treats candidates.json above.
# ============================================================


def _load_pool_rows(path: Path, warnings: list[str], label: str) -> list[dict]:
    """Best-effort raw genome rows from a pool ledger file. Missing file ->
    [] with no warning (pre-seed is an expected state, mirrors candidates.json
    handling); corrupt/malformed JSON -> [] with a warning, via `_read_json`'s
    existing fault isolation. Never constructs a genomes.py dataclass."""
    payload = _read_json(path, warnings, label)
    if not isinstance(payload, dict):
        return []
    rows = payload.get("genomes", [])
    return [r for r in rows if isinstance(r, dict)]


def _status_counts(rows: list[dict]) -> dict[str, int]:
    """live/retired/anchor/meta-anchor (+ any future status) counts, keyed by
    the raw status string so a not-yet-seen status value degrades to an extra
    bucket instead of being silently dropped or raising."""
    counts: dict[str, int] = {}
    for r in rows:
        status = str(r.get("status", "?"))
        counts[status] = counts.get(status, 0) + 1
    return counts


def _top_cells(agent_rows: list[dict], deck_rows: list[dict],
              ledger: MatrixLedger, n: int = TOP_CELLS_N) -> list[dict]:
    """Strongest `n` (agent, deck) cells among ACTIVE (non-retired) genomes on
    both sides -- mirrors `evolution.active_cells`'s retired-only exclusion
    ("anchor"/"meta-anchor" still pair). Only cells where BOTH sides have a
    fitted rating are ranked (an unrated side has no strength signal yet);
    a not-yet-rated population simply yields no top cells, not an error.
    strength = exp(agent.rating + deck.rating), the same gate-facing
    multiplicative scale `evolution._cell_strength` computes."""
    live_agents = [a for a in agent_rows if a.get("status") != "retired"]
    live_decks = [d for d in deck_rows if d.get("status") != "retired"]
    scored: list[dict] = []
    for a in live_agents:
        a_rating = a.get("rating")
        if a_rating is None:
            continue
        for d in live_decks:
            d_rating = d.get("rating")
            if d_rating is None:
                continue
            cid = cell_id(str(a.get("id", "?")), str(d.get("id", "?")))
            scored.append({
                "agent_id": a.get("id", "?"),
                "deck_id": d.get("id", "?"),
                "strength": math.exp(a_rating + d_rating),
                "games": ledger.total_games(cid),
            })
    scored.sort(key=lambda c: c["strength"], reverse=True)
    return scored[:n]


def _recent_genomes(rows_by_kind: list[tuple[str, list[dict]]], *,
                    status: str | None, n: int = RECENT_GENOMES_N) -> list[dict]:
    """Most-recent `n` genomes (optionally filtered to `status`) across both
    populations, newest `born_at` first. `born_at` is an ISO-8601 string
    (genomes.py: `now.isoformat()` at birth), so a lexical sort is
    chronological without parsing.

    JUDGMENT CALL for retirements: AgentGenome/DeckGenome have no
    `retired_at` field, and this task must not add one to genomes.py. So
    "last 5 retirements" is ordered by `born_at` DESCENDING among
    status=="retired" rows -- "most recently BORN among the currently
    retired", not a true retirement-recency ordering. This is a documented
    proxy, not the real thing; a future slice that wants true retirement
    recency needs a `retired_at` field added to genomes.py first.
    """
    combined: list[dict] = []
    for kind, rows in rows_by_kind:
        for r in rows:
            if status is not None and r.get("status") != status:
                continue
            combined.append({
                "kind": kind,
                "id": r.get("id", "?"),
                "born_at": str(r.get("born_at") or ""),
                "lineage": list(r.get("lineage") or []),
            })
    combined.sort(key=lambda r: r["born_at"], reverse=True)
    return combined[:n]


def _assemble_evolution(paths: FactoryPaths, warnings: list[str]) -> dict:
    agent_path = _agent_pool_path(paths)
    deck_path = _deck_pool_path(paths)
    seeded = agent_path.exists() or deck_path.exists()

    agent_rows = _load_pool_rows(agent_path, warnings, "agent_pool.json")
    deck_rows = _load_pool_rows(deck_path, warnings, "deck_pool.json")

    matrix_payload = _read_json(paths.matrix, warnings, "matrix.json")
    if isinstance(matrix_payload, dict):
        ledger = MatrixLedger(pairs=matrix_payload.get("pairs") or {},
                              meta=matrix_payload.get("meta") or {})
    else:
        ledger = MatrixLedger()

    rows_by_kind = [("agent", agent_rows), ("deck", deck_rows)]
    return {
        "seeded": seeded,
        "agent_counts": _status_counts(agent_rows),
        "deck_counts": _status_counts(deck_rows),
        "top_cells": _top_cells(agent_rows, deck_rows, ledger),
        "recent_births": _recent_genomes(rows_by_kind, status=None),
        "recent_retirements": _recent_genomes(rows_by_kind, status="retired"),
    }


def _assemble_loss(paths: FactoryPaths, warnings: list[str]) -> dict:
    """Loss-forensics aggregate (Task 11) from harvested episodes
    (extracts.jsonl): timeout-loss %, WR vs the top-N opponent deck
    clusters (by episode count), sample sizes. Degenerate-safe: a missing,
    empty, or fully-unparseable extracts file returns present=False rather
    than raising -- the harvester may not have run yet, or every line may
    be malformed (organizer schema drift), and the panel must render either
    way (spec: "degenerate-safe when extracts are empty")."""
    extracts_path = _extracts_path(paths)
    if not extracts_path.exists():
        return {"present": False, "n": 0, "timeout_pct": None, "clusters": []}

    try:
        lines = [ln for ln in extracts_path.read_text(encoding="utf-8").splitlines()
                if ln.strip()]
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"extracts.jsonl: {exc}")
        return {"present": False, "n": 0, "timeout_pct": None, "clusters": []}

    records: list[dict] = []
    for ln in lines:
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    if not records:
        return {"present": True, "n": 0, "timeout_pct": None, "clusters": []}

    n = len(records)
    n_timeout = sum(1 for r in records if r.get("timeout"))
    timeout_pct = 100.0 * n_timeout / n

    by_hash: dict[str, dict] = {}
    for r in records:
        h = r.get("opponent_deck_hash") or "unknown"
        entry = by_hash.setdefault(h, {"hash": h, "n": 0, "wins": 0})
        entry["n"] += 1
        if r.get("our_result") == "win":
            entry["wins"] += 1
    clusters = sorted(by_hash.values(), key=lambda e: (-e["n"], e["hash"]))[:LOSS_CLUSTERS_N]
    for c in clusters:
        c["wr"] = (100.0 * c["wins"] / c["n"]) if c["n"] else None

    return {"present": True, "n": n, "timeout_pct": timeout_pct, "clusters": clusters}


def assemble_state(root: Path, *, now: dt.datetime | None = None,
                   utc_now: dt.datetime | None = None) -> dict:
    root = Path(root)
    now = now or dt.datetime.now()
    utc_now = utc_now or dt.datetime.now(dt.timezone.utc)
    fdir = _factory_dir(root)
    warnings: list[str] = []

    paused = (fdir / "PAUSE").exists()

    counter = _read_json(fdir / "submission_counter.json", warnings,
                         "submission_counter.json")

    ledger = _read_json(fdir / "candidates.json", warnings, "candidates.json")
    candidates = list(ledger.get("candidates", [])) if isinstance(ledger, dict) else []
    incumbent_id = next((c.get("id") for c in candidates if c.get("is_incumbent")),
                        None)

    paths = FactoryPaths(root)
    workers = [_worker_entry(name, getattr(paths, f"{name}_heartbeat"),
                             utc_now, warnings)
              for name in WORKER_NAMES]

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
        "utc_now": utc_now,
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
        "workers": workers,
        "evolution": _assemble_evolution(paths, warnings),
        "loss": _assemble_loss(paths, warnings),
        "warnings": warnings,
    }


def _fmt_ts(ts: dt.datetime | None) -> str:
    return ts.isoformat(timespec="minutes") if ts else "unknown"


def _reset_countdown(utc_now: dt.datetime) -> str:
    """Advisory time until the next midnight UTC (the real submission-cap
    reset anchor, see `gate.utc_today`). Caller must pass a UTC-anchored
    instant, not host-local wall-clock time -- on a UTC-10 (HST) machine the
    two differ by up to 10h, which previously produced a countdown wrong by
    that much (see `assemble_state`'s `utc_now`, kept distinct from the
    local-time `now` used for scheduler-staleness comparisons)."""
    next_midnight = dt.datetime.combine(utc_now.date() + dt.timedelta(days=1),
                                        dt.time.min, tzinfo=utc_now.tzinfo)
    rem = next_midnight - utc_now
    total_min = max(0, int(rem.total_seconds() // 60))
    return f"{total_min // 60}h {total_min % 60}m"


def _render_worker_badge(worker: dict, utc_now: dt.datetime) -> str:
    """One heartbeat badge per worker (Task 10 spec): missing -> a plain
    grey item ("not registered"; pre-go-live the workers legitimately don't
    exist yet, so this is never the red stale badge); stale (>WORKER_STALE_MIN
    since last tick) -> the same red `badge-warn` idiom the scheduler-stale
    badge above uses; fresh -> a green `badge-running` pill with the
    worker's own reported detail string."""
    name = worker["name"]
    if worker.get("missing"):
        return (f'<span class="strip-item">{html.escape(name)}: '
                'not registered</span>')
    if worker.get("stale"):
        age_min = int((utc_now - worker["ts"]).total_seconds() // 60)
        return (f'<span class="badge badge-warn">{html.escape(name.upper())} '
                f'STALE {age_min}m</span>')
    detail = html.escape(str(worker.get("detail") or ""))
    return f'<span class="badge badge-running">{html.escape(name)} ✓ {detail}</span>'


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
    # Mirror gate.SubmissionCounter.today_count()'s exact comparison
    # (gate.py:64-65): `self.count if self.date == today else 0`, where
    # `today` is the UTC-anchored ISO date string from gate.utc_today().
    # Displaying `counter["count"]` unconditionally would overstate usage
    # after a UTC date rollover -- yesterday's "5/5" when 5 slots are free.
    utc_today = state["utc_now"].date().isoformat()
    stale_day = counter is not None and counter.get("date") != utc_today
    count = counter.get("count", 0) if counter and not stale_day else 0
    parts.append('<span class="strip-item">submissions '
                 f'{html.escape(str(count))}/{state["cap"]} '
                 f'(resets in {html.escape(_reset_countdown(state["utc_now"]))} UTC)'
                 '</span>')
    if stale_day:
        parts.append('<span class="strip-item">(new UTC day)</span>')

    if state.get("stale"):
        parts.append('<span class="badge badge-warn">scheduler stale '
                     f'(&gt;{STALE_THRESHOLD_MIN}m since last firing)</span>')

    for worker in state.get("workers", []):
        parts.append(_render_worker_badge(worker, state["utc_now"]))

    auth = state.get("auth_dead")
    if auth:
        parts.append('<span class="badge badge-auth-dead">AUTH-DEAD: '
                     f'{html.escape(auth["detail"])}</span>')

    parts.append('</div>')
    return "".join(parts)


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


def render_score_chart(score_history: list, *, width: int = 320,
                       height: int = 120) -> str:
    pad = 10
    pts: list[tuple[str, float]] = []
    for entry in score_history or []:
        try:
            ts, score = entry[0], float(entry[1])
        except (TypeError, ValueError, IndexError, KeyError):
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


def _render_counts(counts: dict[str, int]) -> str:
    if not counts:
        return '<div class="empty">none</div>'
    items = "".join(f'<span class="evo-count">{html.escape(k)}: {v}</span>'
                    for k, v in sorted(counts.items()))
    return f'<div class="evo-counts">{items}</div>'


def _render_top_cells(cells: list[dict]) -> str:
    if not cells:
        return '<div class="empty">no rated cells yet</div>'
    rows = []
    for c in cells:
        rows.append('<div class="evo-row">'
                    f'<span class="evo-id">{html.escape(str(c["agent_id"]))} '
                    f'&times; {html.escape(str(c["deck_id"]))}</span>'
                    f'<span>strength {c["strength"]:.3f}</span>'
                    f'<span>{c["games"]} games</span></div>')
    return "".join(rows)


def _render_genome_list(entries: list[dict], empty_label: str) -> str:
    if not entries:
        return f'<div class="empty">{html.escape(empty_label)}</div>'
    rows = []
    for e in entries:
        lineage = e.get("lineage") or []
        lineage_html = html.escape(" -> ".join(str(p) for p in lineage)) or "(no parents)"
        rows.append('<div class="evo-row">'
                    f'<span class="evo-id">{html.escape(str(e.get("kind", "?")))}: '
                    f'{html.escape(str(e.get("id", "?")))}</span>'
                    f'<span class="evo-born">{html.escape(str(e.get("born_at", "")))}</span>'
                    f'<span class="lineage">{lineage_html}</span></div>')
    return "".join(rows)


def render_evolution(state: dict) -> str:
    """Population panel (spec Task 9). Degenerate-safe by construction: an
    absent "evolution" key (backward compat with any caller/state predating
    this task) is treated identically to a not-yet-seeded factory."""
    evo = state.get("evolution") or {}
    if not evo.get("seeded"):
        return ('<section class="evolution"><h2>Evolutionary population</h2>'
                '<div class="empty">evolution not seeded</div></section>')
    return (
        '<section class="evolution"><h2>Evolutionary population</h2>'
        '<div class="evo-block"><h3>Agents</h3>'
        f'{_render_counts(evo.get("agent_counts", {}))}</div>'
        '<div class="evo-block"><h3>Decks</h3>'
        f'{_render_counts(evo.get("deck_counts", {}))}</div>'
        '<div class="evo-block"><h3>Top cells</h3>'
        f'{_render_top_cells(evo.get("top_cells", []))}</div>'
        '<div class="evo-block"><h3>Recent births</h3>'
        f'{_render_genome_list(evo.get("recent_births", []), "no births yet")}</div>'
        '<div class="evo-block"><h3>Recent retirements</h3>'
        f'{_render_genome_list(evo.get("recent_retirements", []), "no retirements yet")}</div>'
        '</section>'
    )


def _render_loss_clusters(clusters: list[dict]) -> str:
    if not clusters:
        return '<div class="empty">no opponent-deck clusters yet</div>'
    rows = []
    for c in clusters:
        wr = c.get("wr")
        wr_txt = f"{wr:.1f}%" if wr is not None else "n/a"
        rows.append('<div class="loss-row">'
                    f'<span class="loss-id">{html.escape(str(c["hash"]))}</span>'
                    f'<span>WR {wr_txt}</span>'
                    f'<span>{c["n"]} episodes</span></div>')
    return "".join(rows)


def render_loss(state: dict) -> str:
    """Loss/meta panel (spec Task 11). Degenerate-safe: an absent "loss" key
    (backward compat) or a present-but-empty extracts file both render the
    same not-yet-available placeholder rather than raising."""
    loss = state.get("loss") or {}
    if not loss.get("present") or not loss.get("n"):
        return ('<section class="loss"><h2>Loss / meta panel</h2>'
                '<div class="empty">no harvested episodes yet</div></section>')
    timeout_pct = loss.get("timeout_pct")
    timeout_txt = f"{timeout_pct:.1f}%" if timeout_pct is not None else "n/a"
    return (
        '<section class="loss"><h2>Loss / meta panel</h2>'
        f'<div class="loss-summary">timeout-loss: {timeout_txt} '
        f'({loss["n"]} episodes)</div>'
        '<div class="loss-block"><h3>Top opponent-deck clusters</h3>'
        f'{_render_loss_clusters(loss.get("clusters", []))}</div>'
        '</section>'
    )


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
.evolution .evo-block { margin-bottom: .6rem; }
.evolution h3 { font-size: .85rem; margin: .2rem 0 .3rem; }
.evo-counts { display: flex; gap: .5rem; flex-wrap: wrap; }
.evo-count { font-size: .8rem; border: 1px solid #8884; border-radius: 4px;
  padding: .1rem .4rem; }
.evo-row { display: flex; gap: .6rem; flex-wrap: wrap; font-size: .78rem;
  border-top: 1px solid #8882; padding: .2rem 0; }
.evo-id { font-weight: 600; word-break: break-all; }
.evo-born { opacity: .7; }
.loss .loss-block { margin-top: .5rem; }
.loss h3 { font-size: .85rem; margin: .2rem 0 .3rem; }
.loss-summary { font-size: .85rem; }
.loss-row { display: flex; gap: .6rem; flex-wrap: wrap; font-size: .78rem;
  border-top: 1px solid #8882; padding: .2rem 0; }
.loss-id { font-weight: 600; word-break: break-all; }
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
        f"{render_evolution(state)}"
        f"{render_loss(state)}"
        "</body></html>"
    )


def render_dashboard(root: Path, *, now: dt.datetime | None = None,
                     utc_now: dt.datetime | None = None) -> str:
    return render_page(assemble_state(root, now=now, utc_now=utc_now))


def write_dashboard(root: Path, *, now: dt.datetime | None = None,
                    utc_now: dt.datetime | None = None,
                    log: Callable = print) -> Path:
    """Render and atomically publish the dashboard.

    Writes to a per-process temp sibling then `os.replace`s it into place
    (matches the tmp+replace convention used by `watch.record_training`,
    `gate.py`, `candidates.py`, `harvest.py`). The watch loop's busy-branch
    render (another process's firing holds the instance lock) and the
    lock-holder's own render can both be in flight at once, and a browser
    tab re-reads this file on a 60s auto-refresh -- a direct `write_text`
    truncate-in-place risks a torn/interleaved read. The temp filename
    includes `os.getpid()` so two concurrent writer PROCESSES (this is a
    cross-process instance_lock race, not a same-process one) never share a
    temp path, which would just move the race one level down.
    """
    html_str = render_dashboard(root, now=now, utc_now=utc_now)
    out_path = Path(root).joinpath(*DASHBOARD_REL)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(html_str, encoding="utf-8")
    os.replace(tmp, out_path)
    return out_path


def safe_render(root: Path, *, log: Callable = print) -> Path | None:
    """Failure-isolated render hook (spec §3): never raises, logs one loud line
    on failure, returns None. A broken renderer must never fail a factory cycle."""
    try:
        return write_dashboard(root, log=log)
    except Exception as exc:  # noqa: BLE001 - isolation is the whole point
        log(f"dashboard render failed (cycle unaffected): {exc!r}")
        return None
