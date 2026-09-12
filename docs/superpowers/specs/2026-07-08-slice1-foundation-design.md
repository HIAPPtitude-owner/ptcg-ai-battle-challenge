# Slice 1: Foundation — Design Spec

**Date:** 2026-07-08
**Status:** Approved
**Project:** PTCG AI Battle Challenge — dual competition entries

## Context

Two linked Kaggle competitions (both entered, rules accepted):

| | Simulation | Strategy |
|---|---|---|
| Deliverable | `.tar.gz` agent bundle (`main.py` top-level + `deck.csv`) | Kaggle Writeup ≤2,000 words |
| Deadline | 2026-08-16 (entry closed 08-09) | 2026-09-13 (entry closed 09-06) |
| Scoring | Gaussian ladder (μ₀=600), only latest 2 submissions active, 5/day | Model 70% / Deck 20% / Report 10%; ladder rank feeds Model Score |
| Prize | none (prerequisite) | $30,000 × 8 finalists |
| Runtime | 2 vCPUs, 12.2 GiB RAM, ≤197.7 MiB bundle, 10-min match cap (timeout = loss) | — |

**Overall architecture (approved):** staged — (1) heuristic baseline, (2) determinized search over the SDK's `search_begin`/`search_step` forward model with handcrafted eval + anytime time manager, (3) learned value net trained via self-play on the local RTX 3060 8GB. Each stage transition is a measured experiment feeding the writeup.

**Deck strategy (approved):** data-driven — parse the card pool, build 2–3 candidate archetype decks, tournament them in the local arena, entry deck chosen by win-rate.

**Roadmap (approved):**
1. **Slice 1 — Foundation (this spec)**
2. Slice 2 — Determinized search agent + time manager
3. Slice 3 — Deck tournament + eval tuning
4. Slice 4 — Learned value net
5. Slice 5 — Strategy writeup assembly

Baseline agent is submitted to the ladder as soon as Slice 1 completes (validates packaging, gathers real opponent data; zero cost since only latest 2 submissions count).

## Slice 1 Scope

Foundation: project scaffold, local evaluation arena, baseline heuristic agent, candidate decks, submission packaging. Ends with a ladder-ready `submission.tar.gz`.

### 1. Repo layout

```
ptcg-ai-battle-challenge-strategy/
├── pokemon-tcg-ai-battle/     # licensed competition materials — READ-ONLY, git-ignored
├── src/ptcg/                  # our package (uv-managed, Python 3.11)
│   ├── agents/
│   │   ├── base.py            # Agent protocol: act(obs: Observation) -> list[int]
│   │   ├── random_agent.py    # random legal choice (mirrors sample main.py)
│   │   └── heuristic.py       # baseline v0 (see §3)
│   ├── arena/
│   │   ├── runner.py          # battle loop wrapping cg.game
│   │   └── stats.py           # win-rate, Wilson CI, timing aggregation
│   └── decks/
│       ├── analysis.py        # card-pool parser over EN_Card_Data.csv
│       ├── validate.py        # deck legality checker
│       └── candidates/*.csv   # 2–3 candidate decks
├── scripts/
│   ├── run_arena.py           # CLI: agentA vs agentB, N games, seeds, report
│   └── package_submission.py  # build + verify submission.tar.gz
├── submission/                # staging dir (generated; the bundle contents)
├── experiments/EXPERIMENTS.md # run log — the writeup's evidence base
├── tests/                     # pytest
└── CLAUDE.md                  # (exists)
```

- The `cg` SDK is vendored into `src/` (copied from the sample submission) so imports work in dev and in the bundle. Native libs: `cg.dll` (dev, Windows) + `libcg.so` (Kaggle, Linux) + others ship in the bundle exactly as the sample does.
- `pokemon-tcg-ai-battle/` is git-ignored: competition-use-only license forbids republication; it must never reach a remote.

### 2. Arena harness

- `runner.py`: `play_match(agent0, agent1, deck0, deck1, seed) -> MatchResult` using `battle_start` → loop `battle_select` → `battle_finish`. Each observation is routed to the agent indicated by `current.yourIndex` (the engine says who must act).
- `MatchResult`: winner (0/1/2=draw), turns, per-move wall-time list per agent, total time, end reason, error info if crashed.
- `run_series(agentA, agentB, decks, n_games, base_seed)` alternates first player, aggregates `SeriesStats` (win-rate + Wilson 95% CI, avg/max move time, avg game length).
- One battle per process (SDK `Battle` holds module-global state). Parallelism, if needed, via `multiprocessing` worker processes — Slice 1 ships sequential with the process-pool as a stretch item.
- Every `run_arena.py` invocation appends a structured entry (date, agents, decks, N, result, CI, timing) to `experiments/EXPERIMENTS.md`.

