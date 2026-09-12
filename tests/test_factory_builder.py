from collections import Counter
from pathlib import Path

from cg.api import CardType, all_card_data

from ptcg.decks.validate import (
    MIN_BASIC_CARDS,
    attack_payability_problems,
    is_basic_pokemon,
    validate_deck,
)
from ptcg.factory import builder
from ptcg.factory.builder import (
    MIN_BASIC_POKEMON,
    _filler_basic_order,
    _pad_with_basics,
    build_deck,
    enumerate_concepts,
)


def test_enumerate_dedups_by_name_815_basic442_evo373():
    concepts = builder.enumerate_concepts()
    # 815 distinct attacker NAMES verified against the engine (442 basic + 373 evo).
    # Assert a stable band + the dedup property rather than a brittle == (the pool
    # may gain cards mid-competition per CLAUDE.md); log the exact count.
    names = [c.cores[0] for c in concepts if len(c.cores) == 1]
    assert len(names) == len(set(names)), "single-core concepts must be name-deduped"
    assert 800 <= len(names) <= 900, f"expected ~815 single-core concepts, got {len(names)}"
    print(f"SINGLE-CORE CONCEPTS: {len(names)}")


def test_build_deck_is_deterministic_byte_identical():
    concepts = builder.enumerate_concepts()
    c = next(x for x in concepts if len(x.cores) == 1)
    a = builder.build_deck(c)
    b = builder.build_deck(c)
    assert a.cards == b.cards and a.cards is not None


def test_built_deck_is_engine_legal_and_meets_brad_bounds():
    concepts = builder.enumerate_concepts()
    built = 0
    for c in concepts[:40]:
        r = builder.build_deck(c)
        if r.cards is None:
            assert r.unbuildable_reason  # never silently None
            continue
        assert validate_deck(r.cards) == [], f"{c.cores}: {validate_deck(r.cards)}"
        assert builder.brad_bounds_problems(r.cards) == [], (
            f"{c.cores}: {builder.brad_bounds_problems(r.cards)}"
        )
        built += 1
    assert built > 0


def test_two_core_build_deterministic_legal_or_reasoned():
    # Pair concepts (census-scope expansion, Brad 2026-07-23): the builder must
    # handle len(cores)==2 with the same determinism + legality + bounds contract.
    concepts = builder.enumerate_concepts()
    singles = [c.cores[0] for c in concepts if len(c.cores) == 1]
    pair = builder.Concept(cores=tuple(sorted((singles[0], singles[1]))))
    a = builder.build_deck(pair)
    b = builder.build_deck(pair)
    assert a.cards == b.cards  # deterministic (including deterministic unbuildable)
    if a.cards is not None:
        assert validate_deck(a.cards) == []
        assert builder.brad_bounds_problems(a.cards) == []
    else:
        assert a.unbuildable_reason


def test_golden_deck_pins_known_concept():
    # Golden-vector discipline: pin a byte-identical known-good output (global
    # CLAUDE.md serialization-guard rule). Fixture generated once in Step 3.
    #
    # unpayable-attack-pool-rule (2026-08-12), T2: the original golden core
    # ("Pikachu ex") is a genuine, INTENDED casualty of R2's 2-type cap --
    # its own best attack alone costs 3 distinct non-COLORLESS types
    # ([1, 4, 8]), so it is unbuildable under the new cap regardless of
    # fillers/chain (verified executably: build_deck returns
    # unbuildable_reason="needs 3 energy types (cap 2)"). Re-pinned to
    # "Abra" (a genuinely mono-payable-chain core), fixture regenerated
    # against the POST-change builder -- see task-2-report.md for the
    # before/after diff (chain/trainer/energy portions identical to the
    # pre-change build per R2(d); only the filler portion changed, by
    # design, per R3).
    fx = Path(__file__).parent / "fixtures" / "golden_deck_abra.csv"
    cores = ("Abra",)  # confirmed buildable single core (verified at impl time)
    r = builder.build_deck(builder.Concept(cores=cores))
    expected = [int(x) for x in fx.read_text(encoding="utf-8").split()]
    assert r.cards == expected


