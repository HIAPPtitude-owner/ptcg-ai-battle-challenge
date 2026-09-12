# Slice 1: Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Local evaluation arena + baseline heuristic agent + candidate decks + verified Kaggle submission bundle for the PTCG AI Battle Challenge.

**Architecture:** A `src/`-layout Python project. The competition's `cg` SDK (Python wrapper over a native engine DLL) is vendored by script into `src/cg/` (git-ignored, licensed material). Our `src/ptcg/` package provides an `Agent` protocol, a battle-runner arena with statistics, a rule-based heuristic agent, deck analysis/validation, and a packaging script that emits `submission.tar.gz`.

**Tech Stack:** Python 3.11, uv, pytest. Agent runtime is **stdlib-only** (no third-party imports in anything that ships in the bundle).

## Global Constraints

- **License:** never commit `pokemon-tcg-ai-battle/` or `src/cg/` (both git-ignored). The vendor script copies the SDK locally; the bundle includes it, git does not.
- **Working dir:** `C:\Users\Brad\Dev Folder\Product\ptcg-ai-battle-challenge-strategy` (path contains a space — always quote). Branch: `feature/slice1-foundation`.
- **Stdlib-only** in `src/ptcg/agents/`, `src/cg/` consumers that ship in the bundle, and `main.py`.
- **Selection invariants** (engine contract): every returned list satisfies `select.minCount <= len(r) <= select.maxCount`, all elements unique, each `0 <= i < len(select.option)`.
- **Tolerant parsing:** the organizers may append enum members/attributes mid-competition. Never `raise` on unknown `SelectContext`/`OptionType` — fall back (see heuristic fallback).
- Run tests with `uv run pytest` from the repo root. Stage git adds by explicit path (never `git add .`).
- The engine's randomness (shuffles, coins) is NOT seedable through the API — only agent RNG is seeded. Reproducibility is statistical, not exact.

## File Structure

```
pyproject.toml                     # T1
scripts/vendor_sdk.py              # T1
scripts/run_arena.py               # T9
scripts/package_submission.py      # T11
src/cg/                            # vendored by T1 (git-ignored)
src/ptcg/__init__.py               # T1
src/ptcg/agents/__init__.py        # T1
src/ptcg/agents/base.py            # T3
src/ptcg/agents/random_agent.py    # T3
src/ptcg/agents/heuristic.py       # T5
src/ptcg/arena/__init__.py         # T1
src/ptcg/arena/stats.py            # T2
src/ptcg/arena/runner.py           # T4
src/ptcg/decks/__init__.py         # T1
src/ptcg/decks/analysis.py         # T6
src/ptcg/decks/validate.py         # T7
src/ptcg/decks/candidates/*.csv    # T8
src/ptcg/submission_main.py        # T11 (template copied to bundle main.py)
experiments/EXPERIMENTS.md         # T1 (seeded), appended by T9/T10
tests/fixtures/sample_deck.csv     # T1
tests/fixtures/obs.py              # T3 (synthetic Observation builders)
tests/test_stats.py                # T2
tests/test_random_agent.py         # T3
tests/test_runner.py               # T4
tests/test_heuristic.py            # T5
tests/test_analysis.py             # T6
tests/test_validate.py             # T7
tests/test_packaging.py            # T11
```

---

### Task 1: Project scaffold + vendored SDK

**Files:**
- Create: `pyproject.toml`, `scripts/vendor_sdk.py`, `src/ptcg/__init__.py`, `src/ptcg/agents/__init__.py`, `src/ptcg/arena/__init__.py`, `src/ptcg/decks/__init__.py`, `experiments/EXPERIMENTS.md`, `tests/fixtures/sample_deck.csv`, `tests/test_scaffold.py`

**Interfaces:**
- Produces: importable `cg` and `ptcg` packages under `src/` with pytest `pythonpath=["src"]`; `tests/fixtures/sample_deck.csv` (60 card-ID lines) used by all arena tests.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "ptcg"
version = "0.1.0"
description = "PTCG AI Battle Challenge agent + evaluation harness"
requires-python = ">=3.11"
dependencies = []

[dependency-groups]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
pythonpath = ["src", "."]
testpaths = ["tests"]
markers = ["slow: long-running acceptance series (deselect with -m 'not slow')"]
addopts = "-m 'not slow'"

[tool.ruff]
line-length = 100
```

- [ ] **Step 2: Write `scripts/vendor_sdk.py`**

```python
"""Copy the competition cg SDK into src/cg (git-ignored; licensed material)."""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "pokemon-tcg-ai-battle" / "sample_submission" / "sample_submission" / "cg"
DST = ROOT / "src" / "cg"


