# Slice 5 — Search-Architecture Investigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Instrument the ISMCTS searcher to measure why search-v1 sits at parity with heuristic-v0 (does the v0-improvement gate ever fire?), diagnose the binding bottleneck across four experiments, apply evidence-ordered fixes, and flip the ladder submission to the search agent ONLY on a replicated ≥0.55 win-rate gate — otherwise ship a rigorous documented dead-end.

**Architecture:** Section 1 adds a per-decision `SearchStats` record on the `Searcher` with ZERO behavior change (a seed-determinism guard test proves it), aggregated across a game by `SearchAgent` and summarized into series-level metrics by `run_arena.py` (printed, appended to the `Notes` cell of `experiments/EXPERIMENTS.md`, and dumped to a per-series sidecar JSON for distribution analysis). Section 2 runs four diagnosis experiments (D1 budget scan, D2 match-clock math, D3 gate-off ablation, D4 determinization-noise probe) against the instrumented searcher. Section 3 applies fixes cheapest-first (F1 gate retune, F2 budget raise, F3 final-move rule; F4 PUCT prior is a conditional re-plan stub), each validated by its own ≥200-game arena series. Section 4 is the replicated acceptance gate that decides whether `src/ptcg/agents/current.py` flips to the search agent.

**Tech Stack:** Python 3.12 + uv; the vendored `cg` engine SDK (ctypes wrapper over the native forward model, `search_begin`/`search_step`/`search_end`); pytest (tests live next to code, `uv run pytest`). No new third-party dependencies. Bundle inference stays pure-stdlib.

**Spec:** `docs/superpowers/specs/2026-07-10-slice5-search-architecture-investigation-design.md`

## Global Constraints

- **Repo path contains a space:** `C:\Users\Brad\Dev Folder\Product\ptcg-ai-battle-challenge-strategy` — ALWAYS quote paths. Branch: `feature/slice5-search-architecture` (already checked out; baseline 129 tests green).
- **10-minute match clock, timeout = automatic loss.** Any per-move budget raise (F2) must stay strictly inside the provably-safe envelope computed in D2 with a ≥2× safety factor for unknown Kaggle hardware. The final ship decision requires a timeout-safety validation under the REAL clock.
- **Ladder stays heuristic-v0 absent a replicated PASS.** `src/ptcg/agents/current.py` (the single source of truth for the ladder identity), `src/ptcg/submission_main.py`, and the regression pin change ONLY on a replicated gate PASS (Section 4). Otherwise the slice ships a documented dead-end and those files are untouched.
- **Competition materials are read-only.** `pokemon-tcg-ai-battle/` is licensed reference — never modify it.
- **Every Python text-file write passes `encoding="utf-8"`** (`open(...)`, `Path.write_text(...)`, `Path.read_text(...)`). On Windows the default cp1252 codec truncates-then-crashes on non-ASCII, which once wiped `EXPERIMENTS.md`. `tests/test_experiments_append.py` guards this at each call's own bracket depth — keep it green and extend it to any new script that appends to `EXPERIMENTS.md`.
- **Orchestrator owns long-running arena processes.** Steps marked `ORCHESTRATOR-RUN:` are launched by the orchestrator in its OWN background shell. A dispatched implementer/agent must NEVER sit waiting on a multi-minute run (Slice-4 lesson, recurred 5×). Every arena-run task is structured: (a) agent sets up and validates the exact command with a tiny dry run, (b) ORCHESTRATOR runs the full series, (c) agent harvests the output/sidecar and writes the analysis.
- **Stochastic gates near their bar are replicated, never single-run.** Section 4 copies `.claude/rules/stochastic-gate-replication.md`'s recipe verbatim; report ALL runs, not just the passing one.
- **Plan code is reference, not gospel.** Before writing code, grep each cited landmark (function names, line refs, config field names). If a landmark doesn't match, STOP and report `plan-drift`.
- **Hand-verify any plan-authored test constant** against the same task's own arithmetic before transcribing (`.claude/rules/plan-test-arithmetic-sanity.md`). Each such step below names the `python -c` one-liner that confirms the number.
- **Any aggregation/metric code processing N = decisions × games × reruns must carry a big-O comment** and the FIRST production-scale run is itself a test (`.claude/rules/plan-test-arithmetic-sanity.md` scale corollary + global CLAUDE.md scale-blind-plan-code lesson).
- **Card/attack facts come from executable engine-DB checks** (`.claude/rules/verify-game-data-claims.md`); `all_attack()` is a 0-based list of 1-based `attackId`s — build `{a.attackId: a}` maps, never index the list by id.
- **`uv run pytest`** (never `--run`, never bare `pytest`). Slow suite: `uv run pytest -m slow` — only when a task says so.
- **Stage by explicit path** (`git add <files>`), never `git add .`. Keep untracked `Drafts/` untracked. On `index.lock` contention, wait 2s and retry up to 3×.

---

## Phase A — Instrumentation (Tasks 1–4). No behavior change; the seed-determinism guard is the proof.

### Task 1: `SearchStats` record + gate refactor on the Searcher

**Files:**
- Modify: `src/ptcg/search/searcher.py` (add `SearchStats`, `collect_stats` config field, `_v0_sig`/`_gate` helpers, `last_stats`, populate in `search()`)
- Test: `tests/test_searcher.py` (extend — existing `_anchor` tests must stay green)

**Interfaces:**
- Consumes: existing `Node` (`.visits`, `.value_sum`, `.mean`), `action_signature`, `SearchConfig`.
- Produces:
  - `SearchStats` dataclass with fields: `iterations: int`, `root_children: int`, `top_child_visits: int`, `top_child_value: float`, `v0_sig: ActionSig | None`, `search_sig: ActionSig | None`, `most_visited_sig: ActionSig | None`, `deviated: bool`, `gate_blocked: bool`, `begin_failures: int`, `step_failures: int`.
  - `Searcher.last_stats: SearchStats | None` — set by `search()` when `config.collect_stats` is True, else `None`.
  - `SearchConfig.collect_stats: bool = False`.
  - `Searcher._v0_sig(obs) -> ActionSig | None` and `Searcher._gate(obs, visited, most_visited, v0_sig) -> ActionSig`. `_anchor` is retained as a thin shim (`return self._gate(obs, visited, best, self._v0_sig(obs))`) so its existing unit tests are unchanged.

**Design note (why this keeps behavior identical):** the current `search()` calls `_anchor(obs, visited, best)`, which internally computes v0's move once. The refactor computes `v0_sig` once via `_v0_sig`, feeds it to a pure `_gate`, and derives stats from the SAME `most_visited`/`v0_sig`/`chosen` values — no extra v0-policy call in the hot path, no `self.rng` use, so the move sequence is byte-identical whether stats are on or off. `ActionSig = tuple` is imported from `ptcg.search.tree` (add it to the existing import).

- [ ] **Step 1: Write the failing behavior-preserving + stats tests** (append to `tests/test_searcher.py`)

```python
from ptcg.search.searcher import SearchStats  # add to existing imports at top of file


def test_gate_matches_legacy_anchor_across_cases():
    """_gate(obs, visited, most_visited, v0_sig) reproduces _anchor's decisions."""
    s, obs, sig0, sig1 = _anchor_fixture(
        SearchConfig(deviate_min_visits=10, deviate_value_edge=0.1))
    # undersampled challenger -> stay with v0
    visited = {sig0: Node(visits=15, value_sum=7.5),
               sig1: Node(visits=5, value_sum=4.5)}
    assert s._gate(obs, visited, sig1, s._v0_sig(obs)) == sig0
    # confident + better challenger -> deviate
    visited = {sig0: Node(visits=15, value_sum=7.5),
               sig1: Node(visits=20, value_sum=14.0)}
    assert s._gate(obs, visited, sig1, s._v0_sig(obs)) == sig1


def test_v0_sig_none_when_not_single_select():
    s, obs, _, _ = _anchor_fixture(SearchConfig())
    multi = make_obs(make_select([opt(100), opt(200)], min_count=1, max_count=2),
                     make_state())
    assert s._v0_sig(multi) is None  # gate cannot apply to a multi-count select


def test_stats_off_by_default():
    obs = make_obs(sel1([opt(100), opt(200)]), make_state())
    s = searcher(FakeBackend())  # config default collect_stats=False
    s.search(obs, deadline=time.perf_counter() + 5.0)
    assert s.last_stats is None


def test_stats_populated_when_enabled():
    obs = make_obs(sel1([opt(100), opt(200)]), make_state())
    s = Searcher(FakeBelief(), FakeBackend(), opponent_policy=lambda o: [0],
                 rollout_policy=lambda o: [0],
                 config=SearchConfig(max_iterations=40, seed=0, collect_stats=True))
    s.search(obs, deadline=time.perf_counter() + 5.0)
    st = s.last_stats
    assert isinstance(st, SearchStats)
    assert st.iterations == s.iterations_run
    assert st.root_children >= 1
    assert st.top_child_visits >= 1
    assert st.search_sig is not None
    # FakeBackend: option A(100) at real index 0 wins; v0 (rollout_policy->[0]) also
    # picks A, so search agrees with v0 -> no deviation, not gate-blocked.
    assert st.deviated is False
    assert st.gate_blocked is False


def test_stats_flag_does_not_change_move_sequence():
    """Seed-determinism guard: identical returned moves with stats on vs off.
    NOT a self-comparison of one code path — two independently constructed
    searchers (fresh FakeBackend each) driven by the same seed."""
    def run(collect):
        moves = []
        for _ in range(6):
            s = Searcher(FakeBelief(), FakeBackend(), opponent_policy=lambda o: [0],
                         rollout_policy=lambda o: [0],
                         config=SearchConfig(max_iterations=25, seed=0,
                                             collect_stats=collect))
            obs = make_obs(sel1([opt(100), opt(200)]), make_state())
            moves.append(s.search(obs, deadline=time.perf_counter() + 5.0))
        return moves
    assert run(collect=True) == run(collect=False)


def test_gate_blocked_flag_set_when_gate_vetoes_deviation():
    """most-visited != v0 but thresholds veto -> chosen==v0, gate_blocked=True.
    Drive it through the stats path with a hand-built visited dict via _make_stats
    is not exposed; instead assert the flag semantics on _gate + a direct build."""
    s, obs, sig0, sig1 = _anchor_fixture(
        SearchConfig(deviate_min_visits=10, deviate_value_edge=0.1, collect_stats=True))
    visited = {sig0: Node(visits=15, value_sum=7.5),
               sig1: Node(visits=5, value_sum=4.5)}  # challenger undersampled
    v0_sig = s._v0_sig(obs)
    chosen = s._gate(obs, visited, sig1, v0_sig)
    assert chosen == sig0 and v0_sig == sig0  # vetoed back to v0
    # most_visited (sig1) != v0 (sig0) and chosen == v0 -> gate_blocked semantics
    assert (sig1 != v0_sig) and (chosen == v0_sig)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_searcher.py -q`
Expected: FAIL — `ImportError: cannot import name 'SearchStats'` / `AttributeError: 'Searcher' object has no attribute 'last_stats'` / `_v0_sig` not defined.

- [ ] **Step 3: Implement `SearchStats`, the config flag, the gate refactor, and stats population** in `src/ptcg/search/searcher.py`

Add `ActionSig` to the tree import and `math` is not needed here. Update the import line:

```python
from ptcg.search.tree import ActionSig, Node, action_signature, ucb_pick
```

Add the dataclass just above `SearchConfig`:

```python
@dataclass
class SearchStats:
    """Per-decision provenance. Read-only view of what search() already computed;
    populating it changes NO move choice (guard: test_stats_flag_does_not_change_move_sequence)."""
    iterations: int
    root_children: int
    top_child_visits: int
    top_child_value: float
    v0_sig: ActionSig | None
    search_sig: ActionSig | None
    most_visited_sig: ActionSig | None
    deviated: bool
    gate_blocked: bool
    begin_failures: int
    step_failures: int
```

Add `collect_stats: bool = False` as the last field of `SearchConfig`:

```python
    deviate_min_visits: int = 20
    deviate_value_edge: float = 0.12
    collect_stats: bool = False  # populate Searcher.last_stats each search(); zero behavior change
```

In `Searcher.__init__`, add after `self._deadline = float("inf")`:

```python
        self.last_stats: SearchStats | None = None
```

Rewrite the tail of `search()` (from `visited = ...` onward) to compute v0_sig once, gate once, and build stats:

```python
        visited = {s: n for s, n in root.children.items() if n.visits > 0}
        self.last_stats = None
        if not visited:
            return None
        most_visited = max(visited, key=lambda s: visited[s].visits)
        v0_sig = self._v0_sig(obs)
        chosen = self._gate(obs, visited, most_visited, v0_sig)
        if cfg.collect_stats:
            top = visited[most_visited]
            self.last_stats = SearchStats(
                iterations=self.iterations_run,
                root_children=len(visited),
                top_child_visits=top.visits,
                top_child_value=top.mean,
                v0_sig=v0_sig,
                search_sig=chosen,
                most_visited_sig=most_visited,
                deviated=(v0_sig is not None and chosen != v0_sig),
                gate_blocked=(v0_sig is not None and most_visited != v0_sig
                              and chosen == v0_sig),
                begin_failures=self.begin_failures,
                step_failures=self.step_failures,
            )
        idx = root_index.get(chosen)
        return None if idx is None else [idx]
```

Replace `_anchor` with the shim + extract `_v0_sig` and `_gate` (keep the docstring on `_gate`):

```python
    def _v0_sig(self, obs: Observation) -> ActionSig | None:
        """v0's chosen signature for a 1-of-1 select, or None if the gate can't apply."""
        sel = obs.select
        if sel.minCount != 1 or sel.maxCount != 1 or len(sel.option) < 2:
            return None
        try:
            choice = self.rollout_policy(obs)
        except (ValueError, RuntimeError):
            return None
        if not choice or len(choice) != 1:
            return None
        return action_signature(sel, (choice[0],))

    def _gate(self, obs: Observation, visited: dict, most_visited, v0_sig):
        """v0-improvement gate (value-based): keep v0's move unless the most-visited
        challenger is well-sampled AND evaluates meaningfully above v0's move. v0 already
        beats random 93.8%; search must EARN a deviation, not replace v0 on a noisy tie
        (argmax over few-sample estimates otherwise picks the luckiest, not the best)."""
        cfg = self.config
        if cfg.deviate_min_visits <= 0 and cfg.deviate_value_edge <= 0.0:
            return most_visited
        if v0_sig is None or most_visited == v0_sig:
            return most_visited
        best_node = visited[most_visited]
        v0_mean = visited[v0_sig].mean if v0_sig in visited else 0.5
        if (best_node.visits >= cfg.deviate_min_visits
                and best_node.mean >= v0_mean + cfg.deviate_value_edge):
            return most_visited
        return v0_sig

    def _anchor(self, obs: Observation, visited: dict, best):
        """Back-compat shim for existing unit tests; search() no longer calls this."""
        return self._gate(obs, visited, best, self._v0_sig(obs))
```

- [ ] **Step 4: Run the tests to verify they pass** (new + all pre-existing searcher tests)

Run: `uv run pytest tests/test_searcher.py -q`
Expected: PASS — all new tests green AND the pre-existing `test_anchor_*`, `test_picks_winning_move_*`, deadline/failure tests unchanged-green.

- [ ] **Step 5: Run the full fast suite to confirm zero regression**

Run: `uv run pytest -q`
Expected: PASS, 129 prior + new tests, 0 failures.

- [ ] **Step 6: Commit**

```bash
git add "src/ptcg/search/searcher.py" "tests/test_searcher.py"
git commit -m "feat: per-decision SearchStats + gate refactor on Searcher (no behavior change)"
```

---

### Task 2: Agent-level aggregation + pure series-metrics summarizer

**Files:**
- Modify: `src/ptcg/agents/search_agent.py` (accumulate `decision_stats`, enable stats)
- Create: `src/ptcg/arena/search_metrics.py` (pure aggregation, no engine import)
- Test: `tests/test_search_agent.py` (extend), `tests/test_search_metrics.py` (new)

**Interfaces:**
- Consumes: `SearchStats` (Task 1), `SearchConfig.collect_stats`.
- Produces:
  - `SearchAgent.decision_stats: list[SearchStats]` — one entry per search() decision across the agent's whole lifetime (the series). Populated only when the searcher was built with `collect_stats=True`.
  - `SearchAgent(..., collect_stats: bool = False)` constructor param that sets `config.collect_stats` and starts the accumulator.
  - `search_metrics.summarize(stats: list[SearchStats], per_game_counts: list[int]) -> DecisionSummary` and `DecisionSummary` dataclass with: `n_decisions`, `n_gated`, `deviation_rate`, `gate_blocked_rate`, `mean_iterations`, `decisions_per_game_mean`, `begin_failures`, `step_failures`.
  - `search_metrics.format_note(summary: DecisionSummary) -> str` — a compact `Notes`-cell suffix.

- [ ] **Step 1: Write the failing summarizer tests** (`tests/test_search_metrics.py`)

Arithmetic hand-verified before transcription — confirm with:
`uv run python -c "print(165/8, 2/5, 1/5, 8/2)"` → `20.625 0.4 0.2 4.0`.

```python
from ptcg.search.searcher import SearchStats
from ptcg.arena.search_metrics import summarize, format_note


def _stat(iters, v0, mv, chosen, begin=0, step=0):
    # sigs are opaque tuples; use small int-tuples as stand-ins
    return SearchStats(iterations=iters, root_children=2, top_child_visits=iters,
                       top_child_value=0.5, v0_sig=v0, search_sig=chosen,
                       most_visited_sig=mv,
                       deviated=(v0 is not None and chosen != v0),
                       gate_blocked=(v0 is not None and mv != v0 and chosen == v0),
                       begin_failures=begin, step_failures=step)


def test_summarize_rates_over_gated_decisions():
    stats = [
        _stat(10, (0,), (1,), (1,)),   # gated, deviated
        _stat(20, (0,), (1,), (1,)),   # gated, deviated
        _stat(30, (0,), (1,), (0,)),   # gated, gate_blocked (wanted 1, vetoed to 0)
        _stat(40, (0,), (0,), (0,)),   # gated, agreed with v0
        _stat(50, (0,), (0,), (0,)),   # gated, agreed with v0
        _stat(5, None, (0,), (0,)),    # non-gated (multi-count / forced)
        _stat(5, None, (0,), (0,)),    # non-gated
        _stat(5, None, (0,), (0,)),    # non-gated
    ]
    per_game_counts = [3, 5]  # two games, 8 decisions total
    s = summarize(stats, per_game_counts)
    assert s.n_decisions == 8
    assert s.n_gated == 5
    assert s.deviation_rate == 0.4        # 2/5
    assert s.gate_blocked_rate == 0.2     # 1/5
    assert s.mean_iterations == 165 / 8   # 20.625
    assert s.decisions_per_game_mean == 4.0  # 8/2


def test_summarize_empty_is_safe():
    s = summarize([], [])
    assert s.n_decisions == 0 and s.n_gated == 0
    assert s.deviation_rate == 0.0 and s.gate_blocked_rate == 0.0
    assert s.mean_iterations == 0.0 and s.decisions_per_game_mean == 0.0


def test_format_note_is_compact_and_pipe_free():
    s = summarize([_stat(10, (0,), (1,), (1,))], [1])
    note = format_note(s)
    assert "|" not in note  # must not corrupt the EXPERIMENTS.md table
    assert "dev=" in note and "gate_blk=" in note and "iters=" in note
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_search_metrics.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ptcg.arena.search_metrics'`.

- [ ] **Step 3: Implement the pure summarizer** (`src/ptcg/arena/search_metrics.py`)

```python
"""Pure series-level aggregation of per-decision SearchStats. No engine import,
so it stays unit-testable and import-cheap. Complexity is O(n_decisions): a single
linear pass over the flat decision list (n = decisions/game * games, ~hundreds to
low-thousands per series), plus O(n_games) over per-game counts."""
from __future__ import annotations

from dataclasses import dataclass

from ptcg.search.searcher import SearchStats


@dataclass
class DecisionSummary:
    n_decisions: int
    n_gated: int
    deviation_rate: float
    gate_blocked_rate: float
    mean_iterations: float
    decisions_per_game_mean: float
    begin_failures: int
    step_failures: int


def summarize(stats: list[SearchStats], per_game_counts: list[int]) -> DecisionSummary:
    n = len(stats)                                        # O(n) single pass below
    gated = [s for s in stats if s.v0_sig is not None]
    n_gated = len(gated)
    deviations = sum(1 for s in gated if s.deviated)
    blocked = sum(1 for s in gated if s.gate_blocked)
    total_iters = sum(s.iterations for s in stats)
    n_games = len(per_game_counts)
    return DecisionSummary(
        n_decisions=n,
        n_gated=n_gated,
        deviation_rate=(deviations / n_gated) if n_gated else 0.0,
        gate_blocked_rate=(blocked / n_gated) if n_gated else 0.0,
        mean_iterations=(total_iters / n) if n else 0.0,
        decisions_per_game_mean=(n / n_games) if n_games else 0.0,
        begin_failures=sum(s.begin_failures for s in stats),
        step_failures=sum(s.step_failures for s in stats),
    )


def format_note(summary: DecisionSummary) -> str:
    """Compact, pipe-free suffix for the EXPERIMENTS.md Notes cell."""
    return (f"dev={summary.deviation_rate:.1%} "
            f"gate_blk={summary.gate_blocked_rate:.1%} "
            f"iters={summary.mean_iterations:.1f} "
            f"dec/g={summary.decisions_per_game_mean:.1f} "
            f"gated={summary.n_gated}/{summary.n_decisions}")
```

- [ ] **Step 4: Run summarizer tests to verify pass**

