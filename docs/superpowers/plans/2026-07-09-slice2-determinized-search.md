# Slice 2: Determinized MCTS Search Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `SearchAgent` that picks moves via determinized MCTS (ISMCTS, shared tree, root determinization sampling) on the engine's search API, beats heuristic v0 (≥55% observed, Wilson 95% LCB > 50%, ≥200 games at 150–250 ms/move), and ships to the Kaggle ladder.

**Architecture:** Per decision: update a `BeliefState` from `Observation.logs`, then run anytime ISMCTS — each iteration samples a determinization of all hidden zones, `search_begin`s from the *real* agent observation, replays the tree path via `search_step` (children keyed by canonical action signature, never option index), plays opponent decisions with heuristic v0 as a fixed policy, evaluates leaves with a hand-tuned evaluator, backprops, then `search_end`s. Heuristic v0 is the fallback on any failure. Spec: `docs/superpowers/specs/2026-07-09-slice2-determinized-search-design.md`.

**Tech Stack:** Python 3.12, uv, pytest, vendored `cg` SDK (ctypes over prebuilt engine at `src/cg`), no new dependencies.

## Global Constraints

- The repo path contains a space (`Dev Folder`) — ALWAYS quote paths in shell commands.
- Run tests with `uv run pytest` (fast suite; `-m 'not slow'` is the default via pyproject `addopts`). NEVER pass `--run`.
- The cg SDK holds ONE battle per process (module-global state) — matches run sequentially in-process. Search calls (`search_begin`/`search_step`) are only legal on the *real* agent observation (its `search_begin_input` is non-None); `SearchState.observation.search_begin_input` is None, so every MCTS iteration must restart from the root via `search_begin`.
- `pokemon-tcg-ai-battle/` is licensed, read-only, gitignored. `src/cg` is vendored licensed material, gitignored — regenerate with `uv run python scripts/vendor_sdk.py`, never commit it.
- Organizers may append enum members / dataclass attributes mid-competition — code must tolerate unknown values (no exhaustive enum matches without a default branch).
- Any claim about card data (energy cost, damage, HP, basic/stage) MUST be verified by executable check against `all_card_data()`/`all_attack()` per `.claude/rules/verify-game-data-claims.md`, never by prose/CSV reading.
- Ruff: line length 100, double quotes. Type hints on all signatures. Tests live in `tests/` (top-level, established Slice-1 layout).
- Stage git files by explicit path (never `git add .`). Conventional commits.
- Before writing code, grep the touched file(s) for each plan-cited landmark (function names, field names, line refs). If a landmark doesn't match, STOP and report `plan-drift` before implementing.

## Existing interfaces you will consume (verified 2026-07-09)

- `ptcg.agents.base.Agent` — ABC: `name: str`, `act(self, obs: Observation) -> list[int]`.
- `ptcg.agents.heuristic` — `choose(obs, cards: dict[int, CardData], attacks: dict[int, Attack]) -> list[int]` (never raises; internal fallback), `HeuristicAgent` (name `heuristic-v0`).
- `ptcg.arena.runner` — `load_deck(path) -> list[int]`, `play_match(agent0, agent1, deck0, deck1, max_moves=3000) -> MatchResult(winner, turns, moves, seconds, max_move_seconds, error)`, `run_series(agent_a, agent_b, deck_a, deck_b, n_games) -> SeriesStats` (alternating seats; raises on match error).
- `ptcg.arena.stats` — `wilson_ci(successes, n, z=1.96) -> (lo, hi)`, `SeriesStats` (`wins_a`, `win_rate_a`, `ci_a`, `markdown_row(...)`).
- `cg.api` (src/cg/api.py) — `Observation(select, logs, current, search_begin_input)`; `State(turn, yourIndex, result, players[2], stadium, ...)` where `result` is -1 unfinished / winner index / 2 draw; `PlayerState(active, bench, deckCount, discard, prize: list[Card|None], handCount, hand: list[Card]|None, ...)`; `SelectData(type, context, minCount, maxCount, option: list[Option], deck, ...)`; `Option(type, number, area, index, playerIndex, toolIndex, energyIndex, count, inPlayArea, inPlayIndex, attackId, cardId, serial, specialConditionType)`; `Log(type, playerIndex, cardId, serial, fromArea, toArea, ...)`; `CardData(cardId, name, cardType, basic: bool, ...)`; `search_begin(agent_observation, your_deck, your_prize, opponent_deck, opponent_prize, opponent_hand, opponent_active, manual_coin=False) -> SearchState(observation, searchId)` (list args must be **at least** the zone's count; `opponent_active` only consumed when the opponent's active is face-down; raises ValueError on count/ID problems); `search_step(search_id, select: list[int]) -> SearchState`; `search_end()` (frees ALL search memory for reuse); `search_release(search_id)` (frees one).
- Test fixtures at `tests/fixtures/obs.py`: `make_pokemon`, `make_player`, `make_state`, `make_select`, `make_obs`.
- Decks: `src/ptcg/decks/candidates/mega-lucario-fighting.csv` (current entry deck), `tests/fixtures/sample_deck.csv`.

## File Structure

```
src/ptcg/search/__init__.py      (empty package marker)
src/ptcg/search/timing.py        TimeManager — per-move budget arithmetic (pure)
src/ptcg/search/belief.py        BeliefState + Determinization — hidden-zone sampling (pure)
src/ptcg/search/evaluate.py      evaluate(state, my_index) -> [0,1] leaf evaluator (pure)
src/ptcg/search/tree.py          action signatures + Node + UCB1 (pure)
src/ptcg/search/searcher.py      SearchBackend protocol, EngineBackend, SearchConfig, Searcher
src/ptcg/agents/search_agent.py  SearchAgent(Agent) — glue + fallback
scripts/spike_search_throughput.py  Task-1 measurement spike (throwaway, engine-dependent)
tests/test_timing.py  tests/test_belief.py  tests/test_evaluate.py  tests/test_tree.py
tests/test_searcher.py (fake backend)  tests/test_search_agent.py
tests/test_search_integration.py (engine)  tests/test_search_acceptance.py (slow)
```

Pure modules (timing/belief/evaluate/tree) import only `cg.api` dataclasses/enums — no engine calls — so they unit-test without the DLL. All engine calls live behind `SearchBackend`.

---

### Task 1: Spike — search API throughput & mechanics (measurement, no TDD)

**Files:**
- Create: `scripts/spike_search_throughput.py`
- Modify: `experiments/EXPERIMENTS.md` (append a `## Slice 2 spike` section below the table)

**Interfaces:**
- Consumes: `cg.game.battle_start/battle_select/battle_finish`, `cg.api.search_begin/search_step/search_end`, `ptcg.agents.heuristic.HeuristicAgent`, `ptcg.arena.runner.play_match`.
- Produces: measured numbers later tasks cite — `STEP_MS` (mean/p50/p90 per `search_step`), `BEGIN_MS`, decisions-per-game stats, recommended `max_iterations` for 200 ms and 1 s budgets, and `est_total_moves` for `TimeManager`.

This is a throwaway research script — no unit tests; it must not be imported by product code. Its numbers get written into EXPERIMENTS.md and cited in Task 2/6 constants.

- [ ] **Step 1: Write the spike script**

