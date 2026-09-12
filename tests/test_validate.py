from pathlib import Path

from cg.api import CardType, EnergyType, all_attack, all_card_data

from ptcg.decks.validate import attack_payability_problems, validate_deck


def _payable_with(card, energy_type, attacks):
    """True if every attack of `card` needs only COLORLESS or `energy_type`
    (so a single-type basic-energy deck can pay for it)."""
    colorless = int(EnergyType.COLORLESS)
    for attack_id in card.attacks:
        atk = attacks.get(attack_id)
        if atk is None:
            continue
        typed = {int(e) for e in atk.energies if int(e) != colorless}
        if typed - {energy_type}:
            return False
    return True


def _db():
    """Two DISTINCT basic Pokémon species + one basic-energy card such that
    both basics are payable purely from that energy's type (4-copy name cap
    forces >=2 lines for 8 basics; payability keeps callers of validate_deck
    green now that attack_payability_problems is part of it -- found
    executably, never hardcoded, per verify-game-data-claims)."""
    cards = all_card_data()
    attacks = {a.attackId: a for a in all_attack()}
    basics = [c for c in cards if c.cardType == CardType.POKEMON and c.basic]
    energies = [c for c in cards if c.cardType == CardType.BASIC_ENERGY]
    for energy in energies:
        etype = int(energy.energyType)
        distinct: list = []
        seen_names: set[str] = set()
        for card in basics:
            if card.name in seen_names:
                continue
            if _payable_with(card, etype, attacks):
                seen_names.add(card.name)
                distinct.append(card)
            if len(distinct) >= 2:
                break
        if len(distinct) >= 2:
            return distinct[0], distinct[1], energy
    raise AssertionError(
        "no basic-energy type with >=2 payable distinct basic Pokemon found"
    )


def _legal_deck():
    a, b, energy = _db()
    # 4 + 4 + 52 = 60 cards, 8 basic-Pokémon copies (hand-verified arithmetic)
    return [a.cardId] * 4 + [b.cardId] * 4 + [energy.cardId] * 52


def test_legal_deck_passes():
    assert validate_deck(_legal_deck()) == []


def test_wrong_size_fails():
    assert any("60" in v for v in validate_deck(_legal_deck()[:59]))


def test_unknown_id_fails():
    deck = _legal_deck()
    deck[0] = 999999
    assert any("unknown" in v.lower() for v in validate_deck(deck))


def test_five_copies_fails():
    pokemon, _, energy = _db()
    deck = [pokemon.cardId] * 5 + [energy.cardId] * 55
    assert any("4" in v for v in validate_deck(deck))


def test_no_basic_pokemon_fails():
    _, _, energy = _db()
    deck = [energy.cardId] * 60
    assert any("basic" in v.lower() for v in validate_deck(deck))


def _distinct_ace_specs():
    cards = all_card_data()
    ace_specs = [c for c in cards if c.aceSpec]
    seen_names: set[str] = set()
    distinct = []
    for c in ace_specs:
        if c.name not in seen_names:
            seen_names.add(c.name)
            distinct.append(c)
    return distinct


def test_two_ace_specs_fails():
    pokemon, _, energy = _db()
    distinct = _distinct_ace_specs()
    if len(distinct) >= 2:
        ace1, ace2 = distinct[0], distinct[1]
    else:
        # Fewer than 2 distinct ACE SPEC cards in the pool: fall back to two
        # copies of one, which also trips the >4-copies-of-a-name rule at
        # count 2? No—2 copies is fine there, so only the ACE SPEC rule fires.
        ace1 = ace2 = distinct[0]
    deck = (
        [ace1.cardId, ace2.cardId]
        + [pokemon.cardId] * 4
        + [energy.cardId] * (60 - 2 - 4)
    )
    problems = validate_deck(deck)
    assert any("ACE SPEC" in v for v in problems)


def test_one_ace_spec_passes():
    pokemon_a, pokemon_b, energy = _db()
    ace = _distinct_ace_specs()[0]
    # 1 + 4 + 4 + 51 = 60 cards, 8 basic-Pokémon copies (two distinct lines)
    # so this deck clears MIN_BASIC_CARDS while staying otherwise minimal.
    deck = (
        [ace.cardId]
        + [pokemon_a.cardId] * 4
        + [pokemon_b.cardId] * 4
        + [energy.cardId] * (60 - 1 - 4 - 4)
    )
    problems = validate_deck(deck)
    assert problems == []
    assert not any("ACE SPEC" in v for v in problems)


