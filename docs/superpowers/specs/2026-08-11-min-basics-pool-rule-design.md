# Min-Basics Pool Rule — Design Spec (2026-08-11)

Status: APPROVED by Brad (AskUserQuestion, 2026-08-11). TIER: F.

## Motivation

Brad's directive: "decks need to have a minimum of 8 basic pokemon. anything less should be removed." Confirmed metric: 8 basic Pokemon CARD COPIES (matching `deck_quality.py:129-130`'s `basics_count`, which sums copy counts of cards with `cardType==POKEMON` and `basic==True`). Confirmed application: STRICT — the rule applies to the anchor and champions too.

Mechanism this targets: with 4 basics in 60 cards the mulligan probability is ~60%/game, but the current anchor also has 4 basics, so the census anchor-rating is largely mulligan-blind (both sides pay the cost symmetrically). On the Kaggle ladder against opponents with normal basic counts the penalty is asymmetric. The local eval cannot see the weakness this rule targets.

Ground truth (read-only probe of live `tournament.db`, 2026-08-11 ~10:15 HST):

- Anchor `anchor-mega-lucario-fighting-d0`: 4 basic cards (4 copies of 1 species)
- Champions v0.22/v0.23/v0.24 (current baseline): 4 basics each
- Active pool: 1 concept (4 basics); census re-screen mid-flight, ~236,625 untested concepts
- The 95,905 historically culled junk decks had mostly 12-31 basics
- A <8-basics filter culls 100% of the current healthy pool; enforcement design must therefore rebuild the pool, not just filter it.

## Locked decisions (Brad, via AskUserQuestion)

1. Metric: >=8 basic Pokemon card copies per 60-card deck.
2. Application: strict — cull everything below, INCLUDING the current anchor and champions.
3. New anchor: chosen by a mini-tournament among 4 candidate >=8-basic decks.
4. Enforcement: the builder GUARANTEES the minimum at construction; `validate_deck` hard-rejects as safety net.
5. Sequencing: full speed — go live immediately after merge; pair-gate guards the Kaggle counted pair (v0.24/v0.22); freeze-day (~2026-08-13) curation plan unchanged.

## Section 1 — Invariant and enforcement

- Single constant `MIN_BASIC_CARDS = 8`, single source of truth (in `ptcg.decks.validate` or adjacent; planner picks exact home and greps landmarks pre-lock).
- `validate_deck` (`ptcg/decks/validate.py`) gains a hard check: any deck with fewer than 8 basic-Pokemon card copies is illegal. This covers every entry point that already calls the oracle: breeding mutate/crossover (`src/ptcg/factory/breeding.py:141,182,187,202`), census builds (`src/ptcg/factory/census.py:252,412`), deck builds (`src/ptcg/factory/builder.py:254`, extra bounds `:332`), and bundle builds.
- `builder.py` fill algorithm GUARANTEES >=8 basics: when a concept's cores don't supply 8 basic copies, the filler deterministically adds compatible basic lines (consistent with existing fill determinism). Concepts structurally unable to reach 8 basics fail the build and are trashed via the existing unbuildable path (`census.py:415`).
- `deck_quality.py` mulligan-risk flag recalibrates to flag basics < 8 (was <=2); calibration comment updated. UI badges must agree with the pool rule.

## Section 2 — Anchor mini-tournament

- Author 4 candidate anchor decks, each with >=8 basic copies (max 4 copies/name implies >=2 basic species lines), distinct shapes. One candidate is a modified mega-lucario (continuity candidate: keep the fighting core, add a second basic line, cut 4 lowest-value cards). Others drawn from card-pool analysis (`uv run python -m ptcg.decks.analysis`) and deck-findings memory.
- Each candidate passes `validate_deck` (new rule) + bundle smoke before playing.
- Round-robin: C(4,2)=6 pairs x 200 games, heuristic-v0 both sides, in-process (~0.022s/game — approx 30s total compute; run as an orchestrator-owned foreground/background script per `dispatch-test-run-directive.md`, NOT inside a waiting subagent).
- Crowning: highest pooled win rate; tie broken by head-to-head. Record every candidate's WR vs the OLD anchor as a strength reference (reference only, not a gate).
- Result recorded in `experiments/EXPERIMENTS.md`. New anchor registered as the finalist concept (`anchor-<name>-d0` pattern); old anchor culled (status change).

## Section 3 — One-shot pool migration script

