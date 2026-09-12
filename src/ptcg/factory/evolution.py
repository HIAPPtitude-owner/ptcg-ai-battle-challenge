"""Evolutionary agent-population runtime: cell assembly + (later) tournament tick.

A "cell" is one (AgentGenome, DeckGenome) pairing -- the atomic unit the
tournament plays games with. This module currently holds only the CELLS
section (Task 4 of the evolutionary-agent-population slice): the `Cell`
dataclass, `active_cells()` (the agent x deck cross product), and
`cell_candidate_view()` (a transient `Candidate` adapter so a cell can be
fed straight into `evaluate.build_agent`/`play_block` without inventing a
parallel construction path).

A later task (T5) APPENDS the tournament tick (the per-cycle scheduler that
walks `active_cells()`, plays games, and folds results back into the genome
pools' ratings) below this section. Keep new sections separated by a
banner comment like the one below so the two tasks' diffs stay legible.
"""
from __future__ import annotations

import datetime as dt
import math
import random
from dataclasses import dataclass, replace
from itertools import combinations
from pathlib import Path

from ptcg.factory.breeding import breed_agent, breed_deck, select_parents
from ptcg.factory.bt import fit_two_factor
from ptcg.factory.candidates import (
    Candidate,
    Status,
    ledger_lock,
    load_ledger,
    merge_save,
    next_version,
)
from ptcg.factory.deck_matrix import GENERATED_DIR
from ptcg.factory.gate import incumbent as gate_incumbent
from ptcg.factory.genomes import (
    AgentGenome,
    DeckGenome,
    agent_genome_id,
    cell_id,
    deck_genome_id,
    load_pool,
    pool_merge_save,
)
from ptcg.factory.tournament import (
    MIN_COVERAGE_GAMES,
    MIN_COVERAGE_OPPONENTS,
    MatrixLedger,
    _append_block_provenance,
    _matrix_path,
    _write_heartbeat,
    play_block,
    save,
)
from ptcg.factory.watch import append_watch_log

# ============================================================
# CELLS (Task 4) -- T5 appends the tournament tick below this banner.
# ============================================================


@dataclass
class Cell:
    """One (agent, deck) pairing -- the atomic unit the tournament plays."""

    agent: AgentGenome
    deck: DeckGenome

    @property
    def id(self) -> str:
        return cell_id(self.agent.id, self.deck.id)


def active_cells(agents: list[AgentGenome], decks: list[DeckGenome]) -> list[Cell]:
    """All non-retired agents x non-retired decks.

    "anchor" and "meta-anchor" statuses are INCLUDED -- only "retired" is
    excluded (see the status vocabulary documented at genomes.py:48). A
    heuristic-kind agent has no evolvable config but still pairs with every
    live deck like a search-kind agent does; no special-casing by kind.
    """
    live_agents = [a for a in agents if a.status != "retired"]
    live_decks = [d for d in decks if d.status != "retired"]
    return [Cell(agent=agent, deck=deck) for agent in live_agents for deck in live_decks]


def cell_candidate_view(cell: Cell) -> Candidate:
    """Adapt `cell` into a transient `Candidate` for `evaluate.build_agent`/
    `play_block` to consume. This Candidate is NEVER persisted to
    candidates.json -- name/version/status are innocuous placeholders that
    exist only to satisfy the dataclass's required fields.
    """
    agent_kind = "heuristic" if cell.agent.kind == "heuristic" else "search-net"
    agent_config = dict(cell.agent.config)
    if cell.deck.net_weights:
        agent_config["net_weights"] = cell.deck.net_weights
    return Candidate(
        id=cell.id,
        name="cell",
        version="v0.0",
        deck=cell.deck.csv,
        agent_kind=agent_kind,
        agent_config=agent_config,
        status=Status.EVALUATING,
    )


# ============================================================
# TOURNAMENT TICK (Task 5) -- steady-state play/rate/cull/breed under the
# matrix lock. Appended below the CELLS banner per T4's module docstring;
# the CELLS section above is left untouched.
# ============================================================

