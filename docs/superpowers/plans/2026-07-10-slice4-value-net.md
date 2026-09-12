# Slice 4: Learned Value Net Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hand-tuned search evaluator with a torch-trained, stdlib-exported value net; gate at ≥55% replicated vs heuristic-v0; swap the ladder submission only if the gate passes.

**Architecture:** A shared pure-stdlib feature extractor (`features.py`) feeds both a new trajectory logger (v0 self-play over the candidate deck pool → JSONL) and a pure-Python inference module (`value_net.py`) whose weights are trained offline by a torch script and exported as JSON with embedded golden vectors. The net plugs into the existing `Searcher` via its `evaluator` injection point — drop-in first (rollout terminal scorer), then a rollout-depth-0 A/B.

**Tech Stack:** Python 3.12 + uv; PyTorch (dev-only, never bundled); cg engine SDK (vendored); pytest.

**Spec:** `docs/superpowers/specs/2026-07-10-slice4-value-net-design.md`

## Global Constraints

- Repo: `C:\Users\Brad\Dev Folder\Product\ptcg-ai-battle-challenge-strategy` — path contains a space; ALWAYS quote it. Branch: `feature/slice4-value-net`.
- Every text-file write from Python uses an explicit `encoding="utf-8"` kwarg (`open`, `write_text`) — `tests/test_experiments_append.py` guards this at its own call depth.
- Bundle inference is pure stdlib: nothing under `src/ptcg/search/` or `src/ptcg/agents/` may import torch/numpy. Torch imports live ONLY in `scripts/train_value_net.py`.
- `uv run pytest` (never `--run`; never bare `pytest`). Slow suite is `uv run pytest -m slow` — do not run it unless the task says so.
- Plan code is reference, not gospel: before writing code, grep the touched file(s) for each plan-cited landmark (function names, line refs, enum members). If a landmark doesn't match, STOP and report `plan-drift`.
- Hand-verify any plan-authored test constant against the same task's own logic before transcribing (`.claude/rules/plan-test-arithmetic-sanity.md`).
- Stage by explicit path (`git add <files>`), never `git add .`. On `index.lock` contention, wait 2s and retry up to 3×.
- Card/attack facts must come from executable checks against the engine DB (`.claude/rules/verify-game-data-claims.md`); `all_attack()` is 0-based list of 1-based attackIds — always build `{a.attackId: a}` maps.
- Win-rate gates near their bar follow `.claude/rules/stochastic-gate-replication.md` — replicate, report ALL runs.

---

### Task 1: Fix `_cost_satisfied` RAINBOW over-count

**Files:**
- Modify: `src/ptcg/search/evaluate.py:58-72` (`_cost_satisfied`)
- Test: `tests/test_evaluate.py` (extend)

**Interfaces:**
- Produces: corrected `_cost_satisfied(cost: list, attached: list) -> bool` — consumed by `_best_usable_damage` (same file) and by Task 4's `features.py`.

Current bug (evaluate.py:66-72): each specific type independently adds the FULL rainbow pool to its own availability, so one RAINBOW pays every color at once; also `specific_total += n` at line 71 sits after `return False` and is dead code, so the final length check compares against 0 specifics.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_evaluate.py`; verify `EnergyType` member names against existing usage in that file first)

```python
class TestCostSatisfiedRainbow:
    def test_one_rainbow_cannot_pay_two_specific_colors(self):
        # v1 bug: RAINBOW added to FIGHTING availability AND WATER availability
        assert not _cost_satisfied(
            [EnergyType.FIGHTING, EnergyType.WATER],
            [EnergyType.RAINBOW, EnergyType.GRASS])

    def test_two_rainbows_pay_two_specific_colors(self):
        assert _cost_satisfied(
            [EnergyType.FIGHTING, EnergyType.WATER],
            [EnergyType.RAINBOW, EnergyType.RAINBOW])

    def test_rainbow_pays_one_specific_and_other_pays_colorless(self):
        assert _cost_satisfied(
            [EnergyType.FIGHTING, EnergyType.COLORLESS],
            [EnergyType.RAINBOW, EnergyType.GRASS])

    def test_specific_plus_rainbow_covers_double_requirement(self):
        assert _cost_satisfied(
            [EnergyType.FIGHTING, EnergyType.FIGHTING],
            [EnergyType.FIGHTING, EnergyType.RAINBOW])

    def test_colorless_only_paid_by_anything(self):
        assert _cost_satisfied(
            [EnergyType.COLORLESS, EnergyType.COLORLESS],
            [EnergyType.GRASS, EnergyType.WATER])

    def test_insufficient_total_count_fails(self):
        assert not _cost_satisfied([EnergyType.FIGHTING], [])
```

(Import `_cost_satisfied` and `EnergyType` the same way the existing tests in this file do.)

- [ ] **Step 2: Run to verify the first test FAILS** — `uv run pytest tests/test_evaluate.py -k Rainbow -v`. Expected: `test_one_rainbow_cannot_pay_two_specific_colors` FAILS (returns True on v1 code); the others may already pass.

- [ ] **Step 3: Replace `_cost_satisfied`** (evaluate.py:58-72) with:

```python
def _cost_satisfied(cost: list, attached: list) -> bool:
    """True if `attached` energy types can pay `cost`. COLORLESS slots accept any
    energy; each RAINBOW pays exactly ONE specific-type shortfall (shared budget,
    not per-type — the v1 bug double-counted it across types)."""
    if len(attached) < len(cost):
        return False
    have = Counter(attached)
    need = Counter(cost)
    rainbow_left = have.get(EnergyType.RAINBOW, 0)
    if EnergyType.RAINBOW in need:
        rainbow_left -= need[EnergyType.RAINBOW]
        if rainbow_left < 0:
            return False
    for etype, n in need.items():
        if etype in (EnergyType.COLORLESS, EnergyType.RAINBOW):
            continue
        short = n - have.get(etype, 0)
        if short > 0:
            rainbow_left -= short
            if rainbow_left < 0:
                return False
    # Typed requirements consume exactly sum(specific needs) distinct energies;
    # the top length check guarantees enough remain for the COLORLESS slots.
    return True
```

Update the module docstring line about `_cost_satisfied` if it describes the old behavior.

- [ ] **Step 4: Run the full fast suite** — `uv run pytest`. Expected: all pass (106 baseline + 6 new). If an existing `_cost_satisfied`/`_best_usable_damage` test now fails, inspect whether it encoded the buggy semantics — if so fix that test and note it in your report.

- [ ] **Step 5: Commit** — `git add src/ptcg/search/evaluate.py tests/test_evaluate.py && git commit -m "fix: _cost_satisfied shares one RAINBOW budget across specific types"`

---

### Task 2: Belief v2 — reserve the face-down active from the pool

**Files:**
- Modify: `src/ptcg/search/belief.py:87-113` (`BeliefState.sample`, opponent side)
- Test: `tests/test_belief.py` (extend)

**Interfaces:**
- Produces: `BeliefState.sample` unchanged signature; `Determinization.opponent_active` card no longer double-counted in `opponent_deck`/`opponent_hand`/`opponent_prize`.

v1 bug (belief.py:106-110): `opponent_active` is chosen from `pool + opponent_deck` AFTER deck/hand/prizes were dealt, without removing it — the same physical card can be predicted in two zones at once. Fix: reserve the active FIRST (the `need` computation at :93-94 already includes `+1` for it), pop it from the pool, THEN deal hand/prizes/deck. Zone sizes stay exactly `handCount`/`len(prize)`/`deckCount`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_belief.py`; mirror that file's existing State/PlayerState construction style — it builds these directly, not via `tests/fixtures/obs.py`, because it needs `active=[None]`)