def test_five_special_energy_fails():
    pokemon, _, energy = _db()
    cards = all_card_data()
    special_energy = next(
        c for c in cards if c.cardType == CardType.SPECIAL_ENERGY and not c.aceSpec
    )
    deck = (
        [special_energy.cardId] * 5
        + [pokemon.cardId] * 4
        + [energy.cardId] * (60 - 5 - 4)
    )
    problems = validate_deck(deck)
    # This deck also has only 4 Basic Pokemon (< MIN_BASIC_CARDS=8), which
    # emits "fewer than 8 Basic Pokemon cards (4)" -- a bare `"4" in v` check
    # matches that message's count too, so it would pass even if the
    # copies-cap rule were deleted. Assert the copies-cap message itself.
    assert any("more than 4 copies of" in v for v in problems)


def test_min_basics_seven_fails():
    a, b, energy = _db()
    deck = [a.cardId] * 4 + [b.cardId] * 3 + [energy.cardId] * 53  # 7 basics
    assert any("fewer than 8 Basic" in p for p in validate_deck(deck))


def test_min_basics_eight_passes():
    assert not any("fewer than 8" in p for p in validate_deck(_legal_deck()))


def _cards_from_csv(rel):
    return [int(x) for x in Path(rel).read_text(encoding="utf-8").split()]


def _energy_types_of(deck):
    cards = {c.cardId: c for c in all_card_data()}
    return {
        int(cards[cid].energyType)
        for cid in deck
        if cards[cid].cardType in (CardType.BASIC_ENERGY, CardType.SPECIAL_ENERGY)
    }


def _find_offtype_basic_for(deck):
    """First basic Pokémon (scanned executably from all_card_data()/
    all_attack()) with at least one attack whose non-COLORLESS cost
    includes a type the deck does not run — swapping it in trips the rule."""
    cards = all_card_data()
    attacks = {a.attackId: a for a in all_attack()}
    deck_types = _energy_types_of(deck)
    colorless = int(EnergyType.COLORLESS)
    for card in cards:
        if card.cardType != CardType.POKEMON or not card.basic:
            continue
        for attack_id in card.attacks:
            atk = attacks.get(attack_id)
            if atk is None:
                continue
            missing = {int(e) for e in atk.energies if int(e) != colorless} - deck_types
            if missing:
                return card
    raise AssertionError("no off-type basic Pokemon found for deck")


def _find_colorless_only_basic():
    """First basic Pokémon (scanned executably) whose every RESOLVED attack
    has zero typed (non-COLORLESS) cost -- always payable regardless of deck."""
    cards = all_card_data()
    attacks = {a.attackId: a for a in all_attack()}
    colorless = int(EnergyType.COLORLESS)
    for card in cards:
        if card.cardType != CardType.POKEMON or not card.basic:
            continue
        resolved = [attacks[aid] for aid in card.attacks if aid in attacks]
        if not resolved:
            continue
        if all(all(int(e) == colorless for e in atk.energies) for atk in resolved):
            return card
    raise AssertionError("no colorless-only-attack basic Pokemon found")


def test_payability_anchor_and_ladder_pass():
    anchor = _cards_from_csv("src/ptcg/decks/candidates/anchor-min8.csv")
    ladder = _cards_from_csv("src/ptcg/decks/candidates/mega-lucario-fighting.csv")
    assert attack_payability_problems(anchor) == []
    assert attack_payability_problems(ladder) == []


def test_payability_flags_off_type_pokemon():
    """Take the anchor deck (payable) and swap one payable filler for a
    Pokémon whose attack needs an energy type the deck does not run.
    Find the off-type Pokémon EXECUTABLY: scan all_card_data() for a basic
    Pokémon whose attack has a typed cost disjoint from the anchor's
    energy types (verify-game-data-claims rule: never hardcode a card id
    from memory — derive it in the test)."""
    deck = _cards_from_csv("src/ptcg/decks/candidates/anchor-min8.csv")
    off_type = _find_offtype_basic_for(deck)
    mutated = deck[:-1] + [off_type.cardId]
    probs = attack_payability_problems(mutated)
    assert probs and off_type.name in " ".join(probs)
    assert any(off_type.name in p for p in validate_deck(mutated))


def test_payability_colorless_only_always_passes():
    """A colorless-only-attack basic added to any deck never trips the rule
    (find one executably, same discipline)."""
    anchor = _cards_from_csv("src/ptcg/decks/candidates/anchor-min8.csv")
    colorless_basic = _find_colorless_only_basic()
    mutated = anchor[:-1] + [colorless_basic.cardId]
    probs = attack_payability_problems(mutated)
    assert not any(colorless_basic.name in p for p in probs)