TARGET_AGENTS = 12
TARGET_DECKS = 12
GENOME_RETIRE_FLOOR = 40
PLAYOFF_EVERY = 5
PLAYOFF_TOP = 5


def _agent_pool_path(paths) -> Path:
    return paths.root / "experiments" / "factory" / "agent_pool.json"


def _deck_pool_path(paths) -> Path:
    return paths.root / "experiments" / "factory" / "deck_pool.json"


def _cell_score(cell: Cell) -> float:
    """Combined agent+deck log-strength for a cell. An unrated genome
    contributes -inf so a cell missing either rating sorts below every
    fully-rated cell (used only for playoff seeding)."""
    a = cell.agent.rating if cell.agent.rating is not None else float("-inf")
    d = cell.deck.rating if cell.deck.rating is not None else float("-inf")
    return a + d


def next_cell_pair(cells: list[Cell], ledger: MatrixLedger,
                   playoff: bool, diversify: bool = False) -> tuple[Cell, Cell] | None:
    """Choose the next (cell_a, cell_b) block to play.

    Coverage mode (`playoff=False`, `diversify=False` or no eligible frontier
    cell): ranked by a 3-term key --
    (1) PRIMARY: the sum of the four involved genomes' total games (each
    genome's games summed across ALL of its cells, not just the specific
    cross under consideration); (2) games recorded between the two cells
    specifically; (3)/(4) ties broken lexically by (id, id) -- the cell-level
    analogue of `tournament.next_pair`. The primary term is what balances
    coverage across BOTH populations: an under-played agent or deck must drag
    its cells to the front of pairing even when those specific cells already
    have games recorded against each other -- a 2-term
    (games_between, id, id) key alone lets a starved genome's cross sit
    unplayed forever as long as every individual cross it's part of happens
    to have a few games, because the key never looks past the specific pair.

    Per-genome totals are computed via `ledger.total_games()` summed over
    each genome's cells -- the SAME computation `evolution_tick` performs
    post-play to refresh `genome.games` (mirrors it exactly, just pre-play
    against the not-yet-updated `cells`/`ledger` snapshot) -- rather than
    reading the persisted `genome.games` fields, which are one tick stale
    (they reflect the state before this tick's about-to-be-played block and
    before this tick's pool reload). Computed ONCE as two dicts (agent id ->
    games, deck id -> games) over the full `cells` list before the pairwise
    search, so the added cost is O(cells) and the overall selection stays in
    the same complexity class as before (an O(cells) pass plus the existing
    O(pool choose 2) minimum).

    Playoff mode (`playoff=True`): restrict the pairing CANDIDATES to the top
    `PLAYOFF_TOP` cells by combined agent+deck rating (`_cell_score`), then
    apply the same key within that elite subset -- a periodic head-to-head
    among the current leaders. Per-genome totals are still summed over the
    FULL `cells` list (not just the playoff subset), since a genome's overall
    coverage is a population-wide property, not a property of the elite
    subset it's currently ranked into.

    Diversify mode (`diversify=True`, mutually exclusive with `playoff` --
    `playoff` wins if both are set, since it is checked first below):
    targets opponent-DIVERSITY starvation, a distinct failure mode from the
    game-count starvation coverage mode already balances. Coverage mode
    minimizes summed games and so spreads games maximally thin across the
    whole cell x cell space -- it never concentrates on finishing any one
    cell's opponent count, so a cell can sit at e.g. 170 games / 6 distinct
    opponents indefinitely (ample games, starved diversity). The frontier is
    every cell that has cleared `MIN_COVERAGE_GAMES` (the 15-game floor the
    evolution->gate anchor bridge itself uses, not the stricter 30-game gate
    floor `CELL_MIN_GAMES` -- diversify should benefit the anchor bridge as
    soon as possible) but not yet `MIN_COVERAGE_OPPONENTS` distinct
    opponents. The most-invested frontier cell (most total games -- closest
    to paying off; id tiebreak for determinism) is paired against its own
    coverage-starved FRESH opponent (a cell it has never played), reusing the
    same `_coverage` key coverage mode uses so the fresh opponent chosen is
    itself the most coverage-starved candidate -- double duty. If there is no
    frontier cell, or the focal cell has already played every other cell
    (fresh_opponents empty -- its opponent count cannot grow further; the
    floor may be unreachable for tiny pools), falls through to coverage mode
    byte-identical.

    Returns `None` iff fewer than 2 cells exist.
    """
    if len(cells) < 2:
        return None

    agent_games: dict[str, int] = {}
    deck_games: dict[str, int] = {}
    for c in cells:
        tg = ledger.total_games(c.id)
        agent_games[c.agent.id] = agent_games.get(c.agent.id, 0) + tg
        deck_games[c.deck.id] = deck_games.get(c.deck.id, 0) + tg

    def _coverage(cell: Cell) -> int:
        return agent_games[cell.agent.id] + deck_games[cell.deck.id]

    if diversify and not playoff:
        frontier = [c for c in cells
                   if ledger.total_games(c.id) >= MIN_COVERAGE_GAMES
                   and len(ledger.opponents_of(c.id)) < MIN_COVERAGE_OPPONENTS]
        if frontier:
            focal = max(frontier, key=lambda c: (ledger.total_games(c.id), c.id))
            fresh_opponents = [x for x in cells
                               if x.id != focal.id
                               and ledger.games_between(focal.id, x.id) == 0]
            if fresh_opponents:
                return (focal, min(fresh_opponents, key=lambda x: (_coverage(x), x.id)))

    pool = cells
    if playoff:
        pool = sorted(cells, key=lambda c: (_cell_score(c), c.id), reverse=True)[:PLAYOFF_TOP]
    return min(
        combinations(pool, 2),
        key=lambda p: (_coverage(p[0]) + _coverage(p[1]),
                       ledger.games_between(p[0].id, p[1].id), p[0].id, p[1].id),
    )


