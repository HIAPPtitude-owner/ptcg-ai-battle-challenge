---
paths:
  - ".claude/plan.md"
  - "docs/superpowers/plans/**"
---

# A session-startup plan-state report must treat the FINAL/Review section as authoritative over older Progress Log entries — a closed blocker logged mid-slice is not an open blocker

`.claude/plan.md` is append-mostly: the Progress Log accumulates entries in
chronological order, and a blocker discovered at 09:00 gets its own line
regardless of whether 11:00's line closes it. When a plan reaches COMPLETE,
the authoritative statement of what is still open lives in the plan's
**Review / close-out section**, not in the middle of the log.

`plan-manager-agent`'s session-startup job ("read plan.md and report the
current phase, active tasks, and any blockers") is a scan, and a scan over an
append-mostly file finds the *first* mention of a blocker, not the *last*
word on it. The result is a startup report that resurrects already-closed
problems as if they were live — and the orchestrator, which has no prior
context at session start, has no reason to doubt it.

**Datapoint (2026-08-14, freeze-pair-probe-and-finalize).** The startup
report listed two open blockers — a disk-fill root cause and an unexplained
PAUSE window — both of which the *previous* session had root-caused and
closed (dead 30GB pagefile; a benign quiet-machine suite run). Both were
resurrected from mid-slice Progress Log lines. Cost was one correction round
before real work started; the worse counterfactual is a session that scopes
remediation work for a problem that no longer exists.

## Rule

**For plan-manager-agent (include in the startup-report dispatch):**

> When reporting blockers from a plan whose status is COMPLETE (or whose
> Review/close-out section exists), read the Review/close-out section FIRST
> and treat it as authoritative. A Progress Log entry that raises a blocker
> is superseded by any later log entry or Review-section line that resolves
> it. Report a blocker as open only if nothing later in the file closes it,
> and cite the line you are reporting from so the orchestrator can check.
> Prefer "no open blockers per the Review section; the log mentions X and Y,
> both closed later in the file" over listing X and Y as live.

**For the orchestrator:** treat any startup-reported blocker as a *claim
with a citation*, not a fact — the same standard as any other subagent
report (global CLAUDE.md §1). One `Grep -n` for the blocker's keyword across
the whole plan file, reading the LAST hit rather than the first, settles it
in seconds. Do this before letting a reported blocker influence scope.

**Cheapest structural fix, if this recurs:** have the close-out step write
resolutions back onto the originating Progress Log line ("~~BLOCKER: disk
root cause unknown~~ — CLOSED 2026-08-13, dead pagefile") rather than only
appending the resolution further down. A scan then cannot find the open
form at all.

## A session can implement, review, AND fix an entire slice while touching plan.md ZERO times — make the plan-vs-git drift check a mandatory startup step

The rule above assumes plan.md is at least *approximately* current and the
risk is misreading it. The sharper failure is plan.md being **completely
silent** about work that really happened: the file is not wrong, it simply
has no entry at all, so a scan of it is not misleading — it is empty. The
next session then opens onto a plan showing an earlier phase, with no signal
whatsoever that the intervening work exists.

Nothing in the lifecycle catches this. plan-manager-agent only writes when
the orchestrator dispatches it, so an orchestrator that never dispatches it
produces no record and no error. The suite is green, the commits are clean,
the branch is healthy — the only artifact that is wrong is the one nobody
re-reads until the next session starts cold.

**Datapoint (2026-08-24, strategy-report).** The 2026-08-18 session executed
T1–T11 of a locked 13-task plan across **13 commits in ~91 minutes**
(`ad2d341` 08:43 → `728d76a` 10:14) and updated `.claude/plan.md` **zero
times** — not a checkbox, not a single Progress Log line. plan.md's last
touch before this was `8b80d93` (08-18 07:54, the *previous* feature's
close-out); its next was `7018b79`, six days later. The resuming session
found a plan.md still showing Brainstorm-phase state against a git history
showing 11 of 13 tasks done, and spent one Brad round-trip deciding how to
reconcile. Worse, the silence hid *more* than implementation: three of those
commits (`c1087de`, `e6594bc`, `009b3f7`) are explicit review-finding fixes
("Review found…", "Review fixes for Task 8", "Finding 1 / Finding 2") — so
per-task reviews had genuinely run, and the reconciliation, having no record,
wrote "per-task review skipped" onto all eleven tasks. Real review work was
erased from the ledger by the same silence.

**Rule — one command, run at session start, before any scope reasoning:**

```bash
# what git says happened since the plan was locked …
git log --oneline <plan-lock-commit>..HEAD -- . | cat
# … versus what plan.md claims. Any commit in that range with no
# corresponding checkbox/Progress-Log entry is off-ledger work.
grep -n "^- \[" .claude/plan.md
```

The plan-lock commit is recorded in plan.md's own Progress Log ("Plan
COMMITTED (`<sha>`)"), so this is two commands with no setup. A nonempty,
unexplained commit range is the finding — stop and reconcile *before*
scoping, rather than discovering it mid-slice.

**Symmetric end-of-session check (the cheaper half — prevention, not
detection).** Before Finish, the orchestrator asks the same question in
reverse: *did HEAD move this session without plan.md moving?* If yes, the
session is about to hand the next one the exact artifact this datapoint
describes. This costs one `git log` and is the last moment it is free.

**Cheapest structural fix, if this recurs:** a `Stop`-event hook that
compares `git rev-parse HEAD` against a session-start snapshot and warns when
HEAD advanced while `.claude/plan.md` did not (neither modified in the tree
nor touched in the range). Drafted, NOT built — one datapoint, and this
project's hook proposals have repeatedly proven unbuildable against the
current hook API (global CLAUDE.md → Skill proposals). Build only on a 2nd
occurrence; until then the two-command startup check above is the mitigation.

## Do not delete a stale plan.md block until its UNIQUE carry-forwards are migrated — an append-instead-of-edit history buries live obligations inside dead text

plan.md in this project has been written by *appending a fresh full-plan
snapshot* rather than editing the live dashboard in place. As of 2026-08-24
the file carries **three stacked full-plan blocks** (a live one at the top,
plus two superseded Brainstorm-phase snapshots below a reconciliation
divider) — every one with its own Status Dashboard, Task List, and Phase
Checklist, most of them stale.

The obvious cleanup — delete the dead blocks — is the trap. A stale block is
not uniformly stale: it can hold the *only* copy of a live carry-forward that
was never mirrored upward when the newer block was appended.

**Datapoint (2026-08-24).** The third (oldest) snapshot's "Carry-Forward
Notes" section holds two items that appear **nowhere in the live Notes
block**: (1) the **~Aug-31 competition-license deletion obligation** — the
`pokemon-tcg-ai-battle/` engine and card data are licensed for competition
use only and must be deleted at competition end, i.e. a real compliance
obligation, not a nice-to-have; and (2) "76 publish drafts pending Brad
approval." A tidy-up pass that deleted the superseded blocks would have
silently dropped both.

**Rule:** before removing any superseded plan.md block, diff its
Notes/Carry-Forward/Blockers sections against the live block's and migrate
anything unique upward *first*; only then delete. Treat compliance- or
deadline-bearing items (license deletion, data retention, submission
windows) as migrate-always. And when writing plan.md at all, prefer editing
the live dashboard in place over appending a new snapshot — appending is
what created this hazard.
