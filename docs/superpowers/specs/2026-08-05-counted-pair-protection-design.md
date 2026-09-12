# Design Spec: Counted-Pair Protection (2026-08-05)

**Status:** APPROVED (Brad, 2026-08-05)
**Slice:** counted-pair-protection
**Deadline context:** final Kaggle submission deadline 2026-08-16; convergence-freeze checkpoint ~2026-08-13.

## Overview

Add a ladder-merit gate ("pair gate") between the tournament pipeline's champion
promotion and its automated Kaggle upload, so that no automated upload can evict a
counted submission it has not beaten head-to-head. Six components: a pair-gate
head-to-head series run by the existing runner workers, an upload-gate extension in
the submission scheduler, a TOCTOU guard at upload time, disabling the daily floor
probe until after the deadline, a manual freeze-day curation script, and
cosmetic/housekeeping fixes (description evidence, untracked net weights, doc
drift).

Promotion semantics are deliberately UNCHANGED: the 200-game anchor check at bar
0.55 remains the sole promotion gate. Breeding progress and ladder merit are
decoupled — the pair gate governs uploads only.

## Motivation

The generational champion tournament autonomously uploads every anchor-passing
champion (200-game anchor check, bar 0.55) to Kaggle. Kaggle counts only the TWO
MOST RECENT submissions and evicts by RECENCY — a new upload evicts the OLDER
counted submission, regardless of score (see
`.claude/rules/platform-mechanics-model.md`).

Overnight 2026-08-05: v0.11 (anchor 0.70) scored 558.5 — the first
tournament-bred champion inside the 520–575 ladder band — but v0.13 (anchor 0.63)
scored 486.4 (below band) and evicted the curated rescue pair. Two facts follow:

1. **The local anchor bar does not reliably map to an in-band ladder score.** A
   0.07 local win-rate difference corresponded to 72 ladder points.
2. **The tournament submit path has no counted-pair protection.** Any
   anchor-passing champion, however weak on the ladder, can evict a counted
   submission purely by being newer.

With the final submission deadline 2026-08-16 and the convergence-freeze
checkpoint ~2026-08-13, an unprotected eviction is now the single most expensive
failure the factory can produce.

## Design

### 1. Pair-gate head-to-head series

When a champion-elect passes the anchor check and is promoted to baseline
(promotion semantics unchanged — anchor at 0.55 remains the promotion gate),
enqueue a new series with `purpose='pair_gate'` (200 games) into the existing
runner pipeline:

- **Matchup:** new champion vs the exact config (agent genome + deck + net
  weights) of the TO-BE-EVICTED counted submission — the OLDER of the two most
  recent uploads, resolved from the submission ledger + tournament DB.
- **Execution:** games are played by the runner workers; nothing runs inside the
  watch loop.

### 2. Upload gate extension

The submission scheduler's pre-upload gate (currently: anchor verdict == `'pass'`)
gains a second required condition: **pair_gate verdict == `'pass'`**, defined as
wr >= 0.55 over 200 games vs the to-be-evicted config.

- **Verdict pending** → skip this mark; the existing `baseline_changed` retry
  logic re-attempts at later marks for free.
- **Verdict fail** → the champion is never uploaded but REMAINS baseline and
  parents the next generation (Invariant I3).
- **FAIL-CLOSED:** if the to-be-evicted submission cannot be reconstructed
  locally (legacy/rescue bundle, missing genome), block the upload and emit a
  loud log line for manual review — never upload ungated.

### 3. TOCTOU guard

The to-be-evicted opponent can change between pair-gate enqueue and upload
(another upload, manual curation). At upload time, inside a single
`BEGIN IMMEDIATE` transaction, re-verify that the pair-gate verdict references
the CURRENT to-be-evicted submission. A stale verdict does not pass — it triggers
re-enqueue against the correct opponent.

Ships with an interleaved-mutation race test using `tests/fixtures/race.py`
(barrier + sqlite trace-callback statement counter + overlap assertion + RED run
against de-guarded code — per `.claude/rules/single-actor-worker-tests.md`; no
tautological receipts).

### 4. Daily floor probe disabled

The 24h floor probe (subscheduler uploads the baseline agent on an alternate
unprobed deck) is disabled behind a config/constant with an explicit re-enable
note dated after 2026-08-16.

**Rationale:** an ungated upload path that can evict a counted submission is not
worth exploration signal this close to the deadline.

### 5. Freeze-day curation script

`scripts/curate_counted_pair.py`, **MANUAL-ONLY** (never called by any scheduled
task):

1. Takes two candidate identities.
2. Builds + verifies both bundles via the existing bundle builder.
3. Uploads them IN ORDER with best LAST (best = newest = survives one more
   eviction).
4. Confirms both via the submissions API.
5. Sets `SUBMIT_HOLD`.

Includes `--dry-run`. Per the dryrun-is-not-the-real-thing lesson (global
CLAUDE.md), the real path must fail loudly (nonzero exit on any API failure),
order destructive/irreversible effects safely (verify bundles BEFORE any upload;
do NOT set `SUBMIT_HOLD` on partial success), and be reviewed as if it will fail.

### 6. Cosmetic + housekeeping

- **(a) Description string:** replace the merit segment's 15-game census
  screening figure (`census.SCREENING_FLOOR`) with the actual gate evidence —
  200-game anchor wr + pair-gate result.
