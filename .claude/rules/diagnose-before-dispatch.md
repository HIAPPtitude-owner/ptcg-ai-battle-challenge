# Root-cause slices: diagnose with executable receipts before dispatching implementers

When a session's primary goal is to diagnose and fix a bug or system failure (a "root-cause slice"), spend time on **read-only diagnosis BEFORE dispatching any implementer** to make changes. Use three independent channels to build confidence in the root cause, then dispatch implementers with concrete specifications that cite verified facts, not guesses.

**Opposite of:** describing a vague symptom and asking an implementer to "investigate and fix."

**Same spirit as:** global CLAUDE.md "Errors are information, not obstacles" — understanding the root cause first is the asset; the fix is mechanical once the cause is known.

## Three-Channel Verification

Before dispatching, verify each suspected root cause via THREE independent channels:

1. **Read the artifact** (log, ledger, config, state file): `Grep` the actual watch log or ledger file for evidence the defect exists and manifests as claimed.

2. **Execute the failing path locally**: Run the crashing code snippet in isolation to reproduce the exact error message, or manually trace the logic path step-by-step against real data (not a fixture) to confirm the defect is reachable.

3. **Trace the logic path against real data**: If the defect is a state machine or multi-step interaction (a race condition, a read-modify-write sequence, a ledger clobber), manually walk through the logic sequence against the actual ledger/config/state to confirm the bug is **possible** and **observable** in the real data.

**Datapoint (2026-07-22, silent factory crash):** Three independent root causes were verified via this pattern: (1) read the `candidates.json` ledger and grepped for the specific crashing candidate's fields; (2) executed `f"{None:.3f}"` locally and got the exact TypeError; (3) traced the breeding loop's `pool_merge_save` logic against the real ledger to confirm how the seeded anchor row was replaced by a content-identical bred child.

## Verified-Landmark Table

Capture the three-channel verification in a **Verified Landmark Table** (citeable proof) in the plan preamble. Each landmark row should name:
- **Landmark** (what the claim is): e.g., "None-formatted crash", "Anchor row clobbered"
- **Location** (file:line or query): e.g., `submit.py:57`, or `experiments/factory/candidates.json`
- **Verified fact** (1-2 line result of verification): e.g., "f-string at :57 formats candidate.local_wr; candidate #XYZ has local_wr=None"

Example (from the 2026-07-22 plan):
```
| Crash repro | executed | `f"{None:.3f}"` → `TypeError: unsupported format string passed to NoneType.__format__` |
```

The table serves as a checklist: before dispatch, each row is independently verified via one of the three channels. On review, the table lets the reviewer cross-check the root cause against the landing fix.

## Dispatch Instructions: Cite the Landmark

When dispatching an implementer to fix a root cause, include verbatim in the prompt:
- The **verified landmark** (the artifact + verified fact)
- The specific **file:line** where the defect lives
- A **reproduction receipt** (the error message, the ledger state, the traced-out logic step) that proves the defect exists

Example: "The TypeError at `submit.py:57` is reproduced via `f'{None:.3f}'`; the crashing candidate has `local_wr=None` in `experiments/factory/candidates.json` row ID XYZ."

Do NOT say: "There's a crash, investigate it" or "Something in the submission logic is broken, fix it." These push diagnosis onto the implementer.

## When NOT to Use This Pattern

Skip the three-channel verification and go straight to implementer dispatch if:
- The bug is already understood, localized, and reproducible (e.g., a failing test with clear stack trace pointing to a specific line)
- The session is NOT primarily about diagnosis (e.g., a feature add, a planned refactor) and the bug happens to be discovered mid-work
- The root cause is already documented in a prior session or comment

Use this pattern specifically when **diagnosis is the critical unknown** and the session's job is to turn that unknown into precise, verifiable facts before work begins.

## Re-confirmed (2026-07-22)

All four fix tasks passed first-pass review with zero rework. Zero implementer re-dispatch cycles. The concrete, verified specifications enabled implementers to write fixes with confidence instead of burning cycles on exploratory changes.

