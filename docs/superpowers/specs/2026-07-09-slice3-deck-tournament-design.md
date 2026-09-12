# Slice 3 Design: Deck Tournament + Ladder Deck Promotion

Date: 2026-07-09
Status: Approved (Brad, 2026-07-09)
Prior slices: Slice 1 (heuristic v0, merged), Slice 2 (ISMCTS search-v1, parity, merged 788cb08)

## Goal

Ship a measurably better deck for the Kaggle ladder submission. Deck strategy is
20% of Strategy-category judging; the current entry is heuristic-v0 piloting
`mega-lucario-fighting` (0.633 vs sample at 30 games — a noisy measurement).

**Success criteria:**

1. A tournament harness exists that reliably ranks all candidate decks with
   confidence intervals, and stays cheap to re-run as decks are added.
2. Either a challenger deck is promoted (tops standings AND beats the incumbent
   head-to-head with 95% CI separation), or the incumbent's survival against a
   serious challenge is documented. Both outcomes are Strategy-report material.
3. The regression-pin debt item from Slice 2 is closed.

## Architecture: persistent league ledger (Approach A)

Chosen over stateless round-robin (re-burns games every run) and Elo ratings
(weaker CIs at this deck count, less report-friendly). Decision: Brad, 2026-07-09.

### Module layout — `src/ptcg/tournament/`

Three units, each independently testable:

**`ledger.py`** — persistent results store at `experiments/tournament/results.json`.

- Pairing rows: `(deck_hash_a, deck_hash_b, agent_name, wins_a, wins_b, games)`.
- Results are keyed by **deck content hash**, not filename. An in-place edit to a
  deck CSV changes its hash and auto-retires its stale rows. Deck variants are
  therefore always NEW files (`mega-lucario-v2.csv`), never in-place edits.
- Rows also record which agent played them; rows from a non-current agent are
  retired when the current agent changes.
- Writes are atomic (write-temp-then-rename) — a killed run cannot corrupt history.

**`scheduler.py`** — pure logic, no I/O. Given ledger + discovered decks, returns
the next pairing needing a batch, and classifies each pairing as *resolved*
(CI separated from 0.5), *capped* (tie at game cap), or *open*. Owns the
adaptive-sampling policy.

**`runner.py`** — plays one batch for one pairing via existing arena internals,
alternating seating every game to cancel first-player advantage. Streams results
into the ledger after each batch (crash-safe: a killed run loses ≤1 batch).

### Current-agent source of truth — `src/ptcg/agents/current.py`

Exposes `CURRENT_AGENT_NAME` and `make_current_agent(deck)`. The tournament
always pilots both seats with the current agent (today: heuristic-v0, the ladder
pilot). A unit test asserts `submission_main.py` and `current.py` agree, so
tournament measurement can never silently drift from what the ladder runs.
Rationale (Brad): "always use the most current agent... that agent may change
day to day."

### CLI — `scripts/run_tournament.py`

Discovers all CSVs in `src/ptcg/decks/candidates/`, syncs the ledger, loops the
scheduler until all pairings are resolved/capped or a `--max-games` session
budget is hit, then writes a standings block into `experiments/EXPERIMENTS.md`:
win-rate matrix, field win rates with CIs, analytic mulligan rate per deck.

## Adaptive sampling policy

- Batches of **50 games** per pairing, seats alternating.
- After each batch, compute the **Wilson 95% CI** on the pairing win rate.
- Stop when the CI excludes 0.5 (winner) or at **400 games** (statistical tie).
- Rationale: lopsided matchups resolve in ~1 batch; coin-flips cost ≤400 games.
  ~7 decks → 21 pairings → worst case ~8,400 games, acceptable at heuristic
  speed; the ledger ensures no pairing is ever paid for twice.
- Addresses the documented 30-game unreliability (mawile 0.90→0.03 swing).

## Deck candidate work

Two waves of new CSVs in `candidates/`, each passing `validate_deck()` and
getting a `RATIONALE.md` entry:

1. **Champion variants (2-3):** `mega-lucario-v2/v3...` — energy-count tuning,
   trainer-mix changes, tech swaps. Each variant states ONE hypothesis (e.g.,
   "v2: −2 energy +2 draw supporters — hypothesis: energy flooding mid-game").
2. **New archetypes (2-4):** selected from `pool_summary()` output PLUS
   attack-text reading (settled lesson: effect text outweighs stat lines).
   Candidate space: untested types, disruption/control shells.

Every deck gets an **analytic mulligan rate** (hypergeometric from Basic count,
no games needed) in standings; >~15% is flagged.

## Promotion & submission flow

The ladder deck changes ONLY if a challenger tops field standings AND beats the
incumbent head-to-head with 95% CI separation. Ties go to the incumbent (churn
costs: only the 2 most recent Kaggle submissions count). On promotion: rebuild
via `package_submission.py`; Kaggle submit only after Brad approves the
submission description (standing project rule). No promotion → document the
incumbent's survival in EXPERIMENTS.md.

## Regression pin (Slice-2 debt folded in)

A `-m slow` test: current agent + current champion deck vs the sample baseline
deck must clear a conservative win-rate bar over a fixed series. Exact bar and
series length set at plan time WITH explicit CI arithmetic (per
`.claude/rules/plan-test-arithmetic-sanity.md`): roughly ≥0.50 over 200 games
given the 0.633@30 measurement. Near-bar results follow
`.claude/rules/stochastic-gate-replication.md` (replicate before recording).
Other three Slice-2 debt items (RAINBOW `_cost_satisfied` bug, belief-v2,
intra-iteration deadline check) are explicitly deferred to Slice 4.

## Error handling

- A game that crashes the engine is **discarded and logged** — never counted as
  a loss (engine bugs must not skew standings). ≥3 crashes in one pairing aborts
  the run with the error surfaced.
- Ledger writes atomic; batch-level persistence bounds loss from interruption.

## Testing

- **Unit (fast):** scheduler stopping logic vs stubbed runner; ledger round-trip
  + hash/agent invalidation; Wilson CI math vs known values; mulligan
  hypergeometric vs hand-computed cases; current-agent ↔ submission-main
  consistency.
- **Integration (fast):** 2-deck mini-tournament end-to-end with stubbed runner
  (discover → schedule → ledger → standings text).
- **Live (slow/manual rung):** the first real tournament run is the manual smoke.

## Out of scope (named)

- Search-agent internals: RAINBOW bug, belief-v2, deadline check → Slice 4.
- Automated deck search (hill-climbing/genetic).
- Elo ratings.
- Tournament parallelism (single process; revisit only if runtime hurts).