def main() -> None:
    if not SRC.is_dir():
        raise SystemExit(f"SDK source not found: {SRC}")
    if DST.exists():
        shutil.rmtree(DST)
    shutil.copytree(SRC, DST)
    print(f"Vendored {SRC} -> {DST}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create package inits and fixture deck**

Create empty `src/ptcg/__init__.py`, `src/ptcg/agents/__init__.py`, `src/ptcg/arena/__init__.py`, `src/ptcg/decks/__init__.py`.

Copy the sample deck: `Copy-Item "pokemon-tcg-ai-battle\sample_submission\sample_submission\deck.csv" "tests\fixtures\sample_deck.csv"`

Create `experiments/EXPERIMENTS.md`:

```markdown
# Experiment Log — PTCG AI Battle Challenge

Every arena series is appended here. This log is the evidence base for the
Strategy-track writeup (judged on hypotheses tested and results measured).

| Date | Agent A | Agent B | Deck A | Deck B | Games | A wins | B wins | Draws | A win-rate [95% CI] | Avg game s | Max move s | Notes |
|------|---------|---------|--------|--------|-------|--------|--------|-------|---------------------|------------|------------|-------|
```

- [ ] **Step 4: Write the scaffold test**

`tests/test_scaffold.py`:

```python
"""Scaffold sanity: vendored SDK imports and exposes the card database."""


def test_cg_sdk_imports_and_lists_cards():
    from cg.api import all_card_data

    cards = all_card_data()
    assert len(cards) > 100
    assert all(hasattr(c, "cardId") for c in cards[:5])


def test_sample_deck_fixture_has_60_lines():
    from pathlib import Path

    lines = Path("tests/fixtures/sample_deck.csv").read_text().strip().splitlines()
    assert len(lines) == 60
    assert all(line.strip().isdigit() for line in lines)
```

- [ ] **Step 5: Vendor the SDK, sync, run tests**

```powershell
uv run python scripts/vendor_sdk.py
uv sync
uv run pytest tests/test_scaffold.py -v
```

Expected: both tests PASS (the `cg` import loads `cg.dll` and calls `GameInitialize` at import time — a pass proves the native engine works on this machine).

- [ ] **Step 6: Commit**

```powershell
git add pyproject.toml scripts/vendor_sdk.py src/ptcg/__init__.py src/ptcg/agents/__init__.py src/ptcg/arena/__init__.py src/ptcg/decks/__init__.py experiments/EXPERIMENTS.md tests/fixtures/sample_deck.csv tests/test_scaffold.py uv.lock
git commit -m "feat: project scaffold, vendored SDK loader, experiment log"
```

---

### Task 2: Statistics module

**Files:**
- Create: `src/ptcg/arena/stats.py`, `tests/test_stats.py`

**Interfaces:**
- Produces: `wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]`; `@dataclass SeriesStats` with fields `wins_a: int, wins_b: int, draws: int, game_seconds: list[float], max_move_seconds: float` and properties `n`, `win_rate_a`, `ci_a` plus `markdown_row(date, agent_a, agent_b, deck_a, deck_b, notes) -> str`.

- [ ] **Step 1: Write failing tests**

`tests/test_stats.py`:

```python
import math

from ptcg.arena.stats import SeriesStats, wilson_ci


def test_wilson_ci_known_value():
    lo, hi = wilson_ci(90, 100)
    assert math.isclose(lo, 0.8256, abs_tol=0.001)
    assert math.isclose(hi, 0.9448, abs_tol=0.001)


def test_wilson_ci_zero_n():
    assert wilson_ci(0, 0) == (0.0, 1.0)


def test_series_stats_aggregates():
    s = SeriesStats(wins_a=9, wins_b=1, draws=0,
                    game_seconds=[1.0, 2.0], max_move_seconds=0.5)
    assert s.n == 10
    assert s.win_rate_a == 0.9
    lo, hi = s.ci_a
    assert 0 < lo < 0.9 < hi <= 1.0


def test_markdown_row_shape():
    s = SeriesStats(wins_a=1, wins_b=0, draws=0, game_seconds=[1.0], max_move_seconds=0.1)
    row = s.markdown_row("2026-07-08", "heuristic", "random", "d1.csv", "d2.csv", "smoke")
    assert row.startswith("| 2026-07-08 |")
    assert row.count("|") == 14
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_stats.py -v` — Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement `src/ptcg/arena/stats.py`**

```python
"""Series statistics: win-rates with Wilson confidence intervals, timing."""
from __future__ import annotations

import math
from dataclasses import dataclass, field


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


@dataclass
class SeriesStats:
    wins_a: int = 0
    wins_b: int = 0
    draws: int = 0
    game_seconds: list[float] = field(default_factory=list)
    max_move_seconds: float = 0.0

    @property
    def n(self) -> int:
        return self.wins_a + self.wins_b + self.draws

    @property
    def win_rate_a(self) -> float:
        return self.wins_a / self.n if self.n else 0.0

    @property
    def ci_a(self) -> tuple[float, float]:
        return wilson_ci(self.wins_a, self.n)

    def markdown_row(self, date: str, agent_a: str, agent_b: str,
                     deck_a: str, deck_b: str, notes: str) -> str:
        lo, hi = self.ci_a
        avg_s = sum(self.game_seconds) / len(self.game_seconds) if self.game_seconds else 0.0
        return (
            f"| {date} | {agent_a} | {agent_b} | {deck_a} | {deck_b} | {self.n} "
            f"| {self.wins_a} | {self.wins_b} | {self.draws} "
            f"| {self.win_rate_a:.3f} [{lo:.3f}, {hi:.3f}] "
            f"| {avg_s:.2f} | {self.max_move_seconds:.3f} | {notes} |"
        )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_stats.py -v` — Expected: 4 PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/ptcg/arena/stats.py tests/test_stats.py
git commit -m "feat: series statistics with Wilson CI"
```

---

### Task 3: Agent protocol + random agent + observation fixtures

**Files:**
- Create: `src/ptcg/agents/base.py`, `src/ptcg/agents/random_agent.py`, `tests/fixtures/__init__.py`, `tests/fixtures/obs.py`, `tests/test_random_agent.py`

**Interfaces:**
- Consumes: `cg.api.Observation`, `SelectData`, `Option`, enums.
- Produces: `class Agent(ABC)` with `name: str` attribute and `act(self, obs: Observation) -> list[int]`; `class RandomAgent(Agent)` with `__init__(self, seed: int | None = None)`; fixture builders `make_select(...) -> SelectData` and `make_obs(...) -> Observation` used by T4/T5 tests.

- [ ] **Step 1: Write `src/ptcg/agents/base.py`**

```python
"""Agent protocol. An agent maps an engine Observation to selected option indices."""
from __future__ import annotations

from abc import ABC, abstractmethod

from cg.api import Observation


class Agent(ABC):
    name: str = "agent"

    @abstractmethod
    def act(self, obs: Observation) -> list[int]:
        """Return option indices: minCount <= len <= maxCount, unique, in range."""
```

- [ ] **Step 2: Write fixture builders `tests/fixtures/obs.py`**

```python
"""Builders for synthetic Observations so agent logic is testable without the engine."""
from __future__ import annotations

from cg.api import (
    Observation, SelectData, SelectType, SelectContext, Option, OptionType,
    State, PlayerState, Pokemon,
)


def make_pokemon(card_id: int = 721, hp: int = 70, max_hp: int = 70,
                 energies: list | None = None) -> Pokemon:
    return Pokemon(id=card_id, serial=1, hp=hp, maxHp=max_hp, appearThisTurn=False,
                   energies=energies or [], energyCards=[], tools=[], preEvolution=[])


def make_player(active: Pokemon | None = None, hand: list | None = None) -> PlayerState:
    return PlayerState(
        active=[active] if active else [], bench=[], benchMax=5, deckCount=40,
        discard=[], prize=[None] * 6, handCount=len(hand or []), hand=hand,
        poisoned=False, burned=False, asleep=False, paralyzed=False, confused=False,
    )


def make_state(you: PlayerState | None = None, opp: PlayerState | None = None,
               your_index: int = 0, turn: int = 3,
               energy_attached: bool = False, turn_action_count: int = 0) -> State:
    players = [you or make_player(make_pokemon()), opp or make_player(make_pokemon())]
    if your_index == 1:
        players.reverse()
    return State(turn=turn, turnActionCount=turn_action_count, yourIndex=your_index,
                 firstPlayer=0, supporterPlayed=False, stadiumPlayed=False,
                 energyAttached=energy_attached, retreated=False, result=-1,
                 stadium=[], looking=None, players=players)


def make_select(options: list[Option], select_type: SelectType = SelectType.MAIN,
                context: SelectContext = SelectContext.MAIN,
                min_count: int = 1, max_count: int = 1) -> SelectData:
    return SelectData(type=select_type, context=context, minCount=min_count,
                      maxCount=max_count, remainDamageCounter=0, remainEnergyCost=0,
                      option=options, deck=None, contextCard=None, effect=None)


def make_obs(select: SelectData, state: State | None = None) -> Observation:
    return Observation(select=select, logs=[], current=state or make_state())
```

Also create empty `tests/fixtures/__init__.py`.

- [ ] **Step 3: Write failing tests `tests/test_random_agent.py`**

```python
from cg.api import Option, OptionType

from ptcg.agents.random_agent import RandomAgent
from tests.fixtures.obs import make_obs, make_select


def _options(n: int) -> list[Option]:
    return [Option(type=OptionType.CARD, area=2, index=i, playerIndex=0) for i in range(n)]


def test_random_agent_respects_counts_and_range():
    agent = RandomAgent(seed=42)
    obs = make_obs(make_select(_options(5), min_count=1, max_count=3))
    for _ in range(50):
        r = agent.act(obs)
        assert 1 <= len(r) <= 3
        assert len(set(r)) == len(r)
        assert all(0 <= i < 5 for i in r)


def test_random_agent_seeded_reproducible():
    a, b = RandomAgent(seed=7), RandomAgent(seed=7)
    obs = make_obs(make_select(_options(6), min_count=2, max_count=4))
    assert [a.act(obs) for _ in range(10)] == [b.act(obs) for _ in range(10)]
```

- [ ] **Step 4: Run to verify failure**

Run: `uv run pytest tests/test_random_agent.py -v` — Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 5: Implement `src/ptcg/agents/random_agent.py`**

```python
"""Uniform-random legal agent — the floor any real agent must dominate."""
from __future__ import annotations

import random

from cg.api import Observation

from .base import Agent


class RandomAgent(Agent):
    name = "random"

    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)

    def act(self, obs: Observation) -> list[int]:
        sel = obs.select
        k = self.rng.randint(sel.minCount, sel.maxCount)
        return self.rng.sample(range(len(sel.option)), k)