# --- min-basics-pool-rule (2026-08-11): single-source-of-truth + guarantee --


def test_min_basic_constant_is_single_sourced():
    # Fix round 1, Finding 2: `MIN_BASIC_POKEMON is MIN_BASIC_CARDS` has ZERO
    # fail-power -- CPython interns small ints (-5..256), so `8 is 8` is True
    # regardless of which literal defined it, and the prior version of this
    # test PASSED even before the single-source fix landed (see task-3-report
    # anomaly log). Assert against the actual AST instead: the
    # `MIN_BASIC_POKEMON = ...` assignment's RHS must be a bare `Name` node
    # referencing `MIN_BASIC_CARDS`, ruling out a regression to an
    # independent literal (`MIN_BASIC_POKEMON = 8`) -- verified to go RED
    # against a simulated-regression source string before being committed.
    import ast
    import inspect

    source = inspect.getsource(builder)
    tree = ast.parse(source)
    assignment = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "MIN_BASIC_POKEMON"
    )
    assert isinstance(assignment.value, ast.Name)
    assert assignment.value.id == "MIN_BASIC_CARDS"
    assert MIN_BASIC_POKEMON == MIN_BASIC_CARDS == 8


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
    # unbuildable branch: demand more filler copies than distinct names allow.
    # allowed_types=set() is irrelevant here -- every candidate is already
    # skipped by the used_names check before _filler_payable is even reached.
    cards: list[int] = []
    used = {c.name for c in _filler_basic_order()}  # every filler name burned
    assert _pad_with_basics(cards, used, 4, set()) is False


def test_two_core_concept_yields_at_least_8_basics_when_buildable():
    # Fix round 1, Finding 1: the single-core sweep above never exercises a
    # multi-basic-line (2-core) concept -- the only pre-existing 2-core
    # coverage (test_two_core_build_deterministic_legal_or_reasoned) never
    # asserts a basics count, so its outcome is incidental. Pick a
    # deterministic pair of cores whose evolution chains resolve to
    # DIFFERENT Basic ancestors (genuinely two candidate basic lines, not
    # one collapsed by build_deck's own used_names dedup for a shared
    # ancestor) and assert the >=8 guarantee explicitly.
    #
    # unpayable-attack-pool-rule (2026-08-12), T2: also require the pair to
    # actually BUILD -- R2's 2-type cap legitimately makes SOME
    # distinct-ancestor pairs unbuildable (e.g. the original i=0/j=1 pick,
    # `('Abomasnow', 'Aipom Mega ... ')`-adjacent combos, can exceed 2
    # combined energy types), which is orthogonal to the >=8-basics
    # guarantee this test exists to check. Skip unbuildable pairs rather
    # than asserting on one; the test still exercises a real multi-line
    # buildable concept, per its own intent.
    concepts = enumerate_concepts()
    singles = [c.cores[0] for c in concepts if len(c.cores) == 1]
    pair_cores = None
    scan_limit = min(80, len(singles))
    for i in range(scan_limit):
        chain_i = builder._stage_chain(singles[i])
        if chain_i is None:
            continue
        for j in range(i + 1, scan_limit):
            chain_j = builder._stage_chain(singles[j])
            if chain_j is None:
                continue
            if chain_i[0].name == chain_j[0].name:
                continue
            candidate = builder.Concept(cores=tuple(sorted((singles[i], singles[j]))))
            if build_deck(candidate).cards is None:
                continue  # e.g. R2 2-type cap -- try the next distinct-ancestor pair
            pair_cores = (singles[i], singles[j])
            break
        if pair_cores is not None:
            break
    assert pair_cores is not None, (
        "expected a distinct-ancestor, buildable pair in the first 80 concepts"
    )

    concept = builder.Concept(cores=tuple(sorted(pair_cores)))
    r = build_deck(concept)
    assert r.cards is not None, r.unbuildable_reason
    assert _basics(r.cards) >= MIN_BASIC_CARDS
    assert validate_deck(r.cards) == []