This pattern works. Cost is ~1-2 hours of orchestrator time up front; savings is 0 dispatch/rework cycles on the fix tasks themselves.

## Extension — answer Brad's "explain how X works" from the CODE, not from docs or memory (2026-08-05)

A mid-scope question like *"explain the champion playoff and what we actually
submit"* is not a detour to be answered quickly from project CLAUDE.md, a
prior session's memory file, or an ANALYSIS doc — it is a **free diagnosis
opportunity on the exact machinery the slice is about to change**, and it
deserves the same executable-receipt standard as a root-cause investigation.
This repo's docs drift constantly (the factory pipeline has been redesigned
four times), so docs and memory are precisely the sources most likely to be
stale about the mechanism in question.

**Rule:** when Brad asks how a pipeline/mechanism works during scope or
design, build the answer from a fresh code read — an `Explore` dispatch or a
targeted Grep over the live modules — and cite file:line for each claim.
State explicitly where the code contradicts what the docs say.

**Datapoint (counted-pair-protection, 2026-08-05).** Answering the champion-
playoff question from the code (rather than from the CROWN description in
project CLAUDE.md) surfaced two things before a single line was written:
(1) **doc drift** — the documented Bo1001 grand final did not match the
shipped flat round-robin, fixed in `ccdf593`; and (2) a **corrected design
premise** — the counted-pair mechanic evicts by recency, so the framing had
to move from "protect the weakest" to "protect the evicted," which changed
what the slice built. The explanation detour materially improved the design;
answering it from docs would have propagated both errors into the spec.

## Extension — executable receipts earn their keep at REVIEW time too (2026-08-04)

This rule was written for the diagnosis phase (before dispatch). The
tournament-breeding-anchor-pressure slice proved the same discipline is the
deciding factor at the **review** layer: both the whole-branch reviewer and
the Pass-2 critic ran probes against **copies of the live tournament DB**
rather than fixtures, and each caught findings that static review provably
could not — a missing `decks(concept_id)` index whose 180s lock hold would
have deadlocked the runners at go-live, and two TOCTOU race tests whose
lost-race branch returned the winner's verdict (tautologies that could never
fail). Neither is visible by reading the diff.

So: when dispatching a whole-branch reviewer or Pass-2 critic on a slice
that touches live-state machinery (the tournament DB, the ledger, the
scheduler), instruct it to **run probes against a copy of the real state
file**, and to state findings as executable receipts (RED against the
pre-fix shape, GREEN against the fix) — not as prose arguments. Fixture-only
review inherits the fixture's blind spots at review time exactly as it does
at diagnosis time.

### Re-confirmed (2026-08-05, counted-pair-protection)

Held again one session later, on ordinary per-task review (not just
whole-branch/Pass-2). T4's reviewer independently reproduced a race
condition with 15 trials in each direction rather than trusting the
implementer's prose description of the fix, and T6's reviewer found a
superseded-pending-series gap in `enqueue_pair_gate` via a real-DB
claim-ordering probe — a defect invisible to a diff read. Opus-tier
reviewers earning their tier premium specifically on concurrency-shaped
code is now a 2-for-2 pattern across two consecutive sessions; keep
reserving opus for any review task touching shared mutable state
(ledgers, DBs, schedulers), not just the final gates.

### Re-confirmed a third time (2026-08-05, ui-remove-any-deck)

3-for-3 sessions now. T7's race-receipt task delivered an executable
barrier+counter+overlap receipt (not a prose claim) for the bulk-cull-vs-
census-promotion invariant, per `.claude/rules/single-actor-worker-tests.md`.
Both gates then independently caught findings a diff read would have
missed and backed them with receipts against a real/production-scale DB
copy: the whole-branch opus reviewer produced a concrete insta-promote
repro for the restore-mitigation gap (not just "this looks unsafe"), and
Pass 2 measured the O(N²) bulk-restore lock hold at production scale
(61.13s at N=20,000 vs 1.19s indexed) rather than reasoning about it
abstractly. Opus-tier review earning its premium on concurrency/shared-
state-touching code is now settled practice in this repo — stop treating
it as a hypothesis to re-verify and start treating any review task on
ledgers/DBs/schedulers as an automatic opus assignment.

