# Continuous Factory Operation + Automated Queue Refill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The factory runs continuously (repeating scheduled task, every 15 min + at startup) instead of nightly-only, refills its own candidate queue from a deck-matrix generator when the queue runs dry, and releases submissions at cadence 5/day — with locking that makes the 5/day Kaggle hard cap uncrossable even under concurrent processes.

**Architecture:** A thin new entry (`scripts/factory_watch_once.py`) fires per Task Scheduler repetition: single-instance lock → PAUSE check → refill from `src/ptcg/factory/deck_matrix.py` (curated seeds × deterministic legality-preserving mutation rules; staged tiers: heuristic screen first, per-deck net training for top performers, max 1 training run/day) → one throttled `run_cycle` (BelowNormal priority, 60-min budget, digest suppressed for no-op firings). Existing `run_cycle`/`evaluate_queued`/gate/submit machinery is reused unchanged except: a `suppress_empty_digest` flag on `run_cycle`, cadence defaults 2→5, and `SubmissionCounter` gains sidecar locking (reusing `ledger_lock`).

**Tech Stack:** Python 3.12 stdlib only (ctypes for Windows priority class — no psutil), pytest, PowerShell Task Scheduler cmdlets, existing `cg` engine card DB for deck legality.

**Spec:** `docs/superpowers/specs/2026-07-17-continuous-factory-design.md`
**Branch:** `feature/continuous-factory` (baseline: 342 tests green)

## Global Constraints

- Every `write_text`/`open` call passes `encoding="utf-8"` — Windows cp1252 default truncates-then-crashes on non-ASCII (repo lesson; a guard test exists).
- Repo path contains a space (`Dev Folder`) — quote every path in every shell command.
- `ledger_lock` is NOT reentrant (candidates.py:199-201). Never nest acquires on the same path. The three lock files in play are distinct: `candidates.json.lock`, `submission_counter.json.lock`, `watch.lock` — no nesting of the SAME path occurs in this plan; keep it that way.
- Any script/function that is FIRST to write into a directory must have a test that does NOT pre-create the parent (virgin-directory rule — recurred twice in this repo; `tmp_path` pre-creates parents, so point at `tmp_path / "nested" / "deeper"`).
- Tests must never touch the real ledger/scheduler/Kaggle: all factory tests use `tmp_path`-rooted `FactoryPaths` and stub clients (see `tests/test_factory_cycle.py` for the established pattern).
- `git add` by explicit path only; on `index.lock` contention wait 2s and retry up to 3×. Conventional commits.
- TDD per task: write the failing test, watch it fail, implement, watch it pass, commit.
- Plan-authored numbers/fixtures: hand-verify reachability before transcribing (`.claude/rules/plan-test-arithmetic-sanity.md`).
- HARD_DAILY_CAP = 5 is a Kaggle rule and stays literally never-configurable-upward; cadence may equal it but nothing may exceed it.
- Deterministic randomless generation: `deck_matrix` uses no RNG anywhere — same inputs → same decks, same names, same hashes.

## File Structure

- Create: `src/ptcg/factory/deck_matrix.py` (T1: deck IO + mutation rules; T2: matrix enumeration + refill)
- Create: `src/ptcg/factory/watch.py` (T6: instance lock, priority throttle, watch log, training stamp)
- Create: `scripts/factory_watch_once.py` (T7: the per-firing entry)
- Create: `src/ptcg/decks/candidates/generated/` (generated variant CSVs; created at runtime — never pre-create in tests)
- Modify: `src/ptcg/factory/gate.py` (T3: SubmissionCounter locking)
- Modify: `src/ptcg/factory/cycle.py` (T4: `suppress_empty_digest`)
- Modify: `scripts/factory_cycle.py`, `src/ptcg/factory/cycle.py`, `src/ptcg/factory/submit.py`, `src/ptcg/factory/gate.py` (T5: cadence defaults 2→5)
- Modify: `src/ptcg/factory/trainers.py` (T6: generated-deck path fix in `export_and_register`)
- Modify: `scripts/register_factory_task.ps1` (T8: continuous trigger)
- Modify: `docs/factory-operations.md`, `docs/weekly-review-checklist.md` (T9)
- Tests: `tests/test_factory_deck_matrix.py`, `tests/test_factory_watch.py` (new); `tests/test_factory_gate.py`, `tests/test_factory_cycle.py`, `tests/test_factory_trainers.py` (extended)

---

### Task 1: deck_matrix.py — deck IO, card classification, mutation rules

**Files:**
- Create: `src/ptcg/factory/deck_matrix.py`
- Test: `tests/test_factory_deck_matrix.py`

**Interfaces:**
- Consumes: `ptcg.arena.runner.load_deck(path) -> list[int]` (raises ValueError unless exactly 60); `ptcg.decks.validate.validate_deck(deck: list[int]) -> list[str]` (empty list = legal); `cg.api.all_card_data()`, `cg.api.CardType` (members used: `POKEMON`, `BASIC_ENERGY` — everything else counts as trainer-ish for mutation purposes).
- Produces (used by T2/T7): `deck_hash(deck: list[int]) -> str`; `write_deck_csv(path: Path, deck: list[int]) -> None`; `MUTATION_RULES: dict[str, Callable[[list[int]], list[int] | None]]` with keys `"energy-up2"`, `"energy-down2"`, `"attacker-up1"`, `"attacker-down1"`; `apply_rule(deck, rule_name) -> list[int] | None` (None = rule not applicable OR result illegal).

**Card classification** (module-internal): a card id is *basic energy* if `card.cardType == CardType.BASIC_ENERGY`; *pokemon* if `card.cardType == CardType.POKEMON`; otherwise *trainer* for mutation purposes. The swap partner for every rule is the **highest-count trainer** (ties broken by lowest card id — deterministic).

**Rules (all deterministic, all validated):**
- `energy-up2`: +2 copies of the deck's most-common basic-energy id, −2 from highest-count trainer. Not applicable if the deck has no basic energy or no trainer with ≥2 copies.
- `energy-down2`: −2 most-common basic energy (needs ≥3 copies so ≥1 remains), +2 highest-count trainer.
- `attacker-up1`: the highest-count pokemon with exactly 3 copies goes to 4; −1 from highest-count trainer. Not applicable if no 3-count pokemon.
- `attacker-down1`: the highest-count pokemon with exactly 4 copies goes to 3; +1 to highest-count trainer. Not applicable if no 4-count pokemon.

Every rule returns a NEW 60-card list or None; `apply_rule` additionally returns None if `validate_deck(result)` is non-empty. (Note: `validate_deck` caps copies by NAME, not id — the +2/+1 trainer bumps can exceed 4-per-name; that is exactly why the post-validate is mandatory, not belt-and-braces.)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_factory_deck_matrix.py
"""Deck-matrix generation: deterministic, legality-preserving, virgin-dir safe."""
from pathlib import Path

import pytest

