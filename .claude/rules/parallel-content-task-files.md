---
paths:
  - "candidates/**"
  - "experiments/**"
  - "docs/superpowers/plans/**"
---

# Give parallel content tasks per-task output files, never one shared append-file

When a plan fans out N cohesive content tasks that each *append* to the same
document (e.g. one `candidates/RATIONALE.md` written by both the T8 and T9
deck tasks), that shared file becomes a false dependency:

1. It **forces serialization** of otherwise-independent tasks — the two deck
   implementers cannot run in parallel because both write the same file, so
   the orchestrator must chain them and loses a full wall-clock window.
2. It **still gets clobbered** when a fix loop resumes: a re-dispatched fixer
   re-reads a stale copy of the shared file and overwrites the sibling task's
   section (the stale-copy-clobber failure mode). Serialization does not
   prevent this — resumption reintroduces the race.

**Rule for plan authors (writing-plans / project-manager):** when 2+ parallel
tasks each produce narrative/rationale/analysis prose, give each task its **own**
output file (`candidates/RATIONALE-<deck>.md`, `experiments/<task>-notes.md`),
and add a trivial final "concatenate the per-task files into the combined doc"
step if a single document is the deliverable. Reserve a single shared file only
for tasks that are inherently serial. If a shared append-file is unavoidable,
specify an **append-only protocol** in the task spec (each task appends its own
delimited section, never rewrites the whole file) and forbid full-file rewrites.

**Datapoint (2026-07-09, Slice 3 deck-tournament):** T8/T9 shared
`candidates/RATIONALE.md`, which forced their serialization AND was still
clobbered in a resumed fix loop. Per-deck rationale files would have let the
deck tasks parallelize safely and made each fix loop idempotent.
