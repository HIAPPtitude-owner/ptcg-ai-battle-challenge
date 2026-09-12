# Slice 7A Design: Agent Factory + Asymmetric-Pairing Test + Continuous-Training Daemon

Date: 2026-07-11
Status: APPROVED (Brad, brainstorm session 2026-07-11)
Phasing: Slice 7A (this spec) builds the factory, the discriminating test, and the training daemon with a per-deck-net trainer. Slice 7B (next session) swaps the policy-improvement trainer (search-guided self-play) into the daemon.

## Goal

Build a fully automated local system that continuously produces, evaluates, versions, and submits agents to the Kaggle Pokemon TCG AI Battle ladder, using the ladder itself as the evaluator of record (local eval is a sanity filter, not the judge). Every step auto-logs the material the Strategy-category writeup (70% methodology, entry deadline 2026-09-06) will be written from. Simulation-category deadlines: entry 2026-08-09, final submissions 2026-08-16.

## Purpose decisions (locked during brainstorm)

- **Ladder as evaluator:** daily submissions exist to gather information about the real opponent field, which local self-play provably cannot simulate (Slices 4-6: offline evaluator gains never moved arena win rate). Optimize information gained per submission.
- **Variant axes from day 1:** both deck variants (on heuristic-v0) AND agent configs (ISMCTS search variants with the v2 value net, root PUCT prior configs) enter the submission stream immediately.
- **Fully automated:** no per-submission human approval. The prior standing rule (Brad approves each submission description) is RETIRED for this pipeline, replaced by: (a) a versioned-identity description standard, (b) a digest Brad can read anytime, (c) Brad can pause the pipeline at will.

## Section 1: System overview

Four pipeline stages plus a training daemon, all local, all git-logged:

- **Candidate queue** — git-tracked ledger of candidates awaiting evaluation.
- **Evaluation runner** — nightly (Windows Task Scheduler) and on-demand; runs local arena series for queued candidates.
- **Submission stage** — auto-submits qualifying candidates via the Kaggle CLI (`uvx kaggle competitions submit`), builds bundles via the existing `package_submission.py` machinery.
- **Harvester** — event-driven pull of all ladder data Kaggle exposes; runs at the START of every factory cycle so decisions always use the freshest data (not on a daily clock — a daily clock delays data).
- **Continuous-training daemon** — keeps the GPU busy at all times training the next net while the CPU generates data for the one after (producer-consumer overlap). Trainer is a pluggable interface; 7A occupant = per-deck value nets; 7B occupant = policy-improvement trainer.
- **Weekly Claude review** — a documented session checklist: re-prioritize the queue from ladder data, author new candidates, record strategy decisions in the methodology journal.

**Versioned identities:** every candidate has a durable name + semantic version (e.g. `lucario-heuristic v1.0`, `search-v2net v0.3`). The ledger maps each version to its exact deck, agent config, net weights file, and git commit — every ladder score is forever traceable to reproducible code. Kaggle submission descriptions are auto-generated from this identity.

## Section 2: Candidate model

A candidate = `(id, name, version, deck, agent_kind, agent_config, provenance, priority, status)`. Status lifecycle: `queued -> evaluating -> evaluated / evaluated-below-incumbent -> submitted -> scored / retired`.

Initial pool (~13): the 9 Slice-3 candidate decks on heuristic-v0, plus search variants (tuned search + v2 net; root-PUCT-prior config) on top decks. Initial priorities are seeded by the asymmetric-pairing test result (Section 7).

Candidate generation this slice is curated (weekly review authors deck tweaks and configs) plus daemon-produced (each completed training run registers its net as a new candidate version automatically). No auto-evolved decks this slice.

## Section 3: Local evaluation (sanity filter, not gatekeeper)

Nightly and on-demand: top-priority queued candidates run arena series vs a fixed baseline (current ladder pair: heuristic-v0 + mega-lucario-fighting), ~150-300 games each within a hard wall-clock budget. Submission eligibility requires: (a) no crashes/timeouts, (b) passes existing `verify_bundle` + `smoke_bundle`, (c) clears a low floor (~45% WR vs baseline) so a lemon never occupies a counted ladder slot. Per-candidate crash isolation; partial results persist; the queue resumes where it stopped.

## Section 4: Submission policy

- **Better-than-incumbent gate (default):** a candidate uploads when its local eval beats the incumbent (the weaker of the two currently-counted submissions, by local WR measure). Otherwise it is marked `evaluated-below-incumbent` and the factory proceeds to the next version.
- **Exploration exception:** a candidate on a novel axis (first search-config submission, first outing of a new deck archetype) may submit at parity, flagged `exploratory` — the ladder yields information the local arena cannot.
- **Cadence:** default rhythm 2/day (gives each submission roughly 24h of rating convergence in a counted slot), but cadence is a rhythm, not a ceiling: a clearly-better candidate submits immediately regardless of cadence. Hard cap: Kaggle's 5/day, enforced by a local persistent counter. Both knobs configurable.

## Section 5: Continuous harvest (event-driven)

