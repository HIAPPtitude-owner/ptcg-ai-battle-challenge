---
paths:
  - "docs/superpowers/plans/**"
  - "tests/**"
  - "src/**"
---

# Sanity-check plan-authored test arithmetic before transcribing it

Plan documents in this repo sometimes write out a test's expected value or assertion inline (e.g., "assert clamped value equals X after applying clamp Y"). These plan-authored numbers are reference, not gospel (see global CLAUDE.md → Plan Writing & Dispatch), but the arithmetic-error failure mode is specific enough to name: a plan can assert a value that its OWN described clamp/transform makes unreachable — self-contradictory on inspection, not just wrong versus the real code.

**Datapoint (2026-07-09, Slice 2 T2):** the plan's test for `TimeManager` asserted a value that the same task's own clamp logic made impossible to produce. An implementer transcribing the assertion verbatim would have shipped a test that could never pass against correct code, or worse, "passed" against equally-wrong code.

**Practical rule:** before transcribing a plan-supplied assertion value into an actual test, work through the arithmetic (or the clamp/transform chain) by hand and confirm the target value is actually reachable given the described logic in the SAME plan section. If it isn't, flag `plan-drift`/`plan-encoded-test-bug` and derive the correct value from the described behavior rather than the plan's stated number.

**Track record:** injecting this as an explicit arithmetic-sanity directive into implementer dispatch prompts for T3-T8 produced zero recurrences in the rest of the slice — cheap to say, worth saying on every plan-execution dispatch in this repo.

## Refinement: the same self-check applies to plan-cited fixtures and inline code snippets, not just numbers (2026-07-10, Slice 4)

Plan-authored defects in this repo aren't limited to wrong arithmetic. The
same slice caught three more classes, each an implementer-verified-before-transcribing
catch rather than a shipped bug:

- **Nonexistent fixture reference.** A plan task cited a `SAMPLE_DECK` fixture
  that didn't exist in the codebase; the implementer resolved it to the
  repo's actual `[3]*60` convention instead of inventing the fixture.
- **Wrong prose claim about existing code.** A plan asserted a specific
  fixture style for `test_belief.py` that didn't match the file as written.
- **Malformed inline code.** A plan-supplied benchmark one-liner was a
  malformed f-string; the implementer rewrote it with the same methodology
  rather than transcribing the syntax error.

**Practical rule, extended:** before transcribing ANY plan-supplied
artifact verbatim — not just a numeric assertion — do the cheap check that
would have caught it: `Grep` the fixture/symbol name the plan cites to
confirm it exists before referencing it, and mentally parse (or paste into a
scratch REPL) any inline code snippet the plan hands you before typing it
into a real file. This is the general form of the landmark-verification rule
(global CLAUDE.md → Plan Writing & Dispatch) applied to test/benchmark code
specifically. No new directive needed beyond what implementers already do —
this section exists so the next planner recognizes the pattern by name
(`plan-drift`) rather than treating each occurrence as a one-off.

## Refinement: expected-metric semantics can drift, not just arithmetic (2026-07-10, Slice 5)

A plan-authored expectation can be internally consistent arithmetic and still
be **wrong about what the metric means**. Slice 5's D3 ablation task
(gate-off vs gate-on deviation from v0) had a plan step whose expected result
was phrased "deviation near 100% when the gate is off" — but the plan
conflated two different things: "the search plays its own most-visited move"
(a property of a plain best-move-selection ablation) versus "the search's
choice DIFFERS from v0's move" (the actual metric being measured, which
depends on how often v0 and the raw search happen to agree even with no
gate). The self-contradiction wasn't in the arithmetic — the number the plan
predicted was internally coherent — it was in which real-world quantity the
number was supposed to describe. This was caught only because the dry-run
validation step was written to report the exact measured value instead of a
bare PASS/FAIL.

