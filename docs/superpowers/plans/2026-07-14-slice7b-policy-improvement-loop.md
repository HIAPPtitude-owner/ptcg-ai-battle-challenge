# Slice 7B — Policy-Improvement Loop (Expert Iteration) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run an expert-iteration loop — search visit distributions train a per-option policy net, injected back into ISMCTS as an all-node PUCT prior and opponent/rollout policy — testing the last untested hypothesis for a search edge on the mega-lucario mirror.

**Architecture:** New stdlib-serveable `PolicyNet` (per-option scorer over 40 state + 12 action features) with three flag-gated injection sites in the existing `Searcher`/`SearchAgent` (all default-off, bit-identical when off). New policy-target recording alongside the existing trajectory pipeline; a torch trainer mirroring `train_value_net.py`; a `PolicyImprovementTrainer` behind the factory `Trainer` protocol for overnight generations 2–3. Value net FROZEN at v2 throughout.

**Tech Stack:** Python 3.12 / uv, pytest, torch (dev-only), pure-stdlib serve path, existing `cg` engine SDK.

**Spec:** `docs/superpowers/specs/2026-07-14-slice7b-policy-improvement-loop-design.md` (commit 34cc181).

## Global Constraints

- Repo root: `C:\Users\Brad\Dev Folder\Product\ptcg-ai-battle-challenge-strategy` — **path contains a space; always quote**. Branch: `feature/slice7b-policy-improvement`.
- Run tests with `uv run pytest` (never pass `--run`). Baseline at branch point: 258 passed.
- Every Python text write specifies `encoding="utf-8"` (Windows cp1252 truncate-then-crash hazard — global rule).
- Value net FROZEN at `src/ptcg/search/value_net_weights_v2.json` for every 7B agent/run. Never `load_default()` (that is v1) in new 7B code paths.
- All three injection flags (`use_tree_prior`, `policy_opponent`, `policy_rollout`) default OFF; flags-off behavior must be bit-identical to current master (regression-tested in Task 7).
- Serve path (`src/ptcg/search/action_features.py`, `src/ptcg/search/policy_net.py`) is stdlib-only: json + math only, no torch/numpy imports.
- Pre-registered gates (spec §4, locked — do not renegotiate at run time): (1) sanity ≥60% top-1 agreement held-out; (2) throughput ≥15 iters per 200ms move; (3) per-gen probe 200 games, proceed if ≥0.50 or strictly above previous gen; (4) kill after gen 3 if all probe CIs span 0.50 with no monotonic trend; (5) final replicated 2×300 pooled ≥0.55 only if a probe point estimate ≥0.55.
- **Operating config for `search-policy` agents (pre-registered):** `rollout_depth=0`, v2 value evaluator, **v0-improvement gate OFF** (`deviate_min_visits=0`, `deviate_value_edge=0.0`). Rationale: (a) `Searcher._v0_sig` derives the gate's anchor from `rollout_policy` (searcher.py:225), so with a net injected there the "v0 gate" would silently anchor to the net, not v0; (b) training data is generated gate-off, so serve must match the training distribution; (c) Slice-5 D3 showed gate-off ≈ gated within CI.
- Long-running runs (data-gen, arena series) are **orchestrator-owned background processes**; implementer tasks for gate/probe work are scoped to setup + harvest only, never "wait for it". Arena gate/probe runs require a clean machine (`.claude/rules/time-budgeted-arena-contention.md`).
- Plan-authored test values in this plan were hand-derived; still re-verify per `.claude/rules/plan-test-arithmetic-sanity.md` before transcribing. Before writing code, grep the touched file(s) for each plan-cited landmark; if a landmark doesn't match, STOP and report `plan-drift`.
- Stage commits by explicit path; on `index.lock` contention wait 2s and retry up to 3×.
- New files stay under 500 lines; follow existing module docstring style.

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `src/ptcg/search/action_features.py` | Create | Per-option action features (stdlib, bundled) |
| `src/ptcg/search/policy_net.py` | Create | Stdlib PolicyNet inference + PolicyNetPolicy wrapper/prior provider |
| `src/ptcg/search/tree.py` | Modify | `Node.priors` cache field |
| `src/ptcg/search/searcher.py` | Modify | `SearchStats.root_visits_by_option`; `prior_fn`; `use_tree_prior` all-node PUCT; new config flags |
| `src/ptcg/agents/search_agent.py` | Modify | `policy_weights` wiring, failure ladder, `search-policy` name |
| `src/ptcg/train/policy_targets.py` | Create | PolicyDecisionRecord, PolicyRecordingAgent, labeling |
| `scripts/generate_training_data.py` | Modify | `--policy-targets` mode (+ injection flags for gen-2+) |
| `scripts/train_policy_net.py` | Create | Torch trainer, masked-softmax CE, JSON export + goldens |
| `scripts/make_random_policy_weights.py` | Create | Random-weights spec for the pre-data throughput gate |
| `scripts/run_arena.py` | Modify | `search-policy` agent + 4 new flags |
| `src/ptcg/factory/trainers.py` | Modify | `PolicyImprovementTrainer` |
| `src/ptcg/factory/evaluate.py`, `src/ptcg/factory/bundles.py` | Modify | `agent_kind="search-policy"` support (mirror `search-net`) |
| `scripts/run_policy_cycle.py` | Create | Single-cycle synchronous daemon driver (gen-1 in-session; nightly reuse) |

Task order: 1→8 are code (parallelizable where files are disjoint), 9 factory, 10 throughput gate (random weights, before any data-gen), 11 gen-1 cycle + sanity gate, 12 probe, 13 nightly wiring + analysis.

---

### Task 1: Action featurizer

**Files:**
- Create: `src/ptcg/search/action_features.py`
- Test: `tests/test_action_features.py`

**Interfaces:**
- Consumes: `Option` objects from `cg.api` (fields used: `type, cardId, attackId, number, area, energyIndex, count, specialConditionType` — same content fields as `option_signature` in `src/ptcg/search/tree.py:23-27`); the id-keyed card/attack DB dicts `SearchAgent` already builds (`search_agent.py:27-28`).
- Produces: `ACTION_FEATURE_VERSION: int = 1`, `N_ACTION_FEATURES: int = 12`, `ACTION_FEATURE_NAMES: list[str]`, `extract_action(opt, cards: dict, attacks: dict) -> list[float]` (len == 12, all values in [0,1], never raises on unknown enum values or missing DB entries).

**Landmark check (do first):** grep `class Attack` / `class CardData` in `src/cg/api.py` and confirm the attribute names used below (`damage`, `energies` on Attack; `hp`, `basic` on CardData). If any differ, STOP and report `plan-drift` with the actual names before implementing. *(Plan-drift already caught and fixed 2026-07-14: the original plan said `cost`; the real field is `energies: list[EnergyType]`.)*

- [ ] **Step 1: Write the failing test**

```python
# tests/test_action_features.py
"""Action featurizer: fixed-length, [0,1]-bounded, tolerant of unknowns."""
from types import SimpleNamespace

import pytest

from ptcg.search.action_features import (ACTION_FEATURE_NAMES,
                                         ACTION_FEATURE_VERSION,
                                         N_ACTION_FEATURES, extract_action)


def _opt(**kw):
    base = dict(type=None, cardId=None, attackId=None, number=None, area=None,
                playerIndex=None, toolIndex=None, energyIndex=None, count=None,
                inPlayArea=None, inPlayIndex=None, specialConditionType=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_version_and_shape():
    assert ACTION_FEATURE_VERSION == 1
    assert N_ACTION_FEATURES == len(ACTION_FEATURE_NAMES) == 12


def test_all_none_option_is_zero_vector():
    vec = extract_action(_opt(), {}, {})
    assert vec == [0.0] * 12


def test_full_option_values():
    card = SimpleNamespace(hp=170, basic=True)
    attack = SimpleNamespace(damage=120, energies=[1, 2])
    vec = extract_action(
        _opt(type=5, cardId=10, attackId=3, number=3, area=6, count=3,
             energyIndex=1, specialConditionType=2),
        {10: card}, {3: attack})
    # hand-derived: 5/12, 1, 1, 120/300, 2/5, 170/340, 1, 3/10, 6/12, 3/6, 1, 1
    assert vec == pytest.approx([5 / 12, 1.0, 1.0, 0.4, 0.4, 0.5, 1.0,
                                 0.3, 0.5, 0.5, 1.0, 1.0])


def test_caps_and_unknown_ids_never_raise():
    vec = extract_action(_opt(type=999, cardId=424242, attackId=999999,
                              number=50, area=99, count=50), {}, {})
    assert all(0.0 <= v <= 1.0 for v in vec) and len(vec) == 12
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_action_features.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ptcg.search.action_features'`

- [ ] **Step 3: Write the implementation**