```

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest tests/test_random_agent.py -v` — Expected: 2 PASS.

- [ ] **Step 7: Commit**

```powershell
git add src/ptcg/agents/base.py src/ptcg/agents/random_agent.py tests/fixtures/__init__.py tests/fixtures/obs.py tests/test_random_agent.py
git commit -m "feat: agent protocol, random agent, observation fixtures"
```

---

### Task 4: Arena runner

**Files:**
- Create: `src/ptcg/arena/runner.py`, `tests/test_runner.py`

**Interfaces:**
- Consumes: `Agent.act`, `cg.game.battle_start/battle_select/battle_finish`, `cg.api.to_observation_class`, `SeriesStats`.
- Produces: `@dataclass MatchResult(winner: int, turns: int, moves: int, seconds: float, max_move_seconds: tuple[float, float], error: str | None)` (winner: 0/1 player index, 2 = draw, -1 = errored); `play_match(agent0: Agent, agent1: Agent, deck0: list[int], deck1: list[int], max_moves: int = 3000) -> MatchResult`; `run_series(agent_a: Agent, agent_b: Agent, deck_a: list[int], deck_b: list[int], n_games: int) -> SeriesStats` (alternates which agent is player 0); `load_deck(path: str | Path) -> list[int]`.

- [ ] **Step 1: Write failing integration test**

`tests/test_runner.py`:

```python
from pathlib import Path

from ptcg.agents.random_agent import RandomAgent
from ptcg.arena.runner import load_deck, play_match, run_series

DECK = Path("tests/fixtures/sample_deck.csv")


def test_load_deck():
    deck = load_deck(DECK)
    assert len(deck) == 60
    assert all(isinstance(c, int) for c in deck)


def test_single_match_completes():
    deck = load_deck(DECK)
    r = play_match(RandomAgent(seed=1), RandomAgent(seed=2), deck, deck)
    assert r.error is None
    assert r.winner in (0, 1, 2)
    assert r.moves > 0
    assert r.seconds > 0


def test_series_alternates_and_aggregates():
    deck = load_deck(DECK)
    s = run_series(RandomAgent(seed=1), RandomAgent(seed=2), deck, deck, n_games=4)
    assert s.n == 4
    assert s.wins_a + s.wins_b + s.draws == 4
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_runner.py -v` — Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement `src/ptcg/arena/runner.py`**

```python
"""Battle runner: pits two Agents against each other via the cg engine.

The cg SDK holds one battle per process (module-global Battle state), so
matches run sequentially within a process.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from cg.api import to_observation_class
from cg.game import battle_finish, battle_select, battle_start

from ptcg.agents.base import Agent
from ptcg.arena.stats import SeriesStats


def load_deck(path: str | Path) -> list[int]:
    lines = Path(path).read_text().strip().splitlines()
    deck = [int(line.strip()) for line in lines if line.strip()]
    if len(deck) != 60:
        raise ValueError(f"{path}: expected 60 cards, got {len(deck)}")
    return deck


def _validate_selection(selection: list[int], n_options: int,
                        min_count: int, max_count: int, agent_name: str) -> None:
    if not (min_count <= len(selection) <= max_count):
        raise AssertionError(
            f"{agent_name}: returned {len(selection)} selections, "
            f"allowed [{min_count}, {max_count}]")
    if len(set(selection)) != len(selection):
        raise AssertionError(f"{agent_name}: duplicate selections {selection}")
    if any(not (0 <= i < n_options) for i in selection):
        raise AssertionError(f"{agent_name}: selection out of range {selection}")


@dataclass
class MatchResult:
    winner: int  # 0/1 player index, 2 = draw, -1 = errored
    turns: int
    moves: int
    seconds: float
    max_move_seconds: tuple[float, float]  # per player index
    error: str | None = None


def play_match(agent0: Agent, agent1: Agent, deck0: list[int], deck1: list[int],
               max_moves: int = 3000) -> MatchResult:
    agents = (agent0, agent1)
    max_move = [0.0, 0.0]
    start = time.perf_counter()
    obs_dict, start_data = battle_start(deck0, deck1)
    if obs_dict is None:
        return MatchResult(-1, 0, 0, 0.0, (0.0, 0.0),
                           error=f"battle_start failed: player={start_data.errorPlayer} "
                                 f"type={start_data.errorType}")
    moves = 0
    try:
        while True:
            obs = to_observation_class(obs_dict)
            state = obs.current
            if state.result != -1:
                winner = state.result
                break
            if moves >= max_moves:
                return MatchResult(-1, state.turn, moves, time.perf_counter() - start,
                                   tuple(max_move), error="max_moves exceeded")
            idx = state.yourIndex
            t0 = time.perf_counter()
            selection = agents[idx].act(obs)
            max_move[idx] = max(max_move[idx], time.perf_counter() - t0)
            _validate_selection(selection, len(obs.select.option),
                                obs.select.minCount, obs.select.maxCount,
                                agents[idx].name)
            obs_dict = battle_select(selection)
            moves += 1
        return MatchResult(winner, obs.current.turn, moves,
                           time.perf_counter() - start, tuple(max_move))
    except Exception as exc:  # noqa: BLE001 — record, don't crash the series
        return MatchResult(-1, 0, moves, time.perf_counter() - start,
                           tuple(max_move), error=f"{type(exc).__name__}: {exc}")
    finally:
        battle_finish()


def run_series(agent_a: Agent, agent_b: Agent, deck_a: list[int], deck_b: list[int],
               n_games: int) -> SeriesStats:
    """Alternates seats: even games A is player 0, odd games B is player 0."""
    stats = SeriesStats()
    for g in range(n_games):
        a_is_p0 = g % 2 == 0
        if a_is_p0:
            r = play_match(agent_a, agent_b, deck_a, deck_b)
        else:
            r = play_match(agent_b, agent_a, deck_b, deck_a)
        if r.error is not None:
            raise RuntimeError(f"game {g}: {r.error}")
        stats.game_seconds.append(r.seconds)
        stats.max_move_seconds = max(stats.max_move_seconds, *r.max_move_seconds)
        if r.winner == 2:
            stats.draws += 1
        elif (r.winner == 0) == a_is_p0:
            stats.wins_a += 1
        else:
            stats.wins_b += 1
    return stats
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_runner.py -v` — Expected: 3 PASS. Note the runtime of `test_single_match_completes` in your report (first real engine-speed datapoint).

- [ ] **Step 5: Commit**

```powershell
git add src/ptcg/arena/runner.py tests/test_runner.py
git commit -m "feat: arena runner with selection invariants and seat alternation"
```

---

### Task 5: Heuristic agent v0

