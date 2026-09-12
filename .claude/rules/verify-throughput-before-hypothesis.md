---
paths:
  - "docs/superpowers/plans/**"
  - "experiments/**"
  - "src/ptcg/search/**"
  - "scripts/**"
---

# Verify a throughput/latency extrapolation against a real workload before building a hypothesis on it

A synthetic microbenchmark can be off by a large multiplicative factor versus
the real workload it's meant to stand in for, and a slice's leading
hypothesis can get built entirely on the bad number before anyone runs the
real thing.

**Datapoint (2026-07-10, Slice 5 search-architecture investigation):**
Slice 2's synthetic 40-ply microbenchmark predicted ISMCTS would complete
roughly 9 iterations per 200ms move budget. Real-game instrumentation in
Slice 5 measured ~54 iterations/move under the same budget — a 6x error.
The slice's leading hypothesis going in (iteration budget is too shallow to
matter) was built on the 9-iteration number; the real measurement reshaped
which suspect was actually worth investigating first.

**Practical rule:** before building or prioritizing a hypothesis on a
throughput, latency, or iteration-count extrapolation taken from a synthetic
benchmark or spike, spend one instrumented run against the REAL workload
(real game state, real decision points, real time budget) to confirm the
extrapolated number is in the right ballpark. A synthetic spike is fine for
early feasibility checks; it is not fine as the quantitative basis for
ranking suspects or sizing a fix. This generalizes the existing
"independent recompute must use real non-zero data, not a fixture" rule
(global CLAUDE.md → Subagent Discipline) from arithmetic verification to
throughput/latency verification specifically.

## Refinement: a "real workload" probe still carries bias if it samples only one pairing/config (2026-07-10/11, Slice 6)

Even a REAL-workload probe (not a synthetic benchmark) can mislead if it
happens to sample a narrow, unrepresentative slice of the real workload's
variation. Slice 6 hit this twice in one pipeline: (1) the plan's own
estimate for search-data generation throughput (6-12 s/game) was itself off
by 3-5x against the real measured rate — 32.05 s/game across the actual
overnight generation run; (2) the small verification probe run FIRST to
sanity-check that estimate (20 games) was itself biased HIGH relative to
the eventual full 315-game production run (22.69 s/game measured), because
the 20-game probe happened to sample only the first — and, it turned out,
slowest — deck pairing in a 45-pairing rotation, not a representative mix.

**Practical rule, extended:** a throughput/latency probe against the real
workload is only as trustworthy as its SAMPLING breadth, not just its
realism. When the real workload has multiple distinct configs/pairings/
branches with plausibly different costs (different deck matchups, different
game-length distributions, different code paths), a small probe must
deliberately sample ACROSS that variation — not just take the first N items
in whatever order they're generated/iterated — or explicitly flag that it
sampled a narrow slice and the resulting number is a lower/upper bound, not
a central estimate. Cheapest fix: shuffle or explicitly stratify the sample
across the known variation axes before timing it.
