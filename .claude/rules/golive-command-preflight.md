# Before Finish, re-verify every post-merge go-live command line against the real script interface

This repo ships a post-merge go-live rung in nearly every slice (register or
restart Scheduled Tasks, reseed the tournament pool, backfill a series, lift
`PAUSE`/`SUBMIT_HOLD`). Those rungs are written into the plan during the
**Plan** phase — before the scripts they invoke exist, or before an
implementer legitimately deviates from the plan's sketched interface. The
plan's command line then goes stale silently: it is prose, so no test, no
typecheck, and no reviewer running the fast suite ever executes it.

The failure lands at the worst moment — a privileged, production-touching
step, often run under time pressure with the factory paused.

## Rule

As an explicit step of the **Finish** phase, before executing (or handing
Brad) any post-merge go-live rung: for every command line the plan's go-live
section names, run the script's own `--help` (or grep its `add_argument`
block) and reconcile flag-by-flag. Fix the PLAN text, not just the invocation
you happen to type — the plan is what a resumed session or a Brad-run rung
will read.

```bash
uv run python scripts/<name>.py --help
# or, faster:
grep -n "add_argument" -A 3 scripts/<name>.py
```

Check specifically: **required vs. defaulted** flags (a plan that says "runs
against the live DB by default" against a script with `required=True` is a
hard failure at go-live), renamed flags, changed defaults, and positional-vs-
flag drift.

## Datapoint (2026-08-04, tournament-breeding-anchor-pressure)

The plan's go-live rung documented the pool reseed as defaulting to the live
tournament DB. The implementer made `--db` **required** in
`scripts/reseed_tournament_pool.py:266` — a deliberate and correct safety
deviation (`help="Never the production tournament.db in tests."`), reported
in the task report and accepted at review. Nobody propagated it back into the
plan's go-live section, so the documented command line would have failed
immediately with an argparse error at the go-live rung.

Harmless here because the orchestrator ran the rung interactively and caught
it in one round-trip. It would not have been harmless if the rung had been
handed to Brad as a copy-paste block, or resumed by a later session reading
the plan as ground truth.

**Related:** this is the plan-side twin of global CLAUDE.md's
`dryrun-is-not-the-real-thing` lesson (a real/effectful path is untested by
any dry-run) and of the pre-lock landmark-grep rule (verify cited landmarks
against the real files before lock). Landmarks get grepped at plan-lock;
**go-live command lines need the same treatment at Finish**, because they
are the one part of the plan that is written early and executed last.

### Re-confirmed (2026-09-01, strategy-report T12) — 2nd datapoint, and the first on a THIRD-PARTY CLI rather than a project script

The plan-authored T13 Step 1 pre-flight invoked
`kaggle competitions submissions -c <slug>` and
`kaggle competitions files -c <slug>`. The real CLI takes the competition slug
**positionally** for both subcommands; only `competitions list` takes a flag
(`-s <search>`). Caught by running this rule's reconciliation step before
executing, and the plan text was corrected in `0e4336f`.

Two things this datapoint adds to the rule, both worth acting on:

1. **The rule holds for third-party interfaces, and matters MORE there.** With
   a project script (the 2026-08-04 `--db` datapoint) an implementer at least
   *reported* the deviation, so a diligent reader had a second chance to catch
   it. With a third-party CLI nobody ever changed anything — the plan's command
   line was wrong from birth, sourced from the plan author's memory of the flag
   shape, which is the single staleest source in the loop and generates no
   report at all. Reconcile against the tool's own `--help`, never against
   recall of how you have invoked it before.
2. **Reconcile per SUBCOMMAND, not per tool.** A CLI is not obliged to be
   internally consistent: the same `kaggle competitions` noun took the
   competition as a flag under one verb and positionally under two others.
   Checking one subcommand's interface tells you nothing about its siblings —
   run `<tool> <subcommand> --help` for each command line the plan names.

Cost when it fires: one round-trip during an interactive pre-flight. Cost if
it does not: the T13 rung is a **one-shot, unrepeatable** competition
submission, exactly the copy-paste-block-handed-to-Brad scenario the
2026-08-04 entry named as the case where this would not have been harmless.

## Prefer running an EXISTING regression test against a live-DB copy over hand-rolling a go-live probe

A go-live rung often wants to confirm some invariant still holds against
real production state ("can the pair-gate still reconstruct the evictee
after this migration culled its concept?"). The reflex is to hand-roll a
throwaway probe script that calls the production function directly. That
probe is brand-new, unreviewed code written under go-live time pressure
against an API you are not currently holding in context — so its failures
are usually the PROBE's bugs (wrong arity, wrong row shape, wrong fixture
type), not findings about the system, and each one costs a round-trip
while the factory sits held.

