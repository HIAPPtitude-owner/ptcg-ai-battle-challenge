# Tournament Breeding Anchor Pressure — Design Spec (2026-08-03)

## Problem

The generational champion tournament breeds in a closed weak pool with zero
external strength pressure. This was verified by three diagnostics (D1/D2/D3,
2026-08-02, recorded in `.claude/plan.md`): four consecutive crowned champions
failed the ladder anchor gate —

| Champion | Anchor win rate (vs 0.55/200 bar) |
|---|---|
| v0.5 | 0.030 |
| v0.6 | 0.035 |
| v0.7 | 0.100 |
| v0.8 | 0.045 |

The D3 factorization identified deck quality as the primary failure mode, not
agent quality: champion decks score only 4.5–6.5% against mega-lucario-fighting
when both sides use identical agents, and even the pool's single best-rated
deck by internal Bradley-Terry rating (Talonflame, rated 8.51) only scores
1.5–4.0% against the same anchor. In other words, the tournament's internal
ratings are measuring relative dominance within a junk cohort — they say
nothing about absolute strength against a deck that actually wins games on
the real ladder.

Four verified defects underlie this collapse:

1. **Deck-blind mirror/baseline-only MATCH play.** Deck-stage rating games are
   played mirror-style or against the pool's own evolving baseline, never
   against a deck of known real-world strength. A deck can climb the internal
   rating ladder purely by being marginally less bad than its equally-weak
   pool-mates.
2. **No absolute-strength floor anywhere in selection.** Nothing in the
   pipeline checks a candidate against an external strength reference before
   it advances. Weak decks and weak offspring can sail through every internal
   gate.
3. **Champions promoted to next-generation baseline BEFORE the anchor verdict
   lands.** The generation loop crowns a champion and immediately uses it as
   the next generation's baseline before the (much slower, 200-game) anchor
   check has weighed in. This creates a compounding collapse: each new
   generation breeds from the prior generation's already-junk champion, with
   no external correction step anywhere in the loop.
4. **Value-net swaps adopted unvalidated.** When a new value net is trained,
   it is swapped in without being checked against the incumbent net's actual
   performance — a regression in net quality can silently degrade selection
   quality with no gate to catch it.

## Decisions

Approved by Brad on 2026-08-02, via AskUserQuestion:

- **Pressure mechanism:** anchor opponents in rating games plus an early
  floor. (Rejected alternatives: floor-only with no anchor-opponent rating
  change, and direct-anchor-fitness where the anchor score itself becomes the
  fitness function.)
- **Pool:** reseed from proven decks and cull the collapsed lineage. (Rejected
  alternatives: keep the existing pool as-is, and a hybrid partial-reseed.)
- **Scope:** fix all four defects in one slice, not a subset.

## Design

### 1. Anchor-opponent rating (fixes defect 1)

Deck-stage rating games are changed to play against the anchor deck
(mega-lucario-fighting) with equal agents on both sides (the current baseline
agent), replacing the existing mirror/baseline-only play. Deck advancement
through the tournament now keys on win rate versus the anchor, so the rating
that decides which decks survive is an absolute-strength measurement rather
than a measurement of standing within the pool.

Offspring-stage MATCH play (offspring vs. incumbent) is retained unchanged —
it still measures whether an offspring agent config beats the current
incumbent agent. Offspring additionally play anchor games as part of the new
early floor check (see design 2), so both deck selection and offspring
selection get anchor-grounded evidence, not just one of the two axes.

The anchor definition used throughout this design is the same anchor already
defined for the submission gate: mega-lucario-fighting paired with
`HeuristicAgent`, as implemented in `anchor.py`. No new anchor definition is
introduced; this design reuses the existing one so anchor-relative numbers
stay comparable across the tournament and the submission gate.

### 2. Early anchor floor (fixes defect 2)

A new pre-promotion gate is added: a candidate must achieve win rate
**>= 0.40 over 50 games** against the anchor before it is allowed to enter
playoffs or the confirm stage. This floor sits well below the existing
crown-time bar (0.55 over 200 games) deliberately — it exists to cull
obviously-collapsed lineages cheaply and early, not to duplicate the crown
gate's job.

