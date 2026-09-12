"""Deck-matrix generator: curated seeds x deterministic legality-preserving
mutation rules (spec design 2). No RNG anywhere - same inputs, same decks."""
from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Callable

from cg.api import CardType, all_card_data


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


def _ranked(counter: Counter, *, count_eq: int | None = None,
            min_count: int = 1) -> list[int]:
    """All ids matching the filter, ordered by descending count then
    ascending id (deterministic, RNG-free)."""
    items = [(n, cid) for cid, n in counter.items()
             if (count_eq is None or n == count_eq) and n >= min_count]
    items.sort(key=lambda t: (-t[0], t[1]))
    return [cid for _, cid in items]


def _top(counter: Counter, *, count_eq: int | None = None,
         min_count: int = 1) -> int | None:
    """Highest-count id (ties -> lowest id, deterministic); optionally only
    among ids whose count == count_eq / >= min_count."""
    ranked = _ranked(counter, count_eq=count_eq, min_count=min_count)
    return ranked[0] if ranked else None


def _swap(deck: list[int], add_id: int, remove_id: int, n: int) -> list[int]:
    out = list(deck)
    for _ in range(n):
        out.remove(remove_id)
    out.extend([add_id] * n)
    return out


def _try_partners(deck: list[int], partner_counter: Counter, n: int, *,
                   fixed_id: int, fixed_is_add: bool) -> list[int] | None:
    """Iterate swap-partner candidates in descending-count order (ties ->
    lowest id), taking the FIRST partner whose mutated deck passes
    validate_deck. validate_deck is the eligibility oracle here rather than
    a hand-coded count/cap check, so this generalizes across both roles:
    `fixed_is_add=True` means `fixed_id` is added (n copies) and the
    partner is cut; `fixed_is_add=False` means `fixed_id` is cut and the
    partner is added. Blindly picking the single highest-count partner (the
    prior behavior) starves the matrix whenever that partner is already at
    the 4-per-name legality cap -- see MUTATION_RULES docstring below."""
    from ptcg.decks.validate import validate_deck
    for partner_id in _ranked(partner_counter):
        if fixed_is_add:
            partner_count = partner_counter[partner_id]
            if partner_count < n:
                continue
            out = _swap(deck, fixed_id, partner_id, n)
        else:
            out = _swap(deck, partner_id, fixed_id, n)
        if len(out) == 60 and not validate_deck(out):
            return out
    return None


def _energy_up2(deck: list[int]) -> list[int] | None:
    energy, _, trainer = _by_class(deck)
    add = _top(energy)
    if add is None:
        return None
    return _try_partners(deck, trainer, 2, fixed_id=add, fixed_is_add=True)


def _energy_down2(deck: list[int]) -> list[int] | None:
    energy, _, trainer = _by_class(deck)
    cut = _top(energy, min_count=3)
    if cut is None:
        return None
    return _try_partners(deck, trainer, 2, fixed_id=cut, fixed_is_add=False)


def _attacker_up1(deck: list[int]) -> list[int] | None:
    _, pokemon, trainer = _by_class(deck)
    add = _top(pokemon, count_eq=3)
    if add is None:
        return None
    return _try_partners(deck, trainer, 1, fixed_id=add, fixed_is_add=True)


def _attacker_down1(deck: list[int]) -> list[int] | None:
    _, pokemon, trainer = _by_class(deck)
    cut = _top(pokemon, count_eq=4)
    if cut is None:
        return None
    return _try_partners(deck, trainer, 1, fixed_id=cut, fixed_is_add=False)


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


# --- Matrix enumeration + staged refill (spec design 2, T2) ---------------

from ptcg.factory.candidates import Candidate, Status, next_version  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]

SEEDS: tuple[tuple[str, float], ...] = (
    ("src/ptcg/decks/candidates/mega-starmie-water.csv", 0.6),
    ("src/ptcg/decks/candidates/mega-starmie-water-density20.csv", 0.6),
    # 2026-07-20 weekly review: the ladder-best converged line ever (571.6 and
    # climbing on ~10+ refreshes, vs high-local-wr density20's ~525). Seeded so
    # future auto-refills generate 2nd-order mutations around it, not just the
    # mirror decks. Its own local_wr (0.553) is now the gate's beat-this bar.
    # Listed BEFORE the `-lean` seed on purpose: this deck IS `-lean` +
    # attacker-down1, so seeding it first lets the seed own the content hash
    # (matrix_decks dedups the redundant `-lean`->attacker-down1 variant).
    ("src/ptcg/decks/candidates/generated/mega-starmie-water-lean-attacker-down1.csv", 0.6),
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
    """Normalized (deck-path, agent-sig...) triples for ANY candidate
    status -- a candidate counts as "tested" the moment it's queued, not
    only once it's scored, so refill_queue never re-enqueues a cell that's
    already in flight."""
    return {(Path(c.deck).as_posix(), *_agent_sig(c.agent_kind, c.agent_config))
            for c in candidates}


# pytest's default python_functions glob ("test*") matches this name once
# it's imported into a test module's globals (`tested_...` starts with
# "test"), so pytest tries to collect it as a test function and errors on
# the missing `candidates` fixture. __test__ = False is the standard pytest
# escape hatch for a production callable that happens to look test-shaped.
tested_deck_agent_pairs.__test__ = False


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


def _split_variant_stem(stem: str) -> tuple[str, str] | None:
    """(seed_stem, rule) for a generated-variant filename stem, or None if
    `stem` doesn't end in a known rule suffix.

    A plain `stem.rpartition("-")` on the LAST hyphen is unsafe here: rule
    names themselves contain hyphens (`energy-up2`, `attacker-down1`) that
    collide with multi-word seed stems (`mega-starmie-water`) -- e.g.
    `"mega-starmie-water-energy-up2".rpartition("-")` splits at the hyphen
    between "energy" and "up2", recovering the bogus rule name "up2"
    instead of "energy-up2". Match against the known MUTATION_RULES keys
    explicitly instead (per task-2-brief.md implementer note)."""
    for rule in MUTATION_RULES:
        suffix = f"-{rule}"
        if stem.endswith(suffix):
            return stem[: -len(suffix)], rule
    return None


def refill_queue(candidates: list[Candidate], *, max_new: int = 10,
                 decks_dir: Path = GENERATED_DIR, log=print) -> list[Candidate]:
    tested = tested_deck_agent_pairs(candidates)
    cells: list[tuple[float, str, str, str, dict, bool, str]] = []
    # (priority, deck, name, agent_kind, agent_config, novel_axis, provenance)
    for deck_path, prio in matrix_decks(decks_dir=decks_dir):
        stem = Path(deck_path).stem
        is_variant = "/generated/" in Path(deck_path).as_posix()
        split = _split_variant_stem(stem) if is_variant else None
        seed_stem, rule = split if split is not None else (stem, "screen")
        cells.append((prio, deck_path, f"{stem}-heuristic", "heuristic", {},
                      is_variant, f"deck-matrix:{seed_stem}:{rule}"))
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
            notes="deck-matrix refill; deck_hash pending eval")
        working.append(cand)
        new.append(cand)
    if new:
        log(f"refill: enqueued {len(new)} matrix cell(s)")
    return new
