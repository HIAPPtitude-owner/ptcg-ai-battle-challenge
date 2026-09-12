# Slice 6 — Off-Policy Value Blindness: Verify, Then Fix (F4)

**Date:** 2026-07-10
**Status:** Approved (design sections 1-3 approved by Brad in-session)
**Predecessors:** Slice 4 (learned value net, gate FAIL at parity), Slice 5 (search-architecture investigation, documented dead-end, F4 armed as debt)

## Context

Slice 4 trained a value net on v0-self-play data that decisively beats the hand-tuned
evaluator offline (val AUC 0.8971 vs 0.7640 on 264k held-out positions) yet moved arena
win rate vs heuristic-v0 not at all (pooled 0.498 vs the 0.55 bar). Slice 5 then cleared
every mechanical suspect by measurement: gate starvation, iteration/time budget,
determinization noise, and the final-move selection rule are all ruled out
(`experiments/ANALYSIS-slice5-search-architecture.md`).

The residual hypothesis — F4 — is **off-policy value blindness**: the net was trained
exclusively on positions arising from v0-vs-v0 play, so positions on lines where the
search *deviates* from v0 are scored off-distribution, exactly where evaluation accuracy
decides whether a deviation is kept. A co-factor: rollouts and the opponent model score
both sides as fixed heuristic-v0, capping the lookahead advantage search can surface.
The carried-forward alternative hypothesis (the symmetric mirror matchup may have little
exploitable per-decision edge) remains live and is NOT tested by this slice.

**F4's premise is itself unverified.** This slice verifies it cheaply before committing
overnight-scale compute to the fix.

## Decision chain (all branches pre-authorized)

```
Phase 0: Diagnose off-policy blindness (~1-2h compute)
    |
    +-- falsified --> STOP. Document in analysis doc + EXPERIMENTS.md. Re-scope with Brad.
    |                 Ladder unchanged.
    |
    +-- confirmed --> Stage 1: mixed-policy retrain, net as leaf evaluator, UCB1 unchanged
                          |
                          +-- gate PASS (>=0.55 pooled) --> ladder-flip decision goes to Brad
                          |                                 explicitly (confirm-submission rule)
                          |
                          +-- parity --> Stage 2: value-derived root PUCT prior
                                             |
                                             +-- gate PASS --> ladder-flip decision to Brad
                                             |
                                             +-- parity --> documented dead-end (Slice-5 style);
                                                            remaining hypotheses carried forward
```

Staging rationale: F4 as armed bundles two changes (retrain + PUCT prior). They are
separable; staging isolates which change moves win rate. Each stage is cheap once the
data exists.

## Phase 0 — the diagnostic

**Prediction under F4:** the current (Slice-4) net is less accurate on positions from
search-play lines than on v0-play lines, specifically on positions *after* the first
deviation from v0's move.

**Procedure:**

1. Generate ~300 **gate-off** search-mirror games at the 200ms operating budget
   (~1-2h). Gate-off because raw search disagrees with v0 on ~34% of moves vs 3-6%
   gated (Slice-5 D1/D3), maximizing off-policy coverage per game.
2. Record positions + final outcomes in the Slice-4 JSONL schema, with each position
   **tagged `on_v0_path` vs `post_deviation`** — the tag is GAME-LEVEL: the first move
   in the game where EITHER player's chosen action (on a 1-of-1 select with >=2
   options) differs from heuristic-v0's action flips the tag for every subsequent
   position in that game, regardless of who is acting. (Plan-time refinement from the
   approved per-acting-player wording: off-distribution-ness is a property of the
   trajectory, not the mover — once either seat deviates, all later states are off the
   v0-self-play distribution. The deviating decision's own record stays on-path; its
   state was reached on-policy.)
3. Score the **current Slice-4 net** on three buckets:
   - (a) Slice-4 held-out v0 positions — the 264k baseline, known AUC 0.8971;
   - (b) search-game positions tagged `on_v0_path`;
   - (c) search-game positions tagged `post_deviation`.
4. Report AUC and BCE per bucket, with bucket sizes. Report **measured values**, never
   bare PASS/FAIL (repo rule: plan-test-arithmetic-sanity, expected-metric-semantics).

**Pre-registered verdict criterion (provisional — exact numbers pinned at plan time):**
blindness **confirmed** if bucket (c) AUC is >= 0.05 worse than bucket (a); **falsified**
if within noise of baseline. The interesting middle (uniform degradation across (b) and
(c) alike) suggests a distribution shift other than policy deviation (e.g., game length)
and is treated as falsification of the *specific* F4 mechanism — STOP branch, with the
measured pattern documented.

The ~300 Phase-0 games double as the first tranche of Stage-1 training data — nothing
is wasted on the confirmed branch.

## Stage 1 — mixed-policy data generation + retrain (leaf evaluator, UCB1 unchanged)

### Data generation

- Extend `scripts/generate_training_data.py` with an `--agent {v0,search}` selector plus
  pass-through search knobs (`--search-budget-ms`, gate on/off). Output schema: Slice-4
  JSONL plus the `on_v0_path`/`post_deviation` tag (v0 games are always `on_v0_path`).
- **Dry-run throughput probe first** (repo rule: verify-throughput-before-hypothesis):
  measure real games/hour on a ~20-game probe before sizing the overnight run. The
  6-12s/game figure is an estimate, not a measurement.
- Overnight run: **~3,000-5,000 gate-off search-mirror games at 200ms budget**, sized
  from the probe to fit ~10-17h wall-clock. Orchestrator-owned background shell per
  `.claude/rules/background-arena-execution.md` (settled default). Target ~300-500k
  search-line positions.
