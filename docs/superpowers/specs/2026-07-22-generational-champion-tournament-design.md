# Generational Champion Tournament — Design

**Date:** 2026-07-22
**Status:** Approved (Brad, 2026-07-22 via AskUserQuestion) — pending spec review
**Amended:** 2026-07-22 (same-day, second brainstorm round) — cadence
redesign: the discrete generation state machine and the Bo1001 grand final
are replaced by a continuous baseline-challenge loop; the trainer's
finalist-only retargeting and the per-generation single-upload submission
model are likewise replaced. Superseded content is marked, not deleted, in
Locked Decisions below; the replacement mechanics live in the new
Baseline-Challenge Loop and Submission Scheduler sections. Prior state is
preserved in git history (commits `d5fd979`, `333f6e1`).
**Feature slice:** generational-champion-tournament
**Supersedes:** steady-state co-evolution (evolutionary-agent-population, merged `45ee1d5`)

## Context & Motivation

The evolutionary-agent-population slice shipped a continuous, unattended
co-evolution loop: two genome pools bred inside the matrix worker's locked
tick, gated by two-factor Bradley-Terry fitness, submitting whichever
(agent, deck) cell rated best. It runs; Rung 4 (48-hour throughput review)
is due 2026-07-23.

Brad wants a different model: a single lineage and human supervision over
which decks stay in the field. This spec replaces continuous co-evolution
with a **generational champion tournament** — a process that starts from
every buildable single-Pokemon deck concept, continuously screens and
rates the deck field, and breeds challengers against the reigning champion.
The original design crowned one winner per fixed-length generation and
uploaded once per generation; that cadence was redesigned same-day (see
Locked Decisions supersession notes) into a **continuous baseline-challenge
loop** that crowns a new baseline the moment a challenger proves itself in
confirmation play, with submissions on their own independent clock.

**Deadlines:** Kaggle entry 2026-08-09, finals 2026-08-16.

**Submission state:** `SUBMIT_HOLD` (merged `d373e7e`) is ACTIVE. No Kaggle
uploads happen until this design's Submission Scheduler goes live (see the
new Submission Scheduler section and decision 12's go-live sequence). The
current counted ladder pair — including the 724.1-scoring searchnet
submission — is safe while the hold is on; nothing in this spec touches it
before cutover.

## Locked Decisions

Each decision below was approved by Brad on 2026-07-22 with its trade-off
stated explicitly. Some were superseded later the same day in a second
brainstorm round (cadence redesign); those are marked inline with the
original text retained for audit trail, per project convention of not
deleting superseded design history.

1. **ISMCTS agents only. No heuristic agent anywhere in the tournament.**
   Search games cost ~10x a heuristic game (per Slices 4-7B measurements,
   ~23-32 s/game search-vs-search vs ~4.3 s/game heuristic-heavy). Brad
   re-affirmed this after being shown the cost. Consequence: a parallel
   game-runner pool is a prerequisite, not an optimization — single-threaded
   throughput cannot clear the census in a useful time.

2. **One Kaggle upload per generation — SUPERSEDED 2026-07-22 (Brad cadence
   redesign).** Original text retained for audit trail:

   > One Kaggle upload per generation: the crowned winner only. No champion
   > re-upload, no champion-pairing guard. Brad was shown twice that Kaggle
   > counts only the two most-recently-submitted submissions and evicts by
   > recency, not score — there is no way to pin a good submission in the
   > counted pair. He explicitly accepted that a champion two generations
   > old falls out of the counted pair with no republish. The
   > champion-pairing recency-eviction guard (`src/ptcg/factory/gate.py`,
   > built in the 2026-07-14 weekly review per
   > `.claude/rules/platform-mechanics-model.md`) is retired at cutover —
   > it exists to keep a champion pair alive across multiple submissions,
   > and this design deliberately submits only one agent per generation.

   **Replacement:** the Submission Scheduler (see new section below) —
   uploads fire on a 4.8-hour mark whenever the current baseline differs
   from the last-uploaded identity, plus a 24-hour daily-floor probe upload
   that does not bump the version. The underlying Kaggle recency-eviction
   fact this decision was built on is **unchanged** and still governs the
   new model: there is still no way to pin a good submission in the counted
   pair, a champion superseded by a later crown (or displaced by a probe)
   still falls out with no republish, and the champion-pairing guard stays
   retired for the same reason as originally decided — neither model
   attempts to keep a specific champion pair alive across multiple
   submissions.