```python
"""Spike: measure search API cost + game-length stats to size Slice-2 MCTS constants.

Run: uv run python scripts/spike_search_throughput.py
Throwaway measurement script (Slice 2 Task 1) — not imported by product code.
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cg import api  # noqa: E402
from cg.api import to_observation_class  # noqa: E402
from cg.game import battle_finish, battle_select, battle_start  # noqa: E402
from ptcg.agents.heuristic import HeuristicAgent  # noqa: E402
from ptcg.arena.runner import load_deck, play_match  # noqa: E402

DECK = load_deck(ROOT / "src/ptcg/decks/candidates/mega-lucario-fighting.csv")


def game_length_stats(n_games: int = 20) -> list[int]:
    a, b = HeuristicAgent(), HeuristicAgent()
    moves = []
    for _ in range(n_games):
        r = play_match(a, b, DECK, DECK)
        if r.error is None:
            moves.append(r.moves)
    return moves


def measure_search(turn_target: int = 6, n_begins: int = 30, steps_per_walk: int = 100):
    """Drive a live battle to mid-game, then time search_begin + search_step walks."""
    agent = HeuristicAgent()
    obs_dict, _ = battle_start(DECK, DECK)
    try:
        while True:
            obs = to_observation_class(obs_dict)
            st = obs.current
            if st.result != -1:
                raise RuntimeError("game ended before reaching target turn")
            if st.turn >= turn_target and obs.select.minCount == 1 and obs.select.maxCount == 1:
                break
            obs_dict = battle_select(agent.act(obs))

        me = st.yourIndex
        p_me, p_opp = st.players[me], st.players[1 - me]
        # Both decks known in the spike -> build oversized "everything left" predictions.
        # search_begin accepts lists LONGER than the zone count; it needs >=, not ==.
        seen_ids = [c.id for c in p_me.hand or []] + [c.id for c in p_me.discard]
        pool_me = list(DECK)
        for cid in seen_ids:
            if cid in pool_me:
                pool_me.remove(cid)
        pool_opp = list(DECK)
        for c in p_opp.discard:
            if c.id in pool_opp:
                pool_opp.remove(c.id)
        opp_active = [DECK[0]] if (p_opp.active and p_opp.active[0] is None) else []

        begin_ms, step_ms, indices_seen = [], [], set()
        for _ in range(n_begins):
            t0 = time.perf_counter()
            ss = api.search_begin(obs, pool_me, pool_me, pool_opp, pool_opp, pool_opp, opp_active)
            begin_ms.append((time.perf_counter() - t0) * 1000)
            cur = ss
            for _ in range(steps_per_walk):
                o = cur.observation
                if o.current.result != -1:
                    break
                indices_seen.add(o.current.yourIndex)
                sel = agent.act(o)
                t0 = time.perf_counter()
                cur = api.search_step(cur.searchId, sel)
                step_ms.append((time.perf_counter() - t0) * 1000)
            api.search_end()
        return begin_ms, step_ms, indices_seen
    finally:
        battle_finish()


def main() -> None:
    moves = game_length_stats()
    print(f"decisions/game over {len(moves)} games: "
          f"mean={statistics.mean(moves):.0f} p50={statistics.median(moves):.0f} "
          f"max={max(moves)}")
    begin_ms, step_ms, indices_seen = measure_search()
    for name, xs in (("search_begin ms", begin_ms), ("search_step ms", step_ms)):
        q = statistics.quantiles(xs, n=10)
        print(f"{name}: mean={statistics.mean(xs):.3f} p50={statistics.median(xs):.3f} "
              f"p90={q[8]:.3f} n={len(xs)}")
    print(f"both player indices stepped in search: {indices_seen == {0, 1}}")
    step = statistics.median(step_ms)
    begin = statistics.median(begin_ms)
    for budget_ms in (200, 1000):
        # one iteration ~ begin + max_depth(40) steps
        iters = int(budget_ms / (begin + 40 * step)) if (begin + 40 * step) > 0 else 0
        print(f"budget {budget_ms} ms -> ~{iters} iterations at depth 40")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `cd "C:\Users\Brad\Dev Folder\Product\ptcg-ai-battle-challenge-strategy" && uv run python scripts/spike_search_throughput.py`
Expected: prints decisions/game stats, begin/step timings, `both player indices stepped in search: True`, and iteration estimates. If `both player indices ... : False`, STOP and report — the whole opponent-policy design assumes search presents opponent decisions.

- [ ] **Step 3: Record findings**

Append to `experiments/EXPERIMENTS.md` a `## Slice 2 spike (Task 1)` section with the printed numbers plus one sentence each on: iteration count feasible at 200 ms and at 1 s; the `est_total_moves` value Task 2 should use (median decisions/game ÷ 2 — the runner counts BOTH players' moves and TimeManager only budgets ours); any surprise (e.g., search_begin rejecting oversized lists, step cost exploding with depth).

- [ ] **Step 4: Verify fast suite untouched, commit**

Run: `uv run pytest`
Expected: all existing tests pass (34 fast as of Slice 1).

```bash
git add scripts/spike_search_throughput.py experiments/EXPERIMENTS.md
git commit -m "feat: slice-2 spike — search API throughput + game-length measurements"
```

---

### Task 2: TimeManager (`timing.py`)

**Files:**
- Create: `src/ptcg/search/__init__.py` (empty), `src/ptcg/search/timing.py`
- Test: `tests/test_timing.py`

**Interfaces:**
- Consumes: nothing (pure arithmetic).
- Produces: `TimeManager(total_s: float = 480.0, min_move_s: float = 0.05, max_move_s: float = 1.5, est_total_moves: int = <SPIKE VALUE, default 120>, moves_floor: int = 10)` with `move_budget() -> float` and `note_move(seconds: float) -> None`. Task 7 constructs it; Task 9/11 override its fields.

Set the `est_total_moves` default from the Task-1 spike number recorded in EXPERIMENTS.md (our-decisions-per-game median); use 120 only if the spike section is missing.

- [ ] **Step 1: Write failing tests**

```python
"""TimeManager budget arithmetic."""
from ptcg.search.timing import TimeManager


def test_budget_clamped_to_max_when_plenty_remains():
    tm = TimeManager(total_s=480.0, max_move_s=1.5, est_total_moves=100)
    assert tm.move_budget() == 1.5


def test_budget_shrinks_as_time_is_spent():
    tm = TimeManager(total_s=10.0, min_move_s=0.05, max_move_s=1.5, est_total_moves=100)
    tm.spent_s, tm.moves_done = 9.0, 50
    # remaining 1.0s over max(100-50, 10)=50 moves -> 0.02 -> clamped to min 0.05
    assert tm.move_budget() == 0.05


def test_budget_uses_moves_floor_near_game_end():
    tm = TimeManager(total_s=480.0, est_total_moves=100, moves_floor=10, max_move_s=5.0)
    tm.spent_s, tm.moves_done = 100.0, 200  # more moves than estimated
    assert tm.move_budget() == (480.0 - 100.0) / 10  # floor prevents div-by-tiny


def test_note_move_accumulates():
    tm = TimeManager()
    tm.note_move(0.25)
    tm.note_move(0.75)
    assert tm.spent_s == 1.0 and tm.moves_done == 2


def test_budget_never_negative():
    tm = TimeManager(total_s=1.0, min_move_s=0.05)
    tm.spent_s = 5.0
    assert tm.move_budget() == 0.05
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_timing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ptcg.search'`

- [ ] **Step 3: Implement**

```python
"""Per-move time budgeting under the 10-minute match cap (timeout = loss)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TimeManager:
    """Allocates per-decision search budget from the remaining match clock.

    total_s defaults to 480 (8 of the 10 minutes; the margin covers engine
    time, I/O, and a 2-3x slower Kaggle machine).
    """

    total_s: float = 480.0
    min_move_s: float = 0.05
    max_move_s: float = 1.5
    est_total_moves: int = 120  # from Task-1 spike: our decisions per game
    moves_floor: int = 10
    spent_s: float = 0.0
    moves_done: int = 0

    def move_budget(self) -> float:
        remaining = max(self.total_s - self.spent_s, 0.0)
        est_remaining = max(self.est_total_moves - self.moves_done, self.moves_floor)
        return min(self.max_move_s, max(self.min_move_s, remaining / est_remaining))

    def note_move(self, seconds: float) -> None:
        self.spent_s += seconds
        self.moves_done += 1
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_timing.py -v` — Expected: 5 passed. Then `uv run pytest` — Expected: full fast suite green.

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/search/__init__.py src/ptcg/search/timing.py tests/test_timing.py
git commit -m "feat: TimeManager — anytime per-move budgets under the match cap"
```

---

### Task 3: BeliefState + Determinization (`belief.py`)

**Files:**
- Create: `src/ptcg/search/belief.py`
- Test: `tests/test_belief.py`

**Interfaces:**
- Consumes: `cg.api` dataclasses only (`Observation`, `State`, `PlayerState`, `Card`, `Log`, `AreaType`) — NO engine calls; `tests/fixtures/obs.py` builders in tests.
- Produces:
  - `Determinization(your_deck: list[int], your_prize: list[int], opponent_deck: list[int], opponent_prize: list[int], opponent_hand: list[int], opponent_active: list[int])`
  - `BeliefState(my_deck: list[int], basic_ids: frozenset[int] = frozenset())` with `update(obs: Observation) -> None` and `sample(obs: Observation, rng: random.Random) -> Determinization`.
  Task 6's `Searcher` calls `sample`; Task 7's `SearchAgent` calls `update` + constructs with `basic_ids`.

Semantics (from the approved spec §4):
- **Our side is exact:** unseen-multiset = our 60-card list minus every one of OUR cards visible in the observation (hand, discard, board incl. attachments/pre-evolutions, revealed prizes, our stadium). Shuffle it; deal face-down prize slots, then the deck.
- **Opponent side is mirror-prior:** pool = our own deck list minus THEIR visible cards minus their log-tracked known-hand cards; known-hand ids go into the hand prediction first; face-down prizes and deck drawn from the shuffled pool; pool exhaustion pads with the deck's most common card id.
- **Log tracking (v1):** only opponent hand knowledge — a log with `playerIndex == opponent`, `toArea == AreaType.HAND`, non-None `cardId`+`serial` adds to known-hand; `fromArea == AreaType.HAND` removes that serial. `update` resets all tracking when `obs.current.turn` goes backwards (new match — agents are reused across arena games).
- **Prediction lists sized exactly** to the zone counts (`deckCount`, `len(prize)`, `handCount`); `opponent_active` = one basic Pokémon id only when their active is face-down (`active == [None]`), else `[]`.
- At setup (`turn == 0`), guarantee `opponent_deck` contains ≥1 id from `basic_ids` (engine requirement) by swapping index 0 with a basic from `my_deck` when needed.

- [ ] **Step 1: Write failing tests**

```python
"""BeliefState: hidden-zone determinization sampling."""
import random

