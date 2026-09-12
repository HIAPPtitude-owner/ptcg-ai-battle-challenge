# Evolutionary Agent Population — Design

**Date:** 2026-07-21
**Status:** Approved (Brad, 2026-07-21) — pending spec review
**Feature slice:** evolutionary-agent-population
**Supersedes:** hand-authored candidate authoring + deck_matrix queue refill as the factory's candidate source

## Vision

Replace the factory's hand-authored/mutation-refill candidate pipeline with a
**steady-state co-evolutionary population**: two gene pools (agent configs and
decks) continuously rated by a two-factor Bradley-Terry tournament, bred from
their top performers, culled from their bottom, with the Kaggle ladder as the
final evaluator via the existing 5/day submission gate. Phase 2 grounds fitness
in reality by harvesting real ladder episodes and injecting the observed meta's
decks as fixed anchor opponents.

## Locked decisions (Brad, 2026-07-21)

1. **Sequencing:** both builds in this feature — evolution loop first, episode
   harvester second phase.
2. **Fitness:** Bradley-Terry ratings from the matrix worker. Best-of-3
   explicitly rejected (coin-flip selection at the observed 42–54% WR band).
3. **Genome: FULL ISMCTS FOCUS.** Evolve ISMCTS agents (search-config genes +
   decks) as the centerpiece. Deliberate override of the deck-first
   recommendation, accepting that Slices 4–7B found no local ISMCTS edge and
   that search games are ~10× slower — generation throughput is the central
   design constraint.
4. **Evolution model:** steady-state (continuous birth/death), not discrete
   generations. The 100→10→100 vision maps to birth/death rates and a ~10%
   breeding-stock ratio, not batch waves.
5. **Co-evolution:** two populations rated jointly; selection/breeding on
   marginals; **submission picks the best observed (agent, deck) cell** — a
   2nd-place agent on its fitted deck may beat best-agent+best-deck.
6. **Architecture:** breeding/selection lives **inside the matrix worker's
   locked tick**. No new worker process.
