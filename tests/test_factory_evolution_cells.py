"""Tests for evolution.py's CELLS section (Task 4): Cell assembly + the
transient Candidate adapter that lets a cell flow through
evaluate.build_agent unchanged.
"""
from __future__ import annotations

from ptcg.agents.heuristic import HeuristicAgent
from ptcg.agents.search_agent import SearchAgent
from ptcg.factory.evaluate import build_agent
from ptcg.factory.evolution import Cell, active_cells, cell_candidate_view
from ptcg.factory.genomes import AgentGenome, DeckGenome, split_cell_id
from ptcg.search.evaluate import evaluate as hand_tuned_evaluate

DECK = [3] * 60  # 34x Basic Water Energy convention used elsewhere in this repo


def _agent(config: dict | None = None, kind: str = "search", **kw) -> AgentGenome:
    config = config if config is not None else {"search_budget_ms": 200}
    return AgentGenome(id=f"ag-{kw.pop('id_suffix', 'a')}", kind=kind, config=config, **kw)


def _deck(net_weights: str | None = None, csv: str = "decks/x.csv", **kw) -> DeckGenome:
    return DeckGenome(id=f"dk-{kw.pop('id_suffix', 'a')}", cards=list(DECK),
                      csv=csv, net_weights=net_weights, **kw)


def test_active_cells_excludes_retired_only_and_covers_heuristic_agents():
    """2 live agents (one search, one heuristic) x 2 live decks x 1 retired
    agent -> 4 cells; the retired agent is excluded entirely, and the
    heuristic-kind agent still pairs with every live deck (no kind
    special-casing in active_cells)."""
    agent_search = _agent(id_suffix="search-live")
    agent_heuristic = _agent(kind="heuristic", config={}, id_suffix="heuristic-live")
    agent_retired = _agent(status="retired", id_suffix="retired")
    deck1 = _deck(id_suffix="1")
    deck2 = _deck(id_suffix="2")

    cells = active_cells([agent_search, agent_heuristic, agent_retired], [deck1, deck2])

    assert len(cells) == 4
    agent_ids_seen = {c.agent.id for c in cells}
    assert agent_ids_seen == {agent_search.id, agent_heuristic.id}
    assert agent_retired.id not in agent_ids_seen
    deck_ids_seen = {c.deck.id for c in cells}
    assert deck_ids_seen == {deck1.id, deck2.id}


def test_active_cells_includes_anchor_and_meta_anchor_statuses():
    """anchor/meta-anchor are non-retired statuses and must be included,
    per the status vocabulary documented at genomes.py:48."""
    agent_anchor = _agent(status="anchor", id_suffix="anchor")
    deck_meta_anchor = _deck(status="meta-anchor", id_suffix="meta")

    cells = active_cells([agent_anchor], [deck_meta_anchor])

    assert len(cells) == 1
    assert cells[0].agent.id == agent_anchor.id
    assert cells[0].deck.id == deck_meta_anchor.id


def test_cell_candidate_view_merges_net_weights_and_id_round_trips():
    agent = _agent(config={"c_uct": 2.0, "rollout_depth": 8}, id_suffix="net")
    deck = _deck(net_weights="src/ptcg/search/value_net_weights_x.json",
                 csv="src/ptcg/decks/candidates/x.csv", id_suffix="net")
    cell = Cell(agent=agent, deck=deck)

    view = cell_candidate_view(cell)

    assert view.deck == deck.csv
    assert view.agent_kind == "search-net"
    assert view.agent_config == {
        "c_uct": 2.0, "rollout_depth": 8,
        "net_weights": "src/ptcg/search/value_net_weights_x.json",
    }
    # id round-trips through split_cell_id back to the source genome ids.
    assert view.id == cell.id
    assert split_cell_id(view.id) == (agent.id, deck.id)
    # the source genome's config dict is never mutated by the merge.
    assert agent.config == {"c_uct": 2.0, "rollout_depth": 8}


def test_cell_candidate_view_omits_net_weights_key_when_deck_has_none():
    agent = _agent(id_suffix="nonet")
    deck = _deck(net_weights=None, id_suffix="nonet")
    cell = Cell(agent=agent, deck=deck)

    view = cell_candidate_view(cell)

    assert "net_weights" not in view.agent_config
    assert view.agent_config == agent.config


def test_build_agent_wires_full_gene_surface_and_defaults_to_hand_tuned_evaluator():
    """(c) from the task brief: c_uct/max_depth/robust_min_visits/
    deviate_min_visit_frac must reach SearchConfig, and with NO net_weights
    present in agent_config, build_agent must construct SearchAgent WITHOUT
    an evaluator= kwarg -- SearchAgent.__init__ (search_agent.py:65-67) then
    falls back to `evaluate` (ptcg.search.evaluate.evaluate, the hand-tuned
    evaluator, imported as the module default at search_agent.py:15) as
    self.searcher.evaluator. This is the exact attribute we assert on below
    (build_agent itself overwrites agent.name to candidate.id at
    evaluate.py:98, so `.name` is not a usable signal here).
    """
    agent = _agent(config={
        "c_uct": 2.0, "max_depth": 30,
        "robust_min_visits": 9, "deviate_min_visit_frac": 0.2,
    }, id_suffix="genes")
    deck = _deck(net_weights=None, id_suffix="genes")
    view = cell_candidate_view(Cell(agent=agent, deck=deck))

    built = build_agent(view, DECK)

    assert isinstance(built, SearchAgent)
    cfg = built.searcher.config
    assert cfg.c_uct == 2.0
    assert cfg.max_depth == 30
    assert cfg.robust_min_visits == 9
    assert cfg.deviate_min_visit_frac == 0.2
    assert built.searcher.evaluator is hand_tuned_evaluate


def test_build_agent_on_heuristic_kind_cell_view_builds_heuristic_agent():
    agent = _agent(kind="heuristic", config={}, id_suffix="heur")
    deck = _deck(id_suffix="heur")
    view = cell_candidate_view(Cell(agent=agent, deck=deck))

    built = build_agent(view, DECK)

    assert isinstance(built, HeuristicAgent)
