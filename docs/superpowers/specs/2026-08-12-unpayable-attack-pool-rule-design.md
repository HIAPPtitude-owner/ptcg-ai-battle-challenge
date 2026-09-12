# Design: Unpayable-Attack Pool Rule — strict payability + multi-type builder + rebuild-everything migration

**Date:** 2026-08-12 · **Tier:** F · **Second lifecycle of this session** (after freeze-curation go-live; SUBMIT_HOLD is ON, counted pair protected refs 55467338/55467335).

## Problem

`deck_quality`'s `unpayable-attack` flag (src/ptcg/factory/deck_quality.py:171-193, amber) fires on 16,357/17,447 active decks (93.7%, ±0.1% live drift). Root cause is construction, not breeding: `builder.build_deck` funds only the core's best attack (builder.py:284-285) and `_pad_with_basics` (builder.py:239-256) selects filler basics with no energy-type input. Measured facts (2026-08-12, read-only probes against production tournament.db):
- P2 = P3 = 0: every deck's core best attack is payable by construction; no deck is fully attack-dead.
- 13,200/16,357 flagged decks (80.7%) are unpayable ONLY via fillers; ~3,157 via core secondary attacks needing a 2nd energy type.
- 642/708 core species are mono-payable; ~40 need 2+ types; ~37 have an outright off-type attack.
- Filler supply: 595 basic Pokémon; 158 colorless-only-attack basics (universally safe); 213-224 payable candidates within each of the top-3 energy types. No attackless basics exist.
- `coverage` keys on concept_id (deckdb.py:144-147); `games` reference deck ids (deckdb.py:94-103) → in-place deck rebuild under the same deck id preserves referential integrity; coverage must still be reset (cards changed → rating invalid).

## Decisions (Brad-approved 2026-08-12)

1. **Strict rule**, type-coverage semantics only (quantity out of scope), matching the flag exactly.
2. **Rebuild everything**: multi-type builder so core-level cases rebuild payably too — no concept-space loss to this rule.
3. Runs this session, after the curation go-live (done).

## Design

**R1 — validate_deck rule (src/ptcg/decks/validate.py).** For every Pokémon card, for every attack (1-based attackId lookup per the attackId convention), the set of non-COLORLESS cost types must be ⊆ the deck's energy types (basic+special energy cards). Violation message names the card and attack. This is the single source of truth; consumers align by construction (min-basics pattern).

