from collections import Counter
from pathlib import Path

from cg.api import CardType, EnergyType, all_attack, all_card_data

from ptcg.decks.validate import MIN_BASIC_CARDS, attack_payability_problems, is_basic_pokemon, validate_deck
from ptcg.factory.builder import Concept, TRAINER_SKELETON, _card_by_name, _deck_energy_plan, _stage_chain, build_deck
from ptcg.factory.deck_repair import _card_db, repair_deck


def _cards_from_csv(rel: str) -> list[int]:
    return [int(x) for x in Path(rel).read_text(encoding="utf-8").split()]


# --- fixture (d): already-payable real deck. NOTE: intentionally NOT one of
# the anchor-cand-{b,c,d} CSVs -- this task repairs those files for real as
# part of its scope (T3 additional scope), so a fixture reading them at
# import time would observe the POST-repair (already-payable) state and the
# offender-partition tests below would spuriously see zero problems. Use the
# untouched anchor-min8.csv instead (already payable per test_validate.py).
ALREADY_PAYABLE_DECK = _cards_from_csv("src/ptcg/decks/candidates/anchor-min8.csv")


def _filler_offender_fixture() -> list[int]:
    """Take ALREADY_PAYABLE_DECK (single energy type, MIN_BASIC_CARDS-exact)
    and swap out its one swappable filler basic (Mega Zygarde ex -- basic,
    nothing in this deck evolves from it, found executably below rather
    than assumed) for an off-type basic Pokemon (also found executably, per
    verify-game-data-claims) not already in the deck. Produces exactly one
    swappable offender, independent of the mutable anchor-cand-* CSVs."""
    deck = list(ALREADY_PAYABLE_DECK)
    db = {c.cardId: c for c in all_card_data()}
    colorless = int(EnergyType.COLORLESS)
    attacks_by_id = {a.attackId: a for a in all_attack()}
    energy_types = {
        int(db[c].energyType)
        for c in deck
        if db[c].cardType in (CardType.BASIC_ENERGY, CardType.SPECIAL_ENERGY)
    }
    deck_names = {db[c].name for c in deck}

    def _is_filler(cid: int) -> bool:
        card = db[cid]
        if card.cardType != CardType.POKEMON or not card.basic:
            return False
        return not any(
            db[other].evolvesFrom == card.name for other in set(deck) if other in db
        )

    filler_cid = next(cid for cid in sorted(set(deck)) if _is_filler(cid))

    off_type_basic = None
    for card in sorted(all_card_data(), key=lambda c: c.cardId):
        if card.cardType != CardType.POKEMON or not card.basic:
            continue
        if card.name in deck_names:
            continue
        for attack_id in card.attacks:
            atk = attacks_by_id.get(attack_id)
            if atk is None:
                continue
            missing = {int(e) for e in atk.energies if int(e) != colorless} - energy_types
            if missing:
                off_type_basic = card
                break
        if off_type_basic is not None:
            break
    assert off_type_basic is not None, "no off-type basic Pokemon found"

    return [off_type_basic.cardId if c == filler_cid else c for c in deck]


FILLER_ONLY_DECK = _filler_offender_fixture()


def _find_two_type_chain_concept() -> Concept:
    """First single-core concept (scanned executably, deterministic pool
    order) whose evolution chain needs exactly 2 energy types total (one
    primary, one splash with max single-attack count >= 2) AND whose
    build_deck() output is legal+payable -- i.e. a real chain-bound
    2nd-type case we can strip the splash from to make it unpayable."""
    for name in sorted(_card_by_name()):
        chain = _stage_chain(name)
        if chain is None or len(chain) < 2 or chain[-1].name != name:
            continue
        plan = _deck_energy_plan([chain])
        if isinstance(plan, str) or not plan[1]:
            continue
        primary_needs, secondary_max = plan
        if len(set(primary_needs) | set(secondary_max)) != 2:
            continue
        if max(secondary_max.values()) < 2:
            continue
        concept = Concept(cores=(name,))
        result = build_deck(concept)
        if result.cards is None:
            continue
        if attack_payability_problems(result.cards) != []:
            continue
        return concept
    raise AssertionError("no 2-energy-type chain concept found in the pool")