### Re-confirmed a fourth time — and this time the receipts sized the FIX, not just the cause (2026-08-10, factory-db-lock-contention)

4-for-4 sessions. This was a pure root-cause slice (runner pool dying in a
`database is locked` loop, games/day collapsed 73,212 -> 1,698), and the
three-channel protocol ran end to end before a single implementer was
dispatched: code-read of `loop.py`'s CROWN enqueue, live forensics on the
runner/watch logs, and **timed probes against a backup copy of the live
`tournament.db`**. The Verified Landmark Table did two things a symptom-only
dispatch could not have:

1. **Named the cause exactly** — one `BEGIN IMMEDIATE` transaction holding
   the write lock a measured **56.041s** (median of 3 runs) because 595
   per-pair `SELECT COUNT(*)` calls each full-`SCAN`ned a 190,734-row table
   (EQP receipt), against a 30s `busy_timeout`. Two plausible-sounding
   hypotheses were REFUTED by the same probes (UI-restart onset, rating
   refresh at 0.136s wall / ~0s lock) — both would have cost a wasted fix
   round.
2. **Sized the remedy** — the same measurements produced the throughput math
   (19,477 pending crown games ~= 40 days at 4 workers, infeasible against
   the 2026-08-16 deadline) that drove the CROWN rescope decision (top-K=8,
   200 -> 100 games/pair). The diagnosis phase did not just say *what broke*;
   it supplied the numbers Brad's scope AskUserQuestion was decided on.

Outcome: 4 fix tasks, each through per-task review in <=1 fix round, Pass 1
APPROVED with 0 required changes, Pass 2 APPROVED. Post-fix receipt: steady-
state scheduler tick **0.168s vs 56.041s** (~330x), zero new locked errors in
a 30-minute survival window. Cost of the diagnosis phase: roughly half a day
of orchestrator time, entirely read-only.

**The generalizable half:** when a diagnosis probe is already running against
a copy of production state, harvest the *capacity* numbers (rows, rates,
projected completion) in the same pass as the *causal* ones. They cost
nothing extra to collect and they are what turns "here is the bug" into "here
is the bug and here is what the fix has to be scoped to" — which is the
difference between one AskUserQuestion and three.

### Corollary — review-probe dispatches that copy a large state file need an explicit cleanup instruction (2026-08-12, freeze-curation-and-unpayable-pool)

Every re-confirmation above involved a probe against **a copy of** the live
`tournament.db` (181MB and growing). That's the correct pattern — copies
avoid contending for the production lock — but nothing in this rule ever
told the probe dispatch to delete its copy afterward. During an unrelated
disk-full triage on 2026-08-12, ~10 leftover copies (≥50MB each) were found
sitting in session scratchpad directories from multiple prior review/probe
dispatches across several sessions. Disk hygiene was not the root cause of
that day's fill (the cause stayed unidentified), but it is a real, silent,
compounding cost of this rule's own recommended pattern.

**Rule:** any dispatch instructed to "run probes against a copy of the real
state file" (per the Extension above) must also be instructed to delete
that copy before returning — it's a one-line addition to the same dispatch
prompt. And any future disk-space triage on this project should check
session scratchpad directories for accumulated DB copies, not just the
obvious caches (`uv cache`, temp dirs) — they are a known, if unconfirmed,
contributor.

## Extension — plan reconciliation by commit-mapping needs an artifact check, not just a commit match (2026-08-24, strategy-report review)