```python
def test_facedown_active_prediction_not_double_counted():
    """Belief v2: the predicted face-down active must be POPPED from the pool,
    not duplicated into deck/hand/prize predictions."""
    BASIC = 901  # exactly one copy in my_deck; only basic in the prior
    my_deck = [BASIC] + [3] * 59  # 3 = filler energy, not a basic
    belief = BeliefState(my_deck, basic_ids=frozenset({BASIC}))
    # Opponent: face-down active (active=[None]), nothing else visible.
    # Build obs with opp deckCount/handCount/prizes so the whole prior is dealt out.
    obs = _obs_with_facedown_opponent_active(my_deck)  # see note below
    rng = random.Random(0)
    for _ in range(20):  # any single sample could dodge the bug by luck
        det = belief.sample(obs, rng)
        assert det.opponent_active, "expected an active prediction"
        total = (det.opponent_deck + det.opponent_hand
                 + det.opponent_prize + det.opponent_active).count(BASIC)
        assert total <= my_deck.count(BASIC), (
            f"basic {BASIC} predicted {total}x but prior holds "
            f"{my_deck.count(BASIC)}")
```

Write the `_obs_with_facedown_opponent_active` helper in the test file: opponent `PlayerState` with `active=[None]`, `deckCount=53`, `handCount=6`, `prize=[None]*6`, empty bench/discard, `hand=None`; own side may reuse the file's existing minimal construction. With 60 unseen cards and 53+6+6+1 = 66 > 60 slots needed, the pool is fully consumed plus filler — the v1 code then re-picks the basic from `opponent_deck`, tripping the assert.

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/test_belief.py -k double_counted -v`. Expected: FAIL with `total == 2` on at least one sample. If it passes 20/20, tighten the helper (e.g., `deckCount` high enough that the pool is fully dealt) until the v1 duplication reproduces — report if you cannot make it fail.

- [ ] **Step 3: Implement** — in `sample`, move the active-prediction block to run immediately after the pool padding (belief.py:95), BEFORE `opponent_hand`/`opponent_prize`/`opponent_deck` are dealt:

```python
        # belief v2: reserve the face-down active FIRST and pop it from the pool,
        # so one predicted card cannot occupy two zones. `need` already budgets +1.
        opponent_active: list[int] = []
        if opp.active and opp.active[0] is None:
            cand = next((c for c in pool if c in self.basic_ids), None)
            if cand is not None:
                pool.remove(cand)
            else:  # prior has no basic left: fall back without popping (mirror v1)
                cand = next((c for c in self.my_deck if c in self.basic_ids), filler)
            opponent_active = [cand]
```

Delete the old block at :106-110. Keep the turn-0 basic-seed block (:100-104) where it is (after `opponent_deck` exists). Update the module docstring header from "Belief v1" to note the v2 refinement.

- [ ] **Step 4: Run** — `uv run pytest tests/test_belief.py tests/test_searcher.py tests/test_search_agent.py -v` then the full `uv run pytest`. Expected: all green.

- [ ] **Step 5: Commit** — `git add src/ptcg/search/belief.py tests/test_belief.py && git commit -m "fix: belief v2 pops face-down active prediction from sampling pool"`

---

### Task 3: Intra-iteration deadline check in `Searcher`

**Files:**
- Modify: `src/ptcg/search/searcher.py` (`search`, `_simulate`, `_rollout`)
- Test: `tests/test_searcher.py` (extend)

**Interfaces:**
- Produces: `Searcher.search(obs, deadline)` unchanged signature; guarantees return "promptly" after `deadline` even mid-iteration (anytime early-break, no exceptions).

Today the deadline is only checked between iterations (searcher.py:92). One iteration can run `max_depth=40` engine steps plus a 12-ply rollout — a slow engine call sequence blows the move budget. Timeout = match loss on the ladder.

- [ ] **Step 1: Write the failing test** (append to `tests/test_searcher.py`; REUSE that file's existing fake backend/obs fixtures — grep it for its fake `SearchState`/backend pattern and mirror the construction; the sketch below shows intent, adapt the fakes to what exists)

```python
def test_deadline_respected_mid_iteration():
    """A single iteration must not overshoot the deadline by more than one step."""
    STEP_SLEEP = 0.05

    class SlowBackend:  # adapt to the file's existing fake-backend shape
        def __init__(self, inner):
            self.inner = inner
        def begin(self, obs, det):
            return self.inner.begin(obs, det)
        def step(self, sid, sel):
            time.sleep(STEP_SLEEP)
            return self.inner.step(sid, sel)
        def end(self):
            self.inner.end()

    searcher = <construct as existing tests do, wrapping the fake backend in
                SlowBackend, with SearchConfig(rollout_depth=12, max_depth=40,
                max_iterations=50)>
    t0 = time.perf_counter()
    searcher.search(obs, deadline=t0 + 0.12)
    elapsed = time.perf_counter() - t0
    # without intra-iteration checks one iteration alone runs 40+ sleeping steps
    # (>= 2.0s); with them we stop within ~2 steps of the deadline.
    assert elapsed < 1.0, f"search overshot deadline: {elapsed:.2f}s"
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/test_searcher.py -k mid_iteration -v`. Expected: FAIL (elapsed ≈ 2s+). If the existing fakes finish games too quickly to sustain 40 steps, make the fake non-terminal for enough steps.

- [ ] **Step 3: Implement.** In `search()` store `self._deadline = deadline` before the loop. In `_simulate` add as the FIRST line inside the `for` loop (searcher.py:177): `if time.perf_counter() >= self._deadline: break` — a break leaves `value is None`, and the existing :206-207 fallback statically evaluates the current observation. In `_rollout` add the same check as the first line inside its `for` loop (:153): `if time.perf_counter() >= self._deadline: break` — the loop exit evaluates `last`, preserving anytime semantics. Initialize `self._deadline = float("inf")` in `__init__` so direct `_rollout`/`_simulate` unit tests (if any construct without `search()`) keep working.

- [ ] **Step 4: Run** — `uv run pytest tests/test_searcher.py -v`, then full `uv run pytest`. Expected: green.

- [ ] **Step 5: Commit** — `git add src/ptcg/search/searcher.py tests/test_searcher.py && git commit -m "feat: intra-iteration deadline check in searcher (ladder timeout safety)"`

---

### Task 4: Shared feature extractor `features.py`

**Files:**
- Create: `src/ptcg/search/features.py`
- Test: `tests/test_features.py` (new)

**Interfaces:**
- Consumes: `_dbs`, `_best_usable_damage`, `_board`, `_active` from `ptcg.search.evaluate` (Task 1 fixed semantics).
- Produces: `FEATURE_VERSION: int = 1`; `FEATURE_NAMES: list[str]` (length 40); `extract(state: State, my_index: int, dbs: tuple[dict, dict] | None = None) -> list[float]` returning exactly `len(FEATURE_NAMES)` floats. Consumed by Tasks 5, 7, 8.

Pure stdlib + `cg.api` + `ptcg.search.evaluate` imports ONLY (bundle-safe).

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_features.py"""
import pytest

from ptcg.search.features import FEATURE_NAMES, FEATURE_VERSION, extract
from tests.fixtures.obs import make_pokemon, make_player, make_state

EMPTY_DBS = ({}, {})


def _named(vec):
    return dict(zip(FEATURE_NAMES, vec))


def test_length_and_version_pinned():
    assert FEATURE_VERSION == 1
    assert len(FEATURE_NAMES) == 40
    vec = extract(make_state(), 0, dbs=EMPTY_DBS)
    assert len(vec) == len(FEATURE_NAMES)
    assert all(isinstance(v, float) for v in vec)


def test_default_state_hand_computed_values():
    # make_state(): both sides one 70/70 active, no energies, empty bench,
    # deckCount=40, 6 prizes, handCount=0, turn=3, yourIndex=0.
    f = _named(extract(make_state(), 0, dbs=EMPTY_DBS))
    assert f["i_move"] == 1.0
    assert f["turn"] == pytest.approx(3 / 30)
    assert f["energy_attached"] == 0.0
    assert f["prize_diff"] == 0.0
    assert f["me_prizes"] == 1.0
    assert f["me_active_present"] == 1.0
    assert f["me_active_hp_frac"] == 1.0
    assert f["me_active_maxhp"] == pytest.approx(70 / 340)
    assert f["me_active_dmg_taken"] == 0.0
    assert f["me_active_best_dmg"] == 0.0   # empty DB -> no usable attack
    assert f["me_bench_count"] == 0.0
    assert f["me_deck"] == pytest.approx(40 / 60)
    assert f["opp_hand"] == 0.0
    assert f["me_can_ko_opp"] == 0.0


def test_perspective_flip_swaps_sides():
    you = make_player(make_pokemon(hp=35, max_hp=70))
    opp = make_player(make_pokemon(hp=70, max_hp=70))
    st = make_state(you=you, opp=opp, your_index=0)
    mine = _named(extract(st, 0, dbs=EMPTY_DBS))
    theirs = _named(extract(st, 1, dbs=EMPTY_DBS))
    assert mine["me_active_hp_frac"] == pytest.approx(0.5)
    assert theirs["opp_active_hp_frac"] == pytest.approx(0.5)
    assert theirs["me_active_hp_frac"] == pytest.approx(1.0)
    assert mine["i_move"] == 1.0 and theirs["i_move"] == 0.0
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_features.py -v`. Expected: ImportError (module missing).