def _cull_and_breed(pool: list, target: int, breed_fn, rng, now: dt.datetime):
    """One steady-state cull/breed step for a single population (mutates
    `pool` in place; returns the newborn genome or `None`).

    Only status=="live" genomes count as the population -- anchors and
    meta-anchors are never counted toward `target`, never retired, and (via
    `select_parents`, which filters on "live") never chosen as parents.

    - At/over target: if the lowest-rated live genome has played at least
      `GENOME_RETIRE_FLOOR` games, retire it and breed ONE replacement from
      the top-quartile parents (steady-state size).
    - Under target: breed ONE offspring without retiring anyone (the pool
      fills gradually).
    """
    live = [g for g in pool if g.status == "live"]
    child = None
    if len(live) >= target:
        worst = min(
            live,
            key=lambda g: (0 if g.rating is None else 1,
                           g.rating if g.rating is not None else 0.0, g.id),
        )
        if worst.games >= GENOME_RETIRE_FLOOR:
            worst.status = "retired"
            parents = select_parents(rng, pool)
            if parents:
                child = breed_fn(rng, parents)
    elif live:
        parents = select_parents(rng, pool)
        if parents:
            child = breed_fn(rng, parents)

    if child is not None and any(g.id == child.id for g in pool):
        child = None  # content-identical rebirth would clobber the existing row's
                      # status/lineage via pool_merge_save's by-id replace

    if child is not None:
        child.born_at = now.isoformat()
        pool.append(child)
    return child