- **Blend: plain concatenation** with the existing 1.33M v0 positions (~20-27%
  search-line share). No upsampling in v1. The training log reports the distribution
  split so reweighting is a documented follow-up experiment, not a silent knob.

### Retrain

- Reuse `scripts/train_value_net.py`: same 40 features (`ptcg/search/features.py`,
  shared verbatim train/serve), same architecture, torch dev-side only, stdlib-JSON
  export with embedded golden vectors (Slice-4 conventions).
- **Held-out eval on both distributions**, split by *game* (not position) to avoid
  leakage: the Slice-4 v0 held-out set AND a held-out slice of the new search-line data.
- **Pre-registered retrain quality bar (informational, not the slice gate):** the
  retrained net recovers at least half of the Phase-0-measured post-deviation AUC
  deficit vs the v0 baseline, without on-policy regression > 0.02 AUC on the v0
  held-out set. An on-policy collapse is a stopping condition worth a re-plan, not a
  silent proceed.
- New weights ship as a **separate file** (`value_net_weights_v2.json`) selectable via
  config; Slice-4 weights stay in place so arena A/Bs are one flag and nothing downstream
  silently changes evaluator.

### Stage-1 arena gate

- `SearchAgent` + retrained net as leaf evaluator; UCB1, gate, and all other search
  config untouched. Arena vs heuristic-v0 at the 200ms operating budget.
- Slice-4 protocol: replicated runs (2x300, pooled 600), gate = pooled win rate >= 0.55.
  Stochastic-gate-replication rule applies: all runs reported, not just passing ones.
- Rollout-depth (0 = pure net leaf vs 12 = rollout+net) is a plan-time choice: default
  to replicating Slice 4's gate configuration for comparability; one small pre-gate
  probe is allowed to pick between the two.

## Stage 2 — value-derived root PUCT prior (only if Stage 1 gates at parity)

- **Root-only prior.** At move start, for each legal root action signature: step one
  determinization through that action, extract features, evaluate with the retrained
  net -> `v_a`; prior `P(a) = softmax(v_a / tau)`. Interior nodes keep UCB1 — the
  engine's search API steps forward only; per-child branching at every expansion is
  architecturally awkward and budget-hostile.
- **Selection at root:** `argmax over a of Q(a) + c_puct * P(a) * sqrt(N) / (1 + n_a)` —
  a new `puct_pick` alongside `ucb_pick` in `tree.py`, behind a config flag so every
  prior configuration is arena-A/B-able.
- **Budget accounting:** prior computation costs ~|A| engine steps + net evals (roughly
  one iteration's cost per legal root action; |A| typically 10-30 against ~54 iterations
  per 200ms move). It runs inside the move deadline via the existing `TimeManager`.
  Slice-5 D2 headroom (~48x) says this is safe; the dry-run probe re-verifies.
- `tau` and `c_puct` get plan-time defaults with one small probe run allowed before the
  gate. The v0-improvement gate stays **on** (Slice-5 D3: gate-off buys nothing; the
  gate is the safety net).
- Stage-2 arena gate: same protocol and bar as Stage 1.

## Fail/branch paths (pre-registered)

| Event | Action |
|---|---|
| Phase 0 falsifies blindness | STOP — document, re-scope with Brad. Ladder unchanged. |
| Stage 1 gate PASS | Ladder-flip decision to Brad explicitly (confirm-submission rule). |
| Stage 1 parity | Stage 2 proceeds, pre-authorized. |
| Stage 2 gate PASS | Ladder-flip decision to Brad explicitly. |
| Stage 2 parity | Documented dead-end (Slice-5 style). Remaining hypotheses (policy head, no-exploitable-edge) carried forward as Slice-7 candidates. |

In every path, `src/ptcg/submission_main.py` stays byte-identical unless a gate passes
AND Brad approves the flip.

## Testing

- Unit tests: `--agent` selector + deviation tagging in the data generator; game-level
  (not position-level) train/val split correctness; `puct_pick` math with hand-computed
  vectors; golden-vector parity for v2 weights (non-circular, hardcoded expected outputs
  per the Slice-4/token-golden convention).
- Existing invariants: regression pin, ladder-identity check (submission bytes
  unchanged), `write_text` encoding guard.
- All arena/generation series logged to `experiments/EXPERIMENTS.md` as they run.

## Deliverables

- `experiments/ANALYSIS-slice6-<topic>.md` — evidence document (every branch taken,
  measured values for every pre-registered criterion).
- EXPERIMENTS.md rows for every series (probe, Phase 0, overnight generation, training,
  gates).
- `src/ptcg/search/value_net_weights_v2.json` (if trained).
- CLAUDE.md status paragraph; memory + plan.md ledger updates.
- Smoke Test Ladder: slice touches derived numbers -> all three rungs recorded in
  plan.md before Finish.

## Non-goals

- No trained policy head / visit-count distillation (AlphaZero-lite) — carried as a
  future candidate, not this slice.
- No iterated self-play generations (single retrain pass only).
- No test of the alternative no-exploitable-edge hypothesis.
- No opponent-model changes (rollout policy stays heuristic-v0 both sides).
- No deck changes; no submission changes without an explicit gate PASS + Brad approval.

## Open questions resolved at plan time

- Exact Phase-0 confirmation threshold (provisional: AUC delta >= 0.05) and noise band.
- Overnight run size (from the throughput probe).
- Stage-1 gate rollout-depth configuration (replicate Slice 4 vs pure-net-leaf, one probe).
- `tau` / `c_puct` defaults and their probe design (Stage 2 only).