- [ ] **Step 3: Implement `src/ptcg/search/features.py`**

```python
"""Fixed-length feature vector for the value net.

SINGLE source of truth for both training-data generation and bundle inference —
train/serve skew is structurally impossible. Pure stdlib + cg.api only.
"""
from __future__ import annotations

from cg.api import PlayerState, Pokemon, State

from ptcg.search.evaluate import _active, _best_usable_damage, _board, _dbs

FEATURE_VERSION = 1

_SIDE_NAMES = [
    "prizes", "active_present", "active_hp_frac", "active_maxhp",
    "active_dmg_taken", "active_energy", "active_best_dmg",
    "bench_count", "bench_energy", "bench_hp_frac_mean", "bench_dmg_taken",
    "hand", "deck", "discard", "poisoned", "hard_status",
]
FEATURE_NAMES = (
    ["i_move", "turn", "energy_attached", "prize_diff"]
    + [f"me_{n}" for n in _SIDE_NAMES]
    + [f"opp_{n}" for n in _SIDE_NAMES]
    + ["me_can_ko_opp", "opp_can_ko_me", "me_decked", "opp_decked"]
)

_HP_SCALE = 340.0  # max printed HP in the card pool, rounded up


def _cap(x: float, hi: float) -> float:
    return min(x, hi) / hi


def _side(p: PlayerState, best_dmg: int) -> list[float]:
    active = _active(p)
    bench = [pk for pk in p.bench if pk is not None]
    bench_hp = (sum(pk.hp / max(pk.maxHp, 1) for pk in bench) / len(bench)
                if bench else 0.0)
    return [
        len(p.prize) / 6.0,
        1.0 if active is not None else 0.0,
        (active.hp / max(active.maxHp, 1)) if active is not None else 0.0,
        (active.maxHp / _HP_SCALE) if active is not None else 0.0,
        ((active.maxHp - active.hp) / _HP_SCALE) if active is not None else 0.0,
        _cap(float(len(active.energies)), 6.0) if active is not None else 0.0,
        _cap(float(best_dmg), 300.0),
        len(bench) / 5.0,
        _cap(float(sum(len(pk.energies) for pk in bench)), 10.0),
        bench_hp,
        _cap(float(sum(pk.maxHp - pk.hp for pk in bench)), 600.0),
        _cap(float(p.handCount), 12.0),
        p.deckCount / 60.0,
        _cap(float(len(p.discard)), 60.0),
        1.0 if p.poisoned else 0.0,
        1.0 if (p.asleep or p.paralyzed or p.confused) else 0.0,
    ]


def extract(state: State, my_index: int,
            dbs: tuple[dict, dict] | None = None) -> list[float]:
    """Features of `state` from `my_index`'s perspective, len == FEATURE_NAMES."""
    cards, attacks = dbs if dbs is not None else _dbs()
    me, opp = state.players[my_index], state.players[1 - my_index]
    my_best = _best_usable_damage(_active(me), cards, attacks)
    opp_best = _best_usable_damage(_active(opp), cards, attacks)
    my_active, opp_active = _active(me), _active(opp)
    vec = [
        1.0 if state.yourIndex == my_index else 0.0,
        _cap(float(state.turn), 30.0),
        1.0 if state.energyAttached else 0.0,
        (len(opp.prize) - len(me.prize)) / 6.0,
    ]
    vec += _side(me, my_best)
    vec += _side(opp, opp_best)
    vec += [
        1.0 if (opp_active is not None and opp_active.hp > 0
                and my_best >= opp_active.hp) else 0.0,
        1.0 if (my_active is not None and my_active.hp > 0
                and opp_best >= my_active.hp) else 0.0,
        1.0 if me.deckCount == 0 else 0.0,
        1.0 if opp.deckCount == 0 else 0.0,
    ]
    return vec
```

Sanity: 4 + 16 + 16 + 4 = 40 = `len(FEATURE_NAMES)`. Hand-verify the Step-1 constants against this code before running (e.g., `turn` = min(3,30)/30 = 0.1).

- [ ] **Step 4: Run** — `uv run pytest tests/test_features.py -v` then full `uv run pytest`. Expected: green.

- [ ] **Step 5: Commit** — `git add src/ptcg/search/features.py tests/test_features.py && git commit -m "feat: shared pure-stdlib feature extractor for the value net"`

---

### Task 5: Trajectory recording

**Files:**
- Create: `src/ptcg/train/__init__.py` (empty), `src/ptcg/train/trajectory.py`
- Test: `tests/test_trajectory.py` (new)

**Interfaces:**
- Consumes: `extract`, `FEATURE_VERSION` (Task 4); `evaluate` (hand-tuned, for the offline baseline column); `Agent` protocol (`ptcg.agents.base`); `MatchResult.winner` convention (0/1 seat, 2 = draw).
- Produces: `RecordingAgent(inner: Agent, sink: list)`; `label_records(sink, winner) -> list[dict]`; `append_jsonl(records, path) -> None`. Consumed by Task 6.

- [ ] **Step 1: Write the failing tests**

