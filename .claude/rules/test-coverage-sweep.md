# Coverage you drop silently: sweep consumer test files at plan time, and diff replaced assertions at review time

Two distinct ways this repo has lost test coverage without anyone deciding
to lose it. Both are cheap to prevent and invisible to a green suite.

## 1. Plan time — sweep test files named after CONSUMERS, not just after the changed module

Global CLAUDE.md's constant/enum-bump lesson says to grep `tests/` for the
changed SCRIPT/FILE's name, not just the constant it emits. That framing is
scoped to "a bump/rework task changing a script's observable output."
**Generalize it: any change to a module's API surface must sweep the test
files of everything that CONSUMES that module — including script entry
points, whose test files are named after the SCRIPT, not after the module
that changed.**

A plan that lists "the tests for `census.py`" under-counts, because
`scripts/run_census.py` imports `census.py` and its test file is
`tests/test_run_census_smoke.py` — a name that contains neither the changed
module nor the changed symbol.

**Recipe at plan-write time**, alongside the pre-lock landmark grep:

```bash
# 1. who imports the module whose API is changing?
rg -n "from ptcg\.factory\.census import|import census" src/ scripts/ tests/
# 2. for each consuming script/module found, grep tests/ for ITS name too
rg -l "run_census" tests/
```

List every test file found in the task that changes the API, so the suite
stays green through that task rather than going red for N tasks until a
keystone cleans up.

**Datapoint (2026-08-03, tournament-breeding-anchor-pressure, T4).** The T4
census rework changed the census API; the plan named the direct consumer
tests but missed two sibling files —
`tests/test_factory_tournament_phase1.py` and
`tests/test_run_census_smoke.py`. Both broke transiently. Caught mid-slice
and folded into T9 (the keystone) as a named carry-forward, so no rework
was lost — but the suite carried known-red files across five tasks, and the
30-second consumer grep above would have put them in T4 where they belonged.

**Extension — sweep consumers-of-consumers one level when a threshold/flag
changes MEANING, not just when a module's API surface changes (2026-08-11,
min-basics-pool-rule, R8).** The recipe above catches direct importers of
the changed module. It under-counts when the change is a semantic shift in
a shared constant/threshold rather than a signature/API change: a module
two hops away can hard-code an assumption about the constant's OLD meaning
in its own tests, with a filename that names neither the changed module nor
the constant. Datapoint: `builder.py`'s `MIN_BASIC_POKEMON` was re-sourced
from `validate.MIN_BASIC_CARDS` (8, not the old informal minimum); the plan
swept `test_factory_builder.py` and `test_deck_quality.py` (direct
consumers) but missed `tests/test_factory_ui_server.py`, because
`ui_server` consumes `deck_quality` — a consumer of a consumer — and the
filename contains neither `builder`, `validate`, nor `MIN_BASIC`. Caught
only by the whole-branch full-suite run, one review layer later than
necessary. **Rule, extended:** when a change alters what a constant/flag
MEANS (not just its call signature), grep for importers of the CONSUMING
module too, one hop further out:

```bash
# 1. direct importers (existing recipe)
rg -n "from ptcg\.factory\.deck_quality import|import deck_quality" src/ scripts/ tests/
# 2. importers of THOSE importers (the new hop)
rg -n "from ptcg\.factory\.ui_server import|import ui_server" src/ scripts/ tests/
rg -l "ui_server" tests/
```

Do this one-hop consumers-of-consumers walk whenever the change is a
semantic/threshold shift rather than a pure rename/signature change — a
rename breaks the direct importer's import statement (loud, easy to find);
a meaning shift only breaks an assumption baked into a downstream test
(silent until the suite runs it).

## 2. Review time — a test REPLACEMENT must diff old assertions against new

When a task **replaces** an existing test (rewrites it, re-parametrizes it,
swaps a fixture for a better one) rather than adding a new one, the
implementer's own self-review reliably reports "tests updated, all green"
without noticing that the new test asserts *less* than the old one did. The
suite is green either way, so nothing surfaces the loss.

**Standing line for reviewer dispatch prompts when a task modifies existing
test files** (include verbatim):

> If this task REPLACES or rewrites an existing test rather than adding a
> new one, diff the old assertions against the new ones (`git show
> HEAD~1:<test-path>` vs the current file) and name explicitly what
> coverage was dropped. "All tests pass" is not evidence coverage was
> preserved — a weaker test passes too. Report any dropped assertion as a
> finding even if the replacement is otherwise an improvement.

**Datapoint (2026-08-04, tournament-breeding-anchor-pressure, T7).** The
task's own reviewer found **two Important test-gap findings** that the
implementer's self-review had missed: claimed-survival coverage was dropped
during a test replacement. The implementer was not being careless — the
replacement genuinely improved the test in other respects, and the suite
was green. Only an explicit old-vs-new assertion diff surfaces this class.

## 3. `behavior-change-needs-consumer-sweep` — the plan-time sweep applies to BEHAVIOR changes with a stable signature, not just API/signature changes

Section 1's recipe (grep importers, then grep consumers-of-consumers) was
written for API-surface changes: a renamed function, a changed argument, a
threshold whose meaning shifted. It generalizes one step further: a
function whose **signature never changes** but whose **output shape**
changes is just as capable of silently invalidating a downstream fixture,
and the plan-time grep-for-importers recipe still finds every candidate
fixture worth checking — the miss isn't in the recipe, it's in forgetting
to run it when nothing about the call site itself looks different.

**Datapoint (2026-08-13, unpayable-attack-pool-rule, T2).** `builder.py`'s
`build_deck()` kept its exact signature, but its output changed under a new
2-type energy cap plus payability-aware filler selection. A plan-cited
Abomasnow-pair fixture (an existing test asserting a specific deck
composition from `build_deck`) regressed under the new cap — caught at
per-task review, not before, with a fixture-only fix. Nothing about the
call site changed, so a reviewer scanning for renamed/re-signatured calls
would not have flagged it; only running the actual consumer test files
surfaced it.

**Rule, extended:** when a plan task changes a function's *behavior*
(output composition, selection policy, ordering) without changing its
signature, treat it exactly like an API change for the purposes of Section
1's sweep — grep for importers/consumers and run their existing tests
before considering the task done, even though nothing about the diff
"looks like" a breaking change from the call-site's perspective.