```python
# src/ptcg/search/action_features.py
"""Per-option action features for the policy net (ACTION_FEATURE_VERSION 1).

Serve-path module (bundled on Kaggle): stdlib only. Tolerates unknown enum
members and missing DB entries — the organizers may append enum members
mid-competition — by clamping numerics and treating misses as zeros, never
raising."""
from __future__ import annotations

ACTION_FEATURE_VERSION = 1

ACTION_FEATURE_NAMES = [
    "type_norm", "has_card", "has_attack", "attack_damage", "attack_cost",
    "card_hp", "card_basic", "number_norm", "area_norm", "count_norm",
    "has_energy_index", "has_special_condition",
]
N_ACTION_FEATURES = len(ACTION_FEATURE_NAMES)


def _norm(x, hi: float) -> float:
    v = -1.0 if x is None else float(int(x))
    return max(0.0, min(v, hi)) / hi


def extract_action(opt, cards: dict, attacks: dict) -> list[float]:
    """Features of one selectable Option, len == N_ACTION_FEATURES, all in [0,1].
    `cards`/`attacks` are the id-keyed DB dicts SearchAgent already builds."""
    card = cards.get(opt.cardId) if opt.cardId is not None else None
    attack = attacks.get(opt.attackId) if opt.attackId is not None else None
    damage = float(getattr(attack, "damage", 0) or 0) if attack else 0.0
    energies = (attack.energies if attack is not None
                and getattr(attack, "energies", None) else [])
    hp = float(getattr(card, "hp", 0) or 0) if card else 0.0
    return [
        _norm(opt.type, 12.0),
        1.0 if card is not None else 0.0,
        1.0 if attack is not None else 0.0,
        min(damage, 300.0) / 300.0,
        min(float(len(energies)), 5.0) / 5.0,
        min(hp, 340.0) / 340.0,
        1.0 if card is not None and getattr(card, "basic", False) else 0.0,
        _norm(opt.number, 10.0),
        _norm(opt.area, 12.0),
        _norm(opt.count, 6.0),
        1.0 if opt.energyIndex is not None else 0.0,
        1.0 if opt.specialConditionType is not None else 0.0,
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_action_features.py -v` — Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/search/action_features.py tests/test_action_features.py
git commit -m "feat: per-option action featurizer for policy net (v1, 12 features)"
```

---

### Task 2: Expose root visit distribution in SearchStats

**Files:**
- Modify: `src/ptcg/search/searcher.py` (SearchStats dataclass ~:55-69; search() stats block ~:145-160)
- Test: `tests/test_searcher.py` (extend; read it first and reuse its `FakeBelief`/`FakeBackend` fixtures — this is where `test_stats_flag_does_not_change_move_sequence` lives. NOT `tests/test_stats.py`, which tests arena SeriesStats; plan-drift caught and fixed 2026-07-14.)

**Interfaces:**
- Consumes: existing `root_index` map (`searcher.py:114-115`) and `visited` dict (`searcher.py:138`).
- Produces: `SearchStats.root_visits_by_option: dict[int, int] | None = None` — option index → visit count for every visited root child, populated only under `collect_stats`. Consumed by Task 4's recorder.

- [ ] **Step 1: Write the failing test** (in `tests/test_searcher.py`, adapting to its existing `FakeBelief`/`FakeBackend` construction — grep its fixtures before writing; keep the assertions below, adapt the setup)

```python
def test_stats_include_root_visit_distribution(...existing fixture args...):
    # construct Searcher exactly as the neighboring stats tests do,
    # config with collect_stats=True, generous deadline
    result = searcher.search(obs, deadline=time.perf_counter() + 5.0)
    stats = searcher.last_stats
    assert stats is not None and stats.root_visits_by_option
    n_options = len(obs.select.option)
    assert all(0 <= i < n_options for i in stats.root_visits_by_option)
    assert all(v >= 1 for v in stats.root_visits_by_option.values())
    # clean fake backend: every iteration completes exactly one root-child visit
    assert sum(stats.root_visits_by_option.values()) == stats.iterations
    # the most-visited entry agrees with the reported top_child_visits
    assert max(stats.root_visits_by_option.values()) == stats.top_child_visits
```

(Arithmetic sanity, verified against `_simulate`: each iteration that begins successfully appends exactly one root child to `path` and backprops `visits += 1` through it; with a clean fake backend there are no begin/step failures and the test deadline is generous, so the sum equals `iterations`. If the existing fake backend CAN fail mid-iteration, relax the equality to `<= stats.iterations` and say so in your report.)

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/test_searcher.py -v` → new test FAILS (`root_visits_by_option` attribute missing).

- [ ] **Step 3: Implement.** In `SearchStats` add the field (bottom of the dataclass):

```python
    root_visits_by_option: dict[int, int] | None = None
```

In `search()`'s `if cfg.collect_stats:` block, add to the `SearchStats(...)` constructor call:

```python
                root_visits_by_option={root_index[s]: n.visits
                                       for s, n in visited.items()
                                       if s in root_index},
```

- [ ] **Step 4: Run the full fast suite** — `uv run pytest` → all pass, including the existing guard `test_stats_flag_does_not_change_move_sequence` (stats stay read-only provenance).

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/search/searcher.py tests/test_searcher.py
git commit -m "feat: expose root visit distribution in SearchStats (policy-target source)"
```

---

### Task 3: Stdlib policy net + policy wrapper

**Files:**
- Create: `src/ptcg/search/policy_net.py`
- Test: `tests/test_policy_net.py`

**Interfaces:**
- Consumes: `_affine` from `ptcg.search.value_net`; `FEATURE_VERSION`/`extract` from `ptcg.search.features`; `ACTION_FEATURE_VERSION`/`extract_action` from Task 1; `action_signature` from `ptcg.search.tree`.
- Produces:
  - `PolicyNet(spec: dict)` — validates `feature_version` and `action_feature_version`; `score_options(state_x: list[float], option_xs: list[list[float]]) -> list[float]` (softmax probs, uniform fallback on non-finite logits).
  - `PolicyNetPolicy(net, fallback: Policy, dbs: tuple[dict, dict])` — callable `(Observation) -> list[int]` (argmax on 1-of-1 selects with ≥2 options, `fallback(obs)` otherwise or on any error); `priors_for(obs) -> dict[ActionSig, float] | None`; `classmethod load(path, fallback, dbs)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_policy_net.py
"""PolicyNet stdlib inference: golden softmax, degenerate guard, wrapper fallback."""
import math
from types import SimpleNamespace

import pytest

from ptcg.search.policy_net import PolicyNet, PolicyNetPolicy

N_STATE, N_ACTION = 40, 12


def _single_layer_spec():
    # one linear layer 52->1 selecting action feature 0: logit == action_x[0]
    w = [[0.0] * N_STATE + [1.0] + [0.0] * (N_ACTION - 1)]
    return {"feature_version": 1, "action_feature_version": 1,
            "layers": [{"w": w, "b": [0.0]}]}


def test_softmax_golden():
    net = PolicyNet(_single_layer_spec())
    a = [math.log(2.0)] + [0.0] * (N_ACTION - 1)   # logit ln2
    b = [0.0] * N_ACTION                            # logit 0
    probs = net.score_options([0.0] * N_STATE, [a, b])
    # exp(ln2)=2, exp(0)=1 -> [2/3, 1/3]
    assert probs == pytest.approx([2 / 3, 1 / 3])
    assert sum(probs) == pytest.approx(1.0)


def test_nonfinite_logits_fall_back_to_uniform():
    spec = _single_layer_spec()
    spec["layers"][0]["b"] = [float("inf")]
    net = PolicyNet(spec)
    probs = net.score_options([0.0] * N_STATE,
                              [[0.0] * N_ACTION, [0.0] * N_ACTION])
    assert probs == [0.5, 0.5]


def test_version_mismatch_raises():
    spec = _single_layer_spec()
    spec["action_feature_version"] = 99
    with pytest.raises(ValueError):
        PolicyNet(spec)