```python
"""tests/test_trajectory.py"""
import json

from ptcg.agents.heuristic import HeuristicAgent
from ptcg.arena.runner import load_deck, play_match
from ptcg.search.features import FEATURE_NAMES, FEATURE_VERSION
from ptcg.train.trajectory import RecordingAgent, append_jsonl, label_records


def test_real_game_produces_labeled_records(tmp_path):
    deck = load_deck("tests/fixtures/sample_deck.csv")
    sink: list = []
    a0 = RecordingAgent(HeuristicAgent(), sink)
    a1 = RecordingAgent(HeuristicAgent(), sink)
    result = play_match(a0, a1, deck, deck)
    assert result.error is None
    assert len(sink) > 10  # a real game has dozens of decisions
    records = label_records(sink, result.winner, game_id=7)
    assert {r["s"] for r in records} == {0, 1}
    for r in records:
        assert r["v"] == FEATURE_VERSION and r["g"] == 7
        assert len(r["x"]) == len(FEATURE_NAMES)
        assert 0.0 <= r["hte"] <= 1.0
        if result.winner in (0, 1):
            assert r["y"] == (1.0 if r["s"] == result.winner else 0.0)
        else:
            assert r["y"] == 0.5
    out = tmp_path / "traj.jsonl"
    append_jsonl(records, out)
    append_jsonl(records[:3], out)  # append mode
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == len(records) + 3
    assert json.loads(lines[0])["g"] == 7
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_trajectory.py -v`. Expected: ImportError.

- [ ] **Step 3: Implement `src/ptcg/train/trajectory.py`**

```python
"""Per-decision trajectory recording for value-net training (dev-only, never bundled)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from cg.api import Observation

from ptcg.agents.base import Agent
from ptcg.search.evaluate import evaluate
from ptcg.search.features import FEATURE_VERSION, extract


@dataclass
class DecisionRecord:
    seat: int
    features: list[float]
    hte: float  # hand-tuned evaluator's score, the offline baseline column


class RecordingAgent(Agent):
    """Wraps any Agent; records features from the mover's perspective per decision."""

    def __init__(self, inner: Agent, sink: list[DecisionRecord]) -> None:
        self.inner = inner
        self.sink = sink
        self.name = f"rec({inner.name})"

    def act(self, obs: Observation) -> list[int]:
        st = obs.current
        if st is not None and st.result == -1:
            seat = st.yourIndex
            self.sink.append(DecisionRecord(
                seat, extract(st, seat), evaluate(st, seat)))
        return self.inner.act(obs)


def label_records(sink: list[DecisionRecord], winner: int,
                  game_id: int) -> list[dict]:
    """winner: 0/1 seat index, 2 = draw (MatchResult convention)."""
    out = []
    for rec in sink:
        if winner in (0, 1):
            y = 1.0 if rec.seat == winner else 0.0
        else:
            y = 0.5
        out.append({"v": FEATURE_VERSION, "g": game_id, "s": rec.seat, "y": y,
                    "hte": round(rec.hte, 4),
                    "x": [round(v, 4) for v in rec.features]})
    return out


def append_jsonl(records: list[dict], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
```

Check `Agent` in `src/ptcg/agents/base.py` first: if `name` is a class attribute or abstract property, adapt the instance-attribute assignment accordingly (landmark verification).

- [ ] **Step 4: Run** — `uv run pytest tests/test_trajectory.py -v` then full `uv run pytest`. Expected: green.

- [ ] **Step 5: Commit** — `git add src/ptcg/train/__init__.py src/ptcg/train/trajectory.py tests/test_trajectory.py && git commit -m "feat: trajectory recording for value-net training data"`

---

### Task 6: Data-generation script

**Files:**
- Create: `scripts/generate_training_data.py`
- Modify: `.gitignore` (add `experiments/data/`)
- Test: `tests/test_trajectory.py` (extend with pairing-enumeration test)

**Interfaces:**
- Consumes: Task 5's `RecordingAgent`/`label_records`/`append_jsonl`; `play_match`, `load_deck` (runner); candidate decks at `src/ptcg/decks/candidates/*.csv` (9 files).
- Produces: CLI `uv run python scripts/generate_training_data.py [--measure] --games-per-pairing N --out PATH --seed S`; `deck_pairings(deck_dir) -> list[tuple[Path, Path]]` (45 pairings incl. mirrors). Consumed by Task 9 (execution).

- [ ] **Step 1: Write the failing test** (append to `tests/test_trajectory.py`)

```python
def test_deck_pairings_round_robin_with_mirrors():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path("scripts").resolve().parent))
    from scripts.generate_training_data import deck_pairings
    pairs = deck_pairings(Path("src/ptcg/decks/candidates"))
    n = len(list(Path("src/ptcg/decks/candidates").glob("*.csv")))
    assert n == 9
    assert len(pairs) == n * (n + 1) // 2  # 45: unordered pairs + mirrors
    assert all(a.suffix == ".csv" and b.suffix == ".csv" for a, b in pairs)
```

(Match the import mechanics used by `tests/test_packaging.py` for importing from `scripts/` — grep it first and mirror; adjust the sys.path lines if that file does it differently.)

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_trajectory.py -k pairings -v`. Expected: ImportError.

- [ ] **Step 3: Implement `scripts/generate_training_data.py`**

```python
"""Generate value-net training data: v0 self-play over the candidate deck pool."""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptcg.agents.heuristic import HeuristicAgent  # noqa: E402
from ptcg.arena.runner import load_deck, play_match  # noqa: E402
from ptcg.train.trajectory import (RecordingAgent, append_jsonl,  # noqa: E402
                                   label_records)

DECK_DIR = ROOT / "src" / "ptcg" / "decks" / "candidates"


def deck_pairings(deck_dir: Path) -> list[tuple[Path, Path]]:
    decks = sorted(deck_dir.glob("*.csv"))
    return [(a, b) for i, a in enumerate(decks) for b in decks[i:]]


def run(games_per_pairing: int, out: Path, seed: int,
        log_every: int = 200) -> None:
    pairings = deck_pairings(DECK_DIR)
    rng = random.Random(seed)  # reserved for future sampling knobs; order is fixed
    game_id = 0
    errors = 0
    t0 = time.perf_counter()
    for deck_a_path, deck_b_path in pairings:
        deck_a, deck_b = load_deck(deck_a_path), load_deck(deck_b_path)
        for g in range(games_per_pairing):
            sink: list = []
            a0 = RecordingAgent(HeuristicAgent(), sink)
            a1 = RecordingAgent(HeuristicAgent(), sink)
            # alternate seats like run_series does
            if g % 2 == 0:
                result = play_match(a0, a1, deck_a, deck_b)
            else:
                result = play_match(a0, a1, deck_b, deck_a)
            if result.error is not None:
                errors += 1
                continue
            append_jsonl(label_records(sink, result.winner, game_id), out)
            game_id += 1
            if game_id % log_every == 0:
                rate = game_id / (time.perf_counter() - t0)
                print(f"{game_id} games ({rate:.1f}/s), {errors} errors", flush=True)
    print(f"DONE: {game_id} games, {errors} errors, "
          f"{time.perf_counter() - t0:.0f}s -> {out}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--games-per-pairing", type=int, default=300)
    p.add_argument("--out", default="experiments/data/slice4/train_v1.jsonl")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--measure", action="store_true",
                   help="run 20 games on one pairing, print s/game estimate, exit")
    args = p.parse_args()
    if args.measure:
        out = ROOT / "experiments" / "data" / "slice4" / "_measure.jsonl"
        deck = load_deck(deck_pairings(DECK_DIR)[0][0])
        t0 = time.perf_counter()
        for gid in range(20):
            sink: list = []
            r = play_match(RecordingAgent(HeuristicAgent(), sink),
                           RecordingAgent(HeuristicAgent(), sink), deck, deck)
            if r.error is None:
                append_jsonl(label_records(sink, r.winner, gid), out)
        per = (time.perf_counter() - t0) / 20
        total = per * 45 * args.games_per_pairing
        print(f"{per:.2f}s/game -> est {total / 60:.0f} min "
              f"for 45x{args.games_per_pairing} games")
        return
    run(args.games_per_pairing, ROOT / args.out, args.seed)


