# Slice 4 Design: Learned Value Net

**Date:** 2026-07-10
**Status:** Approved by Brad (brainstorm 2026-07-10)
**Predecessors:** Slice 2 (determinized ISMCTS search, closed at parity), Slice 3 (deck tournament, mega-lucario-fighting confirmed entry deck)

## Objective

Replace the hand-tuned `evaluate(state: State, my_index: int) -> float` (value in [0,1], `src/ptcg/search/evaluate.py`) with a learned value net, aiming to break the parity ceiling documented in Slice 2 (tuned search-v1 measured ~47–49% vs heuristic-v0 over 600+ mirror games; the hand-tuned evaluator was characterized as the bottleneck).

## Success criteria (the gate)

- **Gate:** search agent with the value net vs heuristic-v0: **≥55% win rate, replicated** per `.claude/rules/stochastic-gate-replication.md` — multiple independent runs at the identical config, ALL runs reported (not just passing ones).
- **Gate passes →** swap the Kaggle ladder submission to the search+net agent (Section: Ladder swap).
- **Gate fails →** close the slice honestly: ladder stays heuristic-v0, ceiling characterized in EXPERIMENTS.md and the acceptance-test docstring (Slice-2 precedent). The training/inference infrastructure remains the deliverable.

## Decisions locked at brainstorm

| Decision | Choice | Rationale |
|---|---|---|
| Success bar | Measured edge (≥55% replicated), ladder swap conditional on gate | Decouples slice closure from a stochastic outcome while keeping the ladder prize in reach |
| Training stack | PyTorch (uv dev-dependency, CPU) | Brad's explicit choice; maximum flexibility for losses/architecture; never enters the bundle |
| Training data | Heuristic-v0 self-play across the Slice-3 tournament deck pool (varied pairings, not mirror-only) | Cheap generation at scale; deck variety broadens position distribution; standard first-net bootstrap |
| Net integration | Drop-in first, then A/B | Phase 1 isolates "net vs hand-tuned evaluator" from all other variables; Phase 2 tests the architecture question (rollout vs leaf eval) as a separate controlled experiment |
| Carry-forwards in scope | ALL: RAINBOW fix (mandatory pre-req), belief-v2, intra-iteration deadline check, regression pin | Deadline check is required for ladder safety on the gate-pass path; belief-v2 is a cheap forward-model correctness win; regression pin is cheap bookkeeping |

## Constraints (from pre-brainstorm scope scan)

1. **Bundle has zero third-party deps** (`pyproject.toml` dependencies = []; no numpy anywhere in repo). Inference in the submission must be **pure Python stdlib**. Training deps are dev-only and never ship.
2. **No trajectory logging exists.** `run_arena.py`/`runner.py` record only aggregate `MatchResult` rows. Per-decision logging is net-new infrastructure.
3. **Ladder currently ships heuristic-v0** — `ptcg/search/` is not in the bundle; packaging changes are part of the conditional ladder-swap work.
4. **Bundle size limit** 197.7 MiB enforced by `package_submission.py` (weights artifact is trivially small; not a concern, but the verify step must stay green).
5. **10-minute per-match budget; timeout = loss.** Pure-Python net eval must be benchmarked (~1ms/call budget) and the searcher needs the intra-iteration deadline check.

## Architecture

### Pre-requisite fixes (done before any net work builds on them)

1. **`_cost_satisfied` RAINBOW over-count fix** (`src/ptcg/search/evaluate.py:58–72`): today each specific energy requirement independently adds the full RAINBOW pool to its own availability, so one RAINBOW pays every specific color at once (also note the unreachable `specific_total += n` after `return False`). Failure-case test written FIRST (TDD), then fix so RAINBOW allocates once across requirements. This unblocks attack-readiness features and any KO-term use.
2. **Belief-v2:** pop the active-Pokémon prediction from the determinization sampling pool so it is not double-counted when filling remaining hidden zones.
3. **Intra-iteration deadline check:** the searcher checks the move deadline inside an iteration (not only between iterations) so one slow iteration cannot blow the move budget.
4. **Regression pin:** record the shipped-default search config's baseline win rate as a logged bar (test or ledger entry) that future config changes must not degrade.

### Feature extraction — `src/ptcg/search/features.py` (new)

- Pure stdlib. **Single module consumed by BOTH training-data generation and bundle inference** — train/serve skew is structurally impossible.
- Returns a fixed-length `list[float]` plus a `FEATURE_VERSION` constant and feature-name list (for the report and for dataset/versioning sanity).
- Feature families (~50–150 floats, exact set finalized at plan time): prize counts both sides; active HP fraction both sides; per-slot bench HP/energy/damage summaries; energy counts (typed vs RAINBOW, post-fix attack-readiness bits); hand/deck/discard counts; poison flags; turn number; `appearThisTurn`/evolution-stage summaries.
- Perspective: features computed from the mover's perspective (`my_index`).

### Trajectory logging & dataset generation

