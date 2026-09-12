# Slice 7B Analysis — Policy-Improvement Loop (Expert Iteration): Honest-Fail Close-Out

Spec: `docs/superpowers/specs/2026-07-14-slice7b-policy-improvement-loop-design.md`
Plan: `docs/superpowers/plans/2026-07-14-slice7b-policy-improvement-loop.md`

## Question

Slice 6 closed the evaluator-quality hypothesis family end-to-end (learned value net, mixed-policy retrain, root PUCT prior — all arena-FLAT in [0.42, 0.54]) and sharpened a residual: rollouts and the opponent model still play both sides as heuristic-v0 during search, so any search deviation is scored under the assumption that neither player ever deviates from v0 — a limitation no leaf evaluator or root prior can fix. Slice 7A's asymmetric-pairing test separately closed the deck-asymmetry hypothesis (DECISION=FLAT, differential −0.0078, CI [−0.054, +0.038]). **The policy-improvement loop — expert iteration (AlphaZero-lite) — is the last untested hypothesis for a genuine search edge.**

Design: each generation k runs search-guided self-play (gen k−1 policy net injected as PUCT prior / opponent policy / rollout policy; gen 1 uses plain v0-search), records the root visit distribution over options as an imitation target, trains a per-option scorer net (state features + action features) via cross-entropy against that distribution, and injects the trained net back into the search. Three flag-gated injection sites (all default-off, bit-identical when off): PUCT prior at every node, opponent policy at descent, rollout policy. Value net frozen at v2 throughout — the policy is the only variable under test.

The equally-consistent alternative carried in from Slice 6 remains live throughout: the mirror matchup may simply have too little exploitable per-decision edge for this search shape (every Slice 4–6 arena result sits in [0.42, 0.54] regardless of intervention).

## Pre-registered decision criteria

Locked before any run, per `.claude/rules/stochastic-gate-replication.md`:

1. **Sanity gate (gen-1):** policy-net top-1 (argmax) agreement with search's chosen move ≥ 0.60 on held-out decisions.
2. **Throughput gate:** ≥ 15 iterations per 200 ms move with full injection; below floor → reduced config (prior-everywhere + v0 rollouts/opponent, the cheapest injection site per call).
3. **Per-generation probe:** 200 games vs heuristic-v0; proceed if point estimate ≥ 0.50 or strictly improving over the prior generation's probe.
4. **Kill rule:** after generation 3, if every probe CI spans 0.50 with no monotonic trend → documented dead-end, honest-fail close (the Slices 4–6 pattern).
5. **Final gate:** only if a probe point estimate ≥ 0.55 — replicated 2×300 vs v0, pooled ≥ 0.55, all runs reported.

Gate 1 was amended twice mid-slice, both Brad-approved and committed before the retrain each amendment governed (satisfying the pre-registration-amendment conditions in `.claude/rules/stochastic-gate-replication.md`: proposed/decided before the deciding data existed, strictly conservative or diagnostically-driven, explicitly human-approved):

- **Original:** argmax agreement ≥ 0.60 (v1 featurizer, `action_features.py` ACTION_FEATURE_VERSION 1).
- **Amendment 1 — commit `d034ab6`** (Brad-approved, committed before the v2 retrain): the 0.60 floor was diagnosed as mathematically unreachable against the v1 featurizer (see Gate 1 saga below) — re-registered as argmax agreement ≥ 0.45 AND ≥ 0.75× the measured collision ceiling, with ceiling and agreement@top2 reported alongside. Applies to the featurizer-v2 retrain (target-identity fields added, ACTION_FEATURE_VERSION 2).
- **Amendment 2 — commit `7df62ee`** (Brad-approved, committed before the v3 retrain): v2 also failed the amended gate. Spec amended to add v3 identity embeddings (card/attack id embedding tables learned by the trainer, concatenated with the 18 action features + 40 state features at serve time). Gate numbers unchanged from amendment 1; the ceiling is now computed over feature+identity equality.

## Gate 2 — throughput (measured first, precedes any arena spend)

`experiments/EXPERIMENTS.md` rows `7B gate2 throughput` (2026-07-14, `search-policy-policy_rand` vs `heuristic-v0`, mega-lucario-fighting mirror, random policy-net weights):

| Config | Iterations / 200 ms move | Floor | Result |
|---|---|---|---|
| FULL injection (PUCT prior + opponent policy + rollout policy) | 16.0 | 15 | PASS (thin) |
| REDUCED (prior-only, v0 rollouts/opponent) | 47.3 | — | (fallback config, not needed) |