from cg.api import AreaType, Card, Log, LogType

from ptcg.search.belief import BeliefState, Determinization
from tests.fixtures.obs import make_obs, make_player, make_pokemon, make_select

DECK = [3] * 30 + [100] * 4 + [200] * 4 + [300] * 22  # synthetic 60-card list
BASICS = frozenset({100})


def card(cid: int, serial: int = 0, player: int = 1) -> Card:
    return Card(id=cid, serial=serial, playerIndex=player)


def obs_with(me=None, opp=None, logs=None):
    from tests.fixtures.obs import make_state
    o = make_obs(make_select([]), make_state(you=me, opp=opp))
    o.logs = logs or []
    return o


def test_sample_sizes_match_zone_counts():
    me = make_player(make_pokemon(), hand=[card(3, player=0)])
    opp = make_player(make_pokemon())
    opp.handCount, opp.deckCount = 5, 38
    o = obs_with(me, opp)
    d = BeliefState(DECK, BASICS).sample(o, random.Random(0))
    assert isinstance(d, Determinization)
    assert len(d.your_deck) == me.deckCount
    assert len(d.your_prize) == len(me.prize)
    assert len(d.opponent_deck) == 38
    assert len(d.opponent_prize) == len(opp.prize)
    assert len(d.opponent_hand) == 5
    assert d.opponent_active == []  # active is face-up in fixtures


def test_our_unseen_pool_excludes_visible_cards():
    me = make_player(make_pokemon(card_id=100), hand=[card(200, player=0)])
    me.discard = [card(200, player=0)]
    me.deckCount = 50
    o = obs_with(me, make_player(make_pokemon()))
    d = BeliefState(DECK, BASICS).sample(o, random.Random(0))
    pool = d.your_deck + [p for p in d.your_prize]
    # 100 on board once, 200 in hand+discard: only 3 and 2 copies left respectively
    assert pool.count(100) <= 3 and pool.count(200) <= 2


def test_known_opponent_hand_cards_appear_in_hand_prediction():
    me, opp = make_player(make_pokemon()), make_player(make_pokemon())
    opp.handCount = 4
    logs = [Log(type=LogType.SHUFFLE, playerIndex=1, cardId=200, serial=77,
                toArea=AreaType.HAND)]
    o = obs_with(me, opp, logs)
    b = BeliefState(DECK, BASICS)
    b.update(o)
    d = b.sample(o, random.Random(0))
    assert 200 in d.opponent_hand


def test_card_leaving_hand_is_forgotten():
    me, opp = make_player(make_pokemon()), make_player(make_pokemon())
    opp.handCount = 1
    b = BeliefState([3] * 60, frozenset())
    b.update(obs_with(me, opp, [Log(type=LogType.SHUFFLE, playerIndex=1, cardId=200,
                                    serial=77, toArea=AreaType.HAND)]))
    b.update(obs_with(me, opp, [Log(type=LogType.SHUFFLE, playerIndex=1, cardId=200,
                                    serial=77, fromArea=AreaType.HAND)]))
    d = b.sample(obs_with(me, opp), random.Random(0))
    assert d.opponent_hand == [3]  # only filler remains


def test_facedown_opponent_active_predicted_as_basic():
    me, opp = make_player(make_pokemon()), make_player()
    opp.active = [None]
    o = obs_with(me, opp)
    d = BeliefState(DECK, BASICS).sample(o, random.Random(0))
    assert d.opponent_active == [100]


def test_setup_opponent_deck_contains_a_basic():
    me, opp = make_player(make_pokemon()), make_player(make_pokemon())
    o = obs_with(me, opp)
    o.current.turn = 0
    d = BeliefState(DECK, BASICS).sample(o, random.Random(1))
    assert any(c in BASICS for c in d.opponent_deck)


def test_turn_regression_resets_tracking():
    me, opp = make_player(make_pokemon()), make_player(make_pokemon())
    opp.handCount = 1
    b = BeliefState([3] * 60, frozenset())
    o1 = obs_with(me, opp, [Log(type=LogType.SHUFFLE, playerIndex=1, cardId=200,
                                serial=77, toArea=AreaType.HAND)])
    o1.current.turn = 9
    b.update(o1)
    o2 = obs_with(me, opp)
    o2.current.turn = 0  # new match started
    b.update(o2)
    d = b.sample(o2, random.Random(0))
    assert 200 not in d.opponent_hand
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_belief.py -v`
Expected: FAIL — `No module named 'ptcg.search.belief'`

- [ ] **Step 3: Implement**

```python
"""Belief v1: exact accounting for our zones, mirror-prior sampling for the opponent's.

The engine's search_begin needs a full prediction of every hidden zone. We know our
own deck list exactly; the opponent's unseen cards are padded from a mirror prior
(our own list) minus everything they have revealed. Log tracking is limited to
opponent-hand knowledge (cards seen moving to/from their hand).
"""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field

from cg.api import AreaType, Observation, PlayerState, State


@dataclass
class Determinization:
    your_deck: list[int]
    your_prize: list[int]
    opponent_deck: list[int]
    opponent_prize: list[int]
    opponent_hand: list[int]
    opponent_active: list[int]


def _visible_ids(p: PlayerState, include_hand: bool) -> list[int]:
    ids = [c.id for c in p.discard]
    for pk in list(p.active) + list(p.bench):
        if pk is None:
            continue
        ids.append(pk.id)
        for group in (pk.energyCards, pk.tools, pk.preEvolution):
            ids.extend(c.id for c in group)
    ids.extend(c.id for c in p.prize if c is not None)
    if include_hand and p.hand is not None:
        ids.extend(c.id for c in p.hand)
    return ids


def _pool_list(counter: Counter, rng: random.Random) -> list[int]:
    pool = [cid for cid, n in counter.items() for _ in range(max(n, 0))]
    rng.shuffle(pool)
    return pool


@dataclass
class BeliefState:
    my_deck: list[int]
    basic_ids: frozenset[int] = frozenset()
    opp_known_hand: dict[int, int] = field(default_factory=dict)  # serial -> cardId
    _last_turn: int = -1

    def update(self, obs: Observation) -> None:
        st = obs.current
        if st is None:
            return
        if st.turn < self._last_turn:  # arena reuses agents across matches
            self.opp_known_hand.clear()
        self._last_turn = st.turn
        opp = 1 - st.yourIndex
        for log in obs.logs:
            if log.playerIndex != opp or log.serial is None:
                continue
            if log.toArea == AreaType.HAND and log.cardId is not None:
                self.opp_known_hand[log.serial] = log.cardId
            elif log.fromArea == AreaType.HAND:
                self.opp_known_hand.pop(log.serial, None)

    def sample(self, obs: Observation, rng: random.Random) -> Determinization:
        st: State = obs.current
        me_i = st.yourIndex
        me, opp = st.players[me_i], st.players[1 - me_i]
        my_stadium = [c.id for c in st.stadium if c.playerIndex == me_i]
        opp_stadium = [c.id for c in st.stadium if c.playerIndex != me_i]
        filler = Counter(self.my_deck).most_common(1)[0][0]

        # --- our side: exact multiset ---
        unseen = Counter(self.my_deck)
        unseen.subtract(Counter(_visible_ids(me, include_hand=True) + my_stadium))
        pool = _pool_list(unseen, rng)
        need = me.deckCount + sum(1 for c in me.prize if c is None)
        pool.extend([filler] * max(need - len(pool), 0))
        your_prize = [c.id if c is not None else pool.pop() for c in me.prize]
        your_deck = [pool.pop() for _ in range(me.deckCount)]

        # --- opponent side: mirror prior ---
        known_hand = list(self.opp_known_hand.values())[: opp.handCount]
        prior = Counter(self.my_deck)
        prior.subtract(Counter(_visible_ids(opp, include_hand=False) + opp_stadium))
        prior.subtract(Counter(known_hand))
        pool = _pool_list(prior, rng)
        need = (opp.deckCount + sum(1 for c in opp.prize if c is None)
                + (opp.handCount - len(known_hand)) + 1)  # +1 for a possible active
        pool.extend([filler] * max(need - len(pool), 0))
        opponent_hand = known_hand + [pool.pop() for _ in range(opp.handCount - len(known_hand))]
        opponent_prize = [c.id if c is not None else pool.pop() for c in opp.prize]
        opponent_deck = [pool.pop() for _ in range(opp.deckCount)]

        if st.turn == 0 and self.basic_ids and opponent_deck and \
                not any(c in self.basic_ids for c in opponent_deck):
            basic = next((c for c in self.my_deck if c in self.basic_ids), None)
            if basic is not None:
                opponent_deck[0] = basic

        opponent_active: list[int] = []
        if opp.active and opp.active[0] is None:
            opponent_active = [next((c for c in pool + opponent_deck if c in self.basic_ids),
                                    next((c for c in self.my_deck if c in self.basic_ids),
                                         filler))]

        return Determinization(your_deck, your_prize, opponent_deck,
                               opponent_prize, opponent_hand, opponent_active)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_belief.py -v` — Expected: 7 passed. Then `uv run pytest` — full fast suite green. Note: fixture `make_player` defaults `hand=None` with `handCount=0`; if any test needs a non-empty own hand, pass `hand=[card(...)]` explicitly (Task 10 later improves this default — do not fix it here).

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/search/belief.py tests/test_belief.py
git commit -m "feat: BeliefState v1 — exact own-zone + mirror-prior opponent determinization"
```

