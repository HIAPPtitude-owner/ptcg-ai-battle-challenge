---
paths:
  - "scripts/run_arena.py"
  - "experiments/**"
  - "src/ptcg/search/**"
  - "docs/superpowers/plans/**"
---

# Concurrent CPU work during time-budgeted search cells biases the result downward, conservatively

`SearchAgent` moves are governed by a wall-clock time budget (`--search-budget-ms`,
default 200ms per decision), not an iteration count. Any other CPU-bound work
sharing the machine while a time-budgeted arena series is running (a parallel
`pytest` suite, a concurrent training job, another arena series) steals cycles
from the search's iteration loop within its fixed time window, so the
time-budgeted side of the matchup gets fewer ISMCTS iterations than it would
on a clean machine — it is not a timing bug, it is the time budget doing
exactly what it says under contention.

**Direction of the bias matters.** Contention only ever SHRINKS the number of
iterations a time-budgeted agent gets in its window; it can only push a
treatment agent's measured win rate DOWN relative to a clean-machine run,
never up. A win-rate result measured under contention is therefore a
conservative (lower-bound) estimate of the treatment's true strength, not an
unbiased one. This matters when interpreting a near-parity or slightly-below-parity
result: if the arena series ran concurrently with other CPU work, a genuine
edge could be getting suppressed rather than absent.

**Datapoint (2026-07-11, Slice 7A):** a real production acceptance run's arena
cells executed concurrently with pytest suites the orchestrator was running in
parallel elsewhere in the session; the observed win rates for the time-budgeted
side were plausibly depressed relative to a clean run. The bias was recorded as
a caveat on the result rather than re-run clean, since Slice 7A's asymmetric
test decision (FLAT, pooled differential -0.0078) was already conservative in
the "no edge found" direction and contention bias only strengthens that
conclusion — a shrunk-iteration treatment agent that STILL doesn't separate
from the baseline is stronger evidence of "no exploitable edge," not weaker.

**Practical rule:** before launching (or interpreting) a time-budgeted arena
series, check whether other CPU-bound background work (test suites, training
runs, other arena series) will run concurrently on the same machine. If a
genuinely unbiased number is required (e.g. a stochastic acceptance gate near
its 0.55 bar), schedule the series to run alone. If contention is
unavoidable or already happened, record the caveat explicitly next to the
result rather than treating the number as unbiased — and note which
direction (toward parity/loss) the bias pushes the treatment side, since that
determines whether the caveat weakens or strengthens the conclusion actually
drawn.
