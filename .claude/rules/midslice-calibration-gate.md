---
paths:
  - "docs/superpowers/plans/**"
  - ".claude/plan.md"
---

# Calibration-shaped slices: structure the Brad decision as a mid-slice MEASUREMENT gate, and reconcile his answer against the plan's own guarantees before dispatching

Some slices in this repo are not "build X" but "pick the right NUMBER for X"
— recalibrating `FLOOR_BAR`/`ANCHOR_BAR`, sizing a threshold, choosing a
promotion rate. These have a distinctive hazard: the correct value is
unknowable at plan-write time (it depends on production state that may have
shifted since the last session), yet the plan must be locked before the
implementers run. Guessing the value at plan time and "adjusting later" is
how a bar calibrated against a retired regime survives five sessions.

## The structure that worked

Do NOT ask Brad to pick the number at Brainstorm/Plan time, and do NOT let
an implementer pick it. Instead, encode a **three-beat mid-slice gate** into
the plan as its own task:

1. **Measure first** — a dedicated, read-only measurement task against LIVE
   production state (not a fixture, not a copy, not last session's numbers).
   Read-only means it can run with the workers up: `mode=ro`, no DB copy, no
   lock. Its deliverable is a receipt file, not a recommendation.
2. **Gate on the data** — a single AskUserQuestion whose options are
   *derived from the measurement just taken*, with the measured distribution
   quoted in the question. Brad is choosing between real numbers with known
   consequences, not between abstractions.
3. **Flow the answer forward** — the chosen values become inputs to the
   REMAINING tasks, which were written to accept them as parameters. Nothing
   downstream was blocked waiting on the gate; the tasks were already
   dispatchable the moment the answer landed.

The plan must state up front which downstream tasks consume the gate's
output, so the gate is a scheduled beat rather than an interruption.

**Datapoint (2026-08-13/14, census-screening-regime).** The 5-session-old
HIGH carry-forward `FLOOR_BAR`/`ANCHOR_BAR` recalibration was closed exactly
this way. `scripts/measure_screening_regime.py` (read-only, `mode=ro`, run
against the live `tournament.db` with workers up) produced the distribution;
the AskUserQuestion quoted it; Brad picked; T8 consumed the values.
The same measurement also surfaced something no one had asked about — the
active pool is 97% one builder shape — which **rescoped a planned cull
migration to SKIPPED**. That is the compounding argument for measure-then-
ask: the measurement answers the question you asked AND corrects a premise
you didn't know was wrong. A plan-time guess would have shipped both the
wrong bar and an unnecessary migration.

## The friction to prevent: reconcile the gate's answer against the plan's own guarantees BEFORE dispatching

A locked plan often carries invariants about the value being chosen — a
granularity guarantee, a range, a units convention, a "must be a multiple of
N" constraint that some downstream consumer or test depends on. Brad's
answer to the AskUserQuestion is a human answer to a human question; it is
under no obligation to satisfy those invariants, and the AskUserQuestion
options usually don't restate them.

The orchestrator is the only party holding both the locked plan and the
gate's answer at the same time. If it pastes the raw answer into the next
dispatch, the implementer receives a value that contradicts a guarantee the
same plan makes elsewhere.

**Same-session datapoint.** The gate answer was `0.45` at n=50. The plan
guaranteed bar values are multiples of 0.02 (n=50 granularity — 0.45 is not
representable as a win-rate at that sample size). The T8 dispatch carried
`0.45` verbatim. The implementer caught it and used `0.46`, the exact
multiple-of-0.02 equivalent — the right outcome, but the reconciliation was
the orchestrator's job, done one layer late and only by luck of a careful
implementer.

**Rule — a mandatory reconcile step between the gate and the next dispatch:**
after the AskUserQuestion returns and before writing the dispatch prompt,
re-read the plan's constraints on the chosen value and check the answer
against each:

- granularity/representability (is this value reachable at the stated n?)
- range/bounds the plan asserts
- units and direction (fraction vs percent, higher-is-stricter vs looser)
- any invariant a downstream test or consumer pins

If the answer violates one, do NOT silently snap it and do NOT paste it
through. Name the conflict and state the adjustment explicitly — in the
dispatch prompt AND in the plan.md Progress Log — e.g. *"Brad approved 0.45;
the plan guarantees multiples of 0.02 at n=50, so the dispatched value is
0.46 (the exact equivalent). Recorded as a reconciliation, not a
re-decision."* If the adjustment is not exactly equivalent (it changes the
decision's meaning rather than its representation), that is a second
AskUserQuestion, not an orchestrator judgment call.

This is the calibration-gate specialization of global CLAUDE.md's
"reality contradicts the plan → the contradiction is the deliverable": the
gate answer and the plan guarantee are both authoritative sources that can
disagree, and the orchestrator holds both.
