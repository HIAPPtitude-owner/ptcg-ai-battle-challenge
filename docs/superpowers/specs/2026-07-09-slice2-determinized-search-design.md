# Slice 2 Design — Determinized MCTS Search Agent

**Date:** 2026-07-09
**Status:** Approved (Brad, 2026-07-09)
**Depends on:** Slice 1 foundation (heuristic v0 agent, arena harness, deck candidates, packaging) — merged at `b865593`, validated on the Kaggle ladder.

## 1. Goal & Acceptance Criteria

Build a `SearchAgent` that chooses moves via **determinized Monte Carlo Tree Search** (Brad's explicit architecture choice over flat PIMC) on the engine's search API (`cg.api.search_begin` / `search_step` / `search_end` / `search_release`), and submit it to the Kaggle Simulation ladder.

**Acceptance (all required):**
1. Head-to-head series vs heuristic v0: **≥ 200 games**, observed win rate **≥ 55%**, and **Wilson 95% lower confidence bound > 50%**.
2. The acceptance series runs at a **reduced per-move budget (150–250 ms)** so wall time stays ~1–2 h. Rationale: a 1 s/move agent plays ~1–2 min/game; if it beats v0 while thinking less, the full-budget ladder version is strictly stronger. Parallelize across processes if wall time demands.
3. Zero crashes across the acceptance series (fallback engagements are allowed and must be counted/logged).
4. Packaging smoke passes with the Kaggle-harness loading semantics (bare `compile()+exec()` namespace, no `__file__` — carry-forward LESSON from episode 84917131).
5. Bundle uploaded to the ladder and validation result recorded in plan.md.

## 2. Architecture & Components

New package `src/ptcg/search/` (tests live next to code, vertical-slice style):

| Module | Responsibility |
|---|---|
| `belief.py` | `BeliefState` — consumes `Observation.logs` each decision; tracks opponent-revealed cards and our own exactly-known deck remainder; samples determinizations (the six prediction lists `search_begin` requires). |
| `tree.py` | MCTS tree. Children keyed by **canonical action signature** (select type + chosen option content), never raw option index. |
| `searcher.py` | ISMCTS driver — iteration loop, determinization per iteration, selection/expansion/evaluation/backprop, time-manager integration, guaranteed `search_release`/`search_end` via context manager. |
| `evaluate.py` | Hand-tuned state evaluator (leaf values). Replaced wholesale by the Slice-4 value net. |
| `timing.py` | `TimeManager` — match-clock tracking, per-move budget allocation, hard per-decision deadline. |
| `agents/search_agent.py` | `SearchAgent(Agent)` — glue: belief update → trivial-decision shortcut → search within budget → indices. Any exception or empty result → **heuristic v0 answer** (permanent safety net). |

`submission_main.py` switches to `SearchAgent`; packaging flow unchanged.

## 3. Search Algorithm

**Shape: single shared tree ISMCTS with root determinization sampling.**

Per iteration:
1. Sample a fresh determinization from `BeliefState`.
2. `search_begin` with the agent's actual observation + the sampled hidden zones.
3. Walk the tree from the root by replaying selected actions via `search_step`; at each of **our** decision nodes, UCB1-select among options **legal in the current determinization only** (matched by canonical action signature).
4. **Opponent decisions are played by heuristic v0 as a fixed policy** (v1 decision): opponent nodes get no tree statistics. Rationale: with total iterations in the hundreds, adversarial UCB at opponent nodes starves our own nodes; a fixed strong opponent model spends the budget where it pays. Upgrade path to full adversarial ISMCTS is a documented flag for Slice 3/4.
5. Expand one new child at the tree frontier; obtain a leaf value from `evaluate.py` (rollout depth likely **zero** — evaluator-at-leaf; final call made from spike data, §4).
6. Backpropagate; `search_release` the iteration's search states.

**Chance handling:** open-loop. Replaying a path re-rolls engine-internal randomness (coin flips, shuffles), so node statistics naturally average over chance outcomes. `manual_coin` stays `False` in v1.

**Answer:** root child with most visits at deadline. Trivial decisions (single option, or forced `minCount == maxCount == len(options)`) are answered instantly without search.

**Correctness keystone:** two determinizations can present the same logical move at different indices. Keying children by option **content** (not index) is what makes shared-tree statistics sound. A test must cover index-misalignment explicitly.

## 4. Determinization & Belief v1 (approved scope: minimal)

- **Our hidden zones:** our deck list is fully known (we chose it); subtract every card we've seen leave the deck → the unseen remainder is an exact multiset. Prize order = uniform shuffle sample of that remainder.
- **Opponent hidden zones:** track revealed cards from `Observation.logs` (plays from hand, evolutions, attachments, discards). Unseen remainder padded from a **mirror prior** — our own deck list minus their revealed cards — which satisfies the engine's "≥ 1 Basic Pokémon at setup" constraint for free. Deferred to later slices: archetype priors, per-card probability models.
- Determinization outputs must always be **count-valid** (list lengths equal actual zone counts) — engine rejects otherwise; unit-tested.

## 5. Time Management & Safety

- Usable match budget: **~8 of the 10 minutes** (safety margin for engine time + I/O).
- Per-move budget: `clamp(remaining_safe / max(est_moves_remaining, floor), 50 ms, 1.5 s)`; expected-moves estimate seeded from arena data (measured in the spike).
- Hard deadline checked every iteration; the searcher is **anytime** — always returns current-best.
- **Kaggle hardware safety factor:** default budgets assume the ladder machine is 2–3× slower than local.
- Memory hygiene: context-manager wrapper guarantees `search_release` per iteration and `search_end` per decision; the C arena must never leak inside a match.
- **Spike is Task 1:** measure `search_step` throughput (ctypes + JSON per-step overhead is the suspected dominant cost) on real mid-game states; log to `experiments/EXPERIMENTS.md`. K (determinizations), rollout depth, and iteration expectations are sized from measured data, not assumptions.

## 6. Evaluation Function (`evaluate.py`)

`evaluate(state_from_our_view) -> float` in [0, 1]; terminal results dominate (win=1, loss=0). Non-terminal weighted sum (hand-tuned this slice):
- **Prize differential** (strongest term),
- board damage pressure (damage on opponent's Pokémon vs their remaining HP; symmetric for ours),
- development (energy attached, evolution stages, bench occupancy),
- hand-size differential (capped),
- deck-out proximity guard.

Deterministic, pure, unit-testable on fixture states (e.g., "1 prize left" must score above "6 prizes left", all else equal).

## 7. Testing

- **Unit (fast suite):** belief tracking from synthetic log sequences; determinization validity (counts, revealed-card placement, Basic-Pokémon constraint); action-signature keying incl. index-misalignment case; TimeManager math; evaluator ordering sanity.
- **Integration (fast suite):** `SearchAgent` plays full games vs random and vs v0 at tiny budget (~30 ms/move, small K) crash-free; fallback path exercised; search-state hygiene (repeated decisions without leak).
- **Acceptance (slow, gated marker):** §1 series.
- **Packaging smoke:** Kaggle-harness semantics (bare exec, no `__file__`), per Slice-1 LESSON.
- TDD per task (RED-GREEN-REFACTOR); every experiment logged to `experiments/EXPERIMENTS.md` for the Strategy report (70% methodology).

## 8. Also In This Slice

- **Deferred minors from Slice 1** (one cleanup task): `markdown_row` pipe-escaping; `MatchResult` tuple → typed structure; `AreaType` enums in fixture tests; `make_player` hand default.
- `.gitignore` results/ (Kaggle episode replays are derived artifacts).
- Plan/docs updates; Strategy-report methodology notes captured as we go.

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| `search_step` throughput too low for meaningful MCTS | Spike first (Task 1); evaluator-at-leaf instead of rollouts; K and iteration counts sized from data. |
| Kaggle machine slower than local → timeout loss | 2–3× safety factor; anytime search; ~8 min usable budget; trivial-decision shortcut. |
| Option-index misalignment across determinizations | Canonical action-signature keying + dedicated test. |
| C-arena memory leak across a long match | Context-manager release discipline + hygiene test. |
| Search bug mid-ladder-match | Try/except fallback to heuristic v0 on every decision; fallback engagements logged. |
| Mid-competition engine updates (new enum members) | Tolerant parsing per project CLAUDE.md convention. |
