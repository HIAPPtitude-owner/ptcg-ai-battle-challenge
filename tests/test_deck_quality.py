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


def test_basics_two_is_red_and_pct_always_computed():
    # 2 basics is still < MIN_BASIC_CARDS=8 under the recalibrated rule
    # (min-basics-pool-rule Task 4) -- see the dedicated min-basics
    # threshold tests further down for the 7-vs-8 boundary itself.
    deck = [1030] * 2 + [_WATER_ENERGY_ID] * 20 + [1121] * 38
    report = analyze_deck(deck)
    assert report.basics_count == 2
    assert report.mulligan_pct == pytest.approx(0.7785310734463277)
    risk = next(f for f in report.flags if f.code == "mulligan-risk")
    assert risk.severity == "red"


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


def test_champion_deck_now_flags_mulligan_risk_below_min_basics():
    # Pre-min-basics-pool-rule, this real champion snapshot (basics_count=4)
    # was the "zero flags" golden case (pool-pruning-ui-improvements Task 4,
    # 2026-08-10) because the old threshold was BASICS_RED_MAX=2. This
    # slice raises the bar to MIN_BASIC_CARDS=8 (Task 2), so the same
    # fixture -- built before the pool rule existed -- now legitimately
    # trips the badge; see the calibration-comment update in
    # deck_quality.py. The badge now marks a rule violation, not an
    # observed-range outlier.
    assert len(CHAMPION_DECK) == 60
    report = analyze_deck(CHAMPION_DECK)
    assert (report.pokemon_count, report.trainer_count,
            report.energy_count, report.basics_count) == (8, 32, 20, 4)
    assert report.mulligan_pct == pytest.approx(0.6005003742553344)
    assert _codes(report) == {"mulligan-risk"}
    risk = next(f for f in report.flags if f.code == "mulligan-risk")
    assert risk.severity == "red"
    assert "< 8" in risk.reason


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


# --- mulligan-risk recalibrated to MIN_BASIC_CARDS (min-basics-pool-rule Task 4) ---
from ptcg.decks.validate import MIN_BASIC_CARDS  # noqa: E402


def _deck_with_basics(n: int) -> list[int]:
    """n Basic Pokemon copies split across two distinct species (Task-2
    fixture shape -- see tests/test_validate.py's ``_db()``), padded with
    Basic Water Energy filler to 60 cards. analyze_deck accepts any
    length; 60 just keeps this fixture consistent with the others above."""
    cards = all_card_data()
    basics = [c for c in cards if c.cardType == CardType.POKEMON and c.basic]
    basic_a = basics[0]
    basic_b = next(c for c in basics if c.name != basic_a.name)
    half = n // 2
    deck = [basic_a.cardId] * half + [basic_b.cardId] * (n - half)
    return deck + [_WATER_ENERGY_ID] * (60 - n)


def test_mulligan_risk_fires_below_min_basics():
    # Verified: mulligan_probability(7) == 0.3991204507676869 -> "mulligan 40%".
    report = analyze_deck(_deck_with_basics(7))
    # NOTE: brief's snippet used `f.name`; Flag has no such field (it's
    # `code`) -- verified against the Flag dataclass before transcribing.
    assert any(f.code == "mulligan-risk" and f.severity == "red" for f in report.flags)
    risk = next(f for f in report.flags if f.code == "mulligan-risk")
    assert "< 8" in risk.reason


def test_mulligan_risk_absent_at_min_basics():
    report = analyze_deck(_deck_with_basics(8))
    assert not any(f.code == "mulligan-risk" for f in report.flags)