At the start of every factory cycle (evaluation run, submission decision, training-run registration), pull the latest: per-submission scores over time (`kaggle competitions submissions`), leaderboard position, and any episode/match data the simulation API exposes. **Plan task:** investigate what episode data is retrievable (opponent decks and move logs from real ladder games would be the highest-value data in the system - real-field metagame for deck design, real opponent behavior for future belief modeling). Every harvest appends to the ledger; score trajectories per version drive queue re-prioritization.

## Section 6: Writeup capture

Three git-tracked artifacts, mostly auto-written:
- `experiments/LADDER.md` — one row per submission: version, date, description, score trajectory, verdict.
- `experiments/EXPERIMENTS.md` — existing local arena ledger, unchanged.
- `docs/writeup-notes.md` — append-only methodology journal; factory scripts log decisions with reasons; the weekly review adds strategic prose.

The Strategy report becomes an editing job over these ledgers.

## Section 7: Asymmetric-pairing discriminating test (runs FIRST)

The Slice-6 carry-forward experiment, now with a second job: seeding factory priorities.

- **Design:** 3 deck pairings with known strength gaps from the Slice-3 tournament ledger (strong-vs-weak, strong-vs-mid, mid-vs-mid). Each pairing runs both orientations x two matchups: `search-vs-v0` (treatment) and `v0-vs-v0` (control) on identical deck assignments. ~150 games per cell.
- **Metric:** the differential — search's WR minus the v0-vs-v0 control WR on the same pairing/orientation. The control strips deck-strength effects, isolating what search adds (same control discipline as Slice 6's hte-differential; see project rule on control evaluators for distribution-shift diagnostics).
- **Decision criteria (pre-registered):** consistent positive differential (pooled CI above 0) -> search has an exploitable asymmetric edge -> search candidates get top factory priority and the 7B policy-improvement trainer is armed with confidence. Flat/negative -> no-edge hypothesis strengthens -> search candidates enter the queue at low priority flagged `exploratory` (the real ladder still gets its say), and 7B proceeds as the remaining untested hypothesis.
- Replication discipline per `.claude/rules/stochastic-gate-replication.md` applies to any near-threshold result.

## Section 8: Continuous-training daemon

Keeps the GPU constantly training. Producer-consumer pipeline: CPU generates self-play data (native engine) for net N+1 while the GPU trains net N. On completion, the daemon exports weights (existing stdlib-JSON serving format), registers a new candidate version in the queue, and starts the next cycle.

- **Pluggable trainer interface:** 7A ships `PerDeckNetTrainer` — trains value nets on per-deck / per-matchup self-play data. Rationale: all existing nets were trained exclusively on mega-lucario mirror data, so search candidates on the other 8 decks currently run an off-distribution net; per-deck nets are genuinely new signal (NOT a rerun of the closed evaluator-quality hypothesis — the claim is distribution match for other decks, not that better nets beat v0 on mega-lucario).
- 7B swaps in `PolicyImprovementTrainer` (search-guided self-play iteration) behind the same interface; no factory rework needed.
- Honest framing (recorded): value-net-quality improvements on the mirror matchup are a PROVEN dead-end for win rate (Slices 4-6). The daemon's 7A value is (a) proving the continuous-training infrastructure end-to-end and (b) distribution coverage for non-mirror decks. The win-rate hypothesis lives in 7B.

## Section 9: Error handling and testing

- Per-candidate crash isolation in the evaluation runner; partial results persist; queue resumes.
- Kaggle upload failures: one retry, then log and defer to next cycle; never blocks evaluation. Persistent local submission counter enforces the 5/day cap across restarts.
- Every text write uses `encoding="utf-8"` (Slice-3 wipe lesson; existing guard test extended to factory files).
- Every auto-built bundle passes `verify_bundle` + `smoke_bundle` before upload; no torch/numpy in bundles (existing production invariant).
- Tests: unit tests for queue ops, versioning, incumbent-comparison, description templating, cadence/cap logic; end-to-end dry-run mode (`--no-submit`) exercising the full pipeline against a fake Kaggle client.
- Daemon and nightly runner are orchestrator-owned background processes during development sessions (per `.claude/rules/background-arena-execution.md`).

## Section 10: Out of scope (Slice 7A)

- Policy-improvement trainer internals (Slice 7B).
- Auto-evolved decks (mutation/crossover) — weekly review authors variants by hand.
- Bandit/optimizer queue-priority math — priority is a simple score; the weekly Claude review is the brain.

## Success criteria (7A)

1. Asymmetric-pairing test executed with pre-registered decision criteria; result and decision recorded in EXPERIMENTS.md and the analysis doc.
2. Factory end-to-end dry-run green (fake Kaggle client), plus at least one REAL automated submission cycle executed (harvest -> evaluate -> gate -> upload -> ledger row).
3. Daemon completes at least one full per-deck training cycle (data gen -> train -> export -> candidate registered) with CPU/GPU overlap demonstrated.
4. All ledgers auto-populated by the pipeline itself (no hand-written rows).
5. Full test suite green; no torch/numpy in any built bundle.