def test_wrapper_falls_back_on_non_one_of_one():
    calls = []
    fallback = lambda obs: calls.append(obs) or [0]  # noqa: E731
    pol = PolicyNetPolicy(PolicyNet(_single_layer_spec()), fallback, ({}, {}))
    obs = SimpleNamespace(select=SimpleNamespace(minCount=0, maxCount=2,
                                                 option=[1, 2, 3]),
                          current=None)
    assert pol(obs) == [0] and len(calls) == 1
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_policy_net.py -v` → ModuleNotFoundError.

- [ ] **Step 3: Implement**

```python
# src/ptcg/search/policy_net.py
"""Pure-stdlib policy-net inference (per-option scorer) + Policy wrapper.

Weights are torch-trained (scripts/train_policy_net.py) and JSON-exported;
this module must stay importable inside the Kaggle bundle (no torch/numpy).
Input per option = 40 state features ++ 12 action features -> scalar logit;
softmax across the decision's option list."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Callable

from ptcg.search.action_features import ACTION_FEATURE_VERSION, extract_action
from ptcg.search.features import FEATURE_VERSION, extract
from ptcg.search.tree import ActionSig, action_signature
from ptcg.search.value_net import _affine


class PolicyNet:
    def __init__(self, spec: dict) -> None:
        if spec.get("feature_version") != FEATURE_VERSION:
            raise ValueError(f"feature_version {spec.get('feature_version')!r} "
                             f"!= extractor version {FEATURE_VERSION}")
        if spec.get("action_feature_version") != ACTION_FEATURE_VERSION:
            raise ValueError(
                f"action_feature_version {spec.get('action_feature_version')!r} "
                f"!= featurizer version {ACTION_FEATURE_VERSION}")
        self.layers = spec["layers"]

    def _logit(self, x: list[float]) -> float:
        h = x
        for layer in self.layers[:-1]:
            h = [v if v > 0.0 else 0.0 for v in _affine(h, layer["w"], layer["b"])]
        return _affine(h, self.layers[-1]["w"], self.layers[-1]["b"])[0]

    def score_options(self, state_x: list[float],
                      option_xs: list[list[float]]) -> list[float]:
        """Softmax over per-option logits; uniform on any non-finite logit
        (degenerate-weights guard — spec's failure ladder)."""
        logits = [self._logit(state_x + ox) for ox in option_xs]
        if not all(math.isfinite(z) for z in logits):
            return [1.0 / len(option_xs)] * len(option_xs)
        m = max(logits)
        exps = [math.exp(z - m) for z in logits]
        total = sum(exps)
        return [e / total for e in exps]


class PolicyNetPolicy:
    """Policy callable + prior provider for Searcher injection.

    Scores only 1-of-1 selects with >=2 options; everything else (multi-selects,
    forced picks, any scoring error) delegates to `fallback` (heuristic-v0)."""

    def __init__(self, net: PolicyNet, fallback: Callable,
                 dbs: tuple[dict, dict]) -> None:
        self.net = net
        self.fallback = fallback
        self.dbs = dbs

    @classmethod
    def load(cls, path: str | Path, fallback: Callable,
             dbs: tuple[dict, dict]) -> "PolicyNetPolicy":
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(PolicyNet(spec), fallback, dbs)

    def _probs(self, obs) -> list[float]:
        st = obs.current
        state_x = extract(st, st.yourIndex, dbs=self.dbs)
        option_xs = [extract_action(o, self.dbs[0], self.dbs[1])
                     for o in obs.select.option]
        return self.net.score_options(state_x, option_xs)

    def __call__(self, obs) -> list[int]:
        sel = obs.select
        if (sel is None or sel.minCount != 1 or sel.maxCount != 1
                or len(sel.option) < 2):
            return self.fallback(obs)
        try:
            probs = self._probs(obs)
        except (ValueError, RuntimeError, KeyError, AttributeError, TypeError):
            return self.fallback(obs)
        return [max(range(len(probs)), key=probs.__getitem__)]

    def priors_for(self, obs) -> dict[ActionSig, float] | None:
        """PUCT priors keyed by action signature; None (never partial) on any
        failure so the caller falls back to plain UCB for that node."""
        sel = obs.select
        if (sel is None or sel.minCount != 1 or sel.maxCount != 1
                or len(sel.option) < 2):
            return None
        try:
            probs = self._probs(obs)
        except (ValueError, RuntimeError, KeyError, AttributeError, TypeError):
            return None
        return {action_signature(sel, (i,)): p for i, p in enumerate(probs)}
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_policy_net.py -v` → 5 passed. Then `uv run pytest` → suite green.

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/search/policy_net.py tests/test_policy_net.py
git commit -m "feat: stdlib policy-net inference + Policy wrapper with v0 fallback"
```

---

### Task 4: Policy-target recording

**Files:**
- Create: `src/ptcg/train/policy_targets.py`
- Test: `tests/test_policy_targets.py`

**Interfaces:**
- Consumes: `SearchStats.root_visits_by_option` (Task 2) via `inner.searcher.last_stats`; `extract`/`FEATURE_VERSION`; `extract_action`/`ACTION_FEATURE_VERSION`; `append_jsonl` from `ptcg.train.trajectory`; `inner.cards`/`inner.attacks` (SearchAgent attributes, `search_agent.py:27-28`).
- Produces: `PolicyDecisionRecord` dataclass; `PolicyRecordingAgent(inner, sink)` (Agent wrapper); `label_policy_records(sink, winner, game_id) -> list[dict]` with row schema `{"v","pv","g","s","y","x","opts","n","chosen"}`.

**Consume-once contract (encode exactly):** `SearchAgent.act` can fall back to v0 (exception or `result is None`) WITHOUT refreshing `searcher.last_stats`, so a stale stats object from an earlier decision could be misattributed. The wrapper must set `inner.searcher.last_stats = None` BEFORE calling `inner.act`, and only record if a fresh stats object appeared after.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_policy_targets.py
"""Policy-target recording: schema, consume-once staleness guard, labeling."""
from types import SimpleNamespace

import pytest

from ptcg.train.policy_targets import (PolicyDecisionRecord,
                                       PolicyRecordingAgent,
                                       label_policy_records)


class FakeSearcher:
    def __init__(self):
        self.last_stats = None


class FakeInner:
    """Stands in for SearchAgent: name/cards/attacks/searcher/act."""
    name = "search-v1"

    def __init__(self, result, stats_after):
        self.cards, self.attacks = {}, {}
        self.searcher = FakeSearcher()
        self._result = result
        self._stats_after = stats_after

    def act(self, obs):
        self.searcher.last_stats = self._stats_after
        return self._result


def _obs(n_options=3):
    opt = SimpleNamespace(type=1, cardId=None, attackId=None, number=None,
                          area=None, playerIndex=None, toolIndex=None,
                          energyIndex=None, count=None, inPlayArea=None,
                          inPlayIndex=None, specialConditionType=None)
    return SimpleNamespace(
        select=SimpleNamespace(minCount=1, maxCount=1, option=[opt] * n_options),
        current=SimpleNamespace(result=-1, yourIndex=0,
                                # extract() is monkeypatched below; content unused
                                ))


def test_records_fresh_stats(monkeypatch):
    import ptcg.train.policy_targets as pt
    monkeypatch.setattr(pt, "extract", lambda st, seat, dbs=None: [0.0] * 40)
    stats = SimpleNamespace(root_visits_by_option={0: 7, 2: 3})
    sink = []
    agent = PolicyRecordingAgent(FakeInner([0], stats), sink)
    assert agent.act(_obs()) == [0]
    assert len(sink) == 1
    rec = sink[0]
    assert rec.visits == [7, 0, 3] and rec.chosen == 0
    assert len(rec.option_features) == 3


def test_stale_stats_not_recorded(monkeypatch):
    """Inner falls back to v0 (does NOT write last_stats); pre-existing stale
    stats must be cleared, not recorded."""
    import ptcg.train.policy_targets as pt
    monkeypatch.setattr(pt, "extract", lambda st, seat, dbs=None: [0.0] * 40)
    inner = FakeInner([1], stats_after=None)
    inner.searcher.last_stats = SimpleNamespace(
        root_visits_by_option={0: 99})  # stale, from a previous decision
    sink = []
    agent = PolicyRecordingAgent(inner, sink)

    def act_without_stats(obs):  # fallback path: act() never touches last_stats
        return [1]
    inner.act = act_without_stats
    assert agent.act(_obs()) == [1]
    assert sink == []


def test_label_policy_records_outcomes():
    rec = PolicyDecisionRecord(seat=1, state_features=[0.0] * 40,
                               option_features=[[0.0] * 12] * 2,
                               visits=[5, 5], chosen=1)
    rows = label_policy_records([rec], winner=1, game_id=3)
    assert rows[0]["y"] == 1.0 and rows[0]["g"] == 3 and rows[0]["pv"] == 1
    assert label_policy_records([rec], winner=0, game_id=3)[0]["y"] == 0.0
    assert label_policy_records([rec], winner=2, game_id=3)[0]["y"] == 0.5
    with pytest.raises(ValueError):
        label_policy_records([rec], winner=5, game_id=0)
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_policy_targets.py -v` → ModuleNotFoundError.

- [ ] **Step 3: Implement**

```python
# src/ptcg/train/policy_targets.py
"""Per-decision policy-target recording (dev-only, never bundled).

Expert-iteration training signal: the search root's visit distribution over
options, plus state and per-option action features. One JSONL row per decision
the search actually completed (1-of-1 select, >=2 options, fresh SearchStats)."""
from __future__ import annotations

from dataclasses import dataclass

from cg.api import Observation

from ptcg.agents.base import Agent
from ptcg.search.action_features import ACTION_FEATURE_VERSION, extract_action
from ptcg.search.features import FEATURE_VERSION, extract


@dataclass
class PolicyDecisionRecord:
    seat: int
    state_features: list[float]
    option_features: list[list[float]]
    visits: list[int]
    chosen: int


class PolicyRecordingAgent(Agent):
    """Wraps a collect_stats=True SearchAgent.

    Consume-once contract: `searcher.last_stats` is cleared BEFORE inner.act so
    a stale stats object from a previous decision (the v0-fallback path never
    refreshes it) can never be misattributed to this decision."""

    def __init__(self, inner, sink: list[PolicyDecisionRecord]) -> None:
        self.inner = inner
        self.sink = sink
        self.name = f"ptgt({inner.name})"

    def act(self, obs: Observation) -> list[int]:
        st = obs.current
        searcher = self.inner.searcher
        searcher.last_stats = None
        result = self.inner.act(obs)
        stats = searcher.last_stats
        if (st is not None and st.result == -1 and stats is not None
                and getattr(stats, "root_visits_by_option", None)
                and len(result) == 1):
            sel = obs.select
            seat = st.yourIndex
            cards, attacks = self.inner.cards, self.inner.attacks
            opts = [extract_action(o, cards, attacks) for o in sel.option]
            visits = [stats.root_visits_by_option.get(i, 0)
                      for i in range(len(sel.option))]
            if sum(visits) >= 1:
                self.sink.append(PolicyDecisionRecord(
                    seat, extract(st, seat), opts, visits, result[0]))
        return result


def label_policy_records(sink: list[PolicyDecisionRecord], winner: int,
                         game_id: int) -> list[dict]:
    """winner: 0/1 seat index, 2 = draw (MatchResult convention)."""
    if winner not in (0, 1, 2):
        raise ValueError(
            f"label_policy_records called on an errored match (winner={winner})")
    out = []
    for rec in sink:
        y = 0.5 if winner == 2 else (1.0 if rec.seat == winner else 0.0)
        out.append({"v": FEATURE_VERSION, "pv": ACTION_FEATURE_VERSION,
                    "g": game_id, "s": rec.seat, "y": y,
                    "x": [round(v, 4) for v in rec.state_features],
                    "opts": [[round(v, 4) for v in o]
                             for o in rec.option_features],
                    "n": rec.visits, "chosen": rec.chosen})
    return out
```

Note: `extract` is imported at module level so tests can monkeypatch `pt.extract`; keep it that way.

- [ ] **Step 4: Run** — `uv run pytest tests/test_policy_targets.py -v` → 3 passed; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/train/policy_targets.py tests/test_policy_targets.py
git commit -m "feat: policy-target recording with consume-once staleness guard"
```

---

### Task 5: `--policy-targets` data-gen mode

**Files:**
- Modify: `scripts/generate_training_data.py`
- Test: `tests/test_generate_training_data.py` (extend — read the existing tests first and follow their stub/wiring style)

**Interfaces:**
- Consumes: Tasks 3, 4; `SearchConfig` (Task 7 adds the flags — until Task 7 lands, only construct the flags via `getattr`-safe kwargs; see ordering note below); `ValueNetEvaluator.load`.
- Produces: CLI mode `--policy-targets` (requires `--agent search`, forces gate off), new args `--policy-weights`, `--tree-prior`, `--policy-opponent`, `--policy-rollout`; `make_policy_target_agents(deck_a, deck_b, budget_ms, sink, args)`; `V2_WEIGHTS` constant. Rows written via `label_policy_records` + `append_jsonl`. Default out for the mode: `experiments/data/slice7b/policy_gen1.jsonl`.

**Ordering note:** this task depends on Task 7's `SearchConfig` fields (`use_tree_prior`, `policy_opponent`, `policy_rollout`) and Task 8's `SearchAgent(policy_weights=...)` kwarg for the gen-2+ path. Implement Task 5 AFTER Tasks 7–8, or (if dispatched in parallel) pass injection kwargs only when `args.policy_weights` is set — gen-1 usage never sets them. State which you did in your report.

- [ ] **Step 1: Write failing tests** — extend `tests/test_generate_training_data.py`:

```python
def test_policy_targets_requires_search_agent(...):
    # invoking main()/run() with --policy-targets and agent="v0" raises SystemExit
def test_policy_targets_forces_gate_off(...):
    # --policy-targets with --gate on -> SystemExit with a message naming the rationale
def test_make_policy_target_agents_wiring(...):
    # both returned agents are PolicyRecordingAgent wrapping SearchAgent with
    # collect_stats=True, rollout_depth=0, deviate_min_visits=0,
    # deviate_value_edge=0.0, evaluator loaded from V2_WEIGHTS
```

Follow the existing file's fixture style for constructing args/agents without playing games (it already tests `make_agents` wiring — mirror that pattern; if it plays real games instead, STOP and report `plan-drift` with what it actually does).

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Key additions to `scripts/generate_training_data.py`:

```python
from ptcg.train.policy_targets import (PolicyRecordingAgent,   # noqa: E402
                                       label_policy_records)

V2_WEIGHTS = ROOT / "src" / "ptcg" / "search" / "value_net_weights_v2.json"


def make_policy_target_agents(deck_a, deck_b, budget_ms, sink, args):
    """Expert-iteration self-play pair: gate OFF (the search's own move must be
    PLAYED, or trajectories collapse back to v0's distribution — the exact
    off-policy trap Slice 6 diagnosed), rollout_depth=0 + frozen v2 evaluator
    (7B operating config), collect_stats for root visit distributions."""
    kw = {}
    if args.policy_weights:  # gen-2+: inject the previous generation's net
        kw = dict(policy_weights=args.policy_weights)
    agents = []
    for deck in (deck_a, deck_b):
        cfg = SearchConfig(rollout_depth=0, deviate_min_visits=0,
                           deviate_value_edge=0.0,
                           use_tree_prior=args.tree_prior,
                           policy_opponent=args.policy_opponent,
                           policy_rollout=args.policy_rollout)
        inner = SearchAgent(deck, config=cfg,
                            time_manager=TimeManager(
                                total_s=1e9, max_move_s=budget_ms / 1000.0),
                            evaluator=ValueNetEvaluator.load(V2_WEIGHTS),
                            collect_stats=True, **kw)
        agents.append(PolicyRecordingAgent(inner, sink))
    return agents[0], agents[1]
```

CLI: add `--policy-targets` (store_true), `--policy-weights` (default None), `--tree-prior`/`--policy-opponent`/`--policy-rollout` (store_true). In `main()`: if `args.policy_targets`: require `args.agent == "search"` else `SystemExit`; require `args.gate == "off"` else `SystemExit("policy-targets requires --gate off: the search's move must be played or trajectories stay on v0's distribution")`; default `--out` for the mode `experiments/data/slice7b/policy_gen1.jsonl`. Thread a `policy_targets: bool` through `run()`: when true, build agents via `make_policy_target_agents` and label via `label_policy_records(sink, result.winner, game_id)` (no `src` arg). Keep the existing value-net path byte-for-byte unchanged.

- [ ] **Step 4: Run** — new tests + full suite green.

- [ ] **Step 5: Commit**

```bash
git add scripts/generate_training_data.py tests/test_generate_training_data.py
git commit -m "feat: --policy-targets self-play data-gen mode (gate-off, v2 evaluator)"
```

---

### Task 6: Policy-net trainer script

**Files:**
- Create: `scripts/train_policy_net.py`
- Create: `scripts/make_random_policy_weights.py`
- Test: `tests/test_train_policy_export.py`

**Interfaces:**
- Consumes: rows from Task 4's schema; `PolicyNet` for the export parity check; `FEATURE_VERSION`, `ACTION_FEATURE_VERSION`, `N_ACTION_FEATURES`, `FEATURE_NAMES`.
- Produces: `scripts/train_policy_net.py --data <jsonl>... --out <json> [--epochs 40 --hidden 64 --batch 256 --lr 1e-3 --patience 5 --seed 0]`. Exports spec `{"feature_version","action_feature_version","layers","golden":[{"x","opts","p"}...],"meta":{...}}`; prints `VAL agreement <float>` (gate-1 measurement) and `PARITY OK`. Also `scripts/make_random_policy_weights.py --out <json> [--seed 0]` producing a spec with the same architecture and random small weights (throughput gate needs realistic-cost inference before any training data exists).

**Complexity glance (mandatory):** per-batch work is O(B × K × hidden²) with K = max options per decision in the batch (observed decisions have ~2–15 options); the padded tensor is B×K×52 floats. At B=256, K≤20 that is ~2.7 MB — no scale hazard. The first production-scale run (Task 11, ~10⁴–10⁵ decisions) is itself the scale test; nothing here is O(n²) in dataset size.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_train_policy_export.py
"""Train on trivially separable synthetic targets; verify export + parity +
learnability. Torch is a dev-only dependency (mirrors test_train_export.py)."""
import json
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _write_synthetic(path: Path, n_decisions: int = 240) -> None:
    rng = random.Random(0)
    rows = []
    for g in range(n_decisions):
        k = rng.choice([2, 3, 4])
        opts = [[round(rng.random(), 4) for _ in range(12)] for _ in range(k)]
        best = max(range(k), key=lambda i: opts[i][3])
        n = [9 if i == best else 1 for i in range(k)]
        rows.append({"v": 1, "pv": 1, "g": g, "s": g % 2, "y": float(g % 2),
                     "x": [round(rng.random(), 4) for _ in range(40)],
                     "opts": opts, "n": n, "chosen": best})
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_train_export_parity_and_learnability(tmp_path):
    data = tmp_path / "syn.jsonl"
    out = tmp_path / "policy.json"
    _write_synthetic(data)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "train_policy_net.py"),
         "--data", str(data), "--out", str(out),
         "--epochs", "30", "--hidden", "32", "--seed", "0"],
        capture_output=True, text=True, cwd=ROOT, timeout=300)
    assert proc.returncode == 0, proc.stderr
    assert "PARITY OK" in proc.stdout
    spec = json.loads(out.read_text(encoding="utf-8"))
    assert spec["feature_version"] == 1 and spec["action_feature_version"] == 1
    # target is a pure function of action feature 3 -> easily learnable
    assert spec["meta"]["val_agreement"] >= 0.7