---

### Task 4: Leaf evaluator (`evaluate.py`)

**Files:**
- Create: `src/ptcg/search/evaluate.py`
- Test: `tests/test_evaluate.py`

**Interfaces:**
- Consumes: `cg.api.State`/`PlayerState`/`Pokemon` (dataclasses only).
- Produces: `evaluate(state: State, my_index: int) -> float` in [0, 1] — Task 6 calls it at leaves.

Terminal states dominate: `result == my_index` → 1.0, opponent → 0.0, `result == 2` (draw) → 0.5. Non-terminal is `0.5 + 0.5 * clamp(raw, -1, 1)` with `raw = 0.5*prize + 0.2*damage + 0.15*development + 0.1*hand + 0.05*deckout`:
- prize: `(len(opp.prize) - len(me.prize)) / 6` (taking prizes shrinks the array — fewer left for me is winning),
- damage: mean fractional damage on their board minus mean on ours (`(maxHp - hp)/maxHp` over active+bench, 0 if no Pokémon),
- development: `clamp((energies+bench count for me minus theirs) / 10, -1, 1)` counting attached energies plus bench size,
- hand: `clamp((me.handCount - opp.handCount) / 5, -1, 1)`,
- deckout: `-1.0` if `me.deckCount == 0` else (`+1.0` if `opp.deckCount == 0` else 0) — drawing from an empty deck loses.

- [ ] **Step 1: Write failing tests**

```python
"""Leaf evaluator ordering sanity."""
from ptcg.search.evaluate import evaluate
from tests.fixtures.obs import make_player, make_pokemon, make_state


def test_terminal_win_loss_draw():
    st = make_state()
    st.result = 0
    assert evaluate(st, 0) == 1.0
    assert evaluate(st, 1) == 0.0
    st.result = 2
    assert evaluate(st, 0) == 0.5


def test_fewer_prizes_remaining_scores_higher():
    ahead, even = make_state(), make_state()
    ahead.players[0].prize = [None] * 1   # we've taken 5
    assert evaluate(ahead, 0) > evaluate(even, 0)


def test_damage_on_opponent_scores_higher():
    hurt_opp = make_state(opp=make_player(make_pokemon(hp=10, max_hp=70)))
    even = make_state()
    assert evaluate(hurt_opp, 0) > evaluate(even, 0)


def test_symmetric_state_is_half():
    assert abs(evaluate(make_state(), 0) - 0.5) < 1e-9


def test_own_deckout_is_bad():
    st = make_state()
    st.players[0].deckCount = 0
    assert evaluate(st, 0) < 0.5


def test_bounded():
    st = make_state()
    st.players[0].prize = []
    st.players[0].handCount = 40
    assert 0.0 <= evaluate(st, 0) <= 1.0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_evaluate.py -v` — Expected: FAIL, module missing.

- [ ] **Step 3: Implement**

```python
"""Hand-tuned leaf evaluator. Replaced wholesale by the Slice-4 value net."""
from __future__ import annotations

from cg.api import PlayerState, State

_W_PRIZE, _W_DMG, _W_DEV, _W_HAND, _W_DECK = 0.5, 0.2, 0.15, 0.1, 0.05


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _board(p: PlayerState) -> list:
    return [pk for pk in list(p.active) + list(p.bench) if pk is not None]


def _damage_frac(p: PlayerState) -> float:
    board = _board(p)
    if not board:
        return 0.0
    return sum((pk.maxHp - pk.hp) / max(pk.maxHp, 1) for pk in board) / len(board)


def _development(p: PlayerState) -> float:
    return sum(len(pk.energies) for pk in _board(p)) + len(p.bench)


def evaluate(state: State, my_index: int) -> float:
    """Value of `state` for player `my_index`, in [0, 1]."""
    if state.result == my_index:
        return 1.0
    if state.result == 1 - my_index:
        return 0.0
    if state.result != -1:
        return 0.5  # draw or unknown future result code
    me, opp = state.players[my_index], state.players[1 - my_index]
    prize = (len(opp.prize) - len(me.prize)) / 6.0
    damage = _damage_frac(opp) - _damage_frac(me)
    dev = _clamp((_development(me) - _development(opp)) / 10.0)
    hand = _clamp((me.handCount - opp.handCount) / 5.0)
    deck = -1.0 if me.deckCount == 0 else (1.0 if opp.deckCount == 0 else 0.0)
    raw = (_W_PRIZE * prize + _W_DMG * damage + _W_DEV * dev
           + _W_HAND * hand + _W_DECK * deck)
    return 0.5 + 0.5 * _clamp(raw)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_evaluate.py -v` — Expected: 6 passed. Then `uv run pytest` — green.

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/search/evaluate.py tests/test_evaluate.py
git commit -m "feat: hand-tuned leaf evaluator for search"
```

---

### Task 5: Action signatures + tree (`tree.py`)

**Files:**
- Create: `src/ptcg/search/tree.py`
- Test: `tests/test_tree.py`

**Interfaces:**
- Consumes: `cg.api.Option`/`SelectData`.
- Produces: `ActionSig` (hashable tuple), `option_signature(opt: Option) -> tuple`, `action_signature(select: SelectData, indices: Sequence[int]) -> ActionSig`, `Node` (fields `visits: int`, `value_sum: float`, `children: dict[ActionSig, Node]`, property `mean`), `ucb_pick(node: Node, legal: Sequence[ActionSig], c: float, rng: random.Random) -> ActionSig`. Task 6 consumes all of these.

**The correctness keystone of the slice:** signatures identify a move by its *content*, never by list position or per-determinization `serial`. Two determinizations can present the same logical move at different indices; keying by content merges their statistics. `serial` and `Option.index` are EXCLUDED from signatures (serials of predicted hidden cards differ per determinization; index is positional). `inPlayArea`/`inPlayIndex` ARE included (they name a board target, which is meaningful). None fields map to -1 so tuples stay comparable/sortable.

- [ ] **Step 1: Write failing tests**

```python
"""Canonical action signatures + UCB tree."""
import random

from cg.api import AreaType, Option, OptionType

from ptcg.search.tree import Node, action_signature, option_signature, ucb_pick
from tests.fixtures.obs import make_select


def opt(card_id=None, attack_id=None, area=None, index=None, serial=None):
    return Option(type=OptionType.CARD if card_id else OptionType.ATTACK,
                  cardId=card_id, attackId=attack_id, area=area, index=index,
                  serial=serial)


def test_signature_ignores_list_position_and_serial():
    a1 = opt(card_id=100, area=AreaType.HAND, index=0, serial=11)
    a2 = opt(card_id=100, area=AreaType.HAND, index=4, serial=99)
    assert option_signature(a1) == option_signature(a2)


def test_signature_distinguishes_different_cards_and_attacks():
    assert option_signature(opt(card_id=100)) != option_signature(opt(card_id=200))
    assert option_signature(opt(attack_id=7)) != option_signature(opt(attack_id=8))


def test_action_signature_is_order_independent_for_multiselect():
    sel = make_select([opt(card_id=100), opt(card_id=200)])
    assert action_signature(sel, (0, 1)) == action_signature(sel, (1, 0))


