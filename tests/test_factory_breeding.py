"""Tests for mutation + crossover breeding operators (both populations)."""
from __future__ import annotations

import random
from pathlib import Path

import pytest

from cg.api import CardType, all_card_data
from ptcg.arena.runner import load_deck
from ptcg.decks.validate import attack_payability_problems, is_basic_pokemon, validate_deck
from ptcg.factory.breeding import (
    breed_agent,
    breed_deck,
    crossover_agent,
    crossover_deck,
    mutate_agent,
    mutate_deck,
    select_parents,
)
from ptcg.factory.deck_matrix import apply_rule, matrix_decks
from ptcg.factory.genomes import (
    AgentGenome,
    CATEGORICAL_GENES,
    DeckGenome,
    GENE_SPEC,
    agent_genome_id,
    deck_genome_id,
)

ROOT = Path(__file__).resolve().parents[1]


def _decks():
    """First two real seed/generated deck csv paths from matrix_decks(),
    loaded via the arena loader (per the brief's Step 1a), each bumped to
    MIN_BASIC_CARDS=8 (min-basics-pool-rule, 2026-08-11).

    The real matrix seeds run only ~4 basics of a SINGLE species each
    (legacy, pre-rule) -- and crossover_deck's per-name 4-copy cap means two
    parents built around the SAME basic species (as the first two
    matrix_decks() entries are, here: mega-starmie-water + its energy
    variant, both 4x Staryu) can combine to at most 4 copies of any one
    basic, never enough to clear 8. `crossover_deck`/`mutate_deck` both
    defer to `validate_deck` as their own acceptance oracle (see
    breeding.py's module docstring), so a fixture that can never reach 8
    basics makes them return None on every attempt -- verified via a real
    pre-fix run (0/200 crossover successes, 0/10 fallback-mutate
    successes). Each deck gets a second, DISTINCT 4-copy basic line (a
    species absent from BOTH source decks, swapped in for 4 basic-energy
    copies) so both operators can still find legal children.

    unpayable-attack-pool-rule (2026-08-12, Task 5 red-test fix): picking
    the extra basic line by name-availability alone (the original
    `extra_basics[i]` index pick) went stale the moment `validate_deck`
    started enforcing `attack_payability_problems` -- the arbitrary first
    candidate (Hippopotas, attack needing energy type 6) was NOT payable by
    deck_a's actual Water-only energy line, so deck_a itself failed
    `validate_deck` post-bump and every mutate_deck attempt derived from it
    (the whole `test_breed_deck_falls_back_to_mutation_when_crossover_forced_to_fail`
    red test) returned None on all 10 seeds -- a fixture problem, not a
    breed_deck code problem (diagnosed via `validate_deck(bumped_deck_a)`
    printing the exact payability problem string). Fixed by walking
    candidates in order and picking the first one, per deck, whose bump
    keeps `validate_deck` clean (payability included) -- verified via a
    real post-fix run (200/200 crossover successes, 50/50 mutate successes
    on deck_a alone).
    """
    entries = matrix_decks()
    paths = [ROOT / p for p, _prio in entries[:2]]
    decks = [load_deck(p) for p in paths]
    db = {c.cardId: c for c in all_card_data()}
    used_names = {
        db[cid].name for deck in decks for cid in deck if is_basic_pokemon(cid)
    }
    extra_basics = [
        c
        for c in all_card_data()
        if c.cardType == CardType.POKEMON and c.basic and c.name not in used_names
    ]

    def _bump(deck: list[int], extra_id: int) -> list[int]:
        out = list(deck)
        removed = 0
        for i in range(len(out) - 1, -1, -1):
            if removed >= 4:
                break
            card = db.get(out[i])
            if card is not None and card.cardType == CardType.BASIC_ENERGY:
                out.pop(i)
                removed += 1
        out.extend([extra_id] * 4)
        return out

    chosen_ids: set[int] = set()
    bumped_decks = []
    for deck in decks:
        found = None
        for candidate in extra_basics:
            if candidate.cardId in chosen_ids:
                continue
            bumped = _bump(deck, candidate.cardId)
            if validate_deck(bumped) == []:
                found = candidate
                break
        if found is None:
            raise AssertionError(
                "no distinct extra basic line keeps validate_deck clean "
                "(payability included) for this matrix seed deck"
            )
        chosen_ids.add(found.cardId)
        bumped_decks.append(_bump(deck, found.cardId))
    return bumped_decks