3. **Staged series budgets, spending certainty only where it crowns:**
   - Screening: ~40 games/deck, pooled into Bradley-Terry ratings. The
     Founding Census (decision 4) is a one-time exception at ~15
     games/concept — coarser because it screens all 815 single-core
     concepts at once; every subsequent screening (there is no longer a
     "later generation's smaller survivor/pair pool" boundary — screening
     is continuous under the Baseline-Challenge Loop) runs at the standard
     ~40. This continuously-maintained rating is what the Baseline-Challenge
     Loop's MATCH step reads as "current field rating" to pick each
     offspring's top-30 opponent pool.
   - Playoffs: ~200 games/pairing. This budget carries forward unchanged
     into the Baseline-Challenge Loop's CONFIRM series and CROWN survivor
     round-robin (see new section below) — only the ceremony built around
     it (the Grand Final, next bullet) changed.
   - ~~Grand Final: best-of-1001, first to 501 wins.~~ **SUPERSEDED
     2026-07-22 (Brad cadence redesign):** replaced by a ~200-game CONFIRM
     series (<50% measured win rate -> TRASHED, >=50% -> SURVIVOR) plus a
     survivor round-robin CROWN, backstopped by continuous post-crown
     dethronement pressure — every future offspring's CONFIRM step
     challenges the reigning baseline — instead of a single
     high-confidence coronation. Accepted statistical trade, recorded
     explicitly: roughly ±7%-class verdicts per crown instead of Bo1001's
     ±3%, in exchange for ~half a day less compute per crown and a champion
     that stays under continuous challenge rather than being insulated for
     an entire generation.

   Rationale (as originally decided, for the now-superseded Bo1001 choice):
   a Bo11 series gives an equal (50%-true) challenger a 50% series-win
   chance, and gives a 55%-true challenger only ~61% — not enough
   confidence to crown. Bo1001 gives a 55%-true challenger >99.9%. The
   staged shape (cheap screening, medium playoffs, expensive final) spent
   games proportional to how much a wrong call would cost. See the
   Baseline-Challenge Loop section below for the replacement rationale
   (continuous dethronement pressure as the compensating control).

4. **Deck space: Founding Census + enumerated concepts, not random
   generation.** Full 60-card deck enumeration is not computationally
   reachable: under Brad's construction bounds (<=20 energy cards, >=8 basic
   Pokemon, >=4 supporters, >=4 items, exactly 1 ACE SPEC), the exact legal
   deck count from the competition's card pool is **3.4107e103** — computed
   directly from the pool, not estimated. Two supporting facts, both
   computed as design evidence:
   - Lexicographic-prefix demonstration: deck #10^18 in a lexicographic
     enumeration still shares 49 of 60 cards with deck #1 — sequential
     enumeration cannot cover meaningfully different decks inside any
     reachable game budget.
   - Uniform-random legal decks sampled under the same bounds average only
     ~58 distinct card names per deck (near-singleton piles) — random
     sampling does not produce strategically coherent decks either.

   Instead: the pool's **815 attacker Pokemon** (442 basic + 373 evolution,
   each with >=1 damaging attack) are enumerated as single-core deck
   concepts, **all present in the database on day 0**. BOOTSTRAP (this
   design's one-time seed step — formerly labeled "Generation 1" under the
   superseded discrete-generation framing; see the new Baseline-Challenge
   Loop section) is the **Founding Census**: every one of the 815 gets a
   deterministic-builder deck and ~15 screening games, run to full 815/815
   completion before any crowning (census time-box: ~7 days). This
   mechanic is unchanged by the 2026-07-22 cadence redesign. After
   BOOTSTRAP, continuously (formerly labeled "Generation 2+", with no
   generation boundary batching this work anymore): survivor **shell
   variants** (3-5 deterministic variants per surviving single core) plus
   **pair concepts** (two-core decks, C(815,2) = 331,705 combinations,
   enumerated lazily as individual cores prove out) at ~40 games each.
   Concepts that cannot be legally built under Brad's bounds are marked
   `UNBUILDABLE` with a recorded reason — never silently dropped from the
   database.

   **Deterministic builder:** a pure function `concept -> deck`. Given one
   or two core Pokemon, it fills the deck with: the core's evolution
   line(s), cost-matched basic energy for the core's attack costs, and a
   standard trainer skeleton satisfying Brad's bounds (>=4 supporters, >=4
   items, exactly 1 ACE SPEC, <=20 energy cards, >=8 basic Pokemon total).
   Zero randomness. No generator, no RNG seed — the same concept always
   produces the same 60-card list byte-for-byte.

5. **Versioning (updated 2026-07-22, Brad cadence redesign — mechanics
   unchanged, "generation" framing replaced with continuous framing):**
   base agent starts at `v0.1`. Offspring produced by the Baseline-Challenge
   Loop's TRAIN step while `v0.1` is the sitting baseline are numbered
   `v0.1.1 .. v0.1.k` continuously as they complete training — not batched
   by a closing generation boundary, since none exists under the new loop
   (see new section below). The moment CROWN produces a new baseline it is
   promoted to `v0.2`; its own offspring are numbered `v0.2.1 ..`, and so
   on — the minor version still increments once per crowning; the patch
   number still enumerates that baseline's challenger pool, just
   accumulated continuously instead of generation-batched.