def test_make_random_policy_weights(tmp_path):
    out = tmp_path / "rand.json"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_random_policy_weights.py"),
         "--out", str(out)],
        capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert proc.returncode == 0, proc.stderr
    from ptcg.search.policy_net import PolicyNet
    net = PolicyNet(json.loads(out.read_text(encoding="utf-8")))
    probs = net.score_options([0.0] * 40, [[0.0] * 12, [1.0] * 12])
    assert len(probs) == 2 and abs(sum(probs) - 1.0) < 1e-9
```

(Arithmetic sanity: the synthetic target's argmax is by construction `argmax(opts[i][3])`, a linear function of one input — a 32-hidden MLP with 30 epochs on 240 decisions reaches near-1.0 agreement; 0.7 is a deliberately loose floor. If the test flakes at 0.7 with seed 0, that is a real trainer bug, not threshold noise.)

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement `scripts/train_policy_net.py`**

```python
"""Train the policy MLP (torch, dev-only) on visit-distribution targets and
export stdlib-JSON weights + goldens. Mirrors scripts/train_value_net.py."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptcg.search.action_features import (ACTION_FEATURE_NAMES,  # noqa: E402
                                         ACTION_FEATURE_VERSION)
from ptcg.search.features import FEATURE_NAMES, FEATURE_VERSION  # noqa: E402
from ptcg.search.policy_net import PolicyNet  # noqa: E402

N_IN = len(FEATURE_NAMES) + len(ACTION_FEATURE_NAMES)


def load_decisions(paths: list[Path]) -> list[dict]:
    decisions = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if r["v"] != FEATURE_VERSION or r["pv"] != ACTION_FEATURE_VERSION:
                    raise SystemExit(f"feature version mismatch in {path}: "
                                     f"v={r['v']} pv={r['pv']}")
                decisions.append(r)
    return decisions


def pad_batch(batch: list[dict]):
    b, k = len(batch), max(len(d["opts"]) for d in batch)
    x = torch.zeros(b, k, N_IN)
    mask = torch.zeros(b, k, dtype=torch.bool)
    target = torch.zeros(b, k)
    for i, d in enumerate(batch):
        s = torch.tensor(d["x"], dtype=torch.float32)
        for j, ox in enumerate(d["opts"]):
            x[i, j] = torch.cat([s, torch.tensor(ox, dtype=torch.float32)])
            mask[i, j] = True
        n = torch.tensor(d["n"], dtype=torch.float32)
        target[i, :len(d["n"])] = n / n.sum()
    return x, mask, target


def masked_ce(logits, mask, target):
    logits = logits.masked_fill(~mask, -1e9)
    logp = torch.log_softmax(logits, dim=1)
    return -(target * logp).sum(dim=1).mean()


def agreement(logits, mask, target) -> float:
    logits = logits.masked_fill(~mask, -1e9)
    return float((logits.argmax(1) == target.argmax(1)).float().mean())


def evaluate_split(model, split, batch_size):
    losses, agrees, n = [], [], 0
    with torch.no_grad():
        for i in range(0, len(split), batch_size):
            x, mask, tgt = pad_batch(split[i:i + batch_size])
            logits = model(x.view(-1, N_IN)).view(x.shape[0], x.shape[1])
            losses.append(float(masked_ce(logits, mask, tgt)) * x.shape[0])
            agrees.append(agreement(logits, mask, tgt) * x.shape[0])
            n += x.shape[0]
    return sum(losses) / n, sum(agrees) / n


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True, nargs="+")
    p.add_argument("--out", required=True)
    p.add_argument("--epochs", type=int, default=40)
    p.add_argument("--hidden", type=int, default=64)
    p.add_argument("--batch", type=int, default=256)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--patience", type=int, default=5)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()
    torch.manual_seed(args.seed)

    decisions = load_decisions([Path(d) for d in args.data])
    train = [d for d in decisions if d["g"] % 10 < 8]   # split BY GAME: 80/20
    val = [d for d in decisions if d["g"] % 10 >= 8]
    print(f"train {len(train)} / val {len(val)} decisions")
    if not train or not val:
        raise SystemExit("empty train or val split")

    model = nn.Sequential(nn.Linear(N_IN, args.hidden), nn.ReLU(),
                          nn.Linear(args.hidden, args.hidden), nn.ReLU(),
                          nn.Linear(args.hidden, 1))
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    best_val, best_state, stale = float("inf"), None, 0
    rng = torch.Generator().manual_seed(args.seed)
    for epoch in range(args.epochs):
        model.train()
        perm = torch.randperm(len(train), generator=rng).tolist()
        for i in range(0, len(perm), args.batch):
            batch = [train[j] for j in perm[i:i + args.batch]]
            x, mask, tgt = pad_batch(batch)
            opt.zero_grad()
            logits = model(x.view(-1, N_IN)).view(x.shape[0], x.shape[1])
            loss = masked_ce(logits, mask, tgt)
            loss.backward()
            opt.step()
        model.eval()
        vl, va = evaluate_split(model, val, args.batch)
        print(f"epoch {epoch}: val_ce {vl:.4f} val_agreement {va:.4f}",
              flush=True)
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
    val_ce, val_agreement = evaluate_split(model, val, args.batch)
    print(f"VAL ce {val_ce:.4f}")
    print(f"VAL agreement {val_agreement:.4f}")

    linears = [m for m in model if isinstance(m, nn.Linear)]
    layers = [{"w": m.weight.detach().tolist(), "b": m.bias.detach().tolist()}
              for m in linears]
    goldens = []
    with torch.no_grad():
        for d in val[:3]:
            x, mask, _ = pad_batch([d])
            logits = model(x.view(-1, N_IN)).view(1, -1)
            probs = torch.softmax(logits.masked_fill(~mask, -1e9), dim=1)[0]
            goldens.append({"x": d["x"], "opts": d["opts"],
                            "p": [round(float(v), 10)
                                  for v in probs[:len(d["opts"])]]})
    spec = {"feature_version": FEATURE_VERSION,
            "action_feature_version": ACTION_FEATURE_VERSION,
            "layers": layers, "golden": goldens,
            "meta": {"val_ce": val_ce, "val_agreement": val_agreement,
                     "n_train": len(train), "n_val": len(val),
                     "hidden": args.hidden, "seed": args.seed,
                     "data": [str(d) for d in args.data]}}
    pure = PolicyNet(spec)
    for g in goldens:
        got = pure.score_options(g["x"], g["opts"])
        for a, b in zip(got, g["p"]):
            if abs(a - b) > 1e-6:
                raise SystemExit(f"PARITY FAIL: pure {a} vs torch {b}")
    print("PARITY OK: pure-Python softmax matches torch on golden decisions")
    Path(args.out).write_text(json.dumps(spec), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
```

(Lint before committing — `uv run ruff check scripts/train_policy_net.py`.)

And `scripts/make_random_policy_weights.py`:

```python
"""Emit a randomly-initialized policy-net spec (real architecture, no training).
Used by the Task-10 throughput gate: inference cost is architecture-dependent,
not weights-dependent, so the gate can run before any training data exists."""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptcg.search.action_features import (ACTION_FEATURE_NAMES,  # noqa: E402
                                         ACTION_FEATURE_VERSION)
from ptcg.search.features import FEATURE_NAMES, FEATURE_VERSION  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--hidden", type=int, default=64)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()
    rng = random.Random(args.seed)
    n_in = len(FEATURE_NAMES) + len(ACTION_FEATURE_NAMES)

    def layer(n_out, n_inp):
        return {"w": [[rng.uniform(-0.1, 0.1) for _ in range(n_inp)]
                      for _ in range(n_out)],
                "b": [0.0] * n_out}

    spec = {"feature_version": FEATURE_VERSION,
            "action_feature_version": ACTION_FEATURE_VERSION,
            "layers": [layer(args.hidden, n_in),
                       layer(args.hidden, args.hidden),
                       layer(1, args.hidden)],
            "golden": [], "meta": {"random": True, "seed": args.seed}}
    Path(args.out).write_text(json.dumps(spec), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_train_policy_export.py -v` → 2 passed (allow ~30–90s; torch CPU). Full suite green.

- [ ] **Step 5: Commit**

```bash
git add scripts/train_policy_net.py scripts/make_random_policy_weights.py tests/test_train_policy_export.py
git commit -m "feat: policy-net trainer (masked-softmax CE) + random-weights generator"
```

---

### Task 7: All-node PUCT tree prior in Searcher

**Files:**
- Modify: `src/ptcg/search/tree.py` (Node), `src/ptcg/search/searcher.py` (SearchConfig, `__init__`, `_simulate`)
- Test: `tests/test_searcher.py` (extend; reuse its fake backend + fake obs fixtures — read the file first)

**Interfaces:**
- Consumes: `puct_pick` (tree.py:56), slice-6 root-prior branch (`searcher.py:309-312`).
- Produces: `Node.priors: dict | None = None`; `SearchConfig.use_tree_prior: bool = False`, `SearchConfig.policy_opponent: bool = False`, `SearchConfig.policy_rollout: bool = False` (the latter two are consumed by Task 8's SearchAgent wiring, not by Searcher — document that in the config comment); `Searcher.__init__(..., prior_fn: Callable[[Observation], dict | None] | None = None)`.

- [ ] **Step 1: Write the failing tests** (adapt setup to the file's existing fixtures):

```python
def test_flags_off_bit_identical_with_prior_fn_present(...):
    """prior_fn set but use_tree_prior=False must not change ANYTHING:
    same returned move, same iterations, same root visit distribution."""
    # searcher_a: prior_fn=None; searcher_b: prior_fn=lambda o: {}, use_tree_prior=False
    # identical config/seed/backend; collect_stats=True on both
    assert move_a == move_b
    assert stats_a.iterations == stats_b.iterations
    assert stats_a.root_visits_by_option == stats_b.root_visits_by_option

def test_tree_prior_used_when_flag_on(...):
    """use_tree_prior=True routes selection through prior_fn: assert prior_fn
    was CALLED (record invocations in a closure) and search still returns a
    legal option index."""

def test_prior_fn_failure_falls_back_to_ucb(...):
    """prior_fn returning None caches {} on the node and the search completes
    normally (no exception, legal move returned)."""
```

- [ ] **Step 2: Run to verify failures.**

- [ ] **Step 3: Implement.**

`tree.py` — add to `Node`:

```python
    priors: dict | None = None  # policy-net PUCT priors, cached at expansion (7B)
```

`searcher.py` — `SearchConfig` additions (after the slice-6 block):

```python
    # Slice-7B: policy-net injection. use_tree_prior is consumed here (all-node
    # PUCT via prior_fn); policy_opponent/policy_rollout are consumed by
    # SearchAgent's policy wiring, carried in config so one object describes
    # the full injection state.
    use_tree_prior: bool = False
    policy_opponent: bool = False
    policy_rollout: bool = False
```

`Searcher.__init__` — add parameter `prior_fn: Callable[[Observation], dict | None] | None = None` and `self.prior_fn = prior_fn`.

`_simulate` — replace the two-way pick (`searcher.py:309-312`) with:

```python
            if node is root and priors is not None:
                sig = puct_pick(node, list(legal), priors, self.config.c_puct)
            elif self.config.use_tree_prior and self.prior_fn is not None:
                if node.priors is None:
                    node.priors = self.prior_fn(o) or {}
                sig = (puct_pick(node, list(legal), node.priors,
                                 self.config.c_puct)
                       if node.priors
                       else ucb_pick(node, list(legal), self.config.c_uct,
                                     self.rng))
            else:
                sig = ucb_pick(node, list(legal), self.config.c_uct, self.rng)
```

(One net-batch per node lifetime: `node.priors` persists on the shared tree across iterations; a failed `prior_fn` caches `{}` so it is never retried for that node.)

- [ ] **Step 4: Run the FULL suite** — `uv run pytest` → green, explicitly confirming the existing searcher/stats/regression-pin tests still pass (bit-identical-when-off is the load-bearing property).

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/search/tree.py src/ptcg/search/searcher.py tests/test_searcher.py
git commit -m "feat: all-node PUCT tree prior behind use_tree_prior flag (default off, bit-identical)"
```

---

### Task 8: SearchAgent + run_arena plumbing

**Files:**
- Modify: `src/ptcg/agents/search_agent.py`, `scripts/run_arena.py`
- Test: `tests/test_search_agent.py` (extend), `tests/test_run_arena_metrics.py` or the file that covers `AGENTS`/arg wiring (grep first; if none covers AGENTS construction, add cases to `tests/test_search_agent.py` only and note it)

**Interfaces:**
- Consumes: Tasks 3, 7.
- Produces: `SearchAgent(..., policy_weights: str | Path | None = None)`; failure ladder (load failure → one stderr warning + all three injection flags forced off); agent renamed `"search-policy"` when a policy net is active; `run_arena.py` args `--policy-weights`, `--tree-prior`, `--policy-opponent`, `--policy-rollout` and `AGENTS["search-policy"]` (v2 value evaluator default).

- [ ] **Step 1: Write failing tests** (extend `tests/test_search_agent.py`, reusing its stub-backend construction style):

```python
def test_policy_weights_missing_file_disables_flags_and_warns(capsys, ...):
    cfg = SearchConfig(use_tree_prior=True, policy_opponent=True,
                       policy_rollout=True)
    agent = SearchAgent(deck, config=cfg, backend=stub_backend,
                        policy_weights="does/not/exist.json")
    assert agent.policy is None
    assert agent.searcher.config.use_tree_prior is False
    assert agent.searcher.config.policy_opponent is False
    assert agent.searcher.config.policy_rollout is False
    assert agent.name == "search-v1"          # unchanged
    assert "policy-net load failed" in capsys.readouterr().err

def test_policy_weights_wires_injection_sites(tmp_path, ...):
    # write a minimal valid spec (reuse Task 3's _single_layer_spec shape,
    # feature_version 1 / action_feature_version 1) to tmp_path
    cfg = SearchConfig(use_tree_prior=True, policy_opponent=True,
                       policy_rollout=True)
    agent = SearchAgent(deck, config=cfg, backend=stub_backend,
                        policy_weights=weights_path)
    assert agent.policy is not None
    assert agent.searcher.opponent_policy is agent.policy
    assert agent.searcher.rollout_policy is agent.policy
    assert agent.searcher.prior_fn == agent.policy.priors_for
    assert agent.name == "search-policy"

def test_no_flags_means_no_wiring_even_with_weights(tmp_path, ...):
    agent = SearchAgent(deck, backend=stub_backend, policy_weights=weights_path)
    assert agent.policy is None and agent.name == "search-v1"
```

- [ ] **Step 2: Run to verify failures.**

- [ ] **Step 3: Implement `SearchAgent.__init__` changes** (after the existing `cfg` setup, before the `Searcher(...)` construction; add `import sys` and the PolicyNetPolicy import):

```python
        self.policy = None
        want_policy = (cfg.use_tree_prior or cfg.policy_opponent
                       or cfg.policy_rollout)
        if policy_weights is not None and want_policy:
            try:
                self.policy = PolicyNetPolicy.load(
                    policy_weights, fallback=self._policy,
                    dbs=(self.cards, self.attacks))
            except (OSError, ValueError, KeyError) as exc:
                print(f"policy-net load failed ({exc}); "
                      f"injection flags disabled", file=sys.stderr)
                cfg = replace(cfg, use_tree_prior=False,
                              policy_opponent=False, policy_rollout=False)
        policy = self._policy
        opponent = self.policy if (self.policy and cfg.policy_opponent) else policy
        rollout = self.policy if (self.policy and cfg.policy_rollout) else policy
        prior_fn = (self.policy.priors_for
                    if (self.policy and cfg.use_tree_prior) else None)
        self.searcher = Searcher(self.belief, backend or EngineBackend(),
                                 opponent_policy=opponent,
                                 rollout_policy=rollout,
                                 evaluator=evaluator if evaluator is not None
                                 else evaluate,
                                 config=cfg, prior_fn=prior_fn)
```

After the existing `search-net-v1` rename: `if self.policy is not None: self.name = "search-policy"`.

**Gate-anchor caveat — add this comment at the wiring site (spec §2):**

```python
        # NOTE: Searcher._v0_sig derives the v0-improvement gate's anchor from
        # rollout_policy. With policy_rollout on, that anchor becomes the NET's
        # move, not v0's. The pre-registered search-policy operating config
        # therefore runs gate-off (deviate_min_visits=0, deviate_value_edge=0).
```

**`run_arena.py` changes:** add the 4 args; extend `build_search_config` with `use_tree_prior=args.tree_prior, policy_opponent=args.policy_opponent, policy_rollout=args.policy_rollout`; add:

```python
V2_WEIGHTS = ROOT / "src" / "ptcg" / "search" / "value_net_weights_v2.json"

AGENTS["search-policy"] = lambda deck, a: SearchAgent(
    deck, config=build_search_config(a), time_manager=_time_manager(a),
    collect_stats=a.collect_stats,
    evaluator=(ValueNetEvaluator.load(a.net_weights) if a.net_weights
               else ValueNetEvaluator.load(V2_WEIGHTS)),
    policy_weights=a.policy_weights)
```

(Define it inside the AGENTS dict literal alongside the others, matching style; `ROOT` already exists or mirror how other paths are built — grep first.) Add a `_apply_policy_weights_name` helper mirroring `_apply_net_weights_name` (`run_arena.py:55-61`): `search-policy` + `--policy-weights policy_net_weights_mega-lucario-fighting_gen1.json` → name `search-policy-mega-lucario-fighting_gen1` so EXPERIMENTS.md rows distinguish generations; call it next to the existing helper.

- [ ] **Step 4: Run full suite** → green.

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/agents/search_agent.py scripts/run_arena.py tests/test_search_agent.py
git commit -m "feat: search-policy agent wiring — policy injection flags + failure ladder"
```

---

### Task 9: Factory integration — search-policy candidates + PolicyImprovementTrainer

**Files:**
- Modify: `src/ptcg/factory/evaluate.py`, `src/ptcg/factory/bundles.py` (mirror the `agent_kind == "search-net"` handling — grep both for `"search-net"` FIRST; if the construction pattern differs from what you expect from run_arena's, STOP and report `plan-drift`)
- Modify: `src/ptcg/factory/trainers.py` (add `PolicyImprovementTrainer`)
- Create: `scripts/run_policy_cycle.py`
- Test: `tests/test_factory_trainers.py` (extend), `tests/test_factory_evaluate.py` / `tests/test_factory_bundles.py` (extend, mirroring their search-net cases)

**Interfaces:**
- Consumes: `Trainer` Protocol (`daemon.py:17-25`), `run_daemon` (`daemon.py:53`), `Candidate.create`/`next_version` (`candidates.py`), Tasks 5–6 CLI surfaces.
- Produces: factory can evaluate + bundle `agent_kind="search-policy"` candidates whose `agent_config` carries `{"search_budget_ms","rollout_depth","net_weights","policy_weights","tree_prior","policy_opponent","policy_rollout","gate":"off"}`; `PolicyImprovementTrainer` (protocol-conformant); `scripts/run_policy_cycle.py --cycles 1 [--deck <csv>] [--games N]` driving `run_daemon` with **dedicated state file `experiments/factory/policy_daemon_state.json`** (NEVER the 7A `daemon_state.json` — that file's `next_cycle=2` belongs to PerDeckNetTrainer).

- [ ] **Step 1: Write failing tests** — mirror `tests/test_factory_trainers.py`'s stub-runner pattern:

```python
def test_policy_trainer_gen1_no_prior_weights(tmp_path):
    # stub runner records argv; data_dir under tmp_path
    t = PolicyImprovementTrainer(deck=Path("src/ptcg/decks/candidates/mega-lucario-fighting.csv"),
                                 data_dir=tmp_path / "data",
                                 weights_dir=tmp_path / "w", runner=stub)
    out = t.prepare_data(0)
    argv = stub.calls[0]
    assert "--policy-targets" in argv and "--gate" in argv
    assert "--policy-weights" not in argv          # gen-1: no prior net
    assert out.parent.exists()                     # virgin-directory contract

def test_policy_trainer_gen2_injects_previous_weights(tmp_path):
    t = ...same...
    prev = t._policy_weights_path(1)
    prev.parent.mkdir(parents=True, exist_ok=True)
    prev.write_text("{}", encoding="utf-8")
    t.prepare_data(1)
    argv = stub.calls[0]
    assert "--policy-weights" in argv and str(prev) in argv
    assert "--tree-prior" in argv and "--policy-opponent" in argv

def test_policy_trainer_register_shape(tmp_path):
    cand = t.export_and_register(tmp_path / "w" / "policy_net_weights_x_gen1.json",
                                 0, [])
    assert cand.agent_kind == "search-policy"
    assert cand.agent_config["gate"] == "off"
    assert cand.agent_config["net_weights"].endswith("value_net_weights_v2.json")
    assert cand.version == "v0.1" and cand.novel_axis is True

def test_policy_trainer_virgin_data_dir(tmp_path):
    # data_dir points at a path whose PARENT does not exist either;
    # prepare_data must mkdir(parents=True) before invoking the script
    t = PolicyImprovementTrainer(deck=..., data_dir=tmp_path / "a" / "b" / "c",
                                 weights_dir=tmp_path / "w", runner=stub)
    out = t.prepare_data(0)
    assert out.parent.is_dir()
```

Plus evaluate/bundles cases mirroring the existing search-net tests (assert a search-policy candidate constructs an agent with policy weights wired / bundles without error). Copy their fixture shapes.

- [ ] **Step 2: Run to verify failures.**

- [ ] **Step 3: Implement `PolicyImprovementTrainer`** in `factory/trainers.py`:

```python
@dataclass
class PolicyImprovementTrainer:
    """7B occupant of the Trainer protocol: expert iteration. Cycle k trains
    generation k+1; prepare_data(k) injects generation k's net (if its weights
    file exists) into the self-play searchers, closing the improvement loop."""
    deck: Path
    games_per_cycle: int = 400
    epochs: int = 40
    data_dir: Path = field(default_factory=lambda: ROOT / "experiments" / "data" / "slice7b")
    weights_dir: Path = field(default_factory=lambda: ROOT / "src" / "ptcg" / "search")
    value_weights: str = "src/ptcg/search/value_net_weights_v2.json"
    priority: float = 0.6
    runner: Callable = subprocess.run
    name: str = "policy-improvement"

    @property
    def slug(self) -> str:
        return Path(self.deck).stem

    def _policy_weights_path(self, gen: int) -> Path:
        return Path(self.weights_dir) / f"policy_net_weights_{self.slug}_gen{gen}.json"

    def _run(self, args: list[str]) -> None:
        proc = self.runner([sys.executable, *args], cwd=ROOT,
                           timeout=SUBPROCESS_TIMEOUT_S)
        code = getattr(proc, "returncode", 0)
        if code != 0:
            raise RuntimeError(f"{args[0]} exited {code}")

    def prepare_data(self, cycle: int) -> Path:
        out = Path(self.data_dir) / f"{self.slug}-policy-c{cycle}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        args = ["scripts/generate_training_data.py", "--agent", "search",
                "--policy-targets", "--gate", "off",
                "--decks", str(self.deck),
                "--games-per-pairing", str(self.games_per_cycle),
                "--seed", str(7 + cycle), "--out", str(out)]
        prev = self._policy_weights_path(cycle)  # gen k, written by cycle k-1
        if prev.exists():
            args += ["--policy-weights", str(prev), "--tree-prior",
                     "--policy-opponent", "--policy-rollout"]
        self._run(args)
        return out

    def train(self, data_path: Path, cycle: int) -> Path:
        out = self._policy_weights_path(cycle + 1)
        self._run(["scripts/train_policy_net.py", "--data", str(data_path),
                   "--out", str(out), "--epochs", str(self.epochs)])
        return out

    def export_and_register(self, weights_path: Path, cycle: int,
                            candidates: list[Candidate]) -> Candidate:
        name = f"{self.slug}-policyloop"
        version = next_version(candidates, name)
        rel = Path(weights_path)
        if rel.is_absolute():
            try:
                rel = rel.relative_to(ROOT)
            except ValueError:
                pass
        return Candidate.create(
            name=name, version=version,
            deck=f"src/ptcg/decks/candidates/{self.slug}.csv",
            agent_kind="search-policy",
            agent_config={"search_budget_ms": 200, "rollout_depth": 0,
                          "net_weights": self.value_weights,
                          "policy_weights": rel.as_posix(),
                          "tree_prior": True, "policy_opponent": True,
                          "policy_rollout": True, "gate": "off"},
            provenance=f"daemon:{self.name}:cycle{cycle}",
            priority=self.priority,
            novel_axis=version == "v0.1")
```

(Cycle/generation arithmetic, hand-verified: `train(_, cycle)` writes `gen{cycle+1}`; `prepare_data(cycle)` looks for `gen{cycle}`. Cycle 0 → `gen0` absent → plain v0-search, trains `gen1`. Cycle 1 → `gen1` present → injected, trains `gen2`. ✓)

**`scripts/run_policy_cycle.py`** — thin synchronous driver:

```python
"""Run N policy-improvement daemon cycles synchronously (gen-1 in-session;
nightly reuse). Dedicated state file — NEVER share daemon_state.json with the
7A per-deck trainer."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptcg.factory.daemon import run_daemon  # noqa: E402
from ptcg.factory.trainers import PolicyImprovementTrainer  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cycles", type=int, default=1)
    p.add_argument("--deck",
                   default="src/ptcg/decks/candidates/mega-lucario-fighting.csv")
    p.add_argument("--games", type=int, default=400)
    p.add_argument("--epochs", type=int, default=40)
    p.add_argument("--ledger",
                   default="experiments/factory/candidates.json")
    p.add_argument("--state",
                   default="experiments/factory/policy_daemon_state.json")
    p.add_argument("--stop-file",
                   default="experiments/factory/PAUSE")
    args = p.parse_args()
    trainer = PolicyImprovementTrainer(deck=Path(args.deck),
                                       games_per_cycle=args.games,
                                       epochs=args.epochs)
    state = run_daemon(trainer, args.cycles, ROOT / args.ledger,
                       ROOT / args.state, stop_path=ROOT / args.stop_file)
    print(f"next_cycle={state.next_cycle} completed={state.completed}")


