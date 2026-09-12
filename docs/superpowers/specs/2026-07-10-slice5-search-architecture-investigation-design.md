# Slice 5 — Search-Architecture Investigation: Design

## Context & Problem

Slice 4 proved the learned value net decisively beats the hand-tuned evaluator offline (validation AUC 0.8971 vs 0.7640 on 264k held-out positions), yet arena win rate against heuristic-v0 stayed at parity (pooled 299/600 = 0.498; replicated gate FAIL against the 0.55 bar). Evaluator quality is therefore NOT the search's binding bottleneck. This slice investigates the search architecture itself.

**Leading hypothesis (unmeasured until now):** timing measurements from Slice 2 (`search_begin` ≈ 0.8 ms, `search_step` ≈ 0.58 ms) imply that only ~9 full-depth iterations fit in the 200 ms per-move budget (~46 in 1 s), while the v0-improvement gate in `src/ptcg/search/searcher.py` (`deviate_min_visits=20`, `deviate_value_edge=0.12`, defaults at lines 63-64, gate check at lines 142-143) requires a challenger root child to accumulate at least 20 visits before the search may override heuristic-v0's move. With only ~9-20 iterations spread across all root children, the gate may be mathematically unable to fire — meaning Slices 2 and 4 may effectively have been measuring v0-vs-v0. Nothing currently instruments gate-fire rate: the searcher exposes only `iterations_run` / `begin_failures` / `step_failures` counters and records no per-decision provenance (did the search deviate, or did the gate veto it?).

## Approach (approved: Instrument → Diagnose → Fix → Gate)

The slice proceeds in four sections. Section 1 adds measurement with zero behavior change. Section 2 runs diagnosis experiments against the instrumented searcher. Section 3 applies fixes in evidence order, cheapest first, each validated by its own arena series. Section 4 is the acceptance gate that decides whether the ladder submission changes.

## Section 1 — Instrumentation (no behavior change)

Add a per-decision `SearchStats` record on the Searcher capturing:

- iterations achieved for the decision;
- the root-child visit distribution — at minimum the number of root children and the top child's visit count;
- v0's pick signature and the search's pick signature (the content signatures the tree already keys children by);
- `deviated` — the final move differs from v0's move;
- `gate_blocked` — the search's most-visited child differed from v0's move but the visit/edge thresholds vetoed the override;
- the existing `begin_failures` / `step_failures` counters.

`scripts/run_arena.py` aggregates these per-decision records into series-level columns: deviation rate, gate-blocked rate, mean iterations per decision, and decisions per game. These are printed at series end and logged into the `experiments/EXPERIMENTS.md` rows alongside the existing columns.

Guard test: with a fixed seed, enabling stats collection changes zero move choices — the searcher produces the identical move sequence with stats collection on and off.

## Section 2 — Diagnosis experiments

- **D1 — Budget scan.** Instrumented mirror series (search vs heuristic-v0, mega-lucario mirror decks) at 200 ms, 500 ms, 1 s, and 2 s per move, roughly 100 games each. Output: how iterations per decision, deviation rate, and gate-blocked rate scale with budget. This directly tests the gate-can't-fire hypothesis.
- **D2 — Match-clock math.** Harvest the decisions-per-game distribution from D1's logs and compute the maximum per-move budget that is provably safe under the 10-minute match clock with at least a 2× safety factor for unknown Kaggle hardware. Also measure per-move deadline overshoot: a single in-flight iteration is not interrupted at the deadline, and recorded maximum move times were 0.24-0.44 s at a 200 ms budget, so the overshoot distribution must be part of the envelope calculation.
- **D3 — Gate-off ablation.** Zero the deviate thresholds and run 200-300 games at 200 ms and again at 1 s. Decomposition of outcomes: if the ungated search LOSES, the gate was masking determinization noise, which points toward noise fixes and better priors; if the ungated search WINS, the gate was strangling a real edge, which points toward a gate retune plus a budget raise.
- **D4 — Determinization-noise probe.** At ~50 sampled decision points, rerun the full search K times with fresh determinization streams; measure agreement of the picked move and the variance of the root value estimates across reruns. This quantifies the noise floor the search operates above. Script-level only — no arena games.

## Section 3 — Fix ladder (evidence-ordered, cheapest first)

- **F1 — Gate retune.** For example, express the visit threshold as a fraction of achieved iterations rather than the absolute 20, and possibly lower the value edge.
- **F2 — Per-move budget raise**, kept strictly within the proven-safe envelope computed in D2.
- **F3 — Final-move rule variants**, such as robust-child (most-visited) or value-with-minimum-visits selection.
- **F4 — Value-net-as-PUCT-prior.** A structural change; in scope only if the diagnosis says selection quality is the binding constraint and F1-F3 plateau.

Each fix candidate gets its own arena series of at least 200 games before advancing to the next rung; changes gate on evidence, not intuition.

## Section 4 — Acceptance gate & ship rule

The final configuration runs against heuristic-v0 per `.claude/rules/stochastic-gate-replication.md` — copy the exact replication recipe from that rule file at plan time. Replicated runs (e.g., 2×500 games overnight); PASS means the pooled win rate is ≥ 0.55 with both runs individually clearing a floor.

The ladder submission (`src/ptcg/submission_main.py`) flips to the search agent ONLY on a replicated PASS AND a timeout-safety validation under the real 10-minute match clock — `run_arena` currently disables the total clock via `TimeManager(total_s=1e9)`, so the final validation series must re-enable the real clock. Otherwise the ladder stays heuristic-v0 and the slice ships a documented dead-end analysis.

## Section 5 — Deliverables, risks, testing

**Deliverables:** the instrumentation plus its tests; every run logged to `experiments/EXPERIMENTS.md`; a findings document at `experiments/ANALYSIS-slice5-search-architecture.md` written as a report-ready methodology narrative (the Strategy report is judged 70% on methodology); a CLAUDE.md status update at wrap-up.

**Risks:**

- Unknown Kaggle hardware speed — mitigated by D2's ≥2× safety factor and the existing fallback to heuristic-v0 on any search failure.
- Stochastic-gate false positives — mitigated by mandatory replication; no single-run conclusions.
- Long overnight runs — mitigated by the Slice-4 lesson: the orchestrator owns background processes; dispatched agents are scoped to setup and harvest only, never to waiting.

**Testing:** unit tests for the stats counters and gate branches against a fake backend; the full existing test suite (129 tests) stays green throughout; the seed-determinism guard from Section 1.

## Success criteria

1. A diagnosis deliverable regardless of outcome: measured gate-fire/deviation rates, iteration counts, the determinization noise floor, and the match-clock envelope, written up in the ANALYSIS doc.
2. "Beat it": a replicated gate PASS at ≥ 0.55 flips the ladder to the search agent with a validated timeout-safe budget policy.
3. Otherwise: a rigorous, documented dead-end with the full evidence trail.