**R2 — Builder multi-type energy allocation (src/ptcg/factory/builder.py).** Energy requirement set = union of typed costs across ALL core attacks (not just `_best_attack_for`). Invariants: (a) total energy card count unchanged from the current allocation; (b) the primary type (best attack's) keeps the majority; (c) each secondary type gets a minimal splash ≥ the max single-attack cost of that type; (d) **mono-payable cores produce bit-identical energy allocation to today** — only 2-type cores change, bounding competitive risk to ~5% of species. Exact split formula is a plan-phase decision under these invariants.

**R3 — Payability-aware fillers (builder.py `_pad_with_basics`/`_filler_basic_order`).** Filter filler candidates to basics whose every attack is payable within the deck's energy type set; colorless-only basics always qualify. Deterministic order preserved (stable filter, no RNG).

**R4 — Breeding (src/ptcg/factory/breeding.py).** No new logic required — `breed_deck` already defers acceptance to `validate_deck` (breeding.py:182). The plan must verify the retry/attempt bound still yields children at an acceptable rate under the stricter rule (measure locally, real breeding calls, not a fixture) and, if child-yield collapses, fix by applying the R2 energy-allocation helper to the child's cores rather than loosening the rule.

**R5 — Migration (scripts/migrate_unpayable_pool.py, min-basics template: read-only classification pre-pass → single BEGIN IMMEDIATE with drain guard → writes → receipts; --dry-run rolls back; idempotent).**
- Scope: every deck row belonging to a non-culled concept (actives AND untested with materialized deck rows; all shell variants). Quantify counts in the dry-run receipts.
- Action: violating decks are REBUILT IN PLACE (same deck id, new cards from the R2/R3 builder); rebuilt concepts get coverage reset (games_played/distinct_opponents/rating zeroed) → genuine re-screen.
- **Exclusions (never rebuilt): decks referenced by any `baselines` row, the anchor concept, finalists.** The pair-gate reconstructs evictees from these rows (tests/test_factory_pairgate.py pins this); rewriting their cards would falsify reconstruction of what was actually uploaded. Today's counted pair is ledger-sourced (immune), but future tournament evictees are not.
- A concept whose core cannot produce a valid deck even under R2/R3 (if any exist beyond the measured ~40 — quantified at dry-run) is culled with reason `unpayable-rule: <iso>`; expected near-zero.
- Canary receipt: post-migration `unpayable-attack` flag count over the active pool ≈ 0 (excluded rows may still flag; report separately).

**R6 — deck_quality flag unchanged.** It becomes the standing canary; no UI changes.

## Error handling

Migration: drain-guard abort (rollback, loud); rebuild failure for a specific deck → cull that concept with reason, count in receipts, continue (never leave a half-rebuilt row — the rebuild write is per-deck atomic within the one transaction). validate_deck violations raise with card+attack named.

## Testing

- validate_deck: new rule cases (payable passes; filler off-type fails; core secondary off-type fails; colorless-only always passes; special-energy counted in type set per current flag semantics — mirror deck_quality.py:168-169).
- Builder: mono-payable core → bit-identical deck vs pre-change builder (golden pin); 2-type core → all attacks payable, total energy count unchanged, primary majority; filler filter determinism.
- Migration: tmp-DB fixture with all row classes (filler-only, core-2-type, excluded baselines-referenced, anchor, already-payable, untested-with-deck); idempotency (second run = zero-change receipts); dry-run rollback leaves DB byte-identical; drain-guard RED case. Virgin-path rule applies to any new first-write artifact.
- Consumer test sweep per .claude/rules/test-coverage-sweep.md (validate consumers: builder, breeding, census, package_submission; one-hop: ui_server via deck_quality — the rule changes a threshold's MEANING, so do the consumers-of-consumers walk).
- Ladder/anchor sanity: the anchor deck (anchor-min8.csv) and the ladder deck (submission_main's mega-lucario-fighting) must PASS the new validate_deck rule — verify at plan pre-lock with an executable check, not assumed. If either fails, STOP: that is a scope conflict for Brad, not an implementer judgment call.

## Go-live (post-merge rungs)

1. Import-graph walk receipts: confirm which running processes import builder/validate (long-lived scheduler/runner load only on restart; narrowed watch loop's graph verified at plan time). SUBMIT_HOLD stays ON throughout (uploads impossible regardless).
2. Merge → restart `ptcg-factory-runner` + `ptcg-factory-scheduler` (Stop/Start-ScheduledTask, non-elevated; verify next block provenance carries the merge commit per .claude/rules/factory-task-scheduler-liveness.md).
3. Real migration: `--dry-run` first (receipts reviewed), then real run inside the same session; receipts recorded in EXPERIMENTS.md + plan.md.
4. Canary verification: unpayable-attack flag ≈ 0 over active pool; census re-screen resuming (expect a rating-throttle stall like post-reseed — not an anomaly).
5. Disk check (11 GB free as of 11:20 HST; overnight fill cause still unidentified).

## Out of scope

Energy-quantity payability; FLOOR_BAR/ANCHOR_BAR recalibration (standing carry-forward); UI changes; multi-type support beyond 2 types (cap at 2; cores needing 3+ are culled with reason — quantify in receipts, expected ~0).

## Amendments (2026-08-12, Brad-approved at plan gate)

(a) **R5 migration uses a deterministic repair_deck primitive** (filler swaps + minimal secondary splice, 2-type cap, unrepairable → culled) instead of builder-rebuild. Pool decks include mutated reseeds `build_deck` cannot reproduce; repair is the right scope, not full rebuild. Migration receipts quantify `chain_delta` (energy type changes per deck).

(b) **Payability scope corrected to ALL chain-stage attacks** of every deck Pokémon (flag semantics), not just core-card attacks. validate_deck rule applies uniformly; R2 builder applies to cores only; R3 filler filter applies to baselines only. Migration fix scope: repairable chains only; unrepairable cards culled. Migration receipts are counts (incl. `chain_delta`); culled concepts carry `reason='unpayable-rule: <iso>'` (queryable) and are reversible via the UI restore path (no `decisions` audit row → restore-to-`untested` with coverage zeroed).