Run: `uv run pytest tests/test_search_metrics.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing agent-accumulation test** (append to `tests/test_search_agent.py`)

```python
def test_agent_accumulates_decision_stats_when_enabled():
    from cg.api import Option, OptionType
    from ptcg.agents.search_agent import SearchAgent
    from ptcg.search.timing import TimeManager
    from tests.fixtures.obs import make_obs, make_select, make_state

    class WinBackend:  # A at index 0 wins; deterministic
        def begin(self, obs, det):
            from cg.api import SearchState
            o = make_obs(make_select([Option(type=OptionType.CARD, cardId=100),
                                      Option(type=OptionType.CARD, cardId=200)],
                                     min_count=1, max_count=1), make_state())
            self._sid = getattr(self, "_sid", 0) + 1
            return SearchState(observation=o, searchId=self._sid)

        def step(self, search_id, select):
            from cg.api import SearchState
            st = make_state()
            st.result = 0 if select == [0] else 1
            return SearchState(observation=make_obs(
                make_select([Option(type=OptionType.CARD, cardId=999)],
                            min_count=1, max_count=1), st), searchId=search_id)

        def end(self):
            pass

    a = SearchAgent([3] * 60, backend=WinBackend(), collect_stats=True,
                    time_manager=TimeManager(total_s=1e9, max_move_s=0.05))
    obs = make_obs(make_select([Option(type=OptionType.CARD, cardId=100),
                                Option(type=OptionType.CARD, cardId=200)],
                               min_count=1, max_count=1), make_state())
    a.act(obs)
    assert len(a.decision_stats) == 1
    assert a.decision_stats[0].search_sig is not None


def test_agent_no_stats_by_default():
    from ptcg.agents.search_agent import SearchAgent
    a = SearchAgent([3] * 60)
    assert a.decision_stats == []
    assert a.searcher.config.collect_stats is False
```

- [ ] **Step 6: Run to verify failure**

Run: `uv run pytest tests/test_search_agent.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'collect_stats'` / `AttributeError: 'SearchAgent' object has no attribute 'decision_stats'`.

- [ ] **Step 7: Wire accumulation into `SearchAgent`** (`src/ptcg/agents/search_agent.py`)

Change the constructor signature and body. Add `collect_stats: bool = False` param; thread it into the config; init the accumulator:

```python
    def __init__(self, deck: list[int], config: SearchConfig | None = None,
                 time_manager: TimeManager | None = None,
                 backend: SearchBackend | None = None,
                 evaluator: Callable | None = None,
                 collect_stats: bool = False) -> None:
        self.cards = {c.cardId: c for c in all_card_data()}
        self.attacks = {a.attackId: a for a in all_attack()}
        basic_ids = frozenset(c.cardId for c in all_card_data()
                              if c.cardType == CardType.POKEMON and c.basic)
        self.belief = BeliefState(list(deck), basic_ids)
        self.tm = time_manager or TimeManager()
        cfg = config or SearchConfig()
        if collect_stats:
            cfg = replace(cfg, collect_stats=True)
        policy = self._policy
        self.searcher = Searcher(self.belief, backend or EngineBackend(),
                                 opponent_policy=policy, rollout_policy=policy,
                                 evaluator=evaluator if evaluator is not None else evaluate,
                                 config=cfg)
        self.fallbacks = 0
        self.decision_stats: list[SearchStats] = []
        if evaluator is not None:
            self.name = "search-net-v1"
```

Add `from dataclasses import replace` and `from ptcg.search.searcher import SearchStats` (extend the existing searcher import) at the top. In `act()`, after `result = self.searcher.search(obs, deadline)` and before the `if result is None:` check, capture stats:

```python
            result = self.searcher.search(obs, deadline)
            if self.searcher.last_stats is not None:
                self.decision_stats.append(self.searcher.last_stats)
            if result is None:
                self.fallbacks += 1
                return self._policy(obs)
            return result
```

Update the searcher import line to include `SearchStats`:

```python
from ptcg.search.searcher import (EngineBackend, SearchBackend, SearchConfig,
                                  Searcher, SearchStats, forced_selection)
```

- [ ] **Step 8: Run agent tests + full suite**

Run: `uv run pytest tests/test_search_agent.py tests/test_search_metrics.py -q && uv run pytest -q`
Expected: PASS across all; 0 regressions.

- [ ] **Step 9: Commit**

```bash
git add "src/ptcg/agents/search_agent.py" "src/ptcg/arena/search_metrics.py" "tests/test_search_agent.py" "tests/test_search_metrics.py"
git commit -m "feat: SearchAgent decision-stats accumulation + pure series-metrics summarizer"
```

---

### Task 3: `run_series` per-game-end callback (additive, default off)

**Files:**
- Modify: `src/ptcg/arena/runner.py` (`run_series` gains `on_game_end`)
- Test: `tests/test_runner.py` (extend)

**Interfaces:**
- Produces: `run_series(agent_a, agent_b, deck_a, deck_b, n_games, on_game_end: Callable[[int], None] | None = None) -> SeriesStats`. When provided, `on_game_end(game_index)` is called once after each game completes (0-based index). Default `None` preserves current behavior exactly. This lets `run_arena` snapshot the running `len(agent.decision_stats)` delta per game to build the per-game decisions distribution D2 needs.

- [ ] **Step 1: Write the failing test** (append to `tests/test_runner.py`; check the file's existing import idioms first with `Grep -n "def test" tests/test_runner.py`)

```python
def test_run_series_calls_on_game_end_once_per_game():
    from ptcg.arena.runner import run_series
    from ptcg.agents.heuristic import HeuristicAgent
    deck = [3] * 60
    seen: list[int] = []
    run_series(HeuristicAgent(), HeuristicAgent(), deck, deck, n_games=4,
               on_game_end=seen.append)
    assert seen == [0, 1, 2, 3]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_runner.py::test_run_series_calls_on_game_end_once_per_game -q`
Expected: FAIL — `TypeError: run_series() got an unexpected keyword argument 'on_game_end'`.

- [ ] **Step 3: Add the callback param** (`src/ptcg/arena/runner.py`)

Update the signature and the end of the loop:

```python
def run_series(agent_a: Agent, agent_b: Agent, deck_a: list[int], deck_b: list[int],
               n_games: int,
               on_game_end: "Callable[[int], None] | None" = None) -> SeriesStats:
    """Alternates seats: even games A is player 0, odd games B is player 0.
    on_game_end(game_index) is called after each completed game when provided."""
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
        if on_game_end is not None:
            on_game_end(g)
    return stats
```

Add `from typing import Callable` to the imports at the top of `runner.py`.

- [ ] **Step 4: Run to verify pass + full suite**

Run: `uv run pytest tests/test_runner.py -q && uv run pytest -q`
Expected: PASS; 0 regressions.

- [ ] **Step 5: Commit**

```bash
git add "src/ptcg/arena/runner.py" "tests/test_runner.py"
git commit -m "feat: run_series on_game_end callback for per-game instrumentation"
```

---

### Task 4: `run_arena.py` `--collect-stats` wiring + gate-param CLI + sidecar JSON

**Files:**
- Modify: `scripts/run_arena.py` (new CLI flags, metrics aggregation, sidecar JSON, Notes suffix)
- Test: `tests/test_run_arena_metrics.py` (new — tests the extracted pure helpers)

**Interfaces:**
- Produces (new CLI flags on `run_arena.py`): `--collect-stats` (store_true), `--deviate-min-visits INT` (default 20), `--deviate-value-edge FLOAT` (default 0.12), `--deviate-min-visit-frac FLOAT` (default 0.0, F1), `--final-move-rule {most_visited,max_value}` (default most_visited, F3), `--real-clock` (store_true, F2 validation — uses `TimeManager(total_s=480.0)` instead of `1e9`).
- Produces (extracted helper, testable without running games): `scripts/run_arena.py::build_search_config(args) -> SearchConfig` and `scripts/run_arena.py::write_sidecar(path, label, summary, per_game_counts, max_move_s) -> None`.
- Behavior: when `--collect-stats` and a search agent is present, `run_arena` passes an `on_game_end` callback that records per-game decision-count deltas, calls `search_metrics.summarize`, prints the metrics, appends `search_metrics.format_note(summary)` to the `--notes` text in the EXPERIMENTS.md row, and writes a sidecar JSON to `experiments/instrumentation/<date>-<safe-label>.json` with the per-game distribution + summary (this is the input D2 and the analysis tasks read).

**Design note:** the EXPERIMENTS.md row keeps its exact 13-column / 14-pipe shape (guarded by `tests/test_stats.py::test_markdown_row_shape`); instrumentation metrics ride in the free-text `Notes` cell and the sidecar JSON. This is a resolved spec ambiguity — see the Self-Review section.

- [ ] **Step 1: Write the failing helper tests** (`tests/test_run_arena_metrics.py`)

The sidecar is JSON so it must be pipe-safe and utf-8. Arithmetic hand-check for the config frac path: `uv run python -c "import math; print(math.ceil(0.1*40))"` → `4`.

```python
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import run_arena  # noqa: E402
from ptcg.arena.search_metrics import summarize  # noqa: E402
from ptcg.search.searcher import SearchStats  # noqa: E402


