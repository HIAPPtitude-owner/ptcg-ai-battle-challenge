# Pool-Pruning UI Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the localhost pool-pruning UI (`ptcg-factory-ui`, port 8765) per-deck composition-quality analysis (counts, mulligan %, red/amber flags), the parked UX polish items (flash banners, bulk-button affordance, `reason` render, HST timestamp), a `/pool?problems=1` filter, and a `ThreadingHTTPServer` swap so slow writes stop freezing every other page.

**Architecture:** A new pure, watch-loop-inert module `src/ptcg/factory/deck_quality.py` analyzes a 60-card deck from the engine card DB (`cg.api.all_card_data()` / `all_attack()`, already loaded in the UI process) into a `DeckQualityReport`; `ui_pages.py` renders it as a summary line + severity badges + reason list; `ui_server.py` wires it into `/`, `/pool`, `/search`, adds the problems filter and flash-banner PRG redirects; `scripts/factory_ui.py` swaps `HTTPServer` → `ThreadingHTTPServer`. Analysis runs strictly AFTER DB reads complete (post-fetch, in Python) — zero added lock-hold time on `tournament.db`.

**Tech Stack:** Python 3.13 stdlib only (dataclasses, functools.lru_cache, http.server, sqlite3, datetime). Engine card DB via the vendored `cg` SDK. pytest. No new dependencies.

## Global Constraints

- **Zero added lock-hold time on `tournament.db`.** No new SQL writes; the ONLY new SQL is one indexed read (`canonical_decks`, Task 7 — EQP receipt in the Pre-lock appendix shows `SEARCH decks USING INDEX ix_decks_concept`). `deckdb.py` is NOT modified. No `BEGIN IMMEDIATE` anywhere in this slice.
- **Watch-loop inertness:** `deck_quality.py` may be imported ONLY from `ui_pages.py` / `ui_server.py` (and tests). `scripts/factory_watch_once.py` imports `deckdb, episodes, subscheduler, cycle, gate, kaggle_client, watch` (verified 2026-08-10, its lines 51-55) — no `ui_*` module, so all slice code is unreachable from the watch loop. Do not add any import that changes this.
- **Test running:** implementers run ONLY the test file(s) named in their own task via `uv run pytest tests/<file> -q` — NEVER the full suite (`.claude/rules/dispatch-test-run-directive.md`; the orchestrator owns full-suite runs at sync points).
- **Repo conventions:** attack IDs are 1-based and MUST be resolved via a `{a.attackId: a}` dict from `cg.api.all_attack()`, never by list index. Quote all paths (the repo path contains a space). Stage commits by explicit path only; on `index.lock` contention wait 2s and retry up to 3x. Any `Path.write_text` needs `encoding="utf-8"` (none is planned).
- **Sort order on `/pool` stays rating DESC** (comes from SQL; the problems filter must preserve row order).
- Ladder identity files (`src/ptcg/submission_main.py`, `src/ptcg/agents/current.py`) are untouched by this slice.

## Verified Landmark Table (all verified 2026-08-10 against the working tree)

| Landmark | Location | Verified fact |
|---|---|---|
| Engine DB already in UI process | `src/ptcg/factory/ui_pages.py:26` (import), `:56-62` (`_card_id_to_name` lru_cache) | UI already calls `all_card_data()`; spec's no-DLL premise moot (Task 1 amends) |
| Category split pattern | `src/ptcg/decks/analysis.py:52-64` | POKEMON; energy = BASIC_ENERGY\|SPECIAL_ENERGY; trainer = ITEM\|TOOL\|SUPPORTER\|STADIUM; `_dbs()` caches `all_card_data()` + `{a.attackId: a}` |
| Basics predicate | `src/ptcg/decks/validate.py:15-19` | `cardType == CardType.POKEMON and card.basic` |
| CardData fields | `src/cg/api.py:463-483` | `cardId, name, cardType, basic/stage1/stage2, evolvesFrom (pre-evo NAME or None), energyType, attacks: list[int]` |
| Attack fields | `src/cg/api.py:484-491` | `attackId, name, damage, energies: list[EnergyType]` |
| EnergyType members | runtime print | `COLORLESS, GRASS, FIRE, WATER, LIGHTNING, PSYCHIC, FIGHTING, DARKNESS, METAL, DRAGON, RAINBOW, TEAM_ROCKET` |
| Routes | `src/ptcg/factory/ui_server.py`: `make_app` :282, GET `/` :324, `/status` :331, `/pool` :338, `/search` :349; POST `/decision` :374, `/concept-decision` :400, `/bulk-confirm` :448, `/bulk-decision` :463 | fresh conn per request; `now` seam kwarg :286 |
| Renderers | `src/ptcg/factory/ui_pages.py`: `render_review_page` :73, `render_status_page` :100 (`status['now']` rendered :129), `render_pool_page` :134, `render_search_page` :185, `render_bulk_confirm_page` :272, `render_bulk_result_page` :290 | `_shell(title, body)` wrapper; pool rows decode `cards` JSON text |
| `reason` SELECTed but dropped | `src/ptcg/factory/ui_actions.py:47` | `c.reason AS reason` in `search_concepts` SELECT; never rendered |
| Canonical-deck convention | `src/ptcg/factory/ui_actions.py:19-28` (`_POOL_QUERY`) | canonical deck = MIN(`shell_variant`) per concept |
| Server swap site | `scripts/factory_ui.py:34` (`from http.server import HTTPServer`), `:71` (`httpd = HTTPServer(...)`) | exact lines confirmed |
| Champion deck fixture | `experiments/factory/tournament.db` (read-only query, appendix) | concept `reseed-mega-starmie-water-density20`, deck `reseed-mega-starmie-water-density20-sv0`; 60 ids embedded in Task 4; pk=8/tr=32/en=20/basics=4; ZERO flags under this plan's semantics (prototype-executed) |
| Junk deck fixture | `pokemon-tcg-ai-battle/sample_submission/sample_submission/deck.csv` | **35**x card ID 3 (project CLAUDE.md says "34x" — doc drift, real file has 35); pk=10/tr=15/en=35/basics=6 |
| zoneinfo unavailable | runtime | `ZoneInfo('Pacific/Honolulu')` raises `ZoneInfoNotFoundError` (no tzdata on this Windows Python) — Task 1 amends spec to fixed UTC-10 offset |
| Live-server test pattern | `tests/test_factory_ui_server.py:273-284` (`_start_server`/`_stop_server`), `:27-52` (`_seed_concept`/`_seeded_db`) | copy this pattern for any test needing a live server |

---

### Task 1: Spec amendments (loader clause + HST timezone mechanism)

**Files:**
- Modify: `docs/superpowers/specs/2026-08-10-pool-pruning-ui-improvements-design.md` (Section 1 paragraph beginning "Card metadata comes from"; Section 3 bullet containing "zoneinfo `Pacific/Honolulu`")

**Interfaces:**
- Consumes: nothing.
- Produces: the amended spec text that Tasks 2 and 10 implement against.

- [ ] **Step 1: Amend Section 1's loader clause.** Replace the paragraph:

> Card metadata comes from `EN_Card_Data.csv` parsed directly (or reuse an existing
> factory-side loader if one exists — the plan phase resolves this; the engine DLL must
> NOT be loaded into the UI process).

with:

> Card metadata comes from the engine DB via `cg.api.all_card_data()` /
> `cg.api.all_attack()`. (Amended 2026-08-10 at plan time: the "engine DLL must NOT be
> loaded into the UI process" premise was moot — the UI process ALREADY loads the
> engine DB for card-name resolution, `ui_pages.py:26` import + `_card_id_to_name`
> cache at :56-62. Decision: reuse `all_card_data()`, no CSV parser.)

- [ ] **Step 2: Amend Section 3's HST bullet.** Replace:

> - `/status` timestamp rendered in HST alongside UTC via zoneinfo `Pacific/Honolulu`
>   ("as of 3:42 PM HST · 01:42 UTC").

with:

> - `/status` timestamp rendered in HST alongside UTC ("as of 3:42 PM HST · 01:42 UTC")
>   via a fixed offset `datetime.timezone(timedelta(hours=-10), "HST")`. (Amended
>   2026-08-10 at plan time: `zoneinfo.ZoneInfo("Pacific/Honolulu")` raises
>   `ZoneInfoNotFoundError` on this Windows host — no tzdata package installed, receipt
>   in the plan's Pre-lock appendix. Hawaii observes no DST, so the fixed offset is
>   permanently exact and avoids a new dependency.)

- [ ] **Step 3: Commit**

```bash
git add "docs/superpowers/specs/2026-08-10-pool-pruning-ui-improvements-design.md"
git commit -m "docs: amend pool-pruning spec (reuse engine card DB; fixed-offset HST)"
```

---

### Task 2: `deck_quality.py` core — counts, mulligan, ratio + basics flags

**Files:**
- Create: `src/ptcg/factory/deck_quality.py`
- Create: `tests/test_deck_quality.py`

**Interfaces:**
- Consumes: `cg.api.all_card_data() -> list[CardData]`, `cg.api.all_attack() -> list[Attack]`, `cg.api.CardType`, `cg.api.EnergyType` (fields verified in the Landmark Table).
- Produces (later tasks rely on these exact names):
  - `@dataclass(frozen=True) Flag(code: str, severity: str, reason: str)` — severity is `"red"` or `"amber"`.
  - `@dataclass(frozen=True) DeckQualityReport(pokemon_count: int, trainer_count: int, energy_count: int, basics_count: int, mulligan_pct: float, flags: tuple[Flag, ...])` — `mulligan_pct` is a PROBABILITY in [0, 1]. `flags` is a tuple (not list) so the report is hashable/frozen; iteration/truthiness use is identical.
  - `analyze_deck(card_ids: list[int]) -> DeckQualityReport` (raises `KeyError` on unknown card id).
  - `mulligan_probability(basics_count: int, deck_size: int = 60) -> float`.
  - Threshold constants: `ENERGY_HEAVY_MAX = 30`, `ENERGY_STARVED_MIN = 5`, `POKEMON_MIN = 8`, `BASICS_RED_MAX = 2`.

Thresholds are CALIBRATED, not guessed (measured 2026-08-10 against the live 9-deck pool: champion pk=8/tr=32/en=20/basics=4; anchor pk=8/tr=26/en=26/basics=4; pool ranges pk 7-8 / tr 24-33 / en 20-28 / basics 3-4). Flags fire only OUTSIDE those live ranges.

