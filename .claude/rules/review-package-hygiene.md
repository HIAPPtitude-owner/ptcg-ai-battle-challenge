---
paths:
  - "src/**"
  - "scripts/**"
  - "tests/**"
---

# Review packages: pin the commit, and exclude generated blobs from the diff

Two cheap hygiene rules for the diff/context bundle handed to a per-task
reviewer, a whole-branch reviewer, or the Pass-2 critic in this repo. Both
were confirmed by the counted-pair-protection slice (2026-08-05).

## 1. Pin the commit hash — `HEAD` is a moving target under parallel dispatch

Global CLAUDE.md's reviewer git-verification directive says to verify file
contents via `git show HEAD:<path>`. That is correct for a serialized
dispatch, but **wrong the moment reviewer-N runs concurrently with
implementer-N+1** (the settled parallel-dispatch pattern in this repo): the
implementer commits while the reviewer is mid-review, `HEAD` advances, and
the reviewer ends up reading code from a task it was never asked to review —
producing findings against lines outside its scope, or "the fix isn't here"
confusion.

**Rule:** when dispatching a reviewer that may overlap another agent's
commits, capture the commit hash under review at dispatch time and put it in
the prompt verbatim, replacing `HEAD` throughout:

> Verify file contents via `git show <pinned-hash>:<path>`. Do NOT use
> `HEAD` — other tasks are committing concurrently and `HEAD` will move
> during your review. Review only the state at `<pinned-hash>`.

**Datapoint (2026-08-05):** pinned-commit review packages were used across
**4 overlapping reviewer-N / implementer-N+1 pairs** with zero scope
confusion and zero stale-diff findings, alongside zero `index.lock`
collisions. Cost: one `git rev-parse` per dispatch.

## 2. Exclude large generated JSON from the review diff

This repo routinely commits large generated artifacts alongside code —
value-net / policy-net weight blobs (`src/ptcg/search/value_net_weights*.json`),
exported ratings, ledger snapshots. A naive whole-branch diff swells to
megabytes of unreviewable numeric noise that crowds out the code the
reviewer is actually there to read.

**Rule:** build the review package with the generated blobs excluded, and say
in the prompt that they were reviewed separately (their real review is the
parity/golden-vector test, not human diff-reading):

```bash
git diff <base>...<head> -- . ':(exclude)src/ptcg/search/*weights*.json' \
                             ':(exclude)experiments/factory/*.json'
```

**Datapoint (2026-08-05):** the auto-generated whole-branch review package
came to **1.15 MB**, of which 6 net-weight JSON blobs were the bulk; a
hand-curated package with those excluded was **129 KB** — ~9x smaller, with a
one-line note that the blobs were verified by their own parity tests. Fold
the exclude pathspec into the package invocation rather than re-curating by
hand next time.

## 3. `git log` the range before building it — commit order is NOT dispatch order

Under single-branch parallel dispatch, task commits interleave: T5's
implementer can land before T4's fixer, a fix wave lands after the task it
fixes, and a plan-manager `chore:` commit can sit in the middle. An
orchestrator that builds a review range from its own memory of dispatch
order (`git diff <T3's commit>...<T6's commit>`) will silently include or
exclude the wrong tasks — the range still resolves, so nothing errors; the
reviewer just reads the wrong diff.

**Rule:** before constructing ANY review range (per-task, whole-branch,
Pass-2), run `git log --oneline <base>..HEAD` and read the ACTUAL order,
then pick the endpoints from that output. Never derive endpoints from the
order you dispatched in.

**Datapoint (2026-08-11, min-basics-pool-rule):** review-package ranges
were built on dispatch-order assumptions **twice** in one slice, and both
times had to be corrected after a `git log` showed the real interleaving.
Cost each time: one round-trip. Cost of the check: one command. Compounds
with the co-mingle hazard in
`.claude/rules/parallel-dispatch-commit-hygiene.md` — if a commit's stat
doesn't match its task, the range endpoints are wrong even when the order
is right.

**Re-confirmed same day (2026-08-05, ui-remove-any-deck):** pinned-commit
review packages held clean across another slice with ~6 overlapping
reviewer-N/implementer-N+1 pairs (single-branch parallel dispatch, no
worktree) — zero scope confusion, zero stale-diff findings, zero
`index.lock` collisions. Second clean slice in one day under the settled
pattern; no wording change needed.