from ptcg.arena.runner import load_deck
from ptcg.decks.validate import validate_deck
from ptcg.factory.deck_matrix import (
    MUTATION_RULES, apply_rule, deck_hash, write_deck_csv,
)

SEED = Path(__file__).resolve().parents[1] / "src" / "ptcg" / "decks" / \
    "candidates" / "mega-starmie-water.csv"


def test_deck_hash_is_order_insensitive_and_stable():
    deck = load_deck(SEED)
    assert deck_hash(deck) == deck_hash(list(reversed(deck)))
    assert deck_hash(deck) != deck_hash(deck[:59] + [deck[58]])


def test_write_deck_csv_creates_virgin_parents(tmp_path):
    target = tmp_path / "never" / "created" / "deck.csv"  # virgin-dir rule
    deck = load_deck(SEED)
    write_deck_csv(target, deck)
    assert load_deck(target) == deck  # round-trips through the arena loader


def test_every_rule_yields_legal_60_or_none():
    deck = load_deck(SEED)
    applied = 0
    for name in MUTATION_RULES:
        out = apply_rule(deck, name)
        if out is None:
            continue
        applied += 1
        assert len(out) == 60
        assert validate_deck(out) == []
        assert out != deck
    assert applied >= 2  # the starmie seed supports at least energy+attacker moves


def test_rules_are_deterministic():
    deck = load_deck(SEED)
    for name in MUTATION_RULES:
        assert apply_rule(deck, name) == apply_rule(deck, name)


def test_energy_up2_moves_exactly_two_cards():
    deck = load_deck(SEED)
    out = apply_rule(deck, "energy-up2")
    if out is None:
        pytest.skip("seed has no applicable energy-up move")
    assert len(out) == len(deck) == 60
    assert sorted(out) != sorted(deck)
    # exactly 2 slots differ as a multiset
    from collections import Counter
    diff = Counter(out) - Counter(deck)
    assert sum(diff.values()) == 2
```

- [ ] **Step 2: Run tests, verify they fail** — `uv run pytest tests/test_factory_deck_matrix.py -v` → FAIL: `ModuleNotFoundError`/`ImportError` on `ptcg.factory.deck_matrix`.

- [ ] **Step 3: Implement**

```python
# src/ptcg/factory/deck_matrix.py
"""Deck-matrix generator: curated seeds x deterministic legality-preserving
mutation rules (spec design 2). No RNG anywhere - same inputs, same decks."""
from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Callable

from cg.api import CardType, all_card_data

ROOT = Path(__file__).resolve().parents[3]


def _card_db():
    return {c.cardId: c for c in all_card_data()}


def deck_hash(deck: list[int]) -> str:
    return hashlib.sha256(",".join(map(str, sorted(deck))).encode("utf-8")).hexdigest()[:16]


def write_deck_csv(path: Path, deck: list[int]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)  # virgin-dir rule
    path.write_text("\n".join(str(c) for c in deck) + "\n", encoding="utf-8")


def _by_class(deck: list[int]) -> tuple[Counter, Counter, Counter]:
    """(energy, pokemon, trainer) id->count. Unknown ids count as trainer-ish;
    apply_rule's post-validate rejects any deck containing them anyway."""
    db = _card_db()
    energy, pokemon, trainer = Counter(), Counter(), Counter()
    for cid in deck:
        card = db.get(cid)
        if card is not None and card.cardType == CardType.BASIC_ENERGY:
            energy[cid] += 1
        elif card is not None and card.cardType == CardType.POKEMON:
            pokemon[cid] += 1
        else:
            trainer[cid] += 1
    return energy, pokemon, trainer


def _top(counter: Counter, *, count_eq: int | None = None,
         min_count: int = 1) -> int | None:
    """Highest-count id (ties -> lowest id, deterministic); optionally only
    among ids whose count == count_eq / >= min_count."""
    items = [(n, cid) for cid, n in counter.items()
             if (count_eq is None or n == count_eq) and n >= min_count]
    if not items:
        return None
    n, cid = max(items, key=lambda t: (t[0], -t[1]))
    return cid


def _swap(deck: list[int], add_id: int, remove_id: int, n: int) -> list[int]:
    out = list(deck)
    for _ in range(n):
        out.remove(remove_id)
    out.extend([add_id] * n)
    return out


def _energy_up2(deck: list[int]) -> list[int] | None:
    energy, _, trainer = _by_class(deck)
    add, cut = _top(energy), _top(trainer, min_count=2)
    if add is None or cut is None:
        return None
    return _swap(deck, add, cut, 2)


def _energy_down2(deck: list[int]) -> list[int] | None:
    energy, _, trainer = _by_class(deck)
    cut, add = _top(energy, min_count=3), _top(trainer)
    if cut is None or add is None:
        return None
    return _swap(deck, add, cut, 2)


def _attacker_up1(deck: list[int]) -> list[int] | None:
    _, pokemon, trainer = _by_class(deck)
    add, cut = _top(pokemon, count_eq=3), _top(trainer)
    if add is None or cut is None:
        return None
    return _swap(deck, add, cut, 1)


def _attacker_down1(deck: list[int]) -> list[int] | None:
    _, pokemon, trainer = _by_class(deck)
    cut, add = _top(pokemon, count_eq=4), _top(trainer)
    if cut is None or add is None:
        return None
    return _swap(deck, add, cut, 1)


MUTATION_RULES: dict[str, Callable[[list[int]], list[int] | None]] = {
    "energy-up2": _energy_up2,
    "energy-down2": _energy_down2,
    "attacker-up1": _attacker_up1,
    "attacker-down1": _attacker_down1,
}


def apply_rule(deck: list[int], rule_name: str) -> list[int] | None:
    from ptcg.decks.validate import validate_deck
    out = MUTATION_RULES[rule_name](deck)
    if out is None or len(out) != 60 or validate_deck(out):
        return None
    return out