def evolution_tick(paths, *, series_fn=None, now: dt.datetime | None = None,
                   log=print, rng=None) -> str:
    """One steady-state tournament tick for the evolutionary populations.

    Mirrors `tournament.worker_tick`'s TOCTOU discipline (tournament.py:407):
    heartbeat first (a liveness check sees a fresh stamp on every tick,
    including paused/idle ones) -> honor PAUSE -> assemble cells from the
    (unlocked) pools -> play ONE block OUTSIDE any lock (real games are slow)
    -> under `ledger_lock(matrix)`: reload the matrix ledger fresh, record the
    block, append provenance, bump the persisted `evo_tick` counter, save;
    then RELOAD BOTH pools fresh from disk (the pre-play copies are minutes
    stale -- another process may have written meta-anchors or ratings while we
    played), refit two-factor ratings + per-genome game counts, run one
    cull/breed step per population, and persist via `pool_merge_save` ONLY (a
    full-row-replace-by-id merge that preserves every disk-only genome).

    `rng` defaults to `random.Random(evo_tick)` -- deterministic and
    reproducible per tick. Every `PLAYOFF_EVERY`th tick pairs within the
    current leaders instead of the fewest-games pair; the tick at residue 2
    (mutually exclusive with playoff) instead drives a frontier cell -- one
    with ample games but below the opponent-diversity floor -- toward a
    fresh opponent (`next_cell_pair`'s `diversify` mode).

    Returns "paused" (PAUSE file present), "idle" (fewer than 2 cells), or
    "played".
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    matrix_path = _matrix_path(paths)
    _write_heartbeat(paths.matrix_heartbeat, "evo-tick", now)

    if paths.pause_file.exists():
        return "paused"

    agent_pool = _agent_pool_path(paths)
    deck_pool = _deck_pool_path(paths)
    agents = load_pool(agent_pool)
    decks = load_pool(deck_pool)
    cells = active_cells(agents, decks)
    if len(cells) < 2:
        return "idle"

    selection_ledger = MatrixLedger.load(matrix_path)
    tick_count = selection_ledger.meta.get("evo_tick", 0)
    if rng is None:
        rng = random.Random(tick_count)
    playoff = (tick_count + 1) % PLAYOFF_EVERY == 0
    diversify = (tick_count + 1) % PLAYOFF_EVERY == 2

    pair = next_cell_pair(cells, selection_ledger, playoff, diversify=diversify)
    if pair is None:
        return "idle"
    cell_a, cell_b = pair
    wins_a, wins_b = play_block(
        cell_candidate_view(cell_a), cell_candidate_view(cell_b), series_fn=series_fn)

    with ledger_lock(matrix_path, log=log):
        ledger = MatrixLedger.load(matrix_path)
        # CRITICAL: play_block's (wins_a, wins_b) EXCLUDES draws - record
        # games=wins_a+wins_b (the actual decided-game count), NEVER the
        # requested block size, or draws get silently counted as losses.
        ledger.record(cell_a.id, cell_b.id, wins_a, wins_a + wins_b)
        _append_block_provenance(matrix_path, cell_a.id, cell_b.id, wins_a, wins_b, now)
        ledger.meta["evo_tick"] = tick_count + 1
        save(matrix_path, ledger)

        # Reload BOTH pools fresh under the lock: the pre-play copies are now
        # stale (play_block plays real games for minutes), and a concurrent
        # process (the watch loop writing a meta-anchor, another rating pass)
        # may have mutated the pools on disk meanwhile. Mutating the stale
        # snapshots and handing them to pool_merge_save -- which does a
        # full-row replace by id -- would silently clobber that concurrent
        # write. Same TOCTOU-mitigation pattern as worker_tick's candidates
        # reload (tournament.py:472).
        fresh_agents = load_pool(agent_pool)
        fresh_decks = load_pool(deck_pool)
        fresh_cells = active_cells(fresh_agents, fresh_decks)

        agent_str, deck_str = fit_two_factor(ledger.wins_dict())
        agent_games: dict[str, int] = {}
        deck_games: dict[str, int] = {}
        for c in fresh_cells:
            tg = ledger.total_games(c.id)
            agent_games[c.agent.id] = agent_games.get(c.agent.id, 0) + tg
            deck_games[c.deck.id] = deck_games.get(c.deck.id, 0) + tg
        for g in fresh_agents:
            g.rating = agent_str.get(g.id)
            g.games = agent_games.get(g.id, 0)
        for g in fresh_decks:
            g.rating = deck_str.get(g.id)
            g.games = deck_games.get(g.id, 0)

        _cull_and_breed(fresh_agents, TARGET_AGENTS, breed_agent, rng, now)
        _cull_and_breed(fresh_decks, TARGET_DECKS,
                        lambda r, parents: breed_deck(r, parents, GENERATED_DIR),
                        rng, now)

        pool_merge_save(agent_pool, fresh_agents, log=log)
        pool_merge_save(deck_pool, fresh_decks, log=log)

    return "played"


# ============================================================
# SELECTION + GATE SNAPSHOT (Task 6) -- turns the evolutionary populations'
# accumulated matrix stats into a Candidate the existing gate (gate.py:238)
# can evaluate, without changing gate.decide() itself. Appended below T5's
# banner per this module's docstring; T4/T5 sections above are untouched.
# ============================================================

# Plan-time-caught constant collision (this task's brief): a naive
# CELL_MIN_OPPONENTS=5 would let a cell get snapshotted before it clears
# `gate.has_matrix_coverage`'s own MIN_COVERAGE_OPPONENTS=8 floor, so the
# fresh snapshot would sit EVALUATED-but-uncovered and never reach
# `gate.decide`'s matrix comparison path. Import, don't re-literal.
CELL_MIN_GAMES = max(30, MIN_COVERAGE_GAMES)
CELL_MIN_OPPONENTS = MIN_COVERAGE_OPPONENTS


def _cell_win_rate(cell: Cell, ledger: MatrixLedger) -> float:
    """Observed win rate for `cell` across every opponent it has played.
    `ledger.wins_dict()` is keyed `(winner_id, loser_id) -> wins` with one
    entry per ordered pair (see `MatrixLedger.wins_dict`'s own docstring),
    so summing every entry whose winner leg is `cell.id` gives `cell`'s
    total wins across all opponents. Callers should only invoke this on a
    cell with `total_games(cell.id) > 0` (guaranteed by `select_best_cell`'s
    CELL_MIN_GAMES floor in practice) -- returns 0.0 defensively otherwise.
    """
    games = ledger.total_games(cell.id)
    if games == 0:
        return 0.0
    wins = ledger.wins_dict()
    won = sum(w for (winner, _loser), w in wins.items() if winner == cell.id)
    return won / games


def _cell_strength(cell: Cell) -> float:
    """`exp(agent.rating + deck.rating)` -- the gate-facing multiplicative
    scale (genomes.py: `rating` is a two-factor additive LOG strength).
    Returns `-inf` if either half is unrated, so an unrated cell can never
    outrank a rated one (mirrors `_cell_score`'s existing -inf convention,
    used for a different purpose -- playoff seeding -- above)."""
    a = cell.agent.rating
    d = cell.deck.rating
    if a is None or d is None:
        return float("-inf")
    return math.exp(a + d)


def select_best_cell(agents: list[AgentGenome], decks: list[DeckGenome],
                     ledger: MatrixLedger) -> Cell | None:
    """The strongest cell eligible for a gate-facing snapshot.

    Eligibility: `ledger.total_games(cell.id) >= CELL_MIN_GAMES` AND
    `len(ledger.opponents_of(cell.id)) >= CELL_MIN_OPPONENTS` -- these
    floors are set so a snapshot built from an eligible cell always clears
    `gate.has_matrix_coverage` (see the constant-collision note above this
    section). Ranked by `_cell_strength` (higher wins); ties within 1e-9 are
    broken by the cell's own observed win rate (`_cell_win_rate`) rather than
    the log-strength, since exact floating-point equality on a derived score
    is otherwise unreachable in real ledger data.

    Returns `None` when no cell clears both floors.
    """
    eligible = [c for c in active_cells(agents, decks)
               if ledger.total_games(c.id) >= CELL_MIN_GAMES
               and len(ledger.opponents_of(c.id)) >= CELL_MIN_OPPONENTS]
    if not eligible:
        return None

    best_strength = max(_cell_strength(c) for c in eligible)
    tied = [c for c in eligible if abs(_cell_strength(c) - best_strength) < 1e-9]
    if len(tied) == 1:
        return tied[0]
    return max(tied, key=lambda c: _cell_win_rate(c, ledger))


def snapshot_cell(cell: Cell, ledger: MatrixLedger,
                  candidates: list[Candidate]) -> Candidate | None:
    """Turn `cell`'s accumulated matrix stats into a gate-facing Candidate.

    Returns `None` (no-op) when an existing NON-RETIRED candidate already
    carries this exact cell's name (`f"evo-{agent.id}-{deck.id}"`) -- no
    duplicate spam every tick a cell stays the field leader. `next_version`
    (candidates.py:90) scans ALL candidates by name regardless of status, so
    a name whose only prior entry is RETIRED still gets a correct version
    bump rather than restarting at v0.1.
    """
    name = f"evo-{cell.agent.id}-{cell.deck.id}"
    if any(c.name == name and c.status != Status.RETIRED for c in candidates):
        return None

    a = cell.agent.rating if cell.agent.rating is not None else 0.0
    d = cell.deck.rating if cell.deck.rating is not None else 0.0
    view = cell_candidate_view(cell)
    return Candidate.create(
        name=name,
        version=next_version(candidates, name),
        deck=view.deck,
        agent_kind=view.agent_kind,
        agent_config=view.agent_config,
        provenance="evolved",
        status=Status.EVALUATED,
        matrix_rating=math.exp(a + d),
        matrix_games=ledger.total_games(cell.id),
        matrix_opponents=len(ledger.opponents_of(cell.id)),
        notes=(f"evolved cell agent={cell.agent.id} "
              f"(lineage {cell.agent.lineage}) x deck={cell.deck.id} "
              f"(lineage {cell.deck.lineage})"),
    )


def _incumbent_anchor_cell(inc: Candidate, agents: list[AgentGenome],
                           decks: list[DeckGenome]) -> Cell | None:
    """Resolve the pinned incumbent's anchor (agent, deck) pair by content
    address, matching Task 8's founder-seeding design (the incumbent's
    `agent_kind`/`agent_config` and deck are seeded into the pools verbatim
    as `status="anchor"` genomes -- see this plan's Task 8 founder-roster
    note). Content-addressing (`agent_genome_id`/`deck_genome_id`, the same
    convention every other genome id already uses) means this match holds
    regardless of Task 8's exact field choices, as long as the anchor
    genomes carry the incumbent's real config/cards.

    Returns `None` if either half is missing or retired (evolution not yet
    seeded, or Task 8 hasn't run yet) -- the caller treats that identically
    to "no coverage yet".
    """
    agent_target_id = agent_genome_id(inc.agent_config)
    agent = next((a for a in agents
                 if a.id == agent_target_id and a.status != "retired"), None)
    if agent is None:
        return None

    from ptcg.arena.runner import load_deck
    from ptcg.factory.evaluate import ROOT
    try:
        cards = load_deck(ROOT / inc.deck)
    except (OSError, ValueError):
        return None
    deck_target_id = deck_genome_id(cards)
    deck = next((d for d in decks
                if d.id == deck_target_id and d.status != "retired"), None)
    if deck is None:
        return None
    return Cell(agent=agent, deck=deck)


def _anchor_has_coverage(cell: Cell, ledger: MatrixLedger) -> bool:
    """Mirrors `gate.has_matrix_coverage`'s own floors (MIN_COVERAGE_GAMES/
    MIN_COVERAGE_OPPONENTS, imported from tournament.py, not re-literaled)
    -- the scale-bridge only trusts a refresh once the anchor cell clears
    the SAME coverage bar the gate itself requires before it will treat a
    candidate's `matrix_rating` as valid."""
    return (ledger.total_games(cell.id) >= MIN_COVERAGE_GAMES
           and len(ledger.opponents_of(cell.id)) >= MIN_COVERAGE_OPPONENTS)


