# Evolutionary Agent Population Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the factory's hand-authored candidate pipeline with steady-state co-evolution: two gene pools (ISMCTS agent configs + decks) rated by two-factor Bradley-Terry, bred/culled inside the matrix worker's locked tick, best-cell submitted through the existing gate; phase 2 harvests real Kaggle ladder episodes and injects the observed meta's decks as fixed anchor opponents.

**Architecture:** New modules `genomes.py` (pools + ledgers), `breeding.py` (mutation/crossover), `evolution.py` (cell tournament tick) sit beside the existing `tournament.py` (which stays as the legacy fallback). `bt.py` gains a two-factor fit. The watch loop gains a best-cell snapshot step (feeding the existing gate unchanged) and, in phase 2, an episode-harvest step. `candidates.json` schema is unchanged — it becomes the ledger of submitted/submittable cell snapshots.

**Tech Stack:** Python 3.11, pure stdlib in all factory/serve paths (no numpy/torch), pytest, Kaggle CLI via `uvx kaggle`, Windows host.

**Spec:** `docs/superpowers/specs/2026-07-21-evolutionary-agent-population-design.md` (approved 2026-07-21).

## Global Constraints

- **Ladder identity files untouched:** `src/ptcg/submission_main.py`, `src/ptcg/agents/current.py` must show an empty diff at Finish.
- **Pure stdlib** in `src/ptcg/factory/**` and `src/ptcg/search/**` serve paths (torch is dev-only, unchanged).
- **Every text-file write passes `encoding="utf-8"`** (Windows cp1252 truncate-then-crash rule). Every JSON ledger write is atomic: write `{path}.tmp.{pid}` then `os.replace`.
- **All new shared-file writers use fresh-load-under-lock merge semantics** (`candidates.merge_save` pattern); every new read-modify-write window gets an interleaved-mutation test (`.claude/rules/single-actor-worker-tests.md`).
- **PAUSE file** (`experiments/factory/PAUSE`) halts every new loop path (breeding, harvesting) — same check position as existing workers.
- **Seeded RNG everywhere in breeding** (`random.Random(seed)`), seed persisted and logged per birth.
- Repo paths contain a space (`Dev Folder`) — always quote in commands. Run tests with `uv run pytest ...` from the repo root.
- Suite baseline at branch point: **506 passed / 0 failed**. Every task ends with its own tests green; T9 and T12 run the full suite.
- New pool files live at `experiments/factory/agent_pool.json` and `experiments/factory/deck_pool.json`; episode artifacts under `experiments/factory/episodes/`. All are **gitignored except** nothing — they follow the factory's no-autocommit convention (untracked runtime state, committed only by session close-out like candidates.json… which IS tracked; match candidates.json: tracked, updated by workers, committed at session boundaries). Do not add them to .gitignore.
- **Scale notes (checked at plan time):** cell pair selection scans C(~150,2) ≈ 11k pairs per tick — trivial. Two-factor fit is O(iters × distinct-pairs) ≈ 2000 × few-thousand — sub-second, pure floats. Neither grows with total games played (pair aggregation bounds them).

## File Structure

| File | Role |
|---|---|
| `src/ptcg/factory/genomes.py` (new) | `AgentGenome`/`DeckGenome` dataclasses, gene spec + bounds, pool load/save/merge-save, cell-id helpers |
| `src/ptcg/factory/breeding.py` (new) | mutation + crossover for both populations, offspring factory, parent selection |
| `src/ptcg/factory/bt.py` (modify) | add `fit_two_factor` (additive log-strength gradient fit) |
| `src/ptcg/factory/evolution.py` (new) | cell pool assembly, coverage-balanced + playoff pairing, `evolution_tick` (play → record → rate → cull/breed under lock), best-cell selection + candidate snapshot |
| `src/ptcg/factory/evaluate.py` (modify) | `build_agent` gains 4 missing gene kwargs + optional net |
| `src/ptcg/factory/trainer_worker.py` (modify) | `pick_next_deck` reads deck pool when present; post-train net attach |
| `scripts/factory_matrix_worker.py` (modify) | tick dispatch: pools exist → `evolution_tick`, else legacy `worker_tick` |
| `scripts/factory_watch_once.py` + `src/ptcg/factory/cycle.py` (modify) | best-cell snapshot step; skip legacy refill when pools exist; phase-2 harvest step |
| `scripts/seed_evolution_pools.py` (new) | one-shot founder-pool seeding from current candidates (post-merge go-live) |
| `src/ptcg/factory/episodes.py` (new, phase 2) | manifest check, download-filter-extract-delete, stamp, meta-deck aggregation |
| `src/ptcg/factory/kaggle_client.py` (modify, phase 2) | dataset metadata/download wrappers + fakes |
| `src/ptcg/factory/dashboard.py` (modify) | population panel (T9), loss panel (T11) |

Existing `tournament.py` (492 lines, at the 500-line ceiling) is NOT grown — evolution logic imports its primitives (`MatrixLedger`, `play_block`, `_append_block_provenance`, `save`, `_write_heartbeat`) from it.

---

### Task 1: Genome models + pool ledgers (`genomes.py`)

**Files:**
- Create: `src/ptcg/factory/genomes.py`
- Test: `tests/test_factory_genomes.py`

**Interfaces:**
- Produces: `AgentGenome`, `DeckGenome` dataclasses; `GENE_SPEC: dict[str, tuple]` (numeric bounds) and `CATEGORICAL_GENES: dict[str, tuple]`; `load_pool(path) -> list[AgentGenome|DeckGenome]`; `save_pool(path, genomes)`; `pool_merge_save(path, modified) -> list`; `agent_genome_id(config: dict) -> str`; `deck_genome_id(cards: list[int]) -> str`; `cell_id(agent_id, deck_id) -> str`; `split_cell_id(cid) -> tuple[str, str]`.
- Consumes: `candidates.ledger_lock` (`src/ptcg/factory/candidates.py:128`), `deck_matrix.deck_hash` (`src/ptcg/factory/deck_matrix.py:17`).

