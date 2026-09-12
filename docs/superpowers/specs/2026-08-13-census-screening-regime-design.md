# Census/Screening Regime Update — Design Spec (2026-08-13)

## Context & goals

Brad's directive: among the remaining pool decks, prioritize lexicographically by (fewest energy cards) then (fewest Pokémon cards) — a trainer-dense bias. The rationale is compositional: energy slots trade against Pokémon slots, and Pokémon slots trade against trainer slots, so minimizing energy-then-Pokémon maximizes trainer density. The strongest sustained ladder identities support this bias empirically — the counted pair scores 597.7/555.7, and the champion runs 32 trainers / 8 Pokémon / 20 energy.

The directive applies to **census screening order AND pool pruning/culling**. It explicitly does NOT apply to breeding/offspring selection — Brad's call, which keeps the directive an ordering/pruning policy rather than a population-shaping force during the freeze window.

A second item is folded into this slice: **FLOOR_BAR/ANCHOR_BAR recalibration**. Both bars were calibrated against the retired anchor, and the regime has since shifted twice — the anchor swap on 2026-08-11 and the pool repair with coverage reset on 2026-08-13.

The feature operates within the existing hard pool rules (MIN_BASIC_CARDS=8, attack-payability). Operating constraints: the freeze window (SUBMIT_HOLD stays ON until 2026-08-17) and a thin disk margin (no large artifacts; any DB copies used by probes must be deleted afterward).

## Decision record (all Brad-approved via AskUserQuestion, 2026-08-13)

1. **Priority = TIE-BREAK within coverage order** — not composition-first, not a scored blend.
2. **Cull = measure first**, then a Brad-gated threshold via AskUserQuestion before the one-shot migration runs.
3. **Bars = percentile-anchored** — FLOOR_BAR set so a chosen fraction of the current healthy pool fails at birth; ANCHOR_BAR set from the baseline lineage's measured WR plus a margin.
4. **Count delivery = materialized columns on `decks`** — not per-tick Python computation.
5. **Phase-B fold decision:** one slice covering both priority ordering and bar recalibration.

## Verified landmarks (from pre-brainstorm code scan, 2026-08-13)

- **Screening selector:** `census.py:74-86` `_CANDIDATES_QUERY`, `ORDER BY rating ASC NULLS FIRST, games_played ASC, concept_id ASC`; driver `census.schedule_screening_games` (`census.py:110+`) called from `loop_scheduler.py:461`; `SCREENING_FLOOR=15` (`census.py:47`).
- **`promote_proven_singles`:** `census.py:456`; criteria = untested + single-core + `games_played>=15` + `rating IS NOT NULL` — NO rating bar, NO `distinct_opponents` check.
- **Floor:** `FLOOR_BAR=0.40` at `floor.py:50`, sole production consumer `floor.py:291`; `FLOOR_GAMES=50`, `FLOOR_MAX_ATTEMPTS=3`; re-pick mechanics `floor.py:313-334`; on exhaustion the offspring is trashed (`floor.py:336-339`).
- **Anchor:** `ANCHOR_BAR=0.55` at `anchor.py:40` (`ANCHOR_GAMES=200`); consumers `anchor.py:260` (verdict) and `subscheduler.py:529` (display only); elect-pass promotes the baseline in the same transaction, elect-fail trashes (`anchor.py:296`).
- **Culling today is UI-only** (`ui_actions.py:155/191`, bulk `:208+`, legacy `ui_server.py:126` finalist-guarded) plus one-shot migration scripts. NO scheduler-side concept culling exists.
- **Composition counting scheduler-side already exists:** `builder.py:421-438` `brad_bounds_problems` (energy count at `:432`, basics at `:435`); `validate.py:16-24` card DB; `census.py:30` imports `builder` — the card DB already loads on the scheduler side. `deck_quality.py` stays UI-only (importers: `ui_pages`, `ui_server` only).
- **Live DB state at design time:** concepts active=42,070 / culled=98,247 / untested=192,215 (all dormant pairs, zero coverage rows) / finalist=1; only 358 active concepts have `games_played>=15`; the screening queue is effectively the active population; the untested clause of `_CANDIDATES_QUERY` is currently inert; `promote_proven_singles` currently matches zero rows.
- **Schema:** no priority/order column exists on concepts/decks/coverage (`deckdb.py:73-92,144-148`); only `games.priority` exists.

## Section 1 — Composition-count materialization

The `decks` table gains two nullable INTEGER columns: `energy_count` and `pokemon_count`. Trainer count is derivable (60 − energy − pokemon) and is deliberately not stored.

**Counting definition:** the same card-type classification `builder.py` uses — BASIC_ENERGY + SPECIAL_ENERGY for `energy_count`; all Pokémon stages for `pokemon_count`.