def _wlog(paths, log, msg: str) -> None:
    """Diagnostics that must survive the scheduled task's discarded stdout: echo
    to `log` (tests capture this) AND append to the watch log (production truth,
    `paths.log_dir / "watch.log"` -- matches `watch_paths()["watch_log"]` in
    `scripts/factory_watch_once.py`)."""
    log(msg)
    append_watch_log(Path(paths.log_dir) / "watch.log", msg)


def ensure_incumbent_anchor(paths, *, log=print) -> str:
    """Idempotently guarantee the pinned incumbent's (agent, deck) genomes exist
    in the pools with status="anchor", so _incumbent_anchor_cell can resolve and
    the gate's scale bridge can go live. Returns a short outcome string:
    "unseeded" | "no-incumbent" | "deck-unreadable" | "ok" (nothing to do) |
    a comma-joined change summary e.g. "deck:flipped-to-anchor" /
    "agent:created,deck:created".

    Called at the top of every `evo_gate_step` firing (self-healing on every
    future incumbent re-pin, unlike a one-time migration script which would
    rot the moment the pin moves) and safe to call standalone. Runs under
    the MATRIX lock (`ledger_lock(_matrix_path(paths))`), matching
    `evolution_tick`'s lock order (matrix -> pool, pool lock taken inside
    `pool_merge_save`) so a guard write can never land in the window between
    the tick's in-lock reload and its own `pool_merge_save` and get
    clobbered by the tick's full-row replace.
    """
    agent_pool_path = _agent_pool_path(paths)
    if not agent_pool_path.exists():
        return "unseeded"

    inc = gate_incumbent(load_ledger(paths.ledger))
    if inc is None:
        return "no-incumbent"

    agent_target_id = agent_genome_id(inc.agent_config)

    from ptcg.arena.runner import load_deck
    from ptcg.factory.evaluate import ROOT
    try:
        cards = load_deck(ROOT / inc.deck)
    except (OSError, ValueError):
        return "deck-unreadable"
    deck_target_id = deck_genome_id(cards)

    deck_pool_path = _deck_pool_path(paths)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    changes: list[str] = []

    with ledger_lock(_matrix_path(paths), log=log):
        agents = load_pool(agent_pool_path)
        agent_row = next((a for a in agents if a.id == agent_target_id), None)
        changed_agent: AgentGenome | None = None
        if agent_row is None:
            changed_agent = AgentGenome(
                id=agent_target_id,
                kind="heuristic" if inc.agent_kind == "heuristic" else "search",
                config=inc.agent_config, status="anchor", born_at=now,
                notes="incumbent anchor (auto-seeded)")
            changes.append("agent:created")
        elif agent_row.status != "anchor":
            changed_agent = replace(agent_row, status="anchor")
            changes.append("agent:flipped-to-anchor")

        decks = load_pool(deck_pool_path)
        deck_row = next((d for d in decks if d.id == deck_target_id), None)
        changed_deck: DeckGenome | None = None
        if deck_row is None:
            changed_deck = DeckGenome(
                id=deck_target_id, cards=cards, csv=inc.deck,
                net_weights=inc.agent_config.get("net_weights"),
                status="anchor", born_at=now,
                notes="incumbent anchor (auto-seeded)")
            changes.append("deck:created")
        elif deck_row.status != "anchor":
            changed_deck = replace(deck_row, status="anchor")
            changes.append("deck:flipped-to-anchor")

        # Pass ONLY the changed rows to pool_merge_save (never the full
        # in-lock-loaded pool list): its by-id merge fresh-loads from disk
        # and replaces/appends only these ids, so a concurrent writer's
        # untouched rows survive (interleaved-mutation safety). Zero writes
        # when nothing changed -- 96 firings/day must not rewrite the pools
        # 96 times.
        if changed_agent is not None:
            pool_merge_save(agent_pool_path, [changed_agent], log=log)
        if changed_deck is not None:
            pool_merge_save(deck_pool_path, [changed_deck], log=log)

    return ",".join(changes) if changes else "ok"