Pre-registered decision: FULL injection is the operating config (cleared the floor). Random-weights win rates (6.7% FULL / 53.3% REDUCED vs heuristic-v0, 30 games each) came in far from 50/50 in both directions — confirms the injection sites are load-bearing (a policy net actually steering search behavior, not a no-op), which is the intended sanity signal for this gate: a random net should — and does — play badly when it controls the PUCT prior/opponent/rollout paths at full strength.

## Gate 1 — sanity saga: v1 → v2 → v3, all FAIL

All three data-gen runs: mega-lucario-fighting mirror self-play, gen-1 (plain v0-search, no injected net), 600 games, seed 7, 0 errors.

| Featurizer | Data-gen | Rows | Train/Val split | VAL argmax agreement | Ceiling | Required | Result |
|---|---|---|---|---|---|---|---|
| v1 (12-dim, no target-identity fields) | 2924s (4.87 s/game) → `policy_gen1_data.jsonl` | 14,154 | 11,276 / 2,878 | 0.2874 | 0.5197 | ≥ 0.60 (original) | FAIL — floor mathematically unreachable |
| v2 (+6 identity fields, `efc3b04`) | 3054s (5.09 s/game) → `policy_gen1v2_data.jsonl` | 14,793 | 12,024 / 2,769 | 0.3156 | 0.5446 | ≥ 0.45 AND ≥ 0.75×0.5446=0.4085 | FAIL |
| v3 (+identity embeddings, `ff5cd44`) | 3068s (5.11 s/game) → `policy_gen1v3_data.jsonl` | 14,803 | 11,859 / 2,944 | 0.3067 | 0.5448 (identity-aware, unchanged from v2) | ≥ 0.45 AND ≥ 0.75×0.5448=0.4086 | FAIL |

### v1: feature collisions cap the ceiling below the floor

Diagnosis (`.superpowers/sdd/t11-diagnosis.md`, opus, executable receipts against the real data — 14,154 decisions): `extract_action` encoded card/attack metadata but dropped every field identifying which board slot / target an option acts on (`inPlayArea`, `inPlayIndex`, `index`, `playerIndex` — all present on `cg.api.Option`, all ignored). Measured on the real dataset:

- 52.3% of decisions (7,404/14,154) have the target option's 12-dim action vector exactly duplicated by ≥1 competitor in the same decision; 12.75% collapse to a single distinct vector across all options.
- Perfect-scorer ceiling: 0.5197 — the 0.60 argmax floor is unreachable by any function of these action vectors, before training even starts.
- Train ≈ val at every capacity/LR setting tested (default: VAL 0.2874/TRAIN 0.3005; 2× hidden + 3× LR: VAL 0.3047/TRAIN 0.3203) → cannot-fit, not overfit; the net already extracts everything the 12 features permit.
- Under a coarser top-2 lens the net clears chance clearly: agreement@top2 = 0.6206 (net) vs random@top2 0.5289, ceiling@top2 0.8410 — a real, coarse signal exists, but the harsh argmax metric on 42.4% diffuse-target decisions (top-1 visit share < 0.4) understates it.

### v2: identity fields close the collision gap but the residual is DB-projection lossiness

Featurizer v2 adds `inPlayArea`/`inPlayIndex`/`playerIndex`/`toolIndex`/`energyIndex`, bumping ACTION_FEATURE_VERSION to 2, and the 600-game dataset was regenerated. Ceiling rose only 0.5197 → 0.5446 (not toward the ~0.84 top-2 ceiling the diagnosis had projected) because the surviving residual is a different collision source: the features project cardId → (hp, basic) and attackId → (damage, cost-count) — a many-to-one, lossy projection of the underlying DB identity. Distinct cards/attacks with the same projected stats remain indistinguishable to the net. Measured VAL agreement 0.3156 against a required 0.4085 — still FAIL.

### v3: identity embeddings close the projection gap but expose the search's own equivalence classes

