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
pk 7-8 / tr 24-33 / en 20-28. Flags fire only outside those observed-healthy
ranges -- EXCEPT mulligan-risk (basics_count), whose 2026-08-10 "pool range
3-4" calibration is superseded (2026-08-11): the pool rule now enforces
basics_count >= 8 by construction (validate_deck + the deck builder), so
that badge marks rule violations, not observed-range outliers. See
``MIN_BASIC_CARDS`` below.
"""
from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from cg.api import Attack, CardData, CardType, EnergyType, all_attack, all_card_data
from ptcg.decks.validate import MIN_BASIC_CARDS

#: Guards every call into the engine's `all_card_data()`/`all_attack()` DLL
#: entry points. The native buffer they read into is NOT thread-safe (an
#: 8-thread barrier probe against `ThreadingHTTPServer` produced 6x
#: JSONDecodeError plus inconsistent card counts, [1267, 1556], on an
#: unguarded cold cache -- whole-branch review Finding 1). `ui_pages`
#: funnels its own card-name lookup through `_card_db()` below rather than
#: calling `all_card_data()` directly, so this single lock is the one
#: choke point for both consumers.
_DLL_LOCK = threading.Lock()

_ENERGY_TYPES = (CardType.BASIC_ENERGY, CardType.SPECIAL_ENERGY)
_TRAINER_TYPES = (CardType.ITEM, CardType.TOOL, CardType.SUPPORTER, CardType.STADIUM)

#: energy_count > 30 -> red ("energy-heavy"). Junk sample deck: 35. Pool max: 28.
ENERGY_HEAVY_MAX = 30
#: energy_count < 5 -> red ("energy-starved"). Pool min: 20.
ENERGY_STARVED_MIN = 5
#: pokemon_count < 8 -> amber ("few-pokemon"). Pool range: 7-8.
POKEMON_MIN = 8
#: basics_count < MIN_BASIC_CARDS -> red ("mulligan-risk"). Recalibrated
#: 2026-08-11 from the old locally-owned BASICS_RED_MAX=2 (pool range 3-4)
#: to the pool rule's own MIN_BASIC_CARDS=8 -- single source of truth,
#: imported from ptcg.decks.validate rather than duplicated here.


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
    # lru_cache only serializes the cache dict itself -- two threads racing
    # a cold miss both still execute this body concurrently. _DLL_LOCK
    # serializes the actual native call so the two never overlap inside
    # the engine's shared buffer (Finding 1).
    with _DLL_LOCK:
        return {c.cardId: c for c in all_card_data()}


@lru_cache(maxsize=1)
def _attack_db() -> dict[int, Attack]:
    # attackIds are 1-based: ALWAYS resolve via this dict, never list index
    # (repo convention, see deck-findings memory).
    with _DLL_LOCK:
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


def safe_analyze(card_ids: list[int]) -> DeckQualityReport | None:
    """Failure-isolated entry point for UI rendering: ANY exception (unknown
    card id, engine hiccup, bad input shape) returns None instead of raising.
    Pages render an "analysis unavailable" badge for None -- a broken
    analysis must never break a page (spec Section 1)."""
    try:
        return analyze_deck(list(card_ids))
    except Exception:
        return None


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
    # mulligan_probability models the fixed 7-card opening draw from a
    # standard 60-card deck (see its docstring); using len(key) instead of
    # the default divides by zero for any non-60-card input (e.g. the small
    # fixture decks used to test caching below), so the default is used
    # unconditionally rather than the literal input length.
    mull = mulligan_probability(basics_count)

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
    if basics_count < MIN_BASIC_CARDS:
        flags.append(Flag("mulligan-risk", "red",
                          f"only {basics_count} Basic Pokemon (< {MIN_BASIC_CARDS}) -- "
                          f"mulligan {round(mull * 100)}%"))

    attacks = _attack_db()
    # NOTE: cg.utils.to_dataclass only recurses into nested dataclasses; a
    # scalar/list-of-int field annotated as EnergyType (card.energyType,
    # atk.energies elements) is left as a raw int by the engine binding.
    # `==` still works (IntEnum compares equal to int) but `.name` does not
    # -- wrap with EnergyType(...) wherever `.name` is needed.
    deck_energy_types = {EnergyType(db[cid].energyType) for cid in counts
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
            typed = {EnergyType(e) for e in atk.energies if e != EnergyType.COLORLESS}
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

    return DeckQualityReport(
        pokemon_count=pokemon_count,
        trainer_count=trainer_count,
        energy_count=energy_count,
        basics_count=basics_count,
        mulligan_pct=mull,
        flags=tuple(flags),
    )
