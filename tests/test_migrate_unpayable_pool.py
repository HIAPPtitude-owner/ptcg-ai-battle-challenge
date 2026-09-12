"""Tests for the unpayable-attack pool-rule migration (spec 2026-08-12,
`.superpowers/sdd/2026-08-12-unpayable-attack-pool-rule`). Every test
operates on a `tmp_path` DB -- this migration must NEVER touch the live
`experiments/factory/tournament.db` from a test run.

Fixture decks are derived EXECUTABLY from real card data (per
`.claude/rules/verify-game-data-claims.md`), NOT hardcoded card ids. The
derivation logic below is a deliberate, self-contained DUPLICATE of the
equivalent fixtures in `tests/test_deck_repair.py` (T3, a concurrently
in-flight sibling file this task must not touch) rather than an import
from it -- see `.claude/rules/parallel-dispatch-commit-hygiene.md` /
disjoint-files dispatch constraint.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest
from cg.api import CardData, CardType, EnergyType, all_attack, all_card_data

from ptcg.decks.validate import attack_payability_problems
from ptcg.factory import anchor, deckdb
from ptcg.factory.builder import Concept, TRAINER_SKELETON, _card_by_name, _deck_energy_plan, _stage_chain, build_deck
from ptcg.factory.deck_repair import repair_deck

import scripts.migrate_unpayable_pool as migrate_mod
from scripts.migrate_unpayable_pool import main, run_migration

_NOW = dt.datetime.now(dt.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Real-card-data fixture decks (self-contained, mirrors test_deck_repair.py's
# derivation style without importing that concurrently-edited file).
# ---------------------------------------------------------------------------


def _cards_from_csv(rel: str) -> list[int]:
    return [int(x) for x in Path(rel).read_text(encoding="utf-8").split()]


ALREADY_PAYABLE_DECK = _cards_from_csv("src/ptcg/decks/candidates/anchor-min8.csv")
assert attack_payability_problems(ALREADY_PAYABLE_DECK) == []


def _filler_offender_fixture() -> list[int]:
    """ALREADY_PAYABLE_DECK with its one swappable filler basic swapped for
    an off-type basic not already in the deck -- exactly one SWAPPABLE
    offender, zero chain-bound offenders."""
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
assert attack_payability_problems(FILLER_ONLY_DECK) != []


def _find_two_type_chain_concept() -> Concept:
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


def _chain_bound_two_type_fixture() -> list[int]:
    """A real chain-bound deck with its splash energy stripped: the
    evolved core's own attack becomes unpayable. Non-basic offender (an
    evolved form) -> automatically CHAIN-BOUND, never swappable."""
    concept = _find_two_type_chain_concept()
    chain = _stage_chain(concept.cores[0])
    assert chain is not None
    plan = _deck_energy_plan([chain])
    assert not isinstance(plan, str)
    primary_needs, secondary_max = plan
    primary_type = next(iter(set(primary_needs)))
    missing_type = next(iter(secondary_max))

    built = build_deck(concept)
    assert built.cards is not None
    db = {c.cardId: c for c in all_card_data()}
    energy_by_type: dict[int, CardData] = {}
    for c in all_card_data():
        if c.cardType == CardType.BASIC_ENERGY:
            energy_by_type[int(c.energyType)] = c
    primary_card = energy_by_type[primary_type]

    mutated: list[int] = []
    for cid in built.cards:
        card = db[cid]
        if card.cardType == CardType.BASIC_ENERGY and int(card.energyType) == missing_type:
            mutated.append(primary_card.cardId)
        else:
            mutated.append(cid)
    return mutated


CHAIN_BOUND_DECK = _chain_bound_two_type_fixture()
assert attack_payability_problems(CHAIN_BOUND_DECK) != []
assert repair_deck(CHAIN_BOUND_DECK) is not None  # repairable per T3


def _three_type_fixture() -> list[int]:
    """Two real 2-type chain-bound concepts sharing a primary type but
    needing DIFFERENT secondary types, supplied only the primary energy --
    both evolved cores become chain-bound offenders demanding two distinct
    missing types, pushing the total past `repair_deck`'s 2-type cap.
    Unrepairable by construction (returns `None`)."""
    candidates: list[tuple[str, int, int]] = []
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

    chosen: tuple[str, str, int] | None = None
    for ptype, group in by_primary.items():
        stypes = {stype for _, _, stype in group}
        if len(stypes) < 2:
            continue
        s_iter = iter(sorted(stypes))
        s1 = next(s_iter)
        s2 = next(s_iter)
        name1 = next(n for n, _, s in group if s == s1)
        name2 = next(n for n, _, s in group if s == s2)
        chosen = (name1, name2, ptype)
        break
    assert chosen is not None, "no pair of chain concepts sharing a primary type found"
    name1, name2, primary_type = chosen

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
        c for c in all_card_data()
        if c.cardType == CardType.BASIC_ENERGY and int(c.energyType) == primary_type
    )
    remaining = 60 - len(cards)
    assert remaining > 0
    cards += [energy_card.cardId] * remaining
    assert len(cards) == 60
    return cards


THREE_TYPE_DECK = _three_type_fixture()
assert attack_payability_problems(THREE_TYPE_DECK) != []
assert repair_deck(THREE_TYPE_DECK) is None  # unrepairable per T3


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _db(tmp_path: Path):
    conn = deckdb.connect(tmp_path / "t.db")
    deckdb.init_db(conn)
    return conn


def _seed(conn, cid: str, cards: list[int], status: str, cores: str = '["x"]') -> None:
    conn.execute(
        "INSERT INTO concepts(id, cores, status, reason) VALUES(?,?,?,'')",
        (cid, cores, status),
    )
    conn.execute(
        "INSERT INTO decks(id, concept_id, cards, shell_variant) VALUES(?,?,?,0)",
        (cid + "-d0", cid, json.dumps(cards)),
    )


def _seed_coverage(conn, cid: str, games_played: int = 10, distinct: int = 2, rating: float = 0.6) -> None:
    conn.execute(
        "INSERT INTO coverage(concept_id, games_played, distinct_opponents, rating) "
        "VALUES(?,?,?,?)",
        (cid, games_played, distinct, rating),
    )


# ---------------------------------------------------------------------------
# Full-pool partition: one row per class, all receipts asserted together.
# ---------------------------------------------------------------------------


def test_full_pool_partition_and_receipts(tmp_path):
    conn = _db(tmp_path)
    _seed(conn, "c-filler", FILLER_ONLY_DECK, status="active")
    _seed(conn, "c-chain", CHAIN_BOUND_DECK, status="active")
    _seed(conn, "c-three", THREE_TYPE_DECK, status="active")
    _seed(conn, "c-payable", ALREADY_PAYABLE_DECK, status="active")
    _seed(conn, "c-baseline-violator", FILLER_ONLY_DECK, status="active")
    conn.execute(
        "INSERT INTO baselines(version, offspring_id, deck_id, crowned_at) "
        "VALUES('v0.1', NULL, 'c-baseline-violator-d0', ?)", (_NOW,),
    )
    _seed(conn, anchor.ANCHOR_CONCEPT_ID, THREE_TYPE_DECK, status="finalist")
    _seed(conn, "c-finalist-violator", FILLER_ONLY_DECK, status="finalist")
    _seed(conn, "c-culled", THREE_TYPE_DECK, status="culled")

    receipts = run_migration(conn, dry_run=False)

    assert receipts["scanned"] == 7  # every non-culled row; c-culled excluded from the scan
    assert receipts["excluded_baselines"] == 1
    assert receipts["excluded_anchor"] == 1
    assert receipts["excluded_finalist"] == 1
    assert receipts["already_payable"] == 1
    assert receipts["violating"] == 3  # filler, chain, three -- baseline/anchor/finalist excluded first
    assert receipts["repaired"] == 2  # filler, chain
    assert receipts["culled_unrepairable"] == 1  # three
    # chain_delta: chain-bound (evolved core) AND three-type (two evolved
    # cores) both have >=1 chain-bound offender; filler-only does not.
    assert receipts["chain_delta"] == 2

    # invariant: scanned == every disjoint bucket, summed
    assert receipts["scanned"] == (
        receipts["excluded_baselines"] + receipts["excluded_anchor"]
        + receipts["excluded_finalist"] + receipts["already_payable"]
        + receipts["violating"]
    )
    assert receipts["violating"] == receipts["repaired"] + receipts["culled_unrepairable"]

    # repaired decks: same ids, now payable.
    for cid in ("c-filler", "c-chain"):
        row = conn.execute("SELECT cards FROM decks WHERE id=?", (cid + "-d0",)).fetchone()
        assert attack_payability_problems(json.loads(row["cards"])) == []
        status = conn.execute("SELECT status FROM concepts WHERE id=?", (cid,)).fetchone()["status"]
        assert status == "active"  # untouched by the cull path

    # unrepairable deck: concept culled, deck row retained with reason set.
    culled_row = conn.execute("SELECT status, reason FROM concepts WHERE id='c-three'").fetchone()
    assert culled_row["status"] == "culled"
    assert culled_row["reason"].startswith("unpayable-rule: ")
    assert conn.execute("SELECT COUNT(*) FROM decks WHERE id='c-three-d0'").fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Exclusion integrity: baseline-referenced VIOLATING deck survives
# byte-identical.
# ---------------------------------------------------------------------------


def test_baselines_referenced_violating_deck_survives_byte_identical(tmp_path):
    conn = _db(tmp_path)
    _seed(conn, "c-baseline-violator", FILLER_ONLY_DECK, status="active")
    conn.execute(
        "INSERT INTO baselines(version, offspring_id, deck_id, crowned_at) "
        "VALUES('v0.1', NULL, 'c-baseline-violator-d0', ?)", (_NOW,),
    )
    before = json.dumps(FILLER_ONLY_DECK)

    receipts = run_migration(conn, dry_run=False)

    assert receipts["excluded_baselines"] == 1
    assert receipts["violating"] == 0
    row = conn.execute(
        "SELECT cards FROM decks WHERE id='c-baseline-violator-d0'"
    ).fetchone()
    assert row["cards"] == before  # byte-identical, never even read for payability
    status = conn.execute(
        "SELECT status FROM concepts WHERE id='c-baseline-violator'"
    ).fetchone()["status"]
    assert status == "active"


def test_anchor_concept_deck_excluded_even_if_violating(tmp_path):
    conn = _db(tmp_path)
    _seed(conn, anchor.ANCHOR_CONCEPT_ID, THREE_TYPE_DECK, status="finalist")
    before = json.dumps(THREE_TYPE_DECK)

    receipts = run_migration(conn, dry_run=False)

    assert receipts["excluded_anchor"] == 1
    assert receipts["excluded_finalist"] == 0  # anchor check wins priority over finalist
    row = conn.execute(
        "SELECT cards FROM decks WHERE id=?", (anchor.ANCHOR_CONCEPT_ID + "-d0",)
    ).fetchone()
    assert row["cards"] == before
    status = conn.execute(
        "SELECT status FROM concepts WHERE id=?", (anchor.ANCHOR_CONCEPT_ID,)
    ).fetchone()["status"]
    assert status == "finalist"


def test_baselines_row_inserted_during_prepass_is_excluded_late(tmp_path, monkeypatch):
    """Reviewer finding (fix round 1): the exclusion set is read pre-pass
    and was never re-verified inside the txn. Reproduces the reviewer's own
    receipt shape -- a `baselines` row inserted MID-PRE-PASS (after this
    deck's exclusion check already ran, during the repair_deck compute
    step) must still leave the deck untouched at commit time, counted
    `excluded_late` rather than silently written as a stale repair."""
    conn = _db(tmp_path)
    _seed(conn, "c-filler", FILLER_ONLY_DECK, status="active")
    before = json.dumps(FILLER_ONLY_DECK)

    real_repair_deck = migrate_mod.deck_repair.repair_deck
    injected = {"done": False}

    def _patched_repair_deck(cards):
        result = real_repair_deck(cards)
        if not injected["done"] and cards == FILLER_ONLY_DECK:
            # Simulates a concurrent writer landing a NEW baselines row
            # during the pre-pass's own (potentially long) compute step --
            # autocommits immediately on this connection (isolation_level
            # =None), same as a genuinely concurrent connection would.
            conn.execute(
                "INSERT INTO baselines(version, offspring_id, deck_id, crowned_at) "
                "VALUES('v0.1', NULL, 'c-filler-d0', ?)", (_NOW,),
            )
            injected["done"] = True
        return result

    monkeypatch.setattr(migrate_mod.deck_repair, "repair_deck", _patched_repair_deck)

    receipts = run_migration(conn, dry_run=False)

    # Reviewer's receipt shape: excluded_baselines stays 0 (the pre-pass
    # snapshot predates the INSERT), but the deck must NOT be repaired --
    # excluded_late captures the late exclusion instead.
    assert receipts["excluded_baselines"] == 0
    assert receipts["excluded_late"] == 1
    assert receipts["repaired"] == 0

    row = conn.execute("SELECT cards FROM decks WHERE id='c-filler-d0'").fetchone()
    assert row["cards"] == before  # byte-identical -- the stale repair never lands
    status = conn.execute("SELECT status FROM concepts WHERE id='c-filler'").fetchone()["status"]
    assert status == "active"


# ---------------------------------------------------------------------------
# Coverage reset scoping: only the REPAIRED concept's coverage is reset.
# ---------------------------------------------------------------------------


def test_coverage_reset_scoped_to_repaired_concept_only(tmp_path):
    conn = _db(tmp_path)
    _seed(conn, "c-filler", FILLER_ONLY_DECK, status="active")
    _seed_coverage(conn, "c-filler", games_played=42, distinct=3, rating=0.55)
    _seed(conn, "c-payable", ALREADY_PAYABLE_DECK, status="active")
    _seed_coverage(conn, "c-payable", games_played=10, distinct=1, rating=0.5)

    receipts = run_migration(conn, dry_run=False)

    assert receipts["coverage_reset"] == 1
    repaired_cov = conn.execute(
        "SELECT games_played, distinct_opponents, rating FROM coverage WHERE concept_id='c-filler'"
    ).fetchone()
    assert repaired_cov["games_played"] == 0
    assert repaired_cov["distinct_opponents"] == 0
    assert repaired_cov["rating"] is None

    untouched_cov = conn.execute(
        "SELECT games_played, distinct_opponents, rating FROM coverage WHERE concept_id='c-payable'"
    ).fetchone()
    assert untouched_cov["games_played"] == 10
    assert untouched_cov["distinct_opponents"] == 1
    assert untouched_cov["rating"] == 0.5


# ---------------------------------------------------------------------------
# Drain guard
# ---------------------------------------------------------------------------


def test_drain_guard_blocks_and_writes_nothing(tmp_path):
    conn = _db(tmp_path)
    _seed(conn, "c-filler", FILLER_ONLY_DECK, status="active")
    conn.execute(
        "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, agent_version_b, "
        "purpose, status) VALUES('a','b','v0','v0','screening','pending')"
    )

    with pytest.raises(RuntimeError, match="pending/claimed"):
        run_migration(conn, dry_run=False)

    row = conn.execute("SELECT cards FROM decks WHERE id='c-filler-d0'").fetchone()
    assert json.loads(row["cards"]) == FILLER_ONLY_DECK


def test_drain_guard_counts_claimed_games_too(tmp_path):
    conn = _db(tmp_path)
    conn.execute(
        "INSERT INTO games(deck_a_id, deck_b_id, agent_version_a, agent_version_b, "
        "purpose, status) VALUES('a','b','v0','v0','screening','claimed')"
    )
    with pytest.raises(RuntimeError, match="pending/claimed"):
        run_migration(conn, dry_run=False)


# ---------------------------------------------------------------------------
# Dry-run: DB left byte-identical (file-hash check).
# ---------------------------------------------------------------------------


def test_dry_run_leaves_db_byte_identical(tmp_path):
    db_path = tmp_path / "t.db"
    conn = deckdb.connect(db_path)
    deckdb.init_db(conn)
    _seed(conn, "c-filler", FILLER_ONLY_DECK, status="active")
    _seed(conn, "c-three", THREE_TYPE_DECK, status="active")
    _seed_coverage(conn, "c-filler")
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    conn.close()

    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    conn2 = deckdb.connect(db_path)
    dry_receipts = run_migration(conn2, dry_run=True)
    conn2.execute("PRAGMA wal_checkpoint(FULL)")
    conn2.close()

    after = hashlib.sha256(db_path.read_bytes()).hexdigest()
    assert before == after

    assert dry_receipts["repaired"] == 1
    assert dry_receipts["culled_unrepairable"] == 1
    assert "canary_violating_active" not in dry_receipts  # not part of the receipts dict


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------


def test_second_run_is_a_noop(tmp_path):
    conn = _db(tmp_path)
    _seed(conn, "c-filler", FILLER_ONLY_DECK, status="active")
    _seed(conn, "c-chain", CHAIN_BOUND_DECK, status="active")
    _seed(conn, "c-three", THREE_TYPE_DECK, status="active")
    _seed_coverage(conn, "c-filler")
    _seed_coverage(conn, "c-chain")

    first = run_migration(conn, dry_run=False)
    assert first["repaired"] == 2
    assert first["culled_unrepairable"] == 1

    snapshot_before = {
        "concepts": [dict(r) for r in conn.execute("SELECT * FROM concepts ORDER BY id")],
        "decks": [dict(r) for r in conn.execute("SELECT * FROM decks ORDER BY id")],
        "coverage": [dict(r) for r in conn.execute("SELECT * FROM coverage ORDER BY concept_id")],
    }

    second = run_migration(conn, dry_run=False)

    # c-three is now culled -> excluded from the pre-pass scan entirely;
    # c-filler/c-chain are now payable (repaired in the first run) -> still
    # scanned, but reclassified `already_payable`, not `violating`.
    assert second["scanned"] == 2
    assert second["already_payable"] == 2
    assert second["violating"] == 0
    assert second["repaired"] == 0
    assert second["culled_unrepairable"] == 0
    assert second["coverage_reset"] == 0
    assert second["chain_delta"] == 0

    snapshot_after = {
        "concepts": [dict(r) for r in conn.execute("SELECT * FROM concepts ORDER BY id")],
        "decks": [dict(r) for r in conn.execute("SELECT * FROM decks ORDER BY id")],
        "coverage": [dict(r) for r in conn.execute("SELECT * FROM coverage ORDER BY concept_id")],
    }
    assert snapshot_before == snapshot_after


# ---------------------------------------------------------------------------
# CLI / virgin-directory
# ---------------------------------------------------------------------------


def test_main_virgin_db_path_installs_schema(tmp_path):
    db_path = tmp_path / "deep" / "never" / "t.db"
    assert not db_path.parent.exists()

    result = main(["--db", str(db_path)])

    assert db_path.exists()
    conn = deckdb.connect(db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"concepts", "decks", "coverage", "baselines", "games"}.issubset(tables)
    assert result["scanned"] == 0
    conn.close()


def test_main_dry_run_flag_writes_nothing(tmp_path):
    db_path = tmp_path / "t.db"
    result = main(["--db", str(db_path), "--dry-run"])
    assert result["scanned"] == 0
    conn = deckdb.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 0
    conn.close()


def test_db_flag_is_required():
    with pytest.raises(SystemExit):
        main(["--dry-run"])
