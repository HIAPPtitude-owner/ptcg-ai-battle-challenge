---
paths:
  - ".superpowers/sdd/**"
  - "docs/superpowers/plans/**"
---

# `.superpowers/sdd/task-N-brief.md` / `task-N-report.md` are reused every slice — treat as current-slice-only

`.superpowers/sdd/` is gitignored scratch (see `.superpowers/sdd/.gitignore`)
and its per-task brief/report filenames (`task-1-brief.md`,
`task-1-report.md`, ... `task-N-report.md`) are NOT slice-namespaced. Every
new slice's Task 1 implementer writes to the same `task-1-brief.md` a prior
slice's Task 1 used.

**Datapoint (2026-07-10, Slice 5):** a Slice 3 report file was silently
overwritten by a Slice 5 dispatch this session (recoverable — it's gitignored
scratch, not lost work of consequence) and a stale Slice 4 observation on one
of these files triggered the `PreToolUse:Read` truncation hook on what looked
like a fresh Slice 5 brief, costing a confused re-read.

**Practical rule:** in a resumed or long-running session, treat the CONTENTS
of any `.superpowers/sdd/task-N-*.md` file as belonging to the CURRENT slice
only — do not assume a file with that name still holds what an earlier slice
wrote there, and do not rely on it as a durable record across slices (that's
what `docs/superpowers/plans/` and `.claude/plan.md` are for). If a durable
per-slice artifact is needed, prefix the filename with the slice name
(`task-1-brief-slice5.md`) or write it outside `.superpowers/sdd/` entirely.