if __name__ == "__main__":
    main()
```

Evaluate/bundles: mirror the search-net branches, translating `agent_config` into a `SearchAgent(..., policy_weights=..., config=SearchConfig(rollout_depth=..., deviate_min_visits=0, deviate_value_edge=0.0, use_tree_prior=..., policy_opponent=..., policy_rollout=...))` — apply `gate=="off"` → the two deviate zeros. Follow the existing code's structure exactly.

- [ ] **Step 4: Run full suite** → green.

- [ ] **Step 5: Commit**

```bash
git add src/ptcg/factory/trainers.py src/ptcg/factory/evaluate.py src/ptcg/factory/bundles.py scripts/run_policy_cycle.py tests/test_factory_trainers.py tests/test_factory_evaluate.py tests/test_factory_bundles.py
git commit -m "feat: PolicyImprovementTrainer + search-policy candidate support + cycle driver"
```

---

### Task 10: Throughput gate (pre-registered gate 2) — runs are ORCHESTRATOR-OWNED

**Scope for the dispatched implementer: setup + harvest ONLY. The orchestrator launches the arena runs in its own background shells and hands the log paths back. Do not wait on, poll, or Monitor any process.**

**Files:**
- No new source. Deliverable: a harvest note appended to `experiments/EXPERIMENTS.md` (run_arena auto-logs rows; add the gate-decision line) and the decision reported.

**Procedure (orchestrator):**

- [ ] **Step 1:** Generate random weights: `uv run python scripts/make_random_policy_weights.py --out experiments/data/slice7b/policy_rand.json`
- [ ] **Step 2:** Clean machine (no concurrent pytest/training — contention rule). Run FULL injection, 30 games:

```bash
uv run python scripts/run_arena.py --agent-a search-policy --agent-b heuristic \
  --deck-a src/ptcg/decks/candidates/mega-lucario-fighting.csv \
  --deck-b src/ptcg/decks/candidates/mega-lucario-fighting.csv \
  --games 30 --collect-stats --rollout-depth 0 \
  --deviate-min-visits 0 --deviate-value-edge 0.0 \
  --policy-weights experiments/data/slice7b/policy_rand.json \
  --tree-prior --policy-opponent --policy-rollout \
  --notes "7B gate2 throughput: FULL injection, random weights"