if __name__ == "__main__":
    main()
```

Note the seat-alternation nuance: both orderings pass `(a0, a1)` — the DECKS swap, not the agents, so `label_records` seats stay correct while deck-vs-seat bias averages out.

- [ ] **Step 4: Add `experiments/data/` to `.gitignore`** (one line, keep existing entries).

- [ ] **Step 5: Run** — `uv run pytest tests/test_trajectory.py -v` (pairing test green), then smoke the CLI: `uv run python scripts/generate_training_data.py --measure`. Expected: prints an s/game estimate; `experiments/data/slice4/_measure.jsonl` exists and is NOT in `git status`.

- [ ] **Step 6: Commit** — `git add scripts/generate_training_data.py tests/test_trajectory.py .gitignore && git commit -m "feat: self-play training-data generator over candidate deck pool"`

---

### Task 7: Pure-Python inference — `value_net.py` + `ValueNetEvaluator`

**Files:**
- Create: `src/ptcg/search/value_net.py`
- Test: `tests/test_value_net.py` (new)

**Interfaces:**
- Consumes: `extract`, `FEATURE_VERSION`, `FEATURE_NAMES` (Task 4).
- Produces: `ValueNet(spec: dict)` with `.predict(x: list[float]) -> float`; `ValueNetEvaluator` callable `(state, my_index) -> float` matching the `Searcher.evaluator` contract; `ValueNetEvaluator.load_default()` reading `src/ptcg/search/value_net_weights.json`; module-level `DEFAULT_WEIGHTS_PATH`. Consumed by Tasks 8, 10, 12. Weights-spec schema (Task 8 must export exactly this): `{"feature_version": int, "feature_names": [str], "layers": [{"w": [[float]], "b": [float]}], "golden": [{"x": [float], "y": float}], "meta": {...}}` — hidden layers ReLU, final layer sigmoid.

- [ ] **Step 1: Write the failing tests** (hand-verify every constant: sigmoid(1.5) = 1/(1+e^-1.5) ≈ 0.8175744762; two-layer example: affine([3,1]) with w=[[1,0],[0,1]], b=[0,-2] → [3,-1] → relu [3,0] → w=[[1,1]], b=[0] → z=3 → sigmoid(3) ≈ 0.9525741268)

```python
"""tests/test_value_net.py"""
import json
from pathlib import Path

import pytest

from ptcg.search.features import FEATURE_NAMES, FEATURE_VERSION, extract
from ptcg.search.value_net import (DEFAULT_WEIGHTS_PATH, ValueNet,
                                   ValueNetEvaluator)
from tests.fixtures.obs import make_state


def _spec(layers):
    return {"feature_version": FEATURE_VERSION, "feature_names": FEATURE_NAMES,
            "layers": layers, "golden": [], "meta": {}}


def test_single_layer_hand_computed():
    net = ValueNet(_spec([{"w": [[1.0, -1.0]], "b": [0.5]}]))
    assert net.predict([2.0, 1.0]) == pytest.approx(0.8175744762, abs=1e-9)


def test_two_layer_relu_hand_computed():
    net = ValueNet(_spec([
        {"w": [[1.0, 0.0], [0.0, 1.0]], "b": [0.0, -2.0]},
        {"w": [[1.0, 1.0]], "b": [0.0]},
    ]))
    assert net.predict([3.0, 1.0]) == pytest.approx(0.9525741268, abs=1e-9)


def test_evaluator_terminal_shortcuts_and_range():
    tiny = ValueNet(_spec([{"w": [[0.01] * len(FEATURE_NAMES)], "b": [0.0]}]))
    ev = ValueNetEvaluator(tiny)
    st = make_state()
    assert 0.0 <= ev(st, 0) <= 1.0
    st_win = make_state();  st_win.result = 0
    st_loss = make_state(); st_loss.result = 1
    st_draw = make_state(); st_draw.result = 2
    assert ev(st_win, 0) == 1.0
    assert ev(st_loss, 0) == 0.0
    assert ev(st_draw, 0) == 0.5


def test_feature_version_mismatch_rejected():
    spec = _spec([{"w": [[0.0, 0.0]], "b": [0.0]}])
    spec["feature_version"] = 999
    with pytest.raises(ValueError, match="feature_version"):
        ValueNet(spec)


def test_shipped_weights_golden_parity():
    """Torch-exported goldens must reproduce through the pure-Python path."""
    if not Path(DEFAULT_WEIGHTS_PATH).exists():
        pytest.skip("no production weights artifact yet (created in Task 9)")
    spec = json.loads(Path(DEFAULT_WEIGHTS_PATH).read_text(encoding="utf-8"))
    assert spec["golden"], "export must embed golden vectors"
    net = ValueNet(spec)
    for g in spec["golden"]:
        assert net.predict(g["x"]) == pytest.approx(g["y"], abs=1e-6)
```

(If `State` is a frozen dataclass and `st.result = 0` fails, build terminal states via `dataclasses.replace` or by passing `result` through a fixture change — check `make_state` first and adapt; report the adaptation.)

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_value_net.py -v`. Expected: ImportError.

- [ ] **Step 3: Implement `src/ptcg/search/value_net.py`**

```python
"""Pure-stdlib value-net inference. Weights are torch-trained and JSON-exported;
this module must stay importable inside the Kaggle bundle (no torch, no numpy)."""
from __future__ import annotations

import json
import math
from pathlib import Path

from ptcg.search.features import FEATURE_VERSION, extract

DEFAULT_WEIGHTS_PATH = Path(__file__).with_name("value_net_weights.json")


def _affine(x: list[float], w: list[list[float]], b: list[float]) -> list[float]:
    return [sum(wi * xi for wi, xi in zip(row, x)) + bi
            for row, bi in zip(w, b)]


class ValueNet:
    def __init__(self, spec: dict) -> None:
        if spec.get("feature_version") != FEATURE_VERSION:
            raise ValueError(
                f"feature_version {spec.get('feature_version')!r} != "
                f"extractor version {FEATURE_VERSION}")
        self.layers = spec["layers"]

    def predict(self, x: list[float]) -> float:
        h = x
        for layer in self.layers[:-1]:
            h = [v if v > 0.0 else 0.0 for v in _affine(h, layer["w"], layer["b"])]
        z = _affine(h, self.layers[-1]["w"], self.layers[-1]["b"])[0]
        z = max(-60.0, min(60.0, z))
        return 1.0 / (1.0 + math.exp(-z))


class ValueNetEvaluator:
    """Drop-in for ptcg.search.evaluate.evaluate (Searcher's evaluator contract)."""

    def __init__(self, net: ValueNet, dbs: tuple[dict, dict] | None = None) -> None:
        self.net = net
        self.dbs = dbs

    @classmethod
    def load_default(cls) -> "ValueNetEvaluator":
        spec = json.loads(DEFAULT_WEIGHTS_PATH.read_text(encoding="utf-8"))
        return cls(ValueNet(spec))

    def __call__(self, state, my_index: int) -> float:
        if state.result == my_index:
            return 1.0
        if state.result == 1 - my_index:
            return 0.0
        if state.result != -1:
            return 0.5
        return self.net.predict(extract(state, my_index, dbs=self.dbs))
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_value_net.py -v` (golden test SKIPS — expected until Task 9), then full `uv run pytest`. Expected: green.

