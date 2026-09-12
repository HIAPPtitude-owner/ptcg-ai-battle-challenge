# Submission Strength Gate (anchor-confirm) — Design Spec

**Date:** 2026-08-01
**Status:** Approved by Brad (design sections 1–6, AskUserQuestion 2026-08-01)
**Prereq state:** SUBMIT_HOLD ON since 2026-07-31; champions v0.2/v0.3 crowned and queued behind the hold; counted Kaggle pair = rescue pair (555.8 / 452.5).

## Problem

The generational champion tournament selects champions **relatively** (best aggregate crown win% among sibling offspring). A cohort of uniformly weak offspring still produces a "champion" — that is how the 325.1 junk submission (ref 55125891) shipped. The submit path (`subscheduler._decide_and_submit` → `_attempt_upload`) contains **no absolute strength check**, and no strength evidence survives to submit time: `resolve_crown` computes aggregate win% then persists only the winner's identity; the `baselines` row carries just `version/offspring_id/deck_id/crowned_at`; `coverage` is keyed per-concept (deck), not per-champion. The known-strength anchor (HeuristicAgent + mega-lucario-fighting, our live ladder identity, `src/ptcg/agents/current.py:12-13`) plays **zero** games in the tournament DB.

## Decision record (Brad, 2026-08-01)

1. **Anchor wiring: post-crown confirm series.** Champion-only anchor games after crowning; no anchor inside the tournament; no submit-time on-demand series.
2. **Bar: must-beat, WR ≥ 0.55** — deliberate override of the recommended 0.45 junk-filter. Brad accepts the stated trade-off: parity-class champions (Slices 4–7B measured 0.42–0.54 vs this anchor for every search config) will not ship, and the factory may upload nothing further before the 2026-08-16 freeze; the ladder then keeps the rescue pair. Ladder protection over upload volume.
3. **Fail semantics: skip upload, tournament continues.** PENDING → skip the submit tick with a progress log line; FAIL → champion never uploads, verdict logged, next generation proceeds normally.
4. **Go-live tail pre-authorized:** after Pass 2 approves, lift SUBMIT_HOLD and verify the first gated submit tick (§5).

## §1 Architecture

A new **anchor-check stage** between CROWN and upload-eligibility (named "anchor check", not "confirm", to avoid collision with the existing offspring-vs-incumbent CONFIRM stage):

- When a champion is crowned, the scheduler enqueues **`ANCHOR_GAMES = 200`** games with `purpose='anchor'`: champion's cell (agent config + deck) vs the anchor cell (`HeuristicAgent` + mega-lucario-fighting). The champion is ALWAYS `agent_version_a` — the MATCH/CONFIRM fixed-side convention (verified at plan time: crown series do NOT alternate seats either; the engine handles per-game first-player randomization). Only `winner == 0` counts as a champion win.
- The runner pool plays them like any other games (no runner loop changes beyond agent-config resolution, §2).
- A resolve step computes the result once ≥200 anchor games for that version are complete and persists a verdict.
- The subscheduler upload decision reads the verdict for the **current** baseline only: `pass` → eligible; `pending`/row-absent → skip tick; `fail` → never upload this champion.

Constants (named, single definition, logged in every verdict line): `ANCHOR_GAMES = 200`, `ANCHOR_BAR = 0.55`, anchor agent version key `ANCHOR_VERSION = "anchor-heuristic-v0"`.

## §2 Data model (additive only)

New table via the existing `CREATE TABLE IF NOT EXISTS` pattern in `deckdb.py` — added to `_DDL_STATEMENTS` for virgin DBs AND ensured at runtime for the live production DB, which predates the DDL addition (plan-time corrections: `offspring_id`/`deck_id` are TEXT, matching the real schema; `offspring_id` is NULL for a founding baseline):

```sql
CREATE TABLE IF NOT EXISTS anchor_checks (
  version      TEXT PRIMARY KEY,      -- champion baseline version, e.g. 'v0.3'
  offspring_id TEXT,                  -- NULL for founding baseline
  deck_id      TEXT NOT NULL,
  games_planned INTEGER NOT NULL,
  games_done   INTEGER NOT NULL DEFAULT 0,
  wins         INTEGER NOT NULL DEFAULT 0,
  wr           REAL,
  verdict      TEXT NOT NULL DEFAULT 'pending'
               CHECK (verdict IN ('pending','pass','fail')),
  created_at   TEXT NOT NULL,
  resolved_at  TEXT
);
```

- **No existing table changes.** The live ~20k-game DB is touched additively; migration exercised against BOTH a live-shaped fixture (existing tables populated) and a virgin DB (first-ever creation), per the virgin-directory/first-write lesson.
- **Anchor games rows:** `agent_version_{a|b} = ANCHOR_VERSION` on the anchor side; anchor deck referenced by the mega-lucario-fighting concept row (resolve by name from the census-seeded concepts; insert if absent — idempotent).
- **Runner agent-config resolution:** an explicit named special-case for `ANCHOR_VERSION` → heuristic config, checked BEFORE the DB offspring lookup. Unit-tested against exactly the KeyError crash class that starved the runner on 2026-07-31 (commit 22634c5): an unknown version must fail loudly per-game, and `ANCHOR_VERSION` must resolve without a DB row.
- **Draws/indeterminate outcomes** (if the engine produces them) count as champion losses — conservative.

## §3 Step functions & concurrency

Two new scheduler steps, each a **single `BEGIN IMMEDIATE` transaction** end-to-end (settled `toctou-guard-in-step-functions` rule — read-decide-act is atomic even though a concurrent caller may not exist yet):