def test_index_misalignment_across_determinizations_merges_stats():
    # Determinization 1 presents [A, B]; determinization 2 presents [B, A].
    det1 = make_select([opt(card_id=100), opt(card_id=200)])
    det2 = make_select([opt(card_id=200), opt(card_id=100)])
    sig_a_in_det1 = action_signature(det1, (0,))
    sig_a_in_det2 = action_signature(det2, (1,))
    assert sig_a_in_det1 == sig_a_in_det2  # same logical move -> same tree child
    node = Node()
    node.children[sig_a_in_det1] = Node(visits=3, value_sum=2.0)
    assert node.children[sig_a_in_det2].visits == 3


def test_ucb_prefers_unvisited_then_best_mean():
    rng = random.Random(0)
    node = Node(visits=10)
    s1, s2, s3 = ("a",), ("b",), ("c",)
    node.children[s1] = Node(visits=5, value_sum=1.0)
    node.children[s2] = Node(visits=5, value_sum=4.5)
    assert ucb_pick(node, [s1, s2, s3], c=1.4, rng=rng) == s3  # unvisited first
    node.children[s3] = Node(visits=5, value_sum=0.5)
    node.visits = 15
    assert ucb_pick(node, [s1, s2, s3], c=0.0, rng=rng) == s2  # pure exploit -> best mean
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_tree.py -v` — Expected: FAIL, module missing.

- [ ] **Step 3: Implement**

```python
"""MCTS tree keyed by canonical action signatures.

Option indices are NOT stable across determinizations, and serials of predicted
hidden cards differ per determinization. Signatures therefore use only
content-bearing fields. None -> -1 keeps tuples hashable and sortable.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Sequence

from cg.api import Option, SelectData

ActionSig = tuple


def _n(x) -> int:
    return -1 if x is None else int(x)


def option_signature(opt: Option) -> tuple:
    return (_n(opt.type), _n(opt.cardId), _n(opt.attackId), _n(opt.number),
            _n(opt.area), _n(opt.playerIndex), _n(opt.toolIndex), _n(opt.energyIndex),
            _n(opt.count), _n(opt.inPlayArea), _n(opt.inPlayIndex),
            _n(opt.specialConditionType))


def action_signature(select: SelectData, indices: Sequence[int]) -> ActionSig:
    return tuple(sorted(option_signature(select.option[i]) for i in indices))


@dataclass
class Node:
    visits: int = 0
    value_sum: float = 0.0
    children: dict[ActionSig, "Node"] = field(default_factory=dict)

    @property
    def mean(self) -> float:
        return self.value_sum / self.visits if self.visits else 0.0


def ucb_pick(node: Node, legal: Sequence[ActionSig], c: float,
             rng: random.Random) -> ActionSig:
    unvisited = [s for s in legal
                 if s not in node.children or node.children[s].visits == 0]
    if unvisited:
        return rng.choice(unvisited)
    log_n = math.log(max(node.visits, 1))
    return max(legal, key=lambda s: (node.children[s].mean
                                     + c * math.sqrt(log_n / node.children[s].visits)))
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_tree.py -v` — Expected: 5 passed. Then `uv run pytest` — green.

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/search/tree.py tests/test_tree.py
git commit -m "feat: canonical action signatures + UCB1 tree (index-misalignment safe)"
```

---

### Task 6: ISMCTS Searcher (`searcher.py`)

**Files:**
- Create: `src/ptcg/search/searcher.py`
- Test: `tests/test_searcher.py` (fake backend — no engine)

**Interfaces:**
- Consumes: `BeliefState.sample` (Task 3), `evaluate` (Task 4), `tree.py` (Task 5), `cg.api.search_begin/search_step/search_end` (engine, behind adapter).
- Produces:
  - `forced_selection(select: SelectData) -> list[int] | None` — `[]` when `maxCount == 0`; all indices when `minCount == maxCount == len(option)`; `[0]` when there is exactly 1 option and `minCount >= 1`; else None. Task 7 reuses it.
  - `SearchBackend` protocol: `begin(obs, det) -> SearchState`, `step(search_id, select) -> SearchState`, `end() -> None`.
  - `EngineBackend` — thin `cg.api` adapter.
  - `SearchConfig(c_uct: float = 1.4, max_iterations: int = 400, max_depth: int = 40, seed: int = 0)` — defaults revisited against Task-1 spike numbers.
  - `Searcher(belief, backend, opponent_policy, rollout_policy, evaluator=evaluate, config=SearchConfig())` with `search(obs: Observation, deadline: float) -> list[int] | None` and counters `iterations_run`, `begin_failures`. Policies are `Callable[[Observation], list[int]]`.

Algorithm per iteration (spec §3): sample determinization → `begin` → descend: terminal → value; opponent's decision (`yourIndex != my_index`) → `opponent_policy` step (no node); forced or multi-select (`minCount != 1 or maxCount != 1`) → `rollout_policy` step (no node — v1 branches only single-select decisions); else UCB over signatures of the CURRENT observation's options, step, descend; first freshly-created child ends the descent with an evaluator value. Depth cap → evaluator value. Backprop along our-decision path. `backend.end()` after EVERY iteration (frees all engine search states — our Python tree survives). Return `[index]` of the most-visited root child mapped through the REAL root observation's signature→index table; None if no iterations completed.

- [ ] **Step 1: Write failing tests (fake backend)**

```python
"""Searcher against a scripted fake backend — no engine required."""
import random
import time
from dataclasses import replace

from cg.api import Option, OptionType, SearchState

from ptcg.search.belief import Determinization
from ptcg.search.searcher import SearchConfig, Searcher, forced_selection
from tests.fixtures.obs import make_obs, make_select, make_state


def opt(card_id):
    return Option(type=OptionType.CARD, cardId=card_id)


def sel1(options):
    return make_select(options, min_count=1, max_count=1)


class FakeBelief:
    def sample(self, obs, rng):
        return Determinization([], [], [], [], [], [])


class FakeBackend:
    """Root with [A(100), B(200)]; A leads to a win-for-me terminal, B to a loss.

    Alternating begins swap the option order to prove index-misalignment safety.
    """

    def __init__(self):
        self.begins = 0
        self.ended = 0

    def begin(self, obs, det):
        self.begins += 1
        swap = self.begins % 2 == 0
        options = [opt(200), opt(100)] if swap else [opt(100), opt(200)]
        o = make_obs(sel1(options), make_state())
        o.search_begin_input = None
        self._win_index = 1 if swap else 0
        return SearchState(observation=o, searchId=self.begins)

    def step(self, search_id, select):
        won = select == [self._win_index]
        st = make_state()
        st.result = 0 if won else 1  # my_index is 0 in make_state()
        o = make_obs(sel1([opt(999)]), st)
        return SearchState(observation=o, searchId=search_id)

    def end(self):
        self.ended += 1


def searcher(backend, iterations=40):
    return Searcher(FakeBelief(), backend, opponent_policy=lambda o: [0],
                    rollout_policy=lambda o: [0],
                    config=SearchConfig(max_iterations=iterations, seed=0))


def test_picks_winning_move_despite_index_swaps():
    obs = make_obs(sel1([opt(100), opt(200)]), make_state())
    s = searcher(FakeBackend())
    assert s.search(obs, deadline=time.perf_counter() + 5.0) == [0]  # A is index 0 in REAL obs


def test_backend_end_called_every_iteration():
    b = FakeBackend()
    s = searcher(b, iterations=10)
    s.search(make_obs(sel1([opt(100), opt(200)]), make_state()),
             deadline=time.perf_counter() + 5.0)
    assert b.ended == b.begins == 10


def test_deadline_respected():
    s = searcher(FakeBackend(), iterations=10_000)
    t0 = time.perf_counter()
    s.search(make_obs(sel1([opt(100), opt(200)]), make_state()), deadline=t0 + 0.05)
    assert time.perf_counter() - t0 < 1.0
    assert 0 < s.iterations_run < 10_000


def test_returns_none_when_no_iteration_completes():
    class ExplodingBackend(FakeBackend):
        def begin(self, obs, det):
            raise ValueError("bad determinization")

        def end(self):
            pass

    s = searcher(ExplodingBackend(), iterations=5)
    obs = make_obs(sel1([opt(100), opt(200)]), make_state())
    assert s.search(obs, deadline=time.perf_counter() + 1.0) is None
    assert s.begin_failures == 5


def test_forced_selection():
    assert forced_selection(sel1([opt(1)])) == [0]
    both = make_select([opt(1), opt(2)], min_count=2, max_count=2)
    assert forced_selection(both) == [0, 1]
    none_allowed = make_select([opt(1)], min_count=0, max_count=0)
    assert forced_selection(none_allowed) == []
    free = make_select([opt(1), opt(2)], min_count=1, max_count=1)
    assert forced_selection(free) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_searcher.py -v` — Expected: FAIL, module missing.

- [ ] **Step 3: Implement**

```python
"""ISMCTS driver: shared tree over determinizations, opponent as fixed policy.

