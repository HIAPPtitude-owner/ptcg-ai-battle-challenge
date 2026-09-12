# Slice 6 — Mixed-Policy Value Net (F4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify the off-policy value-blindness hypothesis with a pre-registered diagnostic, and if confirmed, fix it via a mixed-policy value-net retrain (Stage 1) and a value-derived root PUCT prior (Stage 2), each behind its own replicated arena gate.

**Architecture:** Extends the Slice-4 training pipeline (`trajectory.py` → `generate_training_data.py` → `train_value_net.py` → `value_net.py`) with deviation tagging and a search-agent generation mode; extends the Slice-2 searcher (`tree.py`/`searcher.py`) with an optional root prior. Every long run is orchestrator-owned; dispatched agents do setup + harvest only.

**Tech Stack:** Python 3 / uv, torch (dev-only, training), pure-stdlib inference, pytest, the `cg` engine SDK.

**Spec:** `docs/superpowers/specs/2026-07-10-slice6-mixed-policy-value-net-design.md`

## Global Constraints

- Every Python text-file write uses `encoding="utf-8"` explicitly (Windows cp1252 truncate-then-crash hazard — repo rule).
- Repo path contains a space: quote `"C:\Users\Brad\Dev Folder\Product\ptcg-ai-battle-challenge-strategy"` in every shell command.
- `src/ptcg/search/value_net.py` and anything it imports must stay stdlib-only (Kaggle bundle constraint). Torch lives only in `scripts/` and `src/ptcg/train/` (dev-only, never bundled).
- `src/ptcg/submission_main.py` and the packaged bundle stay byte-identical in every branch of this slice. The ladder-identity check is an end-gate (Task 14).
- All arena series go through `scripts/run_arena.py` (auto-logs to `experiments/EXPERIMENTS.md`). Data-generation and training runs get manual EXPERIMENTS.md rows.
- Validation steps report MEASURED VALUES, never bare PASS/FAIL. Every expected value below names its exact metric definition.
- Game-level train/val splits only (`g % 10 >= 8`); never split by position.
- AUC is the O(n log n) rank-sum implementation (existing `auc()` in `scripts/train_value_net.py`); never pairwise.
- Long-running generation/arena/training runs are launched by the ORCHESTRATOR in its own background shell. Implementer/agent tasks never wait on them.
- New pytest files go in the flat `tests/` directory, named `test_<module>.py`, following sibling patterns.

## Decision chain / task phases

```
Phase A (always):        T1 tagging  T2 gen-search-mode  T3 metrics module  T4 eval script
                         T5 [ORCH] Phase-0 diagnostic  ── DECISION D0 ──
                              confirmed → Phase B      falsified/uniform-shift → skip to T14 (STOP)
Phase B (D0 confirmed):  T6 train multi-file  T7 v2 loading + arena wiring
                         T8 [ORCH] overnight generation  T9 [ORCH] train v2
                         T10 [ORCH] Stage-1 gate  ── DECISION D1 ──
                              PASS → T14 + ladder question    parity → Phase C
Phase C (D1 parity):     T11 puct_pick  T12 searcher root prior
                         T13 [ORCH] Stage-2 probe + gate  ── DECISION D2 ──
Phase D (always):        T14 finalize (analysis doc, EXPERIMENTS coherence, CLAUDE.md, gates)
```