- [ ] **Step 5: Commit** — `git add src/ptcg/search/value_net.py tests/test_value_net.py && git commit -m "feat: pure-stdlib value-net inference and evaluator"`

---

### Task 8: Torch training + export script

**Files:**
- Create: `scripts/train_value_net.py`
- Modify: `pyproject.toml` via `uv add --dev torch` (never edit deps by hand)
- Test: `tests/test_train_export.py` (new)

**Interfaces:**
- Consumes: JSONL record schema (Task 5); weights-spec schema (Task 7 — export EXACTLY that shape); `ValueNet` for the in-script parity check.
- Produces: CLI `uv run python scripts/train_value_net.py --data PATH [...] --out PATH` that trains, evaluates net-vs-hand-tuned on the by-game val split, embeds golden vectors, asserts torch↔pure-Python parity before writing, and prints a metrics block. Consumed by Task 9.

- [ ] **Step 1: `uv add --dev torch`** — then `uv run python -c "import torch; print(torch.__version__)"`. Expected: a version prints (CPU build). If the install fails, STOP and report (do not pip-install around uv).

- [ ] **Step 2: Write the failing test**

```python
"""tests/test_train_export.py — end-to-end tiny training on synthetic data."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ptcg.search.features import FEATURE_NAMES, FEATURE_VERSION
from ptcg.search.value_net import ValueNet


def _synthetic_dataset(path: Path, n_games: int = 60) -> None:
    """Learnable toy signal: y depends on feature 0 (i_move)... any separable rule."""
    import random
    rng = random.Random(0)
    with open(path, "w", encoding="utf-8") as f:
        for g in range(n_games):
            y = float(g % 2)
            for _ in range(10):
                x = [rng.random() for _ in FEATURE_NAMES]
                x[0] = y  # make the label trivially learnable
                f.write(json.dumps({"v": FEATURE_VERSION, "g": g, "s": 0,
                                    "y": y, "hte": 0.5, "x": x}) + "\n")


def test_train_export_roundtrip(tmp_path):
    data = tmp_path / "toy.jsonl"
    _synthetic_dataset(data)
    out = tmp_path / "weights.json"
    proc = subprocess.run(
        [sys.executable, "scripts/train_value_net.py", "--data", str(data),
         "--out", str(out), "--epochs", "40", "--hidden", "8", "--seed", "0",
         "--batch", "64", "--lr", "0.05"],  # small batches + high lr: the toy
         # rule (x[0]==y) must actually converge in seconds, not just run
        capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr
    assert "PARITY OK" in proc.stdout
    spec = json.loads(out.read_text(encoding="utf-8"))
    assert spec["feature_version"] == FEATURE_VERSION
    assert spec["feature_names"] == FEATURE_NAMES
    assert len(spec["golden"]) >= 3
    net = ValueNet(spec)
    for g in spec["golden"]:
        assert net.predict(g["x"]) == pytest.approx(g["y"], abs=1e-6)
    # the toy rule is learnable: val accuracy reported and high
    assert "val_acc" in spec["meta"] and spec["meta"]["val_acc"] > 0.9
```

- [ ] **Step 3: Run to verify failure** — `uv run pytest tests/test_train_export.py -v`. Expected: FileNotFoundError / nonzero returncode (script missing).

- [ ] **Step 4: Implement `scripts/train_value_net.py`**