**Files:**
- Create: `src/ptcg/agents/heuristic.py`, `tests/test_heuristic.py`

**Interfaces:**
- Consumes: `Agent`, fixture builders from `tests/fixtures/obs.py`, `cg.api.all_card_data/all_attack` (cached at construction).
- Produces: `class HeuristicAgent(Agent)` with `__init__(self)` (loads card/attack DBs once) and pure helper `choose(obs: Observation, cards: dict[int, CardData], attacks: dict[int, Attack]) -> list[int]` (module-level function — unit-testable without engine state).

- [ ] **Step 1: Write failing tests**

`tests/test_heuristic.py`:

```python
from cg.api import Attack, CardData, CardType, EnergyType, Option, OptionType, SelectContext, SelectType

from ptcg.agents.heuristic import choose
from tests.fixtures.obs import make_obs, make_pokemon, make_player, make_select, make_state

ATTACKS = {
    10: Attack(attackId=10, name="Weak", text="", damage=30, energies=[EnergyType.COLORLESS]),
    11: Attack(attackId=11, name="Strong", text="", damage=90, energies=[EnergyType.WATER]),
}
CARDS: dict[int, CardData] = {}


def test_lethal_attack_preferred_over_end():
    opp = make_player(make_pokemon(hp=30))
    you = make_player(make_pokemon())
    state = make_state(you=you, opp=opp)
    options = [
        Option(type=OptionType.END),
        Option(type=OptionType.ATTACK, attackId=10),  # 30 dmg == opp hp -> lethal
    ]
    obs = make_obs(make_select(options), state)
    assert choose(obs, CARDS, ATTACKS) == [1]


def test_highest_damage_lethal_wins_tiebreak():
    opp = make_player(make_pokemon(hp=30))
    state = make_state(you=make_player(make_pokemon()), opp=opp)
    options = [
        Option(type=OptionType.ATTACK, attackId=10),  # lethal, 30 dmg
        Option(type=OptionType.ATTACK, attackId=11),  # lethal, 90 dmg -> preferred
    ]
    obs = make_obs(make_select(options), state)
    assert choose(obs, CARDS, ATTACKS) == [1]


def test_higher_damage_attack_when_no_lethal():
    opp = make_player(make_pokemon(hp=200))
    state = make_state(you=make_player(make_pokemon()), opp=opp)
    options = [
        Option(type=OptionType.ATTACK, attackId=10),
        Option(type=OptionType.ATTACK, attackId=11),
        Option(type=OptionType.END),
    ]
    obs = make_obs(make_select(options), state)
    assert choose(obs, CARDS, ATTACKS) == [1]


def test_evolve_preferred_over_plain_attack():
    state = make_state(you=make_player(make_pokemon()), opp=make_player(make_pokemon(hp=200)))
    options = [
        Option(type=OptionType.ATTACK, attackId=10),
        Option(type=OptionType.EVOLVE, area=2, index=0, inPlayArea=4, inPlayIndex=0),
    ]
    obs = make_obs(make_select(options), state)
    assert choose(obs, CARDS, ATTACKS) == [1]


def test_unknown_context_fallback_respects_counts():
    options = [Option(type=OptionType.CARD, area=2, index=i, playerIndex=0) for i in range(4)]
    sel = make_select(options, select_type=SelectType.CARD,
                      context=SelectContext.LOOK, min_count=0, max_count=2)
    obs = make_obs(sel)
    r = choose(obs, CARDS, ATTACKS)
    assert 0 <= len(r) <= 2
    assert all(0 <= i < 4 for i in r)


def test_loop_guard_prefers_end_after_many_actions():
    state = make_state(turn_action_count=60,
                       you=make_player(make_pokemon()), opp=make_player(make_pokemon(hp=200)))
    options = [
        Option(type=OptionType.ABILITY, area=4, index=0),
        Option(type=OptionType.END),
    ]
    obs = make_obs(make_select(options), state)
    assert choose(obs, CARDS, ATTACKS) == [1]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_heuristic.py -v` — Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement `src/ptcg/agents/heuristic.py`**

