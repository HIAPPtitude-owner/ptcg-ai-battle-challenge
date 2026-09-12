# Slice 6 Analysis — Off-Policy Value Blindness: Verify, Then Fix (F4)

Spec: `docs/superpowers/specs/2026-07-10-slice6-mixed-policy-value-net-design.md`
Plan: `docs/superpowers/plans/2026-07-10-slice6-mixed-policy-value-net.md`

## Question

Slice 4's value net decisively beats the hand-tuned evaluator offline (val AUC
0.8971 vs 0.7640) yet moved arena win rate not at all (0.498 pooled vs the 0.55
bar). Slice 5 cleared every mechanical suspect. The residual hypothesis (F4):
the net was trained only on v0-self-play positions, so states on lines where
the search DEVIATES from v0 are scored off-distribution exactly where accuracy
decides whether a deviation is kept. This slice first tests that premise
directly (Phase 0), and only if confirmed spends the compute to fix it
(Stage 1: mixed-policy retrain; Stage 2: value-derived root PUCT prior).

## Pre-registered decision criteria

Recorded verbatim from the plan BEFORE the Phase-0 data was generated; the D0
amendment (net-vs-hte differential) was Brad-approved while generation was
still running and before any Phase-0 output was seen.

- **D0:** `deficit = AUC_net(a) − AUC_net(c)`; `hte_deficit = AUC_hte(a) −
  AUC_hte(c)`; `differential = deficit − hte_deficit`, where (a) = Slice-4 val
  split, (c) = search-game post-deviation bucket, hte = hand-tuned evaluator
  scored on the same sampled rows. CONFIRMED: deficit ≥ 0.05 AND differential
  ≥ 0.03. FALSIFIED: deficit < 0.02. SHARED-DEGRADATION: deficit ≥ 0.05 AND
  differential < 0.03 (games intrinsically harder for ANY evaluator — evidence
  for the no-exploitable-edge alternative, STOP branch). GRAY: anything else →
  AskUserQuestion. Bucket (b) on-path is informational only (measured: gate-off
  search deviates on ~the first decision of nearly every game, so (b) is tiny
  and early-game-skewed).
- **D1 / D2 (arena gates):** pooled win rate over 2×300 games (wins/600, draws
  count as non-wins) ≥ 0.55; both runs reported verbatim.
- **Retrain quality bar (informational):** v2 recovers ≥ half the Phase-0
  post-deviation AUC deficit without on-policy regression > 0.02 AUC.

## Phase 0 — diagnostic

### Procedure

- Instrumentation: game-level deviation tagging (T1, commit e26a883) — the tag
  flips the first time EITHER player's 1-of-1 choice (≥2 options) differs from
  heuristic-v0's choice on the same observation; the deviating decision's own
  record stays on-path. Generator search mode (T2, cc74312): both seats
  SearchAgent, gate OFF (deviate_min_visits=0, deviate_value_edge=0.0),
  rollout_depth=0, evaluator = Slice-4 v1 net, 200ms/move — deployment-matched
  to the Stage-1 gate config. Bucket evaluation (T4, dce3318 + hte-differential
  fix): rank-sum AUC/BCE per bucket, 100k/bucket seeded sample cap.
- Throughput (measured, 20-game probe, T2): **32.05 s/game** — 3–5× the plan
  estimate of 6–12 s/game; verify-throughput-before-hypothesis rule applied,
  all downstream run sizing derived from the measured figure.
- Generation run: 315 games (45 candidate-deck pairings × 7), seed 61,
  `experiments/data/slice6/phase0_search.jsonl`. Completed 315 games, 0 errors,
  7,147 s wall-clock (22.69 s/game — faster than the 32.05 probe; the probe's
  first pairing is evidently on the slow side).

### Measured results

`eval_value_net.py` output, verbatim (weights = Slice-4 v1, baseline =
slice4/train_v1.jsonl, search = slice6/phase0_search.jsonl, defaults —
100k/bucket cap, seed 0):

```
bucket a_v0_val: n=100000/264478 n_decisive=100000 auc=0.8974 bce=0.3948 acc=0.8030 hte_auc=0.7648 hte_bce=0.6074
bucket b_search_onpath: n=591/591 n_decisive=591 auc=0.4865 bce=0.7017 acc=0.4822 hte_auc=0.5057 hte_bce=0.7087
bucket c_search_postdev: n=40353/40353 n_decisive=40353 auc=0.8168 bce=0.5717 acc=0.7286 hte_auc=0.7294 hte_bce=0.6227
deficit(a-c)=0.0806 deficit(a-b)=0.4109
hte_deficit(a-c)=0.0354 differential(net-hte)=0.0452
advisory: CONFIRMED per amended D0 thresholds (deficit >=0.05 / <0.02; differential >=0.03)
```

### D0 verdict: CONFIRMED