- **(b) Net weights:** commit the 6 untracked live-referenced net-weight JSONs in
  `src/ptcg/search/` (`value_net_weights_c-*.json` +
  `value_net_weights_reseed-mut-2c9c978007d2-sv0.json`).
- **(c) Doc drift:** correct CLAUDE.md (and any rules/memory text encountered
  in-repo) describing "staged playoffs → Bo1001 grand final" — the code
  implements a flat 200-games-per-pair round-robin CROWN; no Bo1001 exists.

## Invariants

- **I1:** The counted pair's minimum quality never decreases due to an automated
  upload — every automated upload must have a pass verdict vs the submission it
  evicts.
- **I2:** No automated upload path exists that bypasses the pair gate (floor
  probe disabled; the only other path is the manual curation script, which is
  human-triggered).
- **I3:** Promotion (baseline advance) is independent of upload decisions — a
  pair-gate fail never blocks breeding progress.
- **I4:** The pair-gate verdict used at upload time always references the
  currently-to-be-evicted submission (TOCTOU guard).

**I1/I4 are multi-actor invariants** → they require adversarial concurrent tests
per `.claude/rules/single-actor-worker-tests.md`, not single-actor tests.

## Verified Landmarks

All landmarks verified via Grep against the working tree at spec-write time
(2026-08-05):

| File | Landmark | Location |
|------|----------|----------|
| `src/ptcg/factory/subscheduler.py` | `MARK_SECONDS = 17280` (4.8h marks) | :67 |
| `src/ptcg/factory/subscheduler.py` | Strength-gate anchor check (`anchor_status` verdict branch) | :393-402 |
| `src/ptcg/factory/subscheduler.py` | Mark + `baseline_changed` upload condition | :409-422 |
| `src/ptcg/factory/subscheduler.py` | Daily floor probe branch | :424-449 |
| `src/ptcg/factory/subscheduler.py` | `_baseline_candidate` | :234-263 |
| `src/ptcg/factory/subscheduler.py` | `_anchor_screening_games` (+ denominator comment block) | :211-232 |
| `src/ptcg/factory/submit.py` | `_merit_segment` — `"anchor-wr {x} of {n} games"` | :26, :48 |
| `src/ptcg/factory/submit.py` | `submission_description` | :52-86 |
| `src/ptcg/factory/anchor.py` | `ANCHOR_GAMES = 200` / `ANCHOR_BAR = 0.55` | :29-30 |
| `src/ptcg/factory/anchor.py` | `resolve_anchor_check` (promotion) | :192-285 |
| `src/ptcg/factory/anchor.py` | `anchor_status` | :288-310 |
| `src/ptcg/factory/harvest.py` | `counted = {c.id for c in submitted[:2]}` (Kaggle: two most recent count) | :102 |
| `src/ptcg/factory/gate.py` | `counted_submissions` + beat-the-weakest legacy logic | :121, :137, :177-184 |
| `src/ptcg/factory/loop.py` | `CROWN_GAMES_PER_PAIR = 200` (flat round-robin, C(K,2) pairs) | :584 |
| `src/ptcg/factory/loop.py` | `resolve_crown` (champion-ELECT nomination) | :695-818 |
| `src/ptcg/factory/census.py` | `SCREENING_FLOOR = 15` | :47 |

## Testing

- **Unit tests per component.**
- **Provenance shapes:** parametrized provenance-shape tests for
  counted-submission reconstruction — tournament champion / legacy bundle /
  floor-probe upload / missing genome — per
  `.claude/rules/provenance-shaped-optional-fields.md`. Reconstruction failure
  must fail closed (block upload, loud log line).
- **I1/I4 race test:** the interleaved-mutation test from Design component 3
  (barrier + statement counter + overlap assertion + RED run against de-guarded
  code, via `tests/fixtures/race.py`).
- **Curation script:** tests for ordering (best-last), partial-failure behavior
  (no `SUBMIT_HOLD` on partial success), and `--dry-run` vs real-path divergence.
- **Rung-3 smoke at go-live:** observe one live watch-loop firing with the gate
  active and a pending pair-gate verdict, verifying skip-and-retry in
  `experiments/factory/logs/watch.log`.

## Go-Live / Safety

- **SUBMIT_HOLD before write:** `SUBMIT_HOLD` is set BEFORE any
  subscheduler-reachable code is written to the working tree — the watch loop
  imports the working tree live every ~15 minutes (see
  `.claude/rules/factory-resume-probe.md`, "live-on-write" section). It is
  lifted only at the post-merge go-live rung.
- **Post-merge go-live rung:**
  1. Restart runner/scheduler workers if their code paths changed (long-lived
     processes don't hot-reload).
  2. Lift `SUBMIT_HOLD`.
  3. Verify the next firing's gate lines in `watch.log`.
- **Command-line preflight:** all go-live command lines get flag-by-flag
  `--help` reconciliation at Finish per
  `.claude/rules/golive-command-preflight.md`.

## Out of Scope

- **Anchor-bar recalibration** — 0.55 stays as the promotion bar; the pair gate
  handles ladder merit.
- **Breeding-side changes**, the R2 faucet, and any evolution-legacy code.

## Current State Snapshot (for the record, 2026-08-05)

- **Counted pair:** v0.13 (486.4, newer) + v0.11 (558.5, older → next to be
  evicted).
- **Baseline:** v0.13.
- **Submission counter:** 2/5 on 2026-08-05.
- **Ladder band:** 520–575.
