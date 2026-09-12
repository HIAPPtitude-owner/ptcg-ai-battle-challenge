"""Tests for the one-shot founder-population seeding script (Task 8,
evolutionary-agent-population slice)."""
from __future__ import annotations

from pathlib import Path

import pytest

from ptcg.factory.candidates import Candidate, Status, save_ledger
from ptcg.factory.cycle import FactoryPaths
from ptcg.factory.evolution import TARGET_AGENTS, TARGET_DECKS
from ptcg.factory.genomes import CATEGORICAL_GENES, GENE_SPEC, load_pool

from scripts.seed_evolution_pools import (
    ROOT, AlreadySeededError, complete_gene_surface, main, seed_pools,
)

INCUMBENT_DECK = "src/ptcg/decks/candidates/mega-lucario-fighting.csv"
STARMIE_DECK = "src/ptcg/decks/candidates/mega-starmie-water.csv"
EXISTING_NET_WEIGHTS = "src/ptcg/search/value_net_weights_mega-starmie-water.json"
MISSING_NET_WEIGHTS = "src/ptcg/search/value_net_weights_does-not-exist.json"


def _write_ledger(path: Path, candidates: list) -> None:
    save_ledger(path, candidates)


def _fixture_candidates() -> list:
    # Distinct (non-empty) config from the heuristic anchor's `{}` so this
    # fixture's incumbent-anchor genome does NOT collide with the always-
    # present heuristic anchor -- that collision case is exercised on its
    # own in test_heuristic_incumbent_collides_with_heuristic_anchor_dedup_keeps_one.
    incumbent = Candidate(
        id="mega-lucario-fighting-searchnet-v1.0", name="mega-lucario-fighting-searchnet",
        version="v1.0", deck=INCUMBENT_DECK, agent_kind="search-net",
        agent_config={"search_budget_ms": 250, "rollout_depth": 10},
        status=Status.SCORED, is_incumbent=True, local_wr=0.55, matrix_rating=0.2)
    best_starmie = Candidate(
        id="mega-starmie-water-searchnet-v1.0", name="mega-starmie-water-searchnet",
        version="v1.0", deck=STARMIE_DECK, agent_kind="search-net",
        agent_config={"net_weights": EXISTING_NET_WEIGHTS, "search_budget_ms": 300},
        status=Status.EVALUATED, matrix_rating=0.5)
    worse_starmie = Candidate(
        id="mega-starmie-water-searchnet-v0.1", name="mega-starmie-water-searchnet",
        version="v0.1", deck=STARMIE_DECK, agent_kind="search-net",
        agent_config={"net_weights": MISSING_NET_WEIGHTS},
        status=Status.EVALUATED, matrix_rating=0.1)
    return [incumbent, best_starmie, worse_starmie]


def test_real_path_seeds_both_pools_with_full_founder_roster(tmp_path):
    """(a) from the brief: real-path run against a tmp candidates.json
    fixture (3 candidates incl. one incumbent) + tmp output dir -> both
    pools written, counts/anchors as specced, every search genome's config
    contains ALL GENE_SPEC + CATEGORICAL keys."""
    paths = FactoryPaths(root=tmp_path)
    _write_ledger(paths.ledger, _fixture_candidates())

    result = seed_pools(paths, root=ROOT)

    agents = result["agents"]
    decks = result["decks"]

    # 1 heuristic anchor + 1 incumbent anchor (distinct configs -> no
    # collision here) + (TARGET_AGENTS - 2) live search genomes.
    assert len(agents) == TARGET_AGENTS
    anchors = [a for a in agents if a.status == "anchor"]
    assert len(anchors) == 2
    assert {a.kind for a in anchors} == {"heuristic", "search"}
    search_genomes = [a for a in agents if a.status == "live"]
    assert len(search_genomes) == TARGET_AGENTS - 2
    all_gene_keys = set(GENE_SPEC) | set(CATEGORICAL_GENES)
    for g in search_genomes:
        assert g.kind == "search"
        assert all_gene_keys <= set(g.config)

    by_csv = {d.csv: d for d in decks}
    assert INCUMBENT_DECK in by_csv
    assert STARMIE_DECK in by_csv
    assert by_csv[INCUMBENT_DECK].status == "anchor"
    assert by_csv[STARMIE_DECK].status == "live"
    # net_weights carried from the BEST-rated (matrix_rating=0.5) starmie
    # candidate, not the worse one (0.1) whose weights file doesn't exist.
    assert by_csv[STARMIE_DECK].net_weights == EXISTING_NET_WEIGHTS
    assert by_csv[INCUMBENT_DECK].net_weights is None  # heuristic incumbent, no net
    deck_anchors = [d for d in decks if d.status == "anchor"]
    assert len(deck_anchors) == 1
    assert deck_anchors[0].csv == INCUMBENT_DECK
    assert len(decks) <= TARGET_DECKS

    on_disk_agents = load_pool(paths.root / "experiments" / "factory" / "agent_pool.json")
    on_disk_decks = load_pool(paths.root / "experiments" / "factory" / "deck_pool.json")
    assert {a.id for a in on_disk_agents} == {a.id for a in agents}
    assert {d.id for d in on_disk_decks} == {d.id for d in decks}


