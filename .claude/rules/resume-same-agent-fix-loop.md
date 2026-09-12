---
paths:
  - "docs/superpowers/plans/**"
  - ".claude/plan.md"
---

# Review-fix loops: resume the ORIGINAL agent via SendMessage, don't dispatch a fresh one

When a per-task reviewer returns a finding and the fix is scoped to files the
original implementer already wrote (the common case), resume that SAME
implementer with `SendMessage` carrying the finding — rather than dispatching
a brand-new fix subagent. The original agent still holds full file context,
the plan section, and its own reasoning about why it wrote the code the way
it did, so the fix lands in one round-trip with zero context-rebuilding cost.
The same applies to re-reviews: resume the ORIGINAL reviewer via SendMessage
("here's the fix commit, re-review") instead of spinning up a fresh reviewer
that has to re-read the whole diff cold.

**Datapoint (2026-07-10/11, Slice 6):** three review-fix loops (T4 hte-
differential fix, T6 empty-file offset-chain fix, T12 engine-safe begin/end
pairing fix) plus their re-reviews were all handled by resuming the original
implementer/reviewer via SendMessage. Every fix landed in a single round-trip;
none required re-establishing context. Four resume cycles total, zero rework,
zero dispatch failures across the slice.

**Re-confirmed (2026-07-21, evolutionary-agent-population):** three
re-review cycles (T5/T6/T8), each a SendMessage-resume of the original
reviewer/implementer, zero context rebuilds. Holds as the settled default
for single-owner-file review-fix loops; stop logging individual
re-confirmations unless a stale-copy-clobber actually bites.

**The stale-copy-clobber boundary (when this is SAFE vs when it is NOT).** A
resumed agent edits from its own transcript's cached view of the files, which
can be stale if another task appended to a SHARED file since the agent last
read it (global CLAUDE.md → Subagent Discipline, "Stale-copy-clobber": a
resumed fix agent deleted an 85-line sibling section from a shared
`RATIONALE.md`). The Slice-6 loops were safe precisely because each fix
touched a SINGLE-OWNER file — code only that implementer had written in that
slice, with no concurrent appenders — so the cached view could not be stale.

**Practical rule:**
1. **Default to SendMessage-resume** for any review-fix loop whose fix is
   confined to files the original agent owns exclusively. It is strictly
   cheaper than a fresh dispatch and loses nothing.
2. **Fall back to a fresh fix dispatch** when the fix must touch a file that
   OTHER tasks have written to since the original agent's last read (shared
   append-files: a rationale doc, EXPERIMENTS.md, a shared fixtures module) —
   OR, if you still resume, the resume message MUST instruct the agent to
   re-read the shared file from disk at HEAD (`Grep -n . <path> -A 5000` /
   `git show HEAD:<path>`) before editing, never trust its cached copy.
3. The single-owner-file check is the gate: "has anything other than this
   agent touched these files since it last saw them?" If no, resume freely.
   If yes, force a fresh read or a fresh dispatch.

## Route the fix by FILE OWNERSHIP, not by which task surfaced it (2026-08-05)

The rule above says "resume the original implementer," which reads as *the
implementer of the task the finding was filed against*. When a reviewer's
finding lands on task N but the defective code lives in a file task M wrote,
resume **M's implementer** — the owner of the file — not N's. The gate in
clause 3 is about who last held the file's context, and that is the file's
author regardless of which task's review surfaced the problem.

**Datapoint (counted-pair-protection, 2026-08-05, T6).** A superseded-series
sweep finding surfaced at T6's review, but the defect was in
`pairgate.py`'s enqueue path, written by the **T3** implementer. Routing the
SendMessage-resume to T3's implementer (not T6's) landed the fix in one
round-trip with a RED receipt and a clean re-review (`edc7f4a`) — no context
rebuild, and no risk of a T6 agent editing a file it had never read. Both fix
rounds this session (T4, T6) closed in a single round-trip under this
routing. Before resuming, ask "who WROTE the lines being changed?", not
"whose task is this finding filed under?"

**Re-confirmed the next session (ui-remove-any-deck, 2026-08-05).** A
finding surfaced during T6's (routing) review was actually a defect in code
T5 (the rendering module) had written — routed to T5's agent, the file's
actual author, rather than T6's. Fixed in one round-trip, zero context
rebuild. Two consecutive sessions confirming file-ownership (not
task-of-origin) as the correct routing key; treat as settled, no further
wording needed.