def _chain_bound_two_type_fixture() -> tuple[list[int], str, int, int]:
    """Real chain-bound deck with its splash energy stripped: only the
    primary energy type remains, so the evolved core's own attack becomes
    unpayable (chain-bound: it's non-basic, so it's automatically NOT a
    filler). Returns (mutated_deck, missing_type_name, missing_type,
    expected_k)."""
    concept = _find_two_type_chain_concept()
    chain = _stage_chain(concept.cores[0])
    assert chain is not None
    plan = _deck_energy_plan([chain])
    assert not isinstance(plan, str)
    primary_needs, secondary_max = plan
    primary_type = next(iter(set(primary_needs)))
    missing_type = next(iter(secondary_max))
    k = secondary_max[missing_type]

    built = build_deck(concept)
    assert built.cards is not None
    db = {c.cardId: c for c in all_card_data()}
    energy_by_type = {}
    for c in all_card_data():
        if c.cardType == CardType.BASIC_ENERGY:
            energy_by_type[int(c.energyType)] = c
    primary_card = energy_by_type[primary_type]

    mutated: list[int] = []
    stripped = 0
    for cid in built.cards:
        card = db[cid]
        if (
            card.cardType == CardType.BASIC_ENERGY
            and int(card.energyType) == missing_type
        ):
            mutated.append(primary_card.cardId)
            stripped += 1
        else:
            mutated.append(cid)
    assert stripped == k, f"expected to strip {k} splash energy, stripped {stripped}"
    return mutated, energy_by_type[missing_type].name, missing_type, k


def _reordered_collision_fixture() -> tuple[list[int], int, str, int, int, int, int]:
    """Regression fixture for a review finding on the conversion loop's
    cardType guard (`_is_most_common_energy`, deck_repair.py): a non-energy
    card can coincidentally share its `energyType` field's int value with a
    real basic-energy type (verified: 'Hippopotas'.energyType == 6, the
    same value as Basic {F} Energy's type). `_chain_bound_two_type_fixture`
    already contains such a collider as a filler, but `build_deck`'s own
    final `cards.sort()` always places low-cardId (1-8) real energy cards
    ahead of any higher-cardId Pokemon in list order -- so even an
    UNGUARDED conversion loop happens to hit genuine energy cards first by
    positional luck, giving that fixture zero fail-power against a
    regression of the guard. Here we take the SAME real fixture and move
    the collider's copies to the FRONT of the list, ahead of the genuine
    energy cards, so an unguarded loop would incorrectly "convert" the
    collider instead. Returns (reordered_deck, collider_cid, collider_name,
    collider_count_before, primary_type, missing_type, k)."""
    mutated, _missing_name, missing_type, k = _chain_bound_two_type_fixture()
    db = {c.cardId: c for c in all_card_data()}
    primary_type = next(iter({
        int(db[c].energyType) for c in mutated if db[c].cardType == CardType.BASIC_ENERGY
    }))
    collider_cid = next(
        cid
        for cid in sorted(set(mutated))
        if db[cid].cardType not in (CardType.BASIC_ENERGY, CardType.SPECIAL_ENERGY)
        and int(db[cid].energyType) == primary_type
    )
    collider_count = mutated.count(collider_cid)
    collider_copies = [c for c in mutated if c == collider_cid]
    rest = [c for c in mutated if c != collider_cid]
    reordered = collider_copies + rest
    return (
        reordered,
        collider_cid,
        db[collider_cid].name,
        collider_count,
        primary_type,
        missing_type,
        k,
    )