```

- [ ] **Step 4: Run tests, verify pass** — `uv run pytest tests/test_factory_deck_matrix.py -v` → all PASS.
- [ ] **Step 5: Commit** — `git add src/ptcg/factory/deck_matrix.py tests/test_factory_deck_matrix.py` → `feat: deck-matrix mutation rules (deterministic, legality-validated)`

---

### Task 2: deck_matrix.py — matrix enumeration + refill_queue

**Files:**
- Modify: `src/ptcg/factory/deck_matrix.py` (append)
- Test: `tests/test_factory_deck_matrix.py` (append)

**Interfaces:**
- Consumes: T1's `MUTATION_RULES`/`apply_rule`/`deck_hash`/`write_deck_csv`; `ptcg.factory.candidates.Candidate`, `Status`, `next_version`; `ptcg.arena.runner.load_deck`.
- Produces (used by T7):
  - `SEEDS: tuple[tuple[str, float], ...]` — (repo-relative csv path, base priority)
  - `matrix_decks(decks_dir: Path = ...) -> list[tuple[str, float]]` — every (repo-relative deck path, priority): seeds + generated variants; writes missing variant CSVs; dedups by `deck_hash`.
  - `tested_deck_agent_pairs(candidates) -> set[tuple[str, str]]` — normalized (deck-path, agent-sig) pairs for ANY candidate status.
  - `net_eligible_decks(candidates, top_n=3) -> list[str]` — deck paths of the top-n heuristic candidates by `(kaggle_score or -inf, local_wr or -inf)`, excluding RETIRED.
  - `refill_queue(candidates, *, max_new=10, decks_dir=..., log=print) -> list[Candidate]` — new QUEUED candidates (not yet saved; caller merge_saves).

**Semantics locked here:**
- `SEEDS`: starmie family at 0.6 (`mega-starmie-water.csv`, `mega-starmie-water-density20.csv`, `mega-starmie-water-lean.csv`), lucario legacy at 0.45 (`mega-lucario-fighting.csv`).
- Variant deck path: `src/ptcg/decks/candidates/generated/{seed_stem}-{rule}.csv`; variant inherits its seed's priority.
- Screen-tier candidate: `agent_kind="heuristic"`, name `f"{deck_stem}-heuristic"`, `novel_axis=True` for rule-generated variants only (parity-band exploration is the point of the matrix), `provenance=f"deck-matrix:{seed_stem}:{rule}"` (seeds re-screened: rule `"screen"`, `novel_axis=False`).
- Net-tier candidate cells are enqueued by refill ONLY when trained weights already exist at `src/ptcg/search/value_net_weights_{deck_stem}.json` (matching `trainers.py` `train()` output). Two config cells per eligible deck: `{"search_budget_ms": 200, "net_weights": "src/ptcg/search/value_net_weights_{stem}.json"}` at priority 0.4 and the same with `500` at 0.35; name `f"{deck_stem}-searchnet"` / `f"{deck_stem}-searchnet-b500"`; `novel_axis=False`; `provenance="deck-matrix:net-tier"`. Training itself is T6/T7's job, never refill's.
- Agent-sig normalization for `tested_deck_agent_pairs`: `agent_kind` plus `search_budget_ms` (only) — i.e. `("heuristic", "")` vs `("search-net", "200")` — so a re-tuned unrelated config key doesn't spuriously block a cell, and deck paths compare as posix-style strings (`str(Path(p).as_posix())`).
- Ordering before the `max_new` cap: descending priority, ties by deck path then name (fully deterministic).

- [ ] **Step 1: Write failing tests** (append to `tests/test_factory_deck_matrix.py`)

```python
from ptcg.factory.candidates import Candidate, Status
from ptcg.factory.deck_matrix import (
    SEEDS, matrix_decks, net_eligible_decks, refill_queue,
    tested_deck_agent_pairs,
)


def _cand(name, deck, kind="heuristic", **kw):
    return Candidate.create(name=name, version="v1.0", deck=deck,
                            agent_kind=kind, **kw)


def test_matrix_decks_writes_variants_into_virgin_dir(tmp_path):
    out_dir = tmp_path / "gen" / "never-created"  # virgin-dir rule
    decks = matrix_decks(decks_dir=out_dir)
    # every seed present, at least one generated variant, all files loadable+legal
    from ptcg.arena.runner import load_deck
    from ptcg.decks.validate import validate_deck
    paths = [p for p, _ in decks]
    assert all(any(seed_path in p for p, _ in decks) for seed_path, _ in SEEDS)
    generated = [p for p in paths if "never-created" in p or str(out_dir) in p]
    assert generated, "expected at least one generated variant"
    for p, _prio in decks:
        deck = load_deck(Path(p) if Path(p).is_absolute() else
                         Path(__file__).resolve().parents[1] / p)
        assert validate_deck(deck) == []


def test_matrix_decks_dedups_by_content(tmp_path):
    a = matrix_decks(decks_dir=tmp_path / "g")
    hashes = set()
    from ptcg.arena.runner import load_deck
    from ptcg.factory.deck_matrix import deck_hash
    root = Path(__file__).resolve().parents[1]
    for p, _ in a:
        d = load_deck(Path(p) if Path(p).is_absolute() else root / p)
        h = deck_hash(d)
        assert h not in hashes, f"duplicate deck content: {p}"
        hashes.add(h)


def test_refill_skips_tested_cells_and_caps_batch(tmp_path):
    # a ledger where the plain starmie screen cell is already tested
    tested = _cand("mega-starmie-water-heuristic",
                   "src/ptcg/decks/candidates/mega-starmie-water.csv")
    tested.status = Status.SCORED
    new = refill_queue([tested], max_new=3, decks_dir=tmp_path / "g")
    assert 0 < len(new) <= 3
    ids = {(c.deck, c.agent_kind) for c in new}
    assert ("src/ptcg/decks/candidates/mega-starmie-water.csv",
            "heuristic") not in ids
    assert all(c.status is Status.QUEUED for c in new)
    assert all(c.version == "v0.1" for c in new)  # fresh names -> v0.1


def test_refill_is_idempotent_once_cells_are_queued(tmp_path):
    first = refill_queue([], max_new=50, decks_dir=tmp_path / "g")
    second = refill_queue(list(first), max_new=50, decks_dir=tmp_path / "g")
    assert second == []  # everything enqueued already counts as tested


def test_net_eligible_ranks_by_ladder_then_local():
    a = _cand("a-heuristic", "src/ptcg/decks/candidates/a.csv")
    a.kaggle_score, a.local_wr = 650.0, 0.4
    b = _cand("b-heuristic", "src/ptcg/decks/candidates/b.csv")
    b.kaggle_score, b.local_wr = 600.0, 0.9
    c = _cand("c-heuristic", "src/ptcg/decks/candidates/c.csv")
    c.local_wr = 0.99  # no ladder score
    r = _cand("r-heuristic", "src/ptcg/decks/candidates/r.csv")
    r.kaggle_score, r.status = 999.0, Status.RETIRED
    got = net_eligible_decks([a, b, c, r], top_n=2)
    assert got == ["src/ptcg/decks/candidates/a.csv",
                   "src/ptcg/decks/candidates/b.csv"]
```

- [ ] **Step 2: Run, verify FAIL** — `uv run pytest tests/test_factory_deck_matrix.py -v` → ImportError on the new names.

- [ ] **Step 3: Implement** (append to `deck_matrix.py`)

```python
from ptcg.factory.candidates import Candidate, Status, next_version  # noqa: E402

SEEDS: tuple[tuple[str, float], ...] = (
    ("src/ptcg/decks/candidates/mega-starmie-water.csv", 0.6),
    ("src/ptcg/decks/candidates/mega-starmie-water-density20.csv", 0.6),
    ("src/ptcg/decks/candidates/mega-starmie-water-lean.csv", 0.6),
    ("src/ptcg/decks/candidates/mega-lucario-fighting.csv", 0.45),
)
GENERATED_DIR = ROOT / "src" / "ptcg" / "decks" / "candidates" / "generated"
WEIGHTS_DIR = ROOT / "src" / "ptcg" / "search"


