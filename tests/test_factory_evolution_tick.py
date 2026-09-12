"""Tests for evolution.py's TOURNAMENT TICK section (Task 5): the
steady-state play/rate/cull/breed cycle under the matrix lock.

Every test seeds tmp_path pools only and injects a fake `series_fn` so no
real games are played. An autouse fixture redirects `evolution.GENERATED_DIR`
(where bred deck csvs are written) into tmp_path so a breeding step can never
pollute the real repo's generated-decks directory.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import random
from pathlib import Path

import pytest

from ptcg.arena.runner import load_deck
from ptcg.factory import evolution
from ptcg.factory.cycle import FactoryPaths
from ptcg.factory.deck_matrix import matrix_decks
from ptcg.factory.evolution import (
    GENOME_RETIRE_FLOOR,
    PLAYOFF_EVERY,
    PLAYOFF_TOP,
    TARGET_AGENTS,
    TARGET_DECKS,
    _agent_pool_path,
    _deck_pool_path,
    evolution_tick,
    next_cell_pair,
    Cell,
    active_cells,
)
from ptcg.factory.genomes import (
    CATEGORICAL_GENES,
    GENE_SPEC,
    AgentGenome,
    DeckGenome,
    agent_genome_id,
    cell_id,
    deck_genome_id,
    load_pool,
    pool_merge_save,
    save_pool,
    split_cell_id,
)
from ptcg.factory.tournament import (
    MIN_COVERAGE_GAMES,
    MIN_COVERAGE_OPPONENTS,
    MatrixLedger,
    save as save_matrix,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolate_generated(monkeypatch, tmp_path):
    """Never write bred deck csvs into the real repo generated dir."""
    gen = tmp_path / "generated"
    gen.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(evolution, "GENERATED_DIR", gen)


# --- construction helpers -------------------------------------------------


def _base_config() -> dict:
    return {gene: lo for gene, (lo, _hi, _s, _c) in GENE_SPEC.items()} | {
        gene: choices[0] for gene, choices in CATEGORICAL_GENES.items()
    }


def _agents(n: int, *, base: int = 60, status: str = "live") -> list[AgentGenome]:
    """n distinct search agents (distinct search_budget_ms -> distinct id)."""
    out: list[AgentGenome] = []
    for i in range(n):
        cfg = _base_config()
        cfg["search_budget_ms"] = base + i * 10
        out.append(AgentGenome(id=agent_genome_id(cfg), kind="search",
                               config=cfg, status=status))
    return out


def _real_decks(n: int, *, status: str = "live") -> list[DeckGenome]:
    entries = matrix_decks()
    out: list[DeckGenome] = []
    for path_rel, _prio in entries[:n]:
        cards = load_deck(ROOT / path_rel)
        out.append(DeckGenome(id=deck_genome_id(cards), cards=cards,
                             csv=path_rel, status=status))
    return out


def _fake(wins_a: int, wins_b: int):
    def fn(cand_a, cand_b, n_games):
        return wins_a, wins_b
    return fn


def _spy(wins_a: int, wins_b: int, seen: list):
    def fn(cand_a, cand_b, n_games):
        seen.append((cand_a.id, cand_b.id))
        return wins_a, wins_b
    return fn


def _seed(paths: FactoryPaths, agents: list, decks: list) -> None:
    save_pool(_agent_pool_path(paths), agents)
    save_pool(_deck_pool_path(paths), decks)


# --- (a) full tick --------------------------------------------------------


def test_full_tick_plays_records_and_rates(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    agents = _agents(3)
    decks = _real_decks(3)
    _seed(paths, agents, decks)
    seen: list = []

    result = evolution_tick(paths, series_fn=_spy(6, 4, seen))

    assert result == "played"
    assert paths.matrix_heartbeat.exists()
    # exactly one block was played and recorded
    assert len(seen) == 1
    a_cell, b_cell = seen[0]
    ledger = MatrixLedger.load(paths.matrix)
    assert ledger.games_between(a_cell, b_cell) == 10  # 6 + 4 decided games
    assert ledger.meta["evo_tick"] == 1
    # both played cell ids are real (agent, deck) cells
    ag_a, dk_a = split_cell_id(a_cell)
    ag_b, dk_b = split_cell_id(b_cell)
    # the involved genomes got ratings written
    saved_agents = {g.id: g for g in load_pool(_agent_pool_path(paths))}
    saved_decks = {g.id: g for g in load_pool(_deck_pool_path(paths))}
    assert saved_agents[ag_a].rating is not None
    assert saved_agents[ag_b].rating is not None
    assert saved_decks[dk_a].rating is not None


# --- (b) paused / idle ----------------------------------------------------


def test_paused_when_pause_file_present(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    _seed(paths, _agents(3), _real_decks(3))
    paths.pause_file.parent.mkdir(parents=True, exist_ok=True)
    paths.pause_file.write_text("", encoding="utf-8")
    seen: list = []

    result = evolution_tick(paths, series_fn=_spy(6, 4, seen))

    assert result == "paused"
    assert seen == []  # nothing played
    assert paths.matrix_heartbeat.exists()  # heartbeat still written first
    assert not paths.matrix.exists()  # nothing recorded


def test_idle_when_single_cell(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    _seed(paths, _agents(1), _real_decks(1))  # 1 x 1 = 1 cell < 2
    seen: list = []

    result = evolution_tick(paths, series_fn=_spy(6, 4, seen))

    assert result == "idle"
    assert seen == []
    assert paths.matrix_heartbeat.exists()


# --- (c) coverage pairing -------------------------------------------------


def test_coverage_pairing_no_starvation(tmp_path):
    """The pairing key's PRIMARY term is the sum of the four involved
    genomes' total games (summed across ALL of each genome's cells, not
    just the specific cross under consideration) -- a naive 2-term key
    `(games_between, id, id)` can starve an under-played genome whose
    SPECIFIC crosses already carry a few games, even while its overall
    exposure across the population is far behind everyone else's.

    Asymmetric 3 agents x 2 decks = 6 cells. a0/a1 are heavily cross-played
    against each other (20 games per pair) except ONE pair -- (a0,d1) vs
    (a1,d0) -- deliberately left completely unplayed (0 games): under the
    naive key this is the unique global-minimum pair and would be picked
    next regardless of genome coverage. a2 is starved overall (28 total
    games vs a0/a1's 112 each) but every one of ITS crosses already has a
    FEW games recorded (2-3), so none of a2's crosses is the naive minimum
    -- this is exactly the "specific cells have games" case the plan calls
    out. The fix's coverage-first key must still pick a pair involving a2
    (the under-played genome), dragging it to the front of pairing ahead of
    the naive zero-games pair.
    """
    a0, a1, a2 = _agents(3)
    d0, d1 = _real_decks(2)
    c_a0d0 = Cell(agent=a0, deck=d0)
    c_a0d1 = Cell(agent=a0, deck=d1)
    c_a1d0 = Cell(agent=a1, deck=d0)
    c_a1d1 = Cell(agent=a1, deck=d1)
    c_a2d0 = Cell(agent=a2, deck=d0)
    c_a2d1 = Cell(agent=a2, deck=d1)
    all_cells = [c_a0d0, c_a0d1, c_a1d0, c_a1d1, c_a2d0, c_a2d1]

    ledger = MatrixLedger()

    def rec(x: Cell, y: Cell, n: int) -> None:
        ledger.record(x.id, y.id, wins=n, games=n)

    # a0/a1 heavily cross-played -- 5 of the 6 core pairs get 20 games...
    rec(c_a0d0, c_a0d1, 20)
    rec(c_a0d0, c_a1d0, 20)
    rec(c_a0d0, c_a1d1, 20)
    rec(c_a0d1, c_a1d1, 20)
    rec(c_a1d0, c_a1d1, 20)
    # ...except this ONE pair, left at 0 games -- the naive key's target.

    # a2 starved overall but every one of its crosses has SOME games.
    for core in (c_a0d0, c_a0d1, c_a1d0, c_a1d1):
        rec(c_a2d0, core, 3)
        rec(c_a2d1, core, 3)
    rec(c_a2d0, c_a2d1, 2)

    # Sanity: the naive-key target really is the unique global minimum, and
    # no a2 cross ties it -- both required for a clean discriminator.
    assert ledger.games_between(c_a0d1.id, c_a1d0.id) == 0
    for cell in (c_a2d0, c_a2d1):
        for other in all_cells:
            if other.id != cell.id:
                assert ledger.games_between(cell.id, other.id) > 0

    pair = next_cell_pair(all_cells, ledger, playoff=False)
    assert pair is not None
    picked_ids = {pair[0].id, pair[1].id}

    # The naive 2-term key would pick the untouched (a0,d1)/(a1,d0) pair --
    # assert the fix does NOT, and instead drags the under-played a2 in.
    assert picked_ids != {c_a0d1.id, c_a1d0.id}
    assert c_a2d0.id in picked_ids or c_a2d1.id in picked_ids


# --- (d) playoff tick -----------------------------------------------------


def test_playoff_tick_pairs_within_top_cells(tmp_path):
    """5th tick (meta['evo_tick'] == 4 before it) restricts pairing to the
    top-PLAYOFF_TOP cells by combined agent+deck rating."""
    paths = FactoryPaths(root=tmp_path)
    agents = _agents(3)
    decks = _real_decks(3)
    # plant distinct ratings so cell scores rank unambiguously
    for i, a in enumerate(agents):
        a.rating = float(i)          # a0<a1<a2
    for i, d in enumerate(decks):
        d.rating = float(i) * 10.0   # d0<d1<d2 (dominant term)
    _seed(paths, agents, decks)

    # arm the persisted counter so this tick is the 5th (playoff)
    ledger = MatrixLedger()
    ledger.meta["evo_tick"] = 4
    save_matrix(paths.matrix, ledger)

    cells = active_cells(agents, decks)
    scored = sorted(cells, key=lambda c: (evolution._cell_score(c), c.id), reverse=True)
    top_ids = {c.id for c in scored[:PLAYOFF_TOP]}

    seen: list = []
    result = evolution_tick(paths, series_fn=_spy(6, 4, seen))

    assert result == "played"
    assert len(seen) == 1
    assert set(seen[0]) <= top_ids, (seen[0], top_ids)


# --- (e) cull + breed at target -------------------------------------------


def test_cull_breed_retires_worst_and_breeds_replacement(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    agents = _agents(TARGET_AGENTS)           # exactly at target
    decks = _real_decks(1)                      # deck pop under target (grows)
    d0 = decks[0]
    a0 = agents[0]
    _seed(paths, agents, decks)

    # Plant the matrix ledger so a0 loses every game (fit -> lowest rating)
    # and accrues >= GENOME_RETIRE_FLOOR games across its single cell.
    ledger = MatrixLedger()
    a0_cell = cell_id(a0.id, d0.id)
    for other in agents[1:]:
        other_cell = cell_id(other.id, d0.id)
        # `other` wins 5-0 vs a0 -> a0 gets 0 wins, 5 losses per opponent
        ledger.record(other_cell, a0_cell, wins=5, games=5)
    save_matrix(paths.matrix, ledger)
    assert ledger.total_games(a0_cell) >= GENOME_RETIRE_FLOOR  # 11 * 5 = 55

    original_ids = {a.id for a in agents}
    result = evolution_tick(paths, series_fn=_fake(6, 4))
    assert result == "played"

    saved = load_pool(_agent_pool_path(paths))
    by_id = {g.id: g for g in saved}
    assert by_id[a0.id].status == "retired"

    live = [g for g in saved if g.status == "live"]
    assert len(live) == TARGET_AGENTS  # retire 1 + breed 1 -> net unchanged

    children = [g for g in live if g.id not in original_ids]
    assert len(children) == 1
    child = children[0]
    assert a0.id not in child.lineage

    # reconstruct exactly the candidate set + head select_parents saw
    # (live survivors, excluding the not-yet-born child).
    candidates = [g for g in saved if g.status == "live" and g.id != child.id]
    ranked = sorted(candidates, key=lambda g: (g.rating is None,
                    -(g.rating if g.rating is not None else 0.0)))
    head_size = max(2, math.ceil(0.25 * len(candidates)))
    head_ids = {g.id for g in ranked[:head_size]}
    assert child.lineage  # bred from parents
    assert set(child.lineage) <= head_ids


# --- (f) under-target growth ----------------------------------------------


def test_under_target_growth_breeds_without_retiring(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    agents = _agents(4)  # < TARGET_AGENTS
    decks = _real_decks(1)
    original_ids = {a.id for a in agents}
    _seed(paths, agents, decks)

    result = evolution_tick(paths, series_fn=_fake(6, 4))
    assert result == "played"

    saved = load_pool(_agent_pool_path(paths))
    assert all(g.status != "retired" for g in saved)  # nothing retired
    live = [g for g in saved if g.status == "live"]
    assert len(live) == 5  # 4 + one newborn
    newborn = [g for g in live if g.id not in original_ids]
    assert len(newborn) == 1
    assert newborn[0].lineage  # has parents


# --- (g) interleaved-mutation (mandatory) ---------------------------------


def test_concurrent_deck_pool_write_survives_the_tick(tmp_path, monkeypatch):
    """A concurrent write to deck_pool.json that lands DURING play_block
    must survive the tick, AND the tick's own rating writes must be present.

    Discriminating for the stale-clobber discipline: the concurrent actor
    (1) adds a brand-new meta-anchor deck and (2) stamps a sentinel `notes`
    onto the EXISTING deck d0. Because pool_merge_save does a full-row
    replace by id, a tick that saved the pools it loaded BEFORE play_block
    would overwrite d0's sentinel with its stale (notes="") copy -> the
    notes assertion FAILS. Only a tick that reloads pools fresh under the
    matrix lock (after play_block) preserves the concurrent stamp.

    Breeding is disabled (select_parents -> []) so this test isolates the
    concurrency/reload-fresh contract: without it, a bred deck child can
    coincidentally collide in content-addressed id with the injected
    meta-anchor (the curated seed decks are mutation-related), muddying the
    survival assertion.
    """
    monkeypatch.setattr(evolution, "select_parents", lambda *a, **k: [])
    paths = FactoryPaths(root=tmp_path)
    agents = _agents(2)
    decks = _real_decks(1)
    d0 = decks[0]
    _seed(paths, agents, decks)

    # a legal meta-anchor deck genome distinct from d0
    other_cards = load_deck(ROOT / matrix_decks()[1][0])
    injected = DeckGenome(id=deck_genome_id(other_cards), cards=other_cards,
                          csv=matrix_decks()[1][0], status="meta-anchor")

    def concurrent_write_series(cand_a, cand_b, n_games):
        # Fires mid-play_block, exactly in the pre-lock stale window.
        fresh = {g.id: g for g in load_pool(_deck_pool_path(paths))}
        touched = fresh[d0.id]
        touched.notes = "CONCURRENT"
        pool_merge_save(_deck_pool_path(paths), [touched, injected])
        return 6, 4

    result = evolution_tick(paths, series_fn=concurrent_write_series)
    assert result == "played"

    saved = {g.id: g for g in load_pool(_deck_pool_path(paths))}
    # (1) the concurrent new genome survived
    assert injected.id in saved
    assert saved[injected.id].status == "meta-anchor"
    # (2) the concurrent stamp on the existing deck survived (reload-fresh)
    assert saved[d0.id].notes == "CONCURRENT"
    # (3) the tick's own rating write landed on d0
    assert saved[d0.id].rating is not None


# --- (h) reproducibility --------------------------------------------------


def _strip_born_at(genomes: list[dict]) -> list[dict]:
    out = []
    for row in genomes:
        row = dict(row)
        row.pop("born_at", None)
        out.append(row)
    return out


def _pools_json(paths: FactoryPaths) -> tuple[list, list]:
    def _load(p: Path) -> list:
        payload = json.loads(p.read_text(encoding="utf-8"))
        return _strip_born_at(payload["genomes"])
    return _load(_agent_pool_path(paths)), _load(_deck_pool_path(paths))


def test_reproducible_across_identical_runs(tmp_path):
    def _run(root: Path):
        paths = FactoryPaths(root=root)
        _seed(paths, _agents(3), _real_decks(3))
        for _ in range(3):
            evolution_tick(paths, series_fn=_fake(6, 4))
        return _pools_json(paths)

    agents_a, decks_a = _run(tmp_path / "a")
    agents_b, decks_b = _run(tmp_path / "b")

    assert agents_a == agents_b
    assert decks_a == decks_b


# --- (i) anchor clobber-proofing (Task 3b) ---------------------------------


def test_cull_never_retires_anchor_even_when_lowest_rated():
    """Regression pin -- no production change needed: `_cull_and_breed`
    already restricts retirement to status=="live" genomes (the `live`
    list is built by filtering on that status before `min()` ever runs).
    A status="anchor" genome with the LOWEST rating in the whole pool must
    never be retired even when it would otherwise be the cull target.
    """
    anchor = DeckGenome(id="dk-anchor0001", cards=[3] * 60, csv="anchor.csv",
                        status="anchor", rating=-100.0, games=GENOME_RETIRE_FLOOR + 5)
    live_genomes = [
        DeckGenome(id=f"dk-live{i:04d}", cards=[4 + i] * 60, csv=f"live{i}.csv",
                  status="live", rating=float(i), games=GENOME_RETIRE_FLOOR + 1)
        for i in range(TARGET_DECKS)
    ]
    pool = [anchor, *live_genomes]

    def stub_breed_fn(rng, parents):
        return DeckGenome(id="dk-newborn001", cards=[99] * 60, csv="newborn.csv",
                          status="live", lineage=[p.id for p in parents])

    now = dt.datetime(2026, 7, 22, tzinfo=dt.timezone.utc)
    child = evolution._cull_and_breed(pool, target=TARGET_DECKS,
                                      breed_fn=stub_breed_fn, rng=random.Random(0), now=now)

    assert anchor.status == "anchor"  # never retired, despite the lowest rating in the pool
    assert child is not None
    retired = [g for g in pool if g.status == "retired"]
    assert len(retired) == 1
    assert retired[0].id != anchor.id
    assert retired[0].rating == 0.0  # the lowest-rated LIVE genome (i=0), not the anchor


def test_bred_child_with_existing_id_is_discarded_not_clobbered(tmp_path):
    """RED-before-fix: `_cull_and_breed` appended a content-identical bred
    child unconditionally; `pool_merge_save`'s by-id replace then silently
    overwrote the existing anchor row's status/lineage with the newborn's --
    the exact mechanism that clobbered the production seeded deck anchor
    (dk-4d8084cbdc) into a retired bred row. Captured at the
    `pool_merge_save` output level (a real tmp pool file) since the clobber
    only manifests once the by-id merge actually runs -- an in-memory-only
    assertion on `pool` would miss it.
    """
    anchor = DeckGenome(id="dk-anchor0002", cards=[3] * 60, csv="anchor.csv",
                        status="anchor", lineage=[], rating=0.7,
                        notes="anchor row -- must survive")
    live1 = DeckGenome(id="dk-live00001", cards=[4] * 60, csv="live1.csv",
                       status="live", rating=0.3)
    live2 = DeckGenome(id="dk-live00002", cards=[5] * 60, csv="live2.csv",
                       status="live", rating=0.1)
    pool = [anchor, live1, live2]
    pool_path = tmp_path / "deck_pool.json"
    save_pool(pool_path, pool)

    def stub_breed_fn(rng, parents):
        # Content-identical rebirth: the bred child's content hash
        # collides with the existing anchor row's id.
        return DeckGenome(id=anchor.id, cards=anchor.cards, csv="bred.csv",
                          status="live", lineage=[p.id for p in parents])

    now = dt.datetime(2026, 7, 22, tzinfo=dt.timezone.utc)
    child = evolution._cull_and_breed(pool, target=10, breed_fn=stub_breed_fn,
                                      rng=random.Random(0), now=now)

    assert child is None  # discarded, not appended -- RED today: not None

    pool_merge_save(pool_path, pool)  # persist whatever _cull_and_breed left in `pool`
    saved = {g.id: g for g in load_pool(pool_path)}
    assert len(saved) == 3  # no phantom duplicate row
    assert saved[anchor.id].status == "anchor"  # RED today: clobbered to "live"
    assert saved[anchor.id].lineage == []  # RED today: clobbered to the child's lineage
    assert saved[anchor.id].notes == "anchor row -- must survive"  # RED today: clobbered to ""


# --- (j) diversify pairing mode (Task 4) -----------------------------------


def test_diversify_mode_pairs_frontier_cell_with_fresh_opponent():
    """Frontier cell F has ample games (20 >= MIN_COVERAGE_GAMES) but only 2
    distinct opponents (< MIN_COVERAGE_OPPONENTS): diversify mode must pair
    it with a cell it has NEVER played, choosing among fresh candidates the
    one that itself minimizes `_coverage` (agent-games + deck-games summed
    across the whole pool) -- reusing coverage mode's own key.

    Per the plan-authored-test-arithmetic-sanity rule, the expected fresh
    opponent is independently RECOMPUTED here from the same `_coverage`
    formula rather than hardcoded by content-hash id (genome ids are
    content-addressed and unpredictable without running the real hash).
    """
    a0, a1, a2 = _agents(3)
    d0, d1, d2 = _real_decks(3)
    focal = Cell(agent=a0, deck=d0)
    played1 = Cell(agent=a0, deck=d1)
    played2 = Cell(agent=a0, deck=d2)
    other_cells = [Cell(agent=a, deck=d)
                  for a in (a1, a2) for d in (d0, d1, d2)]
    all_cells = [focal, played1, played2, *other_cells]
    assert len(all_cells) == 9

    ledger = MatrixLedger()
    ledger.record(focal.id, played1.id, wins=12, games=12)
    ledger.record(focal.id, played2.id, wins=8, games=8)

    # Fixture sanity (hand-checked, not trusted from plan prose):
    assert ledger.total_games(focal.id) == 20 >= MIN_COVERAGE_GAMES
    assert len(ledger.opponents_of(focal.id)) == 2 < MIN_COVERAGE_OPPONENTS

    pair = next_cell_pair(all_cells, ledger, playoff=False, diversify=True)
    assert pair is not None
    assert pair[0].id == focal.id

    fresh_opponents = [x for x in all_cells
                       if x.id != focal.id and ledger.games_between(focal.id, x.id) == 0]
    assert pair[1].id in {x.id for x in fresh_opponents}  # genuinely never-played

    agent_games: dict[str, int] = {}
    deck_games: dict[str, int] = {}
    for c in all_cells:
        tg = ledger.total_games(c.id)
        agent_games[c.agent.id] = agent_games.get(c.agent.id, 0) + tg
        deck_games[c.deck.id] = deck_games.get(c.deck.id, 0) + tg

    def _coverage(cell: Cell) -> int:
        return agent_games[cell.agent.id] + deck_games[cell.deck.id]

    expected = min(fresh_opponents, key=lambda x: (_coverage(x), x.id))
    assert pair[1].id == expected.id


def test_diversify_mode_falls_through_when_no_frontier():
    """All cells below MIN_COVERAGE_GAMES (a virgin ledger) -> no frontier
    cell exists -> diversify=True must produce the byte-identical result to
    diversify=False for the same inputs."""
    a0, a1, a2 = _agents(3)
    d0, d1, d2 = _real_decks(3)
    all_cells = [Cell(agent=a, deck=d) for a in (a0, a1, a2) for d in (d0, d1, d2)]
    ledger = MatrixLedger()  # nothing recorded

    result_off = next_cell_pair(all_cells, ledger, playoff=False, diversify=False)
    result_on = next_cell_pair(all_cells, ledger, playoff=False, diversify=True)

    assert result_off is not None and result_on is not None
    assert {result_off[0].id, result_off[1].id} == {result_on[0].id, result_on[1].id}


def test_diversify_cadence_in_tick(tmp_path, monkeypatch):
    """Residues of (tick_count + 1) % PLAYOFF_EVERY cycle 1,2,3,4,0 across 5
    successive ticks: diversify=True fires exactly once (residue 2, the 2nd
    tick), playoff=True fires exactly once (residue 0, the 5th tick), and
    the two are never simultaneously True (mutually exclusive by cadence
    construction, independent of `next_cell_pair`'s own playoff-wins-if-both
    precedence)."""
    paths = FactoryPaths(root=tmp_path)
    agents = _agents(3)
    decks = _real_decks(3)
    _seed(paths, agents, decks)
    cells = active_cells(agents, decks)
    fixed_pair = (cells[0], cells[1])

    seen_flags: list[tuple[bool, bool]] = []

    def spy_next_cell_pair(cells_arg, ledger_arg, playoff, diversify=False):
        seen_flags.append((playoff, diversify))
        return fixed_pair

    monkeypatch.setattr(evolution, "next_cell_pair", spy_next_cell_pair)

    for _ in range(5):
        result = evolution_tick(paths, series_fn=_fake(6, 4))
        assert result == "played"

    assert seen_flags == [
        (False, False),
        (False, True),
        (False, False),
        (False, False),
        (True, False),
    ]
    assert sum(1 for _, d in seen_flags if d) == 1
    assert sum(1 for p, _ in seen_flags if p) == 1
    assert not any(p and d for p, d in seen_flags)


def test_simulated_ticks_grow_distinct_opponents():
    """Fail-power test for the whole diversify feature: a 3 agent x 4 deck =
    12-cell pool, seeded with a PRODUCTION-shaped concentrated starting
    ledger (per this task's Investigation record: a focal cell with ample
    games but few distinct opponents, plus several completely-unplayed
    cells). Diversify-enabled ticks must grow the population's max
    distinct-opponent count strictly more than the concentrate-only
    (diversify disabled) baseline, and reach the MIN_COVERAGE_OPPONENTS
    floor (8; 11 is the ceiling with 12 cells).

    Per the plan-test-arithmetic-sanity rule's iterative-dynamics
    refinement, this was RUN to convergence before pinning N_TICKS: a
    VIRGIN (empty) starting ledger was tried first via a standalone scratch
    simulation and does NOT discriminate on this small 12-cell fixture out
    to 200 ticks (mode-off actually edges ahead at some tick counts) --
    coverage mode's own SECONDARY `games_between` tiebreak already
    round-robins a small, freshly-started, symmetric pool efficiently
    whenever every pair ties on the PRIMARY summed-coverage term, so
    diversify's periodic redirect only costs ticks there. The concentrated
    seed below reproduces the actual starvation SHAPE the plan's
    Investigation record measured in production (ample games, few
    opponents) -- diversify discriminates clearly on that shape, which is
    the shape the feature exists to fix. This is a deliberate, receipt-backed
    fixture adjustment (not a tick-count increase) -- see the implementer's
    task report for the scratch-simulation numbers at both starts.
    """
    a0, a1, a2 = _agents(3)
    d0, d1, d2, d3 = _real_decks(4)
    all_cells = [Cell(agent=a, deck=d) for a in (a0, a1, a2) for d in (d0, d1, d2, d3)]
    assert len(all_cells) == 12
    N_TICKS = 40  # convergence-verified via scratch simulation before pinning

    def _seed_concentrated(ledger: MatrixLedger) -> None:
        focal = all_cells[0]
        ledger.record(focal.id, all_cells[1].id, wins=60, games=120)
        ledger.record(focal.id, all_cells[2].id, wins=30, games=50)
        for i in range(3, 7):
            ledger.record(all_cells[i].id, all_cells[(i + 1) % len(all_cells)].id,
                          wins=3, games=6)

    def _simulate(diversify_enabled: bool) -> MatrixLedger:
        ledger = MatrixLedger()
        _seed_concentrated(ledger)
        for tick_count in range(N_TICKS):
            playoff = (tick_count + 1) % PLAYOFF_EVERY == 0
            diversify = diversify_enabled and (tick_count + 1) % PLAYOFF_EVERY == 2
            pair = next_cell_pair(all_cells, ledger, playoff, diversify=diversify)
            assert pair is not None
            ledger.record(pair[0].id, pair[1].id, wins=6, games=10)
        return ledger

    ledger_off = _simulate(diversify_enabled=False)
    ledger_on = _simulate(diversify_enabled=True)

    max_off = max(len(ledger_off.opponents_of(c.id)) for c in all_cells)
    max_on = max(len(ledger_on.opponents_of(c.id)) for c in all_cells)

    assert max_on > max_off  # comparative -- robust to constant drift
    assert max_on >= MIN_COVERAGE_OPPONENTS  # at least one cell clears the floor