**Arithmetic verification (executed 2026-08-10 — do not change these constants without re-running):**

```
uv run python -c "
for b in (0, 1, 2, 3, 4, 6, 8):
    p = 1.0
    for i in range(7):
        p *= (60 - b - i) / (60 - i)
    print(b, p)"
# Output:
# 0 1.0
# 1 0.8833333333333332
# 2 0.7785310734463277
# 3 0.6845704266510811
# 4 0.6005003742553344
# 6 0.458563922158619
# 8 0.3464064289681811
```

- [ ] **Step 1: Write the failing test** — `tests/test_deck_quality.py`:

```python
"""Tests for the UI-only deck composition analyzer (pool-pruning slice).

Every numeric constant below was hand-verified by executing the hypergeometric
product before being written down (plan-test-arithmetic-sanity lesson); the
verification command + output are recorded in the plan
(docs/superpowers/plans/2026-08-10-pool-pruning-ui-improvements.md, Task 2).
"""
from __future__ import annotations

import pytest

from ptcg.factory import deck_quality
from ptcg.factory.deck_quality import Flag, analyze_deck, mulligan_probability

_WATER_ENERGY_ID = 3  # "Basic {W} Energy" -- stable real card id (see CLAUDE.md)


def _codes(report) -> set[str]:
    return {f.code for f in report.flags}


def test_mulligan_probability_exact_hypergeometric():
    # prod_{i=0..6}((60-B-i)/(60-i)), verified by hand-run in the plan.
    assert mulligan_probability(0) == 1.0
    assert mulligan_probability(2) == pytest.approx(0.7785310734463277)
    assert mulligan_probability(4) == pytest.approx(0.6005003742553344)
    assert mulligan_probability(8) == pytest.approx(0.3464064289681811)


def test_counts_and_energy_heavy_red():
    # 40 water energy + 20 copies of a real Basic Pokemon would break the
    # 4-copy rule, but the analyzer is deliberately legality-agnostic
    # (legality is validate_deck's job); composition math is what's under test.
    # Card 1030 is Staryu (Basic, WATER) -- from the champion fixture.
    deck = [_WATER_ENERGY_ID] * 40 + [1030] * 20
    report = analyze_deck(deck)
    assert report.energy_count == 40
    assert report.pokemon_count == 20
    assert report.trainer_count == 0
    assert report.basics_count == 20
    assert "energy-heavy" in _codes(report)          # 40 > ENERGY_HEAVY_MAX=30
    assert "no-trainers" in _codes(report)           # trainer_count == 0 -> red
    heavy = next(f for f in report.flags if f.code == "energy-heavy")
    assert heavy.severity == "red"


def test_energy_starved_and_few_pokemon():
    # 2 energy (< 5 -> red), 6 Pokemon (< 8 -> amber). 1121 is a Trainer
    # (Item) from the champion fixture, used as neutral filler.
    deck = [_WATER_ENERGY_ID] * 2 + [1030] * 6 + [1121] * 52
    report = analyze_deck(deck)
    assert report.energy_count == 2
    assert report.pokemon_count == 6
    assert "energy-starved" in _codes(report)
    few = next(f for f in report.flags if f.code == "few-pokemon")
    assert few.severity == "amber"


def test_basics_two_or_fewer_is_red_and_pct_always_computed():
    deck = [1030] * 2 + [_WATER_ENERGY_ID] * 20 + [1121] * 38
    report = analyze_deck(deck)
    assert report.basics_count == 2
    assert report.mulligan_pct == pytest.approx(0.7785310734463277)
    risk = next(f for f in report.flags if f.code == "mulligan-risk")
    assert risk.severity == "red"
    # 4 basics: pct still computed, but no mulligan-risk flag (4 > BASICS_RED_MAX=2).
    ok = analyze_deck([1030] * 4 + [_WATER_ENERGY_ID] * 20 + [1121] * 36)
    assert ok.mulligan_pct == pytest.approx(0.6005003742553344)
    assert "mulligan-risk" not in _codes(ok)


def test_unknown_card_id_raises_keyerror():
    with pytest.raises(KeyError):
        analyze_deck([99999999] * 60)


def test_analysis_is_cached_and_order_insensitive():
    a = analyze_deck([1030, _WATER_ENERGY_ID, 1121])
    b = analyze_deck([1121, 1030, _WATER_ENERGY_ID])
    assert a is b  # canonicalized tuple key -> lru_cache hit


def test_flag_is_frozen_dataclass():
    f = Flag("x", "red", "why")
    with pytest.raises(Exception):
        f.code = "y"  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_deck_quality.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'ptcg.factory.deck_quality'`

- [ ] **Step 3: Write the implementation** — `src/ptcg/factory/deck_quality.py`:

```python
"""Deck composition quality analysis for the factory review UI.

Pure, read-only, UI-ONLY module: imported exclusively from ui_pages/ui_server
(and tests) -- NEVER from the watch loop / runner / scheduler / deckdb, so it
is watch-loop-inert by construction (import-graph receipt in the plan's
Pre-lock appendix). Card metadata comes from the engine's own database via
``cg.api.all_card_data()`` / ``all_attack()`` -- the engine DLL is already
loaded in the UI process by ``ui_pages._card_id_to_name`` (spec Section 1 as
amended 2026-08-10).

Analysis is O(60) per deck, computed strictly AFTER DB reads complete
(callers pass plain card-id lists), and cached on a canonicalized tuple key,
so a page render of ~200 rows costs at most one analysis per distinct deck.

Thresholds are calibrated against the live pool (2026-08-10): champion
pk=8/tr=32/en=20/basics=4, anchor pk=8/tr=26/en=26/basics=4, pool ranges
pk 7-8 / tr 24-33 / en 20-28 / basics 3-4. Flags fire only outside those
observed-healthy ranges.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from cg.api import Attack, CardData, CardType, EnergyType, all_attack, all_card_data

_ENERGY_TYPES = (CardType.BASIC_ENERGY, CardType.SPECIAL_ENERGY)
_TRAINER_TYPES = (CardType.ITEM, CardType.TOOL, CardType.SUPPORTER, CardType.STADIUM)

#: energy_count > 30 -> red ("energy-heavy"). Junk sample deck: 35. Pool max: 28.
ENERGY_HEAVY_MAX = 30
#: energy_count < 5 -> red ("energy-starved"). Pool min: 20.
ENERGY_STARVED_MIN = 5
#: pokemon_count < 8 -> amber ("few-pokemon"). Pool range: 7-8.
POKEMON_MIN = 8
#: basics_count <= 2 -> red ("mulligan-risk"). Pool range: 3-4.
BASICS_RED_MAX = 2


@dataclass(frozen=True)
class Flag:
    code: str      # stable machine id, e.g. "energy-heavy"
    severity: str  # "red" | "amber"
    reason: str    # one-line human explanation, rendered inside <details>


@dataclass(frozen=True)
class DeckQualityReport:
    pokemon_count: int
    trainer_count: int
    energy_count: int
    basics_count: int
    mulligan_pct: float        # P(zero Basic Pokemon in the opening 7), in [0, 1]
    flags: tuple[Flag, ...]    # tuple (not list) so the report stays frozen/hashable


@lru_cache(maxsize=1)
def _card_db() -> dict[int, CardData]:
    return {c.cardId: c for c in all_card_data()}


@lru_cache(maxsize=1)
def _attack_db() -> dict[int, Attack]:
    # attackIds are 1-based: ALWAYS resolve via this dict, never list index
    # (repo convention, see deck-findings memory).
    return {a.attackId: a for a in all_attack()}


def mulligan_probability(basics_count: int, deck_size: int = 60) -> float:
    """Exact hypergeometric P(zero Basic Pokemon in a 7-card opening hand):
    prod_{i=0..6}((deck_size - B - i) / (deck_size - i))."""
    if basics_count <= 0:
        return 1.0
    p = 1.0
    for i in range(7):
        p *= (deck_size - basics_count - i) / (deck_size - i)
    return p


def analyze_deck(card_ids: list[int]) -> DeckQualityReport:
    """Analyze a deck (any length; real decks are 60). Raises KeyError if a
    card id is not in the engine DB -- callers wanting None-on-failure use
    ``safe_analyze`` (added in a later task of this slice)."""
    return _analyze(tuple(sorted(card_ids)))


@lru_cache(maxsize=4096)
def _analyze(key: tuple[int, ...]) -> DeckQualityReport:
    db = _card_db()
    counts = Counter(key)
    for cid in counts:  # fail fast on unknown ids (KeyError) before any math
        db[cid]

    pokemon_count = sum(n for cid, n in counts.items()
                        if db[cid].cardType == CardType.POKEMON)
    energy_count = sum(n for cid, n in counts.items()
                       if db[cid].cardType in _ENERGY_TYPES)
    trainer_count = sum(n for cid, n in counts.items()
                        if db[cid].cardType in _TRAINER_TYPES)
    basics_count = sum(n for cid, n in counts.items()
                       if db[cid].cardType == CardType.POKEMON and db[cid].basic)
    mull = mulligan_probability(basics_count, deck_size=len(key))

    flags: list[Flag] = []
    if energy_count > ENERGY_HEAVY_MAX:
        flags.append(Flag("energy-heavy", "red",
                          f"{energy_count} energy cards (> {ENERGY_HEAVY_MAX})"))
    if energy_count < ENERGY_STARVED_MIN:
        flags.append(Flag("energy-starved", "red",
                          f"only {energy_count} energy cards (< {ENERGY_STARVED_MIN})"))
    if pokemon_count < POKEMON_MIN:
        flags.append(Flag("few-pokemon", "amber",
                          f"only {pokemon_count} Pokemon (< {POKEMON_MIN})"))
    if trainer_count == 0:
        flags.append(Flag("no-trainers", "red", "deck has zero Trainer cards"))
    if basics_count <= BASICS_RED_MAX:
        flags.append(Flag("mulligan-risk", "red",
                          f"only {basics_count} Basic Pokemon -- "
                          f"mulligan {round(mull * 100)}%"))

    return DeckQualityReport(
        pokemon_count=pokemon_count,
        trainer_count=trainer_count,
        energy_count=energy_count,
        basics_count=basics_count,
        mulligan_pct=mull,
        flags=tuple(flags),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_deck_quality.py -q`
Expected: all PASS. (If card 1030 or 1121 is somehow not the expected type, STOP and report `plan-drift` — both were verified in the champion fixture on 2026-08-10: 1030 = Staryu, Basic WATER Pokémon; 1121 = a Trainer/Item.)