Genome shapes (dataclasses with `to_dict`/`from_dict`, unknown JSON keys dropped, missing keys defaulted — same back-compat convention as `Candidate`):

```python
# statuses: "live" | "retired" | "anchor" | "meta-anchor" (meta-anchor is deck-only)
@dataclass
class AgentGenome:
    id: str                    # "ag-" + sha1(canonical config json)[:10]
    kind: str                  # "search" | "heuristic" (heuristic only for anchors)
    config: dict               # full gene dict (see GENE_SPEC keys)
    status: str = "live"
    lineage: list = field(default_factory=list)   # parent genome ids
    born_at: str = ""          # ISO UTC
    seed: int = 0              # RNG seed used at this genome's birth
    rating: float | None = None   # two-factor log-strength (additive scale)
    games: int = 0
    notes: str = ""

@dataclass
class DeckGenome:
    id: str                    # "dk-" + deck_hash(cards)[:10]
    cards: list = field(default_factory=list)      # 60 card ids
    csv: str = ""              # repo-relative path of the written deck csv
    net_weights: str | None = None  # repo-relative value-net weights path
    status: str = "live"
    lineage: list = field(default_factory=list)
    born_at: str = ""
    seed: int = 0
    rating: float | None = None
    games: int = 0
    notes: str = ""
```

Gene spec — bounds and mutation scales (numeric: `(lo, hi, sigma, cast)`); these are the ONLY evolvable keys, all consumed by `build_agent` after Task 4:

```python
GENE_SPEC = {
    "search_budget_ms":       (50,   1000, 60,   int),
    "rollout_depth":          (0,    24,   3,    int),
    "deviate_min_visits":     (0,    60,   6,    int),
    "deviate_value_edge":     (0.0,  0.4,  0.04, float),
    "c_uct":                  (0.5,  3.0,  0.25, float),
    "c_puct":                 (0.5,  3.0,  0.25, float),
    "prior_tau":              (0.02, 1.0,  0.1,  float),
    "max_depth":              (20,   80,   6,    int),
    "robust_min_visits":      (1,    20,   2,    int),
    "deviate_min_visit_frac": (0.0,  0.5,  0.05, float),
}
CATEGORICAL_GENES = {
    "final_move_rule": ("most_visited", "max_value"),
    "use_root_prior":  (False, True),
}
```

`agent_genome_id`: `"ag-" + hashlib.sha1(json.dumps(config, sort_keys=True).encode()).hexdigest()[:10]`. `cell_id(a, d)` = `f"cell~{a}~{d}"`; `split_cell_id` splits on `"~"` and raises `ValueError` on malformed input. (No existing candidate id contains `~` — verified by grep of candidates.json id conventions, `make_id` at candidates.py:41.)

`save_pool`: atomic tmp-then-`os.replace`, `encoding="utf-8"`, `parents=True` mkdir first (virgin-dir rule). `load_pool`: returns `[]` for missing file. `pool_merge_save(path, modified)`: same semantics as `candidates.merge_save` (candidates.py:189) — acquire `ledger_lock(path)`, fresh-load, replace by id, append new ids, save; NOT reentrant.

- [ ] **Step 1: Write failing tests** in `tests/test_factory_genomes.py`: (a) round-trip: build one `AgentGenome` + one `DeckGenome`, `save_pool` → `load_pool`, fields equal; (b) back-compat: hand-write a JSON row with an unknown key `"zzz"` and a missing `rating` — loads with default, unknown dropped; (c) **virgin-dir**: `save_pool(tmp_path / "never" / "made" / "agent_pool.json", [...])` where the parent chain does NOT exist — succeeds (do not pre-create); (d) `cell_id`/`split_cell_id` round-trip + `ValueError` on `"garbage"`; (e) `agent_genome_id` is order-insensitive over dict key insertion; (f) **interleaved-mutation**: load pool, mutate genome A in memory; meanwhile write genome B to disk via a second `save_pool` call (simulated concurrent actor appending a fresh load + B); then `pool_merge_save(path, [A])`; assert B survived on disk alongside updated A.
- [ ] **Step 2: Run tests, verify FAIL** — `uv run pytest tests/test_factory_genomes.py -v` → import error (module missing).
- [ ] **Step 3: Implement `genomes.py`** per the shapes above.
- [ ] **Step 4: Run tests, verify PASS.**
- [ ] **Step 5: Commit** — `git add src/ptcg/factory/genomes.py tests/test_factory_genomes.py` ; `git commit -m "feat: genome models + pool ledgers for evolutionary population"`

### Task 2: Breeding operators (`breeding.py`)

**Files:**
- Create: `src/ptcg/factory/breeding.py`
- Test: `tests/test_factory_breeding.py`

**Interfaces:**
- Consumes: `GENE_SPEC`, `CATEGORICAL_GENES`, genome classes + id fns (Task 1); `deck_matrix.MUTATION_RULES`/`apply_rule` (deck_matrix.py:126/134), `deck_matrix._card_db` (line 13), `deck_matrix.write_deck_csv` (line 21); `ptcg.decks.validate.validate_deck` (validate.py:22).
- Produces: `mutate_agent(rng, parent) -> dict`; `crossover_agent(rng, pa, pb) -> dict`; `crossover_deck(rng, cards_a, cards_b) -> list[int] | None`; `mutate_deck(rng, cards) -> list[int] | None`; `breed_agent(rng, parents: list[AgentGenome]) -> AgentGenome`; `breed_deck(rng, parents: list[DeckGenome], out_dir: Path) -> DeckGenome | None`; `select_parents(rng, pool, k=2, top_frac=0.25) -> list`.