def _three_type_fixture() -> list[int]:
    """Combine two REAL, independently-verified 2-type chain-bound concepts
    that share a common primary type but need DIFFERENT secondary types,
    then supply only the primary energy -- both evolved cores become
    chain-bound offenders demanding two distinct missing types, pushing the
    total past the 2-type cap. Derived executably, not hardcoded."""
    seen_primary: dict[int, str] = {}
    candidates: list[tuple[str, int, int]] = []  # (core_name, primary_type, secondary_type)
    for name in sorted(_card_by_name()):
        chain = _stage_chain(name)
        if chain is None or len(chain) < 2 or chain[-1].name != name:
            continue
        plan = _deck_energy_plan([chain])
        if isinstance(plan, str) or not plan[1]:
            continue
        primary_needs, secondary_max = plan
        if len(set(primary_needs) | set(secondary_max)) != 2:
            continue
        primary_type = next(iter(set(primary_needs)))
        secondary_type = next(iter(secondary_max))
        result = build_deck(Concept(cores=(name,)))
        if result.cards is None or attack_payability_problems(result.cards) != []:
            continue
        candidates.append((name, primary_type, secondary_type))

    by_primary: dict[int, list[tuple[str, int, int]]] = {}
    for name, ptype, stype in candidates:
        by_primary.setdefault(ptype, []).append((name, ptype, stype))

    chosen: tuple[str, str, int, int] | None = None
    for ptype, group in by_primary.items():
        stypes = {stype for _, _, stype in group}
        if len(stypes) < 2:
            continue
        s_iter = iter(sorted(stypes))
        s1 = next(s_iter)
        s2 = next(s_iter)
        name1 = next(n for n, _, s in group if s == s1)
        name2 = next(n for n, _, s in group if s == s2)
        chosen = (name1, name2, ptype, s1)
        break
    assert chosen is not None, "no pair of chain concepts sharing a primary type found"
    name1, name2, primary_type, _ = chosen

    chain1 = _stage_chain(name1)
    chain2 = _stage_chain(name2)
    assert chain1 is not None and chain2 is not None

    by_name = _card_by_name()
    cards: list[int] = []
    for chain in (chain1, chain2):
        for stage in chain:
            cards += [stage.cardId] * 4
    for trainer_name, n in TRAINER_SKELETON:
        cards += [by_name[trainer_name].cardId] * n

    energy_card = next(
        c
        for c in all_card_data()
        if c.cardType == CardType.BASIC_ENERGY and int(c.energyType) == primary_type
    )
    remaining = 60 - len(cards)
    assert remaining > 0
    cards += [energy_card.cardId] * remaining
    assert len(cards) == 60
    return cards


def test_filler_only_offender_is_repaired():
    problems_before = attack_payability_problems(FILLER_ONLY_DECK)
    assert problems_before != []
    offending_names = {p.split("'")[1] for p in problems_before}

    result = repair_deck(FILLER_ONLY_DECK)
    assert result is not None
    repaired, summary = result

    assert len(repaired) == 60
    assert sum(1 for c in repaired if is_basic_pokemon(c)) >= MIN_BASIC_CARDS
    assert attack_payability_problems(repaired) == []
    assert validate_deck(repaired) == []
    assert "swapped" in summary

    # non-offender cards (core chain, trainers, energy) are byte-count
    # untouched -- only the offending filler names left the deck.
    db = {c.cardId: c for c in all_card_data()}
    before_counts = Counter(db[c].name for c in FILLER_ONLY_DECK)
    after_counts = Counter(db[c].name for c in repaired)
    for name, count in before_counts.items():
        if name in offending_names:
            continue
        assert after_counts.get(name) == count, f"non-offender '{name}' count changed"