6. **Parallel game-runner pool:** 4-8 worker processes at BelowNormal
   process priority, the sole game engine for this design (the matrix
   worker's tournament role is retired). Target throughput: ~1,500-2,500
   ISMCTS games/day. This target must be validated against real hardware
   early in Phase 1 — see Calendar & Risks. (This is also the throughput
   figure the Baseline-Challenge Loop's ~1-2-day cycle-time estimate
   depends on.)

7. **UI (Brad's exact refinement — quoted faithfully):** every 15 minutes
   the UI shows the **lowest 10 decks** (by rating, among active tested
   decks meeting a minimum-games floor), each expandable to show the full
   60-card deck list, with per-deck **Remove** / **Pass** buttons.
   - **Remove:** permanently retired from the active field going forward.
     The row is kept forever in the database as culled history — never
     deleted.
   - **Pass:** stays active; may re-list at a later 15-minute check-in if
     it is still in the bottom 10 by then.
   - All removals go through this UI and are Brad-approved. Culling is
     **advisory pace, not a gate**: the tournament progresses regardless of
     how fast Brad reviews. The scheduler prioritizes games by rating
     (worst-rated decks get tested fastest, surfacing them for review
     sooner); culls save compute by retiring decks early, but nothing waits
     on a Remove/Pass decision to keep running.
   - Requires a new localhost-only UI server: `ptcg-factory-ui` (Windows
     Scheduled Task), bound to `127.0.0.1` only, built on Python's stdlib
     `http.server`. It serves the live Remove/Pass review page (not to be
     confused with the existing `experiments/factory/dashboard.html` status
     dashboard — see Workers & Cutover for that dashboard's disposition)
     and accepts Remove/Pass POSTs, writing decisions atomically for the
     worker pool to read on its next tick.

8. **Time-boxed generations — SUPERSEDED 2026-07-22 (Brad cadence
   redesign).** Original text retained for audit trail:

   > a config value `generation_days` (default 4) bounds every generation
   > after the census (census default ~7 days, see decision 4). When the
   > box closes, the crown is contested among whatever is tested at that
   > point — the tournament does not wait for exhaustive coverage of the
   > generation's concept pool. The census is the one exception: it always
   > runs to 815/815 completion regardless of the time box, because it
   > seeds every subsequent generation's survivor pool.

   **Replacement:** there is no generation to time-box under the
   continuous Baseline-Challenge Loop — no `generation_days` config exists.
   Cycle time (roughly 1-2 days per crown) is emergent from game-runner
   throughput, not a configured boundary that forces a crown before
   coverage is ready. The census exception survives unchanged: BOOTSTRAP
   (the Founding Census) still always runs to 815/815 completion regardless
   of any clock, per decision 4, because it still seeds every concept the
   loop screens afterward.

9. **Deck database: SQLite**, via Python's stdlib `sqlite3`. This is a
   deliberate departure from the repo's established JSON-ledger convention
   (`candidates.json`, `matrix.json`, `agent_pool.json`, `deck_pool.json`):
   332,520+ concept rows (815 singles + up to 331,705 lazily-enumerated
   pairs) cannot be atomically rewritten as a single JSON document on every
   write the way the smaller ledgers are. This is recorded here as an
   accepted convention change for this subsystem only — existing JSON
   ledgers (candidates.json, LADDER.md provenance, etc.) are unaffected.

   Schema sketch:
   - `concepts(id, cores, status, builder_version)` — `status` in
     `{untested, active, culled, unbuildable, finalist}`.
   - `decks(id, concept_id, cards, shell_variant)` — the built 60-card list
     for a concept (a concept may have multiple deck rows across shell
     variants).
   - `games(id, deck_a_id, deck_b_id, agent_version_a, agent_version_b,
     winner, timestamp)` — full win/loss history.
   - `decisions(id, deck_id, action, actor, timestamp)` — Brad's Remove/Pass
     log, `action` in `{remove, pass}`.
   - `coverage(concept_id, games_played, distinct_opponents, rating)` —
     denormalized stats for scheduler and UI queries.

   **Extended 2026-07-22 (Brad cadence redesign)** to also persist
   Baseline-Challenge Loop state (see new section below), since the loop
   has no separate ledger of its own:
   - `offspring(id, parent_baseline_version, search_config_json,
     value_net_ref, status, created_at)` — `status` in `{training,
     queued_for_match, matching, confirming, trashed, survivor}`.
   - `baselines(version, offspring_id, deck_id, crowned_at)` — one row per
     crowned baseline in ascending version order; `offspring_id` is `NULL`
     for the founding `v0.1`.

10. **Trainer worker retargeted to finalist decks only — SUPERSEDED
    2026-07-22 (Brad cadence redesign).** Original text retained for audit
    trail:

    > per-deck value-net training runs only for **finalist decks** (the
    > top handful surviving into a generation's playoff/championship
    > stage), not the full census field. The 815-deck census field never
    > gets individual value nets — training compute would be wasted on
    > decks that are about to be culled.

    **Replacement:** the trainer is no longer scoped to a generation's
    finalists because there is no generation boundary. It is now the
    continuous **offspring faucet** for the Baseline-Challenge Loop's TRAIN
    step: it runs continuously, producing roughly 4-6 `SearchConfig`-
    mutated offspring/day, each paired with a value net retrained on recent
    self-play data (~2-4h GPU per offspring). The original waste concern
    (training compute spent on soon-to-be-culled decks) is addressed
    differently under the new model: training targets *agent offspring*,
    not census decks directly — census decks still never get individual
    value nets, only the continuously-maintained field rating that feeds
    MATCH's top-30 selection.

11. **Generation state machine — SUPERSEDED 2026-07-22 (Brad cadence
    redesign).** Original text retained for audit trail:

    > ```
    > CENSUS/DECK_SCREENING -> DECK_PLAYOFF -> OFFSPRING -> CHAMPIONSHIP -> SUBMIT -> (next generation)
    > ```
    >
    > - **CENSUS/DECK_SCREENING:** every active concept gets its screening
    >   games (~15 for census, ~40 for later generations) against the
    >   field; ratings settle via Bradley-Terry.
    > - **DECK_PLAYOFF:** top-rated decks from screening play ~200-game
    >   pairings to establish a reliable ranking among contenders.
    > - **OFFSPRING:** breed ~6 ISMCTS-config children of the incumbent
    >   champion agent, reusing the existing agent-config breeding
    >   operators from the evolutionary-agent-population slice (gene
    >   jitter + crossover + rare categorical flips on `SearchConfig`).
    >   Each child plays a ~200-game series against the incumbent (plus
    >   the generation's top decks, D*). Every child that beats the
    >   incumbent advances to CHAMPIONSHIP.
    > - **CHAMPIONSHIP:** all survivors (deck playoff winners + any
    >   advancing agent offspring) round-robin at ~200 games each; the top
    >   two meet in the **Grand Final**, a Bo1001 (first to 501).
    > - **SUBMIT:** the Grand Final winner is packaged and uploaded — one
    >   Kaggle submission for the whole generation.
    > - The state machine is **crash-safe and checkpointed**: every phase
    >   transition and every game result is persisted immediately, so a
    >   crash mid-Bo1001 resumes from the last recorded game count rather
    >   than restarting the series.

    **Replacement:** the continuous **Baseline-Challenge Loop** (BOOTSTRAP
    once, then TRAIN/MATCH/CONFIRM/CROWN with no generation boundary) — see
    the new section below for the full replacement mechanics. The
    crash-safety/checkpointing property survives, restated for the loop
    model in the Loop Mechanics section.

12. **Workers & cutover.** Retired at cutover: `evolution_tick`, the
    two-pool breeding loop, the best-cell submission gate
    (`src/ptcg/factory/gate.py`'s cell-selection logic), and the
    deck-matrix queue refill (`src/ptcg/factory/deck_matrix.py`). The old
    genome pools (`experiments/factory/agent_pool.json`,
    `experiments/factory/deck_pool.json`) stay on disk as historical
    record — not deleted, not migrated. The agent-config breeding operators
    themselves (mutation/crossover functions in
    `src/ptcg/factory/breeding.py`/`genomes.py`) are **reused** for the
    Baseline-Challenge Loop's TRAIN step (updated 2026-07-22 — supersedes
    the "OFFSPRING phase (decision 11)" framing, since decision 11's phase
    model is retired) — only the pool-management and selection logic
    around them is retired.

    New Windows Scheduled Tasks: the game-runner pool, the tournament
    scheduler (updated 2026-07-22: drives the continuous
    Baseline-Challenge Loop, superseding the "generation state machine"
    framing of decision 11), and `ptcg-factory-ui`. The `ptcg-factory-matrix`
    task (continuous tournament + single-factor Bradley-Terry) is retired.
    The `ptcg-factory-trainer` task is retargeted again per the
    Baseline-Challenge Loop's TRAIN step (updated 2026-07-22 — supersedes
    decision 10's finalist-only retargeting) rather than replaced.

    `PAUSE` (full stop, `experiments/factory/PAUSE`) and `SUBMIT_HOLD`
    (uploads only, `experiments/factory/SUBMIT_HOLD`) continue to govern
    every new component exactly as they govern the current factory.

    **Go-live sequence:** stop old workers (`ptcg-factory-matrix`,
    `ptcg-factory-trainer` — see `.claude/rules/factory-resume-probe.md`
    on why a `Running` task is not evidence of current code) -> register
    new tasks -> lift `SUBMIT_HOLD` -> census begins.

    **Rollback:** flip the new tasks' feature flags off, restart the old
    workers. Because the old genome pools are left untouched on disk, a
    rollback does not lose evolutionary-agent-population state.

13. **Calendar:** ~15-20 implementation tasks, phased into a **fresh
    session** per the global CLAUDE.md one-feature-lifecycle-per-session
    rule. Phase 1: database + census + runner pool. Phase 2 (updated
    2026-07-22: relabeled from "offspring/championship/UI/cutover" now
    that OFFSPRING/CHAMPIONSHIP are no longer discrete phases): the
    TRAIN/MATCH/CONFIRM/CROWN loop, the Submission Scheduler, the UI, and
    cutover. Target: first crowned submission ~2026-08-01 to 2026-08-03; at
    the Baseline-Challenge Loop's ~1-2-day cycle-time estimate, that window
    leaves room for considerably more crown opportunities than the
    original discrete-generation model's ~3-4 before the 2026-08-16 finals
    deadline — the exact count depends on how many offspring clear CONFIRM,
    not calendar time alone.

## Baseline-Challenge Loop

Supersedes the discrete CENSUS/DECK_SCREENING -> DECK_PLAYOFF -> OFFSPRING
-> CHAMPIONSHIP -> SUBMIT -> (next generation) state machine of decision 11
and the time-boxed `generation_days` config of decision 8 (both SUPERSEDED
2026-07-22, Brad cadence redesign — see Locked Decisions). Brad's redesign
replaces batched generations with a single continuous loop: offspring
stream out of the trainer continuously, each is matched against the field,
confirmed against the sitting baseline, and crowned the moment it's
proven — no generation boundary to wait for.

- **BOOTSTRAP (one-time):** the Founding Census (decision 4, unchanged)
  runs to full 815/815 completion, rating the deck field via Bradley-Terry
  and fixing the first baseline: `(v0.1, D*)` where `D*` is the best census
  deck.
- **TRAIN (continuous):** the GPU trainer worker (retargeted again,
  supersedes decision 10's finalist-only scope) produces roughly 4-6
  offspring/day: each a `SearchConfig` mutation via the existing breeding
  operators (`breeding.py`/`genomes.py`, reused per decision 12) paired
  with a value net retrained on recent self-play data pulled from the
  games DB (~2-4h GPU per offspring).
- **MATCH:** each offspring plays the field's current top-30 decks (by
  current field rating — the continuously-maintained screening rating of
  decision 3) at ~15 games each against the sitting baseline, selecting the
  offspring's optimal deck by win%. Rationale: a full-field sweep costs ~5
  days/batch (815+ decks); top-30 costs ~450 games, ≈1/5 day — cheap enough
  to run per-offspring without becoming the bottleneck.
- **CONFIRM:** the offspring, on its optimal deck, plays a ~200-game series
  against the baseline (the same 200-game budget as decision 3's playoffs
  bullet). Measured win rate <50% -> **TRASHED** (a permanent DB record,
  never deleted). >=50% -> **SURVIVOR**. Rationale: the max of 30 noisy
  per-deck win rates (from MATCH) systematically overestimates the
  offspring's true strength; the CONFIRM series regresses that luck out
  before the trash/survive call is made.
- **CROWN:** whenever >=2 survivors exist simultaneously, they play a
  round-robin (~200 games/pair, each on its own optimal deck); the best
  aggregate win% becomes the new baseline and the version bumps (`v0.1 ->
  v0.2 -> ...`, decision 5). The new baseline immediately faces every
  subsequent offspring's CONFIRM step — continuous dethronement pressure
  replaces the Bo1001 grand-final ceremony (decision 3's superseded
  bullet) as the confidence backstop. Accepted statistical trade: a single
  CONFIRM/CROWN verdict carries roughly ±7%-class noise versus Bo1001's
  ±3%, but costs ~half a day less compute per crown, and a champion that
  only barely deserved the crown faces renewed challenge on the very next
  CONFIRM cycle rather than being insulated for an entire generation.
- **Cycle time:** at the ~2k games/day pool throughput target (decision 6,
  within its ~1,500-2,500 range), a new baseline is possible roughly every
  1-2 days — offspring production (TRAIN, ~4-6/day) is not the bottleneck;
  CONFIRM+CROWN game volume is.

**Persistence:** offspring and baseline state live in the same SQLite
database as the deck field (decision 9), extended with the `offspring` and
`baselines` tables described there.

## Architecture

```
                    +-----------------------+
                    |  Deck Database (SQLite) |
                    |  concepts / decks /      |
                    |  games / decisions /      |
                    |  coverage / offspring /    |
                    |  baselines                  |
                    +-----------+---------------+
                                |
        reads/writes            | reads/writes
                                |
   +----------------+   +-------v--------+   +-------------------+
   | Runner Pool     |   | Baseline-       |   | ptcg-factory-ui    |
   | (4-8 worker      |<->| Challenge Loop  |<->| (127.0.0.1 only,    |
   | processes,        |   | Scheduler        |   | http.server,        |
   | BelowNormal)      |   | (TRAIN/MATCH/    |   | Remove/Pass POSTs)  |
   +----------------+   |  CONFIRM/CROWN,  |   +-------------------+
                        |  no discrete      |
                        |  phases)          |
                        +-------+--------+
                                |
                       submission scheduler
                       (independent 4.8h
                       clock: upload on
                       baseline change +
                       24h daily-floor
                       probe)
                                |
                       +--------v--------+
                       |  Kaggle ladder   |
                       +-----------------+

   Trainer worker (continuous offspring faucet, supersedes decision 10):
   retrains the value net on recent self-play data and produces breeding
   offspring continuously, not gated to a generation's finalist decks.
```

The runner pool is the sole game engine. The Baseline-Challenge Loop
Scheduler owns the continuous TRAIN/MATCH/CONFIRM/CROWN cycle (decision 11
is superseded by this model; see the Baseline-Challenge Loop section
above) and writes every game result to SQLite before returning. The
submission scheduler (see Submission Scheduler section below) fires
independently on its own 4.8-hour cadence, decoupled from crown events.
The UI server is a thin read/write layer over the same database — it never
runs games itself.

## Deck Database & Founding Census

The census is the Baseline-Challenge Loop's BOOTSTRAP step (formerly
labeled "Generation 1's DECK_SCREENING phase" under the superseded
discrete-generation framing) and the seed for every concept the loop
screens afterward:

1. On first boot, the deterministic builder is run once per attacker
   Pokemon in the card pool (815 total: 442 basic + 373 evolution, each
   with at least one damaging attack), producing 815 `concepts` rows with
   `status = untested` and 815 corresponding `decks` rows. Concepts the
   builder cannot legally satisfy (e.g. a core whose attack costs cannot be
   filled within the 20-energy-card ceiling) are written with
   `status = unbuildable` and a reason string — they remain visible in the
   database, never silently dropped.
2. The runner pool plays ~15 screening games per buildable concept against
   the field, feeding Bradley-Terry ratings into `coverage`.
3. The census phase does not close until all 815 concepts have reached
   their screening-game floor (or are marked `unbuildable`) — this is the
   one phase exempt from any time constraint (there is no `generation_days`
   box anymore per superseded decision 8; the census was always the one
   exception to it).
4. Survivors feed the continuous field-rating loop — not a discrete
   DECK_PLAYOFF phase (decision 11's phase framing is superseded; see
   Baseline-Challenge Loop above) — supplying both the UI's bottom-10
   culling review (decision 7) and the top-30 field ranking the MATCH step
   uses to pick each offspring's opponents. The concept pool is seeded on
   an ongoing basis from survivor shell variants (3-5 deterministic
   variants per surviving single core) plus lazily-enumerated pair
   concepts, per decision 4, with no generation boundary at which this
   happens once.

## Loop Mechanics

See the Baseline-Challenge Loop section above for the TRAIN/MATCH/CONFIRM/
CROWN cycle that replaces decision 11's discrete state machine. Two
properties carry over unchanged from the original design:

- **Checkpointing:** every game result and every stage transition (queued
  -> training -> matching -> confirming -> trashed/survivor -> crowned) is
  written to SQLite synchronously (not batched), so a process crash loses
  at most the in-flight game, never a completed series or stage boundary.
- **Resumability:** the scheduler determines each offspring's current
  stage and in-stage progress (e.g. games played so far in an active
  CONFIRM series) purely by querying the database on startup — no
  in-memory state is required to resume correctly.

## Submission Scheduler

Supersedes the per-generation single-upload model of decision 2
(SUPERSEDED 2026-07-22, Brad cadence redesign — see Locked Decisions):
there is no generation boundary to key an upload to, so submission now
runs on its own independent clock.

- **Versioning:** see decision 5 (updated 2026-07-22) — `v0.1` is the
  founding base agent; offspring produced while a version is the sitting
  baseline are numbered continuously (`v0.1.1, v0.1.2, ...`); each CROWN
  bumps the minor version and resets offspring numbering under the new
  baseline.
- **Submission marks:** every 4.8 hours (5 marks/day, aligned with
  Kaggle's 5/day cap).
- **At each mark:** if the current baseline differs from the last-uploaded
  identity, upload it — exactly one new identity per mark that has a
  change. The existing 5/day hard cap and atomic-reservation pattern
  (`178043b`) still gate every upload attempt unchanged.
- **Daily floor (Brad's explicit choice):** if 24 hours elapse with no
  upload (no baseline change crossed a mark), upload the next-best
  candidate not yet ladder-probed as a **PROBE**. A probe carries the
  *current* baseline's version tag plus its own deck-concept id, but does
  **not** bump the version — it is not a crowning event. There is always a
  next-best unprobed candidate to send (the field is large and
  continuously growing), so the floor never stalls waiting for a
  candidate.
  - Purpose: keeps real Kaggle-ladder calibration data flowing even during
    a stretch with no new crown, feeding the local-vs-ladder calibration
    view (score trajectories flow back into the DB and the UI).
- **Platform-mechanics rationale** (per
  `.claude/rules/platform-mechanics-model.md`): Kaggle's counted pair is
  the two most-recently-submitted submissions, evicted by recency, not
  score. A counted pair needs roughly 2 days of survival in that pair for
  its score to converge (all 600+ historical scores observed
  pre-convergence were transients, per the 2026-07-20 weekly review).
  Submit-on-baseline-change lets a genuinely-crowned pair sit and converge
  undisturbed for as long as no new crown occurs, while the daily floor's
  probe uploads keep data flowing during idle stretches without displacing
  a converging pair unless 24 hours have genuinely passed with nothing
  better to report.
- **Freeze protocol (locked):** at approximately 2026-08-14, submit the
  all-time best two candidates by combined ladder+local evidence, then set
  `SUBMIT_HOLD` (auto-hold) through the 2026-08-16 final deadline — the
  ~2-day convergence buffer the counted pair needs to settle before finals
  lock.
- The 5/day submission cap, atomic-reservation pattern, and auth-liveness
  check (`check_auth()`, fixed commit `9d3242e`) are unaffected by this
  design and remain in force.

## UI

- Server: `ptcg-factory-ui`, a new Windows Scheduled Task running a stdlib
  `http.server`-based process bound to `127.0.0.1` only — not reachable
  off the local machine, so no authentication is required (see Risks).
- Refresh cadence: every 15 minutes, matching the existing factory
  check-in cadence.
- View: the 10 lowest-rated active decks meeting the minimum-games floor,
  each row expandable to the full 60-card list.
- Actions: **Remove** (permanent retirement, row kept as history) and
  **Pass** (stays active, may re-list later) per deck, per decision 7.
  Decisions are written atomically to the `decisions` table so a worker
  reading mid-write never sees a half-applied decision.
- The UI never blocks the tournament: the scheduler and runner pool
  proceed on their own schedule regardless of review pace.

## Workers & Cutover

See decision 12 for the full retirement/reuse list and go-live sequence.
Summary:

| Component | Disposition |
|---|---|
| `evolution_tick` / two-pool breeding loop | Retired |
| Best-cell submission gate logic | Retired |
| `deck_matrix.py` queue refill | Retired |
| `agent_pool.json` / `deck_pool.json` | Left on disk as history, untouched |
| Agent-config breeding operators (`breeding.py`/`genomes.py`) | Reused in the Baseline-Challenge Loop's TRAIN step |
| `ptcg-factory-matrix` task | Retired |
| `ptcg-factory-trainer` task | Retargeted — continuous offspring faucet (TRAIN step): retrains the value net on recent self-play and produces breeding offspring continuously, not gated to a generation's finalist decks (supersedes decision 10's original finalist-only scope) |
| `ptcg-factory-continuous` task's gate/submit role | Absorbed into the Submission Scheduler's 4.8-hour upload marks (see Submission Scheduler section; supersedes decision 11's SUBMIT-phase framing) |
| New: runner pool task, tournament scheduler task, `ptcg-factory-ui` task | Registered at cutover |

`PAUSE` and `SUBMIT_HOLD` govern every component, old and new, throughout
the cutover — nothing bypasses them.

**Resolved by Brad, 2026-07-22:**

- **Episode harvester** (`src/ptcg/factory/harvest.py`, Phase 2 of the
  evolutionary-agent-population slice): **retires at cutover** along with
  the rest of the steady-state system — the tournament model has no
  meta-anchor deck consumer for harvested episodes. Already-harvested data
  stays on disk untouched.
- **`experiments/factory/dashboard.html` status dashboard**: the new
  localhost `ptcg-factory-ui` server (decision 7) serves the same
  dashboard content live, plus the interactive deck-review panel. The
  static `dashboard.html` continues to be written every firing as a
  no-server fallback view (read-only).

## Testing Strategy

- **TDD throughout**, per the global CLAUDE.md Python defaults.
- **Interleaved-mutation tests for every shared-state writer** — the
  runner pool, tournament scheduler, UI server, and trainer worker all
  read/write the same SQLite database concurrently. Per the standing
  project rule `.claude/rules/single-actor-worker-tests.md`, every
  read-modify-write window gets a test that injects a concurrent write
  mid-operation and asserts no write is silently lost — not just a
  single-actor test against a fixture.
- **Deterministic-builder golden tests:** the same concept must always
  produce a byte-identical 60-card deck. A golden-vector test pins known
  concepts to known deck outputs.
- **Loop resume tests:** kill the scheduler process mid-stage (including
  mid-CONFIRM series) and assert it resumes from the correct stage and
  game count on restart, per the Checkpointing/Resumability properties in
  Loop Mechanics above.
- **Runner-pool concurrency tests:** multiple worker processes claiming
  and completing games against the same database without double-counting
  or dropping results.
- **UI server decision-write atomicity tests:** a Remove/Pass POST must
  either fully apply or not apply at all, even under a concurrent read
  from the scheduler.
- **Virgin-directory tests for every first-write path** (the SQLite
  database file, the UI server's decision log, any new stamp/lock files):
  run against a genuinely fresh, never-created location — not a
  `tmp_path`-fixture directory that pre-creates the parent. Two prior
  datapoints in this repo (`ledger_lock()` and
  `make_random_policy_weights.py`) both crashed on real first-run against
  a nonexistent parent directory despite full `tmp_path`-based test
  coverage; this class of bug is now a mandatory Smoke Test Ladder rung 3
  check per global CLAUDE.md, not an optional unit test.
- **Census smoke, rung 3:** before trusting the full 815-concept census,
  run it against a real (non-mocked) slice of concepts end-to-end —
  builder, screening games, rating, database writes — and eyeball the
  output, per the Smoke Test Ladder's "manual smoke of the actual thing"
  rung.

## Calendar & Risks

**Calendar:** ~15-20 tasks across two implementation plans in a fresh
session (Phase 1: database + census + runner pool; Phase 2, updated
2026-07-22: the TRAIN/MATCH/CONFIRM/CROWN loop, the Submission Scheduler,
the UI, and cutover). Target first crowned submission 2026-08-01 to
2026-08-03; at the Baseline-Challenge Loop's ~1-2-day cycle-time estimate,
that leaves room for considerably more crown opportunities than the
original discrete-generation model's ~3-4 before the 2026-08-16 finals
deadline — exact count depends on how many offspring clear CONFIRM, not
calendar time alone.

**Risks (named, accepted):**

- **Local winner may not be the ladder winner.** Prior slices documented
  local-eval-vs-ladder-score inversions (e.g. starmie-searchnet
  outperforming lucario-net variants on the ladder despite different local
  standing). Accepted: Brad's explicit call is submit-on-baseline-change
  plus the daily-floor probe (see Submission Scheduler) regardless of this
  risk.
- **Daily-floor probe uploads spend submission slots on non-crowned
  candidates.** Mitigation: probes fire only after a genuine 24-hour idle
  window (not stacked), and the existing 5/day hard cap still applies
  unchanged; Brad accepted this trade in exchange for continuous
  ladder-calibration data (see Submission Scheduler).
- **Census screening depth (15 games/concept) is noisy per concept.**
  Mitigation: Bradley-Terry pooling smooths individual-game noise, and
  Brad's Pass option in the UI keeps borderline decks active rather than
  letting screening noise alone decide a cull.
- **Builder quality bounds concept fairness** — a mediocre deterministic
  build can undersell a strong core. Mitigation: shell variants (3-5 per
  surviving core) give a core multiple chances before it is judged.
- **Throughput assumptions (~1,500-2,500 ISMCTS games/day) need day-1
  hardware validation** — this is a target, not a measured fact, and must
  be checked against real hardware early in Phase 1 before either the
  census time-box (~7 days) or the Baseline-Challenge Loop's ~1-2-day
  cycle-time estimate is trusted.
- **The UI server is a new attack surface.** Mitigation: bind to
  `127.0.0.1` only, never expose beyond localhost; no authentication is
  needed because the process is unreachable off the local machine.

## Retired Systems & History

This design supersedes, in whole or in part:

- **Steady-state co-evolution** (evolutionary-agent-population, merged
  `45ee1d5`): two continuously-bred genome pools, two-factor Bradley-Terry
  fitness, best-cell submission. Retired per decision 12. Genome pool
  files left on disk as history.
- **Champion-pairing recency-eviction guard** (built 2026-07-14 weekly
  review, `src/ptcg/factory/gate.py`/`submit.py`, commit `1ed7b60`):
  retired per (superseded) decision 2, and remains retired under the
  redesigned Submission Scheduler (2026-07-22) for the same underlying
  reason — neither the original nor the redesigned submission model
  attempts to keep a specific champion pair alive across multiple
  submissions.
- **`ptcg-factory-matrix` scheduled task** (continuous round-robin
  tournament + single-factor Bradley-Terry, introduced in the
  compute-saturation slice, 2026-07-20): retired. Its game-running role is
  replaced by the new runner pool; its rating role is replaced by the
  continuously-maintained Bradley-Terry screening ratings in the new
  SQLite database (updated 2026-07-22: no longer "per-generation" ratings,
  since screening runs continuously under the Baseline-Challenge Loop).
- **`deck_matrix.py` queue refill** (continuous-factory slice,
  2026-07-17): retired. The Founding Census plus concept-status tracking
  in SQLite replaces hand-curated seed decks and mutation-rule refill.

**Second-round supersessions (2026-07-22, Brad cadence redesign, same day
as this spec's original approval):**

- **Discrete generation state machine**
  (`CENSUS/DECK_SCREENING -> DECK_PLAYOFF -> OFFSPRING -> CHAMPIONSHIP ->
  SUBMIT -> (next generation)`, decision 11): retired. Replaced by the
  continuous Baseline-Challenge Loop (BOOTSTRAP once, then
  TRAIN/MATCH/CONFIRM/CROWN with no generation boundary).
- **Time-boxed `generation_days` config** (decision 8): retired. No config
  value bounds cycle length under the continuous loop; cycle time (~1-2
  days/crown) is emergent from throughput, not a configured box.
- **Bo1001 grand final** (decision 3's original crowning mechanism):
  retired. Replaced by a ~200-game CONFIRM series plus survivor
  round-robin CROWN, backstopped by continuous post-crown dethronement
  pressure. Accepted trade: ±7%-class verdicts instead of Bo1001's ±3%,
  ~half a day cheaper per crown.
- **Trainer worker finalist-only scope** (decision 10): retired. The
  trainer is now a continuous offspring faucet (~4-6 offspring/day), not
  gated to a generation's playoff/championship finalist decks.
- **Per-generation single-upload submission model** (decision 2): retired.
  Replaced by the Submission Scheduler's 4.8-hour marks (upload on
  baseline change) plus a 24-hour daily-floor probe upload.

Unaffected by this design (original or amended): the 5/day submission cap
and its atomic reservation (fixed `178043b`), the Kaggle auth-liveness
check (fixed `9d3242e`), `PAUSE`/`SUBMIT_HOLD` semantics (`SUBMIT_HOLD`
merged `d373e7e`), and the ladder identity files
(`src/ptcg/submission_main.py`, `src/ptcg/agents/current.py`) — this
design changes what feeds a submission decision, not how a submission is
built or uploaded.

## Plan-review overrides (Brad, 2026-07-23)

Recorded at the plan-review gate so spec and plan cannot silently contradict
each other (plan: `docs/superpowers/plans/2026-07-23-generational-champion-tournament.md`).
These override the corresponding "Resolved by Brad, 2026-07-22" items above;
the earlier text is retained per this spec's supersession convention.

- **Episode harvester KEPT (overrides the 2026-07-22 "retires at cutover"
  resolution):** `check_and_harvest` survives cutover, hosted in the narrowed
  `ptcg-factory-continuous` watch loop (15-min firings, internally
  stamp-gated; extract-then-delete, 2GB cap unchanged). The meta-anchor
  injection consumer is still retired with the pool system; extracts are
  retained as a data asset with a named future hook
  (`census.inject_meta_concepts`, not built this slice).
- **Static `dashboard.html` RETIRED at cutover (overrides the 2026-07-22
  "continues to be written as a no-server fallback" resolution):**
  `ptcg-factory-ui` is the SOLE status/review surface; the watch loop stops
  writing `dashboard.html`. Availability: `ptcg-factory-ui` registers with an
  AtStartup trigger + 15-minute watchdog repetition like the other worker
  tasks (auto-start at boot, auto-restart within ~15 min).
- **Pair-concept enumeration pulled INTO the implementation slice
  (enumerate-all, play-singles):** all 332,520 concept rows (815 singles +
  C(815,2) = 331,705 pairs) are enumerated into SQLite during the slice; the
  Founding Census still PLAYS only the 815 single-core concepts (815x15 =
  12,225 games ~= 6.1 days at 2,000 games/day). Playing pairs at founding is
  infeasible (331,705x15 = 4,975,575 games ~= 6.8 years at 2,000/day) - pair
  space is explored generationally post-census via lazy activation "as
  individual cores prove out" (decision 4, unchanged).
