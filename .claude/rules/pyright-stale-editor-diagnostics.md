# `ptcg.factory.*` import-resolution Pyright diagnostics are a known editor-snapshot artifact — adjudicate once per session, then stop re-adjudicating per task

`pyrightconfig.json` at the repo root (`{"extraPaths": ["src", "."], "exclude": [...]}`)
already correctly points Pyright at the `src/` layout. A fresh CLI run against any
committed file in `src/ptcg/factory/` is clean:

```
$ uv run pyright src/ptcg/factory/loop_state.py
0 errors, 0 warnings, 0 informations
```

Verified fresh on 2026-07-29 during Review & Apply for the
generational-champion-tournament session — this is not a config gap to fix,
it is confirmation the config is already right.

**Datapoint.** During Phase 2 (T11–T21, generational-champion-tournament),
harness-surfaced Pyright diagnostics on `ptcg.factory.*` imports ("unknown
import symbol" / "could not be resolved") recurred at T11, T12, T15, T16,
and T19 reviews. Every occurrence that was investigated resolved to the
existing global CLAUDE.md "harness-delivered diagnostics can be mid-edit
snapshots" pattern — a fresh `uv run pyright <file>` (or `git show HEAD:<path>`
+ runtime parse) at the committed state showed zero errors every time.
T12's `18 pyright false signals` cascaded from a single stale annotation
snapshot, not 18 independent problems. Each occurrence still cost a full
reviewer adjudication pass (re-derive that it's stale, produce a fresh-run
receipt, report `stale-diagnostic`) even though the class was already known
from earlier in the same session.

**Rule.** The general "verify before acting on a harness diagnostic" rule
(global CLAUDE.md → Diagnostic Ordering) still applies and is not weakened
here — but for THIS specific, narrow, repeatedly-confirmed class in THIS
repo, do not re-run the full investigation from scratch every time:

1. The first time in a session that a reviewer or implementer hits an
   "unknown import symbol" / "could not be resolved" Pyright diagnostic on
   a `ptcg.factory.*` (or any `src/`-rooted `ptcg.*`) import, confirm it
   with one fresh `uv run pyright <file>` receipt and report
   `stale-diagnostic: known ptcg.factory.* import-resolution class, see
   .claude/rules/pyright-stale-editor-diagnostics.md`.
2. Every SUBSEQUENT occurrence of the *same class* (import-resolution only
   — NOT other diagnostic kinds like `reportArgumentType`,
   `reportOptionalSubscript`, dict-splat typing, or undefined-variable
   hits, which have separately been REAL bugs in this repo's history, e.g.
   T4's `evaluate.py:80-81` dict-splat and the compute-saturation
   None-rating `sorted()` crash) in the same session may cite this file
   directly instead of re-running the investigation, UNLESS the diagnostic
   is on a file that was never checked before or the message shape differs.
3. Non-import-resolution diagnostics (type errors, undefined names,
   argument-type mismatches) always get the full stale-vs-real triage per
   the global CLAUDE.md rule — this file narrows the fast-path to the
   specific import-resolution artifact only.

**Triage clause 3 in two moves, not one — "real" and "mine" are different
questions.** A fresh `uv run pyright <file>` answers *is this error real?*
It does NOT answer *did this slice cause it?* — and only the second question
decides whether the slice must act. Once a diagnostic survives the fresh run,
immediately check pre-existence against the branch base before doing any
analysis of the error itself:

```bash
git show <base-commit>:<path> > /tmp/base.py && uv run pyright /tmp/base.py
# or, when the annotation is localized:
git show <base-commit>:<path> | grep -n "<the symbol pyright names>"
```