def test_zero_basic_line_concepts_are_structurally_unreachable():
    # Fix round 1, Finding 1 (0-basic-line reachability): a concept whose
    # core chain(s) contribute ZERO basic-Pokemon copies cannot occur for
    # any concept build_deck actually returns cards for. `_stage_chain`
    # walks `evolvesFrom` upward and only terminates (`break`) at
    # `card.basic`, so every resolved chain's FIRST element is guaranteed
    # Basic -- contributing >=1 stage's worth of copies before padding even
    # runs. A core whose chain has no resolvable Basic ancestor makes
    # build_deck return `unbuildable_reason` instead of a 0-basic deck, so
    # the 0-basic-line case is structurally impossible on the buildable
    # path, not merely untested. Verified below across a broad, real,
    # both-single-and-pair sample (not asserted as a bare claim).
    concepts = enumerate_concepts()[::16]
    singles = [c.cores[0] for c in concepts if len(c.cores) == 1]
    if len(singles) >= 2:
        concepts = concepts + [builder.Concept(cores=tuple(sorted((singles[0], singles[1])))),
                                builder.Concept(cores=tuple(sorted((singles[2], singles[3]))))]
    checked = 0
    for c in concepts:
        r = build_deck(c)
        if r.cards is None:
            continue
        for core_name in c.cores:
            chain = builder._stage_chain(core_name)
            assert chain is not None and chain[0].basic, (
                f"{core_name}: chain must resolve to a Basic ancestor for "
                "build_deck to have returned cards"
            )
            checked += 1
    assert checked >= 30  # must exercise real successes, not an empty sweep


# --- unpayable-attack-pool-rule (2026-08-12), T2: multi-type energy + -----
# --- payability-aware fillers ---------------------------------------------

# GOLDEN generated from PRE-CHANGE build_deck at commit 55143e0 ("docs:
# implementation plan for unpayable-attack pool rule", the tip of the
# branch before any T2 edit), via a throwaway probe script that scanned
# enumerate_concepts() for the first single-core concept whose FULL
# CHAIN-wide non-COLORLESS attack type set is a single type (walking every
# card in the chain, every attack, not just the core's own best attack --
# the R2 correction). Command run (verbatim, from repo root):
#   uv run python find_golden3.py / find_golden4.py  (scratch scripts, not
#   committed) -> "First mono-payable-chain core (any filler state):
#   ('Abra', {5})"; concept=Abra: build_deck(Concept(cores=("Abra",))) ->
#   cards below (len 60, unbuildable_reason=None). Full derivation +
#   command transcript recorded in task-2-report.md.
GOLDEN_MONO_CARDS = [
    5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5,
    22, 22, 22, 22, 24, 24, 24, 24, 25, 25, 25, 25, 27, 27, 27, 27,
    28, 28, 28, 28, 31, 31, 31, 31, 33, 33, 33,
    109, 109, 109, 109,
    1082, 1097, 1097, 1097, 1097, 1121, 1121, 1121, 1121,
    1213, 1213, 1213, 1213, 1224, 1224, 1224, 1224,
]