Rules (complete, transcribe as written):

```python
MUTATE_P_NUMERIC = 0.35      # per-gene chance of jitter
MUTATE_P_CATEGORICAL = 0.15  # per-gene chance of flip
CROSSOVER_RETRIES = 8        # deck crossover legality retries before fallback

def mutate_agent(rng, config: dict) -> dict:
    child = dict(config)
    for gene, (lo, hi, sigma, cast) in GENE_SPEC.items():
        if rng.random() < MUTATE_P_NUMERIC:
            child[gene] = cast(min(hi, max(lo, child.get(gene, lo) + rng.gauss(0, sigma))))
    for gene, choices in CATEGORICAL_GENES.items():
        if rng.random() < MUTATE_P_CATEGORICAL:
            child[gene] = rng.choice([c for c in choices if c != child.get(gene, choices[0])])
    return child

def crossover_agent(rng, pa: dict, pb: dict) -> dict:
    keys = list(GENE_SPEC) + list(CATEGORICAL_GENES)
    return mutate_agent(rng, {k: (pa if rng.random() < 0.5 else pb).get(k) for k in keys
                              if (pa if True else pb) is not None})  # see note below
```

NOTE for implementer: the dict-comprehension above must skip keys missing from BOTH parents (fall back to `GENE_SPEC` lo / first categorical choice) — write it as an explicit loop, not the compressed form; the compressed form here is illustrative of uniform-per-gene inheritance only. (Hand-verified plan note per `.claude/rules/plan-test-arithmetic-sanity.md`: transcribing the lambda-ish comprehension verbatim would produce `pa` always — write the explicit loop.)

`crossover_deck(rng, a, b)`: build `pool = list(a) + list(b)`, `rng.shuffle(pool)`; walk it appending card ids into `child` while respecting caps computed the same way `validate_deck` does (per-NAME count ≤ 4 excluding `CardType.BASIC_ENERGY`; total aceSpec ≤ 1 — use `_card_db()` lookups); stop at 60. If the walk exhausts before 60, top up with the most-common basic energy id in the parents. Then final oracle: `validate_deck(child) == []` else retry (fresh shuffle) up to `CROSSOVER_RETRIES`, else return `None`. `mutate_deck`: `rng.choice(list(MUTATION_RULES))` → `apply_rule`; on `None`, try each remaining rule in rng order; all-fail → `None`.

`breed_deck`: 70% crossover / 30% mutate (single parent) — on `None` from either, fall back to the other op, then to a straight copy-of-best-parent + `mutate_deck`; if STILL `None`, return `None` (caller skips this birth). Writes csv via `write_deck_csv(out_dir / f"evolved-{deck_id}.csv", cards)`, `net_weights` inherited from the higher-rated parent that has one (else `None`), lineage = parent ids, seed recorded. `breed_agent`: crossover if 2 parents else mutate; id from `agent_genome_id(child_config)`; `kind="search"`.

`select_parents`: rank live genomes by `rating` (None sorts last), take `max(2, ceil(top_frac * len(live)))` head, `rng.sample` k from it (k=1 allowed when pool tiny).

- [ ] **Step 1: Write failing tests**: (a) **legality property test** — seed `random.Random(7)`, load two real seed decks via `deck_matrix.matrix_decks()`'s first two csv paths (`ptcg.arena.runner.load_deck`), run `crossover_deck` 200 times: every non-None child passes `validate_deck(child) == []` and `len(child) == 60`; (b) **reproducibility** — two `random.Random(42)` instances produce identical `breed_agent` config and identical `crossover_deck` output; (c) **bounds** — mutate a config sitting at every gene's `hi` 100 times with `Random(3)`; every value stays within `[lo, hi]` and has the right type; (d) categorical flip only ever produces values from `CATEGORICAL_GENES`; (e) `breed_deck` fallback: parents whose crossover is forced to fail (monkeypatch `crossover_deck` → None) still yields a mutated-copy child or clean `None`; (f) `select_parents` on a 12-genome pool with known ratings returns only members of the top-25% head.
- [ ] **Step 2: Run tests, verify FAIL.**
- [ ] **Step 3: Implement `breeding.py`.** Degenerate-input note for the reviewer (standing rule): probe empty-parent-list and single-parent pools explicitly.
- [ ] **Step 4: Run tests, verify PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat: mutation + crossover breeding operators for both populations"` (explicit paths).

### Task 3: Two-factor Bradley-Terry (`bt.py`)

**Files:**
- Modify: `src/ptcg/factory/bt.py` (append; existing `fit_ratings`/`head_to_head_p` untouched)
- Test: `tests/test_factory_bt_two_factor.py`

**Interfaces:**
- Produces: `fit_two_factor(pair_wins: dict[tuple[str, str], int], parse=split_cell_id) -> tuple[dict[str, float], dict[str, float]]` — input is `MatrixLedger.wins_dict()` keyed by CELL ids; returns `(agent_log_strengths, deck_log_strengths)`, each zero-mean (identifiability pin, tested).
- Consumes: `split_cell_id` (Task 1).

Model: `P(cellA beats cellB) = sigmoid((a_i + d_j) - (a_k + d_l))` on log scale. Fit by full-batch gradient ascent on the smoothed log-likelihood (add `SMOOTH = 0.1` pseudo-wins each direction per observed pair, mirroring bt.py:11), `LR = 0.05`, `MAX_ITERS = 2000`, stop when max abs gradient step < `1e-6`. After each iter, subtract each population's mean (zero-mean both). Cells whose id fails `parse` (legacy candidate ids in an old ledger) are skipped, not fatal. Complexity: O(iters × observed pairs) — bounded by pairs, not games (scale-check done).

