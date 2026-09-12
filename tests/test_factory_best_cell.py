"""Tests for evolution.py's SELECTION + GATE SNAPSHOT section (Task 6):
`select_best_cell`, `snapshot_cell`, and the `evo_gate_step` watch-loop
wiring (incumbent scale-bridge + the legacy refill_queue skip).

Most fixtures use synthetic dummy decks (no cg engine dependency) since
`select_best_cell`/`snapshot_cell` never read deck CSV content. Only the
incumbent scale-bridge tests need a REAL on-disk deck csv, because
`evo_gate_step` resolves the incumbent's anchor cell by reading the
incumbent's `deck` path via `ptcg.arena.runner.load_deck` -- so those tests
reuse a real seed deck from `deck_matrix.matrix_decks()`, exactly like
`tests/test_factory_evolution_tick.py`'s `_real_decks` helper does.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from ptcg.arena.runner import load_deck
from ptcg.factory import evolution
from ptcg.factory.candidates import Candidate, Status, load_ledger, save_ledger
from ptcg.factory.cycle import FactoryPaths
from ptcg.factory.deck_matrix import matrix_decks
from ptcg.factory.evaluate import EvalConfig
from ptcg.factory.evolution import (
    Cell,
    _agent_pool_path,
    _deck_pool_path,
    ensure_incumbent_anchor,
    evo_gate_step,
    select_best_cell,
    snapshot_cell,
)
from ptcg.factory.gate import has_matrix_coverage
from ptcg.factory.gate import incumbent as gate_incumbent
from ptcg.factory.genomes import (
    AgentGenome,
    DeckGenome,
    agent_genome_id,
    cell_id,
    deck_genome_id,
    load_pool,
    pool_merge_save,
    save_pool,
)
from ptcg.factory.tournament import MIN_COVERAGE_GAMES, MIN_COVERAGE_OPPONENTS, MatrixLedger
from ptcg.factory.tournament import save as save_matrix

ROOT = Path(__file__).resolve().parents[1]


# --- construction helpers -------------------------------------------------


def _agent_with_config(cfg: dict, *, kind: str = "search", status: str = "live",
                       rating: float | None = None) -> AgentGenome:
    return AgentGenome(id=agent_genome_id(cfg), kind=kind, config=cfg,
                       status=status, rating=rating)


def _agent(tag: int, *, rating: float | None = None, status: str = "live") -> AgentGenome:
    return _agent_with_config({"search_budget_ms": 100 + tag}, status=status, rating=rating)


def _dummy_deck(*, rating: float | None = None, status: str = "live") -> DeckGenome:
    cards = [3] * 60
    return DeckGenome(id=deck_genome_id(cards), cards=cards, csv="dummy.csv",
                      status=status, rating=rating)


# --- (a) both floors honored -----------------------------------------------


def test_select_best_cell_honors_both_floors():
    deck0 = _dummy_deck(rating=0.0)
    weak_agent = _agent(0, rating=1.0)     # modest rating, floor-clearing
    strong_agent = _agent(1, rating=100.0)  # huge rating, but under-floor
    helpers = [_agent(2 + i, rating=0.0) for i in range(8)]

    ledger = MatrixLedger()
    weak_cell_id = cell_id(weak_agent.id, deck0.id)
    for h in helpers:
        ledger.record(weak_cell_id, cell_id(h.id, deck0.id), wins=2, games=4)
    strong_cell_id = cell_id(strong_agent.id, deck0.id)
    ledger.record(strong_cell_id, cell_id(helpers[0].id, deck0.id), wins=1, games=2)

    # sanity: strong cell is under-floor, weak cell clears both floors
    assert ledger.total_games(strong_cell_id) < evolution.CELL_MIN_GAMES
    assert ledger.total_games(weak_cell_id) >= evolution.CELL_MIN_GAMES
    assert len(ledger.opponents_of(weak_cell_id)) >= evolution.CELL_MIN_OPPONENTS

    result = select_best_cell([strong_agent, weak_agent, *helpers], [deck0], ledger)

    assert result is not None
    assert result.id == weak_cell_id


def test_select_best_cell_returns_none_when_nothing_eligible():
    deck0 = _dummy_deck(rating=0.0)
    agents = [_agent(0, rating=5.0), _agent(1, rating=0.0)]
    ledger = MatrixLedger()  # no games recorded at all

    assert select_best_cell(agents, [deck0], ledger) is None


# --- (b) tiebreak by observed win rate within 1e-9 -------------------------


def test_select_best_cell_tiebreaks_by_win_rate_within_epsilon():
    deck0 = _dummy_deck(rating=0.0)
    agent_a = _agent(0, rating=2.0)
    agent_b = _agent(1, rating=2.0)  # identical rating -> tied strength
    helpers = [_agent(2 + i, rating=0.0) for i in range(8)]

    ledger = MatrixLedger()
    cell_a_id = cell_id(agent_a.id, deck0.id)
    cell_b_id = cell_id(agent_b.id, deck0.id)
    for h in helpers:
        h_cell_id = cell_id(h.id, deck0.id)
        ledger.record(cell_a_id, h_cell_id, wins=2, games=4)   # A: 50% win rate
        ledger.record(cell_b_id, h_cell_id, wins=3, games=4)   # B: 75% win rate

    result = select_best_cell([agent_a, agent_b, *helpers], [deck0], ledger)

    assert result is not None
    assert result.id == cell_b_id


# --- (c) snapshot passes has_matrix_coverage + version bump ---------------


def test_snapshot_cell_passes_coverage_and_bumps_version_past_retired():
    deck0 = _dummy_deck(rating=0.5)
    agent0 = _agent(0, rating=1.5)
    helpers = [_agent(1 + i, rating=0.0) for i in range(8)]
    cell = Cell(agent=agent0, deck=deck0)

    ledger = MatrixLedger()
    for h in helpers:
        ledger.record(cell.id, cell_id(h.id, deck0.id), wins=2, games=4)

    candidates: list[Candidate] = []
    snap1 = snapshot_cell(cell, ledger, candidates)

    assert snap1 is not None
    assert snap1.provenance == "evolved"
    assert snap1.status == Status.EVALUATED
    assert snap1.name == f"evo-{agent0.id}-{deck0.id}"
    assert snap1.version == "v0.1"
    assert snap1.matrix_rating == pytest.approx(math.exp(1.5 + 0.5))
    assert has_matrix_coverage(snap1)

    # a RETIRED prior entry of the same name does not block a new snapshot,
    # and next_version bumps past it rather than restarting at v0.1.
    snap1.status = Status.RETIRED
    candidates.append(snap1)
    snap2 = snapshot_cell(cell, ledger, candidates)

    assert snap2 is not None
    assert snap2.name == snap1.name
    assert snap2.version == "v0.2"


# --- (d) duplicate-cell snapshot returns None ------------------------------


def test_snapshot_cell_returns_none_for_non_retired_duplicate():
    deck0 = _dummy_deck(rating=0.5)
    agent0 = _agent(0, rating=1.5)
    helpers = [_agent(1 + i, rating=0.0) for i in range(8)]
    cell = Cell(agent=agent0, deck=deck0)

    ledger = MatrixLedger()
    for h in helpers:
        ledger.record(cell.id, cell_id(h.id, deck0.id), wins=2, games=4)

    candidates: list[Candidate] = []
    snap1 = snapshot_cell(cell, ledger, candidates)
    assert snap1 is not None
    candidates.append(snap1)  # still EVALUATED (non-retired)

    snap2 = snapshot_cell(cell, ledger, candidates)

    assert snap2 is None


# --- (e) incumbent scale-bridge --------------------------------------------


def _real_deck():
    deck_path, _prio = matrix_decks()[0]
    cards = load_deck(ROOT / deck_path)
    return deck_path, cards


def test_evo_gate_step_refreshes_incumbent_from_covered_anchor(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    deck_path, cards = _real_deck()
    inc_agent_config = {"search_budget_ms": 321}

    inc = Candidate.create(name="incumbent", version="v1.0", deck=deck_path,
                           agent_kind="search-net", agent_config=inc_agent_config,
                           status=Status.SUBMITTED, is_incumbent=True, local_wr=0.6)
    save_ledger(paths.ledger, [inc])

    anchor_agent = _agent_with_config(inc_agent_config, status="anchor", rating=1.5)
    anchor_deck = DeckGenome(id=deck_genome_id(cards), cards=cards, csv=deck_path,
                             status="anchor", rating=0.5)
    helpers = [_agent(500 + i, rating=0.0) for i in range(8)]
    save_pool(_agent_pool_path(paths), [anchor_agent, *helpers])
    save_pool(_deck_pool_path(paths), [anchor_deck])

    ledger = MatrixLedger()
    anchor_cell_id = cell_id(anchor_agent.id, anchor_deck.id)
    for h in helpers:
        ledger.record(anchor_cell_id, cell_id(h.id, anchor_deck.id), wins=1, games=2)
    save_matrix(paths.matrix, ledger)
    assert ledger.total_games(anchor_cell_id) >= MIN_COVERAGE_GAMES
    assert len(ledger.opponents_of(anchor_cell_id)) >= MIN_COVERAGE_OPPONENTS

    logs: list[str] = []
    result = evo_gate_step(paths, log=logs.append)

    assert result == [inc.id]
    assert not any("uncovered" in m for m in logs)
    refreshed = {c.id: c for c in load_ledger(paths.ledger)}
    updated_inc = refreshed[inc.id]
    assert updated_inc.matrix_rating == pytest.approx(math.exp(1.5 + 0.5))
    assert updated_inc.matrix_games == 16
    assert updated_inc.matrix_opponents == 8


def test_evo_gate_step_skips_entirely_when_anchor_uncovered(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    deck_path, cards = _real_deck()
    inc_agent_config = {"search_budget_ms": 321}

    inc = Candidate.create(name="incumbent", version="v1.0", deck=deck_path,
                           agent_kind="search-net", agent_config=inc_agent_config,
                           status=Status.SUBMITTED, is_incumbent=True, local_wr=0.6)
    save_ledger(paths.ledger, [inc])

    anchor_agent = _agent_with_config(inc_agent_config, status="anchor", rating=1.5)
    anchor_deck = DeckGenome(id=deck_genome_id(cards), cards=cards, csv=deck_path,
                             status="anchor", rating=0.5)
    helpers = [_agent(600 + i, rating=0.0) for i in range(3)]  # only 3 < 8 floor
    save_pool(_agent_pool_path(paths), [anchor_agent, *helpers])
    save_pool(_deck_pool_path(paths), [anchor_deck])

    ledger = MatrixLedger()
    anchor_cell_id = cell_id(anchor_agent.id, anchor_deck.id)
    for h in helpers:
        ledger.record(anchor_cell_id, cell_id(h.id, anchor_deck.id), wins=1, games=2)
    save_matrix(paths.matrix, ledger)
    assert len(ledger.opponents_of(anchor_cell_id)) < MIN_COVERAGE_OPPONENTS

    logs: list[str] = []
    result = evo_gate_step(paths, log=logs.append)

    assert result == []
    assert any("uncovered" in m for m in logs)
    unchanged = {c.id: c for c in load_ledger(paths.ledger)}
    assert unchanged[inc.id].matrix_rating is None  # never touched


def _old_flag_only_selector(candidates: list[Candidate]) -> Candidate | None:
    """Replica of evo_gate_step's PRE-FIX incumbent selector: first
    is_incumbent-flagged, non-retired candidate in list order. Kept here
    (not in source) purely to demonstrate divergence from gate.incumbent()
    in the two tests below -- the real evo_gate_step now calls
    gate.incumbent() directly and no longer contains this logic."""
    return next((c for c in candidates
                if getattr(c, "is_incumbent", False)
                and c.status != Status.RETIRED), None)


def test_evo_gate_step_selects_incumbent_via_gate_incumbent_not_flag_only(tmp_path):
    """RED-GREEN receipt for the Task-6 scale-bridge divergence finding.
    Two candidates both carry is_incumbent=True with different local_wr.
    The old flag-only selector picks whichever comes FIRST in list order
    (ignoring local_wr); gate.incumbent() picks the HIGHEST local_wr among
    several flagged candidates (its documented "strictest bar wins" rule).
    inc_low is listed first, so the two selectors provably disagree on this
    fixture -- and only inc_high's anchor cell is wired with coverage, so
    the old selector's pick (inc_low) would have found no anchor and
    skipped, while the correct pick (inc_high) finds a covered anchor and
    gets its matrix fields refreshed.
    """
    paths = FactoryPaths(root=tmp_path)
    deck_path, cards = _real_deck()
    low_agent_config = {"search_budget_ms": 111}
    high_agent_config = {"search_budget_ms": 222}

    inc_low = Candidate.create(name="incumbent-low", version="v1.0", deck=deck_path,
                               agent_kind="search-net", agent_config=low_agent_config,
                               status=Status.SUBMITTED, is_incumbent=True, local_wr=0.3)
    inc_high = Candidate.create(name="incumbent-high", version="v1.0", deck=deck_path,
                                agent_kind="search-net", agent_config=high_agent_config,
                                status=Status.SUBMITTED, is_incumbent=True, local_wr=0.9)
    candidates = [inc_low, inc_high]  # inc_low FIRST -- old selector would pick it
    save_ledger(paths.ledger, candidates)

    # RED: the old (flag-only) selector and gate.incumbent() provably diverge
    # on this exact fixture.
    assert _old_flag_only_selector(candidates).id == inc_low.id
    assert gate_incumbent(candidates).id == inc_high.id
    assert _old_flag_only_selector(candidates).id != gate_incumbent(candidates).id

    # Only inc_high's config has a matching, covered anchor cell.
    anchor_agent = _agent_with_config(high_agent_config, status="anchor", rating=2.0)
    anchor_deck = DeckGenome(id=deck_genome_id(cards), cards=cards, csv=deck_path,
                             status="anchor", rating=1.0)
    helpers = [_agent(700 + i, rating=0.0) for i in range(8)]
    save_pool(_agent_pool_path(paths), [anchor_agent, *helpers])
    save_pool(_deck_pool_path(paths), [anchor_deck])

    ledger = MatrixLedger()
    anchor_cell_id = cell_id(anchor_agent.id, anchor_deck.id)
    for h in helpers:
        ledger.record(anchor_cell_id, cell_id(h.id, anchor_deck.id), wins=1, games=2)
    save_matrix(paths.matrix, ledger)

    logs: list[str] = []
    result = evo_gate_step(paths, log=logs.append)

    # GREEN: evo_gate_step follows gate.incumbent()'s pick (inc_high), not
    # the old flag-only selector's pick (inc_low).
    assert result == [inc_high.id]
    assert not any("uncovered" in m or "no incumbent" in m for m in logs)
    refreshed = {c.id: c for c in load_ledger(paths.ledger)}
    assert refreshed[inc_high.id].matrix_rating == pytest.approx(math.exp(2.0 + 1.0))
    assert refreshed[inc_low.id].matrix_rating is None  # untouched


def test_evo_gate_step_skips_with_no_incumbent_log_when_flag_unselectable(tmp_path):
    """A candidate can carry is_incumbent=True yet still be unselectable by
    gate.incumbent() (no local_wr, and not part of the counted-submissions
    fallback either) -- evo_gate_step must defer to gate.incumbent()'s
    verdict (None) rather than the raw flag, and log a message distinct
    from the anchor-uncovered case so watch.log can tell the two skip
    reasons apart. The anchor cell below IS covered, proving the skip is
    driven by "no incumbent" and not by anchor coverage -- the old
    flag-only selector would have picked this candidate and refreshed it
    despite gate.decide() never being able to use it as a bar.
    """
    paths = FactoryPaths(root=tmp_path)
    deck_path, cards = _real_deck()
    inc_agent_config = {"search_budget_ms": 321}

    inc = Candidate.create(name="incumbent", version="v1.0", deck=deck_path,
                           agent_kind="search-net", agent_config=inc_agent_config,
                           status=Status.QUEUED, is_incumbent=True, local_wr=None)
    save_ledger(paths.ledger, [inc])

    # RED: sanity-check the fixture's premise -- the old flag-only selector
    # would still pick `inc` (it never looked at local_wr or counted-pair
    # membership), while gate.incumbent() correctly refuses to.
    assert _old_flag_only_selector([inc]).id == inc.id
    assert gate_incumbent([inc]) is None

    anchor_agent = _agent_with_config(inc_agent_config, status="anchor", rating=1.5)
    anchor_deck = DeckGenome(id=deck_genome_id(cards), cards=cards, csv=deck_path,
                             status="anchor", rating=0.5)
    helpers = [_agent(800 + i, rating=0.0) for i in range(8)]
    save_pool(_agent_pool_path(paths), [anchor_agent, *helpers])
    save_pool(_deck_pool_path(paths), [anchor_deck])

    ledger = MatrixLedger()
    anchor_cell_id = cell_id(anchor_agent.id, anchor_deck.id)
    for h in helpers:
        ledger.record(anchor_cell_id, cell_id(h.id, anchor_deck.id), wins=1, games=2)
    save_matrix(paths.matrix, ledger)
    assert ledger.total_games(anchor_cell_id) >= MIN_COVERAGE_GAMES
    assert len(ledger.opponents_of(anchor_cell_id)) >= MIN_COVERAGE_OPPONENTS

    logs: list[str] = []
    result = evo_gate_step(paths, log=logs.append)

    # GREEN: skipped entirely via the "no incumbent" log, not "uncovered".
    assert result == []
    assert any("no incumbent" in m for m in logs)
    assert not any("uncovered" in m for m in logs)
    unchanged = {c.id: c for c in load_ledger(paths.ledger)}
    assert unchanged[inc.id].matrix_rating is None  # never touched


def test_evo_gate_step_noop_when_pools_not_seeded(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    save_ledger(paths.ledger, [])

    result = evo_gate_step(paths, log=lambda m: None)

    assert result == []


# NOTE: the legacy `test_watch_once_skips_refill_queue_when_agent_pool_exists`
# was REMOVED at the T20 cutover -- the narrowed watch loop no longer does
# candidate refill at all (evolution/deck-matrix retired from the loop), so
# there is no refill-skip path left to assert here. `evo_gate_step` itself is
# untouched and still unit-tested directly above.


# --- constant-collision sanity ----------------------------------------------


def test_cell_min_constants_honor_the_resolved_collision():
    assert evolution.CELL_MIN_GAMES == max(30, MIN_COVERAGE_GAMES)
    assert evolution.CELL_MIN_OPPONENTS == MIN_COVERAGE_OPPONENTS


# --- (g) ensure_incumbent_anchor idempotent guard (Task 3a/3c) -------------


def test_ensure_incumbent_anchor_flips_retired_deck_row(tmp_path):
    """Mirrors today's real production shape: the agent anchor already
    exists and is healthy; the deck anchor was seeded but a content-
    identical bred child later clobbered it to status="retired" (the
    dk-4d8084cbdc incident). The guard flips it back to "anchor",
    preserving every other field, and evo_gate_step's own coverage path
    then resolves.
    """
    paths = FactoryPaths(root=tmp_path)
    deck_path, cards = _real_deck()
    inc = Candidate.create(name="incumbent", version="v1.0", deck=deck_path,
                           agent_kind="heuristic", agent_config={},
                           status=Status.SUBMITTED, is_incumbent=True, local_wr=0.55)
    save_ledger(paths.ledger, [inc])

    agent_target_id = agent_genome_id({})
    deck_target_id = deck_genome_id(cards)
    anchor_agent = AgentGenome(id=agent_target_id, kind="heuristic", config={},
                               status="anchor", rating=1.5)
    retired_deck = DeckGenome(id=deck_target_id, cards=cards, csv=deck_path,
                              status="retired", lineage=["dk-parent0001", "dk-parent0002"],
                              rating=0.5, notes="bred child that clobbered the anchor")
    helpers = [_agent(900 + i, rating=0.0) for i in range(8)]
    save_pool(_agent_pool_path(paths), [anchor_agent, *helpers])
    save_pool(_deck_pool_path(paths), [retired_deck])

    ledger = MatrixLedger()
    anchor_cell_id = cell_id(agent_target_id, deck_target_id)
    for h in helpers:
        ledger.record(anchor_cell_id, cell_id(h.id, deck_target_id), wins=1, games=2)
    save_matrix(paths.matrix, ledger)

    outcome = ensure_incumbent_anchor(paths)

    assert outcome == "deck:flipped-to-anchor"
    flipped = {g.id: g for g in load_pool(_deck_pool_path(paths))}[deck_target_id]
    assert flipped.status == "anchor"
    assert flipped.lineage == ["dk-parent0001", "dk-parent0002"]  # preserved
    assert flipped.rating == 0.5                                   # preserved
    assert flipped.notes == "bred child that clobbered the anchor"  # preserved

    # evo_gate_step's own coverage path now resolves via the flipped row.
    gate_logs: list[str] = []
    result = evo_gate_step(paths, log=gate_logs.append)
    assert result == [inc.id]
    assert not any("uncovered" in m for m in gate_logs)


def test_ensure_incumbent_anchor_creates_missing_rows(tmp_path):
    paths = FactoryPaths(root=tmp_path)
    deck_path, cards = _real_deck()
    inc_agent_config = {"search_budget_ms": 555,
                        "net_weights": "src/ptcg/search/value_net_weights.json"}
    inc = Candidate.create(name="incumbent", version="v1.0", deck=deck_path,
                           agent_kind="search-net", agent_config=inc_agent_config,
                           status=Status.SUBMITTED, is_incumbent=True, local_wr=0.6)
    save_ledger(paths.ledger, [inc])

    # agent pool FILE EXISTS but lacks the target id; deck pool is empty.
    save_pool(_agent_pool_path(paths), [_agent(0, rating=0.0)])
    save_pool(_deck_pool_path(paths), [])

    outcome = ensure_incumbent_anchor(paths)

    assert outcome == "agent:created,deck:created"
    agent_target_id = agent_genome_id(inc_agent_config)
    deck_target_id = deck_genome_id(cards)
    agents = {g.id: g for g in load_pool(_agent_pool_path(paths))}
    decks = {g.id: g for g in load_pool(_deck_pool_path(paths))}
    assert agents[agent_target_id].status == "anchor"
    assert agents[agent_target_id].kind == "search"
    assert decks[deck_target_id].status == "anchor"
    assert decks[deck_target_id].csv == deck_path
    assert decks[deck_target_id].net_weights == "src/ptcg/search/value_net_weights.json"
    assert decks[deck_target_id].cards == cards


def test_ensure_incumbent_anchor_idempotent_no_rewrite(tmp_path, monkeypatch):
    paths = FactoryPaths(root=tmp_path)
    deck_path, cards = _real_deck()
    inc = Candidate.create(name="incumbent", version="v1.0", deck=deck_path,
                           agent_kind="heuristic", agent_config={},
                           status=Status.SUBMITTED, is_incumbent=True, local_wr=0.55)
    save_ledger(paths.ledger, [inc])

    agent_target_id = agent_genome_id({})
    deck_target_id = deck_genome_id(cards)
    save_pool(_agent_pool_path(paths),
             [AgentGenome(id=agent_target_id, kind="heuristic", config={}, status="anchor")])
    save_pool(_deck_pool_path(paths),
             [DeckGenome(id=deck_target_id, cards=cards, csv=deck_path, status="anchor")])

    first = ensure_incumbent_anchor(paths)
    assert first == "ok"

    calls: list = []
    monkeypatch.setattr(evolution, "pool_merge_save",
                        lambda *a, **k: calls.append((a, k)))
    second = ensure_incumbent_anchor(paths)

    assert second == "ok"
    assert calls == []  # zero writes on the no-op path


def test_ensure_incumbent_anchor_survives_concurrent_pool_write(tmp_path, monkeypatch):
    """Interleaved-mutation test per .claude/rules/single-actor-worker-tests.md:
    the guard is a new read-modify-write on the shared deck pool. Inject a
    concurrent append (a brand-new genome, written via the real
    `pool_merge_save`) between the guard's own in-lock `load_pool` call for
    the deck pool and its own `pool_merge_save` call for that pool; assert
    the concurrent genome survives AND the anchor flip lands
    (changed-rows-only merging makes both true).
    """
    paths = FactoryPaths(root=tmp_path)
    deck_path, cards = _real_deck()
    inc = Candidate.create(name="incumbent", version="v1.0", deck=deck_path,
                           agent_kind="heuristic", agent_config={},
                           status=Status.SUBMITTED, is_incumbent=True, local_wr=0.55)
    save_ledger(paths.ledger, [inc])

    agent_target_id = agent_genome_id({})
    deck_target_id = deck_genome_id(cards)
    save_pool(_agent_pool_path(paths),
             [AgentGenome(id=agent_target_id, kind="heuristic", config={}, status="anchor")])
    save_pool(_deck_pool_path(paths),
             [DeckGenome(id=deck_target_id, cards=cards, csv=deck_path, status="retired")])

    concurrent_genome = DeckGenome(id="dk-concurrent0", cards=[9] * 60,
                                   csv="concurrent.csv", status="live")
    real_load_pool = evolution.load_pool
    injected = {"done": False}

    def spy_load_pool(path):
        result = real_load_pool(path)
        if not injected["done"] and Path(path) == _deck_pool_path(paths):
            injected["done"] = True
            pool_merge_save(_deck_pool_path(paths), [concurrent_genome])
        return result

    monkeypatch.setattr(evolution, "load_pool", spy_load_pool)

    outcome = ensure_incumbent_anchor(paths)

    assert outcome == "deck:flipped-to-anchor"
    saved = {g.id: g for g in load_pool(_deck_pool_path(paths))}
    assert concurrent_genome.id in saved              # concurrent write survived
    assert saved[concurrent_genome.id].status == "live"
    assert saved[deck_target_id].status == "anchor"    # the guard's own flip landed


def test_evo_gate_diagnostics_reach_watch_log(tmp_path):
    """evo_gate_step's diagnostics must reach watch.log (production
    truth -- the scheduled task's stdout is discarded), not just the
    in-test `log` callback. Virgin parent dir: experiments/factory/logs/
    does not exist yet, exercising append_watch_log's mkdir
    (first-run-only-bug rule).
    """
    paths = FactoryPaths(root=tmp_path)
    deck_path, cards = _real_deck()
    inc_agent_config = {"search_budget_ms": 321}
    inc = Candidate.create(name="incumbent", version="v1.0", deck=deck_path,
                           agent_kind="search-net", agent_config=inc_agent_config,
                           status=Status.SUBMITTED, is_incumbent=True, local_wr=0.6)
    save_ledger(paths.ledger, [inc])

    anchor_agent = _agent_with_config(inc_agent_config, status="anchor", rating=1.5)
    anchor_deck = DeckGenome(id=deck_genome_id(cards), cards=cards, csv=deck_path,
                             status="anchor", rating=0.5)
    helpers = [_agent(950 + i, rating=0.0) for i in range(3)]  # 3 < 8 floor -> uncovered
    save_pool(_agent_pool_path(paths), [anchor_agent, *helpers])
    save_pool(_deck_pool_path(paths), [anchor_deck])

    ledger = MatrixLedger()
    anchor_cell_id = cell_id(anchor_agent.id, anchor_deck.id)
    for h in helpers:
        ledger.record(anchor_cell_id, cell_id(h.id, anchor_deck.id), wins=1, games=2)
    save_matrix(paths.matrix, ledger)

    assert not paths.log_dir.exists()  # virgin parent dir

    result = evo_gate_step(paths, log=lambda m: None)

    assert result == []
    watch_log_path = paths.log_dir / "watch.log"
    assert watch_log_path.exists()
    watch_text = watch_log_path.read_text(encoding="utf-8")
    assert "evo-gate: incumbent anchor uncovered" in watch_text