- **`enqueue_anchor_series`** — idempotent, keyed by champion version (existing `anchor_checks` row → no-op). Fires (a) after a crown, and (b) as **backfill**: if the current baseline has no `anchor_checks` row (v0.3 at go-live), enqueue for it. Also **DELETEs never-claimed pending anchor games of superseded versions** so pool throughput isn't wasted (plan-time correction: `games.status` has a CHECK constraint with no 'cancelled' value — deleting never-played pending rows is the correct supersede mechanism; claimed stragglers finish harmlessly); superseded verdicts are left as-is (moot — the gate reads current baseline only). A **top-up branch** refills the series after a partial ROW LOSS (deleted rows), counted idempotently from existing rows — but it deliberately counts ALL statuses, so a poison-dead-lettered anchor game (stuck 'claimed' after the reclaim cap) is NOT topped up: that version's verdict stays 'pending' permanently, which fails SAFE (uploads stay blocked; blast radius one version; self-heals at the next crown). Post-Pass-2 correction (2026-08-01): the original claim that top-up "covers dead-lettered shortfall" overclaimed — recommendation from the Pass-2 review, behavior unchanged.
- **`resolve_anchor_check`** — when completed anchor games for the version ≥ `ANCHOR_GAMES`: `wr = wins/games_done`, `verdict = 'pass' if wr >= ANCHOR_BAR else 'fail'` (boundary: 110/200 = 0.550 → pass), stamp `resolved_at`. Champion is always `agent_version_a`, so wins = `winner == 0` count (fixed-side convention; draws are champion losses).

Both steps ship with **interleaved-mutation tests** (per `.claude/rules/single-actor-worker-tests.md`): a concurrent crown/subscheduler write injected mid-step must survive.

## §4 Gate placement & slice-time inertness

- Gate check in `subscheduler._decide_and_submit` immediately after the baseline load, BEFORE both upload branches — the champion mark-trigger AND the daily-floor PROBE path (`subscheduler.py:383-408`). Plan-time correction closing a spec gap: the probe path also uploads the current baseline's **agent** (on an alternate deck), so an ungated probe would ship a junk champion's agent to the ladder through the side door; one verdict check gates every upload path, and a gate-blocked tick never consumes the mark (the first tick after a later PASS still fires). Row absent is treated as `pending` (scheduler backfill will create it). All four evidence shapes (pass/pending/fail/absent) are first-class tested paths, per `.claude/rules/provenance-shaped-optional-fields.md`.
- Every skip path emits a distinct terminal log line, preserving the strict paired-marker invariant:
  - `submit: awaiting anchor verdict v0.3 (57/200)`
  - `submit: gate FAIL v0.3 (wr=0.41, bar=0.55)`
  - `submit: gate PASS v0.3 (wr=0.58, bar=0.55)` → proceeds to the existing upload path (counter reserve, build, upload).
- **Named inertness mechanisms while the slice is under review** (per `.claude/rules/factory-resume-probe.md` live-on-write rule): the watch-loop-reachable gate code is inert because **SUBMIT_HOLD is ON for the entire slice**; scheduler/runner changes are inert because those long-lived workers **do not hot-reload** until the explicit go-live restart.

## §5 Go-live sequence (post-merge, pre-authorized)

1. Merge to master.
2. Restart `ptcg-factory-scheduler` and `ptcg-factory-runner` (`Stop-ScheduledTask` then `Start-ScheduledTask` for each; a restart is watchdog-respawn-safe since the task stays registered). Verify the next provenance/log stamp carries a post-merge commit — `Running` state alone is not evidence.
3. Verify v0.3's anchor series was enqueued (DB check: `anchor_checks` row + 200 `purpose='anchor'` games).
4. Wait for series completion (~35–60 min at observed pool throughput, shared with tournament games); verify the verdict row.
5. Delete `experiments/factory/SUBMIT_HOLD`.
6. Verify the next submit tick logs the gate verdict:
   - **PASS** → verify the real upload end-to-end (counter increment, Kaggle listing, description standard).
   - **FAIL/PENDING** → the verified held-with-verdict log line IS the go-live evidence (gate exercised correctly against real state); the first actual upload occurs at the first passing generation. Either outcome completes the rung honestly.

## §6 Scope fence, testing, risks

**Not in scope:** re-crown on fail; anchor inside the tournament; changes to crown selection; ladder-score feedback loops; weekly-review actions beyond gate data; ladder identity files (`src/ptcg/submission_main.py`, `src/ptcg/agents/current.py`) — empty-diff verified at review.

**Test matrix:** enqueue idempotency + backfill + supersede-cancel + top-up; resolve boundary (110/200)/pass/fail/insufficient-games; gate read x4 evidence shapes; anchor config resolution (incl. loud-failure for unknown versions); virgin-DB + live-fixture migration; interleaved-mutation pair; fixed-side win counting (draws-as-losses).

**Risks, stated plainly:**
- Given Slices 4–7B (all search configs 0.42–0.54 vs this anchor), it is **likely no champion passes before 2026-08-16**; the ladder keeps the rescue pair. This is the protection Brad chose; `ANCHOR_BAR` is one named constant if he later relaxes it.
- Statistical behavior at n=200: a true-parity champion false-passes ~8%; a true-0.55 champion passes ~50%. The bar is a one-sided evidence threshold, not a point estimate of truth.
- Anchor series consumes ~200 games/generation of shared pool throughput (~35–60 min) — negligible against generation length.
