# Design Spec: Factory Status Dashboard

**Date:** 2026-07-20
**Status:** Approved (design as presented by Brad 2026-07-20; content below is a faithful transcription — structure/prose is this document's own)

## Context

The agent factory (Slices 7A/7B, continuous operation since 2026-07-17) currently exposes its state only through raw artifacts a human has to piece together by hand: `experiments/factory/candidates.json` (the ledger), `experiments/factory/logs/watch.log` (one line per 15-minute firing), `experiments/factory/digests/*.md` (one file per cycle that did work), `experiments/factory/submission_counter.json` (the UTC-day submission cap counter), the `experiments/factory/PAUSE` file (kill switch), and `experiments/LADDER.md` (harvested Kaggle score history in prose-table form). There is no single view of "what is the factory doing right now and is it healthy." This spec adds a self-contained, regenerated-on-every-firing HTML dashboard that renders all of the above into one page.

## Decisions (Brad-approved 2026-07-20)

- **New renderer module**, stdlib only, no new runtime dependencies — matches repo convention (value net / policy net inference are also stdlib-only at serve time).
- **Single self-contained output file**, `experiments/factory/dashboard.html`, gitignored (same class as `watch.log` — regenerating runtime output).
- **Renders on every watch-loop firing, including no-ops**, plus on demand via a CLI script.
- **Failure-isolated**: a rendering bug must never break a factory cycle.
- **Score history source**: resolved by grounding (see Open Question below) rather than left as a design fork.

## Design

### §1 — Architecture

- New module `src/ptcg/factory/dashboard.py`. Pure functions: read the state files below, build an in-memory model, render to a single HTML string. No network calls, no external CSS/JS/font/image requests — all styling and interactivity inline in the one output file (matches the value-net/policy-net "stdlib only at serve time" convention already established in this repo).
- **State files read** (grounded against the real files as of 2026-07-20):
  - `experiments/factory/candidates.json` — `{"version": 1, "candidates": [...]}`. Each candidate dict has (confirmed real field names, via `Candidate` in `src/ptcg/factory/candidates.py`): `id`, `name`, `version`, `deck`, `agent_kind`, `agent_config`, `provenance`, `priority`, `status` (one of the `Status` enum values: `queued`, `evaluating`, `evaluated`, `evaluated-below-incumbent`, `submitted`, `scored`, `retired`), `novel_axis`, `is_incumbent`, `local_wr`, `local_games`, `local_breakdown` (either `null` or a list of `{"baseline": str, "wins": int, "games": int}`), `kaggle_score`, `submitted_at`, `last_resubmitted_at`, `score_history` (a list of `[iso_timestamp, score]` pairs, append-only — see Open Question), `notes`.
  - `experiments/factory/logs/watch.log` — one line per firing, formats confirmed from the real file: `[ISO_TIMESTAMP] paused`, `[ISO_TIMESTAMP] busy: another firing holds the lock`, `[ISO_TIMESTAMP] refill: enqueued N`, `[ISO_TIMESTAMP] training: deck=<name>` / `training failed: <repr>`, `[ISO_TIMESTAMP] cycle: noop`, `[ISO_TIMESTAMP] cycle: evaluated=N actions=N digest=<path>`.
  - `experiments/factory/digests/*.md` — one file per cycle that did work (`cycle-YYYYMMDD-HHMMSS.md`), written by `write_digest()` in `src/ptcg/factory/cycle.py`. Sections: a header line, `Harvest: N matched rows, M score updates, K submissions today.` plus `- unmatched submission: <desc>` lines, `## Evaluated` (`- {id}: local_wr={wr:.3f}/{games} [{baseline wr (wins/games); ...}] -> {status}`), `## Gate actions` (`- {candidate_id}: {action} - {detail}`).
  - `experiments/factory/submission_counter.json` — `{"date": "YYYY-MM-DD", "count": N}` (UTC day, confirmed via live file).
  - `experiments/factory/PAUSE` — presence-only kill switch (no content parsed).
  - `experiments/LADDER.md` — markdown table, columns `Version | Submitted | Description | Status | Score trajectory | Verdict`. Read only as a cross-check fallback (see Open Question) — not the primary score-history source.
- **Output**: `experiments/factory/dashboard.html`, one file, overwritten on every render. Added to `.gitignore` in the same commit that ships the implementation (same class as `watch.log`/digests — regenerated runtime output, not source).
- **Triggers**:
  (a) End of every `scripts/factory_watch_once.py` firing, including no-op firings (lock-skip, PAUSE, nothing to do) — the dashboard's own "last updated" timestamp must stay honest even when the digest is suppressed for that cycle. Wire the render call at the end of `watch_once()` in `scripts/factory_watch_once.py`, after the existing `append_watch_log` calls, inside the same `try` block that already has access to `paths`.
  (b) On-demand CLI `scripts/render_dashboard.py`, callable standalone against a repo root (defaults to the real one) for manual inspection between firings.
- **Auto-refresh**: the emitted HTML includes a `<meta http-equiv="refresh" content="60">` tag (or an equivalent small inline JS `setTimeout(() => location.reload(), 60000)` — implementer's choice, functionally identical) so a browser tab left open on `dashboard.html` picks up each new render without a manual reload.

### §2 — Panels

**Status strip** (top of page, always visible):
- RUNNING / PAUSED badge, derived from `experiments/factory/PAUSE` file presence.
- Last firing time + one-line summary, parsed from the last line of `watch.log` (all five line shapes above must be handled, including `paused` and `busy`).
- Next expected firing, computed as last-firing-time + 15 minutes (the `ptcg-factory-continuous` scheduled task's repetition interval, per `docs/factory-operations.md` / the continuous-factory design spec) — advisory only, not a scheduler query.
- Scheduler-liveness warning: shown when the last `watch.log` line's timestamp is more than 35 minutes old (two 15-minute firing intervals plus slack, matching the window already documented in `.claude/rules/factory-task-scheduler-liveness.md`). This is inferred entirely from the log's own timestamps — the dashboard never calls `Get-ScheduledTask`/`Get-ScheduledTaskInfo` itself, so it stays dependency-free and cross-platform-renderable even though the scheduler it is describing is Windows-only.
- Submission counter: `N/5` plus a UTC-day reset countdown, read directly from `submission_counter.json`'s `count` field against the hard cap of 5 (`HARD_DAILY_CAP` in `src/ptcg/factory/gate.py`); countdown is computed as time remaining until the next UTC midnight from `date`.
- Auth-health signal: derived from the **most recent digest's `## Gate actions` section**, not from `watch.log`. Grounding finding: the pre-submit auth guard (commit `9d3242e`, `src/ptcg/factory/submit.py`) logs `AUTH-DEAD: kaggle auth check failed ...` through the cycle's `log` callable (default `print`, not persisted anywhere by the scheduled task — the task action has no stdout redirect configured in `scripts/register_factory_task.ps1`), but it *also* appends an `("AUTH", "auth-dead", <detail>)` tuple to the cycle's `actions` list, which `write_digest()` renders as a `- AUTH: auth-dead - <detail>` line under `## Gate actions` in that cycle's digest file. The dashboard's auth-health check scans the newest digest (by filename timestamp) for a line starting `- AUTH: auth-dead` and, if found, renders a persistent warning badge (persistent because a stale/dead token stays dead across firings until a human re-authenticates — the badge should not silently clear on the next no-op digest-suppressed cycle; clear it only when a newer digest exists and does NOT contain that line, or when a normal submission has scored since).

**Pipeline view** (queue → evaluate → gate → submit → harvest, as columns):
- Every candidate in `candidates.json["candidates"]` is positioned into a column by its `status` field: `queued` → Queue; `evaluating`/`evaluated` → Evaluate; `evaluated-below-incumbent` and `retired` → Gate (terminal "did not advance" outcomes); `submitted` → Submit; `scored` → Harvest.
- The candidate with `is_incumbent: true` is visually highlighted (border/background accent) wherever it currently sits.
- Deck-matrix lineage is rendered as a derived chain from the `deck` field's path component and the `provenance` field (e.g. `provenance: "deck-matrix:mega-starmie-water-lean:attacker-down1"` on a candidate whose `deck` basename is `mega-starmie-water-lean-attacker-down1.csv` implies parent `mega-starmie-water-lean`); chase parents by matching a candidate's provenance parent-name against another candidate's `name` field to build the chain (e.g. `mega-starmie-water-lean` → `mega-starmie-water-lean-attacker-down1` → `mega-starmie-water-lean-attacker-down1-attacker-up1`, the exact three-generation chain visible in the live ledger today). Candidates with no matching parent (curated seeds, daemon-trained nets) render as chain roots.
- Click-to-expand per-candidate detail panel shows: `local_wr` + `local_games`, the full `local_breakdown` list (baseline name, win rate computed as `wins/games`, raw `wins/games`) when non-null, `kaggle_score`, `version`, and the short git commit (parsed out of `notes` where present, matching the `... - <8-char-hash> - factory ...` convention already used in `LADDER.md` descriptions — if absent, omit the commit field rather than guessing).

**History**:
- Per-candidate ladder score trajectory as an inline SVG line chart, built directly from that candidate's `score_history` list of `[iso_timestamp, score]` pairs (no external charting library — hand-rolled `<svg><polyline>` from normalized x/y coordinates, consistent with the "inline SVG" architecture constraint).
- A shaded band from 520 to 575 on every chart, marking the convergence range documented in the 2026-07-20 weekly review (`experiments/factory/digests` / memory `weekly-review-2026-07-20-gate-recal.md`) as the range recent scores have converged into.
- The last ~5 cycle digests (by filename timestamp, newest first) rendered inline as their raw markdown content (converted to simple HTML — headers/lists only, no need for a full markdown engine given the digest's own fixed, simple structure).

### §3 — Failure isolation (invariant)

- The render call at every trigger site is wrapped so that any exception raised inside `dashboard.py` is caught, logged as a single line (via the existing `log`/`append_watch_log` mechanism already in scope at each call site), and swallowed — the factory cycle that triggered the render must complete exactly as it would have if the dashboard didn't exist. A broken renderer must never fail a cycle, block a submission, or crash `factory_watch_once.py`.
- Missing files (e.g. no digests yet on a fresh clone, no `PAUSE` file present — the normal RUNNING case) are expected states, not errors: render the corresponding panel in a visible "no data yet" / default state rather than raising.
- Unparseable files (a `candidates.json` that fails `json.loads`, a `watch.log` line that doesn't match any known format, a truncated digest) render a visible in-page warning banner naming the file and the parse failure, and the rest of the page renders normally from whatever DID parse. No file-level parse failure may abort the whole render.

### §4 — Testing

- Unit tests build small fixture directories (temp dir standing in for `experiments/factory/`) with hand-written `candidates.json`, `watch.log`, digest files, `submission_counter.json`, and assert the rendered HTML contains the expected markers: the PAUSED badge text when a `PAUSE` file fixture is present, the AUTH-DEAD warning when a fixture digest contains the `- AUTH: auth-dead` line, the correct `N/5` counter value, a rendered lineage chain matching a 3-generation fixture (seed → mutation → second-order mutation), and the incumbent highlight class/attribute on the fixture candidate with `is_incumbent: true`.
- A dedicated failure-isolation test: poison one state file (e.g. write invalid JSON to the fixture `candidates.json`, or an empty/garbage digest) and assert the render hook does not raise and the resulting HTML contains the file-specific warning banner from §3, not a crash.
- Per the repo's virgin-directory lesson (`.claude/rules` / global CLAUDE.md — this bug class has recurred twice already in this repo), include a test that renders against a fixture `experiments/factory/` directory that does NOT pre-exist (no digests dir, no watch.log, no candidates.json) and asserts a first-run, all-"no data yet" render succeeds without raising — do not rely on `tmp_path`'s default pre-creation of parent directories to stand in for this case.
- Full fast suite (`uv run pytest`) stays green with the new tests added; no new `-m slow` marker is needed since nothing here depends on match simulation.

## Open Question — score history source (resolved by grounding, recorded as the plan-phase decision)

**Question as posed:** determine whether harvest already persists score history anywhere structured, and choose between parsing `LADDER.md` prose vs. adding a new append-only score-history JSON file to the harvester.

**Finding:** it already does, and it's already structured. Every candidate in the live `candidates.json` carries its own `score_history` field — a list of `[iso_timestamp, score]` pairs, append-only, populated by the harvester on each Kaggle score refresh (confirmed on the live incumbent candidate `mega-starmie-water-lean-attacker-down1-heuristic-v0.1`: 21 entries from `2026-07-19T21:45` through `2026-07-20T11:45`). `LADDER.md`'s "Score trajectory" column is a derived, prose (`->`-joined) rendering of this exact same data, written by the same harvester — it is strictly a lossier, less structured copy of what `candidates.json` already holds.

**Decision:** the dashboard reads `score_history` directly off each candidate in `candidates.json`. No new score-history file is added, and `LADDER.md` is not parsed for chart data (it may still be read, if convenient, purely as a cross-check that a candidate's rendered trajectory matches its `LADDER.md` row — not required for the panel to function). This is the cheaper path (zero new persistence, zero new harvester code) and the more durable one (structured JSON beats parsing a hand-formatted markdown table).

## Non-goals

- No changes to the harvester, gate, submit, or any factory decision-making logic — this is a read-only reporting layer.
- No live/streaming updates within a single render (the 60s meta-refresh is a full page reload, not a websocket/SSE push).
- No new dependency on a templating engine, charting library, or CSS framework — inline stdlib-generated HTML/CSS/SVG/JS only.
- No querying of the Windows Task Scheduler API from the dashboard itself — scheduler liveness is inferred from `watch.log` timestamps only (per §2).

## Invariants

- A dashboard render (success or failure) never changes factory cycle outcomes: no candidate status, gate decision, submission, or ledger write may depend on whether `dashboard.py` succeeded.
- `experiments/factory/dashboard.html` is gitignored — it is regenerated output, not source, and must never be committed.
- The renderer has zero new runtime dependencies beyond the Python standard library, matching the value-net/policy-net serving convention already established in this repo.