**Practical rule, extended again:** (1) any plan-authored expected-value claim
must name the exact metric definition it refers to (e.g., "P(search's chosen
move != v0's move)", not just "deviation") — ambiguous metric names are a
`plan-drift` smell on their own; (2) experiment/analysis dry-run and
validation steps must report the **measured value**, never just pass/fail —
a bare PASS on a semantically-drifted expectation hides the drift completely,
while a printed number lets a human or reviewer notice "that's not what I
expected" even when the check technically passes.

## Refinement: plan-authored code needs a degenerate-input probe at review, not just its own test (2026-07-10/11, Slice 6 T6)

A plan-authored `load_jsonl` loader sketch tracked a running game-id offset
across multiple input files, incrementing it per file based on the prior
file's row count. The plan's own test fixture used two non-empty files, so
the chain worked; the actual bug — the offset chain resets/miscalculates
when one of the files is EMPTY — was invisible to that test because neither
fixture file was ever empty. The plan-authored test, transcribed verbatim,
would have shipped green and the bug would have shipped with it. It was
caught only because the reviewer independently probed a degenerate input
(an empty file) that the plan never considered, not because the plan's own
arithmetic was hand-checked. Fixed at commit `3771648`.

**Practical rule, extended a third time:** hand-checking a plan-authored
test's arithmetic (the original rule above) catches self-contradictory
numbers, but it does NOT catch a correctly-computed test that simply never
exercises the input class where the bug lives. For any plan-authored code
that processes a COLLECTION or SEQUENCE (files, rows, iterations, a growing/
accumulating chain of state across items), the review pass — not just the
implementer's transcription check — must explicitly probe at least one
degenerate case the plan's own fixtures don't cover: empty input, single-
item input, all-same-value input, or a zero/boundary count. Add this as a
standing item in reviewer dispatch prompts for any task implementing a
plan-authored loader/aggregator/accumulator: "the plan's own test fixture
may not exercise degenerate inputs (empty file, empty list, zero count) —
probe at least one before approving."

## Refinement: a plan-authored ITERATIVE ALGORITHM can diverge on its own fixture — smoke-simulate the loop, don't just hand-check one step (2026-07-21, evolutionary-agent-population T3)

Hand-checking a single arithmetic step (the original rule) and probing a
degenerate input (Slice-6 refinement) both assume the plan-authored code is
a straight-line transform or a bounded pass over a collection. A plan can
also hand you an **iterative/optimization loop** — a gradient update, a
fixed-point solve, an EM/annealing step, a rating-fit — whose per-step
arithmetic is individually correct but whose *dynamics* diverge, oscillate,
or converge to the wrong fixed point on the plan's own fixture. No
single-step hand-check catches this, because every step in isolation looks
fine; the defect only appears when the loop is actually run to
(non-)convergence.

**Datapoint (2026-07-21, T3):** the plan's Bradley-Terry rating gradient
was written **unnormalized**, so it diverged (ratings ran away instead of
settling) on the plan's own fixture. The implementer caught it by actually
running the loop and watching the ratings blow up, then corrected to a
normalized/mean-centered update — a receipt-backed catch, but one the
plan-writing step could have made itself with a 5-line smoke simulation.

**Practical rule, extended a fourth time:** for any plan-authored code that
is an iterative loop toward a fixed point / minimum / equilibrium (gradient
descent, Bradley-Terry / Elo fitting, EM, annealing, power iteration, any
`while not converged` shape), the planner must either (a) smoke-simulate the
loop on a tiny fixture and confirm it actually converges to the claimed
value before locking the plan, or (b) explicitly flag the update rule as
UNVERIFIED-DYNAMICS in the plan so the implementer runs it to convergence
before trusting it — never transcribe an iterative update rule as if its
per-step correctness implied convergence. "The gradient term is right" is
not evidence "the loop converges."

## Re-confirmed on a plan-authored concurrency receipt (2026-08-05, ui-remove-any-deck T7)

The plan's Task 7 spelled out expected statement-execution counts and
decision-row counts per serialized branch of a race test (bulk-cull vs.
census promotion — see `.claude/rules/single-actor-worker-tests.md`'s
barrier+counter+overlap receipt pattern) alongside the arithmetic that
should produce them, exactly the shape the original rule above warns about.
The implementer hand-verified the plan-authored expected counts against the
actual code paths before transcribing the test, per the arithmetic-sanity
directive, and corrected a discrepancy in the plan's stated statement count
before the test was committed — the directive caught it at implementation
time, not at review. Zero recurrence of the self-contradictory-constant
class this session. Counts as a re-confirmation, not a new failure mode:
the mitigation (hand-check plan-authored arithmetic before transcribing,
now standard practice per this file) continues to hold across a 6th+
session since the directive was first injected.

## Re-confirmed a 7th+ time (2026-08-10, pool-pruning-ui-improvements T2)

T2's plan sketch for `deck_quality._analyze` used `len(key)` as the deck
size passed into the hypergeometric mulligan-probability calculation. Fine
against a real 60-card deck, but a `ZeroDivisionError` (`deck_size - i`
denominator hits zero) against the small non-60-card fixture decks the same
plan used to test caching behavior. The implementer verified the arithmetic
against the plan's own stated fixtures before transcribing (per the
standing directive), caught the mismatch, and hardcoded the mulligan
calculation to the fixed `deck_size=60` default rather than the input
length — documented inline in the shipped code
(`src/ptcg/factory/deck_quality.py`) specifically so a future reader
doesn't "fix" it back to `len(key)`. Zero rework; caught pre-commit.
Continues to hold as settled practice.