def _args(**kw):
    base = dict(deviate_min_visits=20, deviate_value_edge=0.12,
                deviate_min_visit_frac=0.0, rollout_depth=12,
                final_move_rule="most_visited", collect_stats=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_build_search_config_defaults_match_slice4():
    cfg = run_arena.build_search_config(_args())
    assert cfg.deviate_min_visits == 20
    assert cfg.deviate_value_edge == 0.12
    assert cfg.deviate_min_visit_frac == 0.0
    assert cfg.rollout_depth == 12
    assert cfg.final_move_rule == "most_visited"


def test_build_search_config_gate_off():
    cfg = run_arena.build_search_config(_args(deviate_min_visits=0, deviate_value_edge=0.0))
    assert cfg.deviate_min_visits == 0 and cfg.deviate_value_edge == 0.0


def test_write_sidecar_is_utf8_json(tmp_path):
    stats = [SearchStats(iterations=10, root_children=2, top_child_visits=10,
                         top_child_value=0.5, v0_sig=(0,), search_sig=(1,),
                         most_visited_sig=(1,), deviated=True, gate_blocked=False,
                         begin_failures=0, step_failures=0)]
    summary = summarize(stats, [1])
    out = tmp_path / "probe.json"
    run_arena.write_sidecar(out, "slice5 D1 budget=200ms", summary, [1], 0.31)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["label"] == "slice5 D1 budget=200ms"
    assert doc["per_game_decisions"] == [1]
    assert doc["deviation_rate"] == 1.0
    assert doc["max_move_seconds"] == 0.31
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_run_arena_metrics.py -q`
Expected: FAIL — `AttributeError: module 'run_arena' has no attribute 'build_search_config'`.

- [ ] **Step 3: Implement the flags, helpers, and wiring** (`scripts/run_arena.py`)

Add imports at the top (after existing): `import json`. Add the two helpers and rewrite `main()`. The full new `run_arena.py` body (replace from the `AGENTS` dict down through `main`):

```python
def build_search_config(args) -> SearchConfig:
    return SearchConfig(
        rollout_depth=args.rollout_depth,
        deviate_min_visits=args.deviate_min_visits,
        deviate_value_edge=args.deviate_value_edge,
        deviate_min_visit_frac=args.deviate_min_visit_frac,
        final_move_rule=args.final_move_rule,
    )


def _time_manager(args):
    total = 480.0 if args.real_clock else 1e9
    return TimeManager(total_s=total, max_move_s=args.search_budget_ms / 1000.0)


AGENTS = {
    "random": lambda deck, a: RandomAgent(seed=0),
    "heuristic": lambda deck, a: HeuristicAgent(),
    "search": lambda deck, a: SearchAgent(
        deck, config=build_search_config(a), time_manager=_time_manager(a),
        collect_stats=a.collect_stats),
    "search-net": lambda deck, a: SearchAgent(
        deck, config=build_search_config(a), time_manager=_time_manager(a),
        collect_stats=a.collect_stats, evaluator=ValueNetEvaluator.load_default()),
}


def write_sidecar(path: Path, label: str, summary, per_game_counts: list[int],
                  max_move_s: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "label": label,
        "n_decisions": summary.n_decisions,
        "n_gated": summary.n_gated,
        "deviation_rate": summary.deviation_rate,
        "gate_blocked_rate": summary.gate_blocked_rate,
        "mean_iterations": summary.mean_iterations,
        "decisions_per_game_mean": summary.decisions_per_game_mean,
        "begin_failures": summary.begin_failures,
        "step_failures": summary.step_failures,
        "per_game_decisions": per_game_counts,
        "max_move_seconds": max_move_s,
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--agent-a", choices=AGENTS, required=True)
    p.add_argument("--agent-b", choices=AGENTS, required=True)
    p.add_argument("--deck-a", default="tests/fixtures/sample_deck.csv")
    p.add_argument("--deck-b", default="tests/fixtures/sample_deck.csv")
    p.add_argument("--games", type=int, default=50)
    p.add_argument("--notes", default="")
    p.add_argument("--search-budget-ms", type=int, default=200)
    p.add_argument("--rollout-depth", type=int, default=12)
    p.add_argument("--deviate-min-visits", type=int, default=20)
    p.add_argument("--deviate-value-edge", type=float, default=0.12)
    p.add_argument("--deviate-min-visit-frac", type=float, default=0.0)
    p.add_argument("--final-move-rule", choices=["most_visited", "max_value"],
                   default="most_visited")
    p.add_argument("--collect-stats", action="store_true")
    p.add_argument("--real-clock", action="store_true")
    args = p.parse_args()

    deck_a, deck_b = load_deck(args.deck_a), load_deck(args.deck_b)
    agent_a = AGENTS[args.agent_a](deck_a, args)
    agent_b = AGENTS[args.agent_b](deck_b, args)

    stat_agents = [ag for ag in (agent_a, agent_b) if getattr(ag, "decision_stats", None) is not None]
    per_game_counts: list[int] = []
    on_game_end = None
    if args.collect_stats and stat_agents:
        prev = {"n": 0}
        def on_game_end(_g):  # noqa: E306 — snapshot per-game decision-count delta
            total = sum(len(ag.decision_stats) for ag in stat_agents)
            per_game_counts.append(total - prev["n"])
            prev["n"] = total

    stats = run_series(agent_a, agent_b, deck_a, deck_b, args.games,
                       on_game_end=on_game_end)

    notes = args.notes
    if args.collect_stats and stat_agents:
        all_stats = [s for ag in stat_agents for s in ag.decision_stats]
        summary = summarize(all_stats, per_game_counts)
        notes = f"{args.notes} {format_note(summary)}".strip()
        date = dt.date.today().isoformat()
        safe = "".join(c if c.isalnum() else "-" for c in (args.notes or "series"))[:48]
        write_sidecar(ROOT / "experiments" / "instrumentation" / f"{date}-{safe}.json",
                      args.notes, summary, per_game_counts, stats.max_move_seconds)
        print(f"instrumentation: {format_note(summary)}")

    row = stats.markdown_row(
        dt.date.today().isoformat(), agent_a.name, agent_b.name,
        Path(args.deck_a).name, Path(args.deck_b).name, notes)
    log = ROOT / "experiments" / "EXPERIMENTS.md"
    log.write_text(log.read_text(encoding="utf-8") + row + "\n", encoding="utf-8")
    lo, hi = stats.ci_a
    print(f"{agent_a.name} vs {agent_b.name}: {stats.wins_a}-{stats.wins_b}-{stats.draws} "
          f"({stats.win_rate_a:.1%} [{lo:.1%}, {hi:.1%}]) "
          f"avg {sum(stats.game_seconds)/stats.n:.2f}s/game, "
          f"max move {stats.max_move_seconds:.3f}s")
    print(f"Logged to {log}")
```

Add the new imports near the top of `run_arena.py` (after the existing `ValueNetEvaluator` import):

```python
from ptcg.arena.search_metrics import format_note, summarize  # noqa: E402
```

**Landmark note:** this task references `SearchConfig.deviate_min_visit_frac` and `SearchConfig.final_move_rule`, which are ADDED in Tasks 10 and 12. To keep Task 4 self-contained and the suite green, add both fields to `SearchConfig` NOW as inert defaults (`deviate_min_visit_frac: float = 0.0`, `final_move_rule: str = "most_visited"`) with a one-line comment `# consumed in Slice-5 F1/F3 (Tasks 10/12)`. The gate/selection LOGIC that reads them lands in Tasks 10/12; declaring the fields early is a deliberate, tracked forward-declaration (NAMED transient: "frac/rule fields declared inert in Task 4, logic wired in Tasks 10/12").

- [ ] **Step 4: Add the inert config fields** to `src/ptcg/search/searcher.py` `SearchConfig` (below `collect_stats`):

```python
    deviate_min_visit_frac: float = 0.0  # consumed in Slice-5 F1 (Task 10); 0.0 = disabled
    final_move_rule: str = "most_visited"  # consumed in Slice-5 F3 (Task 12)
```

- [ ] **Step 5: Run helper tests + the encoding guard + full suite**

Run: `uv run pytest tests/test_run_arena_metrics.py tests/test_experiments_append.py -q && uv run pytest -q`
Expected: PASS. `test_run_arena_experiments_append_has_encoding` still green (the new `write_sidecar` also uses `encoding="utf-8"`; if you added any other `write_text`/`read_text`, they must too).

- [ ] **Step 6: Extend the encoding guard to the sidecar write** (defensive; append to `tests/test_experiments_append.py`)

```python
def test_run_arena_sidecar_write_has_encoding():
    source = (ROOT / "scripts" / "run_arena.py").read_text(encoding="utf-8")
    assert "write_sidecar" in source, "write_sidecar helper missing from run_arena.py"
    # the existing _call_sites_missing_encoding scan already covers ALL write_text/
    # read_text in the file; this test just pins that write_sidecar exists to guard it.
```

Run: `uv run pytest tests/test_experiments_append.py -q` → PASS.

- [ ] **Step 7: Commit**

```bash
git add "scripts/run_arena.py" "src/ptcg/search/searcher.py" "tests/test_run_arena_metrics.py" "tests/test_experiments_append.py"
git commit -m "feat: run_arena --collect-stats metrics, gate/rule CLI flags, sidecar JSON"
```

---

## Phase B — Diagnosis experiments (Tasks 5–8). Each arena run is ORCHESTRATOR-RUN.

Standard deck for all Slice-5 mirror series: `src/ptcg/decks/candidates/mega-lucario-fighting.csv` (field-best per Slice 3). All D-phase series are `--agent-a search --agent-b heuristic` mirror, `--collect-stats`, so search is always seat A.

### Task 5: D1 — Budget scan (200 ms / 500 ms / 1 s / 2 s)

**Files:**
- Create/append: `experiments/ANALYSIS-slice5-search-architecture.md` (D1 section)
- Read: `experiments/EXPERIMENTS.md`, `experiments/instrumentation/*.json`

- [ ] **Step 1 (agent): validate the command with a 4-game dry run**

Run this once to confirm the flags parse, stats aggregate, and a sidecar is written (fast, ~10 s):

```bash
uv run python scripts/run_arena.py --agent-a search --agent-b heuristic \
  --deck-a "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
  --deck-b "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
  --games 4 --search-budget-ms 200 --collect-stats --notes "slice5 D1 dryrun"
```
Expected: prints an `instrumentation: dev=.. gate_blk=.. iters=.. dec/g=..` line and writes `experiments/instrumentation/<date>-slice5-D1-dryrun.json`. Confirm the sidecar parses. Then delete the dry-run EXPERIMENTS.md row and sidecar (`git checkout experiments/EXPERIMENTS.md`; `rm` the dry-run json) so only real runs are logged.

- [ ] **Step 2 (ORCHESTRATOR-RUN): the four budget series (~100 games each)**

The orchestrator runs these four in its OWN background shell, one after another (est. ~45 min total wall-clock: ~4 / ~7 / ~12 / ~22 min). The dispatched agent does NOT wait.

```bash
# ORCHESTRATOR-RUN: D1 budget scan
for MS in 200 500 1000 2000; do
  uv run python scripts/run_arena.py --agent-a search --agent-b heuristic \
    --deck-a "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
    --deck-b "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
    --games 100 --search-budget-ms $MS --collect-stats \
    --notes "slice5 D1 budget=${MS}ms"
done
```

- [ ] **Step 3 (agent): harvest + write the D1 analysis section**

Read the four new EXPERIMENTS.md rows and the four sidecars. Create `experiments/ANALYSIS-slice5-search-architecture.md` with a `## D1 — Budget scan` section tabulating, per budget: win rate [CI], mean iterations/decision, deviation rate, gate-blocked rate, mean decisions/game, max move s. State plainly whether the gate-can't-fire hypothesis holds: if `deviation_rate ≈ 0` and `mean_iterations < deviate_min_visits (20)` at 200 ms, the gate is mathematically starved; note how deviation rate scales as iterations grow with budget. Write the file with `encoding="utf-8"`.

- [ ] **Step 4: Commit**

```bash
git add "experiments/EXPERIMENTS.md" "experiments/instrumentation" "experiments/ANALYSIS-slice5-search-architecture.md"
git commit -m "experiment: slice5 D1 budget scan + analysis"
```

---

### Task 6: D2 — Match-clock envelope math (pure script)

**Files:**
- Create: `scripts/match_clock_envelope.py` (pure math over D1 sidecars — no engine, no games)
- Test: `tests/test_match_clock_envelope.py`
- Append: D2 section of `experiments/ANALYSIS-slice5-search-architecture.md`

**Interfaces:**
- Produces: `match_clock_envelope.safe_budget_ms(decisions_per_game_p95: float, match_seconds: float = 600.0, safety_factor: float = 2.0, overshoot_ms: float = 0.0) -> float` — the maximum per-move budget (ms) that keeps `decisions_per_game_p95 * (budget + overshoot) * safety_factor <= match_seconds*1000`. Plus `percentile(values, q) -> float`.

- [ ] **Step 1: Write the failing test** (`tests/test_match_clock_envelope.py`)

Hand-verify: with p95=30 decisions, match=600 s, safety=2, overshoot=0 → budget = 600000/(30*2) = 10000 ms. With overshoot=400 → 600000/(30*2) - 400 = 10000-400 = 9600 ms. Confirm: `uv run python -c "print(600000/(30*2), 600000/(30*2)-400)"` → `10000.0 9600.0`. Percentile p95 of range(1..100) (nearest-rank) = 95.

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import match_clock_envelope as mce  # noqa: E402


def test_safe_budget_basic():
    assert mce.safe_budget_ms(30, match_seconds=600.0, safety_factor=2.0) == 10000.0


def test_safe_budget_subtracts_overshoot():
    assert mce.safe_budget_ms(30, match_seconds=600.0, safety_factor=2.0,
                              overshoot_ms=400.0) == 9600.0


def test_percentile_nearest_rank():
    assert mce.percentile(list(range(1, 101)), 0.95) == 95
```

- [ ] **Step 2: Run to verify failure** → `uv run pytest tests/test_match_clock_envelope.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement** (`scripts/match_clock_envelope.py`)

```python
"""D2: compute the max per-move budget that is provably timeout-safe under the
10-minute match clock, from D1's per-game decision distribution. Pure math, O(n log n)
for the percentile sort over at most a few hundred games — no engine, no arena."""
from __future__ import annotations

import math


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    rank = max(1, math.ceil(q * len(s)))  # nearest-rank, 1-indexed
    return s[rank - 1]


def safe_budget_ms(decisions_per_game_p95: float, match_seconds: float = 600.0,
                   safety_factor: float = 2.0, overshoot_ms: float = 0.0) -> float:
    """Largest per-move budget (ms) s.t. worst-case total search time over a game,
    inflated by safety_factor for unknown Kaggle hardware, fits the match clock.
    Subtracts the measured deadline overshoot (a single in-flight iteration is not
    interrupted at the deadline)."""
    if decisions_per_game_p95 <= 0:
        return 0.0
    budget_plus_overshoot = (match_seconds * 1000.0) / (decisions_per_game_p95 * safety_factor)
    return max(0.0, budget_plus_overshoot - overshoot_ms)
```

- [ ] **Step 4: Run to verify pass** → `uv run pytest tests/test_match_clock_envelope.py -q` → PASS.

- [ ] **Step 5 (agent, no ORCHESTRATOR run needed — reads existing D1 sidecars): compute + write D2**

Load the four D1 sidecars, pool `per_game_decisions`, compute p95 decisions/game; take the worst-case max move s from D1 rows as `overshoot_ms` (max move − budget, the un-interrupted-iteration tail). Run `safe_budget_ms(...)` for the pooled p95. Append a `## D2 — Match-clock envelope` section stating the proven-safe max budget (with the ≥2× factor and overshoot subtracted) and the specific number F2 may raise to. Include the one-line `python -c` recompute of the final number in the doc (executable-check discipline). Write with `encoding="utf-8"`.

- [ ] **Step 6: Commit**

```bash
git add "scripts/match_clock_envelope.py" "tests/test_match_clock_envelope.py" "experiments/ANALYSIS-slice5-search-architecture.md"
git commit -m "experiment: slice5 D2 match-clock envelope math + analysis"
```

---

### Task 7: D3 — Gate-off ablation

**Files:**
- Append: D3 section of `experiments/ANALYSIS-slice5-search-architecture.md`
- Read: EXPERIMENTS.md rows + sidecars

Uses the `--deviate-min-visits 0 --deviate-value-edge 0` flags from Task 4 (already supported by `_gate`; `deviate_min_visits<=0 and edge<=0` → pure most-visited, no v0 anchor).

- [ ] **Step 1 (agent): validate the gate-off command with a 4-game dry run**

```bash
uv run python scripts/run_arena.py --agent-a search --agent-b heuristic \
  --deck-a "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
  --deck-b "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
  --games 4 --search-budget-ms 200 --deviate-min-visits 0 --deviate-value-edge 0 \
  --collect-stats --notes "slice5 D3 dryrun"
```
Expected: `deviation_rate` near 100% of gated decisions now (gate disabled → search always plays its most-visited). Clean up the dry-run row + sidecar afterward.

- [ ] **Step 2 (ORCHESTRATOR-RUN): two gate-off series (~300 games each, at 200 ms and 1 s)** (est. ~11 + ~35 min):

```bash
# ORCHESTRATOR-RUN: D3 gate-off ablation
for MS in 200 1000; do
  uv run python scripts/run_arena.py --agent-a search --agent-b heuristic \
    --deck-a "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
    --deck-b "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
    --games 300 --search-budget-ms $MS --deviate-min-visits 0 --deviate-value-edge 0 \
    --collect-stats --notes "slice5 D3 gate-off budget=${MS}ms"
done
```

- [ ] **Step 3 (agent): harvest + write D3**

Compare gate-off win rates against D1's gated win rates at the same budgets. Apply the spec's decomposition verbatim: **ungated LOSES → the gate was masking determinization noise (points to noise fixes + better priors, i.e. F4);** **ungated WINS → the gate was strangling a real edge (points to F1 gate retune + F2 budget raise).** Because this is a stochastic comparison, if the gate-off vs gated gap sits within ~1 CI width, state it as inconclusive and note replication would be needed. Write with `encoding="utf-8"`.

- [ ] **Step 4: Commit**

```bash
git add "experiments/EXPERIMENTS.md" "experiments/instrumentation" "experiments/ANALYSIS-slice5-search-architecture.md"
git commit -m "experiment: slice5 D3 gate-off ablation + analysis"
```

---

### Task 8: D4 — Determinization-noise probe

**Files:**
- Create: `scripts/probe_determinization_noise.py`
- Test: `tests/test_probe_determinization_noise.py`
- Append: D4 section of `experiments/ANALYSIS-slice5-search-architecture.md`

**Critical engine constraint (verified in `CLAUDE.md` + `searcher.py`):** `search_begin` only works on the REAL agent's own live `Observation` — a captured/pickled obs cannot be re-searched in a fresh process. Therefore the K reruns must happen INLINE during a live game, at the decision moment, before the agent commits its real move. Running `Searcher.search()` K extra times back-to-back on the same live obs is safe: every iteration calls `backend.end()` and the search arena is separate from the main battle (`battle_select` is never called during a probe), so the live battle state is untouched.

**Interfaces:**
- Produces: `probe_determinization_noise.probe_state(searcher, obs, deadline_fn, reruns: int, base_seed: int) -> tuple[float, float]` returning `(move_agreement, root_value_std)` for one decision state — `move_agreement` = fraction of `reruns` that picked the modal `search_sig`; `root_value_std` = population std of `last_stats.top_child_value` across reruns. Reseeds `searcher.rng = random.Random(base_seed + k)` before each rerun for fresh determinization streams.
- Produces: a `NoiseProbeAgent` wrapping a `SearchAgent` that, at up to `--states` sampled 1-of-1 decision points, calls `probe_state` and records `(agreement, std)`, then plays its REAL move.

- [ ] **Step 1: Write the failing unit test with a fake backend** (`tests/test_probe_determinization_noise.py`)

Uses the same `FakeBackend` shape as `test_searcher.py` (deterministic winner) so `probe_state` returns agreement 1.0 and std 0.0 without the engine. Hand-check: a deterministic searcher picks the same move every rerun → agreement == 1.0, std == 0.0.

```python
import sys
import time
from pathlib import Path

from cg.api import Option, OptionType, SearchState
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import probe_determinization_noise as probe  # noqa: E402

from ptcg.search.belief import Determinization  # noqa: E402
from ptcg.search.searcher import SearchConfig, Searcher  # noqa: E402
from tests.fixtures.obs import make_obs, make_select, make_state  # noqa: E402


def opt(cid):
    return Option(type=OptionType.CARD, cardId=cid)


class FakeBelief:
    def sample(self, obs, rng):
        return Determinization([], [], [], [], [], [])


class WinBackend:
    def __init__(self):
        self.begins = 0
    def begin(self, obs, det):
        self.begins += 1
        o = make_obs(make_select([opt(100), opt(200)], min_count=1, max_count=1),
                     make_state())
        return SearchState(observation=o, searchId=self.begins)
    def step(self, search_id, select):
        st = make_state(); st.result = 0 if select == [0] else 1
        return SearchState(observation=make_obs(
            make_select([opt(999)], min_count=1, max_count=1), st), searchId=search_id)
    def end(self):
        pass


def test_probe_state_deterministic_backend_full_agreement():
    s = Searcher(FakeBelief(), WinBackend(), opponent_policy=lambda o: [0],
                 rollout_policy=lambda o: [0],
                 config=SearchConfig(max_iterations=30, seed=0, collect_stats=True))
    obs = make_obs(make_select([opt(100), opt(200)], min_count=1, max_count=1),
                   make_state())
    agreement, std = probe.probe_state(
        s, obs, deadline_fn=lambda: time.perf_counter() + 5.0, reruns=5, base_seed=0)
    assert agreement == 1.0
    assert std == 0.0
```

- [ ] **Step 2: Run to verify failure** → `uv run pytest tests/test_probe_determinization_noise.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement** (`scripts/probe_determinization_noise.py`)

```python
"""D4: quantify the determinization-noise floor. At sampled LIVE decision states,
rerun the full search K times with fresh determinization streams and measure how
often the picked move agrees and how much the root value estimate varies.

Complexity: O(states * reruns * iterations_per_search). At the production scale
(--states 50 --reruns 20 --budget-ms 200) that is ~50*20 = 1000 full searches; the
first real run IS the scale test — validate the runtime estimate before trusting it."""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ptcg.agents.heuristic import HeuristicAgent  # noqa: E402
from ptcg.agents.search_agent import SearchAgent  # noqa: E402
from ptcg.arena.runner import load_deck, play_match  # noqa: E402
from ptcg.search.searcher import SearchConfig  # noqa: E402
from ptcg.search.timing import TimeManager  # noqa: E402


def probe_state(searcher, obs, deadline_fn, reruns: int, base_seed: int):
    """Rerun search() `reruns` times on ONE live obs with fresh determinization
    streams; return (move_agreement, root_value_std)."""
    sigs = []
    values = []
    for k in range(reruns):
        searcher.rng = random.Random(base_seed + k)  # fresh determinization stream
        searcher.search(obs, deadline_fn())
        st = searcher.last_stats
        if st is None or st.search_sig is None:
            continue
        sigs.append(st.search_sig)
        values.append(st.top_child_value)
    if not sigs:
        return (0.0, 0.0)
    modal = max(set(sigs), key=sigs.count)
    agreement = sigs.count(modal) / len(sigs)
    std = statistics.pstdev(values) if len(values) > 1 else 0.0
    return (agreement, std)


class NoiseProbeAgent(SearchAgent):
    """SearchAgent that probes up to `max_states` of its own 1-of-1 decisions."""

    def __init__(self, deck, budget_ms, reruns, max_states):
        super().__init__(deck, config=SearchConfig(collect_stats=True),
                         time_manager=TimeManager(total_s=1e9, max_move_s=budget_ms / 1000.0),
                         collect_stats=True)
        self.reruns = reruns
        self.max_states = max_states
        self.records: list[tuple[float, float]] = []

    def act(self, obs):
        sel = obs.select
        if (len(self.records) < self.max_states and sel.minCount == 1
                and sel.maxCount == 1 and len(sel.option) >= 2):
            deadline_fn = lambda: time.perf_counter() + self.tm.move_budget()  # noqa: E731
            self.records.append(
                probe_state(self.searcher, obs, deadline_fn, self.reruns, base_seed=1000))
        return super().act(obs)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--deck", default="src/ptcg/decks/candidates/mega-lucario-fighting.csv")
    p.add_argument("--states", type=int, default=50)
    p.add_argument("--reruns", type=int, default=20)
    p.add_argument("--budget-ms", type=int, default=200)
    p.add_argument("--max-games", type=int, default=20)
    args = p.parse_args()

    deck = load_deck(args.deck)
    probe_agent = NoiseProbeAgent(deck, args.budget_ms, args.reruns, args.states)
    opp = HeuristicAgent()
    t0 = time.perf_counter()
    games = 0
    while len(probe_agent.records) < args.states and games < args.max_games:
        play_match(probe_agent, opp, deck, deck)
        games += 1
    recs = probe_agent.records
    agreements = [a for a, _ in recs]
    stds = [s for _, s in recs]
    out = {
        "states_sampled": len(recs),
        "reruns_each": args.reruns,
        "budget_ms": args.budget_ms,
        "mean_move_agreement": statistics.mean(agreements) if agreements else 0.0,
        "mean_root_value_std": statistics.mean(stds) if stds else 0.0,
        "wall_seconds": round(time.perf_counter() - t0, 1),
    }
    path = ROOT / "experiments" / "instrumentation" / "slice5-D4-noise-probe.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the unit test + a tiny inline smoke** (fast, no engine games in the unit test; the smoke uses the real engine but tiny scale)

```bash
uv run pytest tests/test_probe_determinization_noise.py -q
uv run python scripts/probe_determinization_noise.py --states 2 --reruns 3 --budget-ms 50 --max-games 3
```
Expected: unit test PASS; smoke prints a JSON blob with `states_sampled` up to 2 and a `wall_seconds`. Use the smoke's `wall_seconds` to project the full-scale runtime (states*reruns scaling) BEFORE the orchestrator run.

- [ ] **Step 5 (ORCHESTRATOR-RUN): the full-scale probe** (est. from the smoke; ~10–20 min at 50×20 / 200 ms):

```bash
# ORCHESTRATOR-RUN: D4 determinization-noise probe
uv run python scripts/probe_determinization_noise.py --states 50 --reruns 20 --budget-ms 200
```

- [ ] **Step 6 (agent): harvest + write D4**

Read `experiments/instrumentation/slice5-D4-noise-probe.json`. Append a `## D4 — Determinization-noise floor` section: mean move-agreement across states and mean root-value std. Interpret: low agreement + high value std at 200 ms means the belief-v1 mirror prior injects noise the search can't average out at that budget — corroborating or refuting D3's read. Write with `encoding="utf-8"`.

- [ ] **Step 7: Commit**

```bash
git add "scripts/probe_determinization_noise.py" "tests/test_probe_determinization_noise.py" "experiments/instrumentation" "experiments/ANALYSIS-slice5-search-architecture.md"
git commit -m "experiment: slice5 D4 determinization-noise probe + analysis"
```

---

## Phase C — Decision checkpoint (Task 9)

### Task 9: Synthesize D1–D4 → ANALYSIS first draft + fix-path decision matrix

**Files:**
- Modify: `experiments/ANALYSIS-slice5-search-architecture.md` (add synthesis + decision matrix)

- [ ] **Step 1 (agent): write the synthesis + decision matrix**

Add a `## Diagnosis synthesis` section pulling the four D-sections into one narrative (this is report-ready methodology prose — the Strategy report is judged 70% on methodology). Then add an explicit `## Fix-path decision matrix` mapping evidence patterns to which F-rungs to run, e.g.:

| Evidence pattern (from D1–D4) | Interpretation | Fix path |
|---|---|---|
| Gate never fires (deviation≈0, iters<20 @200ms) AND gate-off WINS | Gate strangling a real edge | F1 gate retune → F2 budget raise |
| Gate-off LOSES AND high determinization noise (low agreement, high value std) | Noise, not the gate | F4 PUCT prior (re-plan) ± better belief prior |
| Deviation rate healthy but win rate flat across budgets | Selection/final-move quality | F3 final-move rule → F1 |
| Win rate climbs monotonically with budget, timeout-safe headroom exists | Budget-starved | F2 budget raise first |

- [ ] **Step 2: Commit**

```bash
git add "experiments/ANALYSIS-slice5-search-architecture.md"
git commit -m "docs: slice5 diagnosis synthesis + fix-path decision matrix"
```

- [ ] **Step 3 (orchestrator + Brad DECISION GATE):** the orchestrator presents the decision matrix and the measured evidence to Brad via `AskUserQuestion` — which F-rung(s) to execute, in what order, and whether F4's re-plan trigger is armed. Record the chosen path in `.claude/plan.md` before proceeding. **The F-tasks below are executed in the evidence-chosen order; skip rungs the matrix rules out.**

---

## Phase D — Fix ladder (Tasks 10–13). Each fix gets its own ≥200-game ORCHESTRATOR-RUN series.

### Task 10: F1 — Gate retune (fractional visit threshold)

**Files:**
- Modify: `src/ptcg/search/searcher.py` (`_gate` reads `deviate_min_visit_frac`)
- Test: `tests/test_searcher.py` (extend)
- Append: F1 section of `experiments/ANALYSIS-slice5-search-architecture.md`

**Design:** the effective visit threshold becomes `max(ceil(frac * iterations_run), deviate_min_visits)` when `frac > 0`, else the absolute `deviate_min_visits` (unchanged, backwards-compatible). This lets the threshold scale with achieved iterations so the gate can fire at low budgets. `iterations_run` is already an attribute on `self`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_searcher.py`)

Hand-verify the arithmetic: with `iterations_run=40`, `frac=0.1`, floor=5 → `max(ceil(4.0), 5) = max(4, 5) = 5`. With `frac=0.5`, iters=40, floor=5 → `max(ceil(20.0),5)=20`. Confirm: `uv run python -c "import math; print(max(math.ceil(0.1*40),5), max(math.ceil(0.5*40),5))"` → `5 20`.

```python
import math


def test_frac_threshold_scales_with_iterations():
    s, obs, sig0, sig1 = _anchor_fixture(
        SearchConfig(deviate_min_visits=5, deviate_value_edge=0.1,
                     deviate_min_visit_frac=0.5))
    s.iterations_run = 40  # threshold = max(ceil(0.5*40)=20, floor 5) = 20
    # challenger has 18 visits (< 20) but great value -> still vetoed by frac threshold
    visited = {sig0: Node(visits=15, value_sum=7.5),
               sig1: Node(visits=18, value_sum=16.2)}  # mean 0.9
    assert s._gate(obs, visited, sig1, s._v0_sig(obs)) == sig0


def test_frac_zero_preserves_absolute_threshold():
    s, obs, sig0, sig1 = _anchor_fixture(
        SearchConfig(deviate_min_visits=10, deviate_value_edge=0.1,
                     deviate_min_visit_frac=0.0))
    s.iterations_run = 1000
    visited = {sig0: Node(visits=15, value_sum=7.5),
               sig1: Node(visits=20, value_sum=14.0)}  # 20 >= 10, mean 0.7 >= 0.6
    assert s._gate(obs, visited, sig1, s._v0_sig(obs)) == sig1  # unchanged behavior
```

- [ ] **Step 2: Run to verify failure** → `uv run pytest tests/test_searcher.py -k frac -q` → FAIL (frac ignored, first test returns sig1).

- [ ] **Step 3: Implement** — in `_gate`, replace the visit-threshold check:

```python
        cfg = self.config
        if cfg.deviate_min_visits <= 0 and cfg.deviate_value_edge <= 0.0:
            return most_visited
        if v0_sig is None or most_visited == v0_sig:
            return most_visited
        best_node = visited[most_visited]
        v0_mean = visited[v0_sig].mean if v0_sig in visited else 0.5
        min_visits = cfg.deviate_min_visits
        if cfg.deviate_min_visit_frac > 0.0:
            min_visits = max(math.ceil(cfg.deviate_min_visit_frac * self.iterations_run),
                             cfg.deviate_min_visits)
        if (best_node.visits >= min_visits
                and best_node.mean >= v0_mean + cfg.deviate_value_edge):
            return most_visited
        return v0_sig
```

Add `import math` to `searcher.py` if not already present (it is not — add it next to `import random`, `import time`).

- [ ] **Step 4: Run to verify pass + full suite** → `uv run pytest tests/test_searcher.py -q && uv run pytest -q` → PASS.

- [ ] **Step 5: Commit the code**

```bash
git add "src/ptcg/search/searcher.py" "tests/test_searcher.py"
git commit -m "feat: F1 fractional gate visit-threshold (deviate_min_visit_frac)"
```

- [ ] **Step 6 (agent): validate the F1 arena command with a 4-game dry run**, using the frac + any value-edge chosen from the D-phase (example `--deviate-min-visit-frac 0.25 --deviate-value-edge 0.08`; the actual values come from Task 9's decision). Clean up the dry-run row/sidecar.

- [ ] **Step 7 (ORCHESTRATOR-RUN): F1 validation series (≥200 games)** at the D2-safe budget:

```bash
# ORCHESTRATOR-RUN: F1 gate retune (fill frac/edge/budget from the decision matrix)
uv run python scripts/run_arena.py --agent-a search --agent-b heuristic \
  --deck-a "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
  --deck-b "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
  --games 200 --search-budget-ms <D2_BUDGET> \
  --deviate-min-visit-frac <FRAC> --deviate-value-edge <EDGE> \
  --collect-stats --notes "slice5 F1 frac=<FRAC> edge=<EDGE> budget=<D2_BUDGET>ms"
```

- [ ] **Step 8 (agent): harvest + append F1 section** (win rate [CI] + deviation/gate-blocked rates vs D1 baseline; did the retune raise deviation AND win rate, or just deviation?). If win rate ≥ 0.55 emerges here, note it as a candidate for the Section-4 gate (still requires replication). Commit EXPERIMENTS.md + sidecar + analysis.

---

### Task 11: F2 — Per-move budget raise (within the D2 envelope) + real-clock validation

**Files:**
- Append: F2 section of `experiments/ANALYSIS-slice5-search-architecture.md`
- (No core code — uses `--search-budget-ms` within D2's proven-safe cap and `--real-clock` from Task 4.)

- [ ] **Step 1 (agent): confirm the chosen budget ≤ D2's `safe_budget_ms`.** State the number and its D2 provenance in the F2 analysis stub. If the D2 cap is far above what fits the wall-clock budget for a series, pick the largest budget that keeps the whole F2 series under ~40 min while staying ≤ the D2 cap. Validate with a 4-game dry run at that budget; clean up.

- [ ] **Step 2 (ORCHESTRATOR-RUN): F2 series (≥200 games) at the raised budget, plus a `--real-clock` validation series** (the real-clock run re-enables `TimeManager(total_s=480)` so the budget is validated realistically, not with the arena's `total_s=1e9`):

```bash
# ORCHESTRATOR-RUN: F2 budget raise (fill BUDGET <= D2 cap; keep best gate config)
uv run python scripts/run_arena.py --agent-a search --agent-b heuristic \
  --deck-a "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
  --deck-b "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
  --games 200 --search-budget-ms <BUDGET> --collect-stats \
  --notes "slice5 F2 budget=<BUDGET>ms"

# ORCHESTRATOR-RUN: F2 real-clock timeout-safety spot check
uv run python scripts/run_arena.py --agent-a search --agent-b heuristic \
  --deck-a "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
  --deck-b "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
  --games 100 --search-budget-ms <BUDGET> --real-clock --collect-stats \
  --notes "slice5 F2 real-clock budget=<BUDGET>ms"
```

- [ ] **Step 3 (agent): harvest + append F2 section.** Report win rate vs budget and confirm zero timeout losses / max move s under the safe envelope in the real-clock run. Commit.

---

### Task 12: F3 — Final-move rule variant (`max_value` selection)

**Files:**
- Modify: `src/ptcg/search/searcher.py` (`search()` selects `most_visited` per `config.final_move_rule`)
- Test: `tests/test_searcher.py` (extend)
- Append: F3 section of `experiments/ANALYSIS-slice5-search-architecture.md`

**Design:** add a `_final_pick(visited) -> sig` used in place of the inline `max(...visits)` in `search()`. `"most_visited"` (default) = argmax visits (current behavior). `"max_value"` = argmax `mean` among children with `visits >= robust_min_visits` (a new config field, default 5), falling back to most-visited when none qualify. The v0-improvement gate still runs AFTER selection, unchanged.

- [ ] **Step 1: Write the failing test** (append to `tests/test_searcher.py`)

Hand-verify: with children `A(visits=30, mean=0.5)`, `B(visits=8, mean=0.8)`, `robust_min_visits=5` → most_visited picks A; max_value picks B (both clear 5 visits, B has higher mean). With `robust_min_visits=10` → only A qualifies → max_value falls back to A.

```python
def test_final_pick_most_visited_default():
    s, obs, sig0, sig1 = _anchor_fixture(SearchConfig(final_move_rule="most_visited"))
    visited = {sig0: Node(visits=30, value_sum=15.0),  # mean 0.5, most visits
               sig1: Node(visits=8, value_sum=6.4)}    # mean 0.8
    assert s._final_pick(visited) == sig0


def test_final_pick_max_value_prefers_higher_mean_above_floor():
    s, obs, sig0, sig1 = _anchor_fixture(
        SearchConfig(final_move_rule="max_value", robust_min_visits=5))
    visited = {sig0: Node(visits=30, value_sum=15.0),  # mean 0.5
               sig1: Node(visits=8, value_sum=6.4)}    # mean 0.8, >=5 visits
    assert s._final_pick(visited) == sig1


def test_final_pick_max_value_falls_back_when_none_qualify():
    s, obs, sig0, sig1 = _anchor_fixture(
        SearchConfig(final_move_rule="max_value", robust_min_visits=10))
    visited = {sig0: Node(visits=30, value_sum=15.0),  # only A clears 10 visits
               sig1: Node(visits=8, value_sum=6.4)}
    assert s._final_pick(visited) == sig0
```

- [ ] **Step 2: Run to verify failure** → `uv run pytest tests/test_searcher.py -k final_pick -q` → FAIL (`_final_pick` undefined).

- [ ] **Step 3: Implement** — add `robust_min_visits: int = 5` to `SearchConfig` (below `final_move_rule`), add `_final_pick`, and use it in `search()`:

```python
    def _final_pick(self, visited: dict):
        cfg = self.config
        if cfg.final_move_rule == "max_value":
            eligible = [s for s, n in visited.items() if n.visits >= cfg.robust_min_visits]
            if eligible:
                return max(eligible, key=lambda s: visited[s].mean)
        return max(visited, key=lambda s: visited[s].visits)
```

In `search()`, replace `most_visited = max(visited, key=lambda s: visited[s].visits)` with `most_visited = self._final_pick(visited)`.

- [ ] **Step 4: Run to verify pass + full suite** → `uv run pytest tests/test_searcher.py -q && uv run pytest -q` → PASS.

- [ ] **Step 5: Commit the code**

```bash
git add "src/ptcg/search/searcher.py" "tests/test_searcher.py"
git commit -m "feat: F3 final-move-rule variant (max_value robust-child selection)"
```

- [ ] **Step 6 (agent): validate the F3 arena command with a 4-game dry run** (`--final-move-rule max_value`); clean up.

- [ ] **Step 7 (ORCHESTRATOR-RUN): F3 validation series (≥200 games)** at the best budget/gate config so far:

```bash
# ORCHESTRATOR-RUN: F3 final-move rule
uv run python scripts/run_arena.py --agent-a search --agent-b heuristic \
  --deck-a "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
  --deck-b "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
  --games 200 --search-budget-ms <BUDGET> --final-move-rule max_value \
  --collect-stats --notes "slice5 F3 max_value budget=<BUDGET>ms"
```

- [ ] **Step 8 (agent): harvest + append F3 section.** Commit EXPERIMENTS.md + sidecar + analysis.

---

### Task 13: F4 — Value-net-as-PUCT-prior (CONDITIONAL re-plan stub — the one non-concrete task)

**This task is deliberately NOT fully specified.** F4 is a structural change to the selection formula (replacing the flat UCB `unvisited → rng.choice` expansion with a value-net-derived prior in a PUCT term). It is in scope ONLY if the diagnosis (Tasks 5–9) concludes selection quality is the binding constraint AND F1–F3 (Tasks 10–12) plateau below the 0.55 gate. Fully designing PUCT now would be speculative — the concrete design depends on evidence that does not exist until Phase B/C complete.

- [ ] **Step 1 (agent/orchestrator): evaluate the F4 trigger.** After F1–F3 results are in, check both conditions:
  1. Tasks 5–9 concluded selection quality (not determinization noise, not budget starvation) is the binding constraint (D3 gate-off WON; D4 noise floor low).
  2. F1–F3 best replicated win rate is still `< 0.55`.
- [ ] **Step 2 (conditional): if BOTH hold, STOP and re-plan.** Invoke `superpowers:writing-plans` for a focused F4 sub-plan (prior extraction from the value net at expansion, PUCT `c_puct` term in `ucb_pick`, tree-node prior storage, tests, its own ≥200-game series). Record the re-plan decision in `.claude/plan.md`. Do NOT implement F4 inline in this plan.
- [ ] **Step 3 (else): mark F4 not-triggered** in the ANALYSIS doc with the two-condition evidence, and proceed to Section 4. No commit needed beyond the analysis note.

**Why this is the only allowed non-concrete task:** every other task has a fixed, testable deliverable independent of experiment outcomes. F4's design is a genuine fork that only the D/F evidence can resolve; specifying it now would violate "complete code in every step" with guesses. The trigger conditions above are concrete; the implementation is intentionally deferred to a fresh plan.

---

## Phase E — Acceptance gate & ship rule (Tasks 14–15)

### Task 14: Replicated acceptance gate + real-clock timeout-safety validation

**Files:**
- Append: `## Acceptance gate` section of `experiments/ANALYSIS-slice5-search-architecture.md`
- (No code — runs the best config from Phase D.)

**Replication recipe — copied VERBATIM from `.claude/rules/stochastic-gate-replication.md` (the authority for this gate):**

> Any acceptance/regression gate whose input is a finite random sample (an N-game arena series, a win-rate threshold) is itself a random variable. A single run that clears the bar is not evidence the underlying win rate clears the bar — it may just be a favorable draw. Before committing a result as "the gate passed" or writing the number into EXPERIMENTS.md / a spec / a docstring, run it a second time (same config) whenever the result sits close to the threshold (within roughly one confidence-interval width, or when the decision hinges on the number).

> **Practical rule:** when a stochastic gate's result is close enough to its threshold that a plausible second sample could flip the verdict, run it again before recording anything. Report BOTH (or all) runs verbatim — do not report only the run that supports the claim you want to make.

**PASS definition (resolves the spec's "both runs individually clearing a floor" — floor named here):** run 2×500 games at the best Phase-D config vs heuristic-v0 (mirror mega-lucario). PASS requires ALL of: (a) pooled win rate over the 1000 games ≥ 0.55; (b) each individual 500-game run's win rate ≥ 0.52 (the floor); (c) if the pooled result lands in the near-bar band [0.52, 0.58], run a THIRD 500-game replicate per the recipe before recording a verdict. FAIL otherwise. This is a fixed external bar (0.55), so the plain "run it again near the bar" tripwire applies — NOT the moving-bar/false-trip-probability variant (that is only for regression-pin gates whose bar is re-derived from the pool under test).

- [ ] **Step 1 (agent): pin the exact final config** (budget, gate frac/edge, final-move rule) from Phase D's best result, and write the exact two commands (identical config, different implicit RNG via engine stochasticity — the engine draws are not seeded across runs). Validate with a 4-game dry run; clean up.

- [ ] **Step 2 (ORCHESTRATOR-RUN): the replicated acceptance series (overnight — est. ~1 h/run at ~1 s budget, ~2 h total for two runs):**

```bash
# ORCHESTRATOR-RUN: acceptance gate replicate 1 (fill final config)
uv run python scripts/run_arena.py --agent-a search --agent-b heuristic \
  --deck-a "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
  --deck-b "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
  --games 500 --search-budget-ms <FINAL_BUDGET> \
  --deviate-min-visit-frac <FINAL_FRAC> --deviate-value-edge <FINAL_EDGE> \
  --final-move-rule <FINAL_RULE> --collect-stats \
  --notes "slice5 acceptance run 1 <FINAL_CONFIG>"

# ORCHESTRATOR-RUN: acceptance gate replicate 2 (identical config)
uv run python scripts/run_arena.py --agent-a search --agent-b heuristic \
  --deck-a "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
  --deck-b "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
  --games 500 --search-budget-ms <FINAL_BUDGET> \
  --deviate-min-visit-frac <FINAL_FRAC> --deviate-value-edge <FINAL_EDGE> \
  --final-move-rule <FINAL_RULE> --collect-stats \
  --notes "slice5 acceptance run 2 <FINAL_CONFIG>"
```

- [ ] **Step 3 (ORCHESTRATOR-RUN): real-clock timeout-safety validation series** (≥200 games with `--real-clock` at `<FINAL_BUDGET>`), confirming zero timeout/`max_moves`-exceeded losses and max move s within the D2 envelope:

```bash
# ORCHESTRATOR-RUN: real-clock timeout-safety validation
uv run python scripts/run_arena.py --agent-a search --agent-b heuristic \
  --deck-a "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
  --deck-b "src/ptcg/decks/candidates/mega-lucario-fighting.csv" \
  --games 200 --search-budget-ms <FINAL_BUDGET> --real-clock --collect-stats \
  --notes "slice5 acceptance real-clock validation <FINAL_CONFIG>"
```

- [ ] **Step 4 (agent): compute the verdict.** Pool the two 500-game runs; apply the PASS definition above. If pooled ∈ [0.52, 0.58], flag that a third replicate is required and STOP for the orchestrator to run it. Report BOTH (or all three) runs verbatim in the ANALYSIS `## Acceptance gate` section — pooled rate, each run's rate + Wilson CI, max move s, and the real-clock timeout-safety result. Commit EXPERIMENTS.md + sidecars + analysis.

- [ ] **Step 5 (orchestrator + Brad):** confirm the verdict (PASS → Task 15a; FAIL → Task 15b) via `AskUserQuestion`, and record it in `.claude/plan.md`.

---

### Task 15a: PASS branch — flip the ladder to the search agent

**Run ONLY if Task 14 verdict is PASS. If FAIL, skip to Task 15b.**

**Files:**
- Modify: `src/ptcg/agents/current.py` (`make_current_agent` returns the winning `SearchAgent`; `CURRENT_AGENT_NAME`)
- Modify: `tests/test_regression_pin.py` (re-derive the bar if the ladder identity changes)
- Verify: `scripts/package_submission.py` build

**Interfaces (verified landmarks):** `current.py` today has `CURRENT_AGENT_NAME = "heuristic-v0"`, `CURRENT_DECK_PATH = Path("src/ptcg/decks/candidates/mega-lucario-fighting.csv")`, and `make_current_agent(deck)` returns `HeuristicAgent()`. The regression pin (`tests/test_regression_pin.py`) calls `make_current_agent` for BOTH seats vs the sample deck with bar 0.49; changing the agent changes that measurement, so the bar must be re-derived.

- [ ] **Step 1: Write the failing identity test** (append to `tests/test_scaffold.py` or wherever `current` is unit-tested — grep `Grep -n "make_current_agent" tests` first):

```python
def test_current_agent_is_search_on_pass():
    from ptcg.agents.current import CURRENT_AGENT_NAME, make_current_agent
    from ptcg.agents.search_agent import SearchAgent
    a = make_current_agent([3] * 60)
    assert isinstance(a, SearchAgent)
    assert CURRENT_AGENT_NAME.startswith("search")
```

- [ ] **Step 2: Run to verify failure** → FAIL (returns HeuristicAgent).

- [ ] **Step 3: Implement the flip** (`src/ptcg/agents/current.py`) — return the winning `SearchAgent` with the Phase-D config, real clock, and the frac/edge/rule that passed. Example (fill the winning config):

```python
from ptcg.agents.search_agent import SearchAgent
from ptcg.search.searcher import SearchConfig
from ptcg.search.timing import TimeManager

CURRENT_AGENT_NAME = "search-v1"
CURRENT_DECK_PATH = Path("src/ptcg/decks/candidates/mega-lucario-fighting.csv")


def make_current_agent(deck: list[int]) -> Agent:
    return SearchAgent(
        deck,
        config=SearchConfig(deviate_min_visit_frac=<FINAL_FRAC>,
                            deviate_value_edge=<FINAL_EDGE>,
                            final_move_rule="<FINAL_RULE>"),
        time_manager=TimeManager(max_move_s=<FINAL_BUDGET_MS>/1000.0))
```

Note: `TimeManager` default `total_s=480.0` (the REAL clock) — the ladder agent must NOT use `total_s=1e9`.

- [ ] **Step 4: Re-derive the regression-pin bar.** The regression pin measures the NEW ladder identity vs the sample deck. Per its docstring ("Re-derive the bar whenever CURRENT_DECK_PATH changes" — here the AGENT changed), run the pin's measurement fresh. This is an ORCHESTRATOR-RUN (4×200 games, `make_current_agent` both seats vs sample); the agent then updates `MIN_WIN_RATE` and the docstring math. If the search agent is slower, note the per-game wall-clock increase.

```bash
# ORCHESTRATOR-RUN: regression-pin re-derivation (search-agent ladder identity)
uv run pytest -m slow tests/test_regression_pin.py -q   # will FAIL until bar re-derived; capture the measured rate
```

- [ ] **Step 5: Build + verify the submission bundle.**

```bash
uv run python scripts/package_submission.py "src/ptcg/decks/candidates/mega-lucario-fighting.csv"
```
Expected: builds `submission.tar.gz` and passes its verify (legal deck, agent import, pure-stdlib bundle). Confirm the bundle imports `SearchAgent` cleanly with no torch/numpy.

- [ ] **Step 6: Run the full suite + the slow acceptance guard.**

Run: `uv run pytest -q` then (ORCHESTRATOR-RUN) `uv run pytest -m slow -q`.
Expected: all green with the re-derived bars.

- [ ] **Step 7: Commit**

```bash
git add "src/ptcg/agents/current.py" "tests/test_regression_pin.py" "tests/test_scaffold.py"
git commit -m "feat: flip ladder to search agent on replicated gate PASS"
```

---

### Task 15b: FAIL branch — documented dead-end finalization

**Run ONLY if Task 14 verdict is FAIL.** The ladder stays heuristic-v0; `current.py`, `submission_main.py`, and the regression pin are UNTOUCHED (Global Constraint).

- [ ] **Step 1 (agent): finalize the dead-end in the ANALYSIS doc.** Add a `## Conclusion — documented dead-end` section: the best replicated win rate + CI, why it fell short (which bottleneck the D/F evidence pinned), what was ruled out, and the concrete next-slice hypothesis (e.g. belief-v2 precise prior, PUCT prior per F4, or a different deck). This is the primary Strategy-report artifact for the slice (70% methodology).
- [ ] **Step 2 (agent): confirm no ladder files changed** — `git status` shows `current.py`, `submission_main.py`, `test_regression_pin.py` unmodified.
- [ ] **Step 3: Commit**

```bash
git add "experiments/ANALYSIS-slice5-search-architecture.md"
git commit -m "docs: slice5 documented dead-end conclusion (ladder stays heuristic-v0)"
```

---

## Phase F — Finalization (Task 16)

### Task 16: Full-suite regression + ANALYSIS finalization + EXPERIMENTS coherence

**Files:**
- Modify: `experiments/ANALYSIS-slice5-search-architecture.md` (final pass), `CLAUDE.md` (status update)

- [ ] **Step 1: Full fast-suite regression** → `uv run pytest -q`. Expected: all green (129 baseline + all Slice-5 additions), 0 failures. Fix any breakage before proceeding.

- [ ] **Step 2: EXPERIMENTS.md coherence check.** Confirm every Slice-5 row has the correct 14-pipe shape (`uv run python -c "..."` counting pipes per new row, or `Grep -n '^| 2026-07-1' experiments/EXPERIMENTS.md`), that no row has a broken table (unescaped pipe in Notes), and that each instrumented row has a matching sidecar in `experiments/instrumentation/`. Confirm `test_experiments_append.py` (encoding guard) is green.

- [ ] **Step 3: ANALYSIS doc final pass.** Ensure D1–D4, the decision matrix, every F-section run, the acceptance verdict, and the conclusion (PASS or dead-end) are all present and internally consistent. Verify every derived number in the doc is reproduced by an inline `python -c` check or an EXPERIMENTS.md row (executable-check discipline — no prose-only numbers).

- [ ] **Step 4: CLAUDE.md status update.** Update the "Learned value net (Slice 4)" trailing status with a "Search architecture (Slice 5)" paragraph: the measured gate-fire/deviation rates, the determinization noise floor, the match-clock envelope, and the ship outcome (ladder flipped to search-vN, or stays heuristic-v0 with the pinned dead-end evidence). Reference `experiments/ANALYSIS-slice5-search-architecture.md`.

- [ ] **Step 5: Commit**

```bash
git add "experiments/ANALYSIS-slice5-search-architecture.md" "CLAUDE.md"
git commit -m "docs: slice5 finalize analysis, EXPERIMENTS coherence, CLAUDE.md status"
```

---

## Estimated arena wall-clock (for scheduling ORCHESTRATOR-RUN background shells)

| Phase | Runs | Est. wall-clock |
|---|---|---|
| D1 budget scan | 4×100 games @ 200/500/1000/2000 ms | ~45 min |
| D3 gate-off | 2×300 games @ 200 ms / 1 s | ~46 min |
| D4 noise probe | 50 states × 20 reruns @ 200 ms | ~10–20 min |
| F1/F2/F3 | 3–4 × ~200–300 games | ~1–1.5 h |
| Acceptance | 2×500 games (overnight) + 200 real-clock | ~2.5 h |
| **Total** | | **~5–7 h**, dominated by the overnight acceptance replication |

(D2 and Task 9 are pure math / docs — no arena time.)

---

## Self-Review

**1. Spec coverage** — every spec section maps to a task:
- Section 1 (instrumentation: SearchStats, run_arena aggregation, guard test) → Tasks 1–4. All six SearchStats fields the spec lists are present (iterations, root-child count + top-child visits, v0 pick sig + search pick sig, `deviated`, `gate_blocked`, begin/step failures). The seed-determinism guard is Task 1 Step 1's `test_stats_flag_does_not_change_move_sequence`. Per-move overshoot for D2 → existing `SeriesStats.max_move_seconds` + sidecar `max_move_seconds` (Task 4).
- Section 2 D1–D4 → Tasks 5–8. D4 is script-only (no arena games logged to EXPERIMENTS), matches spec.
- Section 3 F1–F4 → Tasks 10–13. F4 is the conditional stub, as the spec scopes it ("in scope only if...").
- Section 4 acceptance gate + ship rule → Tasks 14, 15a, 15b. Both branches present. Real-clock validation (spec's `TimeManager(total_s=1e9)` concern) → `--real-clock` flag (Task 4) exercised in Tasks 11/14/15a.
- Section 5 deliverables (instrumentation + tests, EXPERIMENTS logging, ANALYSIS doc, CLAUDE.md update) → Tasks 1–4 (tests), all D/F tasks (logging), Tasks 5/9/16 (ANALYSIS), Task 16 (CLAUDE.md). Success criteria 1 (diagnosis regardless of outcome) → ANALYSIS doc; 2 (PASS flips ladder) → 15a; 3 (documented dead-end) → 15b.

**2. Placeholder scan** — code steps contain complete code. Angle-bracket tokens (`<D2_BUDGET>`, `<FINAL_FRAC>`, `<FRAC>`, etc.) appear ONLY in ORCHESTRATOR-RUN command lines and the 15a config, where the value is an experiment OUTPUT that literally cannot exist at plan-write time — each is accompanied by its provenance (which task/decision produces it). This is not a code placeholder; it is a parameter bound at execution from measured evidence. F4 (Task 13) is intentionally non-concrete per the task prompt's explicit allowance, with concrete trigger conditions.

**3. Type consistency** — `SearchStats` field names are identical across Task 1 (definition), Task 2 (`summarize` consumption + `_stat` test builder), Task 4 (`write_sidecar`), and Task 8 (`probe_state` reads `last_stats.search_sig` / `.top_child_value`). `SearchConfig` new fields (`collect_stats`, `deviate_min_visit_frac`, `final_move_rule`, `robust_min_visits`) are declared inert in Task 4 Step 4 / Task 12 Step 3 and consumed in Tasks 10/12 — the forward-declaration is NAMED as tracked transient debt. `_v0_sig`/`_gate`/`_final_pick`/`_anchor` signatures are consistent between definition (Task 1/10/12) and every test call. `run_series(..., on_game_end=...)` signature matches between Task 3 (definition) and Task 4 (caller). `summarize`/`format_note`/`DecisionSummary` names match between Task 2 and Task 4. Deck path `src/ptcg/decks/candidates/mega-lucario-fighting.csv` is identical everywhere and matches `current.py`/`test_search_acceptance.py`.

**Resolved spec ambiguities:**
- *Where instrumentation metrics live in EXPERIMENTS.md* — spec says "logged into the EXPERIMENTS.md rows alongside the existing columns" (spec L24). Adding real columns would break the fixed 13-column/14-pipe `markdown_row` guarded by `tests/test_stats.py::test_markdown_row_shape` and leave slice-1–4 rows ragged. Resolved: metrics ride in the free-text `Notes` cell (`format_note`) PLUS a structured per-series sidecar JSON (`experiments/instrumentation/*.json`) for machine-readable distribution analysis. This preserves the table invariant and gives D2/D4 clean structured input (Task 4).
- *The acceptance "floor"* — spec L46 says "both runs individually clearing a floor" without naming it. Resolved: floor = each 500-game run ≥ 0.52, pooled ≥ 0.55, third replicate if pooled ∈ [0.52, 0.58] (fixed-bar variant of the replication rule, since 0.55 is external, not pool-derived) (Task 14).
- *D4 re-searching captured states* — the engine's `search_begin`-only-on-live-obs constraint (CLAUDE.md) makes offline re-search impossible; resolved by running the K reruns INLINE at live decision points via `NoiseProbeAgent` (Task 8), which is safe because the search arena is separate from the main battle.