```python
"""Train the value MLP (torch, dev-only) and export stdlib-JSON weights + goldens."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptcg.search.features import FEATURE_NAMES, FEATURE_VERSION  # noqa: E402
from ptcg.search.value_net import ValueNet  # noqa: E402


def load_jsonl(path: Path):
    xs, ys, htes, games = [], [], [], []
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["v"] != FEATURE_VERSION:
                raise SystemExit(f"record feature_version {r['v']} != {FEATURE_VERSION}")
            xs.append(r["x"]); ys.append(r["y"]); htes.append(r["hte"]); games.append(r["g"])
    return (torch.tensor(xs, dtype=torch.float32),
            torch.tensor(ys, dtype=torch.float32),
            torch.tensor(htes, dtype=torch.float32),
            torch.tensor(games, dtype=torch.int64))


def auc(scores: torch.Tensor, labels: torch.Tensor) -> float:
    """Rank-based AUC over win/loss labels (draws 0.5 excluded)."""
    mask = labels != 0.5
    s, y = scores[mask], labels[mask]
    pos, neg = s[y == 1.0], s[y == 0.0]
    if len(pos) == 0 or len(neg) == 0:
        return 0.5
    greater = (pos.unsqueeze(1) > neg.unsqueeze(0)).float().sum()
    ties = (pos.unsqueeze(1) == neg.unsqueeze(0)).float().sum()
    return float((greater + 0.5 * ties) / (len(pos) * len(neg)))


def metrics(scores: torch.Tensor, labels: torch.Tensor) -> dict:
    eps = 1e-7
    p = scores.clamp(eps, 1 - eps)
    bce = float(-(labels * p.log() + (1 - labels) * (1 - p).log()).mean())
    mask = labels != 0.5
    acc = float(((p[mask] > 0.5) == (labels[mask] == 1.0)).float().mean()) if mask.any() else 0.0
    return {"bce": round(bce, 4), "acc": round(acc, 4),
            "auc": round(auc(scores, labels), 4)}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--hidden", type=int, default=64)
    p.add_argument("--batch", type=int, default=4096)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--patience", type=int, default=5)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()
    torch.manual_seed(args.seed)

    X, y, hte, g = load_jsonl(Path(args.data))
    val_mask = (g % 10) >= 8  # split BY GAME: 80/20
    Xtr, ytr = X[~val_mask], y[~val_mask]
    Xv, yv, hv = X[val_mask], y[val_mask], hte[val_mask]
    print(f"train {len(Xtr)} / val {len(Xv)} positions "
          f"({int((~val_mask).sum())}/{int(val_mask.sum())} by rows)")

    model = nn.Sequential(
        nn.Linear(X.shape[1], args.hidden), nn.ReLU(),
        nn.Linear(args.hidden, args.hidden), nn.ReLU(),
        nn.Linear(args.hidden, 1))
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    loss_fn = nn.BCEWithLogitsLoss()
    best_val, best_state, stale = float("inf"), None, 0
    for epoch in range(args.epochs):
        model.train()
        perm = torch.randperm(len(Xtr))
        for i in range(0, len(Xtr), args.batch):
            idx = perm[i:i + args.batch]
            opt.zero_grad()
            loss = loss_fn(model(Xtr[idx]).squeeze(-1), ytr[idx])
            loss.backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            vl = float(loss_fn(model(Xv).squeeze(-1), yv))
        print(f"epoch {epoch}: val_bce {vl:.4f}")
        if vl < best_val - 1e-5:
            best_val, stale = vl, 0
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            stale += 1
            if stale >= args.patience:
                print("early stop")
                break
    if best_state is not None:
        model.load_state_dict(best_state)

    model.eval()
    with torch.no_grad():
        val_scores = torch.sigmoid(model(Xv).squeeze(-1))
    net_m = metrics(val_scores, yv)
    hte_m = metrics(hv, yv)
    print(f"NET  val: {net_m}")
    print(f"HTE  val: {hte_m}  (hand-tuned evaluator baseline)")

    linears = [m for m in model if isinstance(m, nn.Linear)]
    layers = [{"w": m.weight.detach().tolist(), "b": m.bias.detach().tolist()}
              for m in linears]
    golden_inputs = [[0.0] * X.shape[1], [0.5] * X.shape[1],
                     [round(v, 4) for v in Xv[0].tolist()]]
    with torch.no_grad():
        golden = [{"x": gx, "y": round(float(torch.sigmoid(
            model(torch.tensor([gx], dtype=torch.float32)).squeeze(-1))[0]), 10)}
            for gx in golden_inputs]
    spec = {"feature_version": FEATURE_VERSION, "feature_names": FEATURE_NAMES,
            "layers": layers, "golden": golden,
            "meta": {"val_bce": net_m["bce"], "val_acc": net_m["acc"],
                     "val_auc": net_m["auc"], "hte_bce": hte_m["bce"],
                     "hte_acc": hte_m["acc"], "hte_auc": hte_m["auc"],
                     "n_train": len(Xtr), "n_val": len(Xv),
                     "hidden": args.hidden, "seed": args.seed,
                     "data": str(args.data)}}

    pure = ValueNet(spec)
    for gg in golden:
        got = pure.predict(gg["x"])
        if abs(got - gg["y"]) > 1e-6:
            raise SystemExit(f"PARITY FAIL: pure {got} vs torch {gg['y']}")
    print("PARITY OK: pure-Python forward matches torch on golden vectors")

    Path(args.out).write_text(json.dumps(spec), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run** — `uv run pytest tests/test_train_export.py -v` then full `uv run pytest`. Expected: green (~1-2 min for the tiny training).

- [ ] **Step 6: Commit** — `git add scripts/train_value_net.py tests/test_train_export.py pyproject.toml uv.lock && git commit -m "feat: torch value-net training with stdlib-JSON export and golden parity"`

---

### Task 9: EXECUTION — generate real data, train the production net

This is an execution task, not a code task. Long-running steps go in background shells; the orchestrator watches PIDs itself (never Git-Bash `kill -0`; use PowerShell `Get-Process`).

**Interfaces:**
- Consumes: Tasks 6+8 CLIs.
- Produces: committed `src/ptcg/search/value_net_weights.json`; dataset at `experiments/data/slice4/train_v1.jsonl` (gitignored); EXPERIMENTS.md notes; a GO/NO-GO offline verdict.

- [ ] **Step 1: Measure** — `uv run python scripts/generate_training_data.py --measure`. Record s/game. If estimated full-run time exceeds ~3 hours, reduce `--games-per-pairing` to fit (floor 150; report the choice).

- [ ] **Step 2: Generate (background)** — `uv run python scripts/generate_training_data.py --games-per-pairing 300 --out experiments/data/slice4/train_v1.jsonl --seed 7`. Verify on completion: `DONE` line printed, error count < 1% of games, file line count > 100k (`(Get-Content experiments/data/slice4/train_v1.jsonl | Measure-Object -Line).Lines` or wc -l).

- [ ] **Step 3: Train** — `uv run python scripts/train_value_net.py --data experiments/data/slice4/train_v1.jsonl --out src/ptcg/search/value_net_weights.json`. Record the printed NET/HTE metric blocks verbatim.

- [ ] **Step 4: Offline GO/NO-GO** — GO iff net val AUC > hand-tuned val AUC AND net val BCE < hand-tuned val BCE. If NO-GO: STOP; do not run arena experiments; report to orchestrator for a re-decide round (feature/data iteration is a scope decision, not an implementer improvisation).

- [ ] **Step 5: Full suite** — `uv run pytest` (the Task-7 golden parity test now RUNS against the real artifact and must pass; no skips for it).

- [ ] **Step 6: Log + commit** — append a summary line to `experiments/EXPERIMENTS.md` (dataset size, games, val metrics net-vs-hte; `encoding="utf-8"` if scripting the append). `git add src/ptcg/search/value_net_weights.json experiments/EXPERIMENTS.md && git commit -m "feat: production value-net weights v1 (offline val beats hand-tuned baseline)"`

---

### Task 10: Integration — evaluator injection through `SearchAgent` and `run_arena`

**Files:**
- Modify: `src/ptcg/agents/search_agent.py` (add `evaluator` kwarg), `scripts/run_arena.py` (add `search-net` agent + `--rollout-depth`)
- Test: `tests/test_search_agent.py` (extend)

**Interfaces:**
- Consumes: `ValueNetEvaluator.load_default()` (Task 7), production weights (Task 9).
- Produces: `SearchAgent(deck, config=..., time_manager=..., backend=..., evaluator=...)`; CLI agents `search-net` (net evaluator) and existing `search` (hand-tuned), both honoring `--rollout-depth N` (default 12). Consumed by Tasks 11–12.

- [ ] **Step 1: Write the failing test** (append to `tests/test_search_agent.py`, mirroring its construction fixtures)

```python
def test_search_agent_accepts_injected_evaluator():
    calls = []
    def stub_eval(state, my_index):
        calls.append(my_index)
        return 0.5
    agent = SearchAgent(SAMPLE_DECK, evaluator=stub_eval)   # reuse the file's deck fixture
    assert agent.searcher.evaluator is stub_eval