### 3. Baseline heuristic agent v0

Deterministic priority rules over the engine-presented legal options, dispatched by `SelectType`/`SelectContext`:

- **MAIN selection:** take a lethal attack if available; else best-damage affordable attack; else prefer (in order) evolve, attach energy toward the highest-damage attacker's unmet cost, play draw supporter when hand ≤ 3, bench a basic if slots free, else END.
- **Card selections:** context-sensitive defaults (e.g. `SETUP_ACTIVE_POKEMON` → highest-HP basic; `TO_HAND` → highest-value card by static priority; `DISCARD` → lowest-value).
- **Safety net:** any unhandled `SelectContext` (49 exist; more may be added mid-competition) falls back to "select the first `maxCount` options" — never crash, never return an invalid selection.
- Pure function of the observation; no timing risk (micro-seconds per decision).

**Acceptance:** ≥90% win-rate vs `random_agent` over 200 games; 0 crashes over 500 games; every returned selection satisfies min/max-count and index-validity invariants (asserted in the runner during tests).

### 4. Deck candidates

- `analysis.py` parses `EN_Card_Data.csv` (handle the UTF-8-mangled header encoding) into typed records; produces a pool summary: evolution lines present, energy types supported, trainer suite, ex/Mega/ACE SPEC availability.
- Build 2–3 candidate decks representing distinct archetypes chosen from what the pool actually supports (single-type aggro line, a second attacker line, trainer-heavy consistency shell — final archetypes decided from the analysis output, not assumed).
- `validate.py` enforces: exactly 60 cards, ≤4 copies per card *name* (basic energy exempt), ≥1 Basic Pokémon, ≤1 ACE SPEC total. Each candidate must also pass a live `battle_start` smoke (engine accepts the deck).
- Candidate-vs-candidate arena results recorded in EXPERIMENTS.md; best current deck becomes the submission `deck.csv`. (Full tournament rigor is Slice 3.)

### 5. Packaging

`package_submission.py`:
1. Assembles `submission/`: top-level `main.py` (self-contained agent entry adapting `ptcg.agents.heuristic` to the Kaggle `agent(obs_dict)` contract, with the `/kaggle_simulations/agent/` path fallback), `deck.csv` (chosen candidate), `cg/` (vendored SDK incl. all native libs), and the `ptcg/` modules the agent imports.
2. Creates `submission.tar.gz` with contents at archive root (equivalent of `tar -czvf submission.tar.gz *` from inside `submission/`).
3. Verifies: `main.py` at archive top level (not nested), `deck.csv` present, size < 197.7 MiB, and a local import-and-one-battle smoke run executed from the staging dir.

Kaggle upload remains a manual step (user, via My Submissions tab).

### 6. Testing

- Unit: heuristic rule dispatch on synthetic `Observation` fixtures (lethal-attack preference, energy-attachment targeting, fallback path); deck validator (each rule, pass + fail cases); stats math (Wilson CI).
- Integration: full battle heuristic-vs-random completes and reports a valid result; 200-game acceptance series (marked slow, run before Finish); packaging structure verification.
- All tests runnable via `uv run pytest`. Selection-invariant assertions live in the arena runner so every integration game also fuzzes the agent contract.

## Success criteria (Slice 1 done =)

1. `uv run pytest` green.
2. Heuristic ≥90% vs random over 200 games, 0 crashes over 500 (recorded in EXPERIMENTS.md).
3. 2–3 legal, engine-accepted candidate decks with pool-analysis rationale.
4. `submission.tar.gz` built, verified, smoke-run — handed to user for upload.
5. EXPERIMENTS.md seeded with the baseline numbers (the Stage-1 datapoint for the writeup).

## Out of scope (later slices)

Search agent, time manager, eval function, learned net, deck tournament rigor, writeup drafting.

## Risks

- **Native lib quirks on Windows dev** (paths with spaces, ASCII-only `search_begin_input`): mitigated by running everything from short-path staging during smokes if needed.
- **Engine updates mid-competition** (enums/attributes may be appended): tolerant parsing everywhere; pin no strict enum validation.
- **Sample deck IDs may not all be legal archetype pieces** — legality validator + engine smoke catches bad decks before submission.