If the same errors are present at base, the finding is **inherited debt**:
record it as accepted debt in plan.md with the base-commit citation and move
on — do not let it consume a fix round in a slice that did not introduce it.
**Datapoint (2026-08-05, counted-pair-protection):** a persistent
`reportArgumentType` batch on `tests/test_factory_subscheduler.py` survived
the fresh-pyright run as **71 real errors** — and the base comparison showed
every one inherited from master (`_seed_founding -> object`), with the suite
pytest-green throughout. Two commands turned an apparent 71-error blocker
into a one-line deferral with a citation.

## Re-confirmed (2026-08-04, tournament-breeding-anchor-pressure)

The adjudicate-once-per-class fast path worked as designed across ~8
diagnostic batches in one slice: the import-resolution noise was dismissed
by citation after the first fresh-`pyright` receipt, and **2 real findings
were correctly separated out** and triaged in full per clause 3. Both halves
of the rule earned their keep — the fast path saved ~7 redundant
investigations, and the carve-out for non-import-resolution diagnostics kept
it from laundering real bugs as known noise. Settled; no wording change
needed.

## Re-confirmed at higher volume (2026-08-05/07, ui-remove-any-deck)

The stale-import-resolution class fired **10+ times across one slice** —
the highest volume observed to date, and the fast path absorbed all of it:
every occurrence after the first was dismissed by citing this file, zero
wasted round-trips, zero real findings laundered as known noise. Recorded
for the volume datapoint only. Note the implication for effort budgeting:
this class is now frequent enough that an orchestrator should expect it
roughly once per task on `ptcg.factory.*`-touching slices and should NOT
read a burst of them as a signal that something is genuinely wrong with
the branch's typing — the fast path is the whole response.

## Generalized to a second tool class: an automated commit-hook SECURITY scanner produces the same false-positive shape, for a structural reason worth naming (2026-08-10, pool-pruning-ui-improvements)

The triage discipline above (fresh run at HEAD -> is it real? -> base
comparison -> is it mine?) was written for Pyright, but this repo now has a
second automated-diagnostic source that fires mid-slice: the commit-hook
security scanner. Its dominant false-positive class has a specific,
predictable cause — **the scanner reads the source site in isolation and
cannot see the escape sink when that sink lives in a different module.**
A handler that interpolates a value into a message string looks like an XSS
source; the fact that every message goes through an HTML-escaping renderer
two modules away is invisible to it.

**Datapoint.** The flash-message path (`ui_actions` producing a message,
`ui_pages` escaping it at render) was flagged mid-slice as an XSS
vulnerability. It was resolved the right way — a **live probe** against the
running UI server confirming the rendered bytes were escaped — not by
arguing from the code's structure. That probe is the receipt; a prose
"but it's escaped downstream" claim is not.

**Fast path for this class (mirrors clauses 1-3 above):**
1. First occurrence in a session: confirm with a real probe of the OUTPUT
   (curl/GET the rendered page and inspect the actual bytes, or a test
   asserting the escaped form), not by reading the escape function.
   Report `scanner-false-positive: source-side-only, escape sink in
   <module>` and cite this file.
2. Subsequent flags of the SAME source->sink pair in the same session may
   cite the first probe rather than re-probing.
3. A flag on a source whose sink you have NOT probed — or any flag that
   is not source-vs-sink shaped (injected SQL, a real unescaped write, a
   secret in a diff) — gets the full triage. Do not let the fast path
   launder a real finding, same carve-out as clause 3 above.

Structural prevention where practical: keep the escape at a single
choke point and note it in a comment at the flagged source site, so the
next scanner run (and the next reader) has the sink named locally.

## Volume note extends beyond factory-touching slices (2026-08-10, pool-pruning-ui-improvements)

The stale import-resolution class fired 10+ times again on a slice whose
touched files were mostly UI (`ui_pages.py`, `ui_server.py`,
`deck_quality.py`) rather than `ptcg.factory.*` core. The fast path
absorbed all of it, zero wasted round-trips. Budget for roughly one
occurrence per task on ANY `src/`-rooted `ptcg.*` slice — not just factory
ones — and continue to read a burst as noise, not as a typing problem with
the branch.