def _rel(p: Path) -> str:
    p = Path(p)
    if p.is_absolute():
        try:
            p = p.relative_to(ROOT)
        except ValueError:
            pass  # tests point outside ROOT; keep absolute
    return p.as_posix()


def matrix_decks(decks_dir: Path = GENERATED_DIR) -> list[tuple[str, float]]:
    """Seeds + generated variants, deduped by content hash. Writes any missing
    variant CSV (virgin-dir safe via write_deck_csv)."""
    from ptcg.arena.runner import load_deck
    decks_dir = Path(decks_dir)
    out: list[tuple[str, float]] = []
    seen: set[str] = set()
    for seed_path, prio in SEEDS:
        seed_abs = ROOT / seed_path
        if not seed_abs.exists():
            continue
        deck = load_deck(seed_abs)
        h = deck_hash(deck)
        if h not in seen:
            seen.add(h)
            out.append((seed_path, prio))
        for rule in MUTATION_RULES:
            variant = apply_rule(deck, rule)
            if variant is None:
                continue
            vh = deck_hash(variant)
            if vh in seen:
                continue
            seen.add(vh)
            vpath = decks_dir / f"{seed_abs.stem}-{rule}.csv"
            if not vpath.exists():
                write_deck_csv(vpath, variant)
            out.append((_rel(vpath), prio))
    return out


def _agent_sig(agent_kind: str, agent_config: dict) -> tuple[str, str]:
    return (agent_kind, str(agent_config.get("search_budget_ms", "")))


def tested_deck_agent_pairs(
        candidates: list[Candidate]) -> set[tuple[str, str, str]]:
    return {(Path(c.deck).as_posix(), *_agent_sig(c.agent_kind, c.agent_config))
            for c in candidates}


def net_eligible_decks(candidates: list[Candidate], top_n: int = 3) -> list[str]:
    heur = [c for c in candidates
            if c.agent_kind == "heuristic" and c.status is not Status.RETIRED
            and (c.kaggle_score is not None or c.local_wr is not None)]
    heur.sort(key=lambda c: (
        c.kaggle_score if c.kaggle_score is not None else float("-inf"),
        c.local_wr if c.local_wr is not None else float("-inf")), reverse=True)
    seen: list[str] = []
    for c in heur:
        p = Path(c.deck).as_posix()
        if p not in seen:
            seen.append(p)
        if len(seen) == top_n:
            break
    return seen


def refill_queue(candidates: list[Candidate], *, max_new: int = 10,
                 decks_dir: Path = GENERATED_DIR, log=print) -> list[Candidate]:
    tested = tested_deck_agent_pairs(candidates)
    cells: list[tuple[float, str, str, str, dict, bool, str]] = []
    # (priority, deck, name, agent_kind, agent_config, novel_axis, provenance)
    for deck_path, prio in matrix_decks(decks_dir=decks_dir):
        stem = Path(deck_path).stem
        is_variant = "/generated/" in Path(deck_path).as_posix()
        seed_stem, _, rule = stem.rpartition("-") if is_variant else (stem, "", "screen")
        cells.append((prio, deck_path, f"{stem}-heuristic", "heuristic", {},
                      is_variant, f"deck-matrix:{seed_stem or stem}:{rule}"))
    for deck_path in net_eligible_decks(candidates):
        stem = Path(deck_path).stem
        weights = WEIGHTS_DIR / f"value_net_weights_{stem}.json"
        if not weights.exists():
            continue  # training is the watch loop's job, never refill's
        wrel = _rel(weights)
        cells.append((0.4, deck_path, f"{stem}-searchnet", "search-net",
                      {"search_budget_ms": 200, "net_weights": wrel}, False,
                      "deck-matrix:net-tier"))
        cells.append((0.35, deck_path, f"{stem}-searchnet-b500", "search-net",
                      {"search_budget_ms": 500, "net_weights": wrel}, False,
                      "deck-matrix:net-tier"))
    cells.sort(key=lambda t: (-t[0], t[1], t[2]))
    working = list(candidates)
    new: list[Candidate] = []
    for prio, deck_path, name, kind, config, novel, prov in cells:
        if len(new) >= max_new:
            break
        if (Path(deck_path).as_posix(), *_agent_sig(kind, config)) in tested:
            continue
        cand = Candidate.create(
            name=name, version=next_version(working, name), deck=deck_path,
            agent_kind=kind, agent_config=config, priority=prio,
            novel_axis=novel, provenance=prov,
            notes=f"deck-matrix refill; deck_hash pending eval")
        working.append(cand)
        new.append(cand)
    if new:
        log(f"refill: enqueued {len(new)} matrix cell(s)")
    return new
```

*(Implementer note: `rpartition` seed/rule split relies on rule names being the suffix after the LAST hyphen-group written by `matrix_decks` — rule names contain hyphens (`energy-up2`), so split on the KNOWN rule suffix instead if the simple rpartition misbehaves: iterate `MUTATION_RULES` keys and match `stem.endswith(f"-{rule}")`. Write the test first; implement whichever passes cleanly. The provenance string just needs to name seed + rule.)*

- [ ] **Step 4: Run, verify PASS** — `uv run pytest tests/test_factory_deck_matrix.py -v`
- [ ] **Step 5: Commit** — `git add src/ptcg/factory/deck_matrix.py tests/test_factory_deck_matrix.py` → `feat: matrix enumeration + staged refill_queue with ledger-derived cell tracking`

---

### Task 3: SubmissionCounter locking (gate.py)

**Files:**
- Modify: `src/ptcg/factory/gate.py` (SubmissionCounter only)
- Test: `tests/test_factory_gate.py` (append)

**Interfaces:**
- Consumes: `ptcg.factory.candidates.ledger_lock` (sidecar `<path>.lock`, non-reentrant).
- Produces: same public API (`today_count`, `record`, `reconcile`) — but `record`/`reconcile` now re-read the file under the lock before mutating, so two processes can never both observe count=4 and each record a 5th+6th.

**Behavior locked here:** `record(today)` and `reconcile(today, observed)` each: acquire `ledger_lock(self.path)` → `self._refresh()` (re-read disk) → apply the mutation exactly as today → `self._save()` → release. `__init__` keeps its unlocked read (constructor race is harmless — every mutator refreshes). `today_count` stays lock-free (advisory read; the gate's cap check is re-validated inside `record` by the caller sequence, and the hard-cap invariant holds because mutations serialize).

- [ ] **Step 1: Write failing tests** (append to `tests/test_factory_gate.py`, following its existing fixture style)

```python
def test_counter_record_rereads_disk_under_lock(tmp_path):
    """Two counter instances (two processes) must serialize: each record()
    must observe the other's persisted count, never clobber it."""
    from ptcg.factory.gate import SubmissionCounter
    path = tmp_path / "sub" / "counter.json"  # virgin parent (sub/ not created)
    a = SubmissionCounter(path)
    b = SubmissionCounter(path)  # stale second instance
    a.record("2026-07-17")
    b.record("2026-07-17")  # must land at 2, not clobber back to 1
    fresh = SubmissionCounter(path)
    assert fresh.today_count("2026-07-17") == 2


