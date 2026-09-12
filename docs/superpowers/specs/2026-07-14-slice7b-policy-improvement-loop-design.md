# Slice 7B — Policy-Improvement Loop (Expert Iteration) Design

**Date:** 2026-07-14
**Status:** Approved (design sections approved in-session 2026-07-14)
**Predecessors:** Slices 4–6 closed the evaluator-quality hypothesis family end-to-end (learned value net, mixed-policy retrain, root PUCT prior — all arena-FLAT). Slice 7A's asymmetric-pairing test closed the deck-asymmetry hypothesis (DECISION=FLAT, differential −0.0078, CI [−0.054, +0.038]). **The policy-improvement loop is the last untested hypothesis for a search edge.**

## Hypothesis under test

Slice 6's sharpened residual: *rollouts and the opponent model play both sides as heuristic-v0, so any search deviation is scored under the assumption that neither player ever deviates from v0 — a limitation no leaf evaluator or root prior can fix.* The fix shape is a policy-improvement loop: search-guided self-play generates action targets, a policy net learns them, and the improved policy is injected back into the search (prior + rollout + opponent model), iterated across generations.

The equally-consistent alternative (the mirror matchup has too little exploitable per-decision edge — only ~4 of ~10–11 decisions/game are contested) remains live. A gen-3 kill rule (below) closes this slice honestly if the loop cannot move arena win rate.

## Decisions locked in-session

| Decision | Choice | Rationale |
|---|---|---|
| Architecture | **Expert iteration** (AlphaZero-lite) | Most faithful test of the policy-reach hypothesis |
| Compute plan | **Gen-1 in-session; gens 2–3 overnight via factory daemon** | Run-the-thing: session validates the loop end-to-end; nights provide compute |
| Anchor deck | **mega-lucario-fighting mirror** | Comparability with the Slices 4–6 parity band [0.42, 0.54]; a gate-clear is attributable to the loop, not a deck change |
| Value net | **Frozen at v2** (`value_net_weights_v2.json`) | One variable at a time — the policy is the thing under test |

## Section 1 — Loop architecture & the policy net

Each **generation k**:
1. **Self-play data-gen:** the search agent (gen k−1 policy net injected; gen 1 uses plain v0-search) plays lucario-mirror games, recording at every decision the **root visit distribution** over options plus state/action features.
2. **Train** a policy net to predict the visit distribution (cross-entropy).
3. **Inject** the new net into the search (Section 2).
4. **Arena probe** vs heuristic-v0 (Section 4); iterate.

**Policy net form — per-option scorer** (not a fixed-action-space head):
- Input: the existing 40 state features (`ptcg/search/features.py`, FEATURE_VERSION 1) + a new small **action-feature vector** (option type, card/attack identity class, damage, energy cost, target — aligned with the content-signature scheme the tree already keys children on).
- Output: one scalar logit per option; softmax across the decision's option list.
- Serve-time inference: **pure stdlib** (json + math), cloning the `value_net.py` pattern — small MLP, JSON weights file, **embedded golden vectors** for a non-circular parity test.

This sidesteps the dynamic-action-space problem: the net never needs a global action index, only a score for each presented option.

## Section 2 — Injection points & timing safety

Three flag-gated injection sites, **all default-off and bit-identical when off**:
1. **PUCT prior at every tree node** — extends Slice 6's root-only prior machinery (`use_root_prior` / `c_puct` / `prior_tau`) to all expansions. Options scored once per node expansion and **cached on the node** — one net-batch per expansion, not per visit.
2. **Opponent policy** — net argmax (or low-temperature sample) replacing v0 `choose` at opponent decision points in descent.
3. **Rollout policy** — same, at rollout steps.

**Cost reality:** v0 costs microseconds/call; a stdlib net eval ~0.5–1 ms × options-per-decision. The search currently achieves ~54 iterations per 200 ms move. Therefore a **throughput gate precedes any arena run** (Section 4, gate 2). If full injection falls below the floor, the pre-agreed **reduced config is prior-everywhere + v0 rollouts/opponent** (priors are the cheapest site per call).

**Failure ladder (unchanged + extended):** hard per-move deadline and fall-back-to-v0-on-any-search-failure stay untouched. New: degenerate softmax (NaN/all-equal) → uniform prior; weights-load failure → injection flags silently off with one logged warning.

## Section 3 — Data generation & training

- **Policy-target record:** extend the trajectory recorder with a per-decision record: root visit counts keyed by content signature, the chosen action, state features, and per-option action features. Delivered as a `--policy-targets` mode on the existing data-gen script.
- **Self-play budget = serve budget (200 ms/move).** AlphaZero convention; keeps targets on-distribution for the budget we actually play at.
- **Gen-1 sizing:** ~500–1,000 self-play games, sized to a few hours of orchestrator-owned background compute in-session.
- **`scripts/train_policy_net.py`** mirrors `train_value_net.py`: torch is dev-only, cross-entropy against visit distributions, exports JSON weights with golden vectors.