Every iteration restarts from the real root observation (the engine only allows
search_begin on it) and replays down the tree with search_step. backend.end()
runs after every iteration so the engine's search arena never grows.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from cg import api
from cg.api import Observation, SearchState, SelectData

from ptcg.search.belief import BeliefState, Determinization
from ptcg.search.evaluate import evaluate
from ptcg.search.tree import Node, action_signature, ucb_pick

Policy = Callable[[Observation], list[int]]


def forced_selection(select: SelectData) -> list[int] | None:
    n = len(select.option)
    if select.maxCount == 0:
        return []
    if select.minCount == n and select.maxCount == n:
        return list(range(n))
    if n == 1 and select.minCount >= 1:
        return [0]
    return None


class SearchBackend(Protocol):
    def begin(self, obs: Observation, det: Determinization) -> SearchState: ...
    def step(self, search_id: int, select: list[int]) -> SearchState: ...
    def end(self) -> None: ...


class EngineBackend:
    def begin(self, obs: Observation, det: Determinization) -> SearchState:
        return api.search_begin(obs, det.your_deck, det.your_prize,
                                det.opponent_deck, det.opponent_prize,
                                det.opponent_hand, det.opponent_active)

    def step(self, search_id: int, select: list[int]) -> SearchState:
        return api.search_step(search_id, select)

    def end(self) -> None:
        api.search_end()


@dataclass
class SearchConfig:
    c_uct: float = 1.4
    max_iterations: int = 400
    max_depth: int = 40
    seed: int = 0


class Searcher:
    def __init__(self, belief: BeliefState, backend: SearchBackend,
                 opponent_policy: Policy, rollout_policy: Policy,
                 evaluator: Callable = evaluate,
                 config: SearchConfig | None = None) -> None:
        self.belief = belief
        self.backend = backend
        self.opponent_policy = opponent_policy
        self.rollout_policy = rollout_policy
        self.evaluator = evaluator
        self.config = config or SearchConfig()
        self.rng = random.Random(self.config.seed)
        self.iterations_run = 0
        self.begin_failures = 0

    def search(self, obs: Observation, deadline: float) -> list[int] | None:
        cfg = self.config
        my_index = obs.current.yourIndex
        root_index = {action_signature(obs.select, (i,)): i
                      for i in range(len(obs.select.option))}
        root = Node()
        self.iterations_run = 0
        self.begin_failures = 0
        while time.perf_counter() < deadline and self.iterations_run < cfg.max_iterations:
            self.iterations_run += 1
            det = self.belief.sample(obs, self.rng)
            try:
                state = self.backend.begin(obs, det)
            except (ValueError, RuntimeError):
                self.begin_failures += 1
                continue
            try:
                self._simulate(state, root, my_index)
            finally:
                self.backend.end()
        visited = {s: n for s, n in root.children.items() if n.visits > 0}
        if not visited:
            return None
        best = max(visited, key=lambda s: visited[s].visits)
        idx = root_index.get(best)
        return None if idx is None else [idx]

    def _simulate(self, state: SearchState, root: Node, my_index: int) -> None:
        path = [root]
        node = root
        value: float | None = None
        for _ in range(self.config.max_depth):
            o = state.observation
            st = o.current
            if st.result != -1:
                value = (1.0 if st.result == my_index
                         else 0.0 if st.result == 1 - my_index else 0.5)
                break
            sel = o.select
            forced = forced_selection(sel)
            if st.yourIndex != my_index:
                state = self.backend.step(state.searchId,
                                          forced if forced is not None
                                          else self.opponent_policy(o))
                continue
            if forced is not None or sel.minCount != 1 or sel.maxCount != 1:
                state = self.backend.step(state.searchId,
                                          forced if forced is not None
                                          else self.rollout_policy(o))
                continue
            legal = {action_signature(sel, (i,)): i for i in range(len(sel.option))}
            sig = ucb_pick(node, list(legal), self.config.c_uct, self.rng)
            child = node.children.setdefault(sig, Node())
            state = self.backend.step(state.searchId, [legal[sig]])
            path.append(child)
            fresh = child.visits == 0
            node = child
            if fresh:
                value = self.evaluator(state.observation.current, my_index)
                break
        if value is None:
            value = self.evaluator(state.observation.current, my_index)
        for n in path:
            n.visits += 1
            n.value_sum += value
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_searcher.py -v` — Expected: 5 passed. Then `uv run pytest` — green.

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/search/searcher.py tests/test_searcher.py
git commit -m "feat: ISMCTS searcher — shared tree, fixed opponent policy, anytime deadline"
```

---

### Task 7: SearchAgent + arena CLI wiring