**Bar calibrated empirically 2026-08-04 (was 0.45 as originally specified).**

```
uv run python scripts/measure_floor_distribution.py --games 100
```

11 reseeded decks x 100 games, heuristic-vs-heuristic vs the anchor (1100
games total): **pooled wr = 0.471 (518/1100), min 0.360, max 0.560.** The
reseeded pool sits near parity with the anchor, NOT far above it — so a 0.45
bar would have sat right on top of the pool's own distribution. Per-deck
false-fail rates at n=50:

| Bar | False-fail range across pool | Parity decks | Weakest deck (0.360) | Junk class (<=0.10) |
|---|---|---|---|---|
| 0.45 | 0.13 – 0.72 | median ~0.39–0.45 | — | ~1.0 |
| **0.40** | 0.008 – 0.39 | 0.04 – 0.20 | 0.68 | ~1.0 |

0.40 keeps the floor's actual target — junk-class decks at 0.03–0.10 — failing
essentially always, while dropping the false-fail rate on genuine parity decks
from roughly 40% per attempt to 4–20% (i.e. they pass ~84–96% of the time).
With `FLOOR_MAX_ATTEMPTS = 3` re-picks, a parity deck's chance of being
wrongly trashed outright becomes negligible. Boundary at the calibrated bar:
`20/50 = 0.400` passes (comparison is `>=`, and n=50 lands on the bar
exactly), `19/50 = 0.38` fails.

The existing full anchor-check gate at crown time (0.55 win rate over 200
games, replicated per the global stochastic-gate-replication rule) is
unchanged and remains the authoritative gate for promotion to ladder
candidate.

Floor games are played heuristic-vs-heuristic (both sides on the baseline
agent), which keeps the 50-game check cheap relative to a full search-agent
evaluation.

All new status-guard gates introduced by this design (both the early floor
gate and any gate ordering change from design 3) must be implemented as
single `BEGIN IMMEDIATE` transactions around their full read-decide-act
sequence, per `.claude/rules/single-actor-worker-tests.md`. This TOCTOU
failure class has recurred three times in this repository already (the 5/day
submission-cap race fixed in `178043b`, the matrix-worker stale-row clobber
fixed in `6009b4d`, and the `enqueue_match_games`/`enqueue_confirm_series`
non-atomic guards fixed in `5f4bd35`) — any new status-guard step function
added here must be hardened against the same class from the start, not
discovered again at review time.

### 3. Anchor-verdict-before-promotion (fixes defect 3)

The generation-advancement ordering changes: a champion is crowned and
becomes the next generation's baseline agent **only after** it has passed the
full 0.55-over-200-games anchor check. If a candidate fails that check, there
is no version bump and no baseline advance — the current generation continues
breeding from its existing baseline instead of adopting the failed candidate.

This directly closes the compounding-collapse mechanism identified in defect
3: previously, each generation's baseline was the prior generation's crowned
champion regardless of whether that champion had passed any external
strength check, so a weak champion could become the seed for an even weaker
next generation with no external correction anywhere in the loop. Under this
design, the loop cannot advance its baseline past a champion that has not
demonstrated real strength against the anchor.

### 4. Validated net swaps (fixes defect 4)

A newly trained value net is adopted as the active net only if it beats the
incumbent net in a direct head-to-head evaluation, defined as win rate >= 0.55
over 100 games. If the new net does not clear this bar, the incumbent net is
kept in place and the rejection is logged (candidate net identity, the
head-to-head result, and the reason for rejection) so the training pipeline's
history remains auditable.

### 5. Pool reseed (one-time migration)

As part of landing this design, the collapsed deck lineage is culled from the
active pool. Culled rows are **retired, not deleted** — their provenance is
preserved in the ledger/database exactly as the existing retirement mechanism
already does elsewhere in the factory, so the collapse itself remains
inspectable after the fact.

The deck pool is reseeded from three proven templates:

- mega-lucario-fighting (the incumbent, already the anchor deck)
- starmie-water
- density20