# Portions of GOLDEN_MONO_CARDS that R2/R3 do NOT touch for a genuinely
# mono-payable-chain core: the chain itself (Abra, cardId 109), the fixed
# trainer skeleton (cardIds 1082/1097/1121/1213/1224), and the energy
# allocation (cardId 5 x12) -- this last one IS the literal R2(d) load-
# bearing invariant ("mono-payable cores produce bit-identical energy
# allocation to today"; hand-verified: Abra's chain-wide secondary_max is
# {} post-change, so secondary_total=0, energy_total-0=energy_total
# unchanged, and _energy_allocation(primary_needs, energy_total) is called
# with the SAME arguments as the pre-change `type_needs` call -- same
# deterministic function, same inputs, same output).
#
# PLAN-DRIFT (flagged per binding constraints): the brief's Step-1 example
# code asserts `result.cards == GOLDEN_MONO_CARDS` (the WHOLE 60-card
# list), and the spec's Acceptance Criteria line says "mono-payable core ->
# bit-identical deck". Neither can hold once R3 (payability-aware fillers,
# also in THIS task's scope) is implemented: an exhaustive sweep of ALL 815
# single-core concepts' pre-change build_deck output (see task-2-report.md)
# found ZERO concepts whose full 60-card deck already passes
# attack_payability_problems([])==[] -- every concept's deterministic,
# type-BLIND filler pick includes at least one off-type Pokemon (confirmed
# for Abra itself: Hippopotas/Pinsir/Iron Leaves/Poltchageist/Chi-Yu/Froakie
# are all off-type fillers in GOLDEN_MONO_CARDS, cardIds 22/25/27/28/31/33).
# So whole-deck bit-identity is unsatisfiable BY CONSTRUCTION for any
# concept, for any implementation that also ships R3 in the same change --
# R2(d)'s own precise wording ("bit-identical ENERGY ALLOCATION") is the
# authoritative, satisfiable invariant, and that is what this test proves.
# cardId 24 (Team Rocket's Kangaskhan ex, already payable pre-change) is
# excluded from this non-filler set even though it happens to survive into
# the post-change filler pick too -- it is genuinely a filler card, its
# survival is incidental to _filler_basic_order()'s deterministic scan
# order, not a structural guarantee.
_GOLDEN_NON_FILLER_IDS = {5, 109, 1082, 1097, 1121, 1213, 1224}


def test_mono_payable_core_bit_identical_golden():
    """R2(d): mono-payable cores (chain-wide non-COLORLESS attack type set
    is a single type) produce a BIT-IDENTICAL energy allocation after the
    multi-type change -- verified here by also checking the chain and
    trainer-skeleton portions (structurally untouched by R2/R3) stay
    bit-identical, per GOLDEN_MONO_CARDS above. Filler-portion drift is
    EXPECTED (R3, in scope for this same task) and deliberately NOT
    asserted -- see the plan-drift note above GOLDEN_MONO_CARDS."""
    result = build_deck(builder.Concept(cores=("Abra",)))
    assert result.cards is not None, result.unbuildable_reason

    got = Counter(result.cards)
    golden = Counter(GOLDEN_MONO_CARDS)
    for cid in _GOLDEN_NON_FILLER_IDS:
        assert got[cid] == golden[cid], (
            f"cardId {cid} count changed: {golden[cid]} -> {got[cid]} "
            "(chain/trainer/energy portions must stay bit-identical for a "
            "mono-payable-chain core per R2(d))"
        )


def _find_two_type_core() -> builder.Concept:
    """First single-core concept whose actual built deck uses exactly 2
    distinct energy card types (derived executably, not hardcoded --
    verify-game-data-claims discipline)."""
    db = {c.cardId: c for c in all_card_data()}
    for c in enumerate_concepts():
        r = build_deck(c)
        if r.cards is None:
            continue
        types = {
            int(db[cid].energyType)
            for cid in r.cards
            if db[cid].cardType in (CardType.BASIC_ENERGY, CardType.SPECIAL_ENERGY)
        }
        if len(types) == 2:
            return c
    raise AssertionError("expected at least one 2-type-energy concept in the pool")


def test_two_type_core_splash_is_payable_and_primary_majority():
    """R2: a core whose CHAIN has a secondary attack typed outside the best
    attack's own type builds fully payable, with total energy count
    unchanged and the primary type keeping the majority over the secondary
    splash. Derived executably via `_find_two_type_core` (first hit is
    'Abomasnow': its own best attack is Colorless-only and falls back to
    DEFAULT_FILLER_ENERGY_TYPE=FIGHTING, but Snover/Abomasnow's OTHER
    attacks need Grass -- a genuine 2-type case only visible once the
    FULL chain is walked, not just the core's best attack)."""
    concept = _find_two_type_core()
    result = build_deck(concept)
    assert result.cards is not None, result.unbuildable_reason
    assert attack_payability_problems(result.cards) == []

    db = {c.cardId: c for c in all_card_data()}
    energy_counts = Counter(
        int(db[cid].energyType)
        for cid in result.cards
        if db[cid].cardType in (CardType.BASIC_ENERGY, CardType.SPECIAL_ENERGY)
    )
    assert sum(energy_counts.values()) == builder.SINGLE_ENERGY_TOTAL
    assert len(energy_counts) == 2, energy_counts
    primary_n = max(energy_counts.values())
    secondary_n = sum(energy_counts.values()) - primary_n
    assert primary_n > secondary_n, energy_counts