**Files:**
- Create: `src/ptcg/agents/search_agent.py`
- Modify: `scripts/run_arena.py` (AGENTS factories take the agent's deck; add `--search-budget-ms`)
- Test: `tests/test_search_agent.py` (fake backend)

**Interfaces:**
- Consumes: everything from Tasks 2–6, `ptcg.agents.heuristic.choose`, `cg.api.all_card_data/all_attack/CardType`.
- Produces: `SearchAgent(deck: list[int], config: SearchConfig | None = None, time_manager: TimeManager | None = None, backend: SearchBackend | None = None)`, `name = "search-v1"`, counter `fallbacks: int`. Tasks 8/9/11 construct it.

`act(obs)`: measure wall time; `belief.update(obs)`; forced → return immediately; non-single-select (`minCount != 1 or maxCount != 1`) → heuristic `choose`; else search with `deadline = t0 + tm.move_budget()`; None result or ANY exception → `choose` + increment `fallbacks`; `finally: tm.note_move(...)`. `choose` never raises (it has its own internal fallback), so the except branch is safe.

- [ ] **Step 1: Write failing tests**

```python
"""SearchAgent glue: shortcuts, search path, fallback."""
from cg.api import Option, OptionType

from ptcg.agents.search_agent import SearchAgent
from ptcg.search.timing import TimeManager
from tests.fixtures.obs import make_obs, make_select, make_state


def opt(card_id):
    return Option(type=OptionType.CARD, cardId=card_id)


class BoomBackend:
    def begin(self, obs, det):
        raise RuntimeError("engine says no")

    def step(self, search_id, select):
        raise AssertionError("unreachable")

    def end(self):
        pass


def agent(backend):
    return SearchAgent([3] * 60, backend=backend,
                       time_manager=TimeManager(max_move_s=0.05))


def test_forced_single_option_answered_without_search():
    a = agent(BoomBackend())
    obs = make_obs(make_select([opt(1)], min_count=1, max_count=1), make_state())
    assert a.act(obs) == [0]
    assert a.fallbacks == 0  # shortcut, not fallback


def test_search_failure_falls_back_to_heuristic_validly():
    a = agent(BoomBackend())
    obs = make_obs(make_select([opt(1), opt(2)], min_count=1, max_count=1), make_state())
    out = a.act(obs)
    assert len(out) == 1 and out[0] in (0, 1)
    assert a.fallbacks == 1


def test_time_is_recorded():
    a = agent(BoomBackend())
    obs = make_obs(make_select([opt(1)], min_count=1, max_count=1), make_state())
    a.act(obs)
    assert a.tm.moves_done == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_search_agent.py -v` — Expected: FAIL, module missing.

Note: `SearchAgent.__init__` calls `all_card_data()`/`all_attack()` (engine DB, no battle needed) — fine in the fast suite; existing tests already load the DLL.

- [ ] **Step 3: Implement**

```python
"""SearchAgent: determinized-MCTS agent with heuristic-v0 safety net."""
from __future__ import annotations

import time

from cg.api import CardType, Observation, all_attack, all_card_data

from ptcg.agents.base import Agent
from ptcg.agents.heuristic import choose
from ptcg.search.belief import BeliefState
from ptcg.search.searcher import (EngineBackend, SearchBackend, SearchConfig,
                                  Searcher, forced_selection)
from ptcg.search.timing import TimeManager


class SearchAgent(Agent):
    name = "search-v1"

    def __init__(self, deck: list[int], config: SearchConfig | None = None,
                 time_manager: TimeManager | None = None,
                 backend: SearchBackend | None = None) -> None:
        self.cards = {c.cardId: c for c in all_card_data()}
        self.attacks = {a.attackId: a for a in all_attack()}
        basic_ids = frozenset(c.cardId for c in all_card_data()
                              if c.cardType == CardType.POKEMON and c.basic)
        self.belief = BeliefState(list(deck), basic_ids)
        self.tm = time_manager or TimeManager()
        policy = self._policy
        self.searcher = Searcher(self.belief, backend or EngineBackend(),
                                 opponent_policy=policy, rollout_policy=policy,
                                 config=config or SearchConfig())
        self.fallbacks = 0

    def _policy(self, obs: Observation) -> list[int]:
        return choose(obs, self.cards, self.attacks)

    def act(self, obs: Observation) -> list[int]:
        t0 = time.perf_counter()
        try:
            self.belief.update(obs)
            sel = obs.select
            forced = forced_selection(sel)
            if forced is not None:
                return forced
            if sel.minCount != 1 or sel.maxCount != 1:
                return self._policy(obs)
            deadline = t0 + self.tm.move_budget()
            result = self.searcher.search(obs, deadline)
            if result is None:
                self.fallbacks += 1
                return self._policy(obs)
            return result
        except Exception:  # noqa: BLE001 — v0 fallback is the safety invariant
            self.fallbacks += 1
            return self._policy(obs)
        finally:
            self.tm.note_move(time.perf_counter() - t0)
```

- [ ] **Step 4: Wire into `scripts/run_arena.py`**

Replace the `AGENTS` dict and its two use sites (factories now take the agent's own deck), and add the budget flag:

```python
from ptcg.agents.search_agent import SearchAgent  # noqa: E402
from ptcg.search.searcher import SearchConfig  # noqa: E402
from ptcg.search.timing import TimeManager  # noqa: E402

AGENTS = {
    "random": lambda deck, budget_ms: RandomAgent(seed=0),
    "heuristic": lambda deck, budget_ms: HeuristicAgent(),
    "search": lambda deck, budget_ms: SearchAgent(
        deck, time_manager=TimeManager(total_s=1e9, max_move_s=budget_ms / 1000.0)),
}
```

In `main()`, add `p.add_argument("--search-budget-ms", type=int, default=200)` and build:

```python
    deck_a, deck_b = load_deck(args.deck_a), load_deck(args.deck_b)
    agent_a = AGENTS[args.agent_a](deck_a, args.search_budget_ms)
    agent_b = AGENTS[args.agent_b](deck_b, args.search_budget_ms)
    stats = run_series(agent_a, agent_b, deck_a, deck_b, args.games)
```

(`total_s=1e9` pins the arena budget to `max_move_s` — arena games must not taper like a real 10-minute match.)

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_search_agent.py -v` — Expected: 3 passed. Then `uv run pytest` — green. Then a 2-game live sanity run:

`uv run python scripts/run_arena.py --agent-a search --agent-b random --games 2 --search-budget-ms 100 --notes "T7 wiring sanity, 2g"`
Expected: completes without error, prints a result line (win/loss irrelevant at n=2).

- [ ] **Step 6: Commit**

```bash
git add src/ptcg/agents/search_agent.py tests/test_search_agent.py scripts/run_arena.py experiments/EXPERIMENTS.md
git commit -m "feat: SearchAgent with v0 fallback; arena CLI grows search + budget flag"
```

---

### Task 8: Engine integration + hygiene tests

**Files:**
- Create: `tests/test_search_integration.py`

**Interfaces:**
- Consumes: `SearchAgent` (Task 7), `play_match`/`run_series` (existing), `load_deck`.
- Produces: fast-suite confidence that search runs against the real engine. Task 9 relies on this being green before burning hours on acceptance.

- [ ] **Step 1: Write the integration tests**

```python
"""SearchAgent vs the real engine at tiny budgets — crash-free is the bar."""
from pathlib import Path

from ptcg.agents.heuristic import HeuristicAgent
from ptcg.agents.random_agent import RandomAgent
from ptcg.agents.search_agent import SearchAgent
from ptcg.arena.runner import load_deck, play_match
from ptcg.search.searcher import SearchConfig
from ptcg.search.timing import TimeManager

DECK = load_deck(Path(__file__).resolve().parents[1]
                 / "src/ptcg/decks/candidates/mega-lucario-fighting.csv")


def tiny_search_agent() -> SearchAgent:
    return SearchAgent(DECK, config=SearchConfig(max_iterations=8, max_depth=20),
                       time_manager=TimeManager(total_s=1e9, max_move_s=0.03))


def test_full_game_vs_random_crash_free():
    r = play_match(tiny_search_agent(), RandomAgent(seed=1), DECK, DECK)
    assert r.error is None
    assert r.winner in (0, 1, 2)


def test_full_game_vs_heuristic_crash_free_and_search_actually_ran():
    a = tiny_search_agent()
    r = play_match(a, HeuristicAgent(), DECK, DECK)
    assert r.error is None
    total_decisions = a.tm.moves_done
    assert total_decisions > 0
    # Not every decision may search (forced/multi-select shortcuts), but a full
    # game must not be 100% fallback: that would mean search_begin never works.
    assert a.fallbacks < total_decisions


def test_two_consecutive_games_reuse_agent_cleanly():
    a = tiny_search_agent()
    for seed in (1, 2):
        r = play_match(a, RandomAgent(seed=seed), DECK, DECK)
        assert r.error is None
```

- [ ] **Step 2: Run**

Run: `uv run pytest tests/test_search_integration.py -v`
Expected: 3 passed (each game ~a few seconds at 30 ms/move). If `fallbacks == total_decisions`, debug `search_begin` inputs (most likely a Determinization count bug — print `a.searcher.begin_failures`) BEFORE proceeding; do not weaken the assertion.

- [ ] **Step 3: Full suite + commit**

Run: `uv run pytest` — Expected: green, runtime still < ~2 min.

```bash
git add tests/test_search_integration.py
git commit -m "test: engine integration — search agent full games crash-free at tiny budget"
```

---

### Task 9: Acceptance series (slow) + constants tuning

**Files:**
- Create: `tests/test_search_acceptance.py`
- Modify: `experiments/EXPERIMENTS.md` (arena rows), possibly `SearchConfig`/`TimeManager` defaults (tuning)

**Interfaces:**
- Consumes: `run_series`, `SearchAgent`, `wilson_ci`, spike numbers.
- Produces: the slice's gate evidence — acceptance assertions + logged experiment rows.

- [ ] **Step 1: Pilot series (50 games) via CLI**

Run: `uv run python scripts/run_arena.py --agent-a search --agent-b heuristic --games 50 --deck-a src/ptcg/decks/candidates/mega-lucario-fighting.csv --deck-b src/ptcg/decks/candidates/mega-lucario-fighting.csv --search-budget-ms 200 --notes "T9 pilot, search v1 defaults"`
Expected: completes (~20–40 min); note the win rate. If < 50%, STOP: tune before the long run — the knobs, in order of leverage: `SearchConfig.max_depth` (deeper sees attack outcomes), evaluator weights, `c_uct`, K implicit via iteration count. Log EVERY tuning run to EXPERIMENTS.md with a hypothesis in the notes. If ≥ 55%, proceed.

- [ ] **Step 2: Write the slow acceptance test**

```python
"""Slice-2 acceptance gate: search-v1 must beat heuristic-v0 significantly."""
from pathlib import Path

import pytest

from ptcg.agents.heuristic import HeuristicAgent
from ptcg.agents.search_agent import SearchAgent
from ptcg.arena.runner import load_deck, run_series
from ptcg.arena.stats import wilson_ci
from ptcg.search.timing import TimeManager

DECK = load_deck(Path(__file__).resolve().parents[1]
                 / "src/ptcg/decks/candidates/mega-lucario-fighting.csv")


@pytest.mark.slow
def test_search_beats_heuristic_significantly():
    search = SearchAgent(DECK, time_manager=TimeManager(total_s=1e9, max_move_s=0.2))
    stats = run_series(search, HeuristicAgent(), DECK, DECK, n_games=200)
    lo, _ = wilson_ci(stats.wins_a, stats.n)
    assert stats.win_rate_a >= 0.55, f"win rate {stats.win_rate_a:.3f} < 0.55 ({stats})"
    assert lo > 0.50, f"Wilson LCB {lo:.3f} <= 0.50 — not significant ({stats})"
```

- [ ] **Step 3: Run the acceptance series**

Run: `uv run pytest -m slow tests/test_search_acceptance.py -v` (expect ~1–2 h wall time; run in background and check on completion — report the single result line, not a poll stream).
Expected: PASS. If FAIL: this is a tuning loop, not a rubber stamp — adjust constants/evaluator (each attempt logged to EXPERIMENTS.md with hypothesis + result), re-run the 50-game pilot first, and only re-run the 200-game series when the pilot clears 55%. Do NOT lower the acceptance bar; if two full tuning cycles fail, STOP and report the numbers.

- [ ] **Step 4: Log the accepted series + commit**

Append the accepted 200-game row to EXPERIMENTS.md via a CLI run note or manual row (same format), then:

```bash
git add tests/test_search_acceptance.py experiments/EXPERIMENTS.md src/ptcg/search/searcher.py src/ptcg/search/evaluate.py src/ptcg/search/timing.py
git commit -m "test: slice-2 acceptance — search-v1 beats heuristic-v0 (200g, Wilson LCB > 0.5)"
```
(Include the search modules only if tuning changed them; state final constants in the commit body.)

---

### Task 10: Slice-1 deferred minors (cleanup)

**Files:**
- Modify: `src/ptcg/arena/stats.py`, `src/ptcg/arena/runner.py:79-83`, `tests/fixtures/obs.py`, `tests/test_stats.py`, plus whichever test files the greps below surface.

**Interfaces:**
- Consumes/Produces: no new interfaces — behavior-preserving cleanup. All four items in ONE task so the suite is green in one pass.

- [ ] **Step 1: markdown_row pipe-escaping (TDD).** Add to `tests/test_stats.py`:

```python
def test_markdown_row_escapes_pipes():
    s = SeriesStats(wins_a=1, wins_b=0, draws=0, game_seconds=[1.0])
    row = s.markdown_row("2026-07-09", "a|b", "c", "d|e", "f", "note|with|pipes")
    cells = row.split(" | ")
    assert "a\\|b" in row and "note\\|with\\|pipes" in row
    assert row.count("|") - row.count("\\|") == 14  # table structure intact
```

Run it (FAIL), then in `stats.py` add `def _esc(s: str) -> str: return str(s).replace("|", "\\|")` and wrap the six caller-supplied fields in `markdown_row` (`date, agent_a, agent_b, deck_a, deck_b, notes`) with `_esc(...)`. Run: PASS.

- [ ] **Step 2: MatchResult tuple shape.** In `runner.py` `play_match`, `tuple(max_move)` types as `tuple[float, ...]`; replace both occurrences with `(max_move[0], max_move[1])`. No behavior change; existing tests must stay green.

- [ ] **Step 3: AreaType enums in fixture tests.** Run `grep -n "area=[0-9]" tests/*.py tests/fixtures/*.py`. Replace every raw int with the corresponding `AreaType` member (per `src/cg/api.py:11-24`: DECK=1, HAND=2, DISCARD=3, ...) and add the import. If the grep returns nothing, record "already clean" in the task report — do not invent changes.

- [ ] **Step 4: make_player hand default.** In `tests/fixtures/obs.py`, change the signature to make hidden-vs-empty hands explicit:

```python
def make_player(active: Pokemon | None = None, hand: list | None = None,
                hand_hidden: bool = False, hand_count: int | None = None) -> PlayerState:
    resolved_hand = None if hand_hidden else (hand or [])
    return PlayerState(
        active=[active] if active else [], bench=[], benchMax=5, deckCount=40,
        discard=[], prize=[None] * 6,
        handCount=hand_count if hand_count is not None else len(hand or []),
        hand=resolved_hand,
        poisoned=False, burned=False, asleep=False, paralyzed=False, confused=False,
    )
```

Grep callers (`grep -rn "make_player" tests/`) and fix any that relied on `hand=None` meaning hidden (they should now pass `hand_hidden=True`). Run the full suite.

- [ ] **Step 5: Full suite + commit**

Run: `uv run pytest` — Expected: green.

```bash
git add src/ptcg/arena/stats.py src/ptcg/arena/runner.py tests/fixtures/obs.py tests/test_stats.py
git commit -m "chore: slice-1 deferred minors — pipe-escaping, tuple shape, AreaType enums, make_player hand"
```
(Add any other touched test files to the explicit-path list.)

---

### Task 11: Submission bundle — SearchAgent to the ladder

**Files:**
- Modify: `src/ptcg/submission_main.py`, possibly `scripts/package_submission.py`
- Test: extend `tests/test_packaging.py`

**Interfaces:**
- Consumes: `SearchAgent`, existing packaging flow (`uv run python scripts/package_submission.py <deck.csv>`).
- Produces: `submission.tar.gz` with search-v1 as the entry agent.

- [ ] **Step 1: Read the current packaging flow.** Grep `scripts/package_submission.py` and `tests/test_packaging.py` for what gets copied (does it copy the whole `src/ptcg` package? Then `src/ptcg/search/` rides along free — verify, don't assume) and how the bare-exec smoke loads `main.py` (`compile()`+`exec()` into a namespace WITHOUT `__file__` — the episode-84917131 LESSON; preserve it exactly).

- [ ] **Step 2: Switch `submission_main.py` to SearchAgent with layered fallback.** Replace the agent construction block (currently `_agent = HeuristicAgent()`) with:

```python
from cg.api import to_observation_class  # noqa: E402
from ptcg.agents.heuristic import HeuristicAgent  # noqa: E402


def _build_agent():
    """search-v1 with layered fallback: constructor failure -> heuristic-v0."""
    try:
        from ptcg.agents.search_agent import SearchAgent
        from ptcg.search.timing import TimeManager
        return SearchAgent(read_deck_csv(),
                           time_manager=TimeManager(total_s=480.0, max_move_s=1.0))
    except Exception:
        return HeuristicAgent()


_agent = _build_agent()
```

(`read_deck_csv` must therefore be defined ABOVE `_build_agent` — move it up if needed. `max_move_s=1.0` is the Kaggle safety factor: half the local 1.5 s headroom + total budget 480 s of the 600 s cap. SearchAgent's per-decision try/except remains the in-match safety net.)

- [ ] **Step 3: Rebuild + smoke.** Run: `uv run python scripts/package_submission.py src/ptcg/decks/candidates/mega-lucario-fighting.csv`
Expected: bundle builds; smoke passes (bare-exec load, deck answer, a legal first selection). Extend `tests/test_packaging.py` with an assertion that the built bundle contains `ptcg/search/` (adjust to the packager's actual layout discovered in Step 1).

- [ ] **Step 4: One timed self-match in bundle-like conditions.** Run a single `play_match(SearchAgent(DECK, time_manager=TimeManager(total_s=480, max_move_s=1.0)), HeuristicAgent(), DECK, DECK)` via a short inline script; assert `seconds < 480` and print `max_move_seconds`. This is the timeout-risk smoke — record the numbers in EXPERIMENTS.md.

- [ ] **Step 5: Full suite, commit**

Run: `uv run pytest` — Expected: green.

```bash
git add src/ptcg/submission_main.py tests/test_packaging.py scripts/package_submission.py experiments/EXPERIMENTS.md
git commit -m "feat: submission bundle ships search-v1 with layered v0 fallback"
```

(Upload to Kaggle is Brad's manual step — surface it as the slice's open user action; record the validation episode result in plan.md when known.)

---

### Task 12: Final verification + docs

**Files:**
- Modify: `CLAUDE.md` (project), `experiments/EXPERIMENTS.md` (if any rows missing), `.claude/plan.md` (via plan-manager, not directly by the implementer)

**Interfaces:** none — verification and documentation.

- [ ] **Step 1: Full fast suite + count check.** Run `uv run pytest -v` — all green; test count must EXCEED Slice 1's 34 (expect ~60+). A shrunken count means tests were lost — investigate.
- [ ] **Step 2: Slow gate evidence check.** Confirm EXPERIMENTS.md contains: the Task-1 spike section, the Task-9 pilot + accepted 200-game rows, the Task-11 timed self-match numbers. These are the Strategy-report raw material — missing rows get reconstructed NOW while the session remembers them.
- [ ] **Step 3: Update project CLAUDE.md.** Add to the Commands section: the `--search-budget-ms` arena flag; a one-paragraph "Search agent (Slice 2)" note under "Things that matter": ISMCTS on the engine search API, belief v1 mirror prior, v0 fallback invariant, and that `search_begin` only works on the real agent observation.
- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md experiments/EXPERIMENTS.md
git commit -m "docs: slice-2 search agent — commands, architecture notes, experiment log"
```

---

## Self-Review (done at write time)

- **Spec coverage:** §1 acceptance → T9; §2 components → T2–T7; §3 algorithm (incl. signature keying + fixed opponent policy + open-loop chance) → T5/T6; §4 belief v1 → T3; §5 timing/safety/spike → T1/T2/T6/T11; §6 evaluator → T4; §7 testing ladder → T8/T9 + packaging in T11; §8 minors + gitignore → T10 (gitignore already done at branch creation); §9 risks all have owning tasks. One divergence vs spec: tests live in `tests/` (Slice-1 established layout), not next to code — repo reality wins.
- **Placeholder scan:** clean — every code step has complete code; the two "grep first" steps (T10.3, T11.1) are discovery steps with explicit fallback instructions, not deferred work.
- **Type consistency:** `Determinization` field order matches `search_begin` arg order; `SearchBackend.begin(obs, det)` used identically in T6 fake and T7 engine paths; `TimeManager(total_s=1e9, max_move_s=X)` pattern identical in T7 CLI, T8, T9; `forced_selection` defined once (T6) and imported in T7.