def _base_agent_config() -> dict:
    return {gene: lo for gene, (lo, _hi, _sigma, _cast) in GENE_SPEC.items()} | {
        gene: choices[0] for gene, choices in CATEGORICAL_GENES.items()
    }


def _agent(config: dict | None = None, **kw) -> AgentGenome:
    config = config if config is not None else _base_agent_config()
    return AgentGenome(id=agent_genome_id(config), kind="search", config=config, **kw)


def _deck(cards: list, **kw) -> DeckGenome:
    return DeckGenome(id=deck_genome_id(cards), cards=cards, **kw)


# --- (a) crossover_deck legality property test --------------------------


def test_crossover_deck_always_yields_legal_60_card_deck_or_none():
    deck_a, deck_b = _decks()
    rng = random.Random(7)
    checked_any_success = False
    for _ in range(200):
        child = crossover_deck(rng, deck_a, deck_b)
        if child is None:
            continue
        checked_any_success = True
        assert len(child) == 60
        assert validate_deck(child) == []
    assert checked_any_success  # the property test must exercise real successes, not all-None


# --- (b) reproducibility --------------------------------------------------


def test_crossover_deck_reproducible_across_identical_seeded_rng():
    deck_a, deck_b = _decks()
    result_1 = crossover_deck(random.Random(42), deck_a, deck_b)
    result_2 = crossover_deck(random.Random(42), deck_a, deck_b)
    assert result_1 == result_2


def test_breed_agent_reproducible_across_identical_seeded_rng():
    config_a = _base_agent_config()
    config_b = dict(config_a)
    config_b["search_budget_ms"] = GENE_SPEC["search_budget_ms"][1]  # hi bound
    parents = [_agent(config_a), _agent(config_b)]

    child_1 = breed_agent(random.Random(42), parents)
    child_2 = breed_agent(random.Random(42), parents)

    assert child_1.config == child_2.config
    assert child_1.id == child_2.id
    assert child_1.seed == child_2.seed


# --- (c) mutate_agent bounds/types ----------------------------------------


def test_mutate_agent_stays_within_bounds_and_types_from_hi_extreme():
    hi_config = {gene: hi for gene, (_lo, hi, _sigma, _cast) in GENE_SPEC.items()}
    hi_config.update({gene: choices[0] for gene, choices in CATEGORICAL_GENES.items()})
    rng = random.Random(3)
    for _ in range(100):
        hi_config = mutate_agent(rng, hi_config)
        for gene, (lo, hi, _sigma, cast) in GENE_SPEC.items():
            value = hi_config[gene]
            assert lo <= value <= hi
            assert isinstance(value, cast)


# --- (d) categorical flip only produces known values ----------------------


def test_mutate_agent_categorical_flip_only_yields_known_choices():
    config = _base_agent_config()
    rng = random.Random(11)
    for _ in range(200):
        config = mutate_agent(rng, config)
        for gene, choices in CATEGORICAL_GENES.items():
            assert config[gene] in choices


# --- (e) breed_deck fallback when crossover is forced to fail -------------


def test_breed_deck_falls_back_to_mutation_when_crossover_forced_to_fail(monkeypatch, tmp_path):
    deck_a, deck_b = _decks()
    monkeypatch.setattr("ptcg.factory.breeding.crossover_deck", lambda rng, a, b: None)
    parents = [_deck(deck_a, rating=5.0), _deck(deck_b, rating=1.0)]

    # rng.random() < 0.7 branch selection is itself seeded; try a spread of
    # seeds so both the "chose crossover, fell back" and "chose mutate
    # directly" paths get covered by at least one seed in this run.
    outcomes = []
    for seed in range(10):
        result = breed_deck(random.Random(seed), parents, tmp_path)
        outcomes.append(result)
        if result is not None:
            assert isinstance(result, DeckGenome)
            assert len(result.cards) == 60
            assert validate_deck(result.cards) == []
    assert any(o is not None for o in outcomes)  # clean success or clean None only, never a crash