def test_search_agent_default_evaluator_unchanged():
    from ptcg.search.evaluate import evaluate
    agent = SearchAgent(SAMPLE_DECK)
    assert agent.searcher.evaluator is evaluate
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_search_agent.py -k evaluator -v`. Expected: TypeError (unexpected kwarg).

- [ ] **Step 3: Implement.** `search_agent.py:19-31` — add `evaluator: Callable | None = None` to `__init__` (import `Callable` from `typing`, `evaluate` from `ptcg.search.evaluate`), pass `evaluator=evaluator if evaluator is not None else evaluate` into the `Searcher(...)` construction. `run_arena.py:18-23` — change factories to accept the parsed args and add the net agent + `--rollout-depth`:

```python
AGENTS = {
    "random": lambda deck, a: RandomAgent(seed=0),
    "heuristic": lambda deck, a: HeuristicAgent(),
    "search": lambda deck, a: SearchAgent(
        deck, config=SearchConfig(rollout_depth=a.rollout_depth),
        time_manager=TimeManager(total_s=1e9, max_move_s=a.search_budget_ms / 1000.0)),
    "search-net": lambda deck, a: SearchAgent(
        deck, config=SearchConfig(rollout_depth=a.rollout_depth),
        time_manager=TimeManager(total_s=1e9, max_move_s=a.search_budget_ms / 1000.0),
        evaluator=ValueNetEvaluator.load_default()),
}
```

with `p.add_argument("--rollout-depth", type=int, default=12)`, imports for `SearchConfig` and `ValueNetEvaluator`, and the two `AGENTS[...](deck, args)` call sites updated. Give the net-backed agent a distinct display name so EXPERIMENTS.md rows are unambiguous: in `SearchAgent.__init__`, when a non-default evaluator is injected set `self.name = "search-net-v1"` (keep class attr `name = "search-v1"` as the default; check how `Agent.name` is declared in `base.py` first and adapt).

- [ ] **Step 4: Benchmark inference (spec's ~1ms budget)** — run and record:

```
uv run python -c "import json, time, sys; sys.path.insert(0, 'src'); from ptcg.search.value_net import ValueNetEvaluator; from tests.fixtures.obs import make_state; ev = ValueNetEvaluator.load_default(); st = make_state(); ev(st, 0); t0 = time.perf_counter(); n = 2000; [ev(st, 0) for _ in range(n)]; print(f'{(time.perf_counter()-t0)/n*1000:.3f} ms/eval')"
```

Expected: well under 1 ms (a 40→64→64→1 pure-Python forward is ~10k mul-adds). If over 1 ms, report the number — do NOT unilaterally shrink the net; that is an orchestrator decision. Record the measured figure in your report (it goes to EXPERIMENTS.md/plan.md).

- [ ] **Step 5: Run** — `uv run pytest tests/test_search_agent.py -v`, full `uv run pytest`, then a 2-game CLI smoke: `uv run python scripts/run_arena.py --agent-a search-net --agent-b heuristic --deck-a src/ptcg/decks/candidates/mega-lucario-fighting.csv --deck-b src/ptcg/decks/candidates/mega-lucario-fighting.csv --games 2 --notes "slice4 T10 wiring smoke"`. Expected: completes, EXPERIMENTS.md gets a row naming `search-net-v1`.

- [ ] **Step 6: Commit** — `git add src/ptcg/agents/search_agent.py scripts/run_arena.py tests/test_search_agent.py experiments/EXPERIMENTS.md && git commit -m "feat: value-net evaluator injection through SearchAgent and run_arena"`

---

### Task 11: EXECUTION — Exp 1, Exp 2, and the replicated gate

Execution task. All series via `scripts/run_arena.py`, mega-lucario mirror decks, `--search-budget-ms 200`, background shells for long runs. Record EVERY run in EXPERIMENTS.md (the CLI does this) — never only the favorable ones.

**Interfaces:**
- Consumes: Task 10 CLI.
- Produces: Exp1/Exp2 results, a gate verdict (PASS/FAIL) with all runs reported, updated `tests/test_search_acceptance.py` docstring narrative.

- [ ] **Step 1: Exp 1 (drop-in)** — 200 games: `--agent-a search-net --agent-b heuristic --rollout-depth 12 --notes "slice4 exp1 drop-in net terminal"`.
- [ ] **Step 2: Exp 2 (leaf eval)** — 200 games: same but `--rollout-depth 0 --notes "slice4 exp2 net leaf eval"`.
- [ ] **Step 3: Pick the winner** (higher win rate; if within 3 points, prefer `--rollout-depth 0` only if its win rate is not lower — fewer engine steps is the tiebreaker). Report both Wilson CIs.
- [ ] **Step 4: Gate** — two independent 300-game runs at the winning config (`--notes "slice4 gate run 1/2"` etc.). PASS iff pooled (600 games) win rate ≥ 0.55 AND each run ≥ 0.50. If the pooled rate lands in [0.52, 0.58] (near-bar per the replication rule), run a third 300-game series and re-pool before declaring.
- [ ] **Step 5: Update `tests/test_search_acceptance.py`'s docstring** to describe the slice-4 outcome (either "net breaks the parity ceiling: <numbers>" or "net measured at <numbers>; ceiling stands") — keep the existing test assertions untouched unless the orchestrator directs otherwise.
- [ ] **Step 6: Commit** — `git add tests/test_search_acceptance.py experiments/EXPERIMENTS.md && git commit -m "docs: slice4 experiment results and gate verdict"`

**Gate FAIL path:** report the verdict; Task 12 is SKIPPED (orchestrator records the rescope); the slice closes honestly.

---

### Task 12: CONDITIONAL — ladder swap, packaging, regression pin re-derivation

Only runs on a Task-11 gate PASS.

**Files:**
- Modify: `src/ptcg/agents/current.py`, `scripts/package_submission.py` (PTCG_MODULES + smoke timeout), `tests/test_regression_pin.py` (bar re-derivation), `tests/test_current.py` (identity assertions)
- Test: existing suites + packaging smoke

**Interfaces:**
- Consumes: winning config from Task 11; `ValueNetEvaluator.load_default()`.
- Produces: ladder identity = search-net agent; verified bundle; re-derived pin bar.

- [ ] **Step 1: Update `current.py`** — `CURRENT_AGENT_NAME = "search-net-v1"`; `make_current_agent(deck)` returns `SearchAgent(deck, config=SearchConfig(rollout_depth=<winning depth>), evaluator=ValueNetEvaluator.load_default())` with the default `TimeManager()` (total_s=480 — the ladder budget; do NOT pass the arena's 1e9 override). Update `tests/test_current.py` to the new name/type.
- [ ] **Step 2: Update `PTCG_MODULES`** (package_submission.py:53-59) — add `src/ptcg/agents/search_agent.py`, `src/ptcg/search/__init__.py`, `src/ptcg/search/belief.py`, `src/ptcg/search/evaluate.py`, `src/ptcg/search/features.py`, `src/ptcg/search/searcher.py`, `src/ptcg/search/timing.py`, `src/ptcg/search/tree.py`, `src/ptcg/search/value_net.py`, `src/ptcg/search/value_net_weights.json`. Raise `smoke_bundle`'s `timeout=120` to `timeout=600` (a search-vs-search smoke battle legitimately runs minutes; note it in the code comment).
- [ ] **Step 3: Package + verify** — `uv run python scripts/package_submission.py src/ptcg/decks/candidates/mega-lucario-fighting.csv`. Expected: `OK: ... MiB` + `SMOKE OK: full battle completed`. Watch it finish; report size.
- [ ] **Step 4: Re-derive the regression pin** (its docstring mandates this when the identity changes): four independent 200-game runs, current agent both seats, champion vs `tests/fixtures/sample_deck.csv`; bar = `floor((pooled_win_rate - 0.105) * 100) / 100` floored at 0.40. Update `MIN_WIN_RATE` and rewrite the derivation docstring with the new runs' numbers (all four, verbatim). NOTE: 800 search-agent games is hours of wall time — run in background; the orchestrator may split across shells.
- [ ] **Step 5: Full verification** — `uv run pytest` AND `uv run pytest -m slow` (acceptance + new pin). Expected: green; report counts per suite separately.
- [ ] **Step 6: Commit** — `git add src/ptcg/agents/current.py scripts/package_submission.py tests/test_regression_pin.py tests/test_current.py && git commit -m "feat: ladder identity swaps to search-net-v1 (gate passed)"`
- [ ] **Step 7: HAND BACK for Kaggle upload** — do NOT upload. The orchestrator asks Brad to approve the submission description first (standing rule), then the upload follows the established submission mechanics.

---

## Verification (slice level)

1. `uv run pytest` green; `uv run pytest -m slow` green (gate-pass path).
2. Rung 2 (verify the automation): confirm the golden-parity test ran against the COMMITTED weights artifact (not a skip); confirm EXPERIMENTS.md rows exist for every series claimed in reports; confirm the val metrics in `value_net_weights.json` `meta` match the training stdout records.
3. Rung 3 (manual smoke): one real `run_arena` invocation eyeballed end-to-end; on the gate-pass path, the packaging smoke battle IS the end-to-end run.