```

- [ ] **Step 3:** Same command with only `--tree-prior` (drop the other two flags), notes "7B gate2 throughput: REDUCED (prior-only)".
- [ ] **Step 4 (harvest, implementer or orchestrator):** read the two runs' iteration summaries (the `--collect-stats` summary reports mean iterations/move — the same channel that measured ~54 iters/move in Slice 6). Real games sample the full workload variation (early/mid/late decisions) by construction, satisfying the sampling-breadth rule.
- [ ] **Step 5 (decision, pre-registered):** operating config = FULL injection if its mean iterations/move ≥ 15; else REDUCED if ≥ 15; if even REDUCED < 15, STOP the slice and surface to Brad (design assumption broken). Record the measured numbers (not just PASS/FAIL) in EXPERIMENTS.md and plan.md.

---

### Task 11: Generation-1 cycle + sanity gate (gate 1) — runs are ORCHESTRATOR-OWNED

**Scope for any dispatched implementer: setup + harvest only.**

- [ ] **Step 1 (probe):** `uv run python scripts/generate_training_data.py --agent search --policy-targets --gate off --decks src/ptcg/decks/candidates/mega-lucario-fighting.csv --games-per-pairing 5 --out experiments/data/slice7b/_probe.jsonl` — confirm rows appear, `opts`/`n` lengths match per row, and note s/game. (Slice-6 measured 22.7–32 s/game for search self-play; if the probe says >45 s/game, reduce Step 2 to 400 games and say so.)
- [ ] **Step 2 (gen-1 data, ~4–5h background, orchestrator-owned):** same command with `--games-per-pairing 600 --out experiments/data/slice7b/policy_gen1_data.jsonl --seed 7`. Verify at completion: file exists, nonzero rows (expect roughly 600 games × ~10 recorded decisions ≈ 5–7k rows; the exact count depends on how many decisions are contested 1-of-1s), zero-error line in stdout. **Artifact check is the success signal, not shell exit** (bg-exit-masking rule).
- [ ] **Step 3 (train):** `uv run python scripts/train_policy_net.py --data experiments/data/slice7b/policy_gen1_data.jsonl --out src/ptcg/search/policy_net_weights_mega-lucario-fighting_gen1.json --epochs 40` — record printed `VAL agreement`.
- [ ] **Step 4 (gate 1, pre-registered):** VAL agreement ≥ 0.60 → PASS, proceed. < 0.60 → STOP: the net cannot imitate the search it's meant to accelerate; diagnose (feature insufficiency vs. noisy targets) before any arena spend. Record the measured value.
- [ ] **Step 5:** Commit the weights file + EXPERIMENTS.md rows:

```bash
git add src/ptcg/search/policy_net_weights_mega-lucario-fighting_gen1.json experiments/EXPERIMENTS.md
git commit -m "feat: gen-1 policy net (expert-iteration cycle 0) + gate measurements"
```

(Gen-1 here intentionally runs the same script surface the trainer shells out to — it validates PolicyImprovementTrainer's commands against reality before the daemon runs them unattended. Task 9's `run_policy_cycle.py` is used from gen-2 onward; if you prefer to run gen-1 THROUGH `run_policy_cycle.py --cycles 1 --games 600`, that is equivalent and also registers the candidate — do that if Task 9 landed first, and say which path ran.)

---

### Task 12: Generation-1 arena probe (gate 3) — runs are ORCHESTRATOR-OWNED

- [ ] **Step 1:** Clean machine. 200 games, operating config from Task 10's decision (FULL shown):

```bash
uv run python scripts/run_arena.py --agent-a search-policy --agent-b heuristic \
  --deck-a src/ptcg/decks/candidates/mega-lucario-fighting.csv \
  --deck-b src/ptcg/decks/candidates/mega-lucario-fighting.csv \
  --games 200 --rollout-depth 0 --deviate-min-visits 0 --deviate-value-edge 0.0 \
  --policy-weights src/ptcg/search/policy_net_weights_mega-lucario-fighting_gen1.json \
  --tree-prior --policy-opponent --policy-rollout \
  --notes "7B gate3 gen-1 probe vs v0, clean machine, operating config per gate2"