def test_counter_reconcile_floors_not_clobbers(tmp_path):
    from ptcg.factory.gate import SubmissionCounter
    path = tmp_path / "counter.json"
    a = SubmissionCounter(path)
    b = SubmissionCounter(path)
    a.record("2026-07-17")
    a.record("2026-07-17")
    b.reconcile("2026-07-17", 1)  # observed 1 on ladder, disk already at 2
    fresh = SubmissionCounter(path)
    assert fresh.today_count("2026-07-17") == 2  # max(disk 2, observed 1)
```

Hand-check (arithmetic-sanity rule): pre-fix, `b.record` in the first test starts from b's stale in-memory (date=None → today_count 0) and writes count=1 — the test FAILS at 1 != 2 against current code, passes only with the re-read. Verified reachable.

- [ ] **Step 2: Run, verify FAIL** — `uv run pytest tests/test_factory_gate.py -k counter -v` → first test fails 1 != 2.

- [ ] **Step 3: Implement** — in `gate.py`: add `from ptcg.factory.candidates import ledger_lock` to the existing import from candidates; extract `_refresh`:

```python
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._refresh()

    def _refresh(self) -> None:
        if self.path.exists():
            doc = json.loads(self.path.read_text(encoding="utf-8"))
            self.date, self.count = doc.get("date"), int(doc.get("count", 0))
        else:
            self.date, self.count = None, 0

    def record(self, today: str) -> None:
        with ledger_lock(self.path):
            self._refresh()
            self.count = self.today_count(today) + 1
            self.date = today
            self._save()

    def reconcile(self, today: str, observed_today: int) -> None:
        """Floor at what the harvested ladder shows (manual uploads count too);
        never decreases within a day."""
        with ledger_lock(self.path):
            self._refresh()
            self.count = max(self.today_count(today), observed_today)
            self.date = today
            self._save()
```

- [ ] **Step 4: Run, verify PASS** — `uv run pytest tests/test_factory_gate.py -v` (whole file — no regression in existing gate tests).
- [ ] **Step 5: Commit** — `git add src/ptcg/factory/gate.py tests/test_factory_gate.py` → `fix: SubmissionCounter serializes record/reconcile under sidecar lock`

---

### Task 4: run_cycle digest suppression for no-op firings

**Files:**
- Modify: `src/ptcg/factory/cycle.py`
- Test: `tests/test_factory_cycle.py` (append)

**Interfaces:**
- Produces: `run_cycle(..., suppress_empty_digest: bool = False)` — default False keeps today's behavior byte-identical for `scripts/factory_cycle.py`. When True and the cycle was a no-op, skip `write_digest` AND `append_journal`, and include `"noop": True` with `"digest": None` in the returned dict.
- **No-op definition (exact):** `not evaluated and not actions and (harvest_res is None or harvest_res.scored_updates == 0)`. (`submissions_today` deliberately does NOT count — it stays >0 all day after any upload and would defeat suppression.)

- [ ] **Step 1: Write failing test** (append to `tests/test_factory_cycle.py`, using its existing stub-client/tmp-paths fixtures — grep the file for the established `FactoryPaths(tmp_path)` + fake client pattern and reuse it verbatim)

```python
def test_suppress_empty_digest_skips_digest_and_journal(tmp_path, ...):
    # arrange: client returning zero rows, empty queue -> guaranteed no-op
    result = run_cycle(paths, client, cfg, no_submit=True,
                       suppress_empty_digest=True)
    assert result.get("noop") is True
    assert result["digest"] is None
    assert not list(paths.digest_dir.glob("*.md"))
    assert not paths.journal.exists()


def test_suppress_flag_still_writes_when_scores_update(tmp_path, ...):
    # arrange: client stub returns one row matching a SUBMITTED candidate with a
    # new public_score -> scored_updates == 1 -> digest MUST be written
    result = run_cycle(paths, client, cfg, no_submit=True,
                       suppress_empty_digest=True)
    assert result.get("noop") is not True
    assert len(list(paths.digest_dir.glob("*.md"))) == 1


def test_default_behavior_unchanged_writes_digest_on_noop(tmp_path, ...):
    result = run_cycle(paths, client, cfg, no_submit=True)
    assert len(list(paths.digest_dir.glob("*.md"))) == 1  # today's behavior
```

- [ ] **Step 2: Run, verify FAIL** — TypeError (unexpected kwarg) / assertion.
- [ ] **Step 3: Implement** — in `run_cycle`, replace step 4 with:

```python
    # 4. digest + methodology journal (spec S6) - suppressed for no-op firings
    # under the continuous watch loop (96 firings/day must not write 96 digests)
    noop = (not evaluated and not actions
            and (harvest_res is None or harvest_res.scored_updates == 0))
    if suppress_empty_digest and noop:
        return {"harvest": harvest_res, "evaluated": [], "actions": actions,
                "digest": None, "noop": True}
    digest_path = write_digest(paths.digest_dir, harvest_res, evaluated, actions,
                               now=now)
    summary = (f"evaluated {len(evaluated)} candidate(s); "
               f"actions: {[f'{cid}:{act}' for cid, act, _ in actions] or 'none'}; "
               f"digest: {digest_path.name}")
    append_journal(paths.journal, f"factory cycle {today}", summary, now=now)
    return {"harvest": harvest_res, "evaluated": [c.id for c in evaluated],
            "actions": actions, "digest": str(digest_path)}