A different flavor of the same "prose/inference is not a receipt" failure,
found not during diagnosis or review but during **plan.md reconciliation**:
when `.claude/plan.md` has gone stale against actual implementation
progress (tasks got done in a prior session but their checkboxes were never
updated), the cheap way to reconcile is to map each planned task to the git
commit(s) that plausibly implemented it and check the box. That mapping is
an *inference* — "a commit exists whose message/diff plausibly matches this
task" — not a *receipt* that the task's deliverable actually exists.

**Datapoint.** Reconciling the `strategy-report` plan this way over-credited
T10 ("fact-check review pass," whose deliverable was a named artifact — a
fact-check verdicts file). A commit existed that was plausibly related, so
the box got checked — but no such file was actually on disk; T10 had never
executed. Caught before dispatching Pass 1 (by chance, not by a standing
check), corrected to `[ ]`, and Pass 1 later confirmed the same gap
independently as a BLOCKER finding.

**Rule:** when reconciling a stale plan.md against git history, for any
task whose deliverable is a **named artifact** (a file, a generated report,
a migration receipt, a script output) — not just "code that implements
behavior X" — verify the artifact's existence directly
(`git ls-files -- <path>`, or `Read`/`git show HEAD:<path>` if the exact
path isn't yet known) before checking the box. A commit-message/diff match
proves work was ATTEMPTED in that area; it does not prove the specific
named deliverable landed. This is the plan-reconciliation instance of this
file's core discipline — an inference about what "probably happened" is not
the same as reading the artifact and confirming it did.

### Same reconciliation, second axis — commit-mapping also UNDER-credits, so account for every commit in the range, not one per task

The Extension above catches the over-credit direction (a box checked with no
artifact behind it). The same mapping fails in the opposite direction for the
same structural reason: it is a *search* ("find a commit that plausibly
implemented task N"), and a search stops when it finds a match. Every commit
in the range that maps to no task is simply never looked at — and those are
precisely the commits that record what happened *around* the implementation:
review findings, fix waves, gates.

**Datapoint (same session).** The `strategy-report` range held 14 pre-existing
commits against 11 tasks. Mapping produced a match for each task and left
three unattributed — `c1087de`, `e6594bc`, `009b3f7` — all three of which are
explicit review-fix commits ("Review found …", "Review fixes for Task 8",
"Finding 1 / Finding 2"). Per-task reviews had genuinely run in the prior
session. The reconciliation, seeing no record, wrote "per-task review skipped
— covered by whole-branch review" onto all eleven tasks: a false ledger entry
that erased real review work, produced by a mapping that never asked what the
leftover commits were.

**Rule:** reconciliation is complete only when **every** commit in
`<plan-lock>..HEAD` is accounted for — either attributed to a task or
explicitly classified (review fix, gate, chore, unrelated). Unattributed
commits are not noise to be discarded; they are the direct evidence of
off-ledger work, and they are the only place the reconciliation can learn what
it does not know it is missing. Run the range with `--stat` and classify the
remainder before writing any status line that asserts what did *not* happen
("review skipped", "never executed", "no fix wave") — a negative claim needs
the same receipt standard as a positive one.

### Corollary — an orchestrator's SKETCHED framing in a fix dispatch is a hypothesis, and the fixer is right to refute it

This file's core discipline is "cite the verified landmark, don't push
diagnosis onto the implementer." The inverse hazard is an orchestrator that
supplies not a verified landmark but a *plausible narrative* — and phrases it
confidently enough that a fixer transcribes it verbatim into a
fact-bearing artifact.

**Datapoint (2026-08-24, strategy-report fix wave).** The orchestrator
sketched "~47h of trajectory data" as the framing for the §1 gating-claim fix.
The fixer checked real git/session history, found that the 2026-08-16
follow-up curation session had never run at all, and wrote the true narrative
(the counted pair was hand-picked 2026-08-14 and froze in at the deadline)
instead of the orchestrator's suggestion — receipting it in `facts.md`. The
mitigation worked and cost nothing; recorded here because the *outcome* was
good only because the fixer independently verified, not because the dispatch
was correct.

**Rule:** when an orchestrator-authored dispatch supplies a factual framing it
has not receipted — a duration, a sequence of events, "what probably happened"
— mark it explicitly as a sketch in the prompt ("SKETCH, unverified — verify
against real history before transcribing; if it does not hold, write the true
version and say so"). This matters most for **prose deliverables**: a wrong
claim in code fails a test, while a wrong claim in a report is fluent, passes
every automated gate, and is caught only by a human or a fact-check pass.
(Third datapoint for the global `orchestrator-no-provisional-diagnosis`
drafted proposal, and the first outside the code/diagnosis domain.)

## Extension — an AGGREGATE claim over a growing artifact has NO spot-checkable receipt, so no fact-check pass can falsify it (2026-09-01, strategy-report T12)

This file's discipline is that a claim needs a receipt a later reader can
re-run. A fact ledger (`docs/report/facts.md`) operationalizes that as a
receipt column. But a receipt is only worth the falsification it enables, and
there is one row shape where the receipt column is structurally incapable of
falsifying its own claim: an **aggregate or derived value** (min / max /
count / range / "N observations") cited against a **growing, append-only
artifact**.

A verifier can open the cited file, confirm the ref's rows are there, and
sign off — without ever recomputing the aggregate. So a wrong aggregate
passes a receipt-existence check indefinitely, and then drifts *further* as
the file grows past the window in which it was written.

**Datapoint (receipted).** Two rows in `docs/report/facts.md` carried
`experiments/factory/ladder_snapshots.jsonl (all ref NNNNN rows)` — a
whole-file aggregate citation — and survived the 2026-08-24 T10 fact-check
pass (67 verified / 0 failed) while being wrong on **two independent axes at
once**:

| ledger claimed | truth (recomputed from all 96 rows) | mechanism |
|---|---|---|
| 55512669 range `~349.5-~569.5` | `349.5-558.4` | max was a write-time **transposition of 549.5**, AND had since gone stale as the log grew (real max rose to 558.4) |
| 55512672 range `~491.1-~569.4` | `450.6-569.4` | 491.1 was that ref's **first observation**, mistaken for its minimum (true min was 465.6 even at write time, later 450.6) |

Neither error ever reached `draft.md`, and both were found only when T12
recomputed the aggregates from raw data because it needed the converged
numbers anyway — not by any verification layer. Contrast the rows that stayed
correct: `jsonl:1` / `jsonl:2` cite the FIRST rows of an append-only file,
which never move.

**Rule — three clauses, cheapest first:**

1. **Content-address every receipt into an append-only artifact.** Cite key +
   timestamp + value together (`jsonl:952 (ref 55512669, utc_ts
   2026-09-01T18:15:05Z, public_score 529.4)`), never a bare line number — the
   row stays re-findable by content after the line number drifts.
2. **Reduce a min/max to a point citation.** An extremum IS a specific row, so
   cite that row the same way as clause 1 rather than writing "range over all
   N rows." This converts the one aggregate shape that can be reduced into a
   spot-checkable claim, and it is exactly the check that would have caught
   both errors above.
3. **A count, and anything else irreducible, must carry an as-of stamp AND be
   recomputed at the final gate.** "96 observations" over a file a live
   process is still appending to is true only at an instant; write it as
   "96 as of <utc_ts>", and treat it as REQUIRING recomputation by any
   downstream gate. A stored `VERIFIED` / `PENDING` status is not evidence for
   an aggregate — no receipt-existence check can falsify one.

**Prospective instance, already live.** The post-fix rows carry a converged
point value that is fully content-addressed (clause 1) but still assert
"96 snapshots" while the snapshot logger keeps running on its 4h cadence —
which is precisely why T13's pre-flight advisory N1 says to re-count from
`ladder_snapshots.jsonl` immediately before the one-shot submission. That
advisory is clause 3 in action; do not treat the number as settled because a
fact-check pass once agreed with it.