def test_chain_bound_two_type_case_is_repaired():
    mutated, missing_name, missing_type, expected_k = _chain_bound_two_type_fixture()
    problems_before = attack_payability_problems(mutated)
    assert problems_before != []

    result = repair_deck(mutated)
    assert result is not None
    repaired, summary = result

    assert len(repaired) == 60
    assert attack_payability_problems(repaired) == []
    assert validate_deck(repaired) == []
    assert "converted" in summary
    assert missing_name in summary

    db = {c.cardId: c for c in all_card_data()}
    energy_after = sum(
        1
        for c in repaired
        if db[c].cardType == CardType.BASIC_ENERGY and int(db[c].energyType) == missing_type
    )
    assert energy_after == expected_k

    total_energy_before = sum(1 for c in mutated if db[c].cardType == CardType.BASIC_ENERGY)
    total_energy_after = sum(1 for c in repaired if db[c].cardType == CardType.BASIC_ENERGY)
    assert total_energy_after == total_energy_before  # count constant, per spec


def test_energy_conversion_ignores_colliding_nonenergy_card():
    """Regression for the Task-3 review finding: the conversion loop must
    require cardType in (BASIC_ENERGY, SPECIAL_ENERGY), not just a matching
    `energyType` int value, when selecting which cards to convert. Uses
    `_reordered_collision_fixture` (a real non-energy card whose energyType
    coincidentally matches the deck's most-common type, positioned BEFORE
    the genuine energy cards in list order) so an unguarded loop would
    wrongly consume the collider's copies instead of real energy cards."""
    (
        reordered,
        collider_cid,
        collider_name,
        collider_count_before,
        primary_type,
        missing_type,
        k,
    ) = _reordered_collision_fixture()
    assert attack_payability_problems(reordered) != []

    result = repair_deck(reordered)
    assert result is not None
    repaired, _ = result

    db = {c.cardId: c for c in all_card_data()}
    assert repaired.count(collider_cid) == collider_count_before, (
        f"'{collider_name}' (non-offending, non-energy, but its energyType "
        "field coincidentally matches the deck's most-common type) must be "
        "left untouched by the energy-conversion step"
    )

    energy_before = Counter(
        int(db[c].energyType) for c in reordered if db[c].cardType == CardType.BASIC_ENERGY
    )
    energy_after = Counter(
        int(db[c].energyType) for c in repaired if db[c].cardType == CardType.BASIC_ENERGY
    )
    assert sum(energy_after.values()) == sum(energy_before.values())  # count constant
    assert energy_after[missing_type] == k
    assert energy_after[primary_type] == energy_before[primary_type] - k


def test_three_type_case_returns_none():
    deck = _three_type_fixture()
    assert attack_payability_problems(deck) != []
    assert repair_deck(deck) is None


def test_already_payable_deck_is_unchanged():
    assert attack_payability_problems(ALREADY_PAYABLE_DECK) == []
    result = repair_deck(ALREADY_PAYABLE_DECK)
    assert result is not None
    repaired, summary = result
    assert repaired == ALREADY_PAYABLE_DECK
    assert summary == "already-payable"


def test_repair_is_deterministic():
    result1 = repair_deck(list(FILLER_ONLY_DECK))
    result2 = repair_deck(list(FILLER_ONLY_DECK))
    assert result1 == result2
    # not just a self-comparison: both results must actually BE the invariant
    # (payable + legal), not merely equal to each other.
    assert result1 is not None
    assert attack_payability_problems(result1[0]) == []
    assert validate_deck(result1[0]) == []


def test_repair_is_idempotent():
    result = repair_deck(list(FILLER_ONLY_DECK))
    assert result is not None
    repaired, _ = result
    result2 = repair_deck(repaired)
    assert result2 is not None
    assert result2[0] == repaired
    assert attack_payability_problems(repaired) == []
    assert validate_deck(repaired) == []


def test_card_db_is_cached():
    """Pin regression (Pass-2 fix round): `_card_db` must be `@lru_cache`d,
    matching the sibling pattern in validate.py -- a plain rebuild-every-call
    function has no `cache_info`, so this fails loudly if the decorator is
    ever silently dropped again."""
    assert hasattr(_card_db, "cache_info"), "_card_db must be @lru_cache-decorated"
    _card_db.cache_clear()
    _card_db()
    _card_db()
    info = _card_db.cache_info()
    assert info.hits >= 1
    assert info.currsize == 1