```

- [ ] **Step 2 (decision, pre-registered):** point estimate ≥ 0.50 OR strictly greater than the previous generation's probe (gen-1 has no predecessor; the ≥ 0.50 arm applies) → proceed to overnight gens 2–3. Below → still proceed unless this is generation 3 (kill rule evaluates after gen 3). If point estimate ≥ 0.55 → schedule the FINAL gate: replicated 2×300 vs v0 (identical command, `--games 300`, run twice, clean machine, report every run, pooled ≥ 0.55 = PASS).
- [ ] **Step 3:** Record win rate + CI in EXPERIMENTS.md (auto) and the decision in plan.md.

---

### Task 13: Nightly wiring for generations 2–3 + docs

**Files:**
- Modify: whatever invokes the nightly factory work (grep for the scheduled-task entry script — `Get-ScheduledTask ptcg-factory-nightly`'s action, and/or `scripts/*nightly*` / `docs/factory-operations.md`). If the nightly structure differs from a simple "add a second invocation" shape, STOP and report `plan-drift` with what you found.
- Modify: `docs/factory-operations.md` (document the policy loop: state file, weights naming, generation arithmetic, PAUSE behavior)
- Create: `experiments/ANALYSIS-slice7b-policy-improvement.md` (scaffold: hypothesis, pre-registered criteria table verbatim from the spec, gate-1/2/3 measured values so far, generations table to be filled as nights complete)

**Interfaces:**
- Consumes: `scripts/run_policy_cycle.py` (Task 9).
- Produces: nightly runs `uv run python scripts/run_policy_cycle.py --cycles 1 --games 400` after the existing factory cycle (sequenced AFTER, not concurrent — the factory eval arena series and policy self-play are both CPU-heavy; concurrent execution violates the contention rule for any factory eval treated as gate evidence). Honors the existing `PAUSE` kill switch via `--stop-file`.

- [ ] Steps: grep nightly entry → add sequenced invocation → update docs → write analysis scaffold → run full suite → commit:

```bash
git add <nightly-entry-file> docs/factory-operations.md experiments/ANALYSIS-slice7b-policy-improvement.md
git commit -m "feat: nightly policy-improvement cycles (gens 2-3) + slice 7B analysis scaffold"
```

---

## Verification (slice-level, before Finish)

1. `uv run pytest` green; `uv run pytest -m slow` for the acceptance series if any slow tests were touched.
2. Smoke Test Ladder rung 2: artifacts verified for every orchestrator-owned run (data file row counts, weights JSON parity line, EXPERIMENTS.md rows present) — never shell exit alone.
3. Bit-identical check at slice level: `uv run python scripts/run_arena.py --agent-a search-net --agent-b heuristic ... --games 20` (no new flags) matches pre-slice behavior expectations; the regression-pin test (`tests/test_regression_pin.py`) stays green throughout.
4. Ladder identity files (`src/ptcg/submission_main.py`, `src/ptcg/agents/current.py`) unchanged: `git diff master...HEAD -- src/ptcg/submission_main.py src/ptcg/agents/current.py` is empty (unless the final gate PASSED and Brad approved the switch).

## Self-Review Notes (plan author)

- Spec coverage: S1 loop/net → T1/T3/T4/T6; S2 injection/timing → T7/T8/T10; S3 data/training → T2/T4/T5/T6/T11; S4 gates → T10/T11/T12 + kill/final criteria in T12; S5 factory → T9/T13; testing reqs (bit-identical, virgin-dir, golden vectors, protocol conformance) → T7/T9/T3/T6/T9.
- Type consistency: `root_visits_by_option: dict[int,int]` produced T2 = consumed T4; `PolicyNetPolicy.load(path, fallback, dbs)` produced T3 = consumed T8; `_policy_weights_path(gen)` naming = T11's `policy_net_weights_mega-lucario-fighting_gen1.json` (slug `mega-lucario-fighting`, gen 1 ✓); trainer cycle arithmetic verified inline.
- Known intentional deviations from existing code style: none; new modules mirror `value_net.py`/`trajectory.py`/`trainers.py` shapes.