Multiplicative bridge for the existing gate: `cell_strength = math.exp(a + d)` is on the same multiplicative scale `head_to_head_p` (bt.py:58) consumes — Task 6 uses this.

- [ ] **Step 1: Write failing tests** (deterministic — no live RNG in expected values): (a) **synthetic recovery**: plant `a = {"ag-strong": +0.8, "ag-weak": -0.8}`, `d = {"dk-good": +0.5, "dk-bad": -0.5}`; for every ordered cell pair set `wins[(A,B)] = round(200 * sigmoid(sA - sB))` and `wins[(B,A)] = 200 - that` (hand-check one value: cell(strong,good)=+1.3 vs cell(weak,bad)=-1.3 → diff 2.6 → sigmoid ≈ 0.9309 → 186 of 200; verify this arithmetic when transcribing); fit recovers the ORDERINGS exactly (strong > weak, good > bad) and each recovered gap within ±0.25 of planted; (b) zero-mean: both returned dicts sum to ≈0 (abs < 1e-6); (c) legacy ids: a wins dict containing one `("old-id-a", "old-id-b")` pair plus cell pairs → no exception, old ids absent from output; (d) empty input → `({}, {})`; (e) single-cell degenerate input → that cell's factors at 0.0.
- [ ] **Step 2: Run tests, verify FAIL** (function absent).
- [ ] **Step 3: Implement `fit_two_factor`.**
- [ ] **Step 4: Run tests, verify PASS**; also `uv run pytest tests/test_factory_bt* -v` (old one-factor tests still green).
- [ ] **Step 5: Commit** — `git commit -m "feat: two-factor Bradley-Terry fit (agent + deck factors)"`.

### Task 4: Cell assembly + `build_agent` gene completion

**Files:**
- Create: `src/ptcg/factory/evolution.py` (first slice: cells only)
- Modify: `src/ptcg/factory/evaluate.py:53-75` (`build_agent`)
- Test: `tests/test_factory_evolution_cells.py`

**Interfaces:**
- Produces (evolution.py): `Cell` dataclass (`agent: AgentGenome`, `deck: DeckGenome`, `id` property = `cell_id(...)`); `active_cells(agents, decks) -> list[Cell]` (all non-retired agents × non-retired decks; a heuristic-kind agent pairs with every deck too); `cell_candidate_view(cell) -> Candidate` — transient Candidate for `play_block`: `agent_kind = "heuristic"` if `cell.agent.kind == "heuristic"` else `"search-net"`, `agent_config = {**cell.agent.config, **({"net_weights": cell.deck.net_weights} if cell.deck.net_weights else {})}`, `deck = cell.deck.csv`, `id = cell.id`.
- Modify (evaluate.py `build_agent`): search-net branch additionally maps `c_uct`, `max_depth`, `robust_min_visits`, `deviate_min_visit_frac` from cfg into `SearchConfig` (defaults per searcher.py:74-98: 1.4, 40, 5, 0.0), and makes the evaluator OPTIONAL — `evaluator=ValueNetEvaluator.load(...)` only when `"net_weights" in cfg`, else `SearchAgent` is constructed without `evaluator=` (hand-tuned evaluator path, the Slice-2 default).
- Consumes: Task 1 types; `Candidate` (candidates.py:46); `SearchConfig` fields verified at `src/ptcg/search/searcher.py:74-98`.

