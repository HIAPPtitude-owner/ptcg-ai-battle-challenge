# Verify external-platform slot/count/eviction mechanics before designing a gate around them

Any gate, guard, or protection mechanism designed around an external platform's
slot-count, ranking, or eviction semantics (submission slots, rate limits,
leaderboard windows, cache-eviction policies) must be checked against the
platform's **actual documented/observed behavior first** — not inferred from
intuition, from how our own internal ranking works, or from "how it probably
works given the UI." An AskUserQuestion approval built on a wrong mental model
still ships the wrong design; the approval step only validates that Brad
agreed with the *stated* model, not that the model is correct.

**Datapoint (2026-07-14, weekly factory review):** a submission-protection
gate was designed and AskUserQuestion-approved on the assumption that Kaggle
evicts submissions by SCORE (keep the top-scored of the counted slots). The
real rule is Kaggle counts only the two most-recently-submitted submissions
and evicts by RECENCY — a single new challenger submission can evict the
highest-scoring submission right along with the weakest one, if it happens to
be the older of the counted pair. The wrong model was only caught by the
Pass-2 blocking critic walking the **actual production submission flow**
end-to-end (not the abstract gate spec) — the critic asked "what physically
happens to the counted pair when this code runs," not "does this code match
the design doc." The corrected design is the champion-pairing guard
(`src/ptcg/factory/gate.py`/`submit.py`, commit `1ed7b60`): full incident
writeup in memory `slice7b-weekly-review-follow-up.md`.

**Rule:**
1. Before locking a plan/design around a platform's slot/count/eviction/
   ranking mechanics, verify the mechanic from the platform's own docs or
   directly-observed behavior — cite the source in the plan.
2. When such a gate is reviewed (Pass-2 or equivalent), the reviewer must
   trace the CONCRETE production flow ("what does the platform actually do
   when this code executes N times in sequence") rather than only checking
   the code against its own design doc — a design can be perfectly
   self-consistent and still be built on a wrong external model.
3. If the mechanic is genuinely undocumented/unclear, say so explicitly in
   the plan and treat the resulting gate as provisional pending real-world
   confirmation, rather than presenting it with unwarranted confidence.

## Documented mechanic: post-deadline convergence period, new-submission seed score, frozen-at-eviction (2026-08-14, freeze-pair-probe-and-finalize)

Verified directly from the competition's own Overview page (not inferred)
ahead of the 2026-08-16 final-submission deadline, and cited with file
references in `docs/superpowers/specs/2026-08-14-freeze-pair-probe-and-finalize-design.md`:

- **The freeze-day leaderboard is NOT the final leaderboard.** After the
  final-submission deadline, games continue roughly Aug 17–31 "or until the
  leaderboard has reached convergence" — the FINAL score is the converged
  end-of-period rating, not whatever is displayed at deadline close. A
  decision made by comparing freeze-day snapshot scores is comparing
  transients, not the quantity that actually decides placement.
- **New submissions seed at publicScore 600.0** and get boosted episode
  rates for their first stretch — confirmed by direct observation this
  session (both probe uploads, refs 55512669/55512672, read exactly 600.0
  at first snapshot, then moved substantially within the first hour:
  349.5→512.3 and 491.1→569.4). Any reading taken in that boosted-episode
  window is a transient, not a settled rating — do not act on it.
- **An evicted (uncounted) submission FREEZES at its score at the moment of
  eviction** — it does not continue to play games or drift. Verified against
  5 historical refs in this repo's own submission history. A high frozen
  reading on an old, evicted submission (e.g. v0.16's 654.8 read at ~10h
  before eviction) is not a live rating and is not comparable to a currently-
  counted submission's in-progress score.
- **Only the 2 most-recent submissions are active/counted; eviction is by
  recency, not score** (this is the mechanic the original 2026-07-14/
  2026-08-05 entries below already establish — restated here because it
  combines with the freeze-at-eviction fact above to fully explain why
  freeze-day snapshot comparisons are decision-unsafe).

**Rule reinforced, not changed:** this is exactly the class of fact this
file already tells you to verify before designing a gate — logged here as a
concrete, dated instance so a future session doesn't have to re-derive it
from the competition Overview page again. See
`docs/superpowers/plans/2026-08-14-freeze-pair-probe-and-finalize.md` for
the full Aug-16 final-curation runbook that applies these mechanics.

## Re-confirmed at DESIGN time, not just review time (2026-08-05, counted-pair-protection)

The rule paid for itself a 2nd time — this time it caught the mistake
BEFORE any code was written, not at the Pass-2 gate. The initial framing
for the new pair-gate was "the champion must beat the WEAKEST counted
submission." Applying the already-documented recency-eviction mechanic
(§ above) immediately surfaced that this was wrong: under recency
eviction, a new upload evicts the OLDER of the two most-recent
submissions, which is not necessarily the weaker one — so the gate had to
check against the TO-BE-EVICTED (older) submission instead. Because the
mechanic was already written down from the 2026-07-14 incident, the
correction cost zero round-trips this time. Trigger for this rule (verify
the platform mechanic before locking the design) fired at plan-time
exactly as intended — this is the rule working as designed, not a new
failure. See memory `counted-pair-protection-2026-08-05.md`.

## Documented mechanic: competition ENTRY status is a first-class CLI field — read it, do not infer it from side-effects (2026-09-01, strategy-report T12 / T13 Step 1)

Kaggle competition entry (rules acceptance) is a hard gate on submitting at
all, and it has a **direct, authoritative** read:

```bash
uvx kaggle competitions list -s "<search term>"   # includes a userHasEntered column
```

The Strategy competition row showed `userHasEntered True` (deadline
2026-09-13 23:59, teamCount 570), which is the receipt T13 Step 1 was closed
on. The tempting alternative — probing `competitions submissions <slug>` or
`competitions files <slug>` and reading whether they error with a
rules-acceptance message — is **inference from a side-effect**: it is slower,
it conflates "not entered" with any other CLI/auth/network failure (the same
conflation `check_auth()` already commits, see
`.claude/rules/factory-resume-probe.md`'s 2026-08-12 addendum), and a
"No submissions found" reply looks identical whether you are entered or not.
Use those two only as corroboration, never as the primary check.

**Two mechanics recorded here so a future session need not re-derive them:**

- **Entry and submission are separate deadlines and separate acts.** Entry
  (2026-09-06 for the Strategy category) is confirmed by `userHasEntered`;
  the Writeup submission (2026-09-13) is a distinct, one-shot act. Being
  entered says nothing about having submitted, and `submissions <slug>`
  returning "No submissions found" on an entered competition is the expected
  state, not a failure.
- **The interface is per-subcommand, not per-tool.** `competitions list`
  takes `-s <search>`; `competitions submissions` and `competitions files`
  take the slug **positionally**, with no `-c` flag. This bit the T13 runbook
  (see the 2026-09-01 re-confirmation in
  `.claude/rules/golive-command-preflight.md`) — reconcile each subcommand
  against its own `--help` before running a plan-authored line.