Pre-registered decision criteria (resolving the spec's open questions):

- **D0 (Phase 0) — AMENDED 2026-07-10 (Brad-approved, pre-registered before the Phase-0 data was seen).** Let `AUC(a)` = rank-sum AUC of the Slice-4 net on the Slice-4 val split (baseline), `AUC(c)` = on the search-game `post_deviation` bucket; `hte_AUC(x)` = the hand-tuned evaluator's AUC on the same sampled rows of bucket x. Define `deficit = AUC(a) − AUC(c)`, `hte_deficit = hte_AUC(a) − hte_AUC(c)`, `differential = deficit − hte_deficit`. **Confirmed:** `deficit ≥ 0.05` AND `differential ≥ 0.03` (the net loses materially more than the hand-tuned evaluator on the same rows — net-specific off-policy blindness). **Falsified:** `deficit < 0.02`. **Shared degradation:** `deficit ≥ 0.05` AND `differential < 0.03` — search-mirror games are intrinsically harder to predict for ANY evaluator; evidence for the alternative (no-exploitable-edge) hypothesis, not for retraining → STOP branch + re-scope AskUserQuestion. **Gray zone** (anything else, incl. deficit in [0.02, 0.05)): AskUserQuestion with measured values. Bucket (b) `on_v0_path` is INFORMATIONAL only — measured reality (T2 smoke): gate-off search deviates on ~the first decision of nearly every game, so (b) is tiny and early-game-skewed; the hte differential replaces it as the shift control. Amendment rationale: strictly conservative — it can only demote a would-be CONFIRMED, never promote.
- **D1 (Stage-1 gate).** Pooled win rate over 2×300 games (`wins / 600`, draws count as non-wins, Slice-4 protocol) ≥ 0.55 → PASS. Both runs reported verbatim.
- **Retrain quality bar (informational, gates nothing by itself):** `AUC_v2(c) ≥ AUC_v1(c) + 0.5 × (AUC_v1(a) − AUC_v1(c))` (recovers half the Phase-0 deficit) AND `AUC_v2(a) ≥ AUC_v1(a) − 0.02` (no on-policy collapse; collapse = stop and re-plan).
- **D2 (Stage-2 gate).** Same as D1, with the probe-selected `(prior_tau, c_puct)` config.

---

### Task 1: Deviation tagging in trajectory recording

**Files:**
- Modify: `src/ptcg/train/trajectory.py`
- Test: `tests/test_trajectory.py` (extend)

**Interfaces:**
- Consumes: existing `DecisionRecord`, `RecordingAgent`, `label_records`, `append_jsonl`.
- Produces: `DeviationTracker` (dataclass, field `deviated: bool = False`); `RecordingAgent.__init__(inner, sink, v0_policy=None, tracker=None)`; `DecisionRecord.post_deviation: bool`; `label_records(sink, winner, game_id, src="v0")` emitting two new JSONL keys — `"d"`: 0/1 (post-deviation at record time) and `"src"`: `"v0"`/`"search"`.

Semantics (game-level tag, spec §Phase-0 as amended): a game's tracker flips to `deviated=True` the first time EITHER player's chosen action on a 1-of-1 select (≥2 options) differs from `v0_policy`'s choice on the same observation. Records made BEFORE the flip (including the deviating decision itself — its state was reached on-policy) carry `d=0`; all later records in that game carry `d=1`. Both seats' RecordingAgents share ONE tracker per game.

- [ ] **Step 1: Write the failing tests** (extend `tests/test_trajectory.py`, following its existing fake-agent/fixture patterns — read the file first):

```python
def test_deviation_tracker_flips_once_and_tags_later_records():
    # Fake inner agent returns [1]; v0_policy returns [0] -> deviation on first 1-of-1
    # Build two decisions: record for decision 1 must have post_deviation=False
    # (state reached on-policy), record for decision 2 must have post_deviation=True.
    ...

def test_no_v0_policy_means_never_deviated():
    # RecordingAgent with v0_policy=None: all records post_deviation=False.
    ...

def test_multi_select_and_forced_do_not_flip_tracker():
    # minCount!=1 or maxCount!=1 or len(option)<2: comparison is skipped entirely.
    ...

def test_label_records_emits_d_and_src():
    # label_records(sink, winner=0, game_id=3, src="search")
    # -> every dict has "d" in (0,1) matching rec.post_deviation and "src" == "search".
    ...

def test_shared_tracker_across_seats():
    # Seat-0 agent deviates; seat-1 agent's NEXT record has post_deviation=True.
    ...
```

Flesh out each `...` using the observation fixtures already in `tests/fixtures/obs.py` / the existing test_trajectory.py fakes. Hand-verify any constants against the described semantics before transcribing (arithmetic-sanity rule).

- [ ] **Step 2: Run to verify the new tests fail** — `uv run pytest tests/test_trajectory.py -v`. Expected: new tests FAIL (missing `DeviationTracker` / unexpected kwargs); existing tests still pass.

- [ ] **Step 3: Implement.** In `src/ptcg/train/trajectory.py`:

```python
@dataclass
class DeviationTracker:
    """Game-level flag: set once EITHER seat deviates from v0 on a 1-of-1 select."""
    deviated: bool = False


@dataclass
class DecisionRecord:
    seat: int
    features: list[float]
    hte: float  # hand-tuned evaluator's score, the offline baseline column
    post_deviation: bool = False
```

`RecordingAgent` changes:

```python
class RecordingAgent(Agent):
    """Wraps any Agent; records features from the mover's perspective per decision.

    With v0_policy + tracker set, also maintains the game-level deviation flag:
    the record for the deviating decision itself stays d=0 (its state was reached
    on-policy); everything after is d=1."""

    def __init__(self, inner: Agent, sink: list[DecisionRecord],
                 v0_policy=None, tracker: DeviationTracker | None = None) -> None:
        self.inner = inner
        self.sink = sink
        self.v0_policy = v0_policy
        self.tracker = tracker
        self.name = f"rec({inner.name})"

    def act(self, obs: Observation) -> list[int]:
        st = obs.current
        live = st is not None and st.result == -1
        if live:
            seat = st.yourIndex
            self.sink.append(DecisionRecord(
                seat, extract(st, seat), evaluate(st, seat),
                post_deviation=bool(self.tracker and self.tracker.deviated)))
        result = self.inner.act(obs)
        if (live and self.tracker is not None and not self.tracker.deviated
                and self.v0_policy is not None):
            sel = obs.select
            if sel.minCount == 1 and sel.maxCount == 1 and len(sel.option) >= 2:
                try:
                    v0 = self.v0_policy(obs)
                except (ValueError, RuntimeError):
                    v0 = None
                if v0 is not None and sorted(result) != sorted(v0):
                    self.tracker.deviated = True
        return result
```

`label_records` gains `src`:

```python
def label_records(sink: list[DecisionRecord], winner: int,
                  game_id: int, src: str = "v0") -> list[dict]:
    """winner: 0/1 seat index, 2 = draw (MatchResult convention)."""
    if winner not in (0, 1, 2):
        raise ValueError(
            f"label_records called on an errored/unknown match (winner={winner})")
    out = []
    for rec in sink:
        if winner in (0, 1):
            y = 1.0 if rec.seat == winner else 0.0
        else:
            y = 0.5
        out.append({"v": FEATURE_VERSION, "g": game_id, "s": rec.seat, "y": y,
                    "hte": round(rec.hte, 4), "d": int(rec.post_deviation),
                    "src": src, "x": [round(v, 4) for v in rec.features]})
    return out
```

- [ ] **Step 4: Run tests** — `uv run pytest tests/test_trajectory.py -v`. Expected: all pass. Then full fast suite `uv run pytest` — expected 155 + new count passed, 0 failed.
- [ ] **Step 5: Commit** — `git add src/ptcg/train/trajectory.py tests/test_trajectory.py && git commit -m "feat: game-level deviation tagging in trajectory recording (slice6 T1)"`

---

### Task 2: Search-agent mode in the training-data generator

**Files:**
- Modify: `scripts/generate_training_data.py`
- Test: `tests/test_generate_training_data.py` (create)

**Interfaces:**
- Consumes: T1's `DeviationTracker`, `RecordingAgent(v0_policy=, tracker=)`, `label_records(src=)`; `SearchAgent(deck, config=, time_manager=, evaluator=)`; `SearchConfig`; `TimeManager(total_s=, max_move_s=)`; `ValueNetEvaluator.load_default()`.
- Produces: CLI flags `--agent {v0,search}` (default `v0`), `--search-budget-ms` (default 200), `--gate {on,off}` (default `off`), plus a pure helper `build_game_agents(agent_kind, deck_len_hint_a, deck_len_hint_b, budget_ms, gate)` — actually expose it as `make_agents(agent_kind: str, deck_a: list[int], deck_b: list[int], budget_ms: int, gate: str, sink: list, tracker) -> tuple[Agent, Agent]` so the wiring is unit-testable without playing games.

Generation config (deployment-matched to the Stage-1 gate): search mode uses `SearchConfig(rollout_depth=0, deviate_min_visits=0, deviate_value_edge=0.0)` when `--gate off` (raw search, ~34% deviation rate maximizes off-policy coverage), `SearchConfig(rollout_depth=0)` when `--gate on`; evaluator = `ValueNetEvaluator.load_default()` (the Slice-4 v1 net — the same net the deployed search would carry); fresh `TimeManager(total_s=1e9, max_move_s=budget_ms/1000.0)` per agent per game (matches `run_arena`'s non-real-clock arm). v0 mode is byte-for-byte the existing behavior with `src="v0"`.

- [ ] **Step 1: Write the failing test** (`tests/test_generate_training_data.py`):

```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from generate_training_data import make_agents  # noqa: E402
from ptcg.train.trajectory import DeviationTracker, RecordingAgent  # noqa: E402


def test_make_agents_search_gate_off_config():
    sink, tracker = [], DeviationTracker()
    deck = [3] * 60
    a0, a1 = make_agents("search", deck, deck, 200, "off", sink, tracker)
    for a in (a0, a1):
        assert isinstance(a, RecordingAgent)
        inner = a.inner
        assert inner.searcher.config.deviate_min_visits == 0
        assert inner.searcher.config.deviate_value_edge == 0.0
        assert inner.searcher.config.rollout_depth == 0
        assert abs(inner.tm.move_budget() - 0.2) < 1e-9
        assert a.tracker is tracker and a.v0_policy is not None


def test_make_agents_v0_has_no_tracker():
    sink, tracker = [], DeviationTracker()
    a0, a1 = make_agents("v0", [3] * 60, [3] * 60, 200, "off", sink, tracker)
    for a in (a0, a1):
        assert a.tracker is None and a.v0_policy is None
```

Note: `TimeManager.move_budget()` semantics — verify against `src/ptcg/search/timing.py` before asserting 0.2 exactly; if the manager clamps or reserves margin, assert the actual formula's value (report the divergence in your task report, don't force the plan's number). `[3]*60` is the repo's sample-deck convention.

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_generate_training_data.py -v`. Expected: ImportError (`make_agents` not defined).

- [ ] **Step 3: Implement.** In `scripts/generate_training_data.py`, add imports and the factory:

```python
from ptcg.agents.search_agent import SearchAgent  # noqa: E402
from ptcg.search.searcher import SearchConfig  # noqa: E402
from ptcg.search.timing import TimeManager  # noqa: E402
from ptcg.search.value_net import ValueNetEvaluator  # noqa: E402
from ptcg.train.trajectory import DeviationTracker  # noqa: E402


def make_agents(agent_kind, deck_a, deck_b, budget_ms, gate, sink, tracker):
    """Two recording agents for one game. Search mode is deployment-matched to the
    Stage-1 gate config (rollout_depth=0, v1-net evaluator); gate=off zeroes the
    v0-improvement gate so raw search deviations reach the game record."""
    if agent_kind == "v0":
        return (RecordingAgent(HeuristicAgent(), sink),
                RecordingAgent(HeuristicAgent(), sink))
    if gate == "off":
        cfg = SearchConfig(rollout_depth=0, deviate_min_visits=0,
                           deviate_value_edge=0.0)
    else:
        cfg = SearchConfig(rollout_depth=0)
    v0 = HeuristicAgent()
    agents = []
    for deck in (deck_a, deck_b):
        inner = SearchAgent(deck, config=cfg,
                            time_manager=TimeManager(total_s=1e9,
                                                     max_move_s=budget_ms / 1000.0),
                            evaluator=ValueNetEvaluator.load_default())
        agents.append(RecordingAgent(inner, sink, v0_policy=v0.act,
                                     tracker=tracker))
    return agents[0], agents[1]
```

Rework `run()` to take `agent_kind, budget_ms, gate` and build per-game agents via the factory (fresh `sink`, fresh `DeviationTracker()` per game, seat alternation unchanged), passing `src=agent_kind if agent_kind == "search" else "v0"`... use exactly `src="search"` / `src="v0"`. `label_records(sink, result.winner, game_id, src=src)`. Update `main()`:

```python
    p.add_argument("--agent", choices=["v0", "search"], default="v0")
    p.add_argument("--search-budget-ms", type=int, default=200)
    p.add_argument("--gate", choices=["on", "off"], default="off")
```

`--measure` in search mode: 20 games on the FIRST pairing with the same factory, print `f"{per:.2f}s/game -> est {per * 45 * args.games_per_pairing / 3600:.1f} h for 45x{args.games_per_pairing} games"`, writing to `experiments/data/slice6/_measure.jsonl`. Default `--out` stays the slice-4 path for v0 mode; document that search runs pass `--out experiments/data/slice6/<name>.jsonl` explicitly.

- [ ] **Step 4: Run tests** — `uv run pytest tests/test_generate_training_data.py tests/test_trajectory.py -v` then full `uv run pytest`. Expected: all pass. Also run a 1-game real smoke: `uv run python scripts/generate_training_data.py --agent search --games-per-pairing 1 --out experiments/data/slice6/_smoke.jsonl` interrupted after the first `DONE`/progress line OR temporarily with a single pairing — simplest: run `--measure --agent search` and confirm it completes 20 games and prints a measured s/game (this doubles as an early throughput reading; report the number). Delete `_smoke`/`_measure` files afterward is NOT needed (gitignored) but report their row counts.
- [ ] **Step 5: Commit** — `git add scripts/generate_training_data.py tests/test_generate_training_data.py && git commit -m "feat: search-agent generation mode with deviation tagging (slice6 T2)"`

---

### Task 3: Factor training metrics into an importable module

**Files:**
- Create: `src/ptcg/train/metrics.py`
- Modify: `scripts/train_value_net.py` (delete its local `auc`/`metrics`, import instead)
- Test: `tests/test_metrics.py` (create)

**Interfaces:**
- Produces: `ptcg.train.metrics.auc(scores: torch.Tensor, labels: torch.Tensor) -> float` and `ptcg.train.metrics.metrics(scores, labels) -> dict` — MOVED VERBATIM from `scripts/train_value_net.py` lines 33–69 (docstrings included), plus a module docstring: `"""Offline eval metrics (torch, dev-only — never bundled)."""`.
- Consumes: nothing new. `scripts/train_value_net.py` and (later) `scripts/eval_value_net.py` import from here.

- [ ] **Step 1: Write the failing test** (`tests/test_metrics.py`) — hand-verified vectors (verify the 0.875 by hand before transcribing: pos scores {0.9, 0.8}, neg {0.8, 0.3}; pairs 0.9>0.8 ✓, 0.9>0.3 ✓, 0.8 vs 0.8 tie=0.5, 0.8>0.3 ✓ → (1+1+0.5+1)/4 = 0.875):

```python
import torch

from ptcg.train.metrics import auc, metrics


def test_auc_hand_computed_with_ties():
    scores = torch.tensor([0.9, 0.8, 0.8, 0.3])
    labels = torch.tensor([1.0, 0.0, 1.0, 0.0])
    assert abs(auc(scores, labels) - 0.875) < 1e-9


def test_auc_excludes_draws_and_degenerate_returns_half():
    scores = torch.tensor([0.9, 0.1, 0.5])
    labels = torch.tensor([1.0, 1.0, 0.5])  # no negatives after draw exclusion
    assert auc(scores, labels) == 0.5


def test_metrics_keys_and_ranges():
    scores = torch.tensor([0.9, 0.2])
    labels = torch.tensor([1.0, 0.0])
    m = metrics(scores, labels)
    assert set(m) == {"bce", "acc", "auc"} and m["acc"] == 1.0 and m["auc"] == 1.0
```

- [ ] **Step 2: Verify failure** — `uv run pytest tests/test_metrics.py -v`. Expected: ModuleNotFoundError.
- [ ] **Step 3: Implement** — create the module (verbatim move + docstring), replace the two functions in `scripts/train_value_net.py` with `from ptcg.train.metrics import auc, metrics  # noqa: E402` (keep the `sys.path.insert` above it; delete the now-unused local defs; `auc` is referenced only via `metrics` and the import).
- [ ] **Step 4: Run** — `uv run pytest tests/test_metrics.py tests/test_train_export.py -v` then full suite. Expected: pass (test_train_export exercises the train script's import path).
- [ ] **Step 5: Commit** — `git add src/ptcg/train/metrics.py scripts/train_value_net.py tests/test_metrics.py && git commit -m "refactor: factor rank-sum AUC/metrics into ptcg.train.metrics (slice6 T3)"`

---

### Task 4: Bucket evaluation script

**Files:**
- Create: `scripts/eval_value_net.py`
- Test: `tests/test_eval_value_net.py` (create)

**Interfaces:**
- Consumes: `ValueNet` (stdlib inference), `ptcg.train.metrics.auc`/`metrics` (torch ok — dev script), JSONL rows with keys `v,g,s,y,hte,x` and optional `d` (default 0), `src` (default `"v0"`).
- Produces: CLI `uv run python scripts/eval_value_net.py --weights <json> --baseline-data <jsonl> --search-data <jsonl> [--max-rows-per-bucket 100000] [--seed 0]`; importable functions `load_rows(path) -> list[dict]`, `bucketize(baseline_rows, search_rows) -> dict[str, list[dict]]` (keys `"a_v0_val"`, `"b_search_onpath"`, `"c_search_postdev"`), `score_bucket(net, rows, cap, seed) -> dict` (returns `{"n": ..., "n_decisive": ..., "auc": ..., "bce": ..., "acc": ...}`).

Bucket definitions (exact): `a_v0_val` = baseline rows with `g % 10 >= 8` (the Slice-4 val split — never train rows, the net saw those); `b_search_onpath` = ALL search rows with `d == 0` (no split needed — the net was never trained on any of them); `c_search_postdev` = ALL search rows with `d == 1`. Rows with `y == 0.5` stay in the bucket (BCE uses them) but are excluded from AUC by `auc()` itself. `--max-rows-per-bucket` takes a seeded `random.Random(seed).sample` when a bucket exceeds the cap; the report prints both the bucket's full size and the n actually scored.

Output format (stdout, one line per bucket + a advisory verdict):

```
bucket a_v0_val: n=100000/264k n_decisive=... auc=0.XXXX bce=0.XXXX acc=0.XXXX
bucket b_search_onpath: ...
bucket c_search_postdev: ...
deficit(a-c)=0.XXXX deficit(a-b)=0.XXXX
advisory: CONFIRMED|FALSIFIED|UNIFORM-SHIFT|GRAY per D0 thresholds (>=0.05 / <0.02 bands)
```

The advisory line is a convenience; the orchestrator records the decision from the printed numbers (measured-values rule).

- [ ] **Step 1: Write the failing test** (`tests/test_eval_value_net.py`). Build a hand-computable weights spec — a single linear layer that passes feature 0 through: `{"feature_version": <FEATURE_VERSION>, "feature_names": [...], "layers": [{"w": [[1.0, 0, ..., 0]], "b": [0.0]}], "golden": []}` — `ValueNet.predict(x)` on that spec is `sigmoid(x[0])` (hand-verify: x0=0 → 0.5; the final layer clamp only binds at |z|>60). Write two tiny JSONL files via `json.dumps` (use 40-length x vectors, matching FEATURE_NAMES length); assert `bucketize` sizes, assert `score_bucket` AUC on a 4-row bucket against a hand-computed rank-sum value, assert rows with missing `d`/`src` default to 0/"v0". All temp files via pytest `tmp_path`, written with `encoding="utf-8"`.
- [ ] **Step 2: Verify failure** — expected ModuleNotFoundError/ImportError.
- [ ] **Step 3: Implement** the script with the interfaces above. Scoring loop: `scores = [net.predict(r["x"]) for r in rows]` then `metrics(torch.tensor(scores), torch.tensor([r["y"] for r in rows]))`. Follow the `sys.path.insert` header convention of sibling scripts.
- [ ] **Step 4: Run** — `uv run pytest tests/test_eval_value_net.py -v`, then full suite. Then one REAL smoke: run the script with `--weights src/ptcg/search/value_net_weights.json --baseline-data experiments/data/slice4/train_v1.jsonl --search-data experiments/data/slice6/_measure.jsonl --max-rows-per-bucket 20000` and report the printed lines (bucket a's AUC should land near the recorded 0.8971 — report the measured value; a large discrepancy means a bucketize/split bug, stop and investigate).
- [ ] **Step 5: Commit** — `git add scripts/eval_value_net.py tests/test_eval_value_net.py && git commit -m "feat: per-bucket value-net eval script for Phase-0 diagnostic (slice6 T4)"`

---

### Task 5: [ORCHESTRATOR] Phase-0 diagnostic — DECISION D0

No implementer dispatch for the runs. Orchestrator executes; a small agent may draft the analysis section afterward from the harvested numbers.

- [ ] **Step 1: Throughput probe** — `uv run python scripts/generate_training_data.py --agent search --measure` (background shell). Record measured s/game. Sanity vs the 6–12s estimate; if wildly off, re-check before proceeding (verify-throughput rule).
- [ ] **Step 2: Launch Phase-0 generation** (background, orchestrator-owned): `uv run python scripts/generate_training_data.py --agent search --gate off --games-per-pairing 7 --out experiments/data/slice6/phase0_search.jsonl --seed 61` (45 pairings × 7 = 315 games; at 6–12s/game ≈ 32–63 min). Poll via `Get-Process`; harvest the `DONE:` line (games, errors, seconds).
- [ ] **Step 3: Run the bucket eval** — `uv run python scripts/eval_value_net.py --weights src/ptcg/search/value_net_weights.json --baseline-data experiments/data/slice4/train_v1.jsonl --search-data experiments/data/slice6/phase0_search.jsonl` and capture all printed lines.
- [ ] **Step 4: Record** — create `experiments/ANALYSIS-slice6-mixed-policy-value-net.md` with a Phase-0 section: procedure, bucket sizes, measured AUC/BCE per bucket, deficits, D0 verdict against the pre-registered thresholds. Add manual EXPERIMENTS.md rows for the probe + generation run (date, config, games, s/game, output path). Commit.
- [ ] **Step 5: DECISION D0** — apply the pre-registered criterion. Confirmed → proceed to Phase B. Falsified or uniform-shift → skip to Task 14 (STOP branch), then AskUserQuestion re-scope. Gray zone → AskUserQuestion with the measured deficits.

---

### Task 6: Multi-file training input with per-bucket validation reporting

**Files:**
- Modify: `scripts/train_value_net.py`
- Test: `tests/test_train_export.py` (extend, following its existing structure)

**Interfaces:**
- Consumes: T3's metrics module; JSONL with optional `d`/`src`.
- Produces: `--data` becomes `nargs="+"` (one or more paths); `load_jsonl(paths: list[Path])` returns the existing 4 tensors PLUS `d` (int64) and `src_is_search` (bool) tensors; game-id namespacing — file k's ids are offset by `1 + max(g)` of all prior files so ids never collide and the `g % 10 >= 8` split stays a deterministic by-game 80/20; per-bucket val metrics printed after training:

```
NET  val[v0]:        {...}   (src=v0 rows in val split)
NET  val[search-on]: {...}   (src=search, d=0, val split)
NET  val[search-dev]:{...}   (src=search, d=1, val split)
HTE  val[v0]:        {...}
```

`meta` in the exported spec gains `"n_search_train"`, `"n_search_val"`, `"val_auc_search_dev"` fields; everything else (arch, early stopping, golden export, parity check) unchanged.

- [ ] **Step 1: Write the failing test** — extend `tests/test_train_export.py` with a loader-level test: write two tiny JSONL files (game ids 0..3 in each, second file rows carrying `"src": "search"`, some `"d": 1`), call the script's `load_jsonl([p1, p2])`, assert: ids from file 2 are all `> max(file-1 ids)` (offset applied), the union has no duplicate (g, s, row) collisions across files, `src_is_search` is False for file-1 rows and True for file-2 rows, `d` defaults to 0 where absent. Follow the existing test's import pattern for the script module.
- [ ] **Step 2: Verify failure** — signature mismatch (load_jsonl takes a single Path today).
- [ ] **Step 3: Implement.** Loader sketch:

```python
def load_jsonl(paths: list[Path]):
    xs, ys, htes, games, ds, srcs = [], [], [], [], [], []
    offset = 0
    for path in paths:
        max_g = -1
        with open(path, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if r["v"] != FEATURE_VERSION:
                    raise SystemExit(
                        f"record feature_version {r['v']} != {FEATURE_VERSION}")
                g = r["g"] + offset
                max_g = max(max_g, g)
                xs.append(r["x"]); ys.append(r["y"]); htes.append(r["hte"])
                games.append(g)
                ds.append(int(r.get("d", 0)))
                srcs.append(r.get("src", "v0") == "search")
        offset = max_g + 1
    return (torch.tensor(xs, dtype=torch.float32),
            torch.tensor(ys, dtype=torch.float32),
            torch.tensor(htes, dtype=torch.float32),
            torch.tensor(games, dtype=torch.int64),
            torch.tensor(ds, dtype=torch.int64),
            torch.tensor(srcs, dtype=torch.bool))
```

Bucket masks in main(): `val_mask = (g % 10) >= 8` as today; `v0_val = val_mask & ~src_is_search`, `son_val = val_mask & src_is_search & (d == 0)`, `sdev_val = val_mask & src_is_search & (d == 1)`. Print all four metric lines (skip a bucket with 0 rows, printing `n=0` instead of crashing). Note: offsetting shifts `g % 10` bucket membership within later files — that is fine (still a deterministic by-game 80/20); do NOT try to preserve per-file mod classes.

- [ ] **Step 4: Run** — extended test + full suite; then a REAL micro-train smoke: `uv run python scripts/train_value_net.py --data experiments/data/slice6/phase0_search.jsonl --out experiments/data/slice6/_smoke_weights.json --epochs 1` — confirm it trains 1 epoch, prints the per-bucket lines with nonzero search buckets, passes parity, writes the file. Report the printed bucket lines verbatim.
- [ ] **Step 5: Commit** — `git add scripts/train_value_net.py tests/test_train_export.py && git commit -m "feat: multi-file training input + per-bucket val reporting (slice6 T6)"`

---

### Task 7: v2 weights loading and arena wiring

**Files:**
- Modify: `src/ptcg/search/value_net.py`, `scripts/run_arena.py`
- Test: `tests/test_value_net.py` (extend), `tests/test_run_arena_metrics.py` (extend if it covers AGENTS construction; otherwise a small new test in it)

**Interfaces:**
- Produces: `ValueNetEvaluator.load(path: str | Path) -> ValueNetEvaluator` (classmethod; `load_default()` becomes `return cls.load(DEFAULT_WEIGHTS_PATH)`); `run_arena.py --net-weights <path>` (default `None` = v1 default) applied to the `search-net` agent, and when set the constructed agent's `name` is reassigned to `f"search-net-{Path(args.net_weights).stem.replace('value_net_weights_', '')}"` (so `value_net_weights_v2.json` → `search-net-v2` in EXPERIMENTS rows).

- [ ] **Step 1: Failing tests.** In `tests/test_value_net.py`: write a valid tiny spec dict to `tmp_path / "w.json"` (with correct `feature_version`), assert `ValueNetEvaluator.load(p)` returns an evaluator whose `net.predict` works; assert `load` with a wrong `feature_version` raises `ValueError`. For run_arena: following `tests/test_run_arena_metrics.py`'s import pattern, test that the `search-net` AGENTS entry with a fake args namespace carrying `net_weights=<tmp spec path>` builds an agent named `search-net-w` (stem `w`) — read the existing test file first and mirror how it fakes args.
- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Implement** (value_net.py):

```python
    @classmethod
    def load(cls, path: str | Path) -> "ValueNetEvaluator":
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(ValueNet(spec))

    @classmethod
    def load_default(cls) -> "ValueNetEvaluator":
        return cls.load(DEFAULT_WEIGHTS_PATH)
```

run_arena.py: add `p.add_argument("--net-weights", default=None)`; change the `search-net` lambda to use `ValueNetEvaluator.load(a.net_weights) if a.net_weights else ValueNetEvaluator.load_default()`; after `agent_a`/`agent_b` construction, for each agent that is a `search-net` kind and `args.net_weights` is set, reassign `.name` as specified above (do it inline right after the AGENTS call, keyed on the CLI choice, not isinstance).

- [ ] **Step 4: Run** — targeted tests + full suite. Expected: pass.
- [ ] **Step 5: Commit** — `git add src/ptcg/search/value_net.py scripts/run_arena.py tests/test_value_net.py tests/test_run_arena_metrics.py && git commit -m "feat: loadable value-net weights + --net-weights arena flag (slice6 T7)"`

---

### Task 8: [ORCHESTRATOR] Overnight mixed-policy generation

- [ ] **Step 1: Size the run** — RESOLVED 2026-07-10 (Brad-approved): measured 32.05 s/game puts a 12h run at ~1,350 games (30/pairing × 45), far below the original [70, 110] clamp. Decision: accept ~1,350 games at the deployment-matched 200ms budget (`--games-per-pairing 30`), ~74k positions (~5% blend share). Pre-authorized follow-up if the blend proves too thin (search-val bucket < ~5k rows or retrain quality bar misses): ONE documented 2× upsample-search-rows training experiment, both runs reported.
- [ ] **Step 2: Launch** (background, orchestrator-owned): `uv run python scripts/generate_training_data.py --agent search --gate off --games-per-pairing <N> --out experiments/data/slice6/train_search_v1.jsonl --seed 62`. This is the overnight run; schedule/hand-off per background-arena-execution rule (dry-run pre-flight: re-confirm the command shape with `--measure` output from T5 — already done — and confirm the out-path is writable).
- [ ] **Step 3: Harvest** — record the `DONE:` line, file size, row count (`Get-Content ... | Measure-Object -Line` is slow at ~400MB; prefer a 3-line Python count with utf-8 encoding). Manual EXPERIMENTS.md row. Commit (docs only — the JSONL is gitignored).

---

### Task 9: [ORCHESTRATOR] Train v2 + retrain quality bar

- [ ] **Step 1: Launch training** (background, orchestrator-owned): `uv run python scripts/train_value_net.py --data experiments/data/slice4/train_v1.jsonl experiments/data/slice6/train_search_v1.jsonl --out src/ptcg/search/value_net_weights_v2.json --seed 0`. Harvest the epoch log, per-bucket val lines, parity line.
- [ ] **Step 2: Retrain quality bar** — run `scripts/eval_value_net.py` twice (v1 weights, v2 weights) with `--search-data experiments/data/slice6/phase0_search.jsonl` and compare against the pre-registered bar: `AUC_v2(c) ≥ AUC_v1(c) + 0.5 × (AUC_v1(a) − AUC_v1(c))` and `AUC_v2(a) ≥ AUC_v1(a) − 0.02`. NOTE the subtlety: v2 was trained on data that includes phase0_search.jsonl rows — bucket (c) here is optimistically biased for v2. The honest bar uses the TRAIN-SCRIPT's own val-split bucket lines (search-dev val rows are held out by game). Use the train script's `NET val[search-dev]` line as `AUC_v2(c)` and the T5 eval-script value as `AUC_v1(c)` restricted to... they are different row sets — acceptable: both estimate the same population quantity (post-deviation AUC); state this in the analysis doc. Record measured values for every term.
- [ ] **Step 3: Commit** the weights + analysis-doc section: `git add src/ptcg/search/value_net_weights_v2.json experiments/ANALYSIS-slice6-mixed-policy-value-net.md experiments/EXPERIMENTS.md && git commit -m "feat: mixed-policy value net v2 weights + retrain quality analysis (slice6 T9)"`. If the on-policy-collapse stop condition fires, STOP and re-plan (AskUserQuestion).

---

### Task 10: [ORCHESTRATOR] Stage-1 gate — DECISION D1

- [ ] **Step 1: Pre-gate probe (optional but default-on):** 2×100-game series, rollout-depth 0 vs 12, both `--agent-a search-net --net-weights src/ptcg/search/value_net_weights_v2.json --agent-b heuristic --search-budget-ms 200 --collect-stats --games 100`, decks = the same deck pair the Slice-4 gate used (grep `experiments/EXPERIMENTS.md` for the slice-4 gate rows and replicate the deck args exactly). Pick the higher pooled win rate; tie → rollout-depth 0 (Slice-4 comparability).
- [ ] **Step 2: Gate runs** (background, orchestrator-owned, sequential): two independent 300-game series at the chosen config, distinct `--notes "slice6-stage1-gate-run1/2"`. Pooled decision: `wins/600 ≥ 0.55`. All runs land in EXPERIMENTS.md automatically.
- [ ] **Step 3: Record + DECISION D1** — analysis-doc Stage-1 section with both runs verbatim + pooled CI. PASS → Task 14 then the ladder-flip AskUserQuestion. Parity → Phase C. Commit.

---

### Task 11: PUCT root selection primitive

**Files:**
- Modify: `src/ptcg/search/tree.py`
- Test: `tests/test_tree.py` (extend)

**Interfaces:**
- Produces: `puct_pick(node: Node, legal: Sequence[ActionSig], priors: dict[ActionSig, float], c_puct: float) -> ActionSig`. Unvisited children score `q = 0.5` (neutral); missing prior keys default 0.0; deterministic argmax (no rng — the prior breaks the symmetry that made `ucb_pick` randomize unvisited).
- Consumes: existing `Node`, `ActionSig`.

- [ ] **Step 1: Failing tests** — hand-verify BOTH vectors before transcribing (arithmetic-sanity). Vector 1: node.visits=4; child A visits=2 value_sum=1.0 (q=0.5), child B visits=2 value_sum=1.2 (q=0.6); priors A=0.8, B=0.2; c_puct=1.0; sqrt(4)=2 → score(A)=0.5+0.8·2/3=1.03333, score(B)=0.6+0.2·2/3=0.73333 → A wins despite lower q. Vector 2 (unvisited neutrality): node.visits=1; child A visits=1 value_sum=1.0 (q=1.0); B unvisited (q=0.5); priors A=0.0, B=1.0; c_puct=1.0; sqrt(1)=1 → score(A)=1.0+0=1.0, score(B)=0.5+1.0·1/1=1.5 → B.

```python
def test_puct_prior_outweighs_value():
    node = Node(visits=4)
    node.children[SIG_A] = Node(visits=2, value_sum=1.0)
    node.children[SIG_B] = Node(visits=2, value_sum=1.2)
    got = puct_pick(node, [SIG_A, SIG_B], {SIG_A: 0.8, SIG_B: 0.2}, 1.0)
    assert got == SIG_A

def test_puct_unvisited_gets_neutral_q_plus_prior():
    node = Node(visits=1)
    node.children[SIG_A] = Node(visits=1, value_sum=1.0)
    got = puct_pick(node, [SIG_A, SIG_B], {SIG_A: 0.0, SIG_B: 1.0}, 1.0)
    assert got == SIG_B

def test_puct_missing_prior_defaults_zero():
    node = Node(visits=1)
    got = puct_pick(node, [SIG_A, SIG_B], {SIG_A: 0.5}, 1.0)
    assert got == SIG_A  # B: q=0.5+0; A: q=0.5+0.5*1/1=1.0
```

(Define `SIG_A`/`SIG_B` as distinct tuples following test_tree.py's existing signature fixtures.)

- [ ] **Step 2: Verify failure** — ImportError.
- [ ] **Step 3: Implement:**

```python
def puct_pick(node: Node, legal: Sequence[ActionSig],
              priors: dict[ActionSig, float], c_puct: float) -> ActionSig:
    """AlphaZero-style PUCT: q + c * P(a) * sqrt(N) / (1 + n(a)).

    Unvisited children take a neutral q of 0.5 so the prior term (not optimism)
    drives first visits; unlike ucb_pick there is no unvisited-first sweep."""
    sqrt_n = math.sqrt(max(node.visits, 1))

    def score(sig: ActionSig) -> float:
        child = node.children.get(sig)
        n = child.visits if child is not None else 0
        q = child.mean if child is not None and n > 0 else 0.5
        return q + c_puct * priors.get(sig, 0.0) * sqrt_n / (1 + n)

    return max(legal, key=score)
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_tree.py -v` + full suite. Expected: pass.
- [ ] **Step 5: Commit** — `git add src/ptcg/search/tree.py tests/test_tree.py && git commit -m "feat: puct_pick root selection primitive (slice6 T11)"`

---

### Task 12: Root prior in the searcher

**Files:**
- Modify: `src/ptcg/search/searcher.py`, `scripts/run_arena.py`
- Test: `tests/test_searcher.py` (extend)

**Interfaces:**
- Consumes: T11's `puct_pick`; existing `Searcher._simulate`/`search` structure, `belief.sample`, backend protocol.
- Produces: `SearchConfig` fields `use_root_prior: bool = False`, `prior_tau: float = 0.1`, `c_puct: float = 1.5`; `Searcher._root_priors(obs, my_index) -> dict[ActionSig, float] | None`; `_simulate(state, root, my_index, priors=None)`; run_arena flags `--root-prior` (store_true), `--prior-tau` (float, 0.1), `--c-puct` (float, 1.5) wired into `build_search_config`.

Behavioral contract: with `use_root_prior=False` (default) the searcher's move sequence is BIT-IDENTICAL to today (guard test, same pattern as `test_stats_flag_does_not_change_move_sequence`). With the flag on, priors are computed ONCE per `search()` call before the iteration loop: one belief determinization, then for each root option `begin → step([i]) → static-evaluate → end`; softmax over values at temperature `prior_tau` (subtract max before exp — overflow guard). If ANY step fails or the deadline arrives mid-computation, return None and fall back to plain UCB for the whole move (partial priors would bias exploration toward whichever actions got scored).

- [ ] **Step 1: Failing tests** (extend `tests/test_searcher.py`, reusing its fake-backend fixtures — read the file's existing fakes first):

```python
def test_root_prior_off_is_bit_identical():
    # Two searchers, same seed/config except use_root_prior absent vs explicit False
    # on a recorded fake backend: identical chosen moves across a scripted sequence.
    ...

def test_root_prior_steers_first_visits():
    # Fake backend where evaluator scores child B >> child A; use_root_prior=True,
    # max_iterations small (e.g. 4), assert B's visit count > A's, and that
    # _root_priors returned P(B) > P(A) (softmax ordering matches evaluator ordering).
    ...

def test_root_prior_failure_falls_back_to_ucb():
    # Fake backend whose begin() raises on the prior-computation calls (count-based):
    # search() still returns a move (UCB path), and begin_failures incremented.
    ...
```

- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Implement.** In `SearchConfig` add the three fields (with the comment `# Slice-6 F4: value-derived root prior`). In `Searcher`, add `import math` at module top, then:

```python
    def _root_priors(self, obs: Observation,
                     my_index: int) -> dict[ActionSig, float] | None:
        """Value-derived root prior: step each legal root action once under a single
        determinization and softmax the static evals at prior_tau. All-or-nothing:
        returns None on any engine failure or deadline hit — partial priors would
        bias exploration toward whichever actions happened to get scored."""
        cfg = self.config
        sel = obs.select
        det = self.belief.sample(obs, self.rng)
        values: dict[ActionSig, float] = {}
        for i in range(len(sel.option)):
            if time.perf_counter() >= self._deadline:
                return None
            sig = action_signature(sel, (i,))
            try:
                state = self.backend.begin(obs, det)
                st = self.backend.step(state.searchId, [i]).observation.current
                values[sig] = (self._terminal_value(st, my_index)
                               if st.result != -1
                               else self.evaluator(st, my_index))
            except (ValueError, RuntimeError):
                self.begin_failures += 1
                return None
            finally:
                self.backend.end()
        if not values:
            return None
        m = max(values.values())
        exps = {s: math.exp((v - m) / cfg.prior_tau) for s, v in values.items()}
        z = sum(exps.values())
        return {s: e / z for s, e in exps.items()}
```

In `search()`, after `root = Node()` setup and before the while loop:

```python
        priors: dict[ActionSig, float] | None = None
        if cfg.use_root_prior:
            priors = self._root_priors(obs, my_index)
```

Pass `priors` into `_simulate(state, root, my_index, priors)`; in `_simulate`, thread the param and replace the selection line:

```python
            if node is root and priors is not None:
                sig = puct_pick(node, list(legal), priors, self.config.c_puct)
            else:
                sig = ucb_pick(node, list(legal), self.config.c_uct, self.rng)
```

(`node is root` is True only at depth 0 — the walk reassigns `node` immediately after the first pick.) Import `puct_pick` in the existing `from ptcg.search.tree import ...` line. In `run_arena.py`: add the three argparse flags and extend `build_search_config` with `use_root_prior=args.root_prior, prior_tau=args.prior_tau, c_puct=args.c_puct`.

- [ ] **Step 4: Run** — `uv run pytest tests/test_searcher.py tests/test_search_agent.py -v` + full suite. Then ONE real 5-game smoke: `uv run python scripts/run_arena.py --agent-a search-net --net-weights src/ptcg/search/value_net_weights_v2.json --root-prior --agent-b heuristic --games 5 --notes "slice6-T12-smoke"` — confirm it completes with zero fallbacks explosion (report the printed line; fallbacks are visible via collect-stats runs later, here just confirm no crash and plausible s/game).
- [ ] **Step 5: Commit** — `git add src/ptcg/search/searcher.py scripts/run_arena.py tests/test_searcher.py && git commit -m "feat: value-derived root PUCT prior behind use_root_prior flag (slice6 T12)"`

---

### Task 13: [ORCHESTRATOR] Stage-2 probe + gate — DECISION D2

- [ ] **Step 1: Probe sweep** (background, sequential, orchestrator-owned): 4×100-game series `search-net(v2, --root-prior)` vs heuristic at 200ms over `prior_tau ∈ {0.05, 0.2} × c_puct ∈ {1.0, 2.5}`, `--collect-stats`, distinct notes. Pick argmax pooled win rate (tie → tau=0.1/c_puct=1.5 defaults — i.e., re-run a 100-game series at defaults as a 5th arm only on a tie).
- [ ] **Step 2: Gate runs** — 2×300 games at the selected config, pooled `wins/600 ≥ 0.55` (D1 protocol). All EXPERIMENTS-logged.
- [ ] **Step 3: Record + DECISION D2** — analysis-doc Stage-2 section, both runs verbatim, pooled CI. PASS → ladder-flip AskUserQuestion at Task 14. Parity → documented dead-end; carry forward remaining hypotheses (policy head; no-exploitable-edge) as Slice-7 candidates. Commit.

---

### Task 14: Finalize (always runs, on every branch)

**Files:**
- Modify: `experiments/ANALYSIS-slice6-mixed-policy-value-net.md`, `experiments/EXPERIMENTS.md` (coherence pass), `CLAUDE.md` (status paragraph)

- [ ] **Step 1: Full regression** — `uv run pytest` (expect all green; count reported) and `uv run pytest -m slow` ONLY if a gate passed and the ladder flip is on the table (the slow acceptance series pins the heuristic agent; on STOP/parity branches the fast suite suffices).
- [ ] **Step 2: Ladder identity** — diff `src/ptcg/submission_main.py` and the packaging inputs against master (`git diff master -- src/ptcg/submission_main.py src/ptcg/decks/` expected empty unless a gate passed AND Brad approved a flip).
- [ ] **Step 3: EXPERIMENTS coherence** — every series/run this slice has a row; every ANALYSIS number traces to a row or sidecar; encoding guard test green.
- [ ] **Step 4: ANALYSIS final pass** — decision chain with the branch actually taken, every pre-registered criterion vs measured value, honest close (mirror Slice-5's dead-end write-up if that's the outcome).
- [ ] **Step 5: CLAUDE.md** — append a "Mixed-policy value net (Slice 6)" status paragraph after the Slice-5 entry (ASCII-clean, same style).
- [ ] **Step 6: Commit** — `git add experiments/ CLAUDE.md && git commit -m "docs: slice6 finalize — analysis, experiments coherence, CLAUDE.md status"`.

---

## Self-review notes (plan-time)

- Spec coverage: Phase 0 → T1/T2/T4/T5; data gen → T2/T8; retrain → T3/T6/T9; Stage-1 gate → T7/T10; Stage-2 → T11/T12/T13; fail paths → D0/D1/D2 decision points + T14 branches; testing section → per-task tests + T14 gates; deliverables → T5/T9/T10/T13/T14. Spec amendment made at plan time: deviation tag is GAME-LEVEL (either player), not per-acting-player — off-distribution-ness is a property of the trajectory, not the mover; spec text updated in the same commit as this plan.
- Complexity glance: all metric code is the existing O(n log n) rank-sum AUC (moved, not rewritten); eval-script scoring is O(rows) stdlib predicts with a 100k/bucket cap; loader is O(rows). No pairwise anything.
- Type consistency: `label_records(..., src=)` (T1) matches T2's call and T6's reader (`r.get("src", "v0")`); `ValueNetEvaluator.load` (T7) matches T9/T10/T12/T13 CLI usage; `puct_pick(node, legal, priors, c_puct)` (T11) matches T12's call; `d`/`src` JSONL keys consistent across T1/T4/T6.