- New script following the `scripts/reseed_tournament_pool.py` precedent: `--db` REQUIRED (never defaults to production; help text says so), dry-run mode, loud printed receipts (counts per action).
- Actions: (a) cull every built deck with <8 basics — this is ALL current built decks, champions and old anchor included; (b) RETAIN concept rows and culled deck rows — cull is a status change, NEVER a row delete; (c) reset every concept's rating/coverage (`games_played`, `distinct_opponents`, `rating`) to untested; (d) install the new anchor as finalist.
- After migration, the census re-screen rebuilds concepts under the new builder — the ~236k-concept exploration space re-enters as COMPLIANT builds rated against the NEW anchor. No hand-authored reseed set is needed; the builder guarantee does the reseeding.
- EXPLICIT OVERRIDE: this supersedes the 2026-08-04 one-way exploration freeze ("no further deck diversification post-reseed"). Brad's strict decision overrides it; this spec is the record.
- Migration transactions follow `BEGIN IMMEDIATE` single-transaction discipline (`.claude/rules/single-actor-worker-tests.md`); any new query on a large table gets `EXPLAIN QUERY PLAN` + production-scale timing before review sign-off (access-path-analysis clause).

## Section 4 — Submission continuity

- Pair-gate (`src/ptcg/factory/pairgate.py`, `PAIR_GATE_BAR=0.55`) and freeze-day curation plan are UNCHANGED.
- DESIGN-CRITICAL INVARIANT: culling v0.22/v0.24 must NOT break pair-gate evictee reconstruction. If the gate fail-closed because an evictee's rows vanished, ALL uploads would silently stop. Hence cull-is-status-change-never-delete, plus an explicit regression test: a culled ex-champion still reconstructs for the head-to-head.
- Pipeline re-founds: baseline/champion-elect state resets (founding-generation semantics in `loop_state`); the next champion is new-regime and uploads only if it beats the to-be-evicted counted submission 0.55/200 as usual.
- Ladder identity files (`src/ptcg/submission_main.py`, `src/ptcg/agents/current.py`) are UNCHANGED by this slice (standing invariant; verify empty diff at Finish).

## Section 5 — Hold and go-live

- Inertness for the implementation window: PAUSE file ONLY (stops all three workers, no UAC elevation — deliberately avoiding `Disable-ScheduledTask` after the 2026-08-10 two-day unanswered-UAC incident). Taken as Task 1 of the plan with a timestamped receipt proving hold-set precedes first watch-loop-reachable write (settled template per `.claude/rules/factory-resume-probe.md`). Import-graph walk of `factory_watch_once.py` required at plan time since `census.py` (watch-loop-imported) is touched.
- Cost accepted: old-regime breeding pauses during implementation; its lineage is doomed under the new rule anyway.
- Go-live rungs (post-merge, explicit in plan per `.claude/rules/golive-command-preflight.md` — reconcile every command line against script `--help` at Finish): (1) run migration script for REAL against live `tournament.db`; (2) `Stop-ScheduledTask`/`Start-ScheduledTask` `ptcg-factory-runner`, `ptcg-factory-scheduler`, `ptcg-factory-ui` (non-elevated stop/start; long-lived workers do not hot-reload); (3) lift PAUSE; (4) verify: `scripts/verify_factory_tasks.ps1` PASS, first watch-loop firing paired markers, census re-screen throughput receipt under the new anchor, and pair-gate still reconstructs evictees.
- Any step that unexpectedly requires UAC elevation is a BLOCKING HUMAN-INPUT REQUEST surfaced loudly to Brad (2026-08-10 addendum rule).

## Section 6 — Testing

- Builder guarantee: unit tests over concepts with 0/1/2 basic lines; a sweep asserting >=8 basics on builds from a sample of REAL concepts (production DB copy), not fixtures only.
- `validate_deck`: rejection tests, including a mutation path that would drop basics below 8 (rejected/re-rolled).
- Migration script: fixture-DB tests AND a dry-run against a copy of the production DB with printed receipts at review time (diagnose-before-dispatch review-time executable-receipt rule). Virgin-directory rule applies to any first-write artifact the script creates.
- Pair-gate reconstruction regression: culled ex-champion reconstructs and the gate can play the head-to-head.
- Concurrency: any changed step function keeps read-decide-act inside one `BEGIN IMMEDIATE`; concurrency receipts follow the barrier+counter+overlap pattern (`tests/fixtures/race.py`), no tautological receipts.
- Stochastic notes: the mini-tournament is an argmax, not a pass/fail gate near a bar — single run acceptable; record all series rows in `EXPERIMENTS.md`. Any future gate near its bar follows stochastic-gate-replication.

## Out of scope

- No changes to pair-gate bar, daily cap, subscheduler cadence, or the disabled daily floor probe.
- No changes to ladder identity files.
- No agent/search-config changes — this is a deck-pool rule slice only.
