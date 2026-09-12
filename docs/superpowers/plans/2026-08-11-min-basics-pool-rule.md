# Min-Basics Pool Rule — Implementation Plan (2026-08-11)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task is dispatched to one implementer who sees ONLY their own task section. Steps use checkbox (`- [ ]`) syntax. Implementers run ONLY their targeted test file(s); the orchestrator owns full-suite runs at sync points (`.claude/rules/dispatch-test-run-directive.md`). Stage by explicit path; the tree carries live factory drift that must never be staged.

**Goal:** Enforce a strict >=8 basic-Pokémon-card-copies rule across the entire deck pool — validator, builder, UI, anchor, and a one-shot pool migration — with a mini-tournament-chosen compliant anchor, without breaking the Kaggle counted pair (v0.24/v0.22) or the pair-gate's evictee reconstruction.

**Architecture:** A single constant `MIN_BASIC_CARDS = 8` lives in `ptcg.decks.validate` and is consumed by `validate_deck` (hard reject), `builder.py` (which ALREADY guarantees >=8 at fill time — see Reconciliation note R1), and `deck_quality.py` (recalibrated mulligan-risk badge). The anchor deck is decoupled from the ladder-identity CSV into a committed `anchor-min8.csv` chosen by a C(4,2)x200 in-process mini-tournament, and a one-shot migration script (reseed-script precedent: `--db` required, dry-run, loud receipts) culls the 12 non-compliant decks' concepts, restores the 815 compliant single concepts to `untested`, zeroes all coverage, trashes old-regime offspring, and installs the new anchor — after which the existing census/screening/offspring pipeline re-rates the pool against the new anchor with no further hand-authoring.

**Tech Stack:** Python 3.11 / uv / pytest; sqlite3 (`deckdb._write` BEGIN IMMEDIATE discipline); the vendored `cg` engine DLL via `ptcg.arena.runner.play_match` for in-process games; PowerShell only at go-live (Scheduled Task stop/start, non-elevated).

## Global Constraints (verbatim from `docs/superpowers/specs/2026-08-11-min-basics-pool-rule-design.md`)