```python
"""Heuristic baseline v0: deterministic priority rules over engine-legal options.

Design: `choose` is a pure function of (observation, card DB, attack DB) so unit
tests need no engine state. HeuristicAgent caches the DBs once per process.

Unknown contexts and option types NEVER raise — the competition may append enum
members mid-season; the fallback picks the first maxCount options.
"""
from __future__ import annotations

from cg.api import (
    Attack, CardData, CardType, Observation, Option, OptionType,
    SelectContext, SelectType, all_attack, all_card_data,
)

from .base import Agent

# Contexts where fewer/cheaper selections are better (costs, discards).
_MINIMIZE_CONTEXTS = frozenset({
    SelectContext.DISCARD, SelectContext.DISCARD_ENERGY,
    SelectContext.DISCARD_ENERGY_CARD, SelectContext.DISCARD_TOOL_CARD,
    SelectContext.DISCARD_CARD_OR_ATTACHED_CARD, SelectContext.TO_DECK,
    SelectContext.TO_DECK_BOTTOM, SelectContext.TO_DECK_ENERGY,
    SelectContext.TO_PRIZE, SelectContext.DEVOLVE, SelectContext.DISABLE_ATTACK,
})

_LOOP_GUARD_ACTIONS = 50  # after this many actions in one turn, wrap up


def _opp_active_hp(obs: Observation) -> int | None:
    state = obs.current
    if state is None:
        return None
    opp = state.players[1 - state.yourIndex]
    if opp.active and opp.active[0] is not None:
        return opp.active[0].hp
    return None


def _attack_damage(option: Option, attacks: dict[int, Attack]) -> int:
    atk = attacks.get(option.attackId or -1)
    return atk.damage if atk else 0


def _main_priority(option: Option, obs: Observation,
                   attacks: dict[int, Attack]) -> tuple:
    """Lower tuple = better. Priorities: lethal attack, evolve, attach, play,
    ability, best attack, other, retreat, end."""
    t = option.type
    opp_hp = _opp_active_hp(obs)
    loop_guard = (obs.current is not None
                  and obs.current.turnActionCount > _LOOP_GUARD_ACTIONS)
    if t == OptionType.ATTACK:
        dmg = _attack_damage(option, attacks)
        if opp_hp is not None and dmg >= opp_hp > 0:
            return (0, -dmg)
        return (5, -dmg)
    if loop_guard:
        # Only attacking (handled above) or ending beats anything else now.
        return (8, 0) if t == OptionType.END else (9, 0)
    if t == OptionType.EVOLVE:
        return (1, 0)
    if t == OptionType.ATTACH:
        return (2, 0)
    if t == OptionType.PLAY:
        return (3, 0)
    if t == OptionType.ABILITY:
        return (4, 0)
    if t == OptionType.RETREAT:
        return (8, 0)
    if t == OptionType.END:
        return (9, 0)
    return (7, 0)


def _choose_main(obs: Observation, attacks: dict[int, Attack]) -> list[int]:
    options = obs.select.option
    best = min(range(len(options)),
               key=lambda i: _main_priority(options[i], obs, attacks))
    return [best]


def _resolve_card_id(obs: Observation, option: Option) -> int | None:
    """Map a CARD option to the underlying card id via the state containers."""
    state, sel = obs.current, obs.select
    if option.area is None or option.index is None:
        return None
    try:
        area = int(option.area)
        if area == 1 and sel.deck is not None:  # DECK
            return sel.deck[option.index].id
        if state is None or option.playerIndex is None:
            return None
        player = state.players[option.playerIndex]
        if area == 2 and player.hand is not None:  # HAND
            return player.hand[option.index].id
        if area == 3:  # DISCARD
            return player.discard[option.index].id
        if area == 4 and player.active and player.active[0]:  # ACTIVE
            return player.active[0].id
        if area == 5:  # BENCH
            return player.bench[option.index].id
        if area == 12 and state.looking:  # LOOKING
            card = state.looking[option.index]
            return card.id if card else None
    except (IndexError, TypeError):
        return None
    return None


def _card_value(card_id: int | None, cards: dict[int, CardData]) -> float:
    """Static desirability of a card. Pokémon > supporter > item > energy."""
    if card_id is None or card_id not in cards:
        return 1.0
    card = cards[card_id]
    ct = card.cardType
    if ct == CardType.POKEMON:
        return 3.0 + card.hp / 1000.0
    if ct == CardType.SUPPORTER:
        return 2.0
    if ct in (CardType.ITEM, CardType.TOOL, CardType.STADIUM):
        return 1.5
    return 0.5  # energies are replaceable


def _choose_cards(obs: Observation, cards: dict[int, CardData]) -> list[int]:
    sel = obs.select
    minimize = sel.context in _MINIMIZE_CONTEXTS
    k = sel.minCount if minimize else sel.maxCount
    if k == 0:
        return []
    scored = sorted(
        range(len(sel.option)),
        key=lambda i: _card_value(_resolve_card_id(obs, sel.option[i]), cards),
        reverse=not minimize,
    )
    return scored[:k]


def _choose_yes_no(obs: Observation) -> list[int]:
    sel = obs.select
    want_no = sel.context == SelectContext.MULLIGAN
    target = OptionType.NO if want_no else OptionType.YES
    for i, option in enumerate(sel.option):
        if option.type == target:
            return [i]
    return [0]


def _choose_count(obs: Observation) -> list[int]:
    sel = obs.select
    best = max(range(len(sel.option)),
               key=lambda i: sel.option[i].number or 0)
    return [best]


def _choose_attack(obs: Observation, attacks: dict[int, Attack]) -> list[int]:
    sel = obs.select
    opp_hp = _opp_active_hp(obs)

    def key(i: int) -> tuple:
        dmg = _attack_damage(sel.option[i], attacks)
        lethal = opp_hp is not None and dmg >= opp_hp > 0
        return (0 if lethal else 1, -dmg)

    return [min(range(len(sel.option)), key=key)]


def _fallback(obs: Observation) -> list[int]:
    sel = obs.select
    k = sel.maxCount
    return list(range(min(k, len(sel.option))))


def choose(obs: Observation, cards: dict[int, CardData],
           attacks: dict[int, Attack]) -> list[int]:
    sel = obs.select
    try:
        st = sel.type
        if st == SelectType.MAIN:
            return _choose_main(obs, attacks)
        if st in (SelectType.CARD, SelectType.CARD_OR_ATTACHED_CARD,
                  SelectType.ATTACHED_CARD):
            return _choose_cards(obs, cards)
        if st == SelectType.YES_NO:
            return _choose_yes_no(obs)
        if st == SelectType.COUNT:
            return _choose_count(obs)
        if st == SelectType.ATTACK:
            return _choose_attack(obs, attacks)
        return _fallback(obs)
    except Exception:  # noqa: BLE001 — never crash on engine surprises
        return _fallback(obs)


class HeuristicAgent(Agent):
    name = "heuristic-v0"

    def __init__(self) -> None:
        self.cards = {c.cardId: c for c in all_card_data()}
        self.attacks = {a.attackId: a for a in all_attack()}

    def act(self, obs: Observation) -> list[int]:
        return choose(obs, self.cards, self.attacks)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_heuristic.py -v` — Expected: 6 PASS.

- [ ] **Step 5: Add the win-rate smoke test**

Append to `tests/test_heuristic.py`:

```python
def test_heuristic_beats_random_smoke():
    """20-game smoke: heuristic must clearly dominate random."""
    from pathlib import Path

    from ptcg.agents.heuristic import HeuristicAgent
    from ptcg.agents.random_agent import RandomAgent
    from ptcg.arena.runner import load_deck, run_series

    deck = load_deck(Path("tests/fixtures/sample_deck.csv"))
    s = run_series(HeuristicAgent(), RandomAgent(seed=3), deck, deck, n_games=20)
    assert s.win_rate_a >= 0.7, f"heuristic only won {s.wins_a}/20"
```

Run: `uv run pytest tests/test_heuristic.py -v` — Expected: 7 PASS. If the smoke fails, debug the heuristic (systematic-debugging) — do not lower the threshold.

- [ ] **Step 6: Commit**

```powershell
git add src/ptcg/agents/heuristic.py tests/test_heuristic.py
git commit -m "feat: heuristic baseline agent v0 with priority rules and safe fallbacks"
```

---

### Task 6: Card-pool analysis

**Files:**
- Create: `src/ptcg/decks/analysis.py`, `tests/test_analysis.py`

**Interfaces:**
- Consumes: `cg.api.all_card_data/all_attack` (engine truth for IDs/flags), `pokemon-tcg-ai-battle/EN_Card_Data.csv` (human-readable effect text).
- Produces: `pool_summary() -> PoolSummary` where `@dataclass PoolSummary` has `total_cards: int`, `pokemon: list[CardData]`, `trainers: list[CardData]`, `energies: list[CardData]`, `evolution_lines: dict[str, list[CardData]]` (basic name → line members), `attackers_ranked: list[tuple[CardData, Attack, float]]` (damage-per-energy descending); `print_report() -> str` (markdown report used by T8 to pick archetypes).

- [ ] **Step 1: Write failing tests**

`tests/test_analysis.py`:

```python
from ptcg.decks.analysis import pool_summary


def test_pool_summary_partitions_cards():
    s = pool_summary()
    assert s.total_cards == len(s.pokemon) + len(s.trainers) + len(s.energies)
    assert len(s.pokemon) > 20
    assert len(s.energies) >= 8


def test_evolution_lines_link_stages():
    s = pool_summary()
    assert len(s.evolution_lines) > 0
    for base_name, members in s.evolution_lines.items():
        assert any(c.basic for c in members), f"line {base_name} has no basic"


def test_attackers_ranked_by_efficiency():
    s = pool_summary()
    effs = [eff for _, _, eff in s.attackers_ranked]
    assert effs == sorted(effs, reverse=True)
    assert effs[0] > 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_analysis.py -v` — Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement `src/ptcg/decks/analysis.py`**

