---
paths:
  - "experiments/**"
  - "src/ptcg/search/**"
  - "scripts/**"
---

# Score an independent control evaluator on the same rows before confirming a distribution-shift diagnosis

When measuring whether a trained model's accuracy drop across two data
distributions is *model-specific* (something wrong with what the model
learned) versus *intrinsic* (the second distribution is just harder for
any evaluator), the raw accuracy delta alone cannot tell the two apart. A
raw deficit confirms nothing on its own — it's consistent with both
explanations.

**Datapoint (2026-07-10/11, Slice 6 Phase-0 diagnostic D0):** the value net's
AUC dropped from 0.8974 (normal validation split) to 0.8168 (post-deviation
positions reached only when search diverges from the policy it was trained
on) — a raw deficit of 0.0806, comfortably over the pre-registered
CONFIRMED bar. But the SAME post-deviation rows were also scored with a
second, independent evaluator already present in every training row (the
project's hand-tuned heuristic evaluator, `hte`), and it dropped too —
0.7648 -> 0.7294, a deficit of 0.0354. That means roughly 44% of the raw
deficit was just intrinsic hardness of post-deviation positions (harder for
ANY evaluator, trained or hand-tuned), not blindness specific to the net.
The net-specific signal is the **differential** (model deficit minus
control deficit) = 0.0452, which is what actually cleared the amended
CONFIRMED bar. Without the control evaluator, the diagnosis would have
false-confirmed on generic game-hardness rather than genuine off-policy
blindness.

**Practical rule:** whenever a diagnostic task's design is "score model M
on distribution A vs distribution B and treat the delta as evidence of a
model-specific defect," require a second, independent evaluator (ideally
one already computed/available in the same data, so it costs nothing extra
to score) on the identical sampled rows. Report BOTH the raw deficit and
the differential-against-control, and gate the CONFIRMED/not-confirmed
verdict on the differential, not the raw number alone. This generalizes the
"independent recompute must use real non-zero data, not a fixture" family
of rules (global CLAUDE.md → Subagent Discipline) to distribution-shift
diagnostics specifically: the control evaluator is what makes the recompute
actually independent of the phenomenon being measured, rather than just
independent of the code path.