## Section 4 — Pre-registered decision criteria (locked before any run)

Per `.claude/rules/stochastic-gate-replication.md`; arena runs respect `.claude/rules/time-budgeted-arena-contention.md` (no concurrent CPU-heavy load).

1. **Sanity gate (gen-1):** policy-net top-1 agreement with search's chosen move ≥ **60%** on held-out decisions.
   **AMENDED AGAIN 2026-07-14 (Brad-approved, v3):** v2 also failed the amended gate (0.3156 vs required 0.4085; ceiling only rose 0.5197→0.5446 because features project cardId→(hp,basic) and attackId→(damage,cost-count), leaving card-identity decisions unlearnable). v3 adds **identity embeddings**: recording stores raw per-option `[cardId, attackId]` ids; the trainer learns small embedding tables (card ~8-dim, attack ~4-dim, rare/unknown ids → shared index-0 bucket) exported into the weights JSON as id→row vocab + table; stdlib serve concatenates embedding vectors with the 18 action features + 40 state features. Gate numbers unchanged from the first amendment (argmax ≥0.45 AND ≥0.75× measured ceiling, ceiling now computed over feature+identity equality). Dataset regenerated once more.
   **AMENDED 2026-07-14 (Brad-approved), before any v2 retrain:** the original 60% floor was mathematically unreachable — action-featurizer v1 dropped all target-identity fields, producing a 52.3% within-decision collision rate and a perfect-scorer ceiling of 0.5197 (diagnosis: `.superpowers/sdd/t11-diagnosis.md`; v1 measured 0.2874, train≈val, agreement@top2 0.62). Re-registered gate 1 (applies to the featurizer-v2 retrain): **argmax agreement ≥ 0.45 AND ≥ 0.75 × the v2-measured collision ceiling**, with the ceiling and agreement@top2 reported alongside. Featurizer v2 adds the target-identity fields (`inPlayArea`, `inPlayIndex`, `playerIndex`, `toolIndex`, `energyIndex` value) and bumps ACTION_FEATURE_VERSION to 2; the 600-game dataset is regenerated (stored rows embed featurized vectors).
2. **Throughput gate:** ≥ **15 iterations per 200 ms move** with full injection; below floor → reduced config (Section 2).
3. **Per-generation probe:** **200 games** vs heuristic-v0. Proceed to next generation if point estimate ≥ 0.50, OR "improving" — defined as this generation's probe point estimate strictly greater than the previous generation's.
4. **Kill rule:** after **generation 3**, if every probe CI spans 0.50 with no monotonic trend → documented dead-end, honest-fail close (the Slices 4–6 pattern). Write-up proceeds regardless of outcome.
5. **Final gate:** only if a probe point estimate ≥ 0.55 — **replicated 2×300** vs v0, pooled ≥ **0.55**, all runs reported (not just passing ones).

**Ladder policy:** `src/ptcg/submission_main.py` / `src/ptcg/agents/current.py` change **only on final-gate PASS**, via the established process. Independently, the factory may submit `search-policy` candidates under its existing better-than-incumbent/exploration gate — that is existing factory policy, not a 7B change.

## Section 5 — Factory integration

`PolicyImprovementTrainer` implements the existing `Trainer` Protocol (`src/ptcg/factory/daemon.py:17`):
- `prepare_data(cycle)` → policy-target self-play (resumable, budget-boxed per the daemon's loop-top budget check)
- `train(data_path, cycle)` → policy-net training, JSON export
- `export_and_register(weights_path, cycle, candidates)` → versioned `Candidate` with `agent_kind="search-policy"`, deck, net-weights paths (policy + frozen value v2), commit traceability

Generations 2–3 run on the nightly daemon; registered candidates feed the currently-empty factory queue. No daemon/factory rework — the trainer swaps in behind the protocol seam built in 7A.

## Testing

- Unit: action featurizer; policy-net inference vs embedded golden vectors; visit-distribution recording on a deterministic mini-game; **bit-identical-when-flags-off** regression on the searcher; Trainer-protocol conformance with stubbed scripts.
- First-run: explicit **virgin-directory** test for every first-write artifact (7A lesson — no relying on `tmp_path` pre-creation).
- Process: long-running arena/data-gen processes are **orchestrator-owned** (settled rule — dispatched agents do setup + harvest only, never wait).

## Non-goals

- No value-net retraining (frozen at v2).
- No starmie/other-deck training this slice (fast-follow via factory once the loop is proven).
- No tree reuse across `search_begin` calls, no engine changes, no new search algorithm — injection only.
- No change to ladder identity files except on final-gate PASS.