def test_two_type_build_is_deterministic():
    """R2/R3 determinism: two builds of the same multi-type concept produce
    byte-identical output."""
    concept = _find_two_type_core()
    a = build_deck(concept)
    b = build_deck(concept)
    assert a.cards == b.cards and a.cards is not None
    # not just a self-comparison: the (shared) result must actually BE the
    # invariant -- payable and legal -- not merely equal to itself.
    assert attack_payability_problems(a.cards) == []
    assert validate_deck(a.cards) == []


def test_all_built_decks_pass_attack_payability():
    """R3: every deck build_deck actually returns cards for (a deterministic
    sample spanning the pool, same convention as
    test_every_buildable_concept_yields_at_least_8_basics) is fully
    attack-payable -- the payability-aware filler filter must never let an
    off-type filler through."""
    concepts = enumerate_concepts()[::16]
    built = 0
    for c in concepts:
        r = build_deck(c)
        if r.cards is None:
            continue
        built += 1
        assert attack_payability_problems(r.cards) == [], c.cores
    assert built >= 30  # the sample must exercise real successes


def test_more_than_two_energy_types_is_capped():
    """R2 cap: a core needing more than 2 distinct energy types (primary +
    chain-wide secondary) is unbuildable, with a reason naming the cap.
    Derived executably: scan enumerate_concepts() for the first concept
    whose unbuildable_reason names the cap (never hardcode the concept
    name -- verify-game-data-claims discipline)."""
    capped_reason = None
    for c in enumerate_concepts():
        r = build_deck(c)
        if r.cards is None and r.unbuildable_reason and "cap" in r.unbuildable_reason.lower():
            capped_reason = r.unbuildable_reason
            break
    assert capped_reason is not None, "expected at least one >2-energy-type concept in the pool"
    assert "energy types" in capped_reason and "cap 2" in capped_reason


# --- composition_counts (census/screening regime slice, spec §1) ---------
import pytest

from ptcg.factory.builder import brad_bounds_problems, composition_counts

_ANCHOR_CSV = (
    Path(__file__).resolve().parents[1]
    / "src" / "ptcg" / "decks" / "candidates" / "anchor-min8.csv"
)


def _anchor_cards() -> list[int]:
    return [
        int(line)
        for line in _ANCHOR_CSV.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_composition_counts_anchor_min8_golden():
    """Golden pin, verified by executing the count against the engine card
    DB at plan time (2026-08-13): anchor-min8.csv is 22 energy / 12
    Pokémon / 26 trainers (60 - 22 - 12) / 8 basics."""
    cards = _anchor_cards()
    energy, pokemon = composition_counts(cards)
    assert (energy, pokemon) == (22, 12)
    assert len(cards) - energy - pokemon == 26  # trainers are derivable, not stored


def test_composition_counts_unknown_id_raises():
    with pytest.raises(ValueError, match="unknown card ids"):
        composition_counts([999_999])


def test_composition_counts_agrees_with_brad_bounds_classification():
    """Same classification as brad_bounds_problems (spec §1): the anchor
    deck's 22 energy exceeds the 20-energy construction bound, so the
    bound flags it exactly when composition_counts counts >20."""
    cards = _anchor_cards()
    energy, _ = composition_counts(cards)
    problems = brad_bounds_problems(cards)
    assert any("energy" in p for p in problems) == (energy > 20)
