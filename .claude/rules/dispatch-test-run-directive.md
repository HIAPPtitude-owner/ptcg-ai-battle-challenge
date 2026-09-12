# Test-run dispatch directive: use the strengthened, explicit form — the soft "foreground/synchronous" wording has already failed once in this repo

Global CLAUDE.md's "Long-command dispatch directive" (Subagent Discipline)
says to include, verbatim, when dispatching an agent to run a test suite or
long command: *"Run this in the FOREGROUND and wait synchronously for it to
finish; do not end your turn while the run is still in progress."*

**That exact soft wording has now failed THREE TIMES in this project —
a loop-failure by global CLAUDE.md's own definition ("a second occurrence of
the same class means the capture/injection loop itself failed").**

1. **2026-07-23, Phase 1, T5** — an implementer backgrounded its full
   pytest run and ended its turn "waiting for the Monitor watcher" despite
   the dispatch's "run in the FOREGROUND and wait synchronously" directive.
   Recovered with the standard SendMessage nudge. Injection strengthened
   for the rest of that session to: *"single synchronous Bash call; NEVER
   pass run_in_background; NEVER use the Monitor tool"* — naming the tools,
   not just the mode.
2. **2026-07-24, Phase 2, T13/T14** — despite the T5 escalation, a fixer
   subagent armed the Monitor tool on the fast suite and again ended its
   turn mid-wait. Recovered with one more SendMessage nudge (zero rework).
   Injection strengthened again to the form below.
3. **2026-08-01, submission-strength-gate, T2** — despite the T13/T14
   escalation and the verbatim-directive strengthening, an implementer
   again armed Monitor on a targeted test run. Third recurrence triggered
   the rule's escalation clause: restructure the dispatch shape itself,
   rather than re-wording for a third time.

**Fix applied (restructure, not re-wording): implementers run ONLY their
targeted test file (quick regression), orchestrator owns full-suite runs at
sync points.** Zero further stalls occurred across the rest of the slice after
restructure applied (2026-08-01 session, session complete). This restructure
is now the **settled default** for this repo — do not escalate the wording
again; use this dispatch shape for ALL test-running tasks going forward:

## Settled Default: Restructured Dispatch Shape

**For implementer test-running tasks: ask implementers to run ONLY their
targeted test file (quick regression for their specific task).** Do NOT ask
them to run the full suite, even with verbatim "no Monitor" instructions.

**For full-suite verification: the orchestrator runs it directly at sync
points** (after task integration, before review; after fix verification;
before final gates). This removes the dispatch shape that tempts a subagent
to reach for Monitor or run_in_background as workarounds.

**Rationale:** Three Monitor-stall recurrences (including one with the
strongest verbatim wording) proved that the dispatch shape itself is the
hazard, not the wording. Removing the subagent from the full-suite path
removes the hazard entirely, zero rewording needed.

### Scope clause — "implementer" is NOT the boundary; the FINISH-phase agent is the blind spot (4th recurrence, 2026-08-14)