- [ ] **Step 5: Commit**

```bash
git add "src/ptcg/factory/deck_quality.py" "tests/test_deck_quality.py"
git commit -m "feat: deck_quality analyzer core (counts, mulligan, ratio flags)"
```

---

### Task 3: `deck_quality.py` — energy-mismatch flags (dead energy, unpayable attacks)

**Files:**
- Modify: `src/ptcg/factory/deck_quality.py` (the `_analyze` function from Task 2)
- Test: `tests/test_deck_quality.py` (append)

**Interfaces:**
- Consumes: Task 2's module (`_analyze`, `_card_db`, `_attack_db`, `Flag`).
- Produces: two new flag codes appended to `DeckQualityReport.flags`: `"dead-energy"` (amber) and `"unpayable-attack"` (amber).

**Exact semantics (encode these, no other interpretation):**
- *Deck energy types* = set of `card.energyType` over the deck's BASIC_ENERGY/SPECIAL_ENERGY cards.
- *Used types* = union over ALL attacks of ALL Pokémon in the deck (any damage value — a 0-damage utility attack's cost still "uses" its energy) of the non-COLORLESS members of `attack.energies`. COLORLESS never counts as "using" a type (it is payable by anything).
- **dead-energy** (amber): each deck energy type NOT in *used types* gets one flag.
- **unpayable-attack** (amber): each (Pokémon card, attack) pair whose typed (non-COLORLESS) cost includes a type with ZERO matching energy cards in the deck gets one flag. An all-COLORLESS cost is never unpayable.
- Attack ids resolved via `_attack_db().get(attack_id)`; an id missing from the attack DB is skipped (engine data tolerance), not an error.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_deck_quality.py`:

```python
# --- energy mismatch (Task 3) -------------------------------------------
# Fixture ids (verified in the champion deck prototype run, 2026-08-10):
# 1030 = Staryu, Basic WATER Pokemon whose attack costs use WATER.
# 3    = Basic {W} (WATER) Energy.
# To build a GRASS energy id fixture without guessing, look one up from the
# engine DB at test time -- the test must not hardcode an unverified id.
from cg.api import CardType, EnergyType, all_card_data


def _energy_id_of_type(etype) -> int:
    for c in all_card_data():
        if c.cardType == CardType.BASIC_ENERGY and c.energyType == etype:
            return c.cardId
    raise AssertionError(f"no basic energy of type {etype} in engine DB")


def test_dead_energy_amber_when_type_unused_by_any_attack():
    grass_id = _energy_id_of_type(EnergyType.GRASS)
    # Staryu (WATER attacker) + GRASS energy the deck's attacks never use.
    deck = [1030] * 8 + [grass_id] * 20 + [_WATER_ENERGY_ID] * 12 + [1121] * 20
    report = analyze_deck(deck)
    dead = [f for f in report.flags if f.code == "dead-energy"]
    assert len(dead) == 1 and dead[0].severity == "amber"
    assert "GRASS" in dead[0].reason


def test_no_dead_energy_when_all_types_used():
    deck = [1030] * 8 + [_WATER_ENERGY_ID] * 20 + [1121] * 32
    assert "dead-energy" not in _codes(analyze_deck(deck))


def test_unpayable_attack_amber_when_typed_cost_has_no_matching_energy():
    grass_id = _energy_id_of_type(EnergyType.GRASS)
    # Staryu needs WATER; the deck runs ONLY GRASS energy.
    deck = [1030] * 8 + [grass_id] * 20 + [1121] * 32
    report = analyze_deck(deck)
    unpay = [f for f in report.flags if f.code == "unpayable-attack"]
    assert unpay and all(f.severity == "amber" for f in unpay)
    assert any("WATER" in f.reason for f in unpay)


def test_colorless_only_cost_is_never_unpayable_or_using():
    # A deck with zero energy: every typed attack is unpayable, but the
    # COLORLESS portion of costs must never appear as a "missing" type name.
    deck = [1030] * 8 + [1121] * 52
    report = analyze_deck(deck)
    for f in report.flags:
        if f.code == "unpayable-attack":
            assert "COLORLESS" not in f.reason
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `uv run pytest tests/test_deck_quality.py -q`
Expected: the four new tests FAIL (no `dead-energy`/`unpayable-attack` flags produced); Task 2 tests still PASS.

- [ ] **Step 3: Implement.** In `_analyze`, after the Task-2 flag block and before constructing the report, add:

```python
    attacks = _attack_db()
    deck_energy_types = {db[cid].energyType for cid in counts
                         if db[cid].cardType in _ENERGY_TYPES}
    used_types: set[EnergyType] = set()
    unpayable: list[Flag] = []
    for cid in sorted(counts):
        card = db[cid]
        if card.cardType != CardType.POKEMON:
            continue
        for attack_id in card.attacks:
            atk = attacks.get(attack_id)  # 1-based id via dict, never list index
            if atk is None:
                continue  # engine data tolerance: unknown attack id is skipped
            typed = {e for e in atk.energies if e != EnergyType.COLORLESS}
            used_types |= typed
            missing = typed - deck_energy_types
            if missing:
                names = ", ".join(sorted(e.name for e in missing))
                unpayable.append(Flag(
                    "unpayable-attack", "amber",
                    f"{card.name}'s {atk.name} needs {names} energy "
                    f"the deck does not run"))
    for etype in sorted(deck_energy_types - used_types, key=lambda e: e.name):
        flags.append(Flag("dead-energy", "amber",
                          f"{etype.name} energy is used by no attack cost "
                          f"in this deck"))
    flags.extend(unpayable)
```

- [ ] **Step 4: Run to verify all pass**

Run: `uv run pytest tests/test_deck_quality.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add "src/ptcg/factory/deck_quality.py" "tests/test_deck_quality.py"
git commit -m "feat: deck_quality energy-mismatch flags (dead energy, unpayable attacks)"
```

---

### Task 4: `deck_quality.py` — evolution flags, `safe_analyze`, golden fixtures

**Files:**
- Modify: `src/ptcg/factory/deck_quality.py`
- Test: `tests/test_deck_quality.py` (append)

**Interfaces:**
- Consumes: Tasks 2-3.
- Produces (later tasks rely on these):
  - flag codes `"evolution-break"` (red) and `"evolution-undersupply"` (amber);
  - `safe_analyze(card_ids: list[int]) -> DeckQualityReport | None` — returns None on ANY exception; this is the ONLY entry point UI rendering code may call (failure isolation: an analysis bug must never break a page).

**Exact semantics:**
- For each Stage1/Stage2 Pokémon card in the deck: `pre = card.evolvesFrom` (a NAME string or None). Pre-evo copies are counted BY NAME across the whole deck (`Counter` of `db[cid].name` weighted by copy count).
- **evolution-break** (red): `pre is None` (data oddity for a Stage card) OR zero copies of `pre` in the deck.
- **evolution-undersupply** (amber): `0 < copies(pre) < copies(this evo card)` (e.g. 4 Stage-2 on 1 Basic).

**Golden fixtures (both compositions verified by direct execution 2026-08-10 — see Pre-lock appendix for the queries/receipts):**
- Champion: deck `reseed-mega-starmie-water-density20-sv0` fetched read-only from `experiments/factory/tournament.db`. pk=8, tr=32, en=20, basics=4, mulligan = 0.6005003742553344, and ZERO flags (dead-energy/unpayable/evolution all clean — prototype-executed against the real engine DB, not assumed).
- Junk: the real sample-submission deck (`deck.csv`), **35**x Basic Water Energy filler (project CLAUDE.md says 34x — the real file has 35; harmless doc drift, noted). pk=10, tr=15, en=35, basics=6, mulligan = 0.458563922158619; `energy-heavy` red must fire.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_deck_quality.py`:

```python
# --- evolution lines + safe_analyze + goldens (Task 4) -------------------
from ptcg.factory.deck_quality import safe_analyze

# reseed-mega-starmie-water-density20-sv0, fetched read-only from
# experiments/factory/tournament.db on 2026-08-10 (query in the plan's
# Pre-lock appendix). 1031 = Mega Starmie ex (Stage 1, evolvesFrom Staryu),
# 1030 = Staryu (Basic WATER), 3 = Basic {W} Energy, rest are Trainers.
CHAMPION_DECK = (
    [1031] * 4 + [1030] * 4 + [3] * 20 + [1121] * 4 + [1102] * 4
    + [1086] * 4 + [1224] * 4 + [1213] * 4 + [1182] * 4 + [1097] * 4
    + [1122] * 3 + [1082] * 1
)

# The real sample_submission/deck.csv composition (35x id 3 filler),
# verified 2026-08-10: {3: 35, 721: 2, 722: 4, 723: 4, 1145: 4, 1158: 1,
# 1205: 2, 1227: 4, 1235: 4}.
JUNK_DECK = (
    [3] * 35 + [721] * 2 + [722] * 4 + [723] * 4 + [1145] * 4
    + [1158] * 1 + [1205] * 2 + [1227] * 4 + [1235] * 4
)


def test_champion_deck_has_zero_flags():
    assert len(CHAMPION_DECK) == 60
    report = analyze_deck(CHAMPION_DECK)
    assert (report.pokemon_count, report.trainer_count,
            report.energy_count, report.basics_count) == (8, 32, 20, 4)
    assert report.mulligan_pct == pytest.approx(0.6005003742553344)
    assert report.flags == ()


def test_junk_sample_deck_triggers_energy_heavy_red():
    assert len(JUNK_DECK) == 60
    report = analyze_deck(JUNK_DECK)
    assert (report.pokemon_count, report.trainer_count,
            report.energy_count, report.basics_count) == (10, 15, 35, 6)
    assert report.mulligan_pct == pytest.approx(0.458563922158619)
    heavy = [f for f in report.flags if f.code == "energy-heavy"]
    assert heavy and heavy[0].severity == "red"


def test_evolution_break_red_when_pre_evo_missing():
    # Mega Starmie ex without any Staryu.
    deck = [1031] * 4 + [3] * 20 + [1121] * 36
    report = analyze_deck(deck)
    breaks = [f for f in report.flags if f.code == "evolution-break"]
    assert len(breaks) == 1 and breaks[0].severity == "red"
    assert "Staryu" in breaks[0].reason


def test_evolution_undersupply_amber():
    # 4 Mega Starmie ex on only 1 Staryu.
    deck = [1031] * 4 + [1030] * 1 + [3] * 20 + [1121] * 35
    report = analyze_deck(deck)
    under = [f for f in report.flags if f.code == "evolution-undersupply"]
    assert len(under) == 1 and under[0].severity == "amber"
    assert "evolution-break" not in _codes(report)


def test_safe_analyze_returns_none_on_bad_input():
    assert safe_analyze([99999999] * 60) is None      # unknown id -> KeyError inside
    assert safe_analyze(CHAMPION_DECK) is not None    # good deck -> report
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `uv run pytest tests/test_deck_quality.py -q`
Expected: `ImportError: cannot import name 'safe_analyze'`.

- [ ] **Step 3: Implement.** In `_analyze`, after the Task-3 block and before the report construction, add:

```python
    name_copies: Counter[str] = Counter()
    for cid, n in counts.items():
        name_copies[db[cid].name] += n
    for cid in sorted(counts):
        card = db[cid]
        if card.cardType != CardType.POKEMON or not (card.stage1 or card.stage2):
            continue
        pre = card.evolvesFrom
        pre_copies = name_copies.get(pre, 0) if pre is not None else 0
        if pre is None or pre_copies == 0:
            flags.append(Flag(
                "evolution-break", "red",
                f"{card.name} has no copies of its pre-evolution "
                f"{pre or '(unknown)'} in the deck"))
        elif pre_copies < counts[cid]:
            flags.append(Flag(
                "evolution-undersupply", "amber",
                f"{counts[cid]}x {card.name} on only {pre_copies}x {pre}"))
```

And at module level (after `analyze_deck`):

```python
def safe_analyze(card_ids: list[int]) -> DeckQualityReport | None:
    """Failure-isolated entry point for UI rendering: ANY exception (unknown
    card id, engine hiccup, bad input shape) returns None instead of raising.
    Pages render an "analysis unavailable" badge for None -- a broken
    analysis must never break a page (spec Section 1)."""
    try:
        return analyze_deck(list(card_ids))
    except Exception:
        return None
```

- [ ] **Step 4: Run to verify all pass**

Run: `uv run pytest tests/test_deck_quality.py -q`
Expected: all PASS. If `test_champion_deck_has_zero_flags` fails on a flag, STOP and report `plan-drift` with the flag's code+reason — the zero-flags result was prototype-verified against the real engine DB on 2026-08-10, so a failure means the implementation diverged from the plan's semantics, not that the fixture is wrong.

- [ ] **Step 5: Commit**

```bash
git add "src/ptcg/factory/deck_quality.py" "tests/test_deck_quality.py"
git commit -m "feat: deck_quality evolution flags, safe_analyze, golden deck fixtures"
```

---

### Task 5: `ui_pages` quality-rendering helpers + `_shell` flash seam

**Files:**
- Modify: `src/ptcg/factory/ui_pages.py` (`_shell` at :47-53; add two helpers after `_card_names` ~:71; extend imports)
- Test: `tests/test_factory_ui_pages.py` (append)

**Interfaces:**
- Consumes: `deck_quality.DeckQualityReport`, `deck_quality.Flag` (Tasks 2-4).
- Produces (Tasks 6-9 rely on these exact names):
  - `quality_summary_html(report: deck_quality.DeckQualityReport | None) -> str` — compact summary + badges, or the "analysis unavailable" badge for None.
  - `quality_reasons_html(report: deck_quality.DeckQualityReport | None) -> str` — `<ul class="flag-reasons">` of flag reasons, `""` when no flags/None.
  - `_shell(title: str, body: str, flash: str = "") -> str` — unchanged for existing callers (keyword default), injects `<p class="flash">` after the nav when non-empty.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_factory_ui_pages.py` (this file already imports `ui_pages`; follow its existing import style):

```python
# --- deck-quality rendering helpers (pool-pruning slice) -----------------
from ptcg.factory import deck_quality


def _report(flags=()):
    return deck_quality.DeckQualityReport(
        pokemon_count=8, trainer_count=32, energy_count=20, basics_count=4,
        mulligan_pct=0.6005003742553344, flags=tuple(flags),
    )


def test_quality_summary_html_counts_and_badges():
    rep = _report([deck_quality.Flag("energy-heavy", "red", "35 energy cards (> 30)"),
                   deck_quality.Flag("few-pokemon", "amber", "only 6 Pokemon (< 8)")])
    out = ui_pages.quality_summary_html(rep)
    assert "8P / 32T / 20E · basics 4 · mull 60%" in out
    assert '<span class="badge red">energy-heavy</span>' in out
    assert '<span class="badge amber">few-pokemon</span>' in out


def test_quality_summary_html_none_renders_unavailable():
    out = ui_pages.quality_summary_html(None)
    assert "analysis unavailable" in out


def test_quality_reasons_html_lists_reasons_or_empty():
    rep = _report([deck_quality.Flag("mulligan-risk", "red",
                                     "only 2 Basic Pokemon -- mulligan 78%")])
    out = ui_pages.quality_reasons_html(rep)
    assert "only 2 Basic Pokemon" in out and "flag-reasons" in out
    assert ui_pages.quality_reasons_html(_report()) == ""
    assert ui_pages.quality_reasons_html(None) == ""


def test_shell_flash_param_injects_banner_and_escapes():
    page = ui_pages._shell("T", "<p>b</p>", flash='Removed <x>')
    assert '<p class="flash">Removed &lt;x&gt;</p>' in page
    assert '<p class="flash">' not in ui_pages._shell("T", "<p>b</p>")
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `uv run pytest tests/test_factory_ui_pages.py -q`
Expected: the four new tests FAIL (`AttributeError: ... has no attribute 'quality_summary_html'` / `_shell() got an unexpected keyword argument 'flash'`); all pre-existing tests in the file still PASS.

- [ ] **Step 3: Implement.** In `src/ptcg/factory/ui_pages.py`:

Add to imports (top of file, after the existing `from cg.api import all_card_data`):

```python
from ptcg.factory import deck_quality
```

Replace `_shell` (currently :47-53) with:

```python
def _shell(title: str, body: str, flash: str = "") -> str:
    """Common `<!doctype html>...<nav>...` wrapper for every page. A non-empty
    `flash` renders a one-line success banner directly under the nav (PRG
    redirect query param -- no session state; spec Section 3)."""
    flash_html = f'<p class="flash">{html.escape(flash)}</p>' if flash else ""
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title></head>"
        f"<body>{NAV_HTML}{flash_html}{body}</body></html>"
    )
```

Add after `_card_names` (~:71):

```python
def quality_summary_html(report: deck_quality.DeckQualityReport | None) -> str:
    """Compact composition summary + severity badges for one deck row.
    None (analysis failed -- see deck_quality.safe_analyze) renders a single
    neutral badge instead of breaking the row (spec Section 1 failure
    isolation)."""
    if report is None:
        return '<span class="badge amber">analysis unavailable</span>'
    summary = (
        f"{report.pokemon_count}P / {report.trainer_count}T / "
        f"{report.energy_count}E · basics {report.basics_count} · "
        f"mull {round(report.mulligan_pct * 100)}%"
    )
    badges = "".join(
        f' <span class="badge {html.escape(f.severity)}">{html.escape(f.code)}</span>'
        for f in report.flags
    )
    return f'<span class="quality">{html.escape(summary)}</span>{badges}'


def quality_reasons_html(report: deck_quality.DeckQualityReport | None) -> str:
    """Full one-line reasons for each flag, for INSIDE a row's existing
    <details> element. Empty string when there is nothing to explain."""
    if report is None or not report.flags:
        return ""
    items = "".join(f"<li>{html.escape(f.reason)}</li>" for f in report.flags)
    return f'<ul class="flag-reasons">{items}</ul>'
```

- [ ] **Step 4: Run to verify all pass**

Run: `uv run pytest tests/test_factory_ui_pages.py -q`
Expected: all PASS (new + pre-existing).

- [ ] **Step 5: Commit**

```bash
git add "src/ptcg/factory/ui_pages.py" "tests/test_factory_ui_pages.py"
git commit -m "feat: quality summary/badge/reason renderers + flash seam in ui_pages"
```

---

### Task 6: Badges on bottom-10 review rows and `/pool` rows

**Files:**
- Modify: `src/ptcg/factory/ui_pages.py` (`render_review_page` :73-98, `render_pool_page` :134-183 — line numbers pre-Task-5; re-locate by function name)
- Test: `tests/test_factory_ui_pages.py` (append), and KEEP GREEN: `tests/test_factory_ui_server.py` (its `GET /` and `/pool` route tests assert substrings that remain present — run it to confirm)

**Interfaces:**
- Consumes: `quality_summary_html` / `quality_reasons_html` (Task 5), `deck_quality.safe_analyze` (Task 4). Review rows carry `row["cards"]` as an already-decoded `list[int]` (`bottom_ten` decodes it); pool rows carry `row["cards"]` as RAW JSON TEXT (decoded via `json.loads`, see the existing render_pool_page docstring).
- Produces: no new names — both renderers gain a summary line + badges in the `<summary>` area and flag reasons inside the `<details>` body.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_factory_ui_pages.py`. Use the champion/junk shapes so the assertions are deterministic (ids verified in Task 4):

```python
_JUNKISH = [3] * 35 + [721] * 2 + [722] * 4 + [723] * 4 + [1145] * 4 \
    + [1158] * 1 + [1205] * 2 + [1227] * 4 + [1235] * 4  # energy-heavy red


def test_review_page_rows_carry_quality_summary_and_reasons():
    rows = [{"deck_id": "d1", "concept_id": "c1", "rating": 1.0,
             "games_played": 20, "cards": list(_JUNKISH)}]
    page = ui_pages.render_review_page(rows)
    assert "10P / 15T / 35E · basics 6 · mull 46%" in page
    assert '<span class="badge red">energy-heavy</span>' in page
    assert "flag-reasons" in page  # reason text inside the details element


def test_pool_page_rows_carry_quality_summary():
    import json as _json
    rows = [{"concept_id": "c1", "deck_id": "d1", "rating": 1.0,
             "games_played": 20, "cards": _json.dumps(_JUNKISH)}]
    page = ui_pages.render_pool_page(rows, None, None)
    assert "10P / 15T / 35E · basics 6 · mull 46%" in page
    assert '<span class="badge red">energy-heavy</span>' in page


def test_pool_page_unanalyzable_row_gets_unavailable_badge_not_error():
    import json as _json
    rows = [{"concept_id": "c1", "deck_id": "d1", "rating": 1.0,
             "games_played": 20, "cards": _json.dumps([99999999] * 60)}]
    page = ui_pages.render_pool_page(rows, None, None)
    assert "analysis unavailable" in page
```

Note: `[99999999] * 60` renders names as `#99999999` via `_card_names`' fallback and `safe_analyze` returns None — the page must still render.

- [ ] **Step 2: Run to verify the new tests fail**

Run: `uv run pytest tests/test_factory_ui_pages.py -q`
Expected: the three new tests FAIL (no quality markup in output); pre-existing tests PASS.

- [ ] **Step 3: Implement.** In `render_review_page`'s row loop (after `names = ...`), add:

```python
            report = deck_quality.safe_analyze(row["cards"])
            quality = quality_summary_html(report)
            reasons = quality_reasons_html(report)
```

and change the item construction to include them — `<summary>` line gains ` — {quality}` after the games count, and `{reasons}` goes next to the names paragraph:

```python
            items.append(
                "<li>"
                f"<details><summary>{deck_id} — rating {html.escape(rating)} "
                f"({row['games_played']} games) — {quality}</summary>"
                f"<p>{names}</p>{reasons}</details>"
                '<form method="post" action="/decision">'
                f'<input type="hidden" name="deck_id" value="{deck_id}">'
                '<button name="action" value="remove">Remove</button>'
                '<button name="action" value="pass">Pass</button>'
                "</form>"
                "</li>"
            )
```

In `render_pool_page`'s row loop, after the `names = ...` line add (note: pool `cards` is JSON text — decode ONCE and reuse for both names and analysis):

```python
        card_ids = json.loads(row["cards"])
        names = ", ".join(html.escape(n) for n in _card_names(card_ids))
        report = deck_quality.safe_analyze(card_ids)
        quality = quality_summary_html(report)
        reasons = quality_reasons_html(report)
```

(replacing the existing single `names = ...` line that inlines `json.loads`), and include in the row item: summary gains ` — {quality}` after `{html.escape(status_text)}`, and `{reasons}` after `<p>{names}</p>`:

```python
        items.append(
            "<li>"
            f"<details><summary>{concept_id} ({deck_id}) — "
            f"{html.escape(status_text)} — {quality}</summary>"
            f"<p>{names}</p>{reasons}</details>{badge}"
            '<form method="post" action="/concept-decision">'
            f'<input type="hidden" name="concept_id" value="{concept_id}">'
            '<input type="hidden" name="from" value="pool">'
            '<button name="action" value="remove">Remove</button>'
            "</form>"
            "</li>"
        )
```

- [ ] **Step 4: Run BOTH named test files**

Run: `uv run pytest tests/test_factory_ui_pages.py tests/test_factory_ui_server.py -q`
Expected: all PASS. (Consumer sweep: `test_factory_ui_server.py`'s `GET /` and `/pool` tests assert substrings — `"d1"`, `"Basic {W} Energy"`, form counts — all additive-safe, but a 3-card seed deck `[3,3,3]` now ALSO renders quality markup; nothing in those tests asserts its absence. If any fails, fix the render, not the test.)

- [ ] **Step 5: Commit**

```bash
git add "src/ptcg/factory/ui_pages.py" "tests/test_factory_ui_pages.py"
git commit -m "feat: quality summary + badges on review and pool rows"
```

---

### Task 7: `/search` badges (canonical-deck lookup) + render the `reason` field

**Files:**
- Modify: `src/ptcg/factory/ui_actions.py` (add `canonical_decks` after `pool_decks` ~:56)
- Modify: `src/ptcg/factory/ui_pages.py` (`render_search_page` :185-270 pre-slice numbering)
- Modify: `src/ptcg/factory/ui_server.py` (`/search` GET route :349-360)
- Test: `tests/test_factory_ui_actions.py` (append), `tests/test_factory_ui_pages.py` (append), and KEEP GREEN: `tests/test_factory_ui_server.py` (`test_search_route_defaults_and_tab`)

**Interfaces:**
- Consumes: `deck_quality.safe_analyze`, `quality_summary_html` (Tasks 4-5); `search_concepts` rows already carry `reason` (`ui_actions.py:47`, SELECTed but currently dropped at render).
- Produces:
  - `ui_actions.canonical_decks(conn: sqlite3.Connection, concept_ids: list[str]) -> dict[str, list[int]]` — canonical (MIN `shell_variant`, matching `_POOL_QUERY`'s convention at `ui_actions.py:23-24`) deck card-ids per concept; concepts with no built deck are absent from the dict.
  - `render_search_page(rows, total, q, tab, *, decks: dict[str, list[int]] | None = None)` — rows whose `concept_id` is in `decks` get the quality summary; rows without stay unchanged (spec Section 2). Also renders each row's `reason` when non-empty.

**Access-path note (`.claude/rules/single-actor-worker-tests.md` access-path clause):** the new SELECT is the slice's ONLY new SQL. EQP verified 2026-08-10 against the REAL production DB (95,907 `decks` rows): `SEARCH decks USING INDEX ix_decks_concept (concept_id=?)` — indexed, read-only, outside any write transaction, ≤ `SEARCH_ROW_CAP` (200) ids per call, zero lock-hold impact. The test below pins the EQP so it cannot silently regress to a SCAN.

- [ ] **Step 1: Write the failing tests.**

Append to `tests/test_factory_ui_actions.py` (follow the file's existing seeding helpers/import style — it already has an EQP-pinning precedent, `test_decisions_concept_index_exists_and_is_used`):

```python
# --- canonical_decks (pool-pruning slice) --------------------------------


def test_canonical_decks_returns_min_shell_variant_per_concept(tmp_path):
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)

    def _seed(c):
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cA','[]','active')")
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cB','[]','untested')")
        c.execute("INSERT INTO concepts(id,cores,status) VALUES('cNoDeck','[]','untested')")
        c.execute("INSERT INTO decks(id,concept_id,cards,shell_variant) "
                  "VALUES('dA1','cA','[1, 2]',1)")
        c.execute("INSERT INTO decks(id,concept_id,cards,shell_variant) "
                  "VALUES('dA0','cA','[3, 4]',0)")   # canonical: min shell_variant
        c.execute("INSERT INTO decks(id,concept_id,cards,shell_variant) "
                  "VALUES('dB0','cB','[5]',0)")

    deckdb._write(db, _seed)

    out = ui_actions.canonical_decks(db, ["cA", "cB", "cNoDeck", "cMissing"])
    assert out == {"cA": [3, 4], "cB": [5]}
    assert ui_actions.canonical_decks(db, []) == {}


def test_canonical_decks_query_uses_concept_index(tmp_path):
    db = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(db)
    plan_rows = db.execute(
        "EXPLAIN QUERY PLAN SELECT concept_id, cards, shell_variant FROM decks "
        "WHERE concept_id IN (?,?) ORDER BY concept_id ASC, shell_variant ASC",
        ("a", "b"),
    ).fetchall()
    detail = " ".join(str(tuple(r)) for r in plan_rows)
    assert "ix_decks_concept" in detail  # indexed lookup, never a table SCAN
```

Append to `tests/test_factory_ui_pages.py`:

```python
def test_search_rows_with_canonical_deck_get_badges_others_unchanged():
    rows = [
        {"concept_id": "cHas", "cores": "x", "status": "untested",
         "reason": None, "rating": None, "games_played": None},
        {"concept_id": "cNone", "cores": "y", "status": "untested",
         "reason": None, "rating": None, "games_played": None},
    ]
    page = ui_pages.render_search_page(
        rows, 2, "c", "untested", decks={"cHas": list(_JUNKISH)})
    assert page.count('<span class="badge red">energy-heavy</span>') == 1
    assert "10P / 15T / 35E" in page
    # decks omitted entirely -> no quality markup at all (back-compat).
    page2 = ui_pages.render_search_page(rows, 2, "c", "untested")
    assert "badge red" not in page2


def test_search_rows_render_reason_when_present():
    rows = [{"concept_id": "c1", "cores": "x", "status": "culled",
             "reason": "ui-cull: 2026-08-09T00:00:00+00:00",
             "rating": None, "games_played": None}]
    page = ui_pages.render_search_page(rows, 1, "c", "culled")
    assert "ui-cull: 2026-08-09" in page
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `uv run pytest tests/test_factory_ui_actions.py tests/test_factory_ui_pages.py -q`
Expected: `AttributeError: module ... has no attribute 'canonical_decks'`; the pages tests FAIL on missing markup / unexpected-keyword `decks`. (`test_canonical_decks_query_uses_concept_index` may PASS already — the index exists; that is fine, it is a pin.)

- [ ] **Step 3: Implement.**

`src/ptcg/factory/ui_actions.py`, after `pool_decks`:

```python
def canonical_decks(
    conn: sqlite3.Connection, concept_ids: list[str]
) -> dict[str, list[int]]:
    """Canonical (MIN shell_variant, same convention as _POOL_QUERY) deck
    card-ids for the given concepts. Concepts with no built deck are absent.

    Access path (pinned by test): indexed via ix_decks_concept, read-only,
    outside any write transaction; callers pass at most SEARCH_ROW_CAP (200)
    ids, so the IN list stays far under SQLite's default 999-variable limit.
    """
    if not concept_ids:
        return {}
    placeholders = ",".join("?" * len(concept_ids))
    rows = conn.execute(
        f"SELECT concept_id, cards, shell_variant FROM decks "
        f"WHERE concept_id IN ({placeholders}) "
        "ORDER BY concept_id ASC, shell_variant ASC",
        concept_ids,
    ).fetchall()
    out: dict[str, list[int]] = {}
    for r in rows:
        cid = r["concept_id"]
        if cid not in out:  # first row per concept == MIN shell_variant
            out[cid] = json.loads(r["cards"])
    return out
```

(`ui_actions.py` already imports `json` for its own use — if not, add `import json` to its stdlib imports.)

`src/ptcg/factory/ui_pages.py` — change `render_search_page`'s signature to:

```python
def render_search_page(
    rows: list[dict], total: int, q: str, tab: str,
    *, decks: dict[str, list[int]] | None = None,
) -> str:
```

and in its row loop, before `row_items.append`, add:

```python
        quality = ""
        if decks is not None and row["concept_id"] in decks:
            report = deck_quality.safe_analyze(decks[row["concept_id"]])
            quality = " — " + quality_summary_html(report)
        reason = row.get("reason")
        reason_html = (
            f' <span class="reason">{html.escape(str(reason))}</span>'
            if reason else ""
        )
```

and change the row `<span>` to include both:

```python
            f"<span>{concept_id} — {cores} — {html.escape(status)} — "
            f"{html.escape(rating_text)}{quality}{reason_html}</span>"
```

`src/ptcg/factory/ui_server.py` — in the `/search` GET route, fetch decks inside the SAME conn block and pass through:

```python
                conn = conn_factory()
                try:
                    rows, total = ui_actions.search_concepts(conn, q, tab)
                    decks = ui_actions.canonical_decks(
                        conn, [r["concept_id"] for r in rows])
                except ValueError as exc:
                    self._send_html(
                        400, f"<h1>400 Bad Request</h1><p>{html.escape(str(exc))}</p>"
                    )
                    return
                finally:
                    conn.close()
                self._send_html(
                    200, ui_pages.render_search_page(rows, total, q, tab, decks=decks))
```

- [ ] **Step 4: Run the three named test files**

Run: `uv run pytest tests/test_factory_ui_actions.py tests/test_factory_ui_pages.py tests/test_factory_ui_server.py -q`
Expected: all PASS (server file is the consumer sweep for the route change — its `/search` test seeds concepts WITH decks, so rows now render quality markup; its substring assertions are additive-safe).

- [ ] **Step 5: Commit**

```bash
git add "src/ptcg/factory/ui_actions.py" "src/ptcg/factory/ui_pages.py" "src/ptcg/factory/ui_server.py" "tests/test_factory_ui_actions.py" "tests/test_factory_ui_pages.py"
git commit -m "feat: quality badges + reason text on /search rows via canonical-deck lookup"
```

---

### Task 8: `/pool?problems=1` filter + toggle link

**Files:**
- Modify: `src/ptcg/factory/ui_server.py` (`/pool` GET route :338-346 pre-slice numbering)
- Modify: `src/ptcg/factory/ui_pages.py` (`render_pool_page` signature + toggle link)
- Test: `tests/test_factory_ui_server.py` (append), `tests/test_factory_ui_pages.py` (append)

**Interfaces:**
- Consumes: `deck_quality.safe_analyze` (Task 4), Task 6's pool rendering.
- Produces: `render_pool_page(rows, anchor, champion_concept_id, *, problems_only: bool = False)` — renders a toggle link (`/pool?problems=1` when showing all, `/pool` when filtered). Filtering itself happens in `ui_server` (Python, post-fetch, AFTER `conn.close()` — zero lock impact; spec Section 2). Rows whose analysis fails (`safe_analyze` → None) are INCLUDED in the problems view: an unanalyzable deck cannot be proven clean, and hiding it from a problems review would be a silent failure.

- [ ] **Step 1: Write the failing tests.**

Append to `tests/test_factory_ui_pages.py`:

```python
def test_pool_page_toggle_link_both_directions():
    page_all = ui_pages.render_pool_page([], None, None)
    assert 'href="/pool?problems=1"' in page_all
    page_filtered = ui_pages.render_pool_page([], None, None, problems_only=True)
    assert 'href="/pool"' in page_filtered
```

Append to `tests/test_factory_ui_server.py` (reuse its `_seed_concept`/`_start_server` helpers; seed one clean-ish deck and one junk deck — `_seed_concept` takes a `cards` kwarg):

```python
_JUNK_CARDS = [3] * 35 + [721] * 2 + [722] * 4 + [723] * 4 + [1145] * 4 \
    + [1158] * 1 + [1205] * 2 + [1227] * 4 + [1235] * 4  # energy-heavy red

_CLEAN_CARDS = (
    [1031] * 4 + [1030] * 4 + [3] * 20 + [1121] * 4 + [1102] * 4
    + [1086] * 4 + [1224] * 4 + [1213] * 4 + [1182] * 4 + [1097] * 4
    + [1122] * 3 + [1082] * 1
)  # champion fixture: zero flags (verified in the plan)


def test_pool_problems_filter_shows_only_flagged(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)

    def _seed(c):
        _seed_concept(c, "cClean", "dClean", cards=list(_CLEAN_CARDS))
        _seed_concept(c, "cJunk", "dJunk", cards=list(_JUNK_CARDS))

    deckdb._write(db, _seed)
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/pool", timeout=5) as r:
            all_body = r.read().decode("utf-8")
        assert "cClean" in all_body and "cJunk" in all_body
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/pool?problems=1", timeout=5
        ) as r:
            filtered = r.read().decode("utf-8")
        assert "cJunk" in filtered
        assert "cClean" not in filtered
    finally:
        _stop_server(httpd, thread)
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `uv run pytest tests/test_factory_ui_pages.py tests/test_factory_ui_server.py -q`
Expected: toggle-link test FAILS (`unexpected keyword argument 'problems_only'`); filter test FAILS (`cClean` still present under `?problems=1`).

- [ ] **Step 3: Implement.**

`render_pool_page`: change signature to

```python
def render_pool_page(
    rows: list[dict], anchor: dict | None, champion_concept_id: str | None,
    *, problems_only: bool = False,
) -> str:
```

and change the final return to include the toggle immediately after the `<h1>`:

```python
    toggle = (
        '<p><a href="/pool">show all</a></p>'
        if problems_only
        else '<p><a href="/pool?problems=1">problems only</a></p>'
    )
    return _shell("Pool", f"<h1>Pool</h1>{toggle}{body}")
```

`/pool` GET route in `ui_server.py` (replace the existing branch; note the filter runs AFTER `conn.close()`):

```python
            elif parsed.path == "/pool":
                qs = urllib.parse.parse_qs(parsed.query)
                problems_only = qs.get("problems", ["0"])[0] == "1"
                conn = conn_factory()
                try:
                    rows = ui_actions.pool_decks(conn)
                    anchor = ui_actions.anchor_concept(conn)
                    champ = ui_actions.champion_concept_id(conn)
                finally:
                    conn.close()
                if problems_only:
                    # Post-fetch, zero lock impact. None (analysis failed)
                    # counts as a problem: it cannot be proven clean.
                    rows = [
                        r for r in rows
                        if (rep := deck_quality.safe_analyze(
                            json.loads(r["cards"]))) is None or rep.flags
                    ]
                page = ui_pages.render_pool_page(
                    rows, anchor, champ, problems_only=problems_only)
                self._send_html(200, page)
```

`ui_server.py` will need `import json` and `from ptcg.factory import deck_quality` added to its imports if not already present (check the file's existing import block and follow its style). Row order is preserved by the comprehension — sort stays rating DESC from `_POOL_QUERY`.

- [ ] **Step 4: Run the two named test files**

Run: `uv run pytest tests/test_factory_ui_pages.py tests/test_factory_ui_server.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add "src/ptcg/factory/ui_server.py" "src/ptcg/factory/ui_pages.py" "tests/test_factory_ui_server.py" "tests/test_factory_ui_pages.py"
git commit -m "feat: /pool problems-only filter (post-fetch, zero lock impact)"
```

---

### Task 9: Flash banners (PRG) on all decision paths; bulk goes redirect-based

**Files:**
- Modify: `src/ptcg/factory/ui_server.py` (GET `/` :324, `/pool` (as rewritten in Task 8), `/search` :349; POST `/decision` :374, `/concept-decision` :400, `/bulk-decision` :463 — pre-slice numbering, re-locate by path string)
- Modify: `src/ptcg/factory/ui_pages.py` (`render_review_page`, `render_pool_page`, `render_search_page` gain `flash`; DELETE `render_bulk_result_page` :290)
- Test: `tests/test_factory_ui_server.py` (append + update 3 named tests), `tests/test_factory_ui_pages.py` (update 2 named tests)

**Interfaces:**
- Consumes: `_shell(..., flash=...)` (Task 5).
- Produces: `render_review_page(rows, *, flash: str = "")`, `render_pool_page(..., problems_only: bool = False, flash: str = "")`, `render_search_page(..., decks=None, flash: str = "")` — each passes `flash` through to `_shell`. POST success paths 303-redirect with a `flash` query param (PRG, no session state). `/bulk-decision` switches from a 200 result page to a 303 redirect back to `/search` with the count in the flash text.

**Existing tests this task MUST update (consumer sweep, assertions preserved not dropped — `.claude/rules/test-coverage-sweep.md`):**
- `tests/test_factory_ui_server.py::test_post_decision_applies_and_redirects` (:325) — Location gains `?flash=...`; keep the path assertion, add a flash-key assertion.
- `tests/test_factory_ui_server.py::test_concept_decision_remove_redirects_to_origin` (:484) — same.
- `tests/test_factory_ui_server.py::test_bulk_decision_executes_and_reports_actual` (:583) — currently asserts `status == 200` and `"3" in body`; becomes: redirect followed → final body contains `"Removed 3 concepts"`; the DB-status assertions stay VERBATIM.
- `tests/test_factory_ui_pages.py::test_bulk_confirm_and_result_pages` (:79) and `::test_bulk_confirm_and_result_hrefs_use_status_key_not_tab` (:86) — drop ONLY the `render_bulk_result_page` halves (the function is deleted); the confirm-page halves stay verbatim. The dropped assertions' intents ("N affected" surfaced to the operator; `status=` not `tab=` key in the return link) are preserved by the new redirect test below (flash carries N; redirect Location uses `status=`).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_factory_ui_server.py`:

```python
def _location_of(port: int, path: str, data: dict) -> str:
    """POST without following the redirect; return the Location header."""
    body = urllib.parse.urlencode(data).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST", path, body,
                     {"Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        assert resp.status == 303, f"expected 303, got {resp.status}"
        return resp.getheader("Location") or ""
    finally:
        conn.close()


def test_decision_redirect_carries_flash_and_page_renders_banner(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    deckdb._write(db, lambda c: _seed_concept(c, "c1", "d1"))
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        loc = _location_of(port, "/decision", {"deck_id": "d1", "action": "pass"})
        split = urllib.parse.urlsplit(loc)
        assert split.path == "/"
        flash = urllib.parse.parse_qs(split.query)["flash"][0]
        assert flash == "Passed d1"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{loc}", timeout=5) as r:
            body = r.read().decode("utf-8")
        assert '<p class="flash">Passed d1</p>' in body
    finally:
        _stop_server(httpd, thread)


def test_bulk_decision_redirects_to_search_with_count_flash(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)

    def _seed(c):
        for i in range(3):
            _seed_concept(c, f"fq{i}", f"dfq{i}", status="untested")

    deckdb._write(db, _seed)
    db.close()

    httpd, thread = _start_server(lambda: deckdb.connect(db_path))
    try:
        port = httpd.server_address[1]
        loc = _location_of(port, "/bulk-decision",
                           {"q": "fq", "tab": "untested", "action": "bulk-remove"})
        split = urllib.parse.urlsplit(loc)
        assert split.path == "/search"
        qs = urllib.parse.parse_qs(split.query)
        assert qs["q"] == ["fq"]
        assert qs["status"] == ["untested"]     # status= key, never tab=
        assert "tab" not in qs
        assert qs["flash"] == ["Removed 3 concepts"]
    finally:
        _stop_server(httpd, thread)
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `uv run pytest tests/test_factory_ui_server.py -q`
Expected: the two new tests FAIL (`/decision` Location is bare `/` with no flash; `/bulk-decision` returns 200, so `_location_of` asserts on the 303). Pre-existing tests still pass at this point.

- [ ] **Step 3: Implement.**

`ui_pages.py`: add `flash: str = ""` keyword param to `render_review_page`, `render_pool_page`, `render_search_page`, passing it to their `_shell(...)` call (e.g. `_shell("Pool", f"<h1>Pool</h1>{toggle}{body}", flash=flash)`). DELETE `render_bulk_result_page` entirely.

`ui_server.py` GET routes — extract flash and pass through:

```python
            if parsed.path == "/":
                qs = urllib.parse.parse_qs(parsed.query)
                flash = qs.get("flash", [""])[0]
                conn = conn_factory()
                try:
                    rows = bottom_ten(conn)
                finally:
                    conn.close()
                self._send_html(200, _render_page(rows, flash=flash))
```

`/pool`: add `flash = qs.get("flash", [""])[0]` next to the existing `problems_only` extraction (Task 8) and pass `flash=flash` to `render_pool_page`. `/search`: add `flash = qs.get("flash", [""])[0]` next to the `q`/`tab` extraction and pass `flash=flash` to `render_search_page`.

POST `/decision` success tail:

```python
                verb = "Removed" if action == "remove" else "Passed"
                self.send_response(303)
                self.send_header(
                    "Location",
                    "/?" + urllib.parse.urlencode({"flash": f"{verb} {deck_id}"}))
                self.end_headers()
```

POST `/concept-decision` success tail:

```python
                verb = "Removed" if action == "remove" else "Restored"
                flash_msg = f"{verb} {concept_id}"
                if origin == "search":
                    location = "/search?" + urllib.parse.urlencode(
                        {"q": q, "status": status, "flash": flash_msg})
                else:
                    location = "/pool?" + urllib.parse.urlencode(
                        {"flash": flash_msg})
                self.send_response(303)
                self.send_header("Location", location)
                self.end_headers()
```

POST `/bulk-decision` success tail (replacing the `render_bulk_result_page` call):

```python
                verb = "Removed" if action == "bulk-remove" else "Restored"
                location = "/search?" + urllib.parse.urlencode(
                    {"q": q, "status": tab, "flash": f"{verb} {n} concepts"})
                self.send_response(303)
                self.send_header("Location", location)
                self.end_headers()
```

- [ ] **Step 4: Update the five named existing tests** (listed under Interfaces above) to the new contract: Location assertions parse via `urllib.parse.urlsplit` + `parse_qs` and assert path + flash key; the bulk server test follows the redirect (its `_post` helper follows 303s via urllib) and asserts `"Removed 3 concepts"` in the final body while keeping its DB-status assertions verbatim; the two ui_pages bulk tests keep only their confirm-page halves.

- [ ] **Step 5: Run both named test files**

Run: `uv run pytest tests/test_factory_ui_server.py tests/test_factory_ui_pages.py -q`
Expected: all PASS, including the updated five.

- [ ] **Step 6: Commit**

```bash
git add "src/ptcg/factory/ui_server.py" "src/ptcg/factory/ui_pages.py" "tests/test_factory_ui_server.py" "tests/test_factory_ui_pages.py"
git commit -m "feat: PRG flash banners on decision paths; bulk-decision redirects with count"
```

---

### Task 10: `/status` HST timestamp + bulk-button disabled affordance

**Files:**
- Modify: `src/ptcg/factory/ui_pages.py` (`render_status_page` — the `(as of ...)` line, originally :128-129; `render_search_page`'s bulk form)
- Test: `tests/test_factory_ui_pages.py` (append), and KEEP GREEN: `tests/test_factory_ui_dashboard.py` (its `/status` route tests assert counts/sections, not the timestamp format — run to confirm)

**Interfaces:**
- Consumes: `status["now"]` is a UTC ISO-8601 string (`now.isoformat()` from `ui_server.status_snapshot`, seam at `ui_server.py:286`).
- Produces: module constant `_HST = dt.timezone(dt.timedelta(hours=-10), "HST")` and helper `_hst_line(now_iso: str) -> str` in `ui_pages.py`; the bulk submit button gains `id="bulk-btn"` + a `disabled` attribute when `q` is empty, plus a tiny inline script toggling it on input (server-side rejection in `/bulk-confirm` stays — this is affordance only, spec Section 3).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_factory_ui_pages.py`:

```python
def _status_fixture(now_iso):
    return {
        "paused": False, "submit_hold": False,
        "submission_counter": {"used": 0, "cap": 5},
        "auth": {"state": "ok", "detail": ""},
        "baseline_version": "v0.14",
        "offspring_counts": {}, 
        "census": {"singles_played": 0, "singles_total": 0,
                   "pairs_activated": 0, "pairs_total": 0},
        "games_last_hour": 7, "now": now_iso,
    }


def test_status_page_renders_hst_alongside_utc():
    # 01:42 UTC == 3:42 PM HST (previous day) -- fixed UTC-10, no DST.
    page = ui_pages.render_status_page(
        _status_fixture("2026-08-10T01:42:00+00:00"))
    assert "as of 3:42 PM HST · 01:42 UTC" in page


def test_bulk_button_disabled_when_query_empty():
    page = ui_pages.render_search_page([], 0, "", "culled")
    assert 'id="bulk-btn" disabled' in page
    page2 = ui_pages.render_search_page([], 3, "abc", "culled")
    assert 'id="bulk-btn">' in page2 and 'id="bulk-btn" disabled' not in page2
    assert "addEventListener" in page2  # inline affordance script present
```

Note: check `_status_fixture` against the REAL `render_status_page` field accesses before running — if `status_snapshot` has gained/renamed fields since plan time, fix the fixture to match the renderer, and report `plan-drift`.

- [ ] **Step 2: Run to verify the new tests fail**

Run: `uv run pytest tests/test_factory_ui_pages.py -q`
Expected: HST test FAILS (page shows the raw ISO string); bulk test FAILS (no `bulk-btn` id).

- [ ] **Step 3: Implement.**

`ui_pages.py` imports gain `import datetime as dt`. Add near the top (after `_SEARCH_TABS`):

```python
#: Hawaii observes no DST, so a fixed UTC-10 offset is permanently exact.
#: zoneinfo("Pacific/Honolulu") is unavailable on this host (no tzdata) --
#: spec Section 3 as amended 2026-08-10.
_HST = dt.timezone(dt.timedelta(hours=-10), "HST")


def _hst_line(now_iso: str) -> str:
    """'as of 3:42 PM HST · 01:42 UTC' from a UTC ISO-8601 timestamp."""
    now = dt.datetime.fromisoformat(now_iso)
    hst = now.astimezone(_HST)
    utc = now.astimezone(dt.timezone.utc)
    hst_text = hst.strftime("%I:%M %p").lstrip("0")
    return f"as of {hst_text} HST · {utc.strftime('%H:%M')} UTC"
```

In `render_status_page`, replace the throughput paragraph's `(as of {html.escape(status['now'])})` with `({html.escape(_hst_line(status['now']))})`.

In `render_search_page`'s `bulk_html` block, change the button line to:

```python
        disabled_attr = "" if q.strip() else " disabled"
        ...
            f'<button type="submit" id="bulk-btn"{disabled_attr}>'
            f"{html.escape(label)}</button>"
```

and append after the bulk form (still inside the `bulk_action is not None` branch):

```python
        bulk_html += (
            "<script>(function () {"
            'var q = document.querySelector(\'form[action="/search"] input[name="q"]\');'
            'var b = document.getElementById("bulk-btn");'
            "if (q && b) q.addEventListener('input', function () {"
            "b.disabled = !q.value.trim(); });"
            "})();</script>"
        )
```

- [ ] **Step 4: Run the two named test files**

Run: `uv run pytest tests/test_factory_ui_pages.py tests/test_factory_ui_dashboard.py -q`
Expected: all PASS (dashboard file is the consumer sweep for the `/status` render change).

- [ ] **Step 5: Commit**

```bash
git add "src/ptcg/factory/ui_pages.py" "tests/test_factory_ui_pages.py"
git commit -m "feat: HST status timestamp (fixed UTC-10) + bulk-button disabled affordance"
```

---

### Task 11: `ThreadingHTTPServer` swap + two-thread concurrency receipt

**Files:**
- Modify: `scripts/factory_ui.py:34` (import) and `:71` (instantiation)
- Create: `tests/test_factory_ui_threading.py`

**Interfaces:**
- Consumes: `ui_server.make_app` (fresh conn per request — the handler holds NO cross-request state, verified statelessness note at `ui_server.py:288-291`; the single-instance lock in `run_server` is process-level and unaffected).
- Produces: `scripts/factory_ui.py` imports and serves `ThreadingHTTPServer`. Note: stdlib `http.server.ThreadingHTTPServer` sets `daemon_threads = True` as a CLASS attribute (Python ≥3.7), satisfying the spec's "use daemon threads so shutdown is not blocked" without extra code — assert it in the test rather than re-setting it.

- [ ] **Step 1: Write the failing test** — `tests/test_factory_ui_threading.py`:

```python
"""Concurrency receipt for the pool-pruning slice's ThreadingHTTPServer swap.

The import below is the RED driver: scripts/factory_ui.py currently imports
HTTPServer (single-threaded), so importing ThreadingHTTPServer FROM IT fails
until the swap lands. The behavioral test then proves a slow request no
longer blocks a concurrent GET -- with a genuine overlap receipt (the fast
request completes WHILE the slow one is provably held open by an Event
gate), per the barrier+overlap discipline in
.claude/rules/single-actor-worker-tests.md.
"""
from __future__ import annotations

import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

from ptcg.factory import deckdb, ui_server

# RED until scripts/factory_ui.py:34 swaps its import (this is the pin that
# the PRODUCTION entrypoint -- not just this test -- serves threaded).
from scripts.factory_ui import ThreadingHTTPServer as ProductionServerClass


def test_production_server_class_is_threading_and_daemon():
    assert ProductionServerClass is ThreadingHTTPServer
    assert ThreadingHTTPServer.daemon_threads is True  # stdlib class default


def test_slow_request_does_not_block_concurrent_get(tmp_path):
    db_path = tmp_path / "t.db"
    db = deckdb.connect(db_path)
    deckdb.init_db(db)
    db.close()

    slow_started = threading.Event()   # slow request is genuinely in-flight
    release_slow = threading.Event()   # gate holding the slow request open
    first_call = threading.Lock()
    seen_first = []

    def conn_factory():
        with first_call:
            if not seen_first:
                seen_first.append(1)
                slow_started.set()
                release_slow.wait(timeout=10)  # hold ONLY the first request
        return deckdb.connect(db_path)

    handler = ui_server.make_app(conn_factory)
    httpd = ProductionServerClass(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    slow_result: dict = {}
    try:
        port = httpd.server_address[1]

        def _slow():
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/", timeout=30
            ) as r:
                slow_result["status"] = r.status

        slow_thread = threading.Thread(target=_slow, daemon=True)
        slow_thread.start()
        assert slow_started.wait(timeout=5)

        # Overlap receipt: this GET completes while the slow request is
        # still held open (release_slow not yet set). Under the old
        # single-threaded HTTPServer this urlopen times out instead.
        # /pool is DB-only (no repo-root filesystem reads, unlike /status),
        # so the test has no environmental dependency beyond tmp_path.
        start = time.monotonic()
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/pool", timeout=5
        ) as r:
            assert r.status == 200
        assert time.monotonic() - start < 4.0
        assert not release_slow.is_set()  # the overlap was real

        release_slow.set()
        slow_thread.join(timeout=10)
        assert slow_result.get("status") == 200  # slow request also finished
    finally:
        release_slow.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_factory_ui_threading.py -q`
Expected: collection error `ImportError: cannot import name 'ThreadingHTTPServer' from 'scripts.factory_ui'`.

- [ ] **Step 3: Implement.** In `scripts/factory_ui.py`:

Line 34: `from http.server import HTTPServer` → `from http.server import ThreadingHTTPServer`.
Line 71: `httpd = HTTPServer(("127.0.0.1", port), handler)` → `httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)`.
Also update the module docstring's serving sentence to mention threaded serving (one line, e.g. append: `Serves via ThreadingHTTPServer (daemon threads) since the pool-pruning slice so a slow DB write no longer freezes concurrent page loads.`).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_factory_ui_threading.py -q`
Expected: both tests PASS. Optional RED-receipt spot check (recommended, 30 seconds): temporarily change the test's `ProductionServerClass(...)` instantiation to `HTTPServer(...)` (import it locally) and confirm `test_slow_request_does_not_block_concurrent_get` FAILS with a timeout on the `/pool` urlopen — proving the test discriminates — then revert. Do NOT leave the reverted form in place; do not run any other suite files while this deliberate-fail check is in flight.

- [ ] **Step 5: Commit**

```bash
git add "scripts/factory_ui.py" "tests/test_factory_ui_threading.py"
git commit -m "feat: ThreadingHTTPServer for factory UI + concurrency overlap receipt"
```

---

## Go-live (post-merge rung — explicit, per repo convention)

All slice code is inert until the long-lived `ptcg-factory-ui` worker restarts (it does not hot-reload; `.claude/rules/factory-resume-probe.md`). No UAC needed — stop/start of an existing task is non-elevated.

**Pre-flight (`.claude/rules/golive-command-preflight.md`): at Finish, re-verify these command lines against the real task names via `Get-ScheduledTask -TaskName ptcg-factory-ui` before running them — do not trust this plan text blindly.**

1. Merge to master; confirm ladder identity files untouched (`git diff master...HEAD -- src/ptcg/submission_main.py src/ptcg/agents/current.py` → empty).
2. Restart the UI worker (non-elevated PowerShell):
   ```powershell
   Stop-ScheduledTask -TaskName ptcg-factory-ui
   Start-ScheduledTask -TaskName ptcg-factory-ui
   Get-ScheduledTask -TaskName ptcg-factory-ui | Select-Object TaskName, State
   ```
   Expect `Ready`/`Running`. Note the watchdog-respawn rule: stop alone is not durable, but here we WANT it running again immediately, so stop→start is correct.
3. Rung-3 manual browser smoke against `http://127.0.0.1:8765` (live server, real DB, read-mostly):
   - `/pool`: quality summary + badges visible on rows; `problems only` toggle filters; sort still rating DESC.
   - `/`: bottom-10 rows show summary/badges; flag reasons inside `<details>`.
   - A harmless `Pass` action on `/` → 303 → flash banner "Passed <deck_id>" renders.
   - `/search?q=<champion substring>&status=all`: badges on rows with decks; `reason` visible on culled rows; bulk button disabled with empty `q`.
   - `/status`: "as of H:MM AM/PM HST · HH:MM UTC" line correct against a clock.
   - Concurrency: open `/pool` in one tab, immediately click a (slow, lock-contended) cull in another; the first tab must remain responsive during the write.
4. Record rung results in plan.md before Finish closes.

## Pre-lock verification appendix (executed 2026-08-10 by the planner)

**1. Landmark grep** — every symbol/path in the Verified Landmark Table was grepped/`sed`-read in the working tree this session. Drift found and fixed in plan text:
- Project CLAUDE.md's "34x card ID 3" sample-deck description vs the REAL `deck.csv`: **35x** (full composition `{3:35, 721:2, 722:4, 723:4, 1145:4, 1158:1, 1205:2, 1227:4, 1235:4}`, pk=10/tr=15/en=35/basics=6). Plan uses the real 35x composition.
- Spec's `zoneinfo("Pacific/Honolulu")`: raises `ZoneInfoNotFoundError` on this host (`ModuleNotFoundError: No module named 'tzdata'` underneath) — Task 1 amends to fixed UTC-10 (receipt: `dt.timezone(dt.timedelta(hours=-10), "HST")` renders `2026-08-10T01:42Z` → `03:42 PM HST`, lstrip → `3:42 PM`).
- Spec's "engine DLL must NOT be loaded into the UI process": moot, DLL already loaded (`ui_pages.py:26`, `_card_id_to_name` :56-62) — Task 1 amends.
- All cited line anchors confirmed exact: `factory_ui.py:34/:71`; ui_server routes :324/:331/:338/:349/:374/:400/:448/:463, make_app :282, now seam :286; ui_pages renderers :73/:100/:134/:185/:272/:290, `status['now']` at :129; `ui_actions.py:47` reason; `_POOL_QUERY` :19-28 (canonical = MIN shell_variant); analysis.py :52-64; validate.py :15-19; api.py CardData :463-483 / Attack :484-491.

**2. Consumer-test sweep** (`rg -l "ui_pages|ui_server|ui_actions|factory_ui" tests/`): `test_factory_ui_server.py`, `test_factory_ui_pages.py`, `test_factory_ui_actions.py`, `test_factory_ui_dashboard.py`, `test_factory_tournament_phase1.py`, `test_register_tournament_tasks.py`, `test_factory_lock_access_paths.py`. Affected files are named INSIDE the tasks that touch their subject: Task 6 (pages+server), Task 7 (actions+pages+server), Task 8 (server+pages), Task 9 (server+pages, incl. the 5 tests that must be UPDATED, named individually), Task 10 (pages+dashboard), Task 11 (new file only). `test_factory_tournament_phase1.py` / `test_register_tournament_tasks.py` / `test_factory_lock_access_paths.py` are unaffected (no deckdb/registration/lock-held-query changes; `test_register_tournament_tasks.py:51` only asserts the literal string "factory_ui.py", which is unchanged).

**3. Existing-test shape check**: `tests/test_factory_ui_server.py:27-52` (`_seed_concept` with `cards` kwarg, `_seeded_db`) and `:273-284` (`_start_server`/`_stop_server` ephemeral-port daemon-thread pattern) read in full; the plan's test code copies these patterns verbatim-compatibly.

**4. Concurrency sketch walk** (`single-actor-worker-tests.md` against the plan's own sketches): the slice adds NO write path, no read-decide-act sequence, no `BEGIN IMMEDIATE`; the only new SQL is `canonical_decks`' single indexed read (EQP receipt: `SEARCH decks USING INDEX ix_decks_concept (concept_id=?)` against the real 95,907-row production DB, read-only URI mode), pinned by a test. `deckdb.py` untouched. The problems filter and all analysis run post-fetch after `conn.close()`. Task 11's concurrency test carries the barrier+overlap receipt shape required by the rule.

**5. Champion deck fixture**: fetched read-only via `sqlite3.connect('file:experiments/factory/tournament.db?mode=ro', uri=True)`, query `SELECT cards FROM decks WHERE id='reseed-mega-starmie-water-density20-sv0'`. The 60 ids are embedded in Task 4's `CHAMPION_DECK`. The full flag pipeline (ratios, basics, dead-energy, unpayable, evolution) was PROTOTYPE-EXECUTED against these ids + the real engine DB: pk=8/tr=32/en=20/basics=4, energy types {WATER} all used, zero unpayable, evolution line intact (4x Mega Starmie ex on 4x Staryu) → zero flags confirmed, not assumed.

**6. Arithmetic**: every mulligan constant in test code hand-verified by execution (command + full output in Task 2). B=4 → 0.6005003742553344 (60%), B=6 → 0.458563922158619 (46%), B=2 → 0.7785310734463277 (78%), B=8 → 0.3464064289681811, B=0 → 1.0.

**Self-review (spec coverage / placeholders / type consistency):** Section 1 → Tasks 2-4 (+ Task 1 amendment); Section 2 → Tasks 6-8; Section 3 → Tasks 9-10 (+ Task 1 HST amendment); Section 4 → Task 11; Section 5 → tests in every task + Go-live section; Out-of-scope respected (no deckdb change, no worker-code change, no batch-cap tripwire, no auth). No TBD/placeholder steps; `DeckQualityReport`/`Flag`/`safe_analyze`/`quality_summary_html`/`quality_reasons_html`/`canonical_decks` signatures identical at definition and every use site. Deliberate deviations from the spec's letter, all recorded: `flags` as tuple (frozen dataclass), bulk PRG redirect replacing the result page (spec Section 3 requires flash "after ... bulk"), None-analysis rows included in the problems filter.