Featurizer v3 adds raw [cardId, attackId] per option, with small trainer-learned embedding tables (card ~8-dim, attack ~4-dim, rare/unknown ids bucketed to a shared index-0), concatenated with the 18 action features + 40 state features at stdlib serve time (weights JSON carries the id→row vocab + table). Identity-aware ceiling: 0.5448 — unchanged from v2. The residual is no longer feature lossiness at all: it is duplicate option signatures that are the search's own equivalence classes — decisions where two or more genuinely-distinct options are, from the perspective of card+attack+target identity, indistinguishable choices the search itself treats as fungible (e.g., two bench Pokemon that are the identical species/state). No featurizer can break a tie the search's own action space does not distinguish. Measured VAL argmax agreement 0.3067 vs required 0.4086 — FAIL, third and final attempt per the amendment ladder.

A supplementary class-level measurement (grouping options by identity-equivalence class rather than raw index) puts a number on how learnable the imitation target actually is: class-level agreement 0.4908 vs a 0.4071 random-class baseline (+8.4 points) — mean ~2.46 distinguishable classes per decision, with 46.5% of targets falling inside a duplicate-signature group. (These class-level figures are re-derivable via `experiments/analyze_slice7b_class_agreement.py`, committed at `1bb7aad`, which reproduces 0.3067/0.4908/0.4650/0.4071 from the committed net + v3 dataset.)

## Decision: honest-fail per registered fail path

No arena probe was run (gates 3–5 never reached); nightly policy-improvement cycles are NOT enabled. Per the pre-registered fail path, once gate 1 (sanity) fails after the full amendment ladder is exhausted, the slice stops before spending arena compute — the same discipline Slices 4–6 used ("evaluator got measurably better, arena didn't move" → don't keep spending). Here the imitation target itself never became learnable enough to justify a self-play arena probe: three separate featurizer generations (12-dim → +6 identity fields → +identity embeddings) each closed the specific collision mechanism the prior generation's diagnosis identified, and each time the ceiling barely moved (0.5197 → 0.5446 → 0.5448) while measured agreement stayed in a narrow band (0.29–0.32).

## Interpretation

The convergent ceiling (~0.545 across three structurally different featurizer fixes) combined with the class-level result (2.46 distinguishable classes/decision, near-tie mass concentrated in duplicate-signature groups) points to a specific conclusion: the search's own visit-count preferences among the handful of genuinely distinguishable action classes per decision are near-ties. This is not a featurizer problem — the last fix (v3) removed featurizer lossiness entirely and the ceiling didn't move — it is a property of what the search itself is choosing between. If the search rarely has a strong, learnable preference among distinct options, an imitation-learning policy net has little signal to imitate, independent of how well-engineered its inputs are.

This is an independent measurement channel from every prior slice's evidence for the no-exploitable-edge hypothesis: Slices 4–6 measured arena win rate directly (a downstream, noisy, expensive signal) and found no configuration cleared parity; Slice 7A's asymmetric-pairing test measured deck-level differential and found FLAT. Slice 7B measures a different thing — how learnable the search's own within-decision preferences are — via imitability rather than arena outcome, and it converges on the same conclusion from a different angle: there is little structured signal to exploit at the level this loop operates on.

## Carry-forwards

1. A future policy-improvement attempt needs a different training signal than visit imitation (e.g., outcome-conditioned targets, policy-gradient / REINFORCE-style objectives that optimize for game outcome rather than matching a near-tied visit distribution). Armed as a Slice 7C+ candidate only if new evidence of a per-decision edge appears — not scheduled as a default next step, given the convergent no-edge measurements across Slices 4–7B.
2. Reusable infrastructure shipped regardless of the fail result: policy-target recording (`PolicyTargetRecorder`, root visit distribution keyed by content signature), the per-option action featurizer + identity embeddings (`action_features.py`, v1/v2/v3), the stdlib policy-net inference path with embedded golden-vector parity tests, three flag-gated injection sites (PUCT prior / opponent policy / rollout policy, all bit-identical-off), and `PolicyImprovementTrainer` + `scripts/run_policy_cycle.py` implementing the Trainer Protocol from Slice 7A's factory (`src/ptcg/factory/daemon.py`) — swappable in behind the same seam with no factory rework.
3. The near-tie class structure (~2.46 distinguishable classes/decision) is a quantified ceiling on per-decision edge for future slices to reason from: any future search-improvement hypothesis operating at the level of "learn to prefer among a decision's options" inherits this ceiling unless it changes what the search's own action space distinguishes, not just what a downstream net is fed.

Per the pre-registered fail path: the ladder submission (`src/ptcg/submission_main.py`) stays unchanged on HeuristicAgent + mega-lucario-fighting. The policy-net infrastructure, injection machinery, and factory trainer ship as repo-side infrastructure only.