The restructure above is written in implementer vocabulary ("ask
implementers to run ONLY their targeted test file"), so it reads as a rule
about the Implement phase. It is not. The hazard is *any dispatched agent
being asked to run the full suite*, and the phase where that is most likely
to slip through is **Finish** — the finishing-a-development-branch dispatch
naturally includes "verify the suite is green before merging," which is a
full-suite run handed to a subagent, by a rule that never mentioned Finish.

**Datapoint (2026-08-14, freeze-pair-probe-and-finalize).** The Finish agent
was dispatched with a full-suite run despite three prior recurrences and a
settled rule. Nothing about the rule's *text* failed — the rule's own
diagnostic question ("did this dispatch ask a subagent to run the full
suite?") was simply never asked while constructing a NON-implementer
dispatch, because the rule reads as implementer-scoped.

**Structural guard — one line, applied at dispatch-construction time for
EVERY dispatch in every phase (Implement, Review, fix waves, Finish,
Wrap-Up):**

> Before sending any dispatch: does this prompt ask the agent to run
> `pytest` without naming a specific test file? If yes, delete that
> instruction and run it yourself at the sync point.

Concretely for Finish: the orchestrator runs the pre-merge full suite
ITSELF (on a quiet machine, per the section below), then hands the Finish
agent the *result* — "suite green at N passing on commit `<sha>`, verified
by the orchestrator" — as an input, never as a task. The Finish agent's job
is branch mechanics (merge, tag, push, plan.md close-out), not verification
it would have to background to survive.

**Re-confirmed (2026-08-04, tournament-breeding-anchor-pressure).** Second
consecutive clean session under the restructured shape: ~30 dispatches
(10 implementers + per-task reviewers + a whole-branch review + two fix
waves), **zero Monitor stalls and zero `run_in_background` test runs**.
The orchestrator owned every full-suite run at its sync points (863 → 912
→ 929 → 943). Two clean sessions in a row on a failure class that had
recurred three times under three successive wordings — treat the
restructure as settled and stop re-litigating the directive text. Do not
add a fourth wording escalation; if a stall ever recurs, the question to
ask is "did this dispatch ask a subagent to run the full suite?", not
"was the wording strong enough?"

**Re-confirmed (2026-08-05/07, ui-remove-any-deck).** Third consecutive
clean session under the restructured shape: ~25 dispatches (8 implementers
+ per-task reviewers + a whole-branch review + a Pass-2 fix wave), zero
Monitor stalls, zero `run_in_background` test runs. The orchestrator owned
every full-suite run at its sync points (985 → 1026 → 1039). Treat this as
confirmation the restructure generalizes past the two sessions it was
proven on, not a new datapoint requiring further tracking — stop logging
individual re-confirmations for this rule unless a stall actually recurs.

**Recurrence #4 (2026-08-14, freeze-pair-probe-and-finalize) — the
restructure's SCOPE was violated, not its wording, and the failure mode
recurred exactly as predicted.** The settled default above says implementers
run only their targeted test file and the orchestrator owns full-suite runs
"at sync points (after task integration, before review; ... before final
gates)" — but names those sync points without explicitly saying Finish-phase
merge/integration dispatches are included. This session's orchestrator
dispatched the FINISH agent itself to run the full suite as part of merge
verification — a full-suite run handed to a subagent, exactly the dispatch
shape the restructure exists to prevent — and the agent backgrounded the
suite and stalled mid-wait, precisely as the restructure predicts for any
subagent asked to run a full suite. Recovery: one SendMessage nudge plus the
orchestrator taking over the run in its own background shell — zero rework,
consistent with every prior recovery in this file. **No wording change to
the rule's mechanics** — the fix is scope, not phrasing: Finish-phase
merge/integration verification is a full-suite run and therefore ALWAYS
orchestrator-owned, with no carve-out for "but it's the Finish agent, not an
implementer." Any dispatch — implementer, reviewer, fixer, or a
lifecycle-phase agent like Finish/Wrap-Up — that could plausibly reach for a
full-suite command must either be scoped to a single test file, or the
orchestrator must run the full suite itself and hand the agent only the
result.

## Corollary — the orchestrator now owns full-suite runs, so the ORCHESTRATOR is the one who can launder their exit code

The restructure above moved full-suite runs from subagents to the
orchestrator. That closed the Monitor-stall class and opened a different
one, on the same command: **piping the run into `| tail -N` (or `| head`,
`| grep`) reports the exit status of the PIPE, not of pytest.** A suite
that exits nonzero reads as a clean run because `tail` exited 0 — the same
exit-code-laundering shape already documented in global CLAUDE.md's
Windows/Platform-Quirks section for `; echo "exit $?"` wrappers and in
`dryrun-is-not-the-real-thing`, but committed by the orchestrator against
its own baseline run rather than by a script.

**Datapoint (2026-08-11, min-basics-pool-rule):** the orchestrator ran the
baseline suite through `| tail -5` to keep the output small and took the
truncated tail as the verdict. Caught in-session, but a truncated tail of a
red suite looks identical to a truncated tail of a green one for any
failure that isn't in the last 5 lines.

**Rule:** when the orchestrator runs the full suite, either (a) run it bare
and read the real summary line, or (b) if the output must be trimmed,
capture the status explicitly and print it AFTER the pipe —
`uv run pytest > "$LOG" 2>&1; echo "PYTEST_EXIT=$?"; tail -20 "$LOG"` — and
read `PYTEST_EXIT`, not the tail. Never treat a piped run's completion as
a pass.

## Full-suite verdicts on this machine are only trustworthy on a QUIET machine

This host runs the factory workers (`ptcg-factory-runner`,
`ptcg-factory-scheduler`, `ptcg-factory-continuous`, `ptcg-factory-ui`)
24/7 at BelowNormal priority, and slices routinely run review probes
against DB copies concurrently. Several tests in this suite are
timing/throughput-sensitive and fail under that load while passing cleanly
in isolation — a red suite is therefore NOT automatically evidence of a
regression, and re-running the failures alone is the cheapest
discriminator.

**Datapoint (2026-08-11, min-basics-pool-rule):** 3 tests failed on a
full-suite run executed concurrently with review probes; all 3 passed in
isolation immediately afterward, with no code change. `tests/test_train_policy_export.py`
is the confirmed repeat offender — the same file was observed
load-sensitive in two prior sessions, making 2026-08-11 the third
occurrence.

**Rule:**
1. Take the load-bearing full-suite verdict (the one recorded at Finish, or
   quoted to Brad) on a quiet machine — factory `PAUSE`d/drained or workers
   otherwise idle, and no concurrent review probes running. State in the
   report which it was.
2. On a failure observed during a loaded run, re-run the failing tests
   ALONE before treating them as a regression, and report both results.
   Do not report a loaded-run red as a regression on its own.
3. When a test is confirmed load-sensitive (fails loaded, passes isolated,
   no code change), append its path to the list above with the date — the
   list is only as useful as it is current, and only
   `tests/test_train_policy_export.py` is confirmed across sessions so far
   (now a 3rd-occurrence citation as of 2026-08-11). Two more entries
   confirmed 2026-08-12/13: `tests/test_factory_ui_server.py::
   test_decision_write_atomic_under_concurrent_scheduler_read` and
   `tests/test_factory_tournament_ledger.py::
   test_adversarial_two_process_concurrency` — both failed on a full-suite
   run executed concurrently with review probes and passed immediately in
   isolation with no code change.

4. **A load-sensitive test can have MULTIPLE independent flake shapes —
   record each shape, not just the test name.** `tests/test_factory_tournament_ledger.py::
   test_adversarial_two_process_concurrency` (already on this list per #3
   above) showed a THIRD, distinct failure shape on 2026-08-13/14
   (census-screening-regime): a transient `PermissionError` on pytest-temp
   lockfile creation — an AV-scanner/temp-directory race, not the
   timing-assertion failure the prior two entries were. Passed cleanly on
   an immediate isolated re-run with zero code change. Do not assume a new
   failure signature on an already-listed test is the same known flake;
   confirm it re-passes isolated before treating it as "the usual" —
   naming the specific shape (not just the test) is what lets a future
   session tell a genuinely new bug apart from a known flake wearing a
   different error message.

## Legacy: The Verbatim Directive (Kept for Reference, Superseded)

For historical reference only — **do not use this on future dispatches**; use
the restructured shape above instead:

> **NEVER use the Monitor tool. NEVER run pytest (or any test/suite command)
> with `run_in_background`. Run the test command in the FOREGROUND and wait
> synchronously for it to finish before reporting back — do not end your
> turn while the run is still in progress.**

This wording was the 2nd escalation applied mid-session 2026-07-24 and held
through the rest of that slice, but failed a 3rd time in 2026-08-01 when an
implementer reached for Monitor despite the strengthened instruction. The
3rd failure triggered the rule's escalation clause: restructure the dispatch
rather than re-word it.
