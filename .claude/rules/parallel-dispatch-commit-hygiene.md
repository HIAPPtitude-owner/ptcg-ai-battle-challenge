---
paths:
  - "src/**"
  - "scripts/**"
  - "tests/**"
---

# Single-branch parallel dispatch: every agent commits with an explicit pathspec, never a bare `git commit -a`/`git commit` over a shared index

This repo runs parallel implementer-N+1 / reviewer-N dispatches on ONE
branch in ONE working tree (no worktree — Python project, but the same
single-tree pattern the Windows-pnpm precheck forces elsewhere). Global
CLAUDE.md already forbids `git add .` for this reason. That rule is
necessary but NOT sufficient: an agent that stages correctly by explicit
path can still SWEEP a sibling's staged files at COMMIT time, because
`git commit` with no pathspec commits the whole index — including whatever
the concurrent sibling has already staged.

**Datapoint (2026-08-11, min-basics-pool-rule, commit `12976e5`).** T3 and
T4 ran in parallel on disjoint files. Both staged by explicit path per the
existing rule. T3 then committed without a pathspec and swept T4's staged
files into its own commit — so `12976e5` ("refactor: builder
MIN_BASIC_POKEMON sourced from validate.MIN_BASIC_CARDS + guarantee tests")
also carries T4's work. No code was lost and nothing broke, but the commit
history no longer maps task->commit, which is exactly what the review
packages, the plan.md ledger, and any later `git log`-based range are built
on (see `.claude/rules/review-package-hygiene.md` §3).

## Rule — include verbatim in every implementer/fixer dispatch on a shared branch

> You are committing on a branch other agents are committing to
> concurrently. Stage by explicit path AND commit by explicit pathspec:
> `git commit -- <path> <path> ...` (or `git commit <paths>`). NEVER run a
> bare `git commit`, `git commit -a`, or `git add .` — either will sweep a
> sibling task's staged files into your commit. On `index.lock`
> contention, wait 2s and retry the git op up to 3x.

## Orchestrator-side check

After a parallel pair lands, `git show --stat <hash>` on each commit and
confirm the file list matches the task's own scope. A commit whose stat
includes files the task never touched is a co-mingle — cheapest to catch
immediately (the fix is bookkeeping/plan.md notation, not a rewrite),
expensive to discover later when a review range or a bisect depends on the
mapping being true.

## The orchestrator-side check applies to `plan-manager-agent`'s own self-reports too (2026-08-12/13, freeze-curation-and-unpayable-pool)

The commit-mapping mismatch above is usually framed as "a sibling implementer
swept another sibling's files." The same class of drift showed up this
session on the plan-management side: `plan-manager-agent` self-reported
having committed the plan/status doc, but `git status` afterward showed it
had actually committed a different set of files than reported. Caught only
because the standing "verify subagent self-reports with `git status`" habit
(global CLAUDE.md, Subagent Discipline) was applied to a *doc* commit, not
just a *code* commit — this discipline is not code-specific.

**Rule, extended:** the orchestrator-side check above ("`git show --stat
<hash>` and confirm the file list matches scope") applies to EVERY agent
that commits on the shared branch, including `plan-manager-agent` itself —
not just parallel implementer pairs. Do not exempt plan/status-doc commits
from the same verification just because they feel lower-stakes than code.

## `plan-manager-amendment-drift`: a delegated doc-writer can embellish dictated spec/plan text, and the drift is invisible until a reviewer checks code against the WRONG expectation

Separately this session, a delegated doc-writer (drafting plan/spec text)
added a clause that was never dictated or approved — "itemize culled
species" in the unpayable-attack-pool-rule spec — beyond what the
orchestrator actually specified. Nobody caught it until Pass 2, which
flagged a false conformance gap: the *code* was correct per the real
requirements, but the *plan text* had drifted to describe something
stricter that was never asked for, so the review initially read as a
missing feature rather than a doc bug.

**Rule:** when dispatching an agent to write plan/spec text from dictated
content (not free-form drafting), the dispatch must supply the exact text
to transcribe, not a paraphrasable instruction — and the orchestrator
should spot-check the committed diff against what was actually dictated
before treating the doc task as done. This is the doc-writing analog of
"quote the spec verbatim in reviewer dispatches" (global CLAUDE.md,
Subagent Discipline) applied to the AUTHORING side instead of the
reviewing side: a paraphrase drifts whether it's a reviewer summarizing a
constraint or a doc-writer transcribing one.
