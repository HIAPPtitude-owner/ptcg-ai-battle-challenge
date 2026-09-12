---
paths:
  - "**"
---

# Always-on workers sharing a mutable ledger need an interleaved-mutation test, not just single-actor tests

Any always-on worker process that reads, transforms, and writes back a
shared mutable file (a JSON ledger, a candidate registry, a ratings store)
concurrently with OTHER processes that also write to that same file needs
a test that specifically injects a concurrent mutation into the worker's
slow phase — not just a test that runs the worker alone against a fixture.
Single-actor tests (worker runs, nothing else touches the file, assert the
resulting file looks right) pass cleanly even when the worker's save logic
has a stale-full-row clobber bug, because nothing else ever writes to the
file during the test.

**Datapoint (compute-saturation slice, 2026-07-20):** the new matrix worker
(`ptcg-factory-matrix` — a continuous round-robin tournament runner writing
Bradley-Terry ratings back to `experiments/factory/candidates.json`) shipped
a Critical bug: its `merge_save` loaded the full candidate row at the START
of a tournament block (before `play_block` ran), then wrote that
now-stale full row back at the END of the block — silently reverting any
field another concurrent process (the watch loop's submit/harvest path)
had written to that same candidate in the meantime, e.g. clobbering a
freshly-written `SUBMITTED` status back to a stale `QUEUED`. This is the
same TOCTOU class as the 5/day submission-cap race fixed in commit
`178043b` (documented in the global CLAUDE.md Review Layering section as
the "invariant-without-adversarial-test" lesson) — but it recurred in a
brand-new worker because that lesson's mitigation (an adversarial
multi-actor TEST authored in the plan) wasn't yet a standing checklist item
for worker-style code, only for explicit concurrency invariants named in a
spec. Caught by per-task review, not by any automated test; fixed
`6009b4d` with a RED receipt (a test that reproduces "QUEUED clobbers
SUBMITTED" against the buggy code, then goes green against the fix). The
reviewer then had to manually trace the sibling trainer worker's
`run_daemon` by inspection to confirm it was SAFE (fresh-load
immediately before training + insert-only writes, no read-modify-write
window) — inspection worked here, but only because the trainer's write
pattern happened to be simple; do not rely on manual inspection as the
standing mitigation.

## Rule

For any new always-on/continuous worker (not just ones with an explicitly
named concurrency invariant in the spec) that shares a mutable file with
other processes:

1. **Identify every read-modify-write window** in the worker's save path —
   any place it loads a row/record, holds it across an operation that
   takes real wall-clock time (a game, a training run, an API call), then
   writes the row back.
2. **Write an interleaved-mutation test**: start the worker's slow
   operation, inject a write to the SAME record from a simulated concurrent
   actor partway through (directly mutate the on-disk file, or call the
   other process's save function), let the worker's save proceed, then
   assert the concurrent actor's write survived (field-by-field merge, or
   an optimistic-lock/version-check rejection) rather than being silently
   reverted.
3. This generalizes the existing "invariant-without-adversarial-test"
   Review Layering lesson (global CLAUDE.md) from "explicit spec-named
   invariants only" to "any always-on worker touching a shared mutable
   file" — treat it as a standing per-worker checklist item, not something
   that only applies when a spec happens to call out a concurrency
   guarantee by name.

## Re-confirmed as a standing checklist item (2026-07-21, evolutionary-agent-population)

The interleaved-mutation test, applied as a per-worker checklist item from
plan-time (not discovered at review), earned its keep across four tasks in
one slice: T1/T5/T7/T11 each shipped a concurrent-mutation test with real
fail-power receipts (a RED against the pre-fix code, GREEN against the fix)
for the evolution/breeding writes to `agent_pool.json`/`deck_pool.json` and
the matrix worker's ratings write-back. This is the intended lifecycle —
the rule created after the compute-saturation clobber bug (caught only at
review) was applied proactively the very next slice, moving the catch from
review-time to plan-time. Settled; the checklist item works, no wording
change needed.

## Recurrence #3 — scope broadened to step functions, not just long-lived workers (2026-07-24, generational-champion-tournament Phase 2, `toctou-guard-in-step-functions`)

The same underlying defect class (a stale-read-then-write race on a shared
mutable store) recurred a third time in this repo, in a NEW shape this rule
didn't originally name: not a long-lived worker's ledger write-back, but a
short-lived **status-guard + bulk-action step function** — `loop.py`'s
`enqueue_match_games` (and its sibling `enqueue_confirm_series`) read a
row's status, decided whether to act based on that stale read, then
performed a bulk action without the read-decide-act sequence being atomic.
T13's review caught a non-atomic TOCTOU here — the same vulnerability class
as the `178043b` submission-cap race and the `6009b4d` matrix-worker
clobber — even though at the time no concurrent caller existed yet (the
T20/T21 scheduler that would make the race real hadn't landed). Captured
mid-flight as `LESSON: toctou-guard-in-step-functions` and injected into
every remaining loop/scheduler dispatch (T14–T18, T20); fixed same-day
(`5f4bd35`, both enqueue functions converted to single `BEGIN IMMEDIATE`
transactions, RED→GREEN receipts: `[75,75]/[50,50]` pre-fix →
`[0,75]/[0,50]` post-fix). Zero further TOCTOU findings across T15–T20 —
the forward-injection held for the rest of the slice.

**Generalized rule, broadened from "always-on workers" to any step
function:** any function that (1) reads a status/count/flag from a shared
store, (2) makes a decision based on that read, and (3) performs a bulk or
consequential action on that decision — is a concurrency surface requiring
either a single-transaction `BEGIN IMMEDIATE` around the whole
read-decide-act sequence, or a CAS reserve-then-fulfill pattern, **even when
no concurrent caller exists yet**. Do not wait for a future task to
introduce the concurrent caller before hardening the guard — by the time
that caller exists, the vulnerable step function is usually already
load-bearing and harder to touch safely. Apply this checklist item at
plan-time for any new status-guard step function in `ptcg/factory/`
(`loop.py`, `loop_state.py`, `subscheduler.py`, `loop_scheduler.py`), not
only to worker-style continuous processes.

## Recurrence #4 — `tautological-concurrency-receipt`: a race test whose lost-race branch equals the winner's asserts nothing (2026-08-04, tournament-breeding-anchor-pressure)

A race/concurrency test is only a receipt if its two branches (winner vs.
loser of the race) are DISTINGUISHABLE in the assertion. A test that starts
two concurrent actors, lets one "lose," and then asserts a value that is
IDENTICAL whether the actor won or lost the race proves nothing — it will
pass against both the correct atomic code and a broken non-atomic version.
This recurred TWICE in one slice: once during the T7 fix round, and again
in the tautological-concurrency-receipt Pass-2 Critical (a Kaggle-gate race
test was missing its barrier+counter entirely, so it couldn't even detect
overlap, let alone a wrong outcome).

**The correct receipt pattern, confirmed working this session:**
1. A **barrier** (e.g. `threading.Event` or an in-process lock release
   gated on both actors reaching a checkpoint) that forces the two actors'
   critical sections to genuinely overlap in wall-clock time — without
   this, "concurrent" tests often run serially by accident and never
   exercise the race at all.
2. A **sqlite trace-callback statement counter** (`sqlite3.Connection.
   set_trace_callback`) asserting the mutating statement executed **exactly
   once** across both actors — this is what actually distinguishes atomic
   (one winner, one no-op/rejected) from non-atomic (both actors' writes
   land, silently doubling or corrupting the effect).
3. An explicit **overlap assertion** — confirm both actors' critical
   sections were actually in-flight simultaneously (not that the test code
   merely spawned two threads), so a serialized-by-accident "race" test
   can't silently pass as if it exercised the real race.
4. A **RED run against a de-transactionalized version of the code** (revert
   the `BEGIN IMMEDIATE`/lock, rerun the same test) — if the test doesn't go
   red on broken code, it isn't testing the invariant, no matter how
   plausible it looks.

The reusable harness for this pattern in this repo is `tests/fixtures/race.py`
— cite it directly in any future concurrency-test dispatch rather than
re-deriving the barrier+counter+overlap shape from scratch.

### Related, distinct caution — a race-test docstring can overclaim determinism even when the assertion itself is sound (2026-08-05, counted-pair-protection, 1 datapoint)

Not the same bug as the tautological receipt above (which asserts nothing
regardless of outcome). Here the T4 fix round shipped a race test whose
assertion WAS a real receipt, but whose docstring claimed the RED
reproduction was deterministic when it was actually probabilistic — a
timing-dependent race that fails to reproduce on some fraction of runs.
A future reader trusting the docstring could mistake an occasional benign
green run for proof the invariant always holds, rather than recognizing
normal timing variance. Rule of thumb: any race-test docstring must state
its own reliability honestly — "reproduces the race on N/M runs" or
"probabilistic, not guaranteed on every invocation" — rather than claiming
determinism a wall-clock race can't deliver. Noted at 1 datapoint; promote
to a standing checklist item on a 2nd occurrence.

## `empirical-check-against-wrong-regime`: a "confirm empirically" instruction can be satisfied against STALE data instead of the regime the change itself creates (2026-08-04, tournament-breeding-anchor-pressure)

When a spec says "confirm this bar/threshold empirically," the confirmation
must run against the NEW regime the change is about to create — not
historical data collected under the OLD (about-to-be-replaced) regime. In
this slice, the design spec set a 0.40 WR/50-game early floor and cited
"confirmed empirically" — but the confirmation reused historical
culled-lineage win rates (0.03-0.10, i.e. exactly the junk-champion data
this slice exists to fix) instead of measuring the RESEEDED pool the floor
would actually gate against. Pass 2 caught it as a Critical; the real
fix was a fresh 1100-game in-process measurement against the reseeded pool
(pooled wr=0.471, run in ~24 seconds — see the cheap-in-process-measurement
note in project CLAUDE.md), which recalibrated the floor to a bar the new
pool could plausibly clear.

**Rule:** before accepting an "empirically confirmed" bar/threshold in a
plan or spec, ask "confirmed against WHAT data — the regime that exists
today, or the regime this change is about to create?" If the answer is
"today's/historical," the confirmation is invalid for a change that alters
the underlying population/process being measured; require a fresh
measurement against the new regime instead. This is a specialization of the
global CLAUDE.md "ground truth must be real runtime data, never a stale
fixture" family of lessons, applied to calibration/threshold-setting
specifically rather than to arithmetic verification.

## `access-path-analysis`: any new query against a shared table needs EXPLAIN QUERY PLAN + a production-scale-N lock-budget probe before review sign-off (2026-08-05, ui-remove-any-deck)

A new read OR write path against a table that other workers concurrently
lock (this repo's `tournament.db`) can be an unindexed full-table-scan
hiding inside a lock-holding transaction — a correctness-passing,
toy-N-passing bug that is invisible to every unit test because unit-test
tables are small enough that a full scan is imperceptibly fast. This is the
concurrency-analog of the global CLAUDE.md "scale-blind plan code" lesson
(an O(n²) metric that OOMs only at production N), specialized to lock HOLD
TIME rather than memory: the query doesn't crash, it just holds the write
lock long enough to starve every other worker on the DB.

**Datapoint.** `ui_actions._restore_target`'s per-concept `SELECT ... FROM
decisions WHERE concept_id=?` ran once per row inside `apply_bulk_decision`'s
single `BEGIN IMMEDIATE` transaction, unindexed. Measured (solo, no
contention) **61.13s at N=20,000 vs 1.19s with `ix_decisions_concept`** —
with a real concurrent writer contending for the lock, the writer hits
`sqlite3.OperationalError: database is locked` once the 30s `busy_timeout`
expires. The live culled pool was 95,895 concepts at review time, so a
real bulk-restore over any meaningful slice of it would have wedged the
runner/scheduler workers. Caught only at Pass 2 — despite an
`ix_decks_concept` precedent for the exact same failure shape sitting 9
lines above the new `decisions` table in the same DDL block, which the
pre-lock landmark grep did not connect to the new query being added.

**Rule:** for any task adding a new query (read or write) against a table
already written to by an always-on worker, the reviewer (per-task or
whole-branch) must, before sign-off:
1. Run `EXPLAIN QUERY PLAN` (or the DB's equivalent) on the new query
   against the REAL table — not a small test fixture — and confirm it uses
   an index, not `SCAN`.
2. If the query runs inside a lock-holding transaction (SQLite
   `BEGIN IMMEDIATE`, or an equivalent explicit-lock pattern), time it at a
   PRODUCTION-SCALE row count (the real table's current size, or the
   plausible near-future size), not the toy N a unit test seeds. State the
   measured time against the shared `busy_timeout`/lock-budget the other
   workers rely on.
3. When a new DDL statement is added to a table with a known existing index
   precedent elsewhere in the same file (e.g. this repo's
   `ix_decks_concept`), treat that precedent as a checklist prompt: "does
   the new table/column combination need the same index?" — don't rely on
   the pre-lock landmark grep alone to surface it, since that grep checks
   named landmarks, not query-shape parallels.

This generalizes the "always-on worker + shared mutable file" concurrency
checklist above from race/clobber conditions to lock-DURATION conditions —
both are TOCTOU-adjacent failure classes on the same shared-state surface,
but access-path-analysis catches a class (unindexed scan inside a lock)
that an interleaved-mutation test alone does not exercise.

## Run this checklist against the PLAN's own code sketches, not only against implemented code (2026-08-05, ui-remove-any-deck T1)

Every clause above is written as a review-time or implementation-time
check. But a plan's illustrative code sketch is transcribed near-verbatim
by implementers (global CLAUDE.md: "Plan code is reference, not gospel" —
but it is followed closely all the same), so a race encoded in the SKETCH
propagates untouched through plan-lock into the first commit. This is the
concurrency twin of the already-documented
`.claude/rules/plan-test-arithmetic-sanity.md` class (a plan can encode
self-contradictory arithmetic); here the plan encodes a non-atomic
read-decide-act sequence.

**Datapoint.** The plan's `migrate_decisions` sketch performed its
`sqlite_master` "is this table already v2?" detection OUTSIDE the
`BEGIN IMMEDIATE` write transaction — textbook read-decide-act across a
lock boundary, so a concurrent loser would rebuild the winner's
already-migrated table and silently drop `concept_id`/`prior_status` via
the 5-column copy `SELECT`. Caught at T1's per-task review and fixed
(`e636d8c`, RED-then-GREEN receipt: `alter_count=2` pre-fix) — the
standing review directives worked, but one round-trip later than
necessary, on a defect that was legible in the plan text itself.

**Rule — add to plan-write, alongside the pre-lock landmark grep:** for
every plan-authored code sketch that touches `tournament.db`, a ledger, or
any other shared mutable store, walk clauses 1-3 of this file against the
SKETCH before the plan is locked. Concretely: does the sketch read a
status/count/existence flag, decide on it, then write — and is that whole
sequence inside one `BEGIN IMMEDIATE`/lock? Does any query it introduces
need an index (the access-path clause above)? Fixing the sketch costs one
plan edit; fixing it after transcription costs an implementer round-trip
plus a fix commit plus a receipt.

## Recurrence #5 — the sharpest access-path-analysis datapoint yet: an unindexed aggregate inside `BEGIN IMMEDIATE` starved an entire production system for 30+ hours (2026-08-08/10, factory-db-lock-contention)

The `ix_decks_concept`/`ix_decisions_concept` datapoints above (2026-08-05)
were caught at REVIEW time, before the unindexed query ever ran at
production scale against real concurrent load. This session is the
access-path-analysis clause's failure-to-catch-it-in-time case: the same
failure shape (unindexed aggregate query inside a `BEGIN IMMEDIATE`
transaction on a shared table) shipped, ran in production for roughly 36
hours at ~every-10-second cadence, and produced 1,210 real runner lock
errors before being diagnosed and fixed.

**Datapoint.** `loop.py`'s `enqueue_crown_round_robin` ran ONE
`BEGIN IMMEDIATE` transaction holding `tournament.db`'s write lock for a
**56.041s median** (measured 3x against a backup copy of the live DB): a
per-pair `SELECT COUNT(*)` looped C(35,2)=595 times as the CROWN survivor
pool grew, each iteration a full unindexed `SCAN` over 190,734 rows
(~94ms/scan). `busy_timeout=30s` sat well under the 56s hold, so SQLite's
non-fair busy-wait killed runner pools 4-at-a-time on every tick. This
predates the `ix_decks_concept`/`ix_decisions_concept` review-time catches
by less than a week — the CROWN query was pre-existing code the 2026-08-05
rule was written FROM, not code that could have cited the rule, so this is
not a rule-failure so much as the datapoint that motivated the rule in the
first place, confirmed at real-production scale.

**Fix, and the standing mitigation this session adds:** a covering index
(`ix_games_crown_pair`) plus a single-GROUP-BY query rewrite collapsed the
56.041s hold to sub-second (steady-state 0.239s, ~330x). Beyond the
one-time fix, this session shipped the standing mitigation the
access-path-analysis clause implies but had not yet been operationalized
as a REGRESSION TEST: `tests/test_factory_lock_access_paths.py`
(`1b5976e`) runs `EXPLAIN QUERY PLAN` (clause 1) against **every**
lock-held query on a large table in the codebase, not just the one that
just broke — turning a one-off review checklist item into a standing CI
guard. Accepted residual debt from Pass 2 review: the guard tests embed
the production SQL as a literal string rather than importing it from the
production module, so a future query edit could drift out of sync with its
own guard test without either side noticing — worth closing if this class
recurs a 6th time.

**Reinforced rule:** clause 1-2 of this file ("run EXPLAIN QUERY PLAN
against the real table, time it at production-scale N") should not be a
review-time-only checklist — for any codebase with more than one or two
lock-held queries on a shared table, write the EQP-guard sweep as an
actual test file once, covering every such query, rather than relying on
each new query getting individually caught at its own review.

## Re-confirmed — a plan-sketched race test can be self-defeating, not just untested (2026-08-10, pool-pruning-ui-improvements T11)

The "run this checklist against the plan's own sketches" clause above was
written for a TOCTOU-shaped defect (a migration's read-decide-act sequence
outside its transaction). It generalizes to a second failure shape inside
the same clause's scope: a plan-sketched CONCURRENCY RECEIPT TEST can itself
be non-atomic in a way that defeats its own purpose, not just fail to
protect production code.

**Datapoint.** T11's plan sketch for the `ThreadingHTTPServer` overlap-proof
test held a shared lock across a `threading.Event.wait()` call inside the
test's mock connection factory. Holding the lock across the wait would have
serialized every subsequent concurrent call behind the first one — exactly
the concurrency the test exists to prove is ABSENT under the fix. The test
would have passed whether the production code was correctly threaded or
not, identical in shape to the tautological-concurrency-receipt failure mode
documented above (Recurrence #4), except the tautology originates in the
plan's own sketch rather than in an implementer's later transcription. The
implementer caught and fixed it before committing (the shipped
`test_factory_ui_threading.py` explicitly comments on the trap: "must guard
ONLY the check-and-set... holding it across release_slow.wait() would
serialize every subsequent conn_factory() call (defeating the concurrency
receipt this test exists to make)").

**Rule, extended:** when applying the plan-sketch checklist to a
CONCURRENCY-RECEIPT test specifically (not just to production code sketched
in a plan), check for the tautological-receipt failure mode (Recurrence #4
above) as its own line item: does any lock/mutex in the test's harness span
the barrier-wait it is supposed to be proving happens concurrently? If so,
the sketch will produce a test that cannot fail regardless of whether the
fix works. Caught pre-commit here; the mitigation is the same
barrier+counter+overlap discipline this file already prescribes, applied to
the plan's own test code before it is transcribed, not just to the
production code the plan describes.

## Recurrence — `deferred-minor-can-be-load-bearing` confirmed a 2nd time, in a NEW shape: DLL/native-buffer concurrency, not a ledger file (2026-08-10, pool-pruning-ui-improvements T11/whole-branch review)

The `.claude/rules/plan-test-arithmetic-sanity.md` family and this file's
own TOCTOU/access-path clauses have so far covered shared MUTABLE STORAGE
(a ledger, a SQLite table). This session's finding is the same underlying
lesson — a per-task reviewer's severity estimate for a concurrency exposure
cannot be trusted without an executable probe — applied to a THIRD kind of
shared resource: a **native DLL's internal buffer**, not a file or a
database row.

**Datapoint.** T11's per-task reviewer graded the `lru_cache` cold-start
exposure in `deck_quality._card_db()`/`_attack_db()` as a deferred Minor:
two threads racing a cold cache miss would both execute the cache-miss body,
which seemed at worst wasteful (duplicate work), not unsafe. The
whole-branch reviewer ran an 8-thread barrier probe directly against the
raw `cg.api.all_card_data()`/`all_attack()` calls and reproduced real
corruption — 6x `JSONDecodeError` plus inconsistent card counts (`[1267,
1556]`) — because the engine DLL's `AllCard()`/`AllAttack()` share a single
native read buffer across calls, a fact invisible from reading the Python
call site. Reclassified Minor -> Critical, fixed `b834ecf` with
`deck_quality._DLL_LOCK` serializing every DLL entry point plus a
single-threaded cache warm-up in `run_server` before `serve_forever()`.

**Rule, reinforced (no wording change to the original clause needed — this
is confirmation the rule generalizes beyond the file/DB shape it was first
written for):** ANY deferred-minor verdict that names a concurrency exposure
on a resource shared across threads/processes — a file, a DB row, OR a
native library's internal state/buffer — requires an executable probe
(a real barrier-style multi-thread/multi-process test against the ACTUAL
shared resource, not reasoning about the Python-level call site) before the
severity is accepted as Minor. "It's just wasted duplicate work" is exactly
the kind of plausible-sounding severity guess this rule exists to catch —
the guess was wrong specifically because the corruption happens beneath the
Python abstraction, in memory the reviewer's static read couldn't see.

## `regime-shift-invalidates-existing-gate`: a change that introduces a NEW input class can leave an existing pass/fail gate unable to discriminate against it (2026-08-12, freeze-curation-legacy-identities)

The `empirical-check-against-wrong-regime` lesson above is about a
THRESHOLD calibrated against stale data. This is the sibling failure on a
different axis: an existing **pass/fail gate** (not a threshold) that was
correct for its original input population can silently stop
discriminating once a change feeds it a NEW class of input it was never
designed to reject.

**Datapoint.** `curate_counted_pair.py`'s confirmation gate verified "did
the upload actually happen" by matching the newly-uploaded submission's ID
against Kaggle's recent-submissions list. That worked fine when every
curated identity was tournament-DB-tracked (IDs generated fresh each run).
Extending the script to resolve **legacy** `candidates.json` identities
introduced a new input class — legacy submission refs whose ID shape
collided with historical rows from a 2026-07-22 upload. A zero-upload
dry-run then matched one of those historical rows by prefix, read as "yes,
the upload happened," and exited 0 having written `SUBMIT_HOLD` without
ever uploading anything. Caught at Pass 2, not before — the pre-lock
landmark grep confirmed the gate's CODE was unchanged and correct for its
original inputs, but never asked whether the gate could still fail against
the NEW inputs the slice was about to feed it.

**Rule:** when a plan changes what KIND of input flows into an existing
pass/fail gate (not just what the gate's own code does), add an explicit
plan-time question: "can this gate still FAIL for the new input class, or
does it silently default to a pass state it was never built to
discriminate against?" A landmark grep proves the gate's code is intact;
it does not prove the gate still has fail-power over the widened input
domain. Fix pattern that worked here: a pre-action snapshot of the
external system's state + a strict "only a genuinely fresh row counts"
confirmation, rather than a substring/prefix match against the full
history.
the kind of plausible-sounding severity guess this rule exists to catch —
the guess was wrong specifically because the corruption happens beneath the
Python abstraction, in memory the reviewer's static read couldn't see.

## `fix-wave-introduced-defect`: a scoped re-review must recompute any NEW number a fix adds, not just re-verdict the original findings (2026-08-13, census-screening-regime)

Third member of the tautological-confirmation family (after
`regime-shift-invalidates-existing-gate` above and the original
`tautological-concurrency-receipt`), on a new axis: a **docs-only fix
wave**, not concurrency and not a gate's input domain, can itself
introduce a fresh, false numeric claim while correcting a different one.

**Datapoint.** Pass 1 (whole-branch, opus) flagged a false statistic in a
code comment and dispatched a docs-only fix. The fix wave corrected the
FLAGGED statistic — but in rewriting the surrounding prose, introduced a
DIFFERENT, new false statistic into the same comment (a "100%-fails-at-0.10"
claim that did not hold). This was not the original finding recurring; it
was a brand-new number, created by the fix itself, that nobody had reason
to check because the re-review's job (as usually scoped) is "confirm the
flagged issue is resolved," not "audit every number in the file again."
Pass 2's independent verification caught it — one gate later than
necessary.

**Rule:** when a fix wave is scoped to "address these N reviewer
findings," the re-review of that fix wave must not stop at re-verdicting
the N original findings. Any NEW factual/numeric claim the fix itself
introduces — a statistic in a corrected comment, a recalculated threshold,
a rewritten docstring number — needs the same executable-receipt or
independent-recompute standard as the finding it was created to fix (per
global CLAUDE.md's arithmetic-verification family). A "the flagged issue
is now correct" verdict says nothing about whether the fix's own new prose
introduced a fresh error next to it. Cheapest mitigation: when a scoped
fix-wave re-review passes, explicitly diff the touched lines (not just the
originally-flagged ones) for any number/statistic/claim that is NEW
relative to the pre-fix version, and verify each one independently before
signing off — the same discipline `.claude/rules/test-coverage-sweep.md`
already applies to dropped test assertions, extended here to added prose
claims.

## Plan-sketch checklist, DDL clause: a new-column index in the shared DDL list must be CONDITIONAL, because `init_db()` runs against the LIVE legacy DB on every firing (2026-08-13, census-screening-regime)

The plan-sketch checklist above ("run this checklist against the PLAN's own
code sketches") and the access-path-analysis clause ("does the new
table/column need an index?") together push a planner toward *adding* an
index alongside a new column. Both are right. Neither says anything about
the index statement being **conditional**, and on this repo that omission is
a production crasher rather than a style nit.

`deckdb._DDL_STATEMENTS` is executed by `deckdb.init_db()`, which
`scripts/factory_watch_once.py` calls on **every ~15-minute firing** against
the live `tournament.db` (see the counter-datapoint in
`.claude/rules/factory-resume-probe.md` — this path is live-on-write during
a slice, independent of any worker restart). An index over columns that do
not exist yet is not a no-op and is not saved by `IF NOT EXISTS` — that
guard covers the *index* name, not the *columns* it references. SQLite
raises on the missing column, `init_db()` raises, and every firing dies at
startup until someone runs the migration.

**Datapoint.** The plan sketched `ix_decks_concept_comp` over
`decks(concept_id, shell_variant, energy_count, pokemon_count)` as a plain
entry in `_DDL_STATEMENTS`, adjacent to the existing unconditional
`ix_decks_concept`. Against a virgin DB that is correct; against the live
production DB — which had neither column until the migration ran — it would
have crashed `init_db()` on the next watch-loop firing. Caught by the
pre-lock landmark grep (one of 5 drifts that grep caught this slice, and the
only one that was a live crasher). Shipped shape: a
`_decks_has_composition_columns(conn)` PRAGMA `table_info(decks)` probe
gating the index creation in `init_db()`, with
`scripts/migrate_composition_columns.py` creating it unconditionally at
go-live once the columns exist.

**Rule — add to the plan-sketch checklist:** any DDL statement a plan adds
to a shared, repeatedly-executed DDL list must be checked for *ordering
dependence on a migration*, not just for correctness on a virgin DB. Ask:
"does this statement reference a column/table that only exists AFTER a
migration that has not run yet on production?" If yes, it must be guarded by
a runtime existence probe (`PRAGMA table_info(<table>)`) rather than placed
unconditionally in the list — and the migration script owns the
unconditional creation. `IF NOT EXISTS` is not the guard you need here; it
protects against re-creation, not against referencing a column that isn't
there. Virgin-DB tests pass either way, which is precisely why this needs to
be a plan-time checklist item and not something the suite is expected to
catch.
