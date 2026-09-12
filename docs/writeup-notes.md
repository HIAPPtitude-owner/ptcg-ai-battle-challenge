# Writeup Notes - methodology journal (append-only)

Factory scripts log decisions with reasons; the weekly review adds strategic
prose. The Strategy report is an editing job over this file plus
experiments/LADDER.md and experiments/EXPERIMENTS.md.

## 2026-07-11T10:50 - factory cycle 2026-07-11

evaluated 2 candidate(s); actions: ['mega-lucario-fighting-heuristic-v1.0:dry-run']; digest: cycle-20260711-105018.md

## 2026-07-11T10:52 - factory cycle 2026-07-11

evaluated 2 candidate(s); actions: ['mega-lucario-fighting-heuristic-v1.0:upload-failed']; digest: cycle-20260711-105226.md

## 2026-07-11T11:10 - first real factory submission: mega-lucario-fighting-heuristic-v1.0 (ref 54585057)

Submitted `mega-lucario-fighting-heuristic-v1.0` (mega-lucario-fighting deck,
`HeuristicAgent`, local_wr 0.460/50) to Kaggle. The automated cycle
(digest cycle-20260711-105226.md) attempted this same candidate and failed
gate/upload with the reason recorded verbatim in that digest:

> mega-lucario-fighting-heuristic-v1.0: upload-failed - RuntimeError('kaggle
> CLI failed (1): ... 400 Client Error: Bad Request for url:
> https://api.kaggle.com/v1/competitions.CompetitionApiService/CreateSubmission')

Root cause: `submission_description()` used `|`/`/` separators, which
Kaggle's CreateSubmission API rejects in the `-m` description. Fixed in
commit `ee78d33` (` - `/`of` separators, same information content) and
verified via a manual CLI retry of the identical bundle, which succeeded
(ref 54585057, public score 600.0). A real read-only harvest cycle run
after the fix confirms `harvest.match_row()` correctly matches the
sanitized-format description against the candidate (matched=1,
scored_updates=1); `experiments/LADDER.md` now carries a MATCHED/counted
row for this candidate and `submission_counter.json` shows count 1 for
2026-07-11.

## 2026-07-11T11:15 - Slice 7A methodology summary

**Ladder-as-evaluator rationale.** Local arena win rate is a sanity filter,
not the judge: Slices 4-6 showed a stronger local evaluator (net AUC
0.897 -> higher) does not reliably translate to local arena win rate, and
the thing that ultimately matters for the Strategy/Simulation categories is
the Kaggle ladder's Gaussian rating, not an internal proxy. The factory
therefore treats every local eval (Section 3) as a cheap pre-filter and the
ladder (`experiments/LADDER.md`, harvested continuously) as the evaluator
of record. Divergence between local-WR ordering and ladder-score ordering
is itself signal, reconciled at the weekly review.

**Asymmetric-pairing test (T1/T2) - design and decision.** Pre-registered
before running: 3 asymmetric deck pairings x 2 seat orders x {heuristic-v0
control, search-net-v2 treatment} = 12 cells, 150 games/cell, with the
control cells isolating baseline pairing asymmetry so the treatment
differential is measured net of it. Decision criteria were locked before
the deciding data existed: a consistent positive pooled differential (CI
above 0) would mean search has an exploitable asymmetric edge and would
raise search-candidate priority + arm the 7B policy-improvement trainer
with confidence; flat/negative would strengthen the no-edge hypothesis and
seed search candidates at low, `exploratory`-flagged priority. Result:
**DECISION=FLAT** - pooled differential -0.0078, CI [-0.054, +0.038]
(spans 0). No exploitable search edge found on asymmetric pairings either.
Search candidates in the T4 seed pool are therefore priority 0.30,
`novel_axis`-flagged exploratory (per the Section-4 exploration exception -
the ladder still gets a say even at local parity). The no-edge hypothesis
across Slices 5-7A is now strengthened at every measurement taken; the 7B
policy-improvement loop (search-guided self-play, not just a better static
evaluator) is the last untested hypothesis for closing the gap. Full
detail: `experiments/ANALYSIS-slice7a-asymmetric-test.md`.

**Submission gate policy.** Better-than-incumbent gate by default: a
candidate uploads only when its local eval beats the incumbent (the weaker
of the two currently-counted ladder submissions). Exploration exception:
a candidate on a genuinely novel axis (first outing of a search config,
first outing of a new deck archetype) may submit at local parity, flagged
`exploratory`, because the ladder yields information the local arena
cannot. Cadence defaults to 2/day (roughly 24h of rating convergence per
counted slot) but is a rhythm, not a ceiling - a clearly-better candidate
submits immediately regardless of cadence. Hard cap is Kaggle's 5/day,
enforced by a local persistent UTC-anchored counter that survives restarts
(fixed in `298e6aa` after catching a local-time anchoring bug).

**Per-deck value nets - honest framing.** All value nets prior to Slice 7A
were trained exclusively on mega-lucario mirror data, so search candidates
on the other 8 field decks ran an off-distribution net. `PerDeckNetTrainer`
(T11/T12) is genuinely new signal along the distribution-match axis, NOT a
rerun of the evaluator-quality hypothesis Slices 4-6 already closed on the
mirror matchup - value-net-quality improvements are a proven dead end for
mirror win rate; the daemon's 7A value is (a) proving the continuous
producer-consumer training infrastructure end-to-end (CPU data generation
for net N+1 overlapping GPU training of net N) and (b) distribution
coverage for non-mirror decks. Two nets (`mega-starmie-water-searchnet`
v0.1/v0.2) were trained and registered by the acceptance run. The win-rate
hypothesis itself lives in 7B (`PolicyImprovementTrainer`, same `Trainer`
protocol, no factory rework needed to swap it in).

**Retired-approval-rule -> description-standard + digest + pause.** The
prior standing rule (Brad approves every submission description before it
uploads) is retired for this pipeline. Replacement: (a) a versioned-identity
description standard auto-generated from the candidate's durable name +
semantic version + deck + agent config, so every ladder score is forever
traceable to reproducible code without a human composing prose per
submission; (b) a digest (`experiments/factory/digests/`) written every
cycle that Brad can read at any time; (c) a `PAUSE` file Brad can drop to
halt the pipeline at will. The first real automated cycle (T13) exercised
this end to end and also surfaced a real defect in the description standard
itself (`|`/`/` characters rejected by Kaggle's CreateSubmission API,
fixed in `ee78d33`) - the digest + manual-retry path is exactly the safety
net this policy was designed to provide.

## 2026-07-12T02:00 - factory cycle 2026-07-12

evaluated 11 candidate(s); actions: ['mega-lucario-fighting-searchnet-v1.0:submitted', 'mega-starmie-water-searchnet-v1.0:submitted']; digest: cycle-20260712-020002.md

## 2026-07-13T02:00 - factory cycle 2026-07-13

evaluated 0 candidate(s); actions: none; digest: cycle-20260713-020002.md

## 2026-07-14T02:00 - factory cycle 2026-07-14

evaluated 0 candidate(s); actions: none; digest: cycle-20260714-020002.md

## 2026-07-14 - weekly review: recalibrate onto the starmie axis

**The evidence: local win rate inverts ladder score.**
`mega-starmie-water-searchnet-v1.0` scored **630.8** on the Kaggle ladder -
our best counted submission - beating every lucario variant (522-577),
*despite* a worse local win rate (0.473/150) and *despite* running the v2
value net that was trained exclusively on lucario-mirror data (off
distribution for a water deck). The starmie **heuristic** sibling had even
been retired locally at 0.433 "below the 0.45 floor". The local arena, which
graded every candidate on its mega-lucario-fighting matchup, was actively
mis-ranking decks relative to the judge that matters. Conclusion: **the deck
axis dominates**, the local single-baseline signal was a poor proxy, and the
starmie family is where the ladder says the signal is. The candidate queue is
empty (all 11 prior candidates scored or retired), so this review restocks it
deliberately around that finding.

**Decision 1 - author starmie-axis deck variants.** Five new decks along the
power-core-density thesis (Slice-3 insight: density beats mulligan-fix
padding). Mega Starmie ex's main attack, Jetting Blow, costs a single Water
energy for 120 damage + 50 bench spread (engine-verified), yet the baseline
deck ran **26** Water energy - roughly 12 more than the win condition needs.
The four heuristic variants thin energy and reinvest it: `-density20` (26->20
energy, +Buddy-Buddy Poffin, the conservative step closest to the 630.8
deck), `-lean` (18 energy, pure consistency, single attacker - the clean
density test), `-ogerpon` (16 energy + 2 Wellspring Mask Ogerpon ex, a Basic
Water secondary attacker that shares the energy base and pressures while
Staryu evolves), and `-turbo` (13 energy sustained by Energy Recycler + max
search - the aggressive density probe). The 20/18/16/13 energy gradient is
intentional experimental design: if density helps, the ladder should show it
monotonically; if the aggressive cut breaks the deck, the conservative step
still tests the thesis. Each deck is engine-validated (60 cards, <=4 per name,
<=1 ACE SPEC) and passed a 20-game non-degeneracy smoke vs lucario-v0
(45-55%, noise-level as expected - local WR is a known-poor proxy here, so a
mid-40s smoke is a pass, not a warning). Two searchnet variants (`-lean`,
`-ogerpon` x the v2-net search config) probe whether search adds anything on
the axis the ladder actually rewards.

**Decision 2 - dual-baseline local eval.** The root cause of the inversion
was structural: grading only against mega-lucario-fighting meant a strong
non-lucario deck was scored purely on its worst matchup. The eval runner now
pools wins across a configurable **list** of baselines (mega-lucario-fighting
+ mega-starmie-water by default), so no single archetype's matchup can sink a
candidate the ladder would reward. Baselines are a list, not a hardcoded
pair, so the field can grow as the ladder teaches us which decks matter.

**Decision 3 - weaker-slot submission gate.** The better-than-incumbent gate
compares against the **weaker** of the two currently-counted ladder slots, so
a new candidate that would improve our worst counted submission can ship even
if it does not beat our best. This keeps the two-slot ladder portfolio
climbing rather than stalling once one strong submission is in place.

**Decision 4 - deprioritize the lucario family.** Every lucario-family
candidate (heuristic + searchnet, all versions) is dropped to priority <=0.20
in the ledger with the reason "ladder-confirmed exhausted line". Their scores
and history are preserved (mega-lucario-fighting-heuristic 574.3,
mega-lucario-fighting-searchnet 522.1) - this is a queue-ordering decision,
not a deletion, and it does **not** touch the pinned ladder identity
(`submission_main.py` stays HeuristicAgent + mega-lucario-fighting). Four
Slices of evaluator-quality and asymmetry work found no per-decision edge on
the lucario mirror; the ladder now confirms the lucario decks themselves
top out around 520-577 while a barely-tuned starmie deck reached 630.8.
Compute is better spent widening the starmie axis than re-tuning a line the
ladder has already ranked.

**Why this matters for the report.** This is the first time the pipeline let
the ladder overrule the local arena on a strategic decision, and the ladder
was right - a concrete instance of "the ladder is the evaluator of record,
local eval is a sanity filter". The deck-axis-dominates finding is the
methodology story: model/search sophistication (Slices 4-6) moved the needle
far less than deck selection (this review), which is exactly the 20%-deck /
70%-methodology tension the Strategy judging encodes.

## 2026-07-15T02:00 - factory cycle 2026-07-15

evaluated 6 candidate(s); actions: ['mega-starmie-water-lean-searchnet-v1.0:skip', 'mega-starmie-water-lean-heuristic-v1.0:skip', 'mega-starmie-water-ogerpon-heuristic-v1.0:skip', 'mega-starmie-water-turbo-heuristic-v1.0:skip', 'mega-starmie-water-searchnet-v1.0:resubmitted-champion', 'mega-starmie-water-density20-heuristic-v1.0:submitted']; digest: cycle-20260715-020002.md

## 2026-07-16T02:00 - factory cycle 2026-07-16

evaluated 0 candidate(s); actions: ['mega-starmie-water-density20-heuristic-v1.0:resubmitted-champion', 'mega-starmie-water-lean-searchnet-v1.0:submitted']; digest: cycle-20260716-020002.md

## 2026-07-17T02:00 - factory cycle 2026-07-17

evaluated 0 candidate(s); actions: none; digest: cycle-20260717-020002.md

## 2026-07-17T08:03 - factory cycle 2026-07-17

evaluated 0 candidate(s); actions: none; digest: cycle-20260717-080358.md

## 2026-07-19T21:30 - factory cycle 2026-07-20

evaluated 10 candidate(s); actions: ['mega-starmie-water-lean-energy-down2-heuristic-v0.1:skip', 'mega-lucario-fighting-energy-down2-heuristic-v0.1:skip', 'mega-starmie-water-density20-attacker-down1-heuristic-v0.1:skip', 'mega-starmie-water-density20-energy-up2-heuristic-v0.1:skip', 'mega-starmie-water-energy-down2-heuristic-v0.1:skip', 'mega-starmie-water-energy-up2-heuristic-v0.1:skip', 'mega-starmie-water-lean-energy-up2-heuristic-v0.1:skip', 'mega-starmie-water-density20-heuristic-v1.0:resubmitted-champion', 'mega-starmie-water-lean-attacker-down1-heuristic-v0.1:submitted']; digest: cycle-20260719-213002.md

## 2026-07-19T21:45 - factory cycle 2026-07-20

evaluated 1 candidate(s); actions: none; digest: cycle-20260719-214501.md

## 2026-07-19T22:00 - factory cycle 2026-07-20

evaluated 1 candidate(s); actions: ['mega-starmie-water-lean-attacker-down1-searchnet-v0.1:skip']; digest: cycle-20260719-220038.md

## 2026-07-19T22:15 - factory cycle 2026-07-20

evaluated 1 candidate(s); actions: ['mega-starmie-water-lean-attacker-down1-searchnet-b500-v0.1:skip']; digest: cycle-20260719-221501.md

## 2026-07-19T22:45 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260719-224500.md

## 2026-07-19T23:00 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260719-230001.md

## 2026-07-19T23:15 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260719-231501.md

## 2026-07-19T23:30 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260719-233001.md

## 2026-07-19T23:45 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260719-234501.md

## 2026-07-20T00:15 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-001501.md

## 2026-07-20T00:45 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-004501.md

## 2026-07-20T01:15 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-011501.md

## 2026-07-20T01:30 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-013001.md

## 2026-07-20T01:45 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-014501.md

## 2026-07-20T02:00 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-020001.md

## 2026-07-20T03:00 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-030001.md

## 2026-07-20T03:15 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-031501.md

## 2026-07-20T04:45 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-044501.md

## 2026-07-20T05:45 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-054501.md

## 2026-07-20T06:00 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-060001.md

## 2026-07-20T06:30 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-063001.md

## 2026-07-20T07:00 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-070001.md

## 2026-07-20T07:45 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-074501.md

## 2026-07-20T08:00 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-080001.md

## 2026-07-20 - weekly review: re-key the gate onto the ladder-best line

**The convergence rule discovered this week.** With enough ladder history we
can now see how a rating actually settles: every line with >=10 refreshes
converges into a **520-575 band**, and every 600+ score we ever celebrated
was an early-refresh transient that decayed back into the band. The 650.5 and
665.5 spikes, and even last week's 630.8 starmie-searchnet high, all misled -
they were provisional Gaussian estimates on 1-3 games that regressed as the
sample filled in (starmie-searchnet drifted 630.8 -> 569.5 -> 567.2 over the
following days). **Operating rule going forward: judge a line only on its
>=10-refresh converged band, never on a fresh-upload transient.** A new
submission's first-day number is noise; wait for convergence before ranking.

**The local-vs-ladder inversion, sharpened - and the gate re-key it forced.**
The dual-baseline eval fixed last week did not fix the deeper problem: local
win rate and converged ladder score point in *opposite* directions on this
axis. `density20` (local_wr **0.647**, our highest) converged to only **~525**
on the ladder. `lean-attacker-down1` (local_wr **0.553**, materially lower)
sits at **571.6 and climbing** - the best converged line we have ever had.
The factory's submission gate keys its "beat-this bar" off local_wr, and the
auto counted-pair rule was pinning that bar to density20's 0.647 - i.e. the
gate was demanding new candidates beat the *worst* converged ladder deck's
inflated local score, gating out exactly the lean line the ladder rewards.
**Decision: re-key the incumbent to `lean-attacker-down1` (bar = its local_wr
0.553).** Implemented as an explicit, legible `is_incumbent` designation flag
on the candidate (gate.incumbent() honors it, decoupled from the volatile
counted pair) rather than by falsifying density20's recorded 0.647 or retiring
it. The flag is the durable record of *which line the ladder says is the bar*,
and is trivially re-pointed at the next review.

**Queue refill around the ladder-best line - and no retirements this week.**
The queue was empty (factory idle since ~03:00), so this review restocks it
deliberately around `lean-attacker-down1`. Added it as a new deck-matrix SEED
so future auto-refills mutate around it, and registered four 2nd-order
candidates via the legality-validated mutation machinery: `-attacker-down2`
(a further attacker cut, highest priority), `-energy-up2` and `-energy-down2`
(the density gradient one step either side; the mutation machinery only
supports +/-2 energy steps, so the intended "energy +/-1" is realized as the
+/-2 rules), and `lean-attacker-down1-searchnet` v0.2 using today's freshly
trained per-deck value-net weights (highest priority - genuinely new signal on
the distribution-match axis). **No retirements this week - deliberate:** the
lucario family (fighting-heuristic 574.3, fighting-searchnet 533.1) stays in
the ledger for one more week of ladder evidence before we cut anything. One
more week is cheap; a premature retirement of a line that later converges high
is not.

**Checklist step 10 note - convergence-freeze timing.** Refreshes arrive fast:
a fresh upload picks up ~10-20 refreshes within a day, so a line reaches its
converged band in roughly **2 days**. That means the champion-freeze before
the **2026-08-16** final-submission deadline needs only about a **2-day
buffer**, not a week - a candidate submitted as late as ~08-13/08-14 still has
time to converge. Set the exact champion-freeze date at the early-August
review, once the current lean-line mutations have converged and shown whether
any beats 571.6.

## 2026-07-20T09:15 - factory cycle 2026-07-20

evaluated 4 candidate(s); actions: ['mega-starmie-water-lean-attacker-down1-energy-up2-heuristic-v0.1:skip', 'mega-starmie-water-lean-attacker-down1-searchnet-v0.2:skip', 'mega-starmie-water-lean-attacker-down2-heuristic-v0.1:skip']; digest: cycle-20260720-091500.md

## 2026-07-20T09:30 - factory cycle 2026-07-20

evaluated 2 candidate(s); actions: ['mega-starmie-water-lean-attacker-down1-heuristic-v0.1:resubmitted-champion', 'mega-starmie-water-lean-attacker-down1-attacker-up1-heuristic-v0.1:upload-failed']; digest: cycle-20260720-093001.md

## 2026-07-20T09:45 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: ['mega-starmie-water-lean-attacker-down1-heuristic-v0.1:upload-failed', 'mega-starmie-water-lean-attacker-down1-attacker-up1-heuristic-v0.1:skip']; digest: cycle-20260720-094501.md

## 2026-07-20T11:15 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: ['mega-starmie-water-lean-attacker-down1-heuristic-v0.1:resubmitted-champion', 'mega-starmie-water-lean-attacker-down1-attacker-up1-heuristic-v0.1:submitted']; digest: cycle-20260720-111502.md

## 2026-07-20T11:30 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-113001.md

## 2026-07-20T11:45 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-114501.md

## 2026-07-20T12:00 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-120001.md

## 2026-07-20T12:15 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-121501.md

## 2026-07-20T13:30 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-133001.md

## 2026-07-20T13:45 - factory cycle 2026-07-20

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-134502.md

## 2026-07-20T14:00 - factory cycle 2026-07-21

evaluated 1 candidate(s); actions: ['mega-lucario-fighting-searchnet-v1.1:skip']; digest: cycle-20260720-140039.md

## 2026-07-20T14:15 - factory cycle 2026-07-21

evaluated 1 candidate(s); actions: ['mega-lucario-fighting-searchnet-b500-v0.1:skip']; digest: cycle-20260720-141501.md

## 2026-07-20T15:15 - factory cycle 2026-07-21

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-151503.md

## 2026-07-20T15:30 - factory cycle 2026-07-21

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-153003.md

## 2026-07-20T16:30 - factory cycle 2026-07-21

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-163001.md

## 2026-07-20T17:00 - factory cycle 2026-07-21

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-170001.md

## 2026-07-20T18:45 - factory cycle 2026-07-21

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-184501.md

## 2026-07-20T19:30 - factory cycle 2026-07-21

evaluated 0 candidate(s); actions: none; digest: cycle-20260720-193003.md

## 2026-07-20T20:45 - factory cycle 2026-07-21

evaluated 0 candidate(s); actions: ['mega-starmie-water-energy-up2-searchnet-v0.1:skip']; digest: cycle-20260720-204501.md

## 2026-07-20T21:00 - factory cycle 2026-07-21

evaluated 0 candidate(s); actions: ['mega-starmie-water-lean-attacker-down1-attacker-down1-searchnet-v0.1:skip']; digest: cycle-20260720-210001.md

## 2026-07-20T23:15 - factory cycle 2026-07-21

evaluated 0 candidate(s); actions: ['AUTH:auth-dead']; digest: cycle-20260720-231500.md

## 2026-07-20T23:30 - factory cycle 2026-07-21

evaluated 0 candidate(s); actions: ['AUTH:auth-dead']; digest: cycle-20260720-233000.md

## 2026-07-21T07:00 - factory cycle 2026-07-21

evaluated 0 candidate(s); actions: ['AUTH:auth-dead']; digest: cycle-20260721-070001.md

## 2026-07-22T09:30 - factory cycle 2026-07-22

evaluated 0 candidate(s); actions: ['mega-starmie-water-lean-energy-up2-searchnet-v0.1:skip', 'mega-starmie-water-lean-energy-down2-searchnet-v0.1:skip', 'mega-starmie-water-turbo-searchnet-v0.1:skip', 'mega-starmie-water-lean-attacker-down1-energy-down2-searchnet-v0.1:skip', 'mega-starmie-water-lean-attacker-down2-searchnet-v0.1:skip', 'mega-starmie-water-lean-attacker-down1-attacker-up1-heuristic-v0.1:resubmitted-champion', 'mega-starmie-water-lean-attacker-down1-attacker-up1-searchnet-v0.1:submitted']; digest: cycle-20260722-093005.md

## 2026-07-22T09:45 - factory cycle 2026-07-22

evaluated 0 candidate(s); actions: ['mega-starmie-water-lean-energy-down2-searchnet-v0.1:skip', 'mega-starmie-water-turbo-searchnet-v0.1:skip', 'mega-starmie-water-lean-attacker-down1-energy-down2-searchnet-v0.1:skip', 'mega-starmie-water-lean-attacker-down1-attacker-up1-searchnet-v0.1:resubmitted-champion', 'mega-starmie-water-lean-energy-up2-searchnet-v0.1:submitted']; digest: cycle-20260722-094506.md

## 2026-07-22T10:00 - factory cycle 2026-07-22

evaluated 0 candidate(s); actions: ['mega-starmie-water-turbo-searchnet-v0.1:skip', 'mega-starmie-water-lean-attacker-down1-energy-down2-searchnet-v0.1:skip', 'mega-starmie-water-lean-energy-down2-searchnet-v0.1:skip']; digest: cycle-20260722-100010.md

## 2026-07-22T10:15 - factory cycle 2026-07-22

evaluated 0 candidate(s); actions: ['mega-starmie-water-turbo-searchnet-v0.1:skip', 'mega-starmie-water-lean-attacker-down1-energy-down2-searchnet-v0.1:skip', 'mega-starmie-water-lean-energy-down2-searchnet-v0.1:skip']; digest: cycle-20260722-101505.md

## 2026-07-22T10:30 - factory cycle 2026-07-22

evaluated 0 candidate(s); actions: ['mega-starmie-water-turbo-searchnet-v0.1:skip', 'mega-starmie-water-lean-attacker-down1-energy-down2-searchnet-v0.1:skip', 'mega-starmie-water-lean-energy-down2-searchnet-v0.1:skip']; digest: cycle-20260722-103005.md

## 2026-07-22T10:45 - factory cycle 2026-07-22

evaluated 0 candidate(s); actions: ['mega-starmie-water-turbo-searchnet-v0.1:skip', 'mega-starmie-water-lean-attacker-down1-energy-down2-searchnet-v0.1:skip', 'mega-starmie-water-lean-energy-down2-searchnet-v0.1:skip']; digest: cycle-20260722-104506.md

## 2026-07-22T11:00 - factory cycle 2026-07-22

evaluated 0 candidate(s); actions: ['mega-starmie-water-turbo-searchnet-v0.1:skip', 'mega-starmie-water-lean-attacker-down1-energy-down2-searchnet-v0.1:skip', 'mega-starmie-water-lean-energy-down2-searchnet-v0.1:skip']; digest: cycle-20260722-110007.md

## 2026-07-22T11:15 - factory cycle 2026-07-22

evaluated 0 candidate(s); actions: ['mega-starmie-water-turbo-searchnet-v0.1:skip', 'mega-starmie-water-lean-attacker-down1-energy-down2-searchnet-v0.1:skip', 'mega-starmie-water-lean-energy-down2-searchnet-v0.1:skip']; digest: cycle-20260722-111505.md

## 2026-07-22T11:30 - factory cycle 2026-07-22

evaluated 0 candidate(s); actions: ['mega-starmie-water-turbo-searchnet-v0.1:skip', 'mega-starmie-water-lean-attacker-down1-energy-down2-searchnet-v0.1:skip', 'mega-starmie-water-lean-energy-down2-searchnet-v0.1:skip']; digest: cycle-20260722-113006.md

## 2026-07-22T11:45 - factory cycle 2026-07-22

evaluated 0 candidate(s); actions: ['mega-starmie-water-turbo-searchnet-v0.1:skip', 'mega-starmie-water-lean-attacker-down1-energy-down2-searchnet-v0.1:skip', 'mega-starmie-water-lean-energy-down2-searchnet-v0.1:skip']; digest: cycle-20260722-114505.md

## 2026-07-22T12:00 - factory cycle 2026-07-22

evaluated 0 candidate(s); actions: ['mega-starmie-water-turbo-searchnet-v0.1:skip', 'mega-starmie-water-lean-attacker-down1-energy-down2-searchnet-v0.1:skip', 'mega-starmie-water-lean-energy-down2-searchnet-v0.1:skip']; digest: cycle-20260722-120013.md

## 2026-07-22T12:15 - factory cycle 2026-07-22

evaluated 0 candidate(s); actions: ['mega-starmie-water-turbo-searchnet-v0.1:skip', 'mega-starmie-water-lean-attacker-down1-energy-down2-searchnet-v0.1:skip', 'mega-starmie-water-lean-energy-down2-searchnet-v0.1:skip']; digest: cycle-20260722-121506.md

## 2026-07-22T12:30 - factory cycle 2026-07-22

evaluated 0 candidate(s); actions: ['mega-starmie-water-turbo-searchnet-v0.1:skip', 'mega-starmie-water-lean-attacker-down1-energy-down2-searchnet-v0.1:skip', 'mega-starmie-water-lean-energy-down2-searchnet-v0.1:skip']; digest: cycle-20260722-123014.md

## 2026-07-22T13:00 - factory cycle 2026-07-22

evaluated 0 candidate(s); actions: none; digest: cycle-20260722-130005.md

## 2026-07-22T14:00 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260722-140036.md

## 2026-07-22T14:17 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260722-141730.md

## 2026-07-22T14:34 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260722-143434.md

## 2026-07-22T15:15 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260722-151506.md

## 2026-07-22T17:00 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260722-170009.md

## 2026-07-22T17:45 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260722-174506.md

## 2026-07-22T18:30 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260722-183005.md

## 2026-07-22T18:45 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260722-184506.md

## 2026-07-22T19:15 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260722-191506.md

## 2026-07-22T21:00 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260722-210006.md

## 2026-07-22T21:30 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260722-213005.md

## 2026-07-22T22:15 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260722-221506.md

## 2026-07-22T22:30 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260722-223005.md

## 2026-07-23T01:00 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-010005.md

## 2026-07-23T01:15 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-011504.md

## 2026-07-23T02:45 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-024504.md

## 2026-07-23T03:30 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-033004.md

## 2026-07-23T05:00 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-050005.md

## 2026-07-23T06:15 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-061505.md

## 2026-07-23T07:00 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-070005.md

## 2026-07-23T07:30 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-073005.md

## 2026-07-23T08:00 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-080007.md

## 2026-07-23T08:30 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-083005.md

## 2026-07-23T09:15 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-091505.md

## 2026-07-23T11:30 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-113018.md

## 2026-07-23T12:00 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-120006.md

## 2026-07-23T12:30 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-123011.md

## 2026-07-23T12:45 - factory cycle 2026-07-23

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-124508.md

## 2026-07-23T14:00 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-140006.md

## 2026-07-23T16:00 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-160005.md

## 2026-07-23T16:30 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-163005.md

## 2026-07-23T17:00 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-170005.md

## 2026-07-23T17:15 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-171505.md

## 2026-07-23T19:15 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-191505.md

## 2026-07-23T20:15 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-201505.md

## 2026-07-23T20:45 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-204505.md

## 2026-07-23T22:45 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-224505.md

## 2026-07-23T23:00 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260723-230005.md

## 2026-07-24T02:15 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260724-021505.md

## 2026-07-24T02:45 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260724-024505.md

## 2026-07-24T04:15 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260724-041505.md

## 2026-07-24T05:00 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260724-050005.md

## 2026-07-24T05:30 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260724-053005.md

## 2026-07-24T05:45 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260724-054505.md

## 2026-07-24T06:45 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260724-064504.md

## 2026-07-24T07:30 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260724-073005.md

## 2026-07-24T08:45 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260724-084509.md

## 2026-07-24T09:15 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260724-091505.md

## 2026-07-24T10:30 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260724-103014.md

## 2026-07-24T10:45 - factory cycle 2026-07-24

evaluated 0 candidate(s); actions: none; digest: cycle-20260724-104509.md