7. **Episode cadence:** check for new episode data **every watch-loop firing
   (~15 min)**; download immediately when a new dataset day/version appears.
   (Ceiling on freshness is Kaggle's publishing cadence — observed daily.)

## Ground-truth constraints (measured)

- Matrix worker throughput at go-live mix: ~836 games/hour (~4.3 s/game,
  heuristic-heavy pool). Search-vs-search games: **~23–32 s/game** measured.
  All-search pools run ~150–300 games/hour.
- Search per-move: ~9 MCTS iterations at 200 ms budget, ~46 at 1 s.
- `SearchConfig` (src/ptcg/search/searcher.py) exposes ~17 tunable parameters;
  factory candidates today vary only 2–3 of them.
- Kaggle episode data: daily datasets `pokemon-tcg-ai-battle-episodes-YYYY-MM-DD`
  (~750 MB/day raw), per-episode JSON with opponent decks + move logs, plus a
  small `episodes-index` manifest. Never previously harvested.
- Pool mechanics today: `POOL_CAP=24` enforced in `enforce_pool_cap` under the
  matrix lock; protected = incumbent or ever-submitted; retire floor 15 games.

## Phase 1 — Co-evolution loop

### 1. Data model

Two new genome ledgers (JSON, atomically written, same tmp-then-replace pattern
as candidates.json):

- **`experiments/factory/agent_pool.json`** — live agent-config genomes.
  Genome = full `SearchConfig` gene bundle: `c_uct`, `rollout_depth`,
  `deviate_min_visits`, `deviate_value_edge`, `deviate_min_visit_frac`,
  `final_move_rule`, `robust_min_visits`, `use_root_prior`, `prior_tau`,
  `c_puct`, `use_tree_prior`, `policy_opponent`, `policy_rollout`,
  `max_depth`, plus **per-move budget `search_budget_ms` bounded [50, 1000]**
  (a gene: evolution may trade strength for game volume). Each genome carries:
  id, lineage (parent ids), birth time, marginal rating, games played, status
  (live/retired/anchor).
- **`experiments/factory/deck_pool.json`** — live deck genomes: deck content
  hash, csv path, lineage, birth time, marginal rating, games, status
  (live/retired/anchor/meta-anchor), optional `net_weights` path.

`candidates.json` is unchanged in schema and remains the ledger of
**submittable/submitted cells**: when the gate selects a cell for submission it
is snapshotted into a versioned Candidate exactly as today, so gate/submit/
harvest/LADDER.md/dashboard keep working without modification.

### 2. Rating: two-factor Bradley-Terry

Extend `bt.py`: `strength(cell) = agent_rating + deck_rating`. Each game
updates all four involved factors (both sides' agent and deck). Identifiability
pinned by zero-sum normalization within each population (or a fixed anchor at
0 — implementer's choice, tested either way). Marginal ratings drive selection.
**Cell-level win/loss records are kept per (agent, deck) pairing** in
matrix.json — that is where fitted combos (interaction effects the two-factor
model deliberately drops) become visible.

Game records in `matrix_blocks.jsonl` gain agent-id and deck-id per side
(backward-compatible extension; old rows remain readable).

### 3. Pairing (sampled grid, not exhaustive)

The matrix worker's `next_pair` moves from fewest-games-per-candidate to
**fewest-games-per-cell with balance constraints**: prioritize cells whose
agent or deck has the least total coverage, and ensure over time every agent
meets every deck and a spread of opponent families. Full grid enumeration is
explicitly out (12×12 = 144 cells → ~10k matchup pairs is not playable at
~25 s/game); the two-factor model is what makes sparse sampling sound.

**Playoff priority:** each tick reserves a fraction of games (~20%) for the
current top-N cells by combined rating, concentrating evidence where the next
submission will come from.

### 4. Steady-state selection and breeding (in the matrix worker locked tick)

- **Death:** a live genome is retire-eligible when it has ≥ games floor
  (agents: ~40 games; decks: ~40 games — tune at 48h review) and sits at the
  bottom of its population's marginal ratings. Protected (never retired):
  current ladder incumbent's agent+deck, anything on the ladder, fixed
  anchors, meta-anchors.
- **Birth (same atomic tick as death):** parents drawn from the population's
  top ~25% by rating (breeding stock).
  - Agent offspring: numeric gene jitter (Gaussian, per-gene scale, clamped to
    bounds) + occasional two-parent gene crossover + rare categorical flips
    (`final_move_rule`, boolean prior flags).
  - Deck offspring: existing four mutation ops **plus new two-parent deck
    crossover** (blend card pools, repair to legality). Hard validator: 60
    cards, ≤4 copies/name, ≤1 ACE SPEC — offspring failing repair are
    discarded and re-rolled.
  - Dedup by content hash (agent: canonical config JSON; deck: existing hash).
- **Anchors:** 2–3 fixed non-breeding genomes (e.g. heuristic-v1.0 pilot as an
  agent anchor, current incumbent deck) — protected, keep ratings comparable
  across time, and keep some games fast.
- **Population sizes at start:** ~12 live agents + ~12 live decks; active
  concurrent-coverage target ~48 cells. **48-hour review checkpoint:** measure
  the true all-search game rate, then adjust pool sizes/floors (scale toward
  Brad's 100-scale vision only if throughput supports it).

RNG: seeded, logged per-birth (reproducibility for the Strategy report).

### 5. Submission rule

Each watch-loop cycle the gate ranks **cells** (not marginals) by observed
performance: eligible = cell games ≥ ~30 with coverage across ≥ ~5 distinct
opponent cells; rank by cell BT strength with observed cell win% as tiebreak
evidence. Winner must clear the existing incumbent-merit check (head-to-head
P ≥ 0.55) and all existing guards (5/day cap with atomic reservation,
champion-pairing recency guard, PAUSE file, auth check). Submitted cell →
snapshotted into candidates.json as a versioned Candidate with lineage in the
description/provenance.

### 6. Trainer worker coupling (unchanged mechanism)

Trainer keeps training one deck per tick, prioritized by deck marginal rating,
for decks lacking a value net. Deck offspring inherit the parent's
`net_weights` when the parent has one (nearest-parent inheritance on
crossover: higher-rated parent). No 1:1 training explosion — the existing
throttle already handles arbitrary birth rates.

## Phase 2 — Episode harvester + meta anchors

### 7. Harvest step (watch loop, every firing)

- Cheap check first: query the `pokemon-tcg-ai-battle-episodes-index` manifest
  / dataset version metadata via the Kaggle CLI. If no new day/version since
  the last harvest stamp → no-op (the common case; a stamp file records last
  harvested version).
- On new data: download, filter to episodes involving **our** submissions
  (match by submission id from candidates.json), extract per-episode: opponent
  decklist, our result, timeout/complete status, game length. Store compact
  extracts under `experiments/factory/episodes/` (raw ~750 MB/day downloads
  are deleted after extraction; keep extracts only, cap retained size).
- Failure isolation: harvest errors never break the gate/submit path (same
  `safe_render`-style isolation as the dashboard).

### 8. Meta anchors + forensics

- Aggregate opponent decklists across a rolling window; the top ~3 recurring
  meta decks (by frequency among opponents that beat us, then overall) are
  injected into the deck pool as **meta-anchor decks**: protected,
  non-breeding, piloted by our own agents like any deck. Fitness then partly
  measures "beats the real ladder meta." Honest limitation (documented in the
  Strategy report): we clone the meta's decks, not its pilots.
- Loss summary (timeout losses vs matchup losses vs close games) added to the
  dashboard as a small panel; weekly review checklist gains an episode-review
  step.

## Testing & safety (both phases)

- **Interleaved-mutation tests** (per `.claude/rules/single-actor-worker-tests.md`)
  for every new read-modify-write window: breeding tick vs watch-loop submit
  writes; harvest-stamp writes vs concurrent firings.
- Two-factor BT: synthetic-data unit tests — planted agent/deck strengths must
  be recovered from simulated game outcomes; identifiability pin tested.
- Offspring legality validator: property-style tests over crossover/mutation
  outputs; deck repair never emits an illegal deck.
- Virgin-directory smoke for every new first-write artifact (agent_pool.json,
  deck_pool.json, episodes/ dir, harvest stamp) — run against a truly fresh
  location per the escalated first-run rule.
- Seeded-RNG reproducibility test for breeding.
- PAUSE file halts breeding, harvesting, and submissions (all existing workers
  already honor it; new paths must too).
- Ladder identity files (`src/ptcg/submission_main.py`, `src/ptcg/agents/current.py`)
  untouched by this slice; submissions flow only through the gate.

## Known risks (named, accepted)

- **Local BT is a proxy.** The ladder remains the evaluator of record; the
  weekly review reconciles local ordering vs ladder scores (existing
  checklist). Slices 4–7B evidence says ISMCTS-config evolution may find no
  edge — the FULL ISMCTS decision accepts this; deck evolution and meta
  anchors are the hedged signal paths within the same design.
- **Interaction effects dropped from ratings** — compensated at submission by
  cell-level playoffs and cell-observed records.
- **Throughput risk:** all-search pools may run slower than the ~25 s/game
  estimate; the 48h checkpoint and the bounded `search_budget_ms` gene are the
  levers. If churn is too slow, pool sizes shrink before floors do.
- **Episode data volume:** ~750 MB/day raw; mitigated by manifest filtering,
  extract-then-delete, and a retained-size cap.
- **Kaggle publishing cadence bounds episode freshness** — 15-min checks catch
  new data as soon as it exists; they cannot make Kaggle publish faster.

## Out of scope

- Replaying opponent *policies* from episode move logs (pilot cloning) — a
  future slice; this slice uses episodes for decklists + loss forensics only.
- Any change to submission cadence/caps, champion-pairing guard, auth, or
  ladder identity files.
- Retiring the trainer worker or changing its protocol.
- Exhaustive agent×deck grid coverage (explicitly replaced by sampled coverage
  + playoffs).