- Recording hook in the match runner: at each decision, capture `(FEATURE_VERSION, feature_vector, mover_index)`; at game end, label every record with the final outcome from that mover's perspective (win 1.0, loss 0.0, draw 0.5).
- Storage: JSONL, one record per decision, **`encoding="utf-8"` on every write** (Windows cp1252 wipe lesson). Includes game ID so the training split can be by-game.
- `scripts/generate_training_data.py`: v0-vs-v0 self-play across Slice-3 deck-pool pairings (round-robin style for diversity). Target on the order of 20–30k games, **sized by an early timing measurement** — measure games/minute first, then set the target.

### Training — `scripts/train_value_net.py`

- PyTorch MLP, approximately `features → 64 → 64 → 1` with sigmoid output, BCE loss.
- **Train/val split BY GAME, never by position** (within-game positions are correlated; position-level splits leak).
- Early stopping on validation loss.
- **Pre-arena sanity signal:** on the held-out validation set, compare the net's accuracy/AUC at predicting game outcome against the hand-tuned `evaluate()` scored on the same positions. If the net cannot beat the hand-tuned evaluator on this offline metric, do NOT burn arena games — iterate on features/data first.
- Reproducibility: fixed seeds, training config + dataset fingerprint logged to EXPERIMENTS.md.

### Export & pure-Python inference — `src/ptcg/search/value_net.py` (new)

- Export step serializes trained weights to a checked-in JSON artifact (`src/ptcg/search/value_net_weights.json`) — data, not code, so review diffs stay meaningful and loading is `json.load`, no exec/import tricks.
- `value_net.py` implements the forward pass in pure stdlib (`math`, lists) and exposes an evaluator conforming to the existing contract: `evaluate(state, my_index) -> [0,1]`.
- **Golden-vector test:** at export time, generate hardcoded input→output pairs from torch and pin them in a test asserting the pure-Python forward pass reproduces them to tolerance. Never a self-comparison (golden-pin lesson).
- Benchmark: pure-Python eval time measured and recorded; ~1ms/call budget.
- Failure handling: if the weights artifact fails to load, `ValueNetEvaluator` construction raises and the search agent falls back to the hand-tuned evaluator (and the existing search→heuristic-v0 fallback on any search failure stays untouched).

### Experiments (in order; all logged to `experiments/EXPERIMENTS.md`)

1. **Exp 1 — drop-in:** net as rollout terminal scorer; searcher config otherwise identical to tuned search-v1 (`rollout_depth=12`, v0-improvement gate unchanged). vs heuristic-v0. Isolates evaluator quality.
2. **Exp 2 — architecture A/B:** `rollout_depth=0` static net leaf-eval (buys more iterations per 200ms budget) vs Exp 1 config. Both configs measured vs heuristic-v0 (same baseline as the gate, so results are directly comparable to Exp 1 and to the gate bar).
3. **The gate:** winner of Exp 2 vs heuristic-v0 at ≥55%, replicated per the stochastic-gate rule.

### Ladder swap & packaging (CONDITIONAL on gate pass)

- `package_submission.py` bundle list gains `ptcg/search/` (searcher, features, value_net, weights artifact) — bundle stays far under the 197.7 MiB limit.
- `submission_main.py` switches from `HeuristicAgent` to the search agent (with all fallbacks intact).
- Deadline check verified against the 10-min match budget; isolation import smoke test re-run (bare-namespace exec, no `__file__`).
- Kaggle upload per established submission mechanics; **Brad approves the submission description before any submit runs** (standing rule).

## Testing

- RAINBOW fix: failure-case test first (TDD), covering multi-color requirements with shared RAINBOW.
- Features: golden-vector tests on constructed states; feature-length/version pin.
- Trajectory logging: unit test that a short scripted game emits correctly labeled records (both perspectives, draw case).
- Export: hardcoded golden-vector torch↔pure-Python parity test.
- Deadline check: unit test with an artificially slow evaluator confirming the deadline aborts the iteration.
- Belief-v2: determinization unit test that the active prediction no longer appears in the remaining pool.
- Acceptance: existing slow-suite gates stay green; new gate runs are experiments (EXPERIMENTS.md), not CI tests — except the regression pin, which is a logged bar.
- `uv run pytest` green throughout; guard test for `write_text`/`open` encoding kwarg already exists and applies to new writers.

## Risks & mitigations

- **Net fails to beat hand-tuned evaluator (Slice-2 déjà vu):** offline val-set comparison gates arena spend; slice closes honestly on infrastructure + characterization if the edge doesn't materialize.
- **Distribution shift** (net trained on v0-self-play positions, evaluated on search-reached positions): known and accepted for a first net; noted in the report as future work (iterated self-play).
- **Pure-Python inference too slow:** benchmarked early (feature extraction + forward pass); mitigations are net-width reduction or feature pruning — decided on measurement, not guessed.
- **Stochastic gate near its bar:** replication rule is mandatory; all runs reported.

## Out of scope (YAGNI)

- Policy head / move priors; net-guided rollouts (only value).
- Iterated self-play (generation-2 data from search games).
- TD/bootstrapped labels (Monte Carlo outcome labels only).
- Any engine or `pokemon-tcg-ai-battle/` modification (licensed, read-only).

## Strategy-report tie-in

Every experiment, sizing measurement, and the offline-vs-arena comparison feeds the Strategy report (70% methodology). The train/val-by-game split, golden-vector export parity, and replicated gate are exactly the methodology substance the judges score.