```python
"""Card-pool analysis over the engine's own card database.

The engine (all_card_data / all_attack) is the source of truth for what is
playable; EN_Card_Data.csv adds human-readable effect text for the report.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from cg.api import Attack, CardData, CardType, all_attack, all_card_data


@dataclass
class PoolSummary:
    total_cards: int
    pokemon: list[CardData] = field(default_factory=list)
    trainers: list[CardData] = field(default_factory=list)
    energies: list[CardData] = field(default_factory=list)
    evolution_lines: dict[str, list[CardData]] = field(default_factory=dict)
    attackers_ranked: list[tuple[CardData, Attack, float]] = field(default_factory=list)


def _evolution_lines(pokemon: list[CardData]) -> dict[str, list[CardData]]:
    """Group Pokémon into lines keyed by the basic stage's name."""
    by_name: dict[str, list[CardData]] = {}
    for c in pokemon:
        by_name.setdefault(c.name, []).append(c)

    def base_of(card: CardData, seen: frozenset[str] = frozenset()) -> str:
        if card.basic or not card.evolvesFrom or card.name in seen:
            return card.name
        parents = by_name.get(card.evolvesFrom)
        if not parents:
            return card.evolvesFrom
        return base_of(parents[0], seen | {card.name})

    lines: dict[str, list[CardData]] = {}
    for c in pokemon:
        lines.setdefault(base_of(c), []).append(c)
    return lines


@lru_cache(maxsize=1)
def _dbs() -> tuple[list[CardData], dict[int, Attack]]:
    return all_card_data(), {a.attackId: a for a in all_attack()}


def pool_summary() -> PoolSummary:
    cards, attacks = _dbs()
    pokemon = [c for c in cards if c.cardType == CardType.POKEMON]
    energies = [c for c in cards
                if c.cardType in (CardType.BASIC_ENERGY, CardType.SPECIAL_ENERGY)]
    trainers = [c for c in cards
                if c.cardType in (CardType.ITEM, CardType.TOOL,
                                  CardType.SUPPORTER, CardType.STADIUM)]
    attackers: list[tuple[CardData, Attack, float]] = []
    for c in pokemon:
        for attack_id in c.attacks:
            atk = attacks.get(attack_id)
            if atk and atk.damage > 0:
                cost = max(1, len(atk.energies))
                attackers.append((c, atk, atk.damage / cost))
    attackers.sort(key=lambda t: t[2], reverse=True)
    return PoolSummary(
        total_cards=len(cards), pokemon=pokemon, trainers=trainers,
        energies=energies, evolution_lines=_evolution_lines(pokemon),
        attackers_ranked=attackers,
    )


def print_report(top_n: int = 25) -> str:
    s = pool_summary()
    lines = [
        "# Card Pool Report",
        f"- Total cards: {s.total_cards} "
        f"(Pokémon {len(s.pokemon)}, Trainers {len(s.trainers)}, Energy {len(s.energies)})",
        f"- Evolution lines: {len(s.evolution_lines)}",
        "",
        "## Top attackers (damage per energy)",
        "| Pokémon | HP | Type | Attack | Dmg | Cost | Dmg/energy | ex/Mega |",
        "|---------|----|------|--------|-----|------|-----------|---------|",
    ]
    for c, a, eff in s.attackers_ranked[:top_n]:
        badge = "Mega" if c.megaEx else ("ex" if c.ex else "")
        lines.append(f"| {c.name} | {c.hp} | {c.energyType} | {a.name} "
                     f"| {a.damage} | {len(a.energies)} | {eff:.0f} | {badge} |")
    return "\n".join(lines)


if __name__ == "__main__":
    print(print_report())
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_analysis.py -v` — Expected: 3 PASS.
Also run: `uv run python -m ptcg.decks.analysis` and paste the top-10 rows of the report into your completion report (T8 needs it).

- [ ] **Step 5: Commit**

```powershell
git add src/ptcg/decks/analysis.py tests/test_analysis.py
git commit -m "feat: card-pool analysis with evolution lines and attacker efficiency"
```

---

### Task 7: Deck legality validator

**Files:**
- Create: `src/ptcg/decks/validate.py`, `tests/test_validate.py`

**Interfaces:**
- Consumes: `cg.api.all_card_data`.
- Produces: `validate_deck(deck: list[int]) -> list[str]` (empty list = legal; each string a violation). Rules: exactly 60 cards; all IDs exist in card DB; ≤4 copies per card *name* except Basic Energy; ≥1 Basic Pokémon; ≤1 ACE SPEC card total.

- [ ] **Step 1: Write failing tests**

`tests/test_validate.py`:

```python
from cg.api import CardType, all_card_data

from ptcg.decks.validate import validate_deck


def _db():
    cards = all_card_data()
    basic_pokemon = next(c for c in cards if c.cardType == CardType.POKEMON and c.basic)
    basic_energy = next(c for c in cards if c.cardType == CardType.BASIC_ENERGY)
    return basic_pokemon, basic_energy


def _legal_deck():
    pokemon, energy = _db()
    return [pokemon.cardId] * 4 + [energy.cardId] * 56


def test_legal_deck_passes():
    assert validate_deck(_legal_deck()) == []


def test_wrong_size_fails():
    assert any("60" in v for v in validate_deck(_legal_deck()[:59]))


def test_unknown_id_fails():
    deck = _legal_deck()
    deck[0] = 999999
    assert any("unknown" in v.lower() for v in validate_deck(deck))


def test_five_copies_fails():
    pokemon, energy = _db()
    deck = [pokemon.cardId] * 5 + [energy.cardId] * 55
    assert any("4" in v for v in validate_deck(deck))


def test_no_basic_pokemon_fails():
    _, energy = _db()
    deck = [energy.cardId] * 60
    assert any("basic" in v.lower() for v in validate_deck(deck))
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_validate.py -v` — Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement `src/ptcg/decks/validate.py`**