- [ ] **Step 1: Write failing tests**: (a) `active_cells` on 2 live + 1 retired agent × 2 live decks → 4 cells, retired excluded; (b) `cell_candidate_view` carries deck csv, merged net_weights, and id round-trips through `split_cell_id`; (c) `build_agent` on a view whose config sets `c_uct=2.0, max_depth=30, robust_min_visits=9, deviate_min_visit_frac=0.2` and NO net_weights → returns a `SearchAgent` whose `config.c_uct == 2.0` etc. and whose evaluator is the default (no ValueNet loaded — assert on the agent's evaluator attribute or name, check searcher/search_agent source for the exact attribute at implement time and cite it in the test); (d) heuristic-kind cell view builds a `HeuristicAgent`.
- [ ] **Step 2: Run tests, verify FAIL.**
- [ ] **Step 3: Implement** (evolution.py cells section + evaluate.py edit).
- [ ] **Step 4: Run** task tests + `uv run pytest tests/test_factory_evaluate*.py tests/test_factory_tournament*.py -v` (no regression in existing build_agent consumers).
- [ ] **Step 5: Commit** — `git commit -m "feat: agentxdeck cells + full gene surface in build_agent"`.

### Task 5: `evolution_tick` — play, rate, cull, breed under lock

**Files:**
- Modify: `src/ptcg/factory/evolution.py` (main body)
- Modify: `scripts/factory_matrix_worker.py` (tick dispatch)
- Test: `tests/test_factory_evolution_tick.py`

**Interfaces:**
- Produces: `next_cell_pair(cells, ledger, playoff: bool) -> tuple[Cell, Cell] | None`; `evolution_tick(paths, *, series_fn=None, now=None, log=print, rng=None) -> str` (returns "paused" | "idle" | "played", mirroring `worker_tick` at tournament.py:407); constants `TARGET_AGENTS = 12`, `TARGET_DECKS = 12`, `GENOME_RETIRE_FLOOR = 40`, `PLAYOFF_EVERY = 5`, `PLAYOFF_TOP = 5`.
- Consumes: `tournament.MatrixLedger/save/play_block/_append_block_provenance/_write_heartbeat/_matrix_path` (tournament.py:54/123/193/360/394/390); `genomes.load_pool/pool_merge_save`; `breeding.breed_agent/breed_deck/select_parents`; `bt.fit_two_factor`.

Pairing key (deterministic): non-playoff ticks pick `min` over cell pairs by `(sum of the four involved genomes' games, ledger.games_between(a.id, b.id), a.id, b.id)` — the genome-games term is what balances coverage across BOTH populations (an under-played agent or deck drags its cells to the front). Playoff ticks (every `PLAYOFF_EVERY`th, counter persisted in `ledger.meta["evo_tick"]`): restrict to the top `PLAYOFF_TOP` cells by `exp(a+d)` strength, pick the fewest-games pair among them (fall through to normal rule if < 2 rated cells).

Tick order (mirrors `worker_tick`'s TOCTOU discipline, tournament.py:409-445 — read that docstring before implementing):

1. heartbeat → PAUSE check (return "paused")
2. load both pools (unlocked), assemble cells; < 2 cells → "idle"
3. read `ledger.meta["evo_tick"]` count → choose pair (playoff or coverage rule) → `play_block(cell_candidate_view(a), cell_candidate_view(b), series_fn=series_fn)` **outside any lock** (games are slow)
4. under `ledger_lock(matrix_path)`: reload matrix ledger fresh; `ledger.record(cell_a.id, cell_b.id, wins_a, wins_a + wins_b)` (draws-excluded rule — copy the CRITICAL comment from tournament.py:465-467 verbatim); `_append_block_provenance`; increment `ledger.meta["evo_tick"]`; `save`
5. still under the matrix lock: **reload both pools fresh from disk** (the pre-play copies are minutes stale — same stale-clobber reasoning as worker_tick's candidates reload, tournament.py:472-478); `fit_two_factor(ledger.wins_dict())` → write `rating`/`games` onto every genome (games = sum of `ledger.total_games(cell.id)` over the genome's cells)
6. **cull/breed per population** (still under lock): for each of (agents, decks): live = status "live"; if `len(live) >= TARGET` and the lowest-rated live genome has `games >= GENOME_RETIRE_FLOOR` → set it "retired" and breed ONE offspring from `select_parents` (deck offspring csv written to `deck_matrix.GENERATED_DIR`); if `len(live) < TARGET` → breed one offspring without retiring (fills the pool gradually). Anchors/meta-anchors are excluded from live, never retired, never parents.
7. `pool_merge_save` both pools; return "played"

`rng` param: default `random.Random(ledger.meta["evo_tick"])` — deterministic per tick, reproducible; births log `seed` into the genome.

`scripts/factory_matrix_worker.py`: at each loop iteration, `evolution_tick(paths, ...)` if `paths.root / "experiments/factory/agent_pool.json"` exists else legacy `worker_tick(paths, ...)` — grep the script for its current `worker_tick` call and wrap the dispatch there; thread `--block-games` through identically (`make_series_fn`, tournament.py:178).

- [ ] **Step 1: Write failing tests** (fake `series_fn` returning fixed `(6, 4)`; `tmp_path` FactoryPaths):
  (a) full tick on seeded pools (3 agents × 3 decks) plays one block, records cell ids in the matrix ledger, writes ratings onto genomes;
  (b) PAUSE file → "paused", nothing written; single-cell pool → "idle";
  (c) coverage pairing: after several ticks, assert min genome games ≥ (max genome games − 2×BLOCK_GAMES) across each population — no starving (loop the tick ~15 times);
  (d) playoff tick: set `meta["evo_tick"] = 4` (next tick is 5th) with planted ratings → the chosen pair ⊆ top-5 cells;
  (e) cull/breed: pool at TARGET with worst genome planted `games=50, rating=-2.0` → after tick, worst is "retired", a new live genome exists with lineage ⊆ top-quartile parent ids, pool size unchanged;
  (f) under-target growth: 4 live agents → tick births one WITHOUT retiring;
  (g) **interleaved-mutation (mandatory)**: monkeypatch `play_block` to, mid-"game", write a concurrent update to `deck_pool.json` (a new meta-anchor genome via `pool_merge_save`) — after the tick completes, that genome exists on disk (survived) AND the tick's own rating writes are present;
  (h) reproducibility: two identical tmp setups run 3 ticks each → identical pool JSON (byte-compare after normalizing `born_at`).
- [ ] **Step 2: Run tests, verify FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** task tests + `uv run pytest tests/test_factory_tournament*.py -v` (legacy tick untouched).
- [ ] **Step 5: Commit** — `git commit -m "feat: steady-state evolution tick (play/rate/cull/breed under matrix lock)"`.

### Task 6: Best-cell selection + candidate snapshot (gate feed)

**Files:**
- Modify: `src/ptcg/factory/evolution.py` (add selection section)
- Modify: `src/ptcg/factory/cycle.py` / `scripts/factory_watch_once.py` (wire the step; grep `watch_once` at scripts/factory_watch_once.py:59 for the step sequence and insert after harvest, before gate)
- Test: `tests/test_factory_best_cell.py`

**Interfaces:**
- Produces: `CELL_MIN_GAMES = 30`, `CELL_MIN_OPPONENTS = 5`; `select_best_cell(agents, decks, ledger) -> Cell | None` — over cells with `ledger.total_games(cell.id) >= CELL_MIN_GAMES` and `len(ledger.opponents_of(cell.id)) >= CELL_MIN_OPPONENTS`, rank by `exp(agent.rating + deck.rating)` with observed cell win-rate (wins/games from `ledger.wins_dict()`) as tiebreak; `snapshot_cell(cell, ledger, candidates) -> Candidate | None` — returns a new/updated Candidate: `name = f"evo-{cell.agent.id}-{cell.deck.id}"`, `version = next_version(candidates, name)` (candidates.py:90), `deck = cell.deck.csv`, `agent_kind`/`agent_config` per `cell_candidate_view`, `provenance = "evolved"`, `status = Status.EVALUATED`, `matrix_rating = exp(a + d)`, `matrix_games`/`matrix_opponents` from the ledger cell stats, `notes` = lineage summary. Returns `None` (no-op) when an existing non-retired candidate already snapshots this exact cell (same name) — no duplicate spam.
- Consumes: gate `decide` (gate.py:238) — UNCHANGED; `has_matrix_coverage` (gate.py:207) passes because snapshot copies cell stats (30 ≥ MIN_COVERAGE_GAMES=15, 5 opp... **NOTE:** `MIN_COVERAGE_OPPONENTS = 8` (tournament.py:35) > `CELL_MIN_OPPONENTS = 5` — the snapshot would FAIL `has_matrix_coverage`. Resolve by setting `CELL_MIN_OPPONENTS = 8`, aligning with the existing constant (import it, don't re-literal). This is a plan-time-caught constant collision; implementer: import `MIN_COVERAGE_GAMES`/`MIN_COVERAGE_OPPONENTS` from tournament.py and set `CELL_MIN_GAMES = max(30, MIN_COVERAGE_GAMES)`, `CELL_MIN_OPPONENTS = MIN_COVERAGE_OPPONENTS`.)
- **Scale-bridge requirement:** whenever pools exist, the watch-loop step ALSO refreshes the pinned incumbent's `matrix_rating/matrix_games/matrix_opponents` from its anchor cell's current two-factor stats (the incumbent's agent+deck are seeded as anchors in Task 8) — this keeps `decide()`'s head-to-head comparison on ONE scale. If the incumbent's anchor cell has no coverage yet, skip the snapshot step entirely this cycle (log "evo-gate: incumbent anchor uncovered") rather than compare across scales.

Watch-loop wiring in the same task: (a) insert `evo_gate_step(paths, ...)` between harvest and gate inside `watch_once`; it loads pools + matrix ledger read-only, runs `select_best_cell` → `snapshot_cell` → `merge_save(paths.ledger, [snapshot, refreshed_incumbent])`; (b) legacy queue refill: grep `watch_once`/`run_cycle` for the `refill_queue` call — skip it when `agent_pool.json` exists (evolution owns births now).

- [ ] **Step 1: Write failing tests**: (a) `select_best_cell` honors both floors (a strong cell below either floor is not selected; the best floor-clearing cell is); (b) tiebreak by observed win rate fires when strengths within 1e-9; (c) `snapshot_cell` produces a Candidate that passes `gate.has_matrix_coverage` and carries `provenance="evolved"`, correct version bump on second snapshot of the same name; (d) duplicate-cell snapshot returns `None`; (e) incumbent scale-bridge: with a pinned `is_incumbent` candidate whose anchor cell has planted stats, the step rewrites its matrix fields to `exp(a+d)`-scale; with an uncovered anchor cell, NO snapshot lands in candidates.json this cycle; (f) refill skip: with pools present, `refill_queue` is not invoked (monkeypatch-sentinel).
- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** task tests + `uv run pytest tests/test_factory_gate*.py tests/test_factory_submit*.py tests/test_factory_watch*.py -v`.
- [ ] **Step 5: Commit** — `git commit -m "feat: best-cell playoff selection + gate snapshot with incumbent scale bridge"`.

### Task 7: Trainer coupling to the deck pool

**Files:**
- Modify: `src/ptcg/factory/trainer_worker.py` (`pick_next_deck` at line 30; `trainer_tick` at line 78)
- Test: `tests/test_factory_trainer_worker.py` (extend existing)

**Interfaces:**
- `pick_next_deck` gains pool awareness: when `deck_pool.json` exists, pick the highest-`rating` LIVE deck genome with `net_weights is None` (return its csv path/deck name in whatever form `PerDeckNetTrainer` consumes — grep `trainer_tick`/`run_daemon` for the exact argument shape at implement time and mirror it); when absent, legacy candidate-based behavior byte-identical.
- After a successful training run, `trainer_tick` attaches the produced weights path onto the deck genome (`net_weights` field) via `pool_merge_save` — insert-only-style single-field update on a freshly loaded row (the trainer holds NO pool row across the training window: load fresh AFTER training completes, set field, merge-save — this avoids a read-modify-write window spanning the hours-long train).

- [ ] **Step 1: Write failing tests**: (a) pool present → netless top-rated live deck picked; all-netted pool → legacy path consulted; (b) post-train attach: fake trainer completes → genome's `net_weights` set on disk; (c) **interleaved-mutation**: concurrent breeding write (new deck genome) lands during the fake training window → survives the trainer's attach merge-save; (d) pool absent → existing tests still green (no behavior change).
- [ ] **Step 2: Run, verify FAIL** (new tests only).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full trainer test file green.
- [ ] **Step 5: Commit** — `git commit -m "feat: trainer worker trains top-rated netless deck genomes, attaches weights post-train"`.

### Task 8: Founder seeding script + go-live rung definition

**Files:**
- Create: `scripts/seed_evolution_pools.py`
- Test: `tests/test_seed_evolution_pools.py`

**Interfaces:**
- CLI: `uv run python scripts/seed_evolution_pools.py [--dry-run]`. Reads `candidates.json` + `deck_matrix.SEEDS`; writes both pool files. **Refuses to run (loud exit 1, no writes) if either pool file already exists** — seeding is one-shot; re-seeding a live population is destructive and must be manual-delete-first.
- Founder agent pool (~12): (1) one **anchor**: `kind="heuristic"`, config `{}` (scale bridge + fast games); (2) one **anchor**: the pinned incumbent's `agent_kind/agent_config` verbatim (grep candidates.json for `is_incumbent: true` at run time); (3) ten live search genomes: the incumbent config plus 9 `mutate_agent` jitters seeded `Random(1)..Random(9)`, each config completed to FULL gene surface (missing genes filled with `SearchConfig` defaults from GENE_SPEC-documented values: budget 200, rollout 12, dmv 20, dve 0.12, c_uct 1.4, c_puct 1.5, tau 0.1, max_depth 40, rmv 5, dmvf 0.0, most_visited, root_prior False).
- Founder deck pool (~12): every distinct active-candidate deck (by `deck_hash`) ranked by `matrix_rating`, top N; incumbent deck flagged **anchor**; `net_weights` carried over from the best-rated candidate using that deck (path exists on disk verified, else None). Fill remaining slots (if < 12 distinct) from `deck_matrix.SEEDS` csvs.
- `--dry-run` prints the founder roster (ids, sources, anchor flags) and writes NOTHING. Remember: the dry-run flag means the real path is untested by dry-runs (`.claude` rule) — hence the real-path test below runs against tmp dirs.

- [ ] **Step 1: Write failing tests**: (a) real-path run against a tmp `candidates.json` fixture (3 candidates incl. one incumbent) + tmp output dir → both pools written, counts/anchors as specced, every agent config contains ALL GENE_SPEC + CATEGORICAL keys; (b) existing-pool refusal: pre-create `agent_pool.json` → exit code 1, deck pool NOT written (fail-safe ordering: check both before writing either); (c) **virgin-dir**: output parent chain does not exist → created; (d) dry-run writes nothing.
- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, verify PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat: one-shot founder pool seeding script for evolution go-live"`.
- [ ] **Step 6 (plan record, no code):** the ACTUAL seeding on master is a **post-merge go-live rung** (like the scheduler registration last slice): run `seed_evolution_pools.py` (real, not dry-run) on master with workers PAUSED, unpause, then verify within ~30 min via `matrix_blocks.jsonl` tail that cell-id blocks are being recorded and via dashboard that pools are live. No elevation needed (no scheduler changes — task names/actions unchanged). Record this rung + the **48h throughput review** (measure games/hour on the evolved pool; revisit TARGET_AGENTS/TARGET_DECKS/floors) in plan.md as explicit post-merge items.

### Task 9: Population dashboard panel + phase-1 full-suite gate

**Files:**
- Modify: `src/ptcg/factory/dashboard.py`
- Test: `tests/test_factory_dashboard.py` (extend)

Panel (read-only over pool files + matrix ledger, inside the existing `safe_render` isolation): live/retired/anchor counts per population, top-5 cells (agent id, deck id, strength, cell games), last 5 births with lineage, last 5 retirements. Degenerate-safe: missing pool files → panel renders "evolution not seeded".

- [ ] **Step 1: Write failing tests**: panel renders with pools present; renders the not-seeded placeholder with pools absent; a corrupt pool JSON does not break the page (safe_render swallows, logs).
- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Full suite: `uv run pytest`** — expect ≥ 506 + all new tests, 0 failures. This is the phase-1 regression gate.
- [ ] **Step 5: Commit** — `git commit -m "feat: evolution population dashboard panel; phase-1 suite green"`.

### Task 10 (Phase 2): Episode harvester (`episodes.py` + kaggle_client datasets)

**Files:**
- Modify: `src/ptcg/factory/kaggle_client.py` (add dataset ops + fake)
- Create: `src/ptcg/factory/episodes.py`
- Create: `scripts/probe_episode_schema.py` (one-shot, network)
- Test: `tests/test_factory_episodes.py`

**Interfaces:**
- kaggle_client additions: `dataset_files(self, dataset: str) -> str` (`self._cli("datasets", "files", "-d", dataset, "--csv")` — verify exact flag spelling against `uvx kaggle datasets files --help` during the probe step and correct here if drifted), `dataset_download(self, dataset: str, dest: Path, filename: str | None = None) -> None` (`datasets download -d <ref> -p <dest> --unzip` + `-f <filename>` when given). `FakeKaggleClient` (kaggle_client.py:118) gains matching fakes fed from fixture dicts.
- episodes.py: `INDEX_DATASET = "kaggle/pokemon-tcg-ai-battle-episodes-index"`, `DAY_DATASET_FMT = "kaggle/pokemon-tcg-ai-battle-episodes-{day}"`; `HarvestStamp` (json at `experiments/factory/episodes/harvest_stamp.json`: `{"last_day": "YYYY-MM-DD", "harvested_at": iso}`); `check_and_harvest(paths, client, *, now, log) -> str` returning "no-new" | "harvested:<day>" | "error:<msg>"; extraction record per episode (jsonl append to `experiments/factory/episodes/extracts.jsonl`, `encoding="utf-8"`): `{"episode_id", "day", "opponent_deck": [card ids], "our_result": "win"|"loss"|"draw", "timeout": bool, "n_turns": int, "opponent_deck_hash"}`. Raw downloads land in a temp subdir and are **deleted after extraction** (try/finally); retained extracts pruned oldest-day-first past `EPISODES_CAP_GB = 2`.
- Watch-loop wiring: `check_and_harvest` runs every firing, BEFORE the evo-gate step, wrapped in the same failure-isolation pattern as dashboard's `safe_render` — a harvest exception logs and never blocks gate/submit. PAUSE honored (skip entirely).
- **Schema honesty:** the per-episode JSON schema and the manifest's our-submission matching key are UNVERIFIED (never downloaded before — Slice-7A appendix T7 documented existence only). `scripts/probe_episode_schema.py` downloads the index manifest + ONE episode file, prints the top-level structure, and exits — it is run ONCE during this task (network step, requires live Kaggle auth) and its findings are pasted into `episodes.py` docstring + used to write the fixture files for tests. The extractor must be schema-tolerant: unknown/missing fields skip the episode with a logged count, never raise.

- [ ] **Step 1: Run the schema probe** (`uv run python scripts/probe_episode_schema.py` — write the script first: ~40 lines, uses `KaggleClient.dataset_download` prototypes inline before they're in the client; capture output to `experiments/factory/episodes/schema-probe.txt`). Record in the task report: manifest columns, episode JSON top-level keys, how our submissions are identified (submission id? team name?), and correct CLI flag spellings.
- [ ] **Step 2: Write failing tests** using fixtures derived from the probe: (a) stamp round-trip + **virgin-dir** first write; (b) "no-new" short-circuit when manifest shows no day newer than stamp; (c) harvest path over a fixture day: extracts written, raw deleted, stamp advanced; (d) malformed episode file → skipped + counted, run completes; (e) cap pruning: plant >cap of fake extracts → oldest day pruned; (f) failure isolation: client raising → return "error:…", watch step continues (test at the watch_once level with FakeKaggleClient raising).
- [ ] **Step 3: Run, verify FAIL.**
- [ ] **Step 4: Implement** client methods + episodes.py + watch wiring.
- [ ] **Step 5: Run, verify PASS.**
- [ ] **Step 6: Commit** — `git commit -m "feat: kaggle episode harvester (per-firing check, extract-then-delete, stamped)"`.

### Task 11 (Phase 2): Meta-anchor decks + loss panel

**Files:**
- Modify: `src/ptcg/factory/episodes.py` (aggregation), `src/ptcg/factory/dashboard.py` (loss panel)
- Modify: `docs/weekly-review-checklist.md` (append episode-review step)
- Test: `tests/test_factory_meta_anchors.py`

**Interfaces:**
- `top_meta_decks(extracts_path, *, window_days=7, k=3) -> list[list[int]]`: aggregate opponent decks over the rolling window, weight decks that beat us 2× vs decks we beat 1×, rank by weighted frequency of `opponent_deck_hash`, return the k distinct decklists.
- `inject_meta_anchors(paths, decks) -> int`: for each, build a `DeckGenome(status="meta-anchor", csv=written to GENERATED_DIR as f"meta-{hash}.csv")`, dedup by deck hash against the ENTIRE pool (any status), cap live meta-anchors at `META_ANCHORS = 3` — when full and a new deck qualifies, retire the meta-anchor with the fewest recent-window observations. Runs in the watch loop right after a successful harvest (single-writer for this path, but writes go through `pool_merge_save` — the matrix worker writes the same file concurrently: **interleaved-mutation test mandatory**.)
- Loss panel: timeout-loss %, win rate vs each of the top-5 opponent deck clusters, sample sizes — degenerate-safe when extracts are empty.
- Checklist append (exact text): `N. Episode review: open the dashboard loss panel; check timeout-loss % (>5% = investigate budget genes), our WR vs each top meta cluster, and whether current meta-anchors still match the observed top-3.`

- [ ] **Step 1: Write failing tests**: (a) `top_meta_decks` weighting (hand-verified fixture: deck X beats us twice (weight 4), deck Y seen 3× all losses-for-them (weight 3) → X ranks first; verify the arithmetic when transcribing); (b) window: extracts older than 7 days excluded; (c) inject: dedup (same hash → no duplicate), cap-3 replacement rule; (d) **interleaved-mutation**: matrix-worker breeding write during injection survives; (e) loss panel renders with data / empty extracts.
- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, verify PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat: meta-anchor deck injection from harvested episodes + dashboard loss panel"`.

### Task 12: Docs, full-suite gate, acceptance checklist

**Files:**
- Modify: `docs/factory-operations.md` (new "Evolutionary population" section: pool files, seeding runbook, go-live rung, 48h review knobs, PAUSE semantics, episode harvester operation + auth dependency)
- Test: full suite

- [ ] **Step 1: Write the ops doc section** (facts only from the implemented code — cite real constants by importing-file:line).
- [ ] **Step 2: Full suite `uv run pytest`** — 0 failures, count recorded.
- [ ] **Step 3: Verify ladder identity untouched:** `git diff master...HEAD -- src/ptcg/submission_main.py src/ptcg/agents/current.py` → EMPTY output (paste into report).
- [ ] **Step 4: Commit** — `git commit -m "docs: evolutionary population operations runbook"`.

---

## Post-merge go-live rungs (explicit, per acceptance-rung rule)

1. **Seed pools on master** (workers PAUSED via PAUSE file) → `uv run python scripts/seed_evolution_pools.py` (real run) → remove PAUSE.
2. **Verify evolution live** within ~30 min: `matrix_blocks.jsonl` tail shows `cell~…` ids; dashboard population panel live; `Get-ScheduledTask` liveness for all three tasks (unchanged names).
3. **First harvest** (phase 2): confirm one real `check_and_harvest` produced extracts or a clean "no-new", and that a failed harvest does not block a gate cycle.
4. **48h throughput review**: measured games/hour on the evolved pool; adjust TARGET_* / floors; recorded in plan.md + weekly checklist.

## Self-Review (performed at plan time)

- **Spec coverage:** every spec section maps to a task (data model→T1, BT→T3, pairing/playoffs→T5, selection/breeding→T5, submission→T6, trainer→T7, throughput governance→T5 constants + go-live rung 4, harvester→T10, meta-anchors+forensics→T11, testing reqs distributed per task, docs→T12, seeding/migration→T8).
- **Constant collision found and resolved at plan time:** CELL_MIN_OPPONENTS vs MIN_COVERAGE_OPPONENTS (Task 6) — resolved by importing the existing constant.
- **Known deliberate deferral:** episode schema is probe-first (T10 step 1) because the data has never been downloaded; fixtures derive from the probe, not guesses.
- **Type consistency:** `fit_two_factor` returns log-strengths; every gate-facing consumer converts via `exp(a+d)` (T3 bridge note, T6 snapshot, T5 playoff ranking) — one convention, stated in all three places.