```

and add `suppress_empty_digest: bool = False` to the signature (keyword-only, after `cadence_per_day`).

- [ ] **Step 4: Run, verify PASS** — `uv run pytest tests/test_factory_cycle.py -v` (whole file).
- [ ] **Step 5: Commit** — `git add src/ptcg/factory/cycle.py tests/test_factory_cycle.py` → `feat: run_cycle suppress_empty_digest flag for no-op watch firings`

---

### Task 5: cadence defaults 2 → 5 (all sites, one task)

**Files:**
- Modify: `scripts/factory_cycle.py` (line 28 `--cadence` default), `src/ptcg/factory/cycle.py` (`run_cycle` `cadence_per_day=2` default), `src/ptcg/factory/submit.py` (`submit_candidates` default), `src/ptcg/factory/gate.py` (`decide` default)
- Test: every assertion site found by the sweep below

**Per the constant-bump rule, this task updates ALL defaults and ALL test assertion sites in ONE task so the suite never goes red across tasks.**

- [ ] **Step 1: Sweep for every site** — run and record output in the task report:
  - `Grep -n "cadence" src/ scripts/ tests/` (all matches)
  - Update the four defaults to `5`. For each TEST currently asserting cadence-2 behavior (e.g. gate tests that construct "cadence used up" scenarios with 2 submissions), decide per test: if it PASSES an explicit `cadence_per_day=2`, leave it (explicit args still valid); if it RELIES on the default, either pin `cadence_per_day=2` explicitly (preserving the scenario) or update the scenario to 5 — prefer pinning explicitly, it keeps scenario arithmetic intact.
- [ ] **Step 2: Run full factory tests** — `uv run pytest tests/test_factory_gate.py tests/test_factory_submit.py tests/test_factory_cycle.py -v` → all PASS.
- [ ] **Step 3: Run the whole suite** — `uv run pytest` → 342+ PASS (no distant assertion site missed).
- [ ] **Step 4: Commit** — explicit paths → `feat: raise default submission cadence 2->5 per day (hard cap 5 unchanged)`

---

### Task 6: watch.py helpers + trainers.py generated-deck path fix

**Files:**
- Create: `src/ptcg/factory/watch.py`
- Modify: `src/ptcg/factory/trainers.py` (`PerDeckNetTrainer.export_and_register` deck path)
- Test: `tests/test_factory_watch.py` (new), `tests/test_factory_trainers.py` (append)

**Interfaces (consumed by T7):**
- `instance_lock(lock_path: Path)` — context manager; raises `TimeoutError` fast (timeout_s=0.5) if another firing holds it; stale-break 8h (28800 s — must exceed the longest legitimate holder: a training run). Reuses `ledger_lock` with those parameters.
- `throttle_below_normal(log=print) -> bool` — Windows-only ctypes `SetPriorityClass(GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS=0x00004000)`; returns True on success, False (logged, never raises) elsewhere/on failure. Child processes inherit the class.
- `append_watch_log(log_path: Path, msg: str, now=None) -> None` — one `[ISO-minutes] msg` line, `encoding="utf-8"`, parents auto-created (virgin-dir).
- `training_due(stamp_path: Path, today: str) -> bool` / `record_training(stamp_path: Path, today: str, deck: str) -> None` — JSON stamp `{"date": ..., "deck": ...}`; due iff stamp missing or `date != today`. Atomic tmp+`os.replace` write like the counter.
- Trainers fix: `export_and_register` currently returns `deck=f"src/ptcg/decks/candidates/{self.slug}.csv"` — change to `deck=<self.deck relative to ROOT, posix>` (same relativize pattern the method already uses for `weights_rel`), so a generated deck at `candidates/generated/x.csv` registers with its true path. Existing tests asserting the old hardcoded path get updated in this task.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_factory_watch.py
"""Watch helpers: single-instance lock, throttle, watch log, training stamp."""
import sys
from pathlib import Path

import pytest

from ptcg.factory.watch import (
    append_watch_log, instance_lock, record_training, throttle_below_normal,
    training_due,
)


def test_instance_lock_excludes_second_holder(tmp_path):
    lock = tmp_path / "locks" / "watch.lock"  # virgin parent
    with instance_lock(lock):
        with pytest.raises(TimeoutError):
            with instance_lock(lock):
                pass
    with instance_lock(lock):  # released -> reacquirable
        pass


def test_throttle_returns_bool_and_never_raises():
    ok = throttle_below_normal(log=lambda m: None)
    assert ok is (sys.platform == "win32")


def test_append_watch_log_virgin_dir_and_utf8(tmp_path):
    log = tmp_path / "logs" / "never" / "watch.log"
    append_watch_log(log, "refill: enqueued 3 — matrix")  # em dash: utf-8 check
    append_watch_log(log, "second line")
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and lines[0].endswith("matrix")


def test_training_stamp_roundtrip(tmp_path):
    stamp = tmp_path / "stamps" / "training_stamp.json"  # virgin parent
    assert training_due(stamp, "2026-07-17") is True
    record_training(stamp, "2026-07-17", "some/deck.csv")
    assert training_due(stamp, "2026-07-17") is False
    assert training_due(stamp, "2026-07-18") is True
```

```python
# append to tests/test_factory_trainers.py
def test_export_and_register_preserves_generated_deck_path(tmp_path):
    from ptcg.factory.trainers import PerDeckNetTrainer, ROOT
    deck = ROOT / "src" / "ptcg" / "decks" / "candidates" / "generated" / "x-energy-up2.csv"
    t = PerDeckNetTrainer(deck=deck)
    cand = t.export_and_register(tmp_path / "w.json", 0, [])
    assert cand.deck == "src/ptcg/decks/candidates/generated/x-energy-up2.csv"
```

(Adapt the existing `test_factory_trainers.py` construction/fixture conventions — grep it first; if `export_and_register` needs more of the trainer configured in the established tests, mirror that setup.)

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement**

```python
# src/ptcg/factory/watch.py
"""Per-firing helpers for the continuous watch loop (spec design 1)."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

from ptcg.factory.candidates import ledger_lock

WATCH_LOCK_STALE_S = 8 * 3600.0  # > longest legitimate holder (a training run)


def instance_lock(lock_path: Path):
    """Single-instance guard for watch firings: fail FAST if another firing is
    live (Task Scheduler will fire again in 15 min), break locks older than 8h
    (a crashed holder must never wedge the loop)."""
    return ledger_lock(Path(lock_path), timeout_s=0.5,
                       stale_after_s=WATCH_LOCK_STALE_S)


def throttle_below_normal(log=print) -> bool:
    """BelowNormal priority class so the machine stays usable (spec: always-on
    but throttled). Child processes inherit the class on Windows."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = bool(ctypes.windll.kernel32.SetPriorityClass(
            handle, BELOW_NORMAL_PRIORITY_CLASS))
        if not ok:
            log("throttle: SetPriorityClass failed (continuing unthrottled)")
        return ok
    except Exception as exc:
        log(f"throttle failed (continuing unthrottled): {exc!r}")
        return False


def append_watch_log(log_path: Path, msg: str,
                     now: dt.datetime | None = None) -> None:
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = (now or dt.datetime.now()).isoformat(timespec="minutes")
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {msg}\n")


def training_due(stamp_path: Path, today: str) -> bool:
    stamp_path = Path(stamp_path)
    if not stamp_path.exists():
        return True
    doc = json.loads(stamp_path.read_text(encoding="utf-8"))
    return doc.get("date") != today


def record_training(stamp_path: Path, today: str, deck: str) -> None:
    stamp_path = Path(stamp_path)
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = stamp_path.with_suffix(stamp_path.suffix + ".tmp")
    tmp.write_text(json.dumps({"date": today, "deck": deck}), encoding="utf-8")
    os.replace(tmp, stamp_path)
```

Trainers fix — in `export_and_register`, replace the hardcoded deck string with the same relativize dance used for `weights_rel`, applied to `self.deck`:

```python
        deck_rel = Path(self.deck)
        if deck_rel.is_absolute():
            try:
                deck_rel = deck_rel.relative_to(ROOT)
            except ValueError:
                pass
        return Candidate.create(
            name=name, version=version,
            deck=deck_rel.as_posix(),
            ...  # rest unchanged
```