# --- (f) select_parents top-frac head -------------------------------------


def _unique_agent_config(i: int) -> dict:
    """Distinct config per index (varies search_budget_ms) so
    agent_genome_id (content-addressed) never collides across fixture
    genomes in the same test pool."""
    config = _base_agent_config()
    config["search_budget_ms"] = min(
        GENE_SPEC["search_budget_ms"][1], GENE_SPEC["search_budget_ms"][0] + i
    )
    return config


def test_select_parents_returns_only_top_quarter_by_rating():
    genomes = [_agent(config=_unique_agent_config(i), rating=float(i)) for i in range(12)]
    # top 25% of 12 -> ceil(3) = 3 highest-rated genomes (ratings 11, 10, 9).
    top_ids = {genomes[11].id, genomes[10].id, genomes[9].id}

    rng = random.Random(5)
    for _ in range(30):
        selected = select_parents(rng, genomes, k=2, top_frac=0.25)
        assert len(selected) == 2
        assert len({g.id for g in selected}) == 2  # no duplicate parent
        for g in selected:
            assert g.id in top_ids


def test_select_parents_none_rating_sorts_last():
    rated = [_agent(config=_unique_agent_config(i), rating=1.0) for i in range(3)]
    unrated = _agent(config=_unique_agent_config(99), rating=None)
    pool = rated + [unrated]

    rng = random.Random(1)
    # top_frac=0.25 of 4 -> ceil(1)=1, but head floor is max(2, ...) = 2.
    for _ in range(20):
        selected = select_parents(rng, pool, k=2, top_frac=0.25)
        assert unrated.id not in {g.id for g in selected}


# --- Degenerate-input probes (standing rule: probe empty/single explicitly) --


def test_select_parents_empty_pool_returns_empty_list():
    assert select_parents(random.Random(1), []) == []


def test_select_parents_all_retired_pool_returns_empty_list():
    retired = [_agent(config=_base_agent_config(), rating=1.0, status="retired")]
    assert select_parents(random.Random(1), retired) == []


def test_select_parents_tiny_pool_allows_k1():
    only = _agent(config=_base_agent_config(), rating=1.0)
    selected = select_parents(random.Random(1), [only], k=2, top_frac=0.25)
    assert len(selected) == 1
    assert selected[0].id == only.id


def test_breed_agent_single_parent_uses_mutate_not_crossover():
    parent = _agent(config=_base_agent_config())
    child = breed_agent(random.Random(9), [parent])
    assert child.lineage == [parent.id]
    assert child.kind == "search"


def test_breed_agent_empty_parents_raises():
    with pytest.raises(ValueError):
        breed_agent(random.Random(1), [])


def test_breed_deck_single_parent_pool_mutates_only(tmp_path):
    deck_a, _deck_b = _decks()
    parent = _deck(deck_a, rating=2.0, net_weights="src/ptcg/search/value_net_weights.json")
    result = breed_deck(random.Random(4), [parent], tmp_path)
    if result is not None:
        assert result.lineage == [parent.id]
        assert result.net_weights == parent.net_weights
        assert validate_deck(result.cards) == []
        assert Path(ROOT / result.csv if not Path(result.csv).is_absolute() else result.csv).exists()


def test_breed_deck_empty_parents_returns_none(tmp_path):
    assert breed_deck(random.Random(1), [], tmp_path) is None


def test_breed_deck_writes_csv_and_inherits_higher_rated_net_weights(tmp_path):
    deck_a, deck_b = _decks()
    lower = _deck(deck_a, rating=1.0, net_weights="src/ptcg/search/value_net_weights_a.json")
    higher = _deck(deck_b, rating=9.0, net_weights="src/ptcg/search/value_net_weights_b.json")

    found = None
    for seed in range(20):
        result = breed_deck(random.Random(seed), [lower, higher], tmp_path)
        if result is not None:
            found = result
            break
    assert found is not None
    assert found.net_weights == higher.net_weights
    assert found.lineage == [lower.id, higher.id]
    written_path = tmp_path / f"evolved-{found.id}.csv"
    assert written_path.exists()