plus legality-validated mutations of each of these three, generated through
the existing `breeding.py` `validate_deck` legality oracle — no new mutation
mechanism is introduced by this design.

**Accepted one-way door: the reseed permanently freezes the deck pool.**
Decided by Brad on 2026-08-03, deliberately, for the 2026-08-16 window. After
the reseed runs, the seeded set is the entire deck search space for the rest
of the competition — nothing in the factory can add a deck back:

- culled concepts are retired for provenance and are never reactivated (no
  code path flips `status='culled'` back to `active`/`untested`);
- pair activation (`census.activate_pair_concepts`) can only combine cores
  that predate the reseed; it cannot match the synthetic `reseed-` /
  `reseed-mut-` concept ids, so no new pair concepts arise from the seeded
  set;
- no worker authors decks (the deck-matrix refill path is retired, and the
  tournament loop breeds AGENTS, not decks).

This is accepted rather than fixed: the window is short, the diagnostics
(D1–D3) attribute the collapse to lineage quality rather than to insufficient
exploration breadth, and a champion that cannot clear the anchor gate from
these three proven templates would not be rescued by more deck variety. The
cost is explicit: if the seeded pool's ceiling is below the 0.55 anchor bar
(see §7's ceiling risk), the factory cannot explore its way out — re-opening
exploration requires a NEW migration, not a config change.

The mutation operator also caps the pool below its nominal size, measured
2026-08-03: `breeding.mutate_deck` draws from 4 deterministic rules that are
pure functions of the parent deck (the rng only permutes rule order), so each
template yields at most 4 distinct children — actually 3 / 3 / 2 here. The
reseeded pool is therefore **11 decks (3 templates + 8 mutations)**, not the
18 the `5 mutations/template` constant nominally implies; raising the attempt
budget cannot change this (verified at 8/12/20/40 attempts).

#### Go-live note: legacy in-flight offspring (added Pass 2)

The deck pool is not the only pre-slice state that survives go-live. Offspring
rows created BEFORE the floor/netcheck gates existed carry no evidence rows,
so the new gates would either wave them through or skip them permanently.
`run_reseed` therefore also reconciles in-flight offspring
(`_reconcile_legacy_offspring`), inside the same transaction, idempotently:

- `status='survivor'` with **no `floor_checks` row** → `'trashed'`. This is a
  candidate that reached CROWN eligibility without ever facing the anchor
  floor, i.e. the collapsed lineage the D1–D3 diagnostics condemned. Leaving
  it in place spends a full CROWN round-robin plus a 200-game anchor series
  (~400 games) re-discovering that it is junk.
- `status='queued_for_match'` with **no `net_checks` row** → back to
  `'training'` (the live `v0.10.1` case). `'training'` is the only status
  `netcheck.enqueue_net_check` will run for, so without this reset that
  offspring's net is never validated.
- Every other offspring row is left untouched — in particular a `'survivor'`
  that DOES carry a floor row (floored under the new rules, legitimately
  eligible).

Both UPDATEs are guarded on the status they move away from AND on the
absent-evidence-row condition, so a repeat run matches zero rows. `run_reseed`
reports `survivors_trashed` / `reset_to_training` alongside the pool counts;
the go-live rung should record both.

### 6. Live-exposure management

Per `.claude/rules/factory-resume-probe.md`, `ptcg-factory-runner` and
`ptcg-factory-scheduler` are long-lived, always-on worker processes. Because
this design changes the deck pool, the rating-game opponent selection, and
the generation-advancement gate ordering — all of which those workers read
and act on continuously — the `PAUSE` file must be touched **before**
implementation begins and held for the entire implementation window. This
prevents a scenario where a scheduled watchdog respawn (the workers run under
a 15-minute `MultipleInstances=IgnoreNew` watchdog trigger) picks up
half-written working-tree code mid-implementation.

Post-merge go-live is an explicit rung of this design, not an assumption:

1. Restart both workers explicitly (`Stop-ScheduledTask` followed by
   `Start-ScheduledTask` for `ptcg-factory-runner` and
   `ptcg-factory-scheduler`) — per the long-lived-worker-code-staleness rule,
   a `Running` task status is not evidence a long-lived worker has picked up
   a merge; only an explicit restart guarantees it.
2. Verify the restart actually took effect by checking that the next block or
   cycle's provenance stamp in `tournament.db` / `experiments/factory/logs/watch.log`
   carries a commit at or after this design's merge commit.
3. Only after that provenance check passes is the `PAUSE` file removed.

### 7. Success criteria & ceiling risk (honest framing)

**Upside outcome:** the factory crowns a champion that passes the 0.55/200
anchor gate and that champion is uploaded to the Kaggle ladder before the
2026-08-16 final-submission deadline.

**Acceptable honest outcome:** no future champion ever clears the anchor gate
under this design. In that case the gate simply keeps the existing rescue
pair (starmie-searchnet at 544.1, lucario-heuristic at 481.7) as the counted
Kaggle submissions, and this design's value is fully captured by the compute
savings in design 2 below — it does not require a passing champion to be
worth shipping.

**Ceiling risk**, identified during the D3 diagnostic and stated here
explicitly rather than glossed over: because the reseeded pool is built
around mega-lucario-fighting itself (design 5), a deck that merely mirrors
the anchor tops out at roughly a 0.50 win rate against it — below the 0.55
bar. Clearing the gate therefore requires a genuine improvement over the
anchor deck/agent combination, not just parity. A run of zero successful
uploads under this design is a possible and legitimate result of that ceiling
being real, not evidence the fix itself failed.

Either way, the early floor (design 2) delivers value independent of whether
any champion ultimately clears the crown gate: junk lineages are now
identified and culled after 50 games instead of surviving an entire
generation's worth of compute before failing the crown check.

## Constraints

- **Deadline:** the competition's final ladder submission window closes
  2026-08-16. Implementation of this design should complete within
  approximately 1–2 days so that meaningful autonomous tournament runtime
  remains before that date.
- **Ladder identity files are out of scope.** `src/ptcg/submission_main.py`
  and `src/ptcg/agents/current.py` are not touched by this design; the ladder
  continues running `HeuristicAgent` on mega-lucario-fighting regardless of
  what this design's tournament changes produce, unless and until a crowned
  champion actually clears the submission gate through the existing,
  unmodified submission pipeline.
- **Stochastic gate replication.** Per the global CLAUDE.md rule on
  stochastic acceptance gates near their bar, any gate result close to its
  threshold needs replication rather than a single run before being recorded
  as a PASS. The crown gate (0.55/200) is already replicated under the
  existing pipeline and stays that way. The new early floor was originally
  specified at 0.45/50 on the assumption it sat far below the field, since the
  collapsed lineage measured roughly 0.03–0.10 in the D1–D3 diagnostics. That
  assumption was **checked rather than assumed, and did not survive**: the
  2026-08-04 measurement (`uv run python
  scripts/measure_floor_distribution.py --games 100`; 11 reseeded decks x 100
  games vs the anchor, 1100 games) found the RESEEDED pool at **pooled wr
  0.471 [min 0.360, max 0.560]** — near parity with the anchor, not far above
  it. A 0.45 bar would therefore have sat inside the pool's own distribution
  and false-failed genuine parity decks ~40% per attempt. The bar was
  recalibrated to **0.40**, where junk-class decks still fail ~always but
  parity decks pass 84–96% per attempt. The D1–D3 numbers describe the culled
  lineage and must not be used to calibrate a gate applied to the reseeded
  pool.
- **Windows encoding.** Every `write_text` / `open` call touching a file in
  this design's code paths must pass `encoding="utf-8"` explicitly, per the
  standing project lesson that Windows' default cp1252 codec silently
  truncates-then-crashes on non-ASCII content.
- **Test coverage required for every new gate and ordering path**, including:
  - interleaved-mutation tests for any shared-store write introduced or
    modified by this design (per `.claude/rules/single-actor-worker-tests.md`
    and the `toctou-guard-in-step-functions` lesson), and
  - provenance-shape tests for any Optional field this design introduces or
    whose population now varies by code path (per
    `.claude/rules/provenance-shaped-optional-fields.md`).