- [ ] **Step 4: Run, verify PASS** — `uv run pytest tests/test_factory_watch.py tests/test_factory_trainers.py -v`
- [ ] **Step 5: Commit** — explicit paths → `feat: watch helpers (instance lock, throttle, watch log, training stamp) + trainer generated-deck path fix`

---

### Task 7: scripts/factory_watch_once.py — the per-firing entry

**Files:**
- Create: `scripts/factory_watch_once.py`
- Test: `tests/test_factory_watch.py` (append — test the extracted `watch_once()` function, not the CLI shell)

**Interfaces:**
- Consumes: T2 `refill_queue`/`net_eligible_decks`, T4 `run_cycle(suppress_empty_digest=)`, T5 cadence default, T6 all helpers, `ptcg.factory.daemon.run_daemon`, `ptcg.factory.trainers.PerDeckNetTrainer`, `merge_save`, `load_ledger`, `Status`, `FactoryPaths`, `EvalConfig`, `KaggleClient`.
- Produces: `watch_once(paths, client, cfg, *, cadence_per_day=5, max_refill=10, skip_training=False, train_fn=None, refill_fn=refill_queue, cycle_fn=run_cycle, log=print) -> dict` — injectable seams (`train_fn`, `refill_fn`, `cycle_fn`) so tests never run real training/series. New watch-owned paths (extend usage, not FactoryPaths — keep cycle.py untouched by this task): `watch_lock = paths.root/"experiments"/"factory"/"watch.lock"`, `watch_log = paths.log_dir/"watch.log"`, `training_stamp = paths.root/"experiments"/"factory"/"training_stamp.json"`, `daemon_stop = paths.root/"experiments"/"factory"/"DAEMON_STOP"`, `daemon_state = paths.root/"experiments"/"factory"/"daemon_state.json"`.

**Flow (locked):**
1. `throttle_below_normal(log)`.
2. PAUSE file → watch-log line `"paused"`, return `{"paused": True}`.
3. `instance_lock` → on `TimeoutError`: watch-log `"busy: another firing holds the lock"`, return `{"busy": True}`. ALL remaining steps run inside the lock.
4. `candidates = load_ledger(paths.ledger)`; if no QUEUED candidate: `new = refill_fn(candidates, max_new=max_refill)`; if new: `merge_save(paths.ledger, new, log=log)`, watch-log `f"refill: enqueued {len(new)}"`.
5. Training tier — only if (queue was empty) AND (refill produced nothing) AND (not skip_training) AND `training_due(stamp, utc_today())`: pick the first deck from `net_eligible_decks(candidates)` whose weights file `src/ptcg/search/value_net_weights_{stem}.json` does NOT exist; if one exists, run `train_fn(deck)` (default: `run_daemon(PerDeckNetTrainer(deck=paths.root / deck), 1, paths.ledger, daemon_state, log=log, stop_path=daemon_stop)`), then `record_training(stamp, utc_today(), deck)`, watch-log line. Training failures: catch `Exception`, watch-log `f"training failed: {exc!r}"`, continue to the cycle (crash isolation — a broken trainer must not stop harvest/eval).
6. `result = cycle_fn(paths, client, cfg, no_submit=no_submit, cadence_per_day=cadence_per_day, suppress_empty_digest=True, log=log)`.
7. Watch-log one summary line: `f"cycle: noop"` or `f"cycle: evaluated={len(result['evaluated'])} actions={len(result['actions'])} digest={result['digest']}"`. Return the merged dict (add `"refilled": len(new)`).

CLI (`main()`): argparse `--no-submit`, `--games` (150), `--budget-minutes` (60.0 — the watch default, NOT 240), `--max-candidates` (None), `--cadence` (5), `--max-refill` (10), `--skip-training`; construct `FactoryPaths(ROOT)`, `EvalConfig`, `KaggleClient()`; same `sys.path` bootstrap as `scripts/factory_cycle.py`; exit 0 in every non-crash outcome (busy/paused included — Task Scheduler treats nonzero as task failure noise).

- [ ] **Step 1: Write failing tests** (append to `tests/test_factory_watch.py`; build stubs — a `cycle_fn` recording its kwargs and returning `{"noop": True, "digest": None, "evaluated": [], "actions": []}`, a `refill_fn` returning canned candidates, a `train_fn` recording calls; `FactoryPaths(tmp_path)` roots everything in tmp). Cases:

```python
def test_watch_once_paused_short_circuits(tmp_path): ...
    # PAUSE present -> {"paused": True}, cycle_fn NOT called, watch.log has "paused"

def test_watch_once_busy_when_lock_held(tmp_path): ...
    # hold instance_lock manually -> {"busy": True}, cycle_fn NOT called

def test_watch_once_refills_only_when_queue_empty(tmp_path): ...
    # ledger with 1 QUEUED candidate -> refill_fn NOT called; empty -> called,
    # new candidates merged into ledger on disk (reload and assert)

def test_watch_once_trains_only_when_refill_dry_and_stamp_due(tmp_path): ...
    # refill_fn returns [] -> train_fn called once, stamp recorded;
    # second call same day -> train_fn NOT called again

def test_watch_once_training_failure_still_runs_cycle(tmp_path): ...
    # train_fn raises -> cycle_fn still called, watch.log records failure

def test_watch_once_passes_suppress_flag_and_cadence(tmp_path): ...
    # recorded cycle_fn kwargs: suppress_empty_digest is True, cadence_per_day == 5
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement** `scripts/factory_watch_once.py` per the locked flow. Put `watch_once()` in the script with the same import-bootstrap style as `factory_cycle.py`; tests import it via `sys.path` insertion of `scripts/` (follow how existing tests import script-level code — grep `tests/` for `scripts` imports; if no precedent exists, place `watch_once` in `src/ptcg/factory/watch.py` instead and keep the script as a 20-line argparse shim — PREFERRED if no precedent).
- [ ] **Step 4: Run, verify PASS** — `uv run pytest tests/test_factory_watch.py -v`, then the full `uv run pytest`.
- [ ] **Step 5: Commit** — explicit paths → `feat: factory_watch_once per-firing entry (refill -> train -> throttled cycle)`

---

### Task 8: register_factory_task.ps1 — continuous trigger

**Files:**
- Modify: `scripts/register_factory_task.ps1`

**Changes (keep -DryRun/-Unregister/idempotency):**
- `$TaskName = "ptcg-factory-continuous"`; `$OldTaskName = "ptcg-factory-nightly"`.
- New param `[int]$IntervalMinutes = 15`.
- Action: `run python scripts/factory_watch_once.py` (working dir unchanged).
- Triggers: (1) repetition — `New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)` (do NOT use `[TimeSpan]::MaxValue` — PS5.1 serialization is unreliable); (2) `New-ScheduledTaskTrigger -AtStartup`. Pass both as an array.
- Settings: `-StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 8) -MultipleInstances IgnoreNew` (8h covers a training-run firing; IgnoreNew is defense-in-depth on top of the instance lock).
- On register (non-DryRun): first unregister `$OldTaskName` if present: `if (Get-ScheduledTask -TaskName $OldTaskName -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $OldTaskName -Confirm:$false }`. `-Unregister` removes BOTH names.
- DryRun output extended to print interval, both triggers, IgnoreNew, and the old-task retirement line.

- [ ] **Step 1: Edit the script** per above.
- [ ] **Step 2: Verify via DryRun (never registers):** `powershell -ExecutionPolicy Bypass -File "scripts\register_factory_task.ps1" -DryRun` → output names `ptcg-factory-continuous`, `factory_watch_once.py`, interval 15, AtStartup, 8h limit, IgnoreNew, retire-nightly line. Paste output in the task report.
- [ ] **Step 3: Commit** — `git add scripts/register_factory_task.ps1` → `feat: register continuous watch task (15-min repetition + startup), retire nightly`

*(Real registration is deliberately NOT part of this task — it happens at T10 rung 3, run by the orchestrator directly, per the safety-classifier split-dispatch lesson.)*

---

### Task 9: Operations docs

**Files:**
- Modify: `docs/factory-operations.md` — new task name + triggers, `factory_watch_once.py` usage, watch.log / training_stamp.json / watch.lock locations, digest-suppression semantics (digests only for cycles that did something; watch.log is the no-op trail), cadence-5 default, throttling note, generated-decks dir + "generated decks/candidates are session-commit drift like all factory output (no-autocommit convention unchanged)".
- Modify: `docs/weekly-review-checklist.md` — update the step-8 liveness check to the new task name (`Get-ScheduledTaskInfo ptcg-factory-continuous`; LastRunTime should be within ~30 min now, not 24-36h); add a matrix-curation step (review `SEEDS`, retire dead seeds, add new archetype seeds; review generated-variant performance in the ledger); note the 1-training-run/day stamp.
- Also update `.claude/rules/factory-task-scheduler-liveness.md` and `.claude/rules/factory-resume-probe.md` task-name references (`ptcg-factory-nightly` → `ptcg-factory-continuous`) and the "~02:00 boundary" phrasing in the resume-probe rule (continuous firing means the mid-session re-probe matters MORE — any long session should re-probe before git-state-dependent actions, not just ones crossing 02:00).

- [ ] **Step 1: Make the edits.** Precision matters: grep each file for `ptcg-factory-nightly` and `02:00` and update every hit or justify leaving it (historical datapoint text stays as-is — only operative instructions change).
- [ ] **Step 2: Commit** — explicit paths → `docs: continuous-factory operations, weekly checklist, resume-probe/liveness rule updates`

---

### Task 10: Acceptance — Smoke Test Ladder + real registration

This slice touches unattended automation + derived numbers → all three rungs are mandatory, plus the scheduler-liveness pre-gate.

- [ ] **Rung 1 (automated):** `uv run pytest` — full suite, exit 0, count ≥ 342 + new tests. Record exact count.
- [ ] **Rung 2 (verify the automation):** confirm run happened on `feature/continuous-factory` at the expected HEAD (`git rev-parse --short HEAD` in the same shell); confirm new test files actually collected (`uv run pytest tests/test_factory_deck_matrix.py tests/test_factory_watch.py --collect-only -q` shows nonzero items); confirm no test touched the real ledger (`git status --porcelain experiments/` clean apart from expected pre-existing drift).
- [ ] **Rung 3 (real thing, part A — refill against the LIVE ledger):** `uv run python scripts/factory_watch_once.py --no-submit --budget-minutes 0 --skip-training` (budget 0 → refill happens, evaluation loop exits immediately, gate/submit dry-runs, digest suppressed unless something happened). Then verify: `experiments/factory/candidates.json` now has QUEUED matrix candidates (list them); generated CSVs exist under `src/ptcg/decks/candidates/generated/` and each passes `validate_deck` (run a 5-line check script); `experiments/factory/logs/watch.log` has the refill line; NO Kaggle upload occurred (it was --no-submit; confirm no new submission via the counter file unchanged).
- [ ] **Rung 3 (part B — real registration, ORCHESTRATOR-RUN, not a subagent):** `powershell -ExecutionPolicy Bypass -File "scripts\register_factory_task.ps1"` then verify: `Get-ScheduledTask -TaskName "ptcg-factory-continuous" | Select-Object -ExpandProperty Triggers` shows the 15-min repetition + AtStartup; `Get-ScheduledTaskInfo -TaskName "ptcg-factory-continuous"` shows NextRunTime within 15 min; old task gone (`Get-ScheduledTask -TaskName "ptcg-factory-nightly"` errors). After ≥20 min, re-check `LastRunTime`/`LastTaskResult=0` and `watch.log` for the firing's line — the loop is LIVE.
- [ ] **Record all rung results in plan.md** (via plan-manager) before Finish.

*(The first real evaluations will run at full games=150 under the live loop; their results land in the ledger/digests autonomously. That is the production path working as designed, not part of this slice's gate.)*

---

## Self-Review (completed at plan-write)

1. **Spec coverage:** §1 loop → T6/T7/T8; §2 matrix/refill/staging/1-per-day training → T1/T2/T6/T7; §3 cadence+locks → T3/T5; §4 tests/smoke/docs → per-task tests + T9/T10. Digest suppression → T4. Trainer path fix (discovered at plan-write) → T6. No spec item unowned.
2. **Placeholders:** none — every code step has concrete code; T2's rpartition caveat is an explicit implementer decision with both options specified, not a TBD.
3. **Type consistency:** `refill_queue` returns `list[Candidate]` consumed by `merge_save(paths.ledger, new)` ✓; `instance_lock` raises `TimeoutError` and T7 step 3 catches exactly that ✓; `suppress_empty_digest` kwarg name matches T4/T7 ✓; `net_eligible_decks` returns deck-path strings, T7 training picks `paths.root / deck` for `PerDeckNetTrainer(deck=...)` which T6's fix relativizes back ✓; weights filename `value_net_weights_{stem}.json` matches `trainers.py` `train()` output ✓.
4. **Landmark verification (pre-lock, per mandatory rule):** all cited lines re-read directly at HEAD this session — `evaluate_queued` evaluate.py:224; `run_cycle` cycle.py:104 (PAUSE :109); `HARD_DAILY_CAP` gate.py:13; `SubmissionCounter` gate.py:35; `decide` cadence default gate.py:132; `ledger_lock` candidates.py:118 (non-reentrancy note :199-201); `merge_save` candidates.py:179; `--cadence` default factory_cycle.py:28; ps1 registration :49-57; `Trainer` daemon.py:17-26; `run_daemon` daemon.py:53; `validate_deck` src/ptcg/decks/validate.py:22; `load_deck` src/ptcg/arena/runner.py:20; `PerDeckNetTrainer` trainers.py:31 (hardcoded deck path :82).