```python
"""Deck legality: engine card DB is the source of truth."""
from __future__ import annotations

from collections import Counter
from functools import lru_cache

from cg.api import CardData, CardType, all_card_data


@lru_cache(maxsize=1)
def _card_db() -> dict[int, CardData]:
    return {c.cardId: c for c in all_card_data()}


def validate_deck(deck: list[int]) -> list[str]:
    problems: list[str] = []
    db = _card_db()
    if len(deck) != 60:
        problems.append(f"deck must have exactly 60 cards, has {len(deck)}")
    unknown = sorted({cid for cid in deck if cid not in db})
    if unknown:
        problems.append(f"unknown card ids: {unknown}")
        return problems  # further checks need valid ids
    name_counts = Counter(
        db[cid].name for cid in deck if db[cid].cardType != CardType.BASIC_ENERGY
    )
    for name, count in sorted(name_counts.items()):
        if count > 4:
            problems.append(f"more than 4 copies of '{name}' ({count})")
    if not any(db[cid].cardType == CardType.POKEMON and db[cid].basic for cid in deck):
        problems.append("deck has no Basic Pokémon")
    ace_specs = sum(1 for cid in deck if db[cid].aceSpec)
    if ace_specs > 1:
        problems.append(f"more than 1 ACE SPEC card ({ace_specs})")
    return problems
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_validate.py -v` — Expected: 5 PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/ptcg/decks/validate.py tests/test_validate.py
git commit -m "feat: deck legality validator against engine card DB"
```

---

### Task 8: Candidate decks (judgment task)

**Files:**
- Create: `src/ptcg/decks/candidates/<archetype>.csv` × 2–3, `src/ptcg/decks/candidates/RATIONALE.md`

**Interfaces:**
- Consumes: `pool_summary()`/`print_report()` (T6), `validate_deck` (T7), `run_series` + `HeuristicAgent` (T4/T5).
- Produces: 2–3 deck CSV files (60 lines, one card ID per line — same format as `tests/fixtures/sample_deck.csv`) + `RATIONALE.md` documenting each deck's concept, key cards, and measured arena results.

This task requires real judgment — there is no verbatim deck list to copy. Procedure:

- [ ] **Step 1: Study the pool.** Run `uv run python -m ptcg.decks.analysis`. Identify the 3–5 strongest evolution lines by: attacker efficiency (damage/energy) of the line's top stage, HP, energy type concentration (mono-type decks are more consistent), and prize liability (ex/Mega give up 2–3 prizes — high HP must justify it). Also enumerate the trainer suite: list every SUPPORTER and ITEM with its skill text (`CardData.skills`) and flag draw/search effects — consistency trainers are usually the strongest cards in any deck.

- [ ] **Step 2: Build 2–3 decks with distinct concepts.** Skeleton per deck: 12–18 Pokémon (1 primary line ~4-3-3 or 4-4, optionally 1 thin secondary line or tech basics), 10–14 basic energy matching the primary attackers, remaining 28–38 slots trainers maxing the best draw supporters and search items (4 copies each of the top picks). Write each as a CSV of card IDs. Suggested distinct concepts (adjust to the actual pool): (a) fastest single-prize aggro line, (b) highest-HP ex/Mega line, (c) whichever line the trainer suite best supports.

- [ ] **Step 3: Validate.** For each deck: `validate_deck` returns `[]`, and a live smoke — `play_match(HeuristicAgent(), HeuristicAgent(), deck, deck)` completes without error (engine accepts the list).

- [ ] **Step 4: Measure.** For each candidate: `run_series(HeuristicAgent(), HeuristicAgent(), candidate, sample_deck, n_games=30)` (sample deck = `tests/fixtures/sample_deck.csv`). Every candidate should beat the near-vanilla sample deck. Then round-robin the candidates against each other, 30 games per pairing.

- [ ] **Step 5: Document.** Write `RATIONALE.md`: per deck — concept (2–3 sentences), key cards and why, the measured win-rates from Step 4 (with CIs, from `SeriesStats`). Append all series rows to `experiments/EXPERIMENTS.md`.

- [ ] **Step 6: Commit**

```powershell
git add src/ptcg/decks/candidates/
git commit -m "feat: candidate decks with pool-derived rationale and arena results"
```

---

### Task 9: Arena CLI + experiment logging

**Files:**
- Create: `scripts/run_arena.py`
- Modify: `experiments/EXPERIMENTS.md` (appended by runs)

**Interfaces:**
- Consumes: `run_series`, `load_deck`, `SeriesStats.markdown_row`, `RandomAgent`, `HeuristicAgent`.
- Produces: CLI `uv run python scripts/run_arena.py --agent-a heuristic --agent-b random --deck-a <path> --deck-b <path> --games 50 --notes "..."` that prints the stats and appends the markdown row to `experiments/EXPERIMENTS.md`.

- [ ] **Step 1: Implement `scripts/run_arena.py`**

```python
"""CLI: run an agent-vs-agent series and log it to experiments/EXPERIMENTS.md."""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptcg.agents.heuristic import HeuristicAgent  # noqa: E402
from ptcg.agents.random_agent import RandomAgent  # noqa: E402
from ptcg.arena.runner import load_deck, run_series  # noqa: E402