- deficit(a−c) = 0.0806 ≥ 0.05 ✓ and differential(net−hte) = 0.0452 ≥ 0.03 ✓.
- Interpretation: on post-deviation rows the net's ranking power drops 0.0806
  AUC while the hand-tuned evaluator (scored on the SAME 40,353 rows) drops
  only 0.0354. The excess 0.045 is net-specific off-policy blindness — the F4
  mechanism is real, though milder than the raw deficit suggests: roughly 44%
  of the net's degradation is shared with the hand-tuned evaluator (search
  games are somewhat harder to predict for everyone).
- Bucket (b), informational: n=591 (gate-off search deviates on ~the first
  decision of nearly every game, as the T2 smoke predicted), AUC ≈ random for
  BOTH evaluators (net 0.4865, hte 0.5057) — early-game positions before any
  deviation are simply undetermined; this is the phase confound that motivated
  demoting (b) from the decision rule.
- Branch taken: Phase B (mixed-policy retrain).

## Stage 1 — mixed-policy retrain (D0 confirmed)

### Data + training (T8/T9)

- Overnight generation: 1,350 games (45 pairings × 30, Brad-approved sizing),
  seed 62, 0 errors, 30,036 s (22.25 s/game), **172,829 positions** →
  `experiments/data/slice6/train_search_v1.jsonl`. Blend share ≈ 11.5% of the
  1.5M-row combined training set — over 2× the position estimate, comfortably
  above the 5k search-val tripwire (measured search-val: 34,379 rows).
- Training: `train_value_net.py --data slice4/train_v1.jsonl
  slice6/train_search_v1.jsonl --out value_net_weights_v2.json --seed 0`,
  30 epochs (no early stop; val_bce still edging down at epoch 29). Parity OK.
  Per-bucket val lines, verbatim:

```
NET  val[v0]:         {'bce': 0.3943, 'acc': 0.803, 'auc': 0.8979}  (n=264478)
NET  val[search-on]: {'bce': 0.6881, 'acc': 0.5336, 'auc': 0.5445}  (n=491)
NET  val[search-dev]: {'bce': 0.3708, 'acc': 0.8233, 'auc': 0.913}  (n=34379)
HTE  val[v0]:         {'bce': 0.6079, 'acc': 0.6714, 'auc': 0.764}  (n=264478)
```

### Retrain quality bar: PASSED

