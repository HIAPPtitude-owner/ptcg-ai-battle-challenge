# Unpayable-Attack Pool Rule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce strict attack-payability (type coverage) as a hard deck rule, make the builder produce payable decks (multi-type energy + payability-aware fillers), and repair-in-place every violating pool deck.

**Architecture:** `validate.py` gains the rule (single source of truth, mirroring `deck_quality`'s flag semantics exactly). `builder.py` extends energy typing from best-attack-only to all-chain-attacks with a minimal secondary splash and filters fillers by payability. A new `deck_repair.py` provides the deterministic repair primitive the migration uses — repair, not rebuild, because pool decks include mutated reseeds that `build_deck(concept)` cannot reproduce (spec amendment, surfaced to Brad at plan handoff). Spec: `docs/superpowers/specs/2026-08-12-unpayable-attack-pool-rule-design.md` (`75dcec0`).

**Tech Stack:** Python 3.11 / uv / pytest / sqlite3. No new dependencies.

## Global Constraints

- Branch: `feature/unpayable-attack-pool-rule` (single working tree, no worktree). SUBMIT_HOLD is ON — uploads impossible; do not touch it.
- Commit hygiene: stage by explicit path AND commit by explicit pathspec. Never bare `git commit` / `-a` / `git add .`. `index.lock` contention: wait 2s, retry ≤3×.
- Implementers run ONLY their named targeted test files; the orchestrator owns full-suite runs at sync points.
- Rule semantics are TYPE COVERAGE ONLY (quantity out of scope), mirroring `deck_quality.py:171-193` exactly: non-COLORLESS cost types of every attack of every Pokémon ⊆ deck energy types (BASIC_ENERGY + SPECIAL_ENERGY cards); unknown attack ids skipped silently; 1-based attackId dict lookup, never `all_attack()[i]`.
- Enum ints: `cg.utils.to_dataclass` leaves enum fields as raw ints — always compare via `int(...)` and wrap with `EnergyType(x)` only when `.name` is needed.
- `Path.write_text(..., encoding="utf-8")` everywhere.
- Prior-read files: use `Grep -n . <path> -A 5000`, not Read.
- Hand-verify plan-authored assertions/arithmetic before transcribing; grep cited landmarks first; report `plan-drift` on mismatch.
- Production `tournament.db` is NEVER opened read-write by tests or probes — tmp fixture DBs and `mode=ro` URIs only. Only the migration script (T4) writes, and only when pointed at a DB explicitly.
- Executable pre-lock receipts (2026-08-12): anchor deck `anchor-min8.csv` → zero flags; ladder deck `mega-lucario-fighting.csv` → no `unpayable-attack` flag (only `mulligan-risk`, expected — ladder identity files are not pool-managed and must NOT be modified by this slice).

## Verified landmarks (grepped at plan-lock, 2026-08-12)

| Landmark | Location | Fact |
|---|---|---|
| validate_deck today | `src/ptcg/decks/validate.py:27-50` | 60-cards / unknown-ids / ≤4 copies / ≥1 basic / `MIN_BASIC_CARDS=8` / ≤1 ACE SPEC. Has `_card_db()` lru_cache; no attack DB import yet |
| Flag semantics | `src/ptcg/factory/deck_quality.py:160-193` | energy set from BASIC/SPECIAL energy `energyType`; per-attack `typed = {e != COLORLESS}`; flag if `typed - deck_energy_types`; unknown attack ids skipped; `_attack_db()` is 1-based dict at `:87-93` |
| Energy allocation | `src/ptcg/factory/builder.py:221-236` | `_energy_allocation(types_needed, total)` — deterministic proportional split, already multi-type capable |
| type_needs today | `builder.py:280-285` | best attack only: `needed = [int(e) for e in best.energies if int(e) != int(EnergyType.COLORLESS)]`; falls back `DEFAULT_FILLER_ENERGY_TYPE` |
| Fillers | `builder.py:239-256` | `_pad_with_basics(cards, used_names, target)` iterates `_filler_basic_order()`, energy-blind |
| Chains in deck | `builder.py:272-297` | deck contains ALL evolution-chain stages (`_stage_chain`), so payability must cover chain cards' attacks, not just the core's (plan-time correction to the spec's "core attacks" phrasing — the flag fires on any Pokémon in the deck) |
| Migration template | `scripts/migrate_min_basics_pool.py:126-184` | read-only pre-pass → `BEGIN IMMEDIATE` → drain guard (`:147-155`) → writes → receipts → `--dry-run` ROLLBACK; idempotent via status-guarded UPDATEs |
| Coverage/games keys | `src/ptcg/factory/deckdb.py:144-147`, `:94-103` | coverage keys `concept_id`; games reference deck ids → in-place card rewrite keeps integrity; coverage reset still required |
| validate consumers (tests) | `tests/test_validate.py`, `test_factory_builder.py`, `test_factory_breeding.py`, `test_deck_quality.py`, `test_anchor_candidates.py`, `test_current.py`, `test_factory_anchor.py`, `test_factory_deck_matrix.py`, `test_migrate_min_basics_pool.py`, `test_reseed_tournament_pool.py` | all import `validate_deck` — T1 sweeps all 10 |
| Breeding enforcement | `src/ptcg/factory/breeding.py:182` | children accepted via `validate_deck` — rule applies automatically |

Measured facts feeding this plan (2026-08-12 read-only probes): flag hits 16,357/17,447 active; 13,200 filler-only; 642/708 core species mono-payable (CORE CARD only — chain-stage attacks add an unquantified delta, captured by T4's dry-run receipts); filler supply 158 colorless-only + 213-224 per major type; P2=P3=0.

---

### Task 1: validate_deck payability rule + 10-file consumer sweep

**Files:**
- Modify: `src/ptcg/decks/validate.py`
- Test: `tests/test_validate.py` (append new; sweep the other 9 consumer files for fixture breakage)

**Interfaces:**
- Produces: `attack_payability_problems(deck: list[int]) -> list[str]` (module-level, used by validate_deck internally; T3/T4 reuse it); `validate_deck` now includes its output.

- [ ] **Step 1: Failing tests** — append to `tests/test_validate.py` (follow its existing fixture style — grep it first; use real card ids resolved via `cg.api.all_card_data()` helpers the file already uses, or build minimal decks around the anchor CSV):

```python
def _cards_from_csv(rel):
    from pathlib import Path
    return [int(x) for x in Path(rel).read_text(encoding="utf-8").split()]


def test_payability_anchor_and_ladder_pass():
    anchor = _cards_from_csv("src/ptcg/decks/candidates/anchor-min8.csv")
    ladder = _cards_from_csv("src/ptcg/decks/candidates/mega-lucario-fighting.csv")
    assert attack_payability_problems(anchor) == []
    assert attack_payability_problems(ladder) == []


def test_payability_flags_off_type_pokemon():
    """Take the anchor deck (payable) and swap one payable filler for a
    Pokémon whose attack needs an energy type the deck does not run.
    Find the off-type Pokémon EXECUTABLY: scan all_card_data() for a basic
    Pokémon whose every attack has a typed cost disjoint from the anchor's
    energy types (verify-game-data-claims rule: never hardcode a card id
    from memory — derive it in the test)."""
    deck = _cards_from_csv("src/ptcg/decks/candidates/anchor-min8.csv")
    off_type = _find_offtype_basic_for(deck)  # helper you write in the test file
    mutated = deck[:-1] + [off_type.cardId]
    probs = attack_payability_problems(mutated)
    assert probs and off_type.name in " ".join(probs)
    assert any(off_type.name in p for p in validate_deck(mutated))


def test_payability_colorless_only_always_passes():
    """A colorless-only-attack basic added to any deck never trips the rule
    (find one executably, same discipline)."""
```

- [ ] **Step 2: Run** `uv run pytest tests/test_validate.py -v` — new tests FAIL (`attack_payability_problems` undefined). Existing pass.

- [ ] **Step 3: Implement** in `validate.py` (mirror `deck_quality.py:160-193` semantics EXACTLY — grep it side-by-side while writing):

```python
from cg.api import Attack, CardData, CardType, EnergyType, all_attack, all_card_data


@lru_cache(maxsize=1)
def _attack_db() -> dict[int, Attack]:
    """1-based attackId -> Attack (never index all_attack() directly)."""
    return {a.attackId: a for a in all_attack()}


def attack_payability_problems(deck: list[int]) -> list[str]:
    """Strict type-coverage payability (spec 2026-08-12): every non-COLORLESS
    cost type of every attack of every Pokémon must be a deck energy type.
    Quantity deliberately out of scope — mirrors deck_quality's flag."""
    db = _card_db()
    if any(cid not in db for cid in deck):
        return []  # unknown ids are validate_deck's own finding, not ours
    attacks = _attack_db()
    colorless = int(EnergyType.COLORLESS)
    energy_types = {
        int(db[cid].energyType)
        for cid in deck
        if db[cid].cardType in (CardType.BASIC_ENERGY, CardType.SPECIAL_ENERGY)
    }
    problems: list[str] = []
    for cid in sorted(set(deck)):
        card = db[cid]
        if card.cardType != CardType.POKEMON:
            continue
        for attack_id in card.attacks:
            atk = attacks.get(attack_id)
            if atk is None:
                continue  # mirror deck_quality: unknown attack ids skipped
            missing = {int(e) for e in atk.energies if int(e) != colorless} - energy_types
            if missing:
                problems.append(
                    f"'{card.name}' attack {attack_id} needs energy types "
                    f"{sorted(missing)} not provided by deck energy {sorted(energy_types)}"
                )
    return problems
```

and append `problems.extend(attack_payability_problems(deck))` at the end of `validate_deck` (after the ACE SPEC check, before `return`).

- [ ] **Step 4: Targeted run** `uv run pytest tests/test_validate.py -v` — all pass.
- [ ] **Step 5: Consumer sweep** — run the other 9 consumer test files by name (one pytest invocation listing all 9 paths). Any failure caused by a fixture deck that is now invalid gets its FIXTURE fixed (make the fixture payable — usually add/replace an energy card or swap the off-type Pokémon), never by weakening an assertion. Diff-discipline: additions/fixture-edits only; if a test's assertion must change, name the dropped coverage in your report. Note: some consumers (builder/breeding tests) may legitimately stay red until T2 lands — if a failure is caused by `build_deck` output now failing validate (not by the fixture), record it as a NAMED transient for T2 (`carry-forward: <test> red until T2`) rather than patching the builder yourself.
- [ ] **Step 6: pyright** `uv run pyright src/ptcg/decks/validate.py` fresh run — 0 errors.
- [ ] **Step 7: Commit** `git add src/ptcg/decks/validate.py tests/test_validate.py <any swept test files>` then commit the same explicit paths, `-m "feat: strict attack-payability rule in validate_deck"` + session trailer.

---

### Task 2: Builder — all-chain energy typing, secondary splash, payability-aware fillers

**Files:**
- Modify: `src/ptcg/factory/builder.py`
- Test: `tests/test_factory_builder.py` (append; clear any T1 named transient)

**Interfaces:**
- Consumes: `attack_payability_problems` (T1).
- Produces: `_deck_energy_plan(chains) -> tuple[list[int], dict[int, int]] | str` (primary_needs, secondary_splash) — returns reason string when unbuildable; `_pad_with_basics(cards, used_names, target, allowed_types)` (new 4th param); `_filler_payable(card, allowed_types) -> bool`. T3 reuses `_filler_payable` and the splash logic.

- [ ] **Step 1: Golden pin FIRST (against CURRENT code, pre-change).** Pick a real mono-payable single core (derive executably: first `enumerate_concepts()` entry whose full chain-attack set is single-type). Run current `build_deck` and embed its exact 60-card output as a literal:

```python
def test_mono_payable_core_bit_identical_golden():
    """R2(d): mono-payable cores build BIT-IDENTICAL decks after the
    multi-type change. GOLDEN generated from pre-change build_deck at
    commit <fill in>: concept=<name>, cards=<60-int literal>."""
    result = build_deck(Concept(cores=("<name>",)))
    assert result.cards == GOLDEN_MONO_CARDS
```
Record in your report the pre-change run output you pinned (honest golden — generated before any builder edit).

- [ ] **Step 2: Failing tests** — append: (a) a 2-type core (find executably: a core whose chain has a secondary attack typed outside the best attack's type) builds with `attack_payability_problems(result.cards) == []`, total energy count unchanged vs `SINGLE_ENERGY_TOTAL`, primary type count > secondary type count; (b) fillers: every built deck passes `attack_payability_problems`; (c) >2 distinct energy types → `unbuildable_reason` mentions the cap; (d) determinism: two builds of the same concept are identical.
- [ ] **Step 3: Run** — golden passes (no change yet), new ones fail.
- [ ] **Step 4: Implement.** In `build_deck`: replace the `type_needs` block. Compute per-core: `primary_needs` from best attack (unchanged); then walk EVERY card in EVERY chain, every attack via `_attacks_by_id()`, collect `secondary_max[t] = max(secondary_max.get(t, 0), count_of_t_in_attack)` for each non-COLORLESS type `t` not in `set(primary_needs)`. Cap: `len(set(primary_needs) | set(secondary_max)) > 2` → `BuildResult(None, f"needs {n} energy types (cap 2)")`. Allocation: `secondary_total = sum(secondary_max.values())`; if `energy_total - secondary_total < len(set(primary_needs))` → unbuildable reason; primary alloc = `_energy_allocation(primary_needs, energy_total - secondary_total)`; merge secondary_max into the alloc dict (types are disjoint by construction). Fillers: `allowed = set(primary_needs) | set(secondary_max)`; pass to `_pad_with_basics(cards, used_names, remaining, allowed)`; inside, skip cards failing `_filler_payable(card, allowed)`:

```python
def _filler_payable(card: CardData, allowed_types: set[int]) -> bool:
    attacks_by_id = _attacks_by_id()
    colorless = int(EnergyType.COLORLESS)
    return all(
        {int(e) for e in atk.energies if int(e) != colorless} <= allowed_types
        for attack_id in card.attacks
        if (atk := attacks_by_id.get(attack_id)) is not None
    )
```
Hand-verify the mono-payable path is genuinely unchanged: `secondary_max == {}` → `secondary_total == 0` → allocation identical → golden stays green.
- [ ] **Step 5: Run** `uv run pytest tests/test_factory_builder.py tests/test_validate.py -v` — all pass incl. golden; clear/verify any T1 named transient in your report.
- [ ] **Step 6: pyright** fresh on builder.py — 0 errors. **Step 7: Commit** explicit pathspec, `-m "feat: multi-type energy + payability-aware fillers in builder"` + trailer.

---

### Task 3: `deck_repair.py` — deterministic in-place repair primitive

**Files:**
- Create: `src/ptcg/factory/deck_repair.py`
- Test: `tests/test_deck_repair.py` (new)

**Interfaces:**
- Consumes: `attack_payability_problems` (T1), `_filler_payable`, `_filler_basic_order`, `_attacks_by_id`, `_energy_by_type` (T2/builder), `validate_deck`.
- Produces: `repair_deck(cards: list[int]) -> tuple[list[int], str] | None` — repaired 60-card list + human-readable summary of changes, or `None` when unrepairable (caller culls). Pure, deterministic, no DB access.

Algorithm (repair, not rebuild — preserves mutated-deck identity):
1. If `attack_payability_problems(cards) == []` → return `(cards, "already-payable")`.
2. Partition offending Pokémon: an offender is SWAPPABLE iff it is a basic Pokémon that nothing in the deck evolves from (`evolvesFrom` of any other deck Pokémon ≠ its name) — i.e. a filler; otherwise it is CHAIN-BOUND.
3. Swappable offenders: replace all copies with equal copies of payable fillers (`_filler_basic_order()` filtered by `_filler_payable(card, deck_energy_types)`, skipping names already present) — basic-for-basic so `MIN_BASIC_CARDS` is preserved.
4. Chain-bound offenders: compute missing types across their attacks (max single-attack count per type, as T2). If `len(current_energy_types | missing_types) > 2` → return None. Else convert K copies of the MOST-COMMON current energy type to the missing type (K = the max-count splash), keeping total energy count constant. If the most-common type would drop below the max primary-attack cost of the deck's highest-damage payable attacker, return None (don't starve the primary).
5. Re-check: `attack_payability_problems == []` AND `validate_deck == []` — else return None.

- [ ] **Step 1: Failing tests** — fixture decks built from real cards (derive ids executably): (a) filler-only offender → repaired, same length, min-basics preserved, payable, non-filler cards untouched; (b) chain-bound 2nd-type case → energy splash applied, count constant, payable; (c) 3-type case → None; (d) already-payable → unchanged identity (`repaired == cards`); (e) determinism: repair twice → identical; (f) idempotence: `repair_deck(repaired)[0] == repaired`.
- [ ] **Step 2: RED run** `uv run pytest tests/test_deck_repair.py -v`. **Step 3: Implement** per algorithm. **Step 4: GREEN run.** **Step 5: pyright fresh — 0 errors.** **Step 6: Commit** explicit pathspec, `-m "feat: deterministic deck_repair primitive"` + trailer.

---

### Task 4: Migration script `scripts/migrate_unpayable_pool.py`

**Files:**
- Create: `scripts/migrate_unpayable_pool.py`
- Test: `tests/test_migrate_unpayable_pool.py` (new)

**Interfaces:**
- Consumes: `repair_deck` (T3), `deckdb`, `attack_payability_problems` (T1).
- Produces: CLI `--db` (REQUIRED, no default — reseed-script precedent: never default to production in a destructive script), `--dry-run`.

Template: `scripts/migrate_min_basics_pool.py:126-184` — read-only classification pre-pass OUTSIDE the lock; then ONE `BEGIN IMMEDIATE` containing: drain guard (abort+rollback if pending/claimed games exist, same query shape as the template's `:147-155`); per-deck actions; receipts dict printed at the end; `--dry-run` executes everything then ROLLBACK.

Scope & actions inside the transaction:
- Every `decks` row whose concept is not `culled`. EXCLUSIONS (never touched, counted in receipts): deck ids referenced by any `baselines` row; the anchor concept's decks; `finalist` concepts' decks.
- Violating deck (`attack_payability_problems(cards) != []`): `repair_deck` → on success `UPDATE decks SET cards=? WHERE id=?` (same id) + coverage reset for its concept (zero `games_played`/`distinct_opponents`/`rating` — copy the exact column semantics from `migrate_min_basics_pool.py`'s coverage reset, grep it); on `None` → cull concept with `reason='unpayable-rule: <iso>'` (status-guarded UPDATE, idempotent).
- Receipts: `scanned, violating, repaired, culled_unrepairable, coverage_reset, excluded_baselines, excluded_anchor, excluded_finalist, already_payable, chain_delta` (chain_delta = violating decks whose offenders were chain-bound — quantifies the core-only measurement gap).
- Canary (post-commit, read-only): recount flag over active pool; print. Idempotency: second run → `violating=0`.

- [ ] **Step 1: Failing tests** — tmp fixture DB (copy `test_migrate_min_basics_pool.py`'s seeding style — grep it first) with one row per class: filler-only violator, chain-bound violator, unrepairable (3-type), already-payable, baselines-referenced violator (must be EXCLUDED and survive byte-identical), anchor, finalist, culled concept (ignored). Assert per-class receipts, in-place id preservation, coverage reset, exclusion integrity, idempotent second run, dry-run leaves DB byte-identical (hash the file before/after).
- [ ] **Step 2: RED.** **Step 3: Implement.** **Step 4: GREEN** (`uv run pytest tests/test_migrate_unpayable_pool.py -v`). **Step 5: pyright fresh — 0 errors.** **Step 6: Commit** explicit pathspec, `-m "feat: unpayable-pool repair migration"` + trailer.

---

### Task 5: Breeding yield verification (measurement, not code — code only if yield collapses)

**Files:** none expected; possibly `src/ptcg/factory/breeding.py` if the fix path triggers.
**Consumes:** T1+T2 on disk.

- [ ] **Step 1:** With REAL breeding calls (no fixtures): run ≥200 `breed_deck` attempts across real parent decks sampled from a read-only copy of the production DB (mode=ro; sample across distinct concepts, not the first N — sampling-breadth rule). Report: valid-child yield rate before retries exhaust, mean attempts per accepted child.
- [ ] **Step 2:** If yield ≥ 50% of pre-rule yield (measure pre-rule by running the same sample against `git stash`-free BASE code is NOT possible mid-branch — instead compare against the same run with the payability check monkeypatched out), record numbers and stop. If collapsed below that: apply the R2 energy-plan helper to the child's cores before validation (small breeding.py change + test), then re-measure.
- [ ] **Step 3:** Write both numbers into the report + ledger; commit only if code changed.

---

### Task 6: Go-live runbook (orchestrator + Brad — POST-merge)

- [ ] 1. Flag-reconcile `scripts/migrate_unpayable_pool.py --help` against this plan (golive-command-preflight).
- [ ] 2. Import-graph walk receipts: grep `factory_watch_once.py` + `loop_scheduler.py`/`runner_pool.py` import chains for `builder`/`validate`/`deck_repair`; name which long-lived workers need restart.
- [ ] 3. Merge to master → `Stop-ScheduledTask`/`Start-ScheduledTask` on `ptcg-factory-runner` + `ptcg-factory-scheduler` (non-elevated); verify next provenance stamp carries the merge commit.
- [ ] 4. Migration `--dry-run` against production `tournament.db` (explicit `--db`); review receipts (esp. `chain_delta`, `culled_unrepairable`, exclusion counts); then REAL run in the same sitting; record receipts. Sizing note (do not plan the PAUSE window off the plan-time dry-run's ~29min wall time — that measurement is stale): the pool grew 2.15x overnight (scanned 45,087 / violating 42,579 at 2026-08-13 08:37 vs the plan-time 20,957/19,668), but the T3 fix-wave's `_card_db` `@lru_cache` collapses the read-only pre-pass to ~1-2 min; this rung's own fresh `--dry-run` at execution time is the authoritative re-sizing — trust its printed receipts, not any earlier estimate.
- [ ] 5. Canary: `unpayable-attack` flag ≈ 0 over active pool (excluded rows reported separately); census re-screen resuming (rating-throttle stall expected, not an anomaly).
- [ ] 6. Record in `experiments/EXPERIMENTS.md` + plan.md; disk free-space check.

---

## Self-review (done at plan-write)

- Spec coverage: R1→T1, R2/R3→T2, R4→T5, R5→T3+T4 (with the repair-not-rebuild amendment flagged for Brad at handoff), R6 canary→T4/T6. Chain-attack scope correction documented in landmarks.
- Placeholders: none (T2 golden literal is generated by the implementer at Step 1 by design — the instruction says exactly how).
- Type consistency: `attack_payability_problems(list[int]) -> list[str]` consistent T1/T3/T4; `_filler_payable(card, set[int])` consistent T2/T3; `repair_deck -> tuple[list[int], str] | None` consistent T3/T4.