# --- min-basics-pool-rule (2026-08-11): mutation-path rejection ------------


class _FixedFirstRng:
    """Deterministic double for `breeding.mutate_deck`'s `rng.choice`/
    `rng.shuffle` usage: `choice` always returns the pre-set rule name
    (forcing the mutation-under-test to be tried first); `shuffle` is a
    no-op (keeps the fallback rule order stable/inspectable)."""

    def __init__(self, first: str) -> None:
        self._first = first

    def choice(self, seq):
        return self._first

    def shuffle(self, seq):
        return None


def _payable_basic_pair_and_energy():
    """First energy type (in `_energy_by_type()` iteration order) that pays
    for >=2 distinct filler basics from `_filler_basic_order()`, plus the
    first two such basics -- found executably (verify-game-data-claims
    rule: never hardcode which basics/energy pair, derive it), needed so
    the hand-built parent below clears the new attack-payability rule
    (unpayable-attack-pool-rule, 2026-08-12) same as it already clears
    min-basics-pool-rule."""
    from ptcg.factory import builder as factory_builder

    fillers = factory_builder._filler_basic_order()
    energies = factory_builder._energy_by_type()
    for energy_card in energies.values():
        payable = [
            c
            for c in fillers
            if not attack_payability_problems([c.cardId] * 4 + [energy_card.cardId] * 56)
        ]
        if len(payable) >= 2:
            return payable[0], payable[1], energy_card
    raise AssertionError("no basic-energy type pays for >=2 distinct filler basics")


def test_attacker_down1_mutation_rejects_sub_8_basics_child():
    """Requirement B (Task 2 review, spec Section 6): a mutation path that
    would drop basics below MIN_BASIC_CARDS must be rejected/re-rolled by
    the oracle (validate_deck, via apply_rule), never silently accepted.

    Parent: exactly 8 basics via TWO distinct 4-copy Basic Pokemon lines --
    the ONLY Pokemon in this hand-built deck, so `_attacker_down1`'s
    `_top(pokemon, count_eq=4)` is guaranteed to pick one of them (never a
    non-basic decoy). Two trainers sit at 3 copies (not 4) so the swap's
    +1 partner-copy never itself trips the 4-copy cap -- isolating the
    rejection to the basics-count rule specifically. Cutting either basic
    drops the deck to 7 basics (< 8) for every possible trainer partner,
    so `apply_rule` must return None regardless of which partner is tried.
    """
    from ptcg.factory import builder as factory_builder

    basic_a, basic_b, energy_card = _payable_basic_pair_and_energy()
    cheren = factory_builder._card_by_name()["Cheren"]
    judge = factory_builder._card_by_name()["Judge"]

    parent = (
        [basic_a.cardId] * 4
        + [basic_b.cardId] * 4
        + [cheren.cardId] * 3
        + [judge.cardId] * 3
        + [energy_card.cardId] * (60 - 8 - 3 - 3)
    )
    assert len(parent) == 60
    assert validate_deck(parent) == [], validate_deck(parent)

    # Direct oracle check: this specific mutation is rejected outright.
    assert apply_rule(list(parent), "attacker-down1") is None

    # Full breeding.mutate_deck path: force the first pick to be the
    # rejected rule. mutate_deck must either re-roll to a different rule
    # (whose result, if any, is still validate_deck-legal -- i.e. >=8
    # basics) or return None. It must never surface the rejected sub-8
    # child.
    rng = _FixedFirstRng("attacker-down1")
    result = mutate_deck(rng, list(parent))
    if result is not None:
        assert validate_deck(result) == []


# --- unpayable-attack-pool-rule (2026-08-12), Task 5: crossover repair path -