**Datapoint (2026-08-11, min-basics-pool-rule).** A hand-rolled pair-gate
probe failed twice on interface mistakes before running — `resolve_evictee`
takes Kaggle-row objects, not the plain dicts/ids the probe passed. The
invariant it was trying to check was ALREADY covered by a committed
regression test (`88039fc`, "pin pair-gate evictee reconstruction against
culled ex-champions") and had already been verified by the whole-branch
reviewer. Two round-trips bought nothing.

**Rule, in order of preference, for any go-live verification rung:**
1. **Run the existing regression test** that already pins the invariant,
   pointed at a COPY of the live DB (`cp tournament.db /tmp/probe.db`, then
   the test's `--db`/fixture pointed there). The test already knows the
   real call signatures and already went through review.
2. If no such test exists, that absence is itself the finding — the
   invariant is unpinned. Prefer writing the test (it lands as durable
   coverage) over writing a throwaway probe.
3. Hand-rolled probes are the last resort, for genuinely one-off
   measurements with no invariant behind them (row counts, timings). When
   you do write one, `grep` the real signature of every production function
   it calls before running it, per the flag-reconciliation rule above —
   the same staleness that bites go-live COMMAND LINES bites go-live PROBE
   CODE, for the same reason.

## The go-live section's PASS CRITERIA go stale exactly like its command lines — trace each one against LIVE production state, and make it a whole-branch-reviewer checklist item

Everything above is about the go-live rung's *commands*. The same
write-early/execute-last staleness applies to its **verification criteria**
— the "step N PASSES when we observe X" clauses — and that half is worse,
because a stale command line fails loudly with an argparse error while a
stale criterion fails *silently*: it either can't be evaluated at all
against real data, or it can be satisfied by a state that doesn't actually
demonstrate the thing shipped.

A criterion is written at plan time against an *assumed* production state.
Nothing in the normal review stack ever executes it: per-task reviewers see
one task's diff, the whole-branch reviewer reads code, the suite tests
behavior. Nobody queries the live DB and asks "would this criterion be
observable right now?" — so it rides to the final gate unchecked.

**Datapoint (2026-08-13/14, census-screening-regime).** The plan's go-live
step-4 PASS criterion was keyed on `energy_count` ordering. A mid-slice
production measurement had already established that the active pool is
**97% one single builder shape** (16 energy / 27 Pokemon / 17 trainers) —
so `energy_count` is currently *inert*, carries no ordering signal, and the
criterion could never have demonstrated the shipped directive was live.
Per-task reviews, the whole-branch review, and a green 1216-test suite all
passed it. Pass 2 caught it and BLOCKED; re-keying the criterion to
`pokemon_count` (`8a10a5b`) produced a criterion that actually
discriminates — and the real go-live receipt then confirmed it (30/30
newest screening subjects front-loaded at `pokemon_count=24` ahead of the
`pokemon_count=27` bulk, zero NULLs). Note the measurement that invalidated
the criterion was taken **in this same session, before the criterion was
re-read** — the data to catch it existed the whole time; nobody re-traced
the go-live section against it.

**Two rules:**

1. **At Finish, alongside the flag reconciliation above, trace every
   go-live PASS criterion against live state before executing the rung.**
   For each criterion ask: *can this be observed in production right now,
   and does observing it actually prove the change shipped?* A read-only
   query against the live DB (or a `mode=ro` probe) is usually one command.
   A criterion that is unobservable, or that a pre-change state would
   satisfy just as well, is a tautological receipt in the sense of
   `.claude/rules/single-actor-worker-tests.md` — it will pass regardless
   and proves nothing.

2. **Add it to the WHOLE-BRANCH reviewer's standing dispatch checklist, so
   it is caught a layer before Pass 2.** Include verbatim:

   > Read the plan's Go-Live section. For each PASS criterion it states,
   > determine whether it is observable against CURRENT production state
   > and whether observing it would actually discriminate shipped-vs-not.
   > Run a read-only query to check where you can. Report any criterion
   > that is unobservable, inert, or satisfiable by the pre-change state
   > as a finding — a green suite is not evidence a go-live criterion has
   > fail-power.

### Corollary — mark gate-CONDITIONAL go-live steps as conditional at plan-write time

A go-live step whose existence depends on a decision that has not been made
yet (a mid-slice Brad gate, a measurement outcome) will be written in the
plan as unconditional prose, and then silently falsified when the gate
resolves the other way. The plan text keeps confidently naming a script or
migration that no longer exists.

**Same-session datapoint.** The plan's go-live section named a cull
migration script. The T6 gate measurement (97% one shape → no composition
tail to cull) led Brad to rescope that cull to SKIPPED — so the script was
never written, while the go-live section still instructed a future reader
to run it. Caught by the whole-branch reviewer (`aa1028e`, "stale Go-Live
step 5") — one layer earlier than the criterion problem above, but still
after lock.

**Rule:** when writing a go-live step that depends on an unresolved gate,
write it as explicitly conditional and name the deciding gate —
*"Step 5 (ONLY IF the T6 gate selects cull; SKIP entirely otherwise): run
`scripts/<x>.py`"* — rather than as an unconditional instruction. Then, when
the gate resolves, updating the plan is a one-line edit that the resolution
itself prompts, instead of a stale landmine that depends on someone
re-reading the whole go-live section later.

## Generalized beyond go-live: this whole file applies to ANY date-gated or deferred-execution plan section — those sections are where the final gate's findings concentrate

Everything above is scoped to the post-merge go-live rung because that is
where this hazard was first found. But the go-live rung is not special — it is
just this repo's most common instance of a **plan section written during Plan
and executed weeks later, by nobody, in the meantime**. Any section with that
shape inherits the entire failure family: stale command lines, stale PASS
criteria, unowned steps, and branches that never terminate. Date-gated tasks
("[DATED post-Aug-31] …", "[DATED ~Sep 8-10, Brad-gated] …") are the purest
form — they are guaranteed not to execute during the slice that writes them,
so no implementer, no per-task reviewer, and no test ever runs them.

**Datapoint (2026-08-24, strategy-report).** A 13-task plan closed with T12
and T13 open **by design** — a post-convergence number-finalization runbook and
a Brad-gated one-shot-submission runbook. The report body itself was clean:
Pass 1's spot-checks passed, a full T10 fact-check pass returned 67 verified /
0 failed, and Pass 2's independent spot-checks all passed. **Both** of Pass 2's
BLOCKER findings landed in the T12/T13 runbook text, and nowhere else:

1. **A non-convergence deadlock.** T12 said "skip and leave placeholders
   unless spread < 5.0"; T13 required zero placeholders before submitting.
   The two clauses were written in different sittings and never read against
   each other. Critically, this was not a hypothetical corner: the *measured*
   spreads that same day were **10.6 and 26.7** — the deadlock condition was
   the live state of the world, so as written the plan could never reach a
   submittable state. Fixed (`d27784f`) by converting the check into an
   explicit two-branch selector, both branches provably terminating at zero
   placeholders.
2. **An unowned required step.** The plan's own mandatory ">=81-word trim"
   appeared in a carry-forward note but was performed by no step in either
   T12 or T13. Fixed by adding an explicitly owned trim step.

Both were pure plan-text defects. A green 1239-test suite, a clean word gate,
and three independent fact-verification layers said nothing about either,
because none of them execute prose.

**Two checks, run at Finish, on every deferred/date-gated section — the same
pass that reconciles go-live command lines and PASS criteria:**

1. **Termination.** For every conditional in the section, walk *both* arms to
   the end state the plan requires. Does each arm actually reach a
   "done/submittable" state, or does one arm exit into a condition a later
   step forbids? Then check the live value of the deciding variable — a branch
   that is unreachable in theory and *taken today* is not an edge case.
2. **Ownership.** List every step the section says MUST happen (including ones
   named only in a carry-forward note or a blocker line) and point at the
   numbered step that performs each. A requirement with no owning step is not
   scheduled, it is merely mentioned.

### Sub-corollary — a deferred section's own gate checks must read the VERDICT STRING, not the exit code

The exit-code-laundering family (global CLAUDE.md → Windows/Platform-Quirks;
`.claude/rules/dispatch-test-run-directive.md`'s pipe corollary) shows up here
in its purest form, because a deferred section's checks are written from
memory of what a script does rather than from running it.

**Same datapoint, receipted:** `scripts/report_wordcount.py` returns
`WARN, code 0` for over-target-but-not-failing and `PASS, code 0` for clean —
two materially different verdicts behind one exit code. T12/T13's pre-flights
were written as "confirm the gate exits 0", which passes silently on an
over-length report. The measured state at the time was **1981 words against a
1900 target — a live WARN** that an exit-code check would have waved through
into a one-shot, unrepeatable competition submission. Fixed by switching both
checks to assert the verdict *string* contains `PASS`.

**Rule:** whenever a deferred plan step verifies a script, grep that script's
actual exit-code assignments (`grep -n "exit\|verdict" <script>`) and confirm
the check discriminates every verdict the script can emit. A multi-verdict
script collapsed onto one exit code is the default, not the exception — write
the check against the thing the script *says*, not the number it returns.