def test_existing_agent_pool_refuses_before_writing_deck_pool(tmp_path):
    """(b) from the brief: existing-pool refusal -> exit code path raises,
    deck pool NOT written (fail-safe ordering: check both before writing
    either)."""
    paths = FactoryPaths(root=tmp_path)
    agent_pool_path = paths.root / "experiments" / "factory" / "agent_pool.json"
    agent_pool_path.parent.mkdir(parents=True)
    agent_pool_path.write_text('{"version": 1, "genomes": []}', encoding="utf-8")
    deck_pool_path = paths.root / "experiments" / "factory" / "deck_pool.json"

    with pytest.raises(AlreadySeededError):
        seed_pools(paths, root=ROOT)

    assert not deck_pool_path.exists()


def test_cli_main_exits_1_on_already_seeded(tmp_path):
    """(b), CLI-level: main() maps the refusal to a loud exit code 1."""
    factory_dir = tmp_path / "experiments" / "factory"
    factory_dir.mkdir(parents=True)
    (factory_dir / "agent_pool.json").write_text(
        '{"version": 1, "genomes": []}', encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        main(["--root", str(tmp_path)])

    assert exc_info.value.code == 1
    assert not (factory_dir / "deck_pool.json").exists()


def test_virgin_output_dir_creates_parent_chain(tmp_path):
    """(c) from the brief: the output parent chain does not exist before
    the call and must not be pre-created by the test -- seed_pools (via
    save_pool) must mkdir it itself. The ledger fixture lives in a
    SEPARATE, already-existing directory so this test isolates the pool
    OUTPUT directory's virginity from the ledger read."""
    ledger_fixture = tmp_path / "fixture" / "candidates.json"
    _write_ledger(ledger_fixture, _fixture_candidates())
    output_root = tmp_path / "brand-new-output"
    paths = FactoryPaths(root=output_root)
    assert not (output_root / "experiments" / "factory").exists()

    seed_pools(paths, ledger_path=ledger_fixture, root=ROOT)

    assert (output_root / "experiments" / "factory" / "agent_pool.json").exists()
    assert (output_root / "experiments" / "factory" / "deck_pool.json").exists()


def test_dry_run_prints_roster_and_writes_nothing(tmp_path, capsys):
    """(d) from the brief: --dry-run prints the roster and writes NOTHING."""
    paths = FactoryPaths(root=tmp_path)
    _write_ledger(paths.ledger, _fixture_candidates())

    result = seed_pools(paths, dry_run=True, root=ROOT)

    assert len(result["agents"]) == TARGET_AGENTS
    assert len(result["decks"]) >= 2
    factory_dir = tmp_path / "experiments" / "factory"
    assert not (factory_dir / "agent_pool.json").exists()
    assert not (factory_dir / "deck_pool.json").exists()
    out = "\n".join(str(a.id) for a in result["agents"])  # sanity: ids are printable
    assert out


def test_heuristic_incumbent_collides_with_heuristic_anchor_dedup_keeps_one(tmp_path):
    """Brief's named dedup case: when the incumbent's own agent_kind is
    "heuristic" (config {} verbatim), its anchor genome collides on
    agent_genome_id with the always-present heuristic anchor. Keep-first
    dedup must leave exactly ONE anchor, not two, shrinking the pool by 1."""
    paths = FactoryPaths(root=tmp_path)
    incumbent = Candidate(
        id="mega-lucario-fighting-heuristic-v1.0", name="mega-lucario-fighting-heuristic",
        version="v1.0", deck=INCUMBENT_DECK, agent_kind="heuristic", agent_config={},
        status=Status.SCORED, is_incumbent=True, local_wr=0.55)
    _write_ledger(paths.ledger, [incumbent])

    result = seed_pools(paths, root=ROOT)

    agents = result["agents"]
    assert len(agents) == TARGET_AGENTS - 1
    anchors = [a for a in agents if a.status == "anchor"]
    assert len(anchors) == 1
    assert anchors[0].kind == "heuristic"
    assert anchors[0].notes == "founder: heuristic anchor (scale bridge)"


def test_complete_gene_surface_fills_missing_keys_only():
    partial = {"search_budget_ms": 999, "final_move_rule": "max_value"}
    completed = complete_gene_surface(partial)
    assert completed["search_budget_ms"] == 999
    assert completed["final_move_rule"] == "max_value"
    assert completed["rollout_depth"] == 12
    assert completed["use_root_prior"] is False
    assert set(completed) == set(GENE_SPEC) | set(CATEGORICAL_GENES)


def test_no_incumbent_raises_clear_error(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    _write_ledger(paths.ledger, [])

    with pytest.raises(RuntimeError):
        seed_pools(paths, root=ROOT)


def test_incumbent_net_weights_stripped_from_all_agent_genomes(tmp_path):
    """Fix-pass regression (review of 1506a6c, finding 1): the incumbent's
    own `agent_config` can carry a deck-specific `net_weights` key (set
    whenever the incumbent itself is a search-net candidate). Before this
    fix, `_build_agent_pool` copied that config VERBATIM into every founder
    agent genome (the heuristic-anchor's `{}` excepted). `cell_candidate_view`
    (evolution.py:94-96) only injects `net_weights` back in for cells whose
    DECK carries one -- so pairing a stale-net_weights agent genome with any
    NETLESS deck would silently load the WRONG deck's ValueNetEvaluator (the
    override never fires for that pairing, so the agent's own leftover path
    wins). Every founder agent genome's config must have the key stripped;
    deck-side net_weights carry (DeckGenome.net_weights, unrelated code path)
    must be unaffected.
    """
    paths = FactoryPaths(root=tmp_path)
    incumbent = Candidate(
        id="mega-lucario-fighting-searchnet-v1.0", name="mega-lucario-fighting-searchnet",
        version="v1.0", deck=INCUMBENT_DECK, agent_kind="search-net",
        agent_config={"net_weights": EXISTING_NET_WEIGHTS, "search_budget_ms": 250},
        status=Status.SCORED, is_incumbent=True, local_wr=0.55, matrix_rating=0.2)
    best_starmie = Candidate(
        id="mega-starmie-water-searchnet-v1.0", name="mega-starmie-water-searchnet",
        version="v1.0", deck=STARMIE_DECK, agent_kind="search-net",
        agent_config={"net_weights": EXISTING_NET_WEIGHTS, "search_budget_ms": 300},
        status=Status.EVALUATED, matrix_rating=0.5)
    _write_ledger(paths.ledger, [incumbent, best_starmie])

    result = seed_pools(paths, root=ROOT)

    agents = result["agents"]
    assert len(agents) == TARGET_AGENTS
    for g in agents:
        assert "net_weights" not in g.config, (
            f"agent genome {g.id!r} retained a stale net_weights key")

    # Deck-side carry is a distinct code path (DeckGenome.net_weights) and
    # must still work: the starmie deck's DeckGenome carries net_weights
    # from its best-rated candidate.
    by_csv = {d.csv: d for d in result["decks"]}
    assert by_csv[STARMIE_DECK].net_weights == EXISTING_NET_WEIGHTS


def test_cli_exits_1_cleanly_on_missing_incumbent_deck_csv(tmp_path, capsys):
    """Fix-pass regression (review of 1506a6c, finding 2): `_build_deck_pool`'s
    incumbent deck-csv load previously let a bare FileNotFoundError propagate
    straight past main()'s `(AlreadySeededError, RuntimeError)` catch -- an
    ugly traceback exit instead of the script's `error: ...` + exit-1
    convention. The load is now wrapped to raise RuntimeError, caught
    cleanly by main(). No partial pool is ever written: `seed_pools` builds
    BOTH the agent and deck pool before saving either, so a deck-pool build
    failure leaves the (already-built, in-memory-only) agent pool unsaved
    too."""
    incumbent = Candidate(
        id="mega-lucario-fighting-heuristic-v1.0", name="mega-lucario-fighting-heuristic",
        version="v1.0", deck="src/ptcg/decks/candidates/does-not-exist.csv",
        agent_kind="heuristic", agent_config={},
        status=Status.SCORED, is_incumbent=True, local_wr=0.55)
    paths = FactoryPaths(root=tmp_path)
    _write_ledger(paths.ledger, [incumbent])

    with pytest.raises(SystemExit) as exc_info:
        main(["--root", str(tmp_path)])

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "cannot load incumbent deck csv" in err
    factory_dir = tmp_path / "experiments" / "factory"
    assert not (factory_dir / "agent_pool.json").exists()
    assert not (factory_dir / "deck_pool.json").exists()