def _two_type_clashing_decks() -> tuple[list[int], list[int]]:
    """Two independently legal+payable 60-card decks, each built around a
    DIFFERENT energy type with two genuinely type-dependent (not
    colorless-only-attack) basic attackers -- derived executably from the
    real card DB (verify-game-data-claims rule: never hardcode which
    basics/types clash, find them) rather than assuming specific card
    names. Crossing them exercises `crossover_deck`'s repair-on-failure
    path: a raw pool-recombined child frequently inherits Pokemon from
    BOTH types without carrying over a payable energy split, since the
    greedy shuffle-and-fill has no notion of energy composition at all.
    """
    from cg.api import EnergyType
    from ptcg.factory import builder as factory_builder

    fillers = factory_builder._filler_basic_order()
    energies = factory_builder._energy_by_type()
    attacks_by_id = factory_builder._attacks_by_id()
    colorless = int(EnergyType.COLORLESS)

    def _requires_type(card, energy_type: int) -> bool:
        for attack_id in card.attacks:
            atk = attacks_by_id.get(attack_id)
            if atk is None:
                continue
            if any(int(e) == energy_type and energy_type != colorless for e in atk.energies):
                return True
        return False

    def _build(energy_type: int, energy_card) -> list[int]:
        payable = [
            c
            for c in fillers
            if not attack_payability_problems([c.cardId] * 4 + [energy_card.cardId] * 56)
        ]
        typed = [c for c in payable if _requires_type(c, energy_type)]
        assert len(typed) >= 2, f"no >=2 type-dependent payable fillers for type {energy_type}"
        basic_a, basic_b = typed[0], typed[1]
        cheren = factory_builder._card_by_name()["Cheren"]
        judge = factory_builder._card_by_name()["Judge"]
        deck = (
            [basic_a.cardId] * 4
            + [basic_b.cardId] * 4
            + [cheren.cardId] * 3
            + [judge.cardId] * 3
            + [energy_card.cardId] * (60 - 8 - 3 - 3)
        )
        assert validate_deck(deck) == [], validate_deck(deck)
        return deck

    types = list(energies.items())
    deck_a = _build(*types[0])
    deck_b = _build(*types[1])
    return deck_a, deck_b


def test_crossover_deck_repairs_unpayable_child_via_deck_repair(monkeypatch):
    """unpayable-attack-pool-rule (2026-08-12, Task 5): `crossover_deck` now
    applies `ptcg.factory.deck_repair.repair_deck` to a raw pool-recombined
    child before giving up on it. Diagnosed via a real measurement against
    the live `experiments/factory/tournament.db` (mode=ro; see
    task-5-report.md for the full numbers): crossing real matrix/tournament
    parent decks dropped `crossover_deck`'s standalone yield from 200/200
    (with the payability check monkeypatched out) to 63/200 once
    `attack_payability_problems` started gating `validate_deck` -- a
    recombined-from-two-decks child frequently inherits Pokemon from BOTH
    parents' type lines without inheriting a payable energy split, and
    plain retry-the-shuffle (`CROSSOVER_RETRIES`) does not fix that.
    Post-fix: 189-199/200 across repeated real runs on the same shape of
    sample. This test pins the repair path is GENUINELY exercised -- not
    just that results happen to already be legal -- via a call-counting spy
    on the real `repair_deck` implementation, against a deterministic
    two-different-energy-type deck pair built here (no dependency on
    `tournament.db`, so the test is hermetic and reproducible)."""
    import ptcg.factory.deck_repair as deck_repair_mod

    deck_a, deck_b = _two_type_clashing_decks()

    calls = {"n": 0}
    orig_repair = deck_repair_mod.repair_deck

    def _spy(cards):
        calls["n"] += 1
        return orig_repair(cards)

    monkeypatch.setattr(deck_repair_mod, "repair_deck", _spy)

    rng = random.Random(123)
    successes = 0
    for _ in range(200):
        child = crossover_deck(rng, deck_a, deck_b)
        if child is not None:
            successes += 1
            assert validate_deck(child) == []
    assert successes > 0
    # The repair path must actually run on this clashing-types scenario --
    # not merely be dead code that never fires because every raw candidate
    # already happened to be legal.
    assert calls["n"] > 0