AGENTS = {
    "random": lambda: RandomAgent(seed=0),
    "heuristic": HeuristicAgent,
}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--agent-a", choices=AGENTS, required=True)
    p.add_argument("--agent-b", choices=AGENTS, required=True)
    p.add_argument("--deck-a", default="tests/fixtures/sample_deck.csv")
    p.add_argument("--deck-b", default="tests/fixtures/sample_deck.csv")
    p.add_argument("--games", type=int, default=50)
    p.add_argument("--notes", default="")
    args = p.parse_args()

    agent_a, agent_b = AGENTS[args.agent_a](), AGENTS[args.agent_b]()
    stats = run_series(agent_a, agent_b,
                       load_deck(args.deck_a), load_deck(args.deck_b), args.games)
    row = stats.markdown_row(
        dt.date.today().isoformat(), agent_a.name, agent_b.name,
        Path(args.deck_a).name, Path(args.deck_b).name, args.notes)
    log = ROOT / "experiments" / "EXPERIMENTS.md"
    log.write_text(log.read_text() + row + "\n")
    lo, hi = stats.ci_a
    print(f"{agent_a.name} vs {agent_b.name}: {stats.wins_a}-{stats.wins_b}-{stats.draws} "
          f"({stats.win_rate_a:.1%} [{lo:.1%}, {hi:.1%}]) "
          f"avg {sum(stats.game_seconds)/stats.n:.2f}s/game, "
          f"max move {stats.max_move_seconds:.3f}s")
    print(f"Logged to {log}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify manually**

Run: `uv run python scripts/run_arena.py --agent-a heuristic --agent-b random --games 10 --notes "CLI smoke"`
Expected: printed stats, one new row appended to `experiments/EXPERIMENTS.md` (check with `Get-Content experiments\EXPERIMENTS.md -Tail 2`).

- [ ] **Step 3: Commit**

```powershell
git add scripts/run_arena.py experiments/EXPERIMENTS.md
git commit -m "feat: arena CLI with experiment logging"
```

---

### Task 10: Acceptance runs (slow suite)

**Files:**
- Modify: `tests/test_heuristic.py` (add slow-marked acceptance test), `experiments/EXPERIMENTS.md`

**Interfaces:**
- Consumes: everything prior.

- [ ] **Step 1: Add the acceptance test**

Append to `tests/test_heuristic.py`:

```python
import pytest


@pytest.mark.slow
def test_heuristic_acceptance_200_games():
    """Spec bar: >=90% vs random over 200 games, zero errors."""
    from pathlib import Path

    from ptcg.agents.heuristic import HeuristicAgent
    from ptcg.agents.random_agent import RandomAgent
    from ptcg.arena.runner import load_deck, run_series

    deck = load_deck(Path("tests/fixtures/sample_deck.csv"))
    s = run_series(HeuristicAgent(), RandomAgent(seed=11), deck, deck, n_games=200)
    assert s.win_rate_a >= 0.9, f"win-rate {s.win_rate_a:.3f} below 0.9 bar"
```

- [ ] **Step 2: Run the acceptance suite**

Run: `uv run pytest -m slow -v` — Expected: PASS. Note total wall time.

- [ ] **Step 3: Crash-free 500-game run**

Run: `uv run python scripts/run_arena.py --agent-a heuristic --agent-b random --games 500 --notes "acceptance: 500-game crash-free + win-rate"`
Expected: completes with no `RuntimeError` (the runner raises on any errored game), win-rate ≥ 0.9. This also logs the headline Stage-1 baseline number for the writeup.

- [ ] **Step 4: Commit**

```powershell
git add tests/test_heuristic.py experiments/EXPERIMENTS.md
git commit -m "test: acceptance runs — heuristic >=90% vs random, 500-game crash-free"
```

---

### Task 11: Submission packaging

**Files:**
- Create: `src/ptcg/submission_main.py`, `scripts/package_submission.py`, `tests/test_packaging.py`

**Interfaces:**
- Consumes: `HeuristicAgent`, chosen deck from T8, vendored `src/cg/`.
- Produces: `submission.tar.gz` at repo root; `build_bundle(deck_path: Path, out_dir: Path) -> Path` and `verify_bundle(tar_path: Path) -> list[str]` (empty = OK) in `scripts/package_submission.py`.

- [ ] **Step 1: Write `src/ptcg/submission_main.py`** (this file becomes the bundle's top-level `main.py`)

```python
"""Kaggle submission entry point. Copied to the bundle root as main.py.

The Kaggle harness calls agent(obs_dict). First call (select is None) must
return the 60-card deck; afterwards, option indices.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cg.api import to_observation_class  # noqa: E402
from ptcg.agents.heuristic import HeuristicAgent  # noqa: E402

_agent = HeuristicAgent()


def read_deck_csv() -> list:
    path = "deck.csv"
    if not os.path.exists(path):
        path = "/kaggle_simulations/agent/deck.csv"
    with open(path) as f:
        return [int(line) for line in f.read().split("\n")[:60]]


def agent(obs_dict: dict) -> list:
    obs = to_observation_class(obs_dict)
    if obs.select is None:
        return read_deck_csv()
    return _agent.act(obs)
```

- [ ] **Step 2: Write failing packaging test**

`tests/test_packaging.py`:

```python
import tarfile
from pathlib import Path

from scripts.package_submission import build_bundle, verify_bundle


def test_bundle_structure(tmp_path):
    tar = build_bundle(Path("tests/fixtures/sample_deck.csv"), tmp_path)
    assert verify_bundle(tar) == []
    with tarfile.open(tar) as tf:
        names = tf.getnames()
    assert "main.py" in names
    assert "deck.csv" in names
    assert any(n.startswith("cg/") for n in names)
    assert any(n.startswith("ptcg/") for n in names)


def test_verify_catches_missing_main(tmp_path):
    import tarfile as tf_mod

    bad = tmp_path / "bad.tar.gz"
    with tf_mod.open(bad, "w:gz") as tf:
        deck = Path("tests/fixtures/sample_deck.csv")
        tf.add(deck, arcname="deck.csv")
    assert any("main.py" in p for p in verify_bundle(bad))
```

Add empty `scripts/__init__.py` so `scripts.package_submission` imports under pytest (pyproject already sets `pythonpath = ["src", "."]`).

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_packaging.py -v` — Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 4: Implement `scripts/package_submission.py`**

```python
"""Build and verify the Kaggle submission bundle.

Bundle layout (archive root): main.py, deck.csv, cg/, ptcg/.
Kaggle requires main.py at the TOP level and total size < 197.7 MiB.
"""
from __future__ import annotations

import shutil
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIZE_LIMIT = int(197.7 * 1024 * 1024)

# Only what the agent imports at runtime — keep the bundle lean.
PTCG_MODULES = [
    "src/ptcg/__init__.py",
    "src/ptcg/agents/__init__.py",
    "src/ptcg/agents/base.py",
    "src/ptcg/agents/heuristic.py",
]


def build_bundle(deck_path: Path, out_dir: Path) -> Path:
    staging = out_dir / "submission"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copy(ROOT / "src" / "ptcg" / "submission_main.py", staging / "main.py")
    shutil.copy(deck_path, staging / "deck.csv")
    shutil.copytree(ROOT / "src" / "cg", staging / "cg")
    for mod in PTCG_MODULES:
        src = ROOT / mod
        dst = staging / Path(mod).relative_to("src")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)
    tar_path = out_dir / "submission.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for item in sorted(staging.iterdir()):
            tf.add(item, arcname=item.name)
    return tar_path


def verify_bundle(tar_path: Path) -> list[str]:
    problems: list[str] = []
    size = tar_path.stat().st_size
    if size >= SIZE_LIMIT:
        problems.append(f"bundle {size} bytes exceeds limit {SIZE_LIMIT}")
    with tarfile.open(tar_path) as tf:
        names = tf.getnames()
    if "main.py" not in names:
        problems.append("main.py missing at archive top level")
    if "deck.csv" not in names:
        problems.append("deck.csv missing at archive top level")
    for lib in ("cg/libcg.so", "cg/cg.dll"):
        if lib not in names:
            problems.append(f"{lib} missing (Kaggle runs Linux; dev runs Windows)")
    if not any(n == "ptcg/agents/heuristic.py" for n in names):
        problems.append("ptcg agent modules missing")
    return problems


def main() -> None:
    deck = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tests/fixtures/sample_deck.csv"
    tar = build_bundle(deck, ROOT)
    problems = verify_bundle(tar)
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        raise SystemExit(1)
    print(f"OK: {tar} ({tar.stat().st_size / 1024 / 1024:.1f} MiB)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_packaging.py -v` — Expected: 2 PASS.

- [ ] **Step 6: Staging smoke — run a battle from inside the bundle layout**

```powershell
uv run python scripts/package_submission.py "src/ptcg/decks/candidates/<best-deck-from-T8>.csv"
cd submission
python -c "from main import agent, read_deck_csv; from cg.game import battle_start, battle_select, battle_finish; import json; d=read_deck_csv(); obs,_=battle_start(d,d); count=0
while obs['current']['result']==-1 and count<3000: obs=battle_select(agent(obs)); count+=1
print('winner', obs['current']['result'], 'moves', count); battle_finish()"
cd ..
```

Expected: prints a winner (0/1/2) — proves the bundle's `main.py` drives a full game using only bundle-local files. (Write this loop as a small temp script if the one-liner fights PowerShell quoting.)

- [ ] **Step 7: Commit**

```powershell
git add src/ptcg/submission_main.py scripts/package_submission.py scripts/__init__.py tests/test_packaging.py
git commit -m "feat: submission bundle builder with structure verification"
```

---

### Task 12: Final verification + docs

**Files:**
- Modify: `CLAUDE.md` (commands section), `experiments/EXPERIMENTS.md`

- [ ] **Step 1: Full suite**

Run: `uv run pytest -v` then `uv run pytest -m slow -v` — Expected: all PASS, zero skips other than intended.

- [ ] **Step 2: Rebuild the final bundle with the T8-chosen deck**

Run: `uv run python scripts/package_submission.py "src/ptcg/decks/candidates/<chosen>.csv"` — Expected: `OK: ... submission.tar.gz`.

- [ ] **Step 3: Update CLAUDE.md commands section**

Replace the placeholder "Commands" section content in `CLAUDE.md` with:

```markdown
## Commands

- `uv run pytest` — fast suite. `uv run pytest -m slow` — acceptance series (200-game win-rate).
- `uv run python scripts/vendor_sdk.py` — (re)copy the cg SDK into src/cg (required after fresh clone; src/cg is git-ignored licensed material).
- `uv run python scripts/run_arena.py --agent-a heuristic --agent-b random --games 50 --notes "..."` — run a series, auto-logged to experiments/EXPERIMENTS.md.
- `uv run python -m ptcg.decks.analysis` — card-pool report.
- `uv run python scripts/package_submission.py <deck.csv>` — build + verify submission.tar.gz.
```

- [ ] **Step 4: Commit**

```powershell
git add CLAUDE.md experiments/EXPERIMENTS.md
git commit -m "docs: dev commands + final slice-1 experiment log"
```

---

## Verification checklist (Slice 1 done)

1. `uv run pytest` and `uv run pytest -m slow` green.
2. EXPERIMENTS.md contains: heuristic-vs-random 200-game (≥90%) + 500-game crash-free rows, candidate-deck series.
3. 2–3 validated candidate decks + RATIONALE.md.
4. `submission.tar.gz` built with chosen deck, `verify_bundle` clean, staging smoke ran a full game.
5. Hand `submission.tar.gz` to Brad for manual Kaggle upload (My Submissions tab).