**Stamping:** the counts are stamped at every deck-insert site going forward — builder deck creation, `deck_repair` output, and breeding offspring decks. Stamping breeding output is data hygiene and does not violate the breeding exclusion, which is about prioritization, not about recording counts.

**Backfill of existing rows happens ONLY in a one-shot go-live migration script** (`BEGIN IMMEDIATE`, idempotent, adds the supporting index). The backfill is deliberately NOT placed in `deckdb.init_db()` — that keeps the change fully inert mid-slice, since the watch loop calls `init_db` every ~15 minutes.

## Section 2 — Screening-order change

`_CANDIDATES_QUERY`'s ORDER BY becomes:

```
rating ASC NULLS FIRST, games_played ASC, energy_count ASC, pokemon_count ASC, concept_id ASC
```

with `concept_id` last as the deterministic final tiebreak. The composition keys are tie-breaks WITHIN coverage order, not a composition-first re-screen: in the CURRENT pool the energy key is inert among the unrated mass (energy_count is uniformly 16 there), so the ordering directive within that mass is carried by pokemon_count, not energy_count. Rated lean-energy concepts sort behind the unrated mass by design, preserving worst-first re-rating semantics once coverage builds.

- NULL counts sort LAST — an unstamped deck never jumps the queue.
- The join shape from coverage/concepts to the concept's deck(s) is pinned at plan phase — the plan must specify the exact aggregation (e.g., MIN over the concept's decks) — and it must stay index-backed.
- The new query shape gets an EXPLAIN QUERY PLAN guard test in `tests/test_factory_lock_access_paths.py` (no SCAN on a lock-held path).

## Section 3 — Measurement script

A new read-only script (e.g., `scripts/measure_screening_regime.py`) produces:

- (a) the composition distribution of the active pool (energy/Pokémon/trainer histograms);
- (b) WR-vs-anchor across composition strata via in-process games — a stratified sample of ~40 decks × 50 games ≈ 2,000 games (minutes of wall time at the measured heuristic speed of ~0.02s/game), run TWICE per `.claude/rules/stochastic-gate-replication.md`, with both runs reported.

Output is a small markdown/JSON receipts artifact in `experiments/`. The script is disk-light; any DB copy it uses must be deleted by the same task (the diagnose-before-dispatch cleanup corollary).

Measurement runs against the CURRENT post-swap, post-repair pool — never against historical/stale data (the empirical-check-against-wrong-regime rule).

## Section 4 — Cull migration (Brad-gated)

After measurement, an AskUserQuestion presents the histogram plus proposed thresholds BEFORE any cull executes. No cull runs without Brad's explicit approval of the threshold.

The migration is a one-shot script following the min-basics/unpayable pattern: a single `BEGIN IMMEDIATE` transaction; reversible (`status='culled'`, `reason='composition-rule'`); deck rows retained (for pair-gate evictee reconstruction); champion/anchor/finalist protected; idempotent, with a violating=0 re-run receipt.

## Section 5 — Bar recalibration

- **FLOOR_BAR:** set so a Brad-chosen fraction of the current healthy pool fails at birth; the percentile is proposed with the measurement data in hand.
- **ANCHOR_BAR:** the baseline lineage's measured WR plus a margin.

The constants are updated in place (`floor.py:50`, `anchor.py:40`) with provenance comments. A full grep sweep of tests asserting 0.40/0.55 runs per the constant-bump rule — all assertion sites updated in ONE task.

## Section 6 — Inertness & go-live

**Inert during the slice:** `census.py`/`floor.py`/`anchor.py` load only in the long-lived scheduler, which is restart-gated; the watch-loop import graph (`subscheduler`/`episodes`/`deckdb.init_db`) is untouched — the import-graph walk was done at design time; migrations run only at go-live.

**Go-live sequence:**

1. Column migration + backfill.
2. Restart `ptcg-factory-scheduler` + `ptcg-factory-runner`.
3. Verify the new ordering via a real scheduling-tick receipt.
4. Brad-approved cull migration.
5. Bars go live with the restart.

SUBMIT_HOLD stays ON throughout. Go-live command lines get `--help` reconciliation at Finish (`.claude/rules/golive-command-preflight.md`).

## Section 7 — Testing

- Count computation pinned against known decks.
- Stamping covered at every insert site — consumer sweep per `.claude/rules/test-coverage-sweep.md`, since `build_deck`'s output shape effectively changes.
- ORDER BY behavior verified against a mixed-composition fixture DB.
- Migration idempotency plus a virgin-path test (the never-created-parent-directory class).
- The EXPLAIN QUERY PLAN guard.
- The bar-constant assertion sweep.
- Floor re-pick mechanics unchanged and still covered.

## Out of scope

Breeding/offspring selection logic; UI changes (the `deck_quality` badges already exist); any submission-path change; pagefile/disk work (Phase A closed separately).