- "Metric: >=8 basic Pokemon card copies per 60-card deck." (matches `deck_quality.py`'s `basics_count`: copies of cards with `cardType==POKEMON and basic==True`; basic ENERGY does not count)
- "Application: strict — cull everything below, INCLUDING the current anchor and champions."
- "New anchor: chosen by a mini-tournament among 4 candidate >=8-basic decks."
- "Enforcement: the builder GUARANTEES the minimum at construction; `validate_deck` hard-rejects as safety net."
- "Sequencing: full speed — go live immediately after merge; pair-gate guards the Kaggle counted pair (v0.24/v0.22); freeze-day (~2026-08-13) curation plan unchanged."
- "Single constant `MIN_BASIC_CARDS = 8`, single source of truth."
- "(b) RETAIN concept rows and culled deck rows — cull is a status change, NEVER a row delete."
- "DESIGN-CRITICAL INVARIANT: culling v0.22/v0.24 must NOT break pair-gate evictee reconstruction."
- "EXPLICIT OVERRIDE: this supersedes the 2026-08-04 one-way exploration freeze."
- "Ladder identity files (`src/ptcg/submission_main.py`, `src/ptcg/agents/current.py`) are UNCHANGED by this slice (standing invariant; verify empty diff at Finish)."
- "Inertness for the implementation window: PAUSE file ONLY ... no UAC elevation."
- "Migration transactions follow `BEGIN IMMEDIATE` single-transaction discipline; any new query on a large table gets `EXPLAIN QUERY PLAN` + production-scale timing before review sign-off."
- "The mini-tournament is an argmax, not a pass/fail gate near a bar — single run acceptable; record all series rows in `EXPERIMENTS.md`."
- Out of scope: pair-gate bar/daily cap/subscheduler cadence/disabled floor probe; ladder identity files; agent/search-config changes.

## Capacity math (accepted-informational — Brad already chose full speed; do NOT re-open)

Measured on a backup copy of live `tournament.db` (sqlite `backup()` API, 2026-08-11 ~10:45 HST, 155.3 MB):

- Throughput: **105,324 done games in the trailing 24 h (~4,389/hr)**; trailing hour 2,562/hr. (The spec-cited ~4,400/hr is the 24-h average.)
- Post-migration re-screen population: **815 restored single concepts x SCREENING_FLOOR(15) = 12,225 screening games ≈ 2.8–4.8 h.** The 236,625 `untested` pair concepts have NO coverage rows and re-enter lazily via `activate_pair_concepts` (10/tick, prioritized by combined single ratings) — they do not gate the re-screen.
- Offspring pipeline restart from zero survivors (migration trashes all 26 non-trashed offspring): floor (50/offspring) + MATCH + CONFIRM (400) + CROWN (top-K=8, C(K,2)x100) + anchor (200) + pair-gate (200). Observed crown cadence on 2026-08-11: **5 crowns in ~9.4 h (~2.3 h/crown)** with a warm survivor pool; cold-start realistically needs several MATCH/CONFIRM cycles first.
- **Conclusion: first new-regime champion plausibly lands ~12–36 h after go-live — tight but feasible before the ~2026-08-13 freeze; NOT guaranteed.** If it does not crown in time, the counted pair (v0.24/v0.22) stays protected by the pair-gate and the freeze-day curation plan is unchanged.

## Reconciliation notes (spec vs. verified code — read before implementing)

- **R1 — builder guarantee already shipped.** `builder.py` has had `MIN_BASIC_POKEMON = 8` (line 66), `_pad_with_basics` (line 235), the `need_more_basics` unbuildable check (lines 314–320), and `brad_bounds_problems`' `basic_pokemon_count < MIN_BASIC_POKEMON` check (line 353) since tournament T2 (commit `9908306`). Empirically confirmed on the live DB copy: **all 95,895 builder-built decks have >=8 basics; exactly 12 decks are <8** (the 11 hand-seeded 2026-08-04 reseed decks + the old anchor). This slice does NOT change the fill algorithm — it re-points the constant at the new single source of truth and adds the spec-required tests (Task 3).
- **R2 — spec's migration action list omits the restoration its own "re-enters" paragraph requires.** With (a) cull-all-<8 alone, the pool would have ZERO screenable concepts (825/826 singles are already `culled`; the 1 `active` is <8) and pair activation requires `active` singles — a permanent deadlock. The migration therefore ALSO restores culled ge8 SINGLE concepts to `untested` (815 rows). **Scope decision:** the 95,080 culled BUILT pair concepts stay culled — restoring them means 95,895 x 15 ≈ 1.44M screening games (~14–23 days), infeasible before the deadline; the pair space re-enters via the 236,625 untested pairs instead. Revisitable post-deadline.
- **R3 — watch-loop import graph (live-on-write).** `scripts/factory_watch_once.py` imports `deckdb`, `episodes`, `subscheduler`, `cycle`, `gate`, `kaggle_client`, `watch`; `subscheduler` imports `anchor` + `pairgate`; `episodes` imports `breeding` (which lazy-imports `ptcg.decks.validate`). So **`anchor.py`, `pairgate.py`, `breeding.py`, and `validate.py` are watch-loop-reachable; `census.py` is NOT** (spec drift — the spec claimed census is watch-loop-imported; it is imported only by `loop_scheduler`, a long-lived worker that does not hot-reload mid-slice). The Task-1 PAUSE hold covers behavior: `factory_watch_once.main` returns at the `paths.pause_file.exists()` check (line 117) before any subscheduler tick; module imports still execute on each firing, so every commit must leave the tree import-clean (normal discipline).
- **R4 — PAUSE does NOT stop the runner** (spec drift: "stops all three workers" is wrong for the current roster). `factory_runner_pool.py` / `runner_pool.py` contain no PAUSE check; with the scheduler paused the runner drains the pending queue (~1,247 games ≈ 20–30 min) and then idles. The migration therefore has an in-transaction drain check (Task 9) and the go-live rungs require a drained-queue receipt before the real run.
- **R5 — "pipeline re-founds" cannot be literal.** `loop_state.set_founding_baseline` hard-codes `INSERT INTO baselines('v0.1', ...)` — a PK collision against the existing v0.1–v0.24 rows. Concrete semantics adopted: baseline stays **v0.24** (agent-config lineage continues), all 26 non-trashed offspring are trashed by the migration, offspring numbering continues (`v0.24.N`), and the next champion crowns as **v0.25** gated against the NEW anchor + pair-gate. Accepted residual: `loop.train_offspring` materializes the (now-culled) v0.24 baseline deck CSV for training until v0.25 crowns — its lineage is doomed anyway (spec's accepted cost).
- **R6 — pair-gate reconstruction is status-blind (invariant verified SAFE).** `pairgate.resolve_evictee` checks the `baselines` row, the `_version_resolvable` offspring/meta chain, and bare `SELECT 1 FROM decks WHERE id=?` — no concept-status filter; `runner_pool._load_deck_cards` (line 143) is likewise status-blind. Cull-as-status-change preserves reconstruction; Task 10 pins it with a regression test and the go-live rung re-verifies on the live DB.
- **R7 — the anchor deck is currently the LADDER identity CSV.** `anchor.ensure_anchor_deck` reads `CURRENT_DECK_PATH` (`ptcg.agents.current`). Installing a different anchor requires decoupling (new committed CSV + new `ANCHOR_CONCEPT_ID`/`ANCHOR_DECK_ID`); ladder identity files stay untouched (Task 8). `rating.py`, `census.py`, `floor.py`, `subscheduler.py` all consume the constants symbolically — they follow automatically, and old screening games (keyed on the OLD `deck_b_id`) silently drop out of `rating.refresh_field_ratings`' aggregate.
- **R8 — `validate_deck` consumer blast radius.** `tests/test_current.py:16` asserts the frozen 4-basic ladder deck validates clean — under the new rule it must instead assert the min-basics problem is the ONLY problem (documented frozen-identity exception). `tests/test_validate.py`'s `_legal_deck()` (4 basics), `tests/test_reseed_tournament_pool.py:129,151` (3–4-basic historical decks), `tests/test_factory_breeding.py:69,142,224` and `tests/test_factory_deck_matrix.py:70,105` (fixture parents) all break without the Task-2 sweep. All assertion updates land in ONE task so the suite never goes red across tasks.
- **R9 — `uv run python -m ptcg.decks.analysis` (CLAUDE.md command) currently fails** (`No module named 'ptcg'` — src layout not on path). Scripts must `sys.path.insert(0, str(ROOT / "src"))` like `scripts/measure_floor_distribution.py` does. Candidate tooling in Task 5 follows that precedent.

## Parallelization map

- **T1 (PAUSE)** blocks everything (hold-set receipt precedes the first watch-loop-reachable write; `validate.py` is watch-loop-reachable per R3).
- **T2 (validate.py + 6 test files)** blocks T3, T4, T5 (all import `MIN_BASIC_CARDS`).
- **T3 (builder.py + tests/test_factory_builder.py)**, **T4 (deck_quality.py + 2 test files)**, **T10 (tests/test_factory_pairgate.py only)** are mutually file-disjoint — safe to dispatch in parallel after T2 (T10 even before T2).
- **T5 (candidate script + CSVs) → T6 (tournament script) → T7 (orchestrator run) → T8 (anchor swap)** serialize on the winner artifact.
- **T9 (migration script)** needs T2 (constant) + T8 (`ensure_anchor_deck` new source) — serialize after T8. **T11 (rehearsal)** after T9.
- No two parallel tasks touch the same file. Single branch, no worktree: include the `index.lock` wait-2s-retry-3x directive in parallel dispatches.

---

### Task 1: PAUSE hold with timestamped receipt (no code)

**Files:** Create: `experiments/factory/PAUSE` (untracked by convention — do NOT commit it). No source changes.

**Interfaces:** Consumes: `ptcg.factory.cycle.FactoryPaths.pause_file` = `<root>/experiments/factory/PAUSE` (cycle.py:47-48); watch-loop check at `scripts/factory_watch_once.py:117` (`paused` terminal marker); scheduler check at `scripts/factory_tournament_scheduler.py:146-148` (`scheduler: paused (PAUSE present)`).

- [ ] Record the current time, then create the hold: `touch "experiments/factory/PAUSE"` (bash) from the repo root. Print `ls -la experiments/factory/PAUSE` with its timestamp.
- [ ] Wait for the next watch-loop firing (<=15 min) and capture the receipt: `Grep -n "paused" experiments/factory/logs/watch.log | tail -3` — the newest `paused` marker's timestamp must be AFTER the PAUSE-file mtime. Paste both timestamps into the task report (hold-set-precedes-first-write receipt, settled template per `.claude/rules/factory-resume-probe.md`).
- [ ] Confirm the scheduler is also paused: tail its log under `experiments/factory/logs/` for the `scheduler: paused (PAUSE present)` line stamped after the hold.
- [ ] Record the runner-drain baseline (read-only; the runner does NOT honor PAUSE — R4): `uv run python -c "import sqlite3; c=sqlite3.connect('experiments/factory/tournament.db', timeout=60); print(c.execute(\"SELECT status, COUNT(*) FROM games WHERE status IN ('pending','claimed') GROUP BY status\").fetchall())"`. Note the count; the queue only shrinks from here (scheduler paused). Full drain (0/0) is required before the POST-MERGE migration run, not before code tasks.
- [ ] Report: PAUSE mtime, paused-marker timestamp, scheduler-paused timestamp, drain baseline. No commit (nothing tracked changed).

### Task 2: `MIN_BASIC_CARDS` + `validate_deck` hard check + FULL consumer-test sweep

**Files:** Modify: `src/ptcg/decks/validate.py` (42 lines today). Test: `tests/test_validate.py`, `tests/test_current.py`, `tests/test_reseed_tournament_pool.py` (lines 129, 151), `tests/test_factory_breeding.py` (lines 69, 142, 224 + `_decks()` fixture), `tests/test_factory_deck_matrix.py` (lines 70, 105 + fixtures).

**Interfaces:** Produces: `MIN_BASIC_CARDS: int = 8` (module constant, THE single source of truth) and an extended `validate_deck(deck: list[int]) -> list[str]` that appends `f"fewer than {MIN_BASIC_CARDS} Basic Pokémon cards ({basics})"` when the deck has <8 basic-Pokémon copies. Consumes: existing `is_basic_pokemon(card_id: int) -> bool` (validate.py:15).

- [ ] Write the failing tests first in `tests/test_validate.py`. Replace the `_db()`/`_legal_deck()` fixtures (a 4-basic deck is no longer legal) and add the new rejection tests:

```python
def _db():
    cards = all_card_data()
    basics = [c for c in cards if c.cardType == CardType.POKEMON and c.basic]
    # two DISTINCT basic species (4-copy name cap forces >=2 lines for 8 basics)
    basic_a, basic_b = basics[0], next(c for c in basics if c.name != basics[0].name)
    basic_energy = next(c for c in cards if c.cardType == CardType.BASIC_ENERGY)
    return basic_a, basic_b, basic_energy


def _legal_deck():
    a, b, energy = _db()
    # 4 + 4 + 52 = 60 cards, 8 basic-Pokémon copies (hand-verified arithmetic)
    return [a.cardId] * 4 + [b.cardId] * 4 + [energy.cardId] * 52


def test_min_basics_seven_fails():
    a, b, energy = _db()
    deck = [a.cardId] * 4 + [b.cardId] * 3 + [energy.cardId] * 53  # 7 basics
    assert any("fewer than 8 Basic" in p for p in validate_deck(deck))


def test_min_basics_eight_passes():
    assert not any("fewer than 8" in p for p in validate_deck(_legal_deck()))
```

  Update the existing `test_five_copies_fails`/`test_no_basic_pokemon_fails` call sites for the 3-tuple `_db()` return; their `any(...)` assertions are unaffected by the extra min-basics problem line.
- [ ] Run `uv run pytest tests/test_validate.py` — see the new tests FAIL (rule absent).
- [ ] Implement in `src/ptcg/decks/validate.py` (append after the existing no-basic check at line 37-38; keep that check — its message is load-bearing for existing assertions):

```python
#: Pool rule (spec 2026-08-11): every deck must run at least this many basic
#: Pokémon CARD COPIES (basic energy does not count). Single source of truth —
#: builder.MIN_BASIC_POKEMON and deck_quality's mulligan-risk flag import this.
MIN_BASIC_CARDS = 8
```

```python
    basics = sum(1 for cid in deck if is_basic_pokemon(cid))
    if basics < MIN_BASIC_CARDS:
        problems.append(f"fewer than {MIN_BASIC_CARDS} Basic Pokémon cards ({basics})")
```

- [ ] Run `uv run pytest tests/test_validate.py` — PASS. Commit: `feat: MIN_BASIC_CARDS=8 hard check in validate_deck` (stage `src/ptcg/decks/validate.py tests/test_validate.py` by path).
- [ ] Sweep `tests/test_current.py`: the ladder deck (4x Riolu = 4 basics) is a FROZEN identity, deliberately out of scope. Change line 16 to assert the min-basics line is the ONLY problem:

```python
def test_current_deck_is_engine_legal():
    deck = load_current_deck()
    problems = validate_deck(deck)
    # The ladder identity (frozen, out of scope for the 2026-08-11 min-basics
    # pool rule) predates MIN_BASIC_CARDS: it runs 4 basics. It must remain
    # engine-legal in every OTHER respect. Do not "fix" the deck — ladder
    # identity files are unchanged by design (spec Section 4).
    assert problems == ["fewer than 8 Basic Pokémon cards (4)"]
```

  (Adapt the function/loader names to the file's actual contents; the assertion shape is the requirement.)
- [ ] Sweep `tests/test_reseed_tournament_pool.py` lines 129/151: the reconstructed 2026-08-04 reseed decks (3–4 basics) are historical. Replace `assert validate_deck(cards) == []` with:

```python
        problems = [p for p in validate_deck(cards) if "fewer than 8 Basic" not in p]
        assert problems == []  # historical pre-rule decks: engine-legal, min-basics exempt
```

- [ ] Sweep `tests/test_factory_breeding.py` and `tests/test_factory_deck_matrix.py`: their fixtures feed `validate_deck` as the breeding oracle. Rebuild fixture parent decks to >=8 basics (two 4-copy basic lines, per the `_legal_deck()` shape above) so mutate/crossover children can still validate; where a fixture deliberately exercises the legacy (frozen) deck-matrix path with <8-basic seeds and the oracle now returns None for every child, convert the assertion to the exempt-filter form above and say so in a comment. Verify each file's intent before editing — these are verify-then-diverge edits, not mechanical replacements.
- [ ] Run each swept file: `uv run pytest tests/test_current.py tests/test_reseed_tournament_pool.py tests/test_factory_breeding.py tests/test_factory_deck_matrix.py tests/test_validate.py` — ALL PASS.
- [ ] Commit: `test: sweep validate_deck consumers for min-basics rule` (stage the four test files by path).

### Task 3: Builder single-source-of-truth + guarantee tests (no fill-algorithm change — R1)

**Files:** Modify: `src/ptcg/factory/builder.py` (line 66 only). Test: `tests/test_factory_builder.py`.

**Interfaces:** Consumes: `MIN_BASIC_CARDS` from `ptcg.decks.validate` (Task 2); `build_deck(concept: Concept) -> BuildResult` (builder.py:254); `_pad_with_basics(cards, used_names, target) -> bool` (builder.py:235); `brad_bounds_problems(deck: list[int]) -> list[str]` (builder.py:331). Produces: `MIN_BASIC_POKEMON` aliased to the shared constant; no behavior change (the >=8 guarantee shipped in commit `9908306`).

- [ ] Write the failing test first (guarantee + single-source pin) in `tests/test_factory_builder.py`:

```python
from ptcg.decks.validate import MIN_BASIC_CARDS, is_basic_pokemon
from ptcg.factory.builder import MIN_BASIC_POKEMON


def test_min_basic_constant_is_single_sourced():
    assert MIN_BASIC_POKEMON is MIN_BASIC_CARDS or MIN_BASIC_POKEMON == MIN_BASIC_CARDS == 8


def _basics(cards: list[int]) -> int:
    return sum(1 for cid in cards if is_basic_pokemon(cid))


def test_every_buildable_concept_yields_at_least_8_basics():
    # spec Section 6: unit sweep over real concepts, not fixtures only.
    # enumerate_concepts() is the real pool (~815 single cores); build a
    # deterministic sample of 50 (every 16th) to keep the test fast.
    concepts = enumerate_concepts()[::16]
    built = 0
    for c in concepts:
        r = build_deck(c)
        if r.cards is None:
            continue
        built += 1
        assert _basics(r.cards) >= MIN_BASIC_CARDS, c.cores
    assert built >= 30  # the sample must exercise real successes


def test_pad_with_basics_reports_exhaustion():
    # unbuildable branch: demand more filler copies than distinct names allow
    cards: list[int] = []
    used = {c.name for c in _filler_basic_order()}  # every filler name burned
    assert _pad_with_basics(cards, used, 4) is False
```

  (Import `enumerate_concepts`, `_filler_basic_order`, `_pad_with_basics` from `ptcg.factory.builder`; keep existing tests untouched — they already assert `validate_deck(r.cards) == []` at lines 34/52, which now ALSO exercises the min-basics rule for free.)
- [ ] Run `uv run pytest tests/test_factory_builder.py` — the single-source pin FAILS (builder still defines its own literal).
- [ ] Implement — replace builder.py line 66 (`MIN_BASIC_POKEMON = 8`) with:

```python
from ptcg.decks.validate import MIN_BASIC_CARDS

#: Single source of truth is ptcg.decks.validate.MIN_BASIC_CARDS (pool rule,
#: spec 2026-08-11). The local name is kept — every internal use and the
#: unbuildable-reason strings reference MIN_BASIC_POKEMON.
MIN_BASIC_POKEMON = MIN_BASIC_CARDS
```

  (Import goes at the top of the file with the existing imports; no cycle — `validate` imports only `cg.api`.)
- [ ] Run `uv run pytest tests/test_factory_builder.py` — PASS.
- [ ] Commit: `refactor: builder MIN_BASIC_POKEMON sourced from validate.MIN_BASIC_CARDS + guarantee tests` (stage `src/ptcg/factory/builder.py tests/test_factory_builder.py`).

### Task 4: `deck_quality` mulligan-risk recalibration

**Files:** Modify: `src/ptcg/factory/deck_quality.py` (lines 44-49 constants block, lines 150-153 flag). Test: `tests/test_deck_quality.py`, `tests/test_factory_ui_pages.py`.

**Interfaces:** Consumes: `MIN_BASIC_CARDS` (Task 2). Produces: mulligan-risk flag fires for `basics_count < MIN_BASIC_CARDS` (was `<= BASICS_RED_MAX == 2`); `BASICS_RED_MAX` deleted. `DeckQualityReport` shape unchanged.

- [ ] Write failing tests in `tests/test_deck_quality.py`: a 7-basic deck flags `mulligan-risk` red; an 8-basic deck does not. Executably verified arithmetic for the message (`for i in range(7): p *= (60-b-i)/(60-i)`): `mulligan_probability(7) = 0.3991 -> "mulligan 40%"`, `mulligan_probability(4) = 0.6005 -> 60%`, `mulligan_probability(8) = 0.3464` (no flag). Assert on flag presence/absence and `< 8` in the detail string, NOT on exact rounded percentages beyond one pinned case:

```python
def test_mulligan_risk_fires_below_min_basics():
    report = analyze_deck(_deck_with_basics(7))       # helper: 7 basic copies + filler
    assert any(f.name == "mulligan-risk" and f.severity == "red" for f in report.flags)


def test_mulligan_risk_absent_at_min_basics():
    report = analyze_deck(_deck_with_basics(8))
    assert not any(f.name == "mulligan-risk" for f in report.flags)
```

  Build `_deck_with_basics(n)` from two distinct basic species (Task-2 fixture shape) + basic energy filler; sizes need not be 60 (`_analyze` accepts any length; mulligan math is pinned to deck_size=60 — do NOT "fix" that, see the inline comment at deck_quality.py:131-135).
- [ ] Run `uv run pytest tests/test_deck_quality.py` — new tests FAIL (7 basics is not `<= 2`).
- [ ] Implement: delete `BASICS_RED_MAX = 2` (line 49) and its comment line 48; add `from ptcg.decks.validate import MIN_BASIC_CARDS` to the imports; replace the flag condition (line 150) with:

```python
    if basics_count < MIN_BASIC_CARDS:
        flags.append(Flag("mulligan-risk", "red",
                          f"only {basics_count} Basic Pokemon (< {MIN_BASIC_CARDS}) -- "
                          f"mulligan {round(mull * 100)}%"))
```

  Update the calibration header comment: the 2026-08-10 "Pool range: 3-4" note is superseded — the pool rule now enforces >=8 by construction (validate_deck + builder), so the badge marks rule violations, not observed-range outliers.
- [ ] Sweep `tests/test_factory_ui_pages.py` for fixtures/assertions that reference `BASICS_RED_MAX` or expect a 3-4-basic deck to be unflagged; update to the new threshold. Run `uv run pytest tests/test_deck_quality.py tests/test_factory_ui_pages.py` — PASS.
- [ ] Commit: `feat: deck_quality mulligan-risk recalibrated to MIN_BASIC_CARDS` (stage `src/ptcg/factory/deck_quality.py tests/test_deck_quality.py tests/test_factory_ui_pages.py`).

### Task 5: Author the 4 anchor candidates (deterministic construction + committed CSVs)

**Files:** Create: `scripts/make_anchor_candidates.py`, `src/ptcg/decks/candidates/anchor-cand-a-lucario-min8.csv`, `...-b-mega-starmie.csv`, `...-c-palafin.csv`, `...-d-tinkaton.csv`. Test: `tests/test_anchor_candidates.py`.

**Interfaces:** Consumes: the ladder CSV `src/ptcg/decks/candidates/mega-lucario-fighting.csv` (composition verified: 26x id-6 Basic {F} Energy, 4x 677 Riolu, 4x 678 Mega Lucario ex, 4x each 1224/1213/1121/1102/1182/1097, 1x 1082, 1x 1122; basics_count = 4); `build_deck`/`Concept` from `ptcg.factory.builder`; `validate_deck`/`is_basic_pokemon`/`MIN_BASIC_CARDS` from `ptcg.decks.validate`; `cg.api.all_card_data`. Produces: four 60-card CSVs (one card id per line), each passing the NEW `validate_deck` with >=8 basics. Scripts must `sys.path.insert(0, str(ROOT / "src"))` (R9) and write files with `encoding="utf-8"`.

- [ ] Write `scripts/make_anchor_candidates.py` implementing exactly this deterministic procedure (print each candidate's composition + basics count + `validate_deck` output):
  - **Candidate A (continuity, `anchor-cand-a-lucario-min8`):** ladder CSV minus 4 copies of card id 6 (Basic {F} Energy: 26 -> 22) plus 4 copies of the SECOND basic line, chosen as: among cards with `cardType == POKEMON and basic == True` whose best damaging attack (builder `_best_attack_for`) has non-Colorless energy costs ⊆ {FIGHTING}, pick max HP, tie-break lowest `cardId`. Resulting deck: 60 cards, 8 basics (4 Riolu + 4 new), 12 Pokémon, 22 trainers, 22 energy.
  - **Candidate B (`anchor-cand-b-mega-starmie`):** `build_deck(Concept(cores=("Mega Starmie ex",)))` — ladder evidence: legacy starmie identities held the best sustained Kaggle scores (~597/~646 transient). Concept name verified present in the pool (`concepts` row `c-7e050e10b21c`, cores `["Mega Starmie ex"]`).
  - **Candidate C (`anchor-cand-c-palafin`):** `build_deck(Concept(cores=("Palafin ex",)))` — #1 damage-per-energy attacker in the card-pool report (Giga Impact 250/1, 340 HP).
  - **Candidate D (`anchor-cand-d-tinkaton`):** `build_deck(Concept(cores=("Tinkaton",)))` — top non-ex attacker (Windup Swing 240/1), different type from B/C.
  - Fallback rule (deterministic): if any of B/C/D is unbuildable (`BuildResult.cards is None`), take the next row down the card-pool report's top-attackers table whose core name resolves and whose type differs from the already-chosen candidates; print the substitution loudly.
- [ ] Run it: `uv run python scripts/make_anchor_candidates.py` — writes the four CSVs. Eyeball the printed compositions.
- [ ] Write `tests/test_anchor_candidates.py`:

```python
import pytest
from pathlib import Path
from ptcg.decks.validate import MIN_BASIC_CARDS, is_basic_pokemon, validate_deck

CANDIDATES = sorted(Path("src/ptcg/decks/candidates").glob("anchor-cand-*.csv"))


def test_exactly_four_candidates():
    assert len(CANDIDATES) == 4


@pytest.mark.parametrize("csv_path", CANDIDATES, ids=lambda p: p.stem)
def test_candidate_is_legal_and_min_basics(csv_path):
    cards = [int(x) for x in csv_path.read_text(encoding="utf-8").split()]
    assert len(cards) == 60
    assert validate_deck(cards) == []
    assert sum(1 for c in cards if is_basic_pokemon(c)) >= MIN_BASIC_CARDS
```

- [ ] Run `uv run pytest tests/test_anchor_candidates.py` — PASS.
- [ ] Bundle smoke each candidate (spec Section 2): `uv run python scripts/package_submission.py "src/ptcg/decks/candidates/anchor-cand-a-lucario-min8.csv"` (and b/c/d) — exit 0 each; report the four results explicitly (watched-exit-0 receipts, not "should pass").
- [ ] Commit: `feat: 4 anchor candidate decks + deterministic construction script` (stage the script, the four CSVs, and the test file by path).

### Task 6: Mini-tournament script (code only — the RUN is Task 7, orchestrator-owned)

**Files:** Create: `scripts/run_anchor_minitournament.py`. Test: `tests/test_anchor_minitournament.py`.

**Interfaces:** Consumes: `ptcg.arena.runner.play_match(agent0, agent1, deck0, deck1, max_moves=3000) -> MatchResult` (winner: 0 = agent0 won, 1 = agent1, 2 = draw — mirrors `games.winner` semantics); `ptcg.agents.heuristic.HeuristicAgent`; the four candidate CSVs (Task 5); the OLD anchor deck = ladder CSV (strength reference series). Produces: `run_tournament(decks: dict[str, list[int]], games_per_pair: int) -> TournamentResult` (pure-testable tally) and a CLI that prints per-pair W/L/D, pooled scores, the winner, and ready-to-paste `EXPERIMENTS.md` rows. Follows `scripts/measure_floor_distribution.py`'s structure (sys.path insert, live progress prints).

- [ ] Write the pure tally/selection logic first with tests (no games — feed synthetic results):

```python
def pooled_score(wins: int, draws: int, games: int) -> float:
    """Pooled win rate with draws counted 0.5 (symmetric argmax metric)."""
    return (wins + 0.5 * draws) / games


def pick_winner(scores: dict[str, float],
                head_to_head: dict[tuple[str, str], float]) -> str:
    """Highest pooled score; ties broken by direct head-to-head score
    (spec Section 2), then lexicographic candidate name (determinism)."""
    best = max(scores.values())
    tied = sorted(k for k, v in scores.items() if v == best)
    if len(tied) == 1:
        return tied[0]
    a, b = tied[0], tied[1]  # >2-way tie: compare the first two, winner stands
    return a if head_to_head.get((a, b), 0.5) >= 0.5 else b
```

  Tests (hand-verified arithmetic per `.claude/rules/plan-test-arithmetic-sanity.md`): `pooled_score(120, 10, 200) == 0.625` (120 + 5 = 125; 125/200 = 0.625 ✓); a two-way tie at 0.5 resolved by `head_to_head[("a","b")] = 0.55 -> "a"`; degenerate input: single-candidate dict returns that candidate (loader/aggregator degenerate-probe rule).
- [ ] Run `uv run pytest tests/test_anchor_minitournament.py` — FAIL then implement then PASS.
- [ ] Implement the CLI: `--games-per-pair` (default 200), `--reference-games` (default 200), `--candidates-glob` (default `src/ptcg/decks/candidates/anchor-cand-*.csv`). Round-robin C(4,2)=6 pairs x 200 games, HeuristicAgent both sides, alternating deck order every game (`if g % 2: swap decks and invert the winner mapping`); then each candidate x 200 games vs the OLD anchor deck (`src/ptcg/decks/candidates/mega-lucario-fighting.csv`) as a strength REFERENCE (recorded, not a gate). Expected wall time: (1,200 + 800) games x ~0.022 s ≈ 45 s. Print an `EXPERIMENTS.md`-format row per series (match the existing pipe-table columns) and a final `WINNER: <name>` line.
- [ ] Commit: `feat: anchor mini-tournament script (round-robin + old-anchor reference)` (stage the script + test file).

### Task 7: ORCHESTRATOR SYNC POINT — run the mini-tournament, record, pick the winner

**Files:** Modify: `experiments/EXPERIMENTS.md` (append section + series rows).

**Interfaces:** Consumes: Task 6 CLI. Produces: the winner's candidate CSV path — the input artifact for Task 8.

- [ ] **Orchestrator-owned foreground run** (never dispatched to a waiting subagent — `.claude/rules/dispatch-test-run-directive.md`): `uv run python scripts/run_anchor_minitournament.py` from the repo root. ~1–2 min.
- [ ] Append the printed rows + a short decision paragraph (winner, pooled scores, reference WRs vs old anchor) to `experiments/EXPERIMENTS.md` under a `## Anchor mini-tournament (min-basics rule, 2026-08-11)` heading.
- [ ] Single run is acceptable — this is an argmax, not a near-bar pass/fail gate (spec Section 6). EXCEPTION: if the top two pooled scores are within 0.04 (~1 CI width at n=600 pooled games/candidate), re-run once and pool before deciding; record both runs.
- [ ] Commit: `docs: anchor mini-tournament results for min-basics rule` (stage `experiments/EXPERIMENTS.md` by path — the tree has unrelated live drift; stage NOTHING else).

### Task 8: Anchor swap — decouple from ladder identity, install winner as the anchor source

**Files:** Create: `src/ptcg/decks/candidates/anchor-min8.csv` (byte-copy of the Task-7 winner CSV). Modify: `src/ptcg/factory/anchor.py` (constants block lines 28-32 + `ensure_anchor_deck` lines 58-105). Test: `tests/test_factory_anchor.py`.

**Interfaces:** Produces: `ANCHOR_DECK_PATH: Path` (repo-root-relative via `Path(__file__).resolve().parents[3]`), `ANCHOR_CONCEPT_ID = "anchor-min8-<winner-slug>"`, `ANCHOR_DECK_ID = ANCHOR_CONCEPT_ID + "-d0"` (spec's `anchor-<name>-d0` pattern), and `ensure_anchor_deck` reading `ANCHOR_DECK_PATH` and REFUSING to register a deck failing the new `validate_deck`. `ANCHOR_VERSION = "anchor-heuristic-v0"` UNCHANGED (agent identity is still heuristic-v0). Consumers (`rating.py:51`, `census.py:29`, `floor.py:241-242`, `subscheduler.py:246,529`, `runner_pool.py:225`) all reference the constants symbolically — verified, no edits needed there. `<winner-slug>` = the winner's candidate stem minus the `anchor-cand-X-` prefix (e.g. `lucario-min8`), supplied by the orchestrator in the dispatch from Task 7's result.

- [ ] Copy the winner CSV to `src/ptcg/decks/candidates/anchor-min8.csv` (this stable path is the anchor source from now on; future swaps replace the file + bump the ids).
- [ ] Write failing tests in `tests/test_factory_anchor.py` (adapt the existing fixture helpers): `ensure_anchor_deck` registers a `decks` row whose cards equal `anchor-min8.csv`'s contents and whose id is the NEW `ANCHOR_DECK_ID`; the registered deck passes `validate_deck` (== []); a monkeypatched `ANCHOR_DECK_PATH` pointing at a 4-basic CSV makes `ensure_anchor_deck` raise `RuntimeError` (refusal receipt). The existing tests at lines 73-262 reference `anchor.ANCHOR_CONCEPT_ID`/`ANCHOR_DECK_ID` symbolically and should pass unmodified — verify, and update only assertions pinned to the OLD card list.
- [ ] Run `uv run pytest tests/test_factory_anchor.py` — new tests FAIL.
- [ ] Implement in `anchor.py`:

```python
_REPO_ROOT = Path(__file__).resolve().parents[3]
#: The anchor deck is DECOUPLED from the ladder identity as of the min-basics
#: pool rule (spec 2026-08-11): the ladder still plays mega-lucario-fighting
#: (4 basics, frozen), while the census/floor/anchor-gate opponent is this
#: committed >=8-basic mini-tournament winner.
ANCHOR_DECK_PATH = _REPO_ROOT / "src" / "ptcg" / "decks" / "candidates" / "anchor-min8.csv"

ANCHOR_VERSION = "anchor-heuristic-v0"          # unchanged: same heuristic agent
ANCHOR_GAMES = 200
ANCHOR_BAR = 0.55
ANCHOR_CONCEPT_ID = "anchor-min8-<winner-slug>"
ANCHOR_DECK_ID = ANCHOR_CONCEPT_ID + "-d0"
```

  In `ensure_anchor_deck`: read `ANCHOR_DECK_PATH` instead of `CURRENT_DECK_PATH` (drop that import); after the 60-card length check add:

```python
    problems = validate_deck(cards)
    if problems:
        raise RuntimeError(
            f"ensure_anchor_deck: {ANCHOR_DECK_PATH} fails validate_deck: {problems} "
            "-- refusing to register a non-compliant anchor deck"
        )
```

  (`from ptcg.decks.validate import validate_deck` at top; keep the docstring's coverage-row rationale intact — it still applies verbatim.)
- [ ] Run `uv run pytest tests/test_factory_anchor.py` — PASS. Also run the direct symbolic consumers' test files: `uv run pytest tests/test_factory_rating.py tests/test_factory_census_schedule.py` — PASS (they build the anchor via `ensure_anchor_deck` and reference constants symbolically).
- [ ] Commit: `feat: anchor decoupled from ladder identity; min8 mini-tournament winner installed as anchor source` (stage `src/ptcg/factory/anchor.py src/ptcg/decks/candidates/anchor-min8.csv tests/test_factory_anchor.py`).

### Task 9: One-shot migration script `scripts/migrate_min_basics_pool.py`

**Files:** Create: `scripts/migrate_min_basics_pool.py`. Test: `tests/test_migrate_min_basics_pool.py`.

**Interfaces:** Consumes: `deckdb.connect/init_db/_write`; `anchor.ensure_anchor_deck` (Task 8); `is_basic_pokemon`/`MIN_BASIC_CARDS` (Task 2). CLI (reseed-script precedent, `scripts/reseed_tournament_pool.py:263-270`): `--db` REQUIRED with help text `"Path to the tournament DB. Never the production tournament.db in tests."`; `--dry-run` flag (exact receipts via execute-then-ROLLBACK). Produces: `run_migration(conn, dry_run: bool) -> dict` returning printed receipt counts.

**Expected live receipts (pre-computed on the 2026-08-11 DB copy — the rehearsal in Task 11 must reproduce these):** cull 2 (the `active` concept of `reseed-mut-51430502f003-sv0` + the `finalist` old anchor `anchor-mega-lucario-fighting`; the 10 other <8 reseed concepts are ALREADY culled and are not re-written), restore 815, coverage reset 95,906, offspring trashed 26, pending anchor/floor checks failed 0, games swept 0 (drain precondition).

- [ ] Write fixture-DB tests first (`deckdb.init_db` on a tmp_path DB — the connect helper already `mkdir(parents=True)`s, virgin-directory safe; ALSO point one test at a nested never-created path one level below `tmp_path` per the virgin-directory rule). Cover, one test each:
  - a <8-basic `active` concept is culled with reason `min-basics-rule: 4 basics < 8` and its deck row RETAINED;
  - a `finalist` <8 concept (old anchor shape) is culled;
  - a ge8 `culled` SINGLE concept is restored to `untested` — and a ge8 culled single whose LATEST `decisions` row is `remove`/`bulk-remove` is NOT restored (human culls respected; lookup uses `ix_decisions_concept`);
  - a ge8 `culled` PAIR concept stays culled (R2 scope decision);
  - coverage rows are zeroed (`games_played=0, distinct_opponents=0, rating IS NULL`);
  - all non-trashed offspring become `trashed`;
  - pending `anchor_checks`/`floor_checks` rows get `verdict='fail'` + `resolved_at`;
  - the drain guard: a fixture with one `pending` game makes the migration raise/exit nonzero WITHOUT writing (assert statuses unchanged after the failure);
  - `--dry-run` prints the same counts and writes NOTHING (re-query after);
  - idempotence: running twice yields zero additional changes on the second run.
- [ ] Run `uv run pytest tests/test_migrate_min_basics_pool.py` — FAIL (script absent).
- [ ] Implement. Core shape (single-actor-worker checklist applied — the read-decide-act including the drain check sits INSIDE one `BEGIN IMMEDIATE`; `ensure_anchor_deck` runs AFTER commit because `deckdb._write` cannot nest):

```python
def _basics(cards_json: str) -> int:
    return sum(1 for cid in json.loads(cards_json) if is_basic_pokemon(cid))


def run_migration(conn: sqlite3.Connection, dry_run: bool) -> dict:
    # Pre-pass (read-only, NO write lock held): classify every deck. ~95,907
    # rows x 60 DLL-backed dict lookups, a few seconds, deliberately outside
    # the transaction so the write lock is held only for indexed UPDATEs.
    to_cull: list[tuple[str, int]] = []      # (concept_id, basics)
    to_restore: list[str] = []
    for r in conn.execute(
        "SELECT d.cards, c.id AS cid, c.status, json_array_length(c.cores) AS arity "
        "FROM decks d JOIN concepts c ON c.id = d.concept_id "
        "WHERE c.status != 'unbuildable'"
    ):
        b = _basics(r["cards"])
        if b < MIN_BASIC_CARDS and r["status"] in ("untested", "active", "finalist"):
            to_cull.append((r["cid"], b))
        elif b >= MIN_BASIC_CARDS and r["status"] == "culled" and r["arity"] == 1:
            to_restore.append(r["cid"])

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn.execute("BEGIN IMMEDIATE")
    try:
        undrained = conn.execute(
            "SELECT COUNT(*) FROM games WHERE status IN ('pending','claimed')"
        ).fetchone()[0]
        if undrained:
            raise RuntimeError(
                f"{undrained} pending/claimed games -- the runner has not drained. "
                "Hold PAUSE, wait for the queue to reach 0, re-run."
            )
        receipts = {"culled": 0, "restored": 0, "restore_skipped_human_cull": 0}
        for cid, b in to_cull:
            receipts["culled"] += conn.execute(
                "UPDATE concepts SET status='culled', reason=? "
                "WHERE id=? AND status != 'culled'",
                (f"min-basics-rule: {b} basics < {MIN_BASIC_CARDS} (2026-08-11)", cid),
            ).rowcount
        for cid in to_restore:
            last = conn.execute(
                "SELECT action FROM decisions WHERE concept_id=? "
                "ORDER BY id DESC LIMIT 1", (cid,)          # uses ix_decisions_concept
            ).fetchone()
            if last is not None and last["action"] in ("remove", "bulk-remove"):
                receipts["restore_skipped_human_cull"] += 1
                continue
            receipts["restored"] += conn.execute(
                "UPDATE concepts SET status='untested', reason='' "
                "WHERE id=? AND status='culled'", (cid,),
            ).rowcount
        receipts["coverage_reset"] = conn.execute(
            "UPDATE coverage SET games_played=0, distinct_opponents=0, rating=NULL"
        ).rowcount
        receipts["offspring_trashed"] = conn.execute(
            "UPDATE offspring SET status='trashed' WHERE status != 'trashed'"
        ).rowcount
        for table in ("anchor_checks", "floor_checks"):
            receipts[f"{table}_failed"] = conn.execute(
                f"UPDATE {table} SET verdict='fail', resolved_at=? "
                "WHERE verdict='pending'", (now,),
            ).rowcount
        conn.execute("ROLLBACK" if dry_run else "COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    if not dry_run:
        # separate transactions by design: deckdb._write cannot nest.
        anchor.ensure_anchor_deck(conn)
        receipts["anchor_installed"] = anchor.ANCHOR_DECK_ID
    for k, v in receipts.items():
        print(f"migrate_min_basics_pool: {k} = {v}")
    return receipts
```

  Notes the implementer must keep: `pair_gate_checks` is deliberately untouched (v0.24's settled `pass` row is history; `enqueue_pair_gate`'s supersede-DELETE handles version turnover); the games queue is NOT deleted — the drain guard makes an empty queue a precondition instead (R4); champion/old-anchor DECK rows are never written (pair-gate reconstruction invariant, R6).
- [ ] Run `uv run pytest tests/test_migrate_min_basics_pool.py` — PASS.
- [ ] Access-path receipts (single-actor-worker-tests access-path clause) — run against the Task-11 production copy and paste outputs into the task report: `EXPLAIN QUERY PLAN` for (i) the decisions lookup (must show `SEARCH ... USING INDEX ix_decisions_concept`), (ii) the concepts UPDATEs (PK search), (iii) the drain COUNT (must use `ix_games_claim`); plus a timed full run (pre-pass + txn) — expected well under 60 s total, with the write txn itself sub-second-to-seconds (indexed UPDATEs + one full-table coverage UPDATE). The factory is PAUSED+drained when this runs for real, so there is no lock contention by construction; the receipts pin it anyway.
- [ ] Commit: `feat: one-shot min-basics pool migration script` (stage `scripts/migrate_min_basics_pool.py tests/test_migrate_min_basics_pool.py`).

### Task 10: Pair-gate evictee-reconstruction regression test (invariant pin)

**Files:** Test only: `tests/test_factory_pairgate.py` (additions — do not rewrite existing tests; if any assertion is replaced, diff old-vs-new per `.claude/rules/test-coverage-sweep.md` §2).

**Interfaces:** Consumes: `pairgate.resolve_evictee(conn, rows) -> EvicteeRef | None`, `pairgate.enqueue_pair_gate(conn, version, evictee)`, `pairgate.EvicteeUnreconstructable`; `submit.submission_description` format (`"tournament-champion <version> - deck <deck_id> - ..."` — mirror the file's existing fake-Kaggle-row fixtures). Verified reconstruction chain (pairgate.py:137-172): baselines row -> `_version_resolvable` (offspring row exists OR baselines->offspring/meta chain) -> bare `SELECT 1 FROM decks WHERE id=?` — NO status filter anywhere.

- [ ] Add the regression tests (they should pass against current code — they exist to FAIL if anyone ever adds a status filter to the reconstruction chain):

```python
def test_culled_ex_champion_still_reconstructs_and_gates(tmp_db):
    """min-basics invariant (spec Section 4): cull-is-status-change must keep
    the evictee reconstructable, or every upload silently stops (fail-closed).
    Fixture mirrors the post-migration live shape: champion deck's concept is
    'culled', its offspring row is 'trashed', deck row retained."""
    conn = tmp_db
    conn.execute("INSERT INTO concepts(id, cores, status, reason) "
                 "VALUES('cX', '[\"x\"]', 'culled', 'min-basics-rule: 4 basics < 8')")
    conn.execute("INSERT INTO decks(id, concept_id, cards, shell_variant) "
                 "VALUES('dX', 'cX', '[3]', 0)")
    conn.execute("INSERT INTO offspring(id, parent_baseline_version, search_config_json, "
                 "status, created_at) VALUES('v9.8.1', 'v9.8', '{}', 'trashed', 'now')")
    conn.execute("INSERT INTO baselines(version, offspring_id, deck_id, crowned_at) "
                 "VALUES('v9.9', 'v9.8.1', 'dX', 'now')")
    conn.commit()
    rows = [_row("tournament-champion v9.10 - deck dY - x", "2026-08-11 09:00:00"),
            _row("tournament-champion v9.9 - deck dX - x", "2026-08-10 09:00:00")]
    ref = pairgate.resolve_evictee(conn, rows)
    assert ref is not None and ref.version == "v9.9" and ref.deck_id == "dX"
```

  (`_row` = the file's existing fake-Kaggle-row helper with `.status`/`.date`/`.description`; reuse it. Add the enqueue leg: insert a `baselines` row for the gating version, call `enqueue_pair_gate`, assert 200 `purpose='pair_gate'` games exist with `deck_b_id='dX'` — the culled deck is playable by the runner because `_load_deck_cards` is status-blind.)
- [ ] Add the negative pin: deleting the deck ROW (the thing the migration must never do) raises `EvicteeUnreconstructable` — proving the test discriminates (RED-capable receipt, not a tautology).
- [ ] Run `uv run pytest tests/test_factory_pairgate.py` — PASS.
- [ ] Commit: `test: pin pair-gate evictee reconstruction against culled ex-champions` (stage `tests/test_factory_pairgate.py`).

### Task 11: ORCHESTRATOR SYNC POINT — migration rehearsal against a production copy (review-time executable receipts)

**Files:** None committed (scratchpad artifacts only; receipts pasted into the review dispatches and plan progress log).

- [ ] Copy the live DB via the sqlite backup API to the scratchpad (never file-copy a hot WAL DB): `uv run python -c "import sqlite3; s=sqlite3.connect('experiments/factory/tournament.db', timeout=60); d=sqlite3.connect(r'<scratchpad>\\rehearsal.db'); s.backup(d)"`.
- [ ] The copy may contain pending/claimed games (live queue) — this legitimately exercises the drain guard: first run must REFUSE. Then clear them ON THE COPY ONLY (`DELETE FROM games WHERE status IN ('pending','claimed')` — rehearsal stand-in for the real drain) and run `--dry-run`: `uv run python scripts/migrate_min_basics_pool.py --db "<scratchpad>\rehearsal.db" --dry-run`.
- [ ] Verify the printed receipts against Task 9's pre-computed expectations: culled 2, restored 815, coverage_reset 95,906, offspring_trashed 26, checks-failed 0 (if the live DB has moved — e.g. a new crown since 2026-08-11 morning — explain each delta from `baselines`/`offspring` rows before proceeding; unexplained deltas are a STOP).
- [ ] Run FOR REAL on the copy (no `--dry-run`), then execute the post-state receipts and paste outputs:
  - `census_complete(conn) is False` and `_CANDIDATES_QUERY` returns restored singles (the re-screen will resume);
  - new anchor rows present: `SELECT status FROM concepts WHERE id='<new ANCHOR_CONCEPT_ID>'` -> `finalist`; old anchor concept -> `culled`; both DECK rows still present;
  - pair-gate reconstruction on the REAL migrated state: `resolve_evictee` with a synthetic 2-row Kaggle list naming the actual counted pair (v0.24 newer / v0.22 older, deck ids from their `baselines` rows) returns `EvicteeRef(version='v0.22', deck_id='reseed-mega-starmie-water-sv0')`;
  - EXPLAIN QUERY PLAN + timing receipts from Task 9's checklist.
- [ ] Record all receipts in plan.md progress log (via plan-manager). These are the review-time executable receipts required by `.claude/rules/diagnose-before-dispatch.md` — the whole-branch reviewer and Pass 2 must be handed this rehearsal transcript, not prose claims.

---

## Post-merge go-live rungs (NOT tasks — explicit post-merge actions; per `.claude/rules/golive-command-preflight.md`, re-verify every command line below against each script's `--help` / `add_argument` block at Finish before executing or handing to Brad)

1. **Pre-flight:** `uv run python scripts/migrate_min_basics_pool.py --help` and reconcile flags (`--db` required, `--dry-run`); `Get-ScheduledTask ptcg-factory-runner, ptcg-factory-scheduler, ptcg-factory-ui, ptcg-factory-continuous | Select TaskName, State`.
2. **Drain receipt (R4):** confirm PAUSE still held AND `SELECT COUNT(*) FROM games WHERE status IN ('pending','claimed')` == 0 on the LIVE DB (read-only query). The runner does not honor PAUSE; it drains then idles — do not proceed until 0.
3. **Live dry-run, then real migration:** `uv run python scripts/migrate_min_basics_pool.py --db "experiments/factory/tournament.db" --dry-run` — compare receipts against the Task-11 rehearsal (explain any drift from interim crowns); then the same command WITHOUT `--dry-run`. Paste both receipt blocks into EXPERIMENTS.md's go-live entry.
4. **Worker restart (long-lived workers do not hot-reload):** `Stop-ScheduledTask -TaskName ptcg-factory-runner; Start-ScheduledTask -TaskName ptcg-factory-runner` and the same pair for `ptcg-factory-scheduler` and `ptcg-factory-ui` (non-elevated; stop is immediately followed by start, so the watchdog-respawn hazard does not apply). `ptcg-factory-continuous` needs no restart (fresh process per firing).
5. **Lift PAUSE:** delete `experiments/factory/PAUSE`.
6. **Verify:** `powershell -ExecutionPolicy Bypass -File scripts\verify_factory_tasks.ps1` -> PASS; first unpaused watch-loop firing shows a paired terminal marker in `watch.log`; screening resumes against the NEW anchor (`SELECT COUNT(*) FROM games WHERE purpose='screening' AND deck_b_id='<new ANCHOR_DECK_ID>' AND status='done'` growing over ~30 min — the census-throughput receipt); read-only `resolve_evictee` probe against the live DB reconstructs v0.22 (pair-gate invariant live receipt); ladder identity files empty-diff check (`git diff master~1..master -- src/ptcg/submission_main.py src/ptcg/agents/current.py` after merge — must be empty).
7. **UAC rule:** none of these rungs should require elevation. If any prompt appears, STOP — it is a BLOCKING HUMAN-INPUT REQUEST surfaced loudly to Brad (2026-08-10 addendum, `.claude/rules/factory-resume-probe.md`).

## Pre-lock verification (Verified Landmark Table)

| Landmark (spec cite) | Verified at | Status |
|---|---|---|
| `validate_deck` in `ptcg/decks/validate.py` | validate.py:22, `-> list[str]`, has `is_basic_pokemon` helper :15 | OK |
| `builder.py:254 build_deck`, `:332` extra bounds | build_deck at :254 ✓; `brad_bounds_problems` at **:331** (off by one) | OK (cite :331) |
| Builder fill guarantee "to be built" | **ALREADY SHIPPED**: `MIN_BASIC_POKEMON=8` :66, `_pad_with_basics` :235, need_more_basics check :314-320, since commit `9908306` | plan-drift R1 — task rescoped |
| `census.py:252/:412` build_deck calls, `:415` unbuildable, `:456-490 promote_proven_singles` | :252 ✓ (seed_census), :412 ✓ (activate_pair), :415 ✓, promote_proven_singles def at :456 ✓ | OK |
| `breeding.py:141,182,187,202` validate_deck sites | :141 lazy import, :182 crossover check, :187 mutate_deck def, :202 breed_deck def ✓ | OK |
| `deck_quality.py:129-130 basics_count` | :129-130 ✓ (copies of POKEMON+basic); `BASICS_RED_MAX=2` at :49 | OK |
| `pairgate.py PAIR_GATE_BAR=0.55` | pairgate.py:39 ✓; reconstruction chain :137-172 is status-blind | OK |
| Anchor representation in tournament.db | `concepts.status='finalist'`, id `anchor-mega-lucario-fighting`, deck `...-d0`; cards sourced from LADDER CSV via `CURRENT_DECK_PATH` (anchor.py:24,80-83) | plan-drift R7 — decoupling task added |
| Census rating vs anchor | `rating.py`: wr in [0,1] over `screening` games with `deck_b_id == ANCHOR_DECK_ID` (:51,:82); constant swap auto-invalidates old games | OK |
| `loop_state` founding semantics | `set_founding_baseline` hard-codes `INSERT ... 'v0.1'` — collides with existing v0.1–v0.24 rows; literal re-found impossible | plan-drift R5 — keep v0.24, trash offspring |
| Subscheduler champion refs | reads `loop_state.current_baseline` + `anchor_status`/`pair_gate_status` by version; `_ANCHOR_SCREENING_GAMES_QUERY` keys on `anchor.ANCHOR_DECK_ID` (:246) — symbolic, swap-safe | OK |
| deckdb schema | concepts(status CHECK incl. 'culled','finalist'), decks(NO status col — cull lives on concepts), games(status CHECK pending/claimed/done; ix_games_claim, ix_games_crown_pair), coverage, offspring(status CHECK), baselines, anchor_checks, floor_checks, pair_gate_checks, decisions(+ix_decisions_concept), meta | OK |
| Watch-loop import graph | watch_once -> deckdb, episodes(->breeding->validate), subscheduler(->anchor, pairgate), cycle, gate, kaggle_client, watch. **census NOT imported** | plan-drift R3 |
| PAUSE mechanism | `cycle.FactoryPaths.pause_file` = `experiments/factory/PAUSE`; checked by watch_once:117 + tournament scheduler:146; **runner has NO pause check** | plan-drift R4 — drain protocol added |
| Reseed-script CLI precedent | `--db` `required=True` + help "Never the production tournament.db in tests." (reseed_tournament_pool.py:265-268) | OK |
| In-process game precedent | `ptcg.arena.runner.play_match`, HeuristicAgent both sides (`scripts/measure_floor_distribution.py`); 1,100 games/24 s measured 2026-08-04 | OK |
| Live pool ground truth | 12 decks <8 basics (11 reseed + old anchor); 95,895 builder decks all >=8; 825 culled singles (815 ge8), 1 active (<8), 236,625 untested pairs (no decks/coverage); baseline v0.24, counted pair v0.24/v0.22; 26 non-trashed offspring | measured (probe scripts, DB backup copy 2026-08-11) |
| `uv run python -m ptcg.decks.analysis` (spec cite) | FAILS from repo root (src layout not on sys.path) — scripts must sys.path-insert | plan-drift R9 |

## Self-review

- Spec coverage: Section 1 -> T2/T3/T4; Section 2 -> T5/T6/T7/T8; Section 3 -> T9/T11; Section 4 -> T10 + rung 6 + R5/R6; Section 5 -> T1 + go-live rungs; Section 6 -> tests embedded per task. Out-of-scope items untouched by any task.
- No placeholders; all code blocks are concrete (the single `<winner-slug>`/`<new ANCHOR_CONCEPT_ID>` substitutions are sync-point artifacts produced by T7, by design).
- Names/types consistent across tasks: `MIN_BASIC_CARDS` (validate) / `MIN_BASIC_POKEMON` (builder alias) / `ANCHOR_DECK_PATH`/`ANCHOR_CONCEPT_ID`/`ANCHOR_DECK_ID` (anchor) / `run_migration(conn, dry_run) -> dict`.