Measured the clean way — v2 scored on the FULL Phase-0 file (none of whose
rows entered v2's training; same rows v1 was measured on):

```
bucket a_v0_val: n=100000/264478 n_decisive=100000 auc=0.8979 bce=0.3940 acc=0.8027 hte_auc=0.7648 hte_bce=0.6074
bucket c_search_postdev: n=40353/40353 n_decisive=40353 auc=0.8751 bce=0.4460 acc=0.7894 hte_auc=0.7294 hte_bce=0.6227
deficit(a-c)=0.0228 hte_deficit(a-c)=0.0354 differential(net-hte)=-0.0126
```

- Bar 1 (recover ≥ half the deficit): AUC_v2(c) = 0.8751 ≥ 0.8168 + 0.5×0.0806
  = 0.8571 ✓ — the deficit shrank 0.0806 → 0.0228 (72% recovered).
- Bar 2 (no on-policy collapse): AUC_v2(a) = 0.8979 ≥ 0.8971 − 0.02 ✓ (no
  regression at all).
- The differential flipped NEGATIVE (−0.0126): v2 degrades less off-policy
  than the hand-tuned control on the same rows. Net-specific off-policy
  blindness is repaired at the evaluator level; whether that converts to WIN
  RATE is exactly what the Stage-1 gate now measures.

### Stage-1 arena gate (T10)

Pre-gate probe (100 games each, search-net-v2 vs heuristic-v0, mega-lucario
mirror, 200ms): rollout-depth 0 → 43-57-0 (43.0% [33.7%, 52.8%]);
rollout-depth 12 → 54-46-0 (54.0% [44.3%, 63.4%]). Selected rd12 per the
pre-registered rule (higher pooled win rate).

Gate runs (2×300, rd12, both reported verbatim):

- run 1: 152-148-0 (50.7% [45.0%, 56.3%]), dev=6.1% gate_blk=30.7% iters=71.4
- run 2: 149-151-0 (49.7% [44.0%, 55.3%]), dev=6.1% gate_blk=30.7% iters=74.4

**Pooled: 301/600 = 0.5017 vs the 0.55 bar → D1 = FAIL (parity).** The
probe's 54% was sampling noise — the replication protocol caught it.

### Stage-1 interpretation

The central Slice-6 finding: the evaluator-quality hypothesis is now closed
at BOTH ends. Slice 4 showed a better on-policy evaluator doesn't move win
rate; Stage 1 shows that repairing the off-policy blindness (post-deviation
AUC 0.8168 → 0.8751 on held-out rows, differential negative) doesn't either.
Deviation behavior barely changed (dev 6.1% vs v1's ~5-6%). Remaining
suspects, in F4's own terms: (1) the lookahead's VALUE SIGNAL is fine but its
POLICY REACH is capped — rollouts and the opponent model still play both
sides as heuristic-v0, so search lines are evaluated under the assumption the
opponent never punishes differently than v0 would; (2) the alternative
hypothesis — the symmetric mirror simply offers too little per-decision edge
for ±0.05 win-rate at this budget. Stage 2 (value-derived root PUCT prior)
is the last pre-registered intervention of this slice; it attacks exploration
allocation, not evaluation.

Branch taken: Phase C (per pre-registered D1 fail path).

## Stage 2 — value-derived root PUCT prior (D1 parity path)

Implementation (T11 870c3aa, T12 b963b71 + a51a052): `puct_pick` at the root
only, priors = softmax of v2-net static evals of each legal root action under
one determinization, all-or-nothing on failure/deadline, flag-off path
bit-identical (guard-tested including rng-consumption parity).

Probe sweep (100 games each, search-net-v2 + --root-prior, rd12, 200ms,
mega-lucario mirror):

| prior_tau | c_puct | result | win rate |
|---|---|---|---|
| 0.05 | 1.0 | 52-48-0 | 52.0% [42.3%, 61.5%] ← selected (argmax) |
| 0.05 | 2.5 | 38-62-0 | 38.0% [29.1%, 47.8%] |
| 0.2  | 1.0 | 47-53-0 | 47.0% [37.5%, 56.7%] |
| 0.2  | 2.5 | 47-53-0 | 47.0% [37.5%, 56.7%] |

Heavy prior weighting (c_puct=2.5) is actively harmful — over-committing
exploration to the net's 1-ply ranking loses to spreading visits.

Gate runs (2×300 at tau=0.05, c_puct=1.0, both verbatim):

- run 1: 134-166-0 (44.7% [39.1%, 50.3%]), dev=8.9% gate_blk=52.7% iters=80.4
- run 2: 149-151-0 (49.7% [44.0%, 55.3%]), dev=11.1% gate_blk=50.5% iters=72.7

**Pooled: 283/600 = 0.4717 vs the 0.55 bar → D2 = FAIL.** Instrumentation
shows the prior did its mechanical job — deviation-candidate pressure rose
(gate-blocked ~30% → ~51-53%) — but the extra deviations it surfaced did not
convert to wins. The 52% probe was sampling noise, caught by replication for
the third time this slice (54% Stage-1 probe, 52% here, both pooled to ≤0.50).

## Conclusion

**Both fixes worked at their own level; neither moved win rate. The
evaluator-quality family of hypotheses is now closed with measurements at
every link of the chain:**

| Pre-registered criterion | Bar | Measured | Verdict |
|---|---|---|---|
| D0 deficit(a−c) | ≥ 0.05 | 0.0806 | CONFIRMED |
| D0 differential(net−hte) | ≥ 0.03 | 0.0452 | CONFIRMED |
| Retrain bar: recover ½ deficit | AUC(c) ≥ 0.8571 | 0.8751 (72% recovered) | PASSED |
| Retrain bar: no on-policy collapse | ≥ 0.8951 | 0.8979 | PASSED |
| D1 pooled win rate | ≥ 0.55 | 0.5017 (301/600) | FAIL |
| D2 pooled win rate | ≥ 0.55 | 0.4717 (283/600) | FAIL |

The F4 causal chain — "off-policy blindness → bad deviation decisions →
parity" — is broken at its last link: the blindness was real (D0), was
repaired (retrain bar, differential flipped negative), the repaired signal
was even given control of exploration (Stage 2), and parity persisted.

**What remains, sharpened for Slice 7+:**

1. **Policy reach, not value accuracy.** Rollouts and the opponent model
   still play both sides as heuristic-v0. A deviation is evaluated under the
   assumption that BOTH players continue as v0 — lines whose value depends on
   follow-through the searcher itself would choose (or punishes the opponent
   would never make as v0) are systematically mis-valued in a way no leaf
   evaluator can fix. The fix shape is a policy-improvement loop (search-
   guided rollouts / iterated self-play), which is a full slice of its own.
2. **The no-exploitable-edge alternative.** Every Slice 4-6 arena result
   (gated, gate-off, budget-swept, selection-rule-swept, retrained, prior-
   steered) sits in [0.42, 0.54] on the mega-lucario mirror. It is
   increasingly plausible the symmetric mirror at 200ms simply lacks ±5%
   of per-decision edge for THIS search shape. A cheap discriminating test:
   run the identical agent pair on an ASYMMETRIC deck pairing (where
   per-decision choices differ more) before spending another slice on the
   search itself.

Per the pre-registered fail path: the ladder submission
(`src/ptcg/submission_main.py`) stays unchanged on HeuristicAgent +
mega-lucario-fighting. The v2 net, the tagging/eval infrastructure, and the
root-prior machinery (flag-off by default) ship as repo-side infrastructure
for future slices.