def evo_gate_step(paths, *, log=print) -> list[str]:
    """One watch-loop firing's evolution -> gate feed (Task 6). The only
    writes are the `ensure_incumbent_anchor` guard's conditional anchor-row
    repair (see that function's docstring) and a single `merge_save` onto
    `paths.ledger` covering whatever this step actually changed.

    No-op (returns `[]`, touches nothing) when the evolutionary pools
    haven't been seeded yet (`agent_pool.json` missing) -- safe to call
    unconditionally from every watch firing regardless of whether Task 8's
    founder-seeding has run yet.

    The incumbent to refresh is resolved via `gate.incumbent()` -- the exact
    same selector `gate.decide()` will use as its beat-this bar -- rather
    than an independent re-derivation of the `is_incumbent` flag. The two
    selectors diverged: `gate.incumbent()` additionally requires the
    designated candidate to carry a `local_wr` (and, among several flagged
    candidates, picks the highest-`local_wr` one), so re-checking only the
    flag here could refresh a different candidate's matrix fields than the
    one `gate.decide()` actually compares against -- a silent cross-scale
    bug (the gate would then measure `exp(a+d)` against a candidate whose
    matrix fields were never refreshed). Mirroring `gate.incumbent()`
    exactly keeps "whichever candidate the gate will use" and "whichever
    candidate gets scale-refreshed" the same candidate, always.

    When `gate.incumbent()` returns None (no designated/eligible incumbent
    at all, e.g. nothing flagged or only a retired flag), this step no-ops
    ENTIRELY this cycle -- logged distinctly from the anchor-uncovered case
    below so watch.log can tell "nothing to refresh" apart from "refresh
    target identified but its anchor cell lacks coverage".

    When a pinned incumbent exists, its matrix fields are refreshed from its
    anchor cell's current two-factor stats FIRST, so `gate.decide()`'s
    head-to-head comparison stays on the `exp(a+d)` scale both candidates
    are on -- comparing a freshly-evolved snapshot's `matrix_rating` against
    a stale incumbent value from a different rating regime would be an
    apples-to-oranges bug `gate.decide` has no way to detect on its own. If
    the anchor cell isn't covered yet, this step no-ops ENTIRELY this cycle
    (no incumbent refresh, no new snapshot) rather than risk that
    cross-scale comparison -- logged so a stalled anchor is visible in
    watch.log.
    """
    agent_pool_path = _agent_pool_path(paths)
    if not agent_pool_path.exists():
        return []

    anchor_outcome = ensure_incumbent_anchor(paths, log=log)
    if anchor_outcome not in ("ok", "unseeded"):
        _wlog(paths, log, f"evo-gate: anchor-guard {anchor_outcome}")

    agents = load_pool(agent_pool_path)
    decks = load_pool(_deck_pool_path(paths))
    ledger = MatrixLedger.load(_matrix_path(paths))
    candidates = load_ledger(paths.ledger)

    modified: list[Candidate] = []
    inc = gate_incumbent(candidates)
    if inc is None:
        _wlog(paths, log, "evo-gate: no incumbent")
        return []

    anchor_cell = _incumbent_anchor_cell(inc, agents, decks)
    if anchor_cell is None or not _anchor_has_coverage(anchor_cell, ledger):
        _wlog(paths, log, "evo-gate: incumbent anchor uncovered")
        return []
    a = anchor_cell.agent.rating if anchor_cell.agent.rating is not None else 0.0
    d = anchor_cell.deck.rating if anchor_cell.deck.rating is not None else 0.0
    inc.matrix_rating = math.exp(a + d)
    inc.matrix_games = ledger.total_games(anchor_cell.id)
    inc.matrix_opponents = len(ledger.opponents_of(anchor_cell.id))
    modified.append(inc)

    cell = select_best_cell(agents, decks, ledger)
    snap = None
    if cell is not None:
        snap = snapshot_cell(cell, ledger, candidates)
        if snap is not None:
            modified.append(snap)

    if modified:
        _wlog(paths, log, f"evo-gate: refreshed {inc.id}"
              + (f", snapshotted {snap.id}" if snap else ""))
        merge_save(paths.ledger, modified, log=log)
    return [c.id for c in modified]
